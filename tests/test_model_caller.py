"""
Tests for ModelCaller.call() — retry logic, error handling, input validation.
Also covers ReflectionEngine resilience when model_caller raises exceptions.
"""

import json
import io
import threading
import time
import pytest
from unittest.mock import Mock, patch, MagicMock
from urllib.error import HTTPError, URLError

from sage.tools.model_caller import (
    ModelCaller,
    ModelCallerError,
    TokenBucketRateLimiter,
)
from sage.reflection import ReflectionEngine
from sage.memory.procedural import ProceduralMemory
from sage.memory.episodic import EpisodicMemory


# ─── Helpers ─────────────────────────────────────────────────────────────────


def make_caller():
    """Create a ModelCaller with fake keys (no real API calls)."""
    caller = ModelCaller.__new__(ModelCaller)
    caller.use_qwen = True
    caller.qwen_api_key = "fake-qwen-key"
    caller.qwen_endpoint = "https://fake-endpoint.com"
    caller._qwen_limiter = TokenBucketRateLimiter(rate=100.0, burst=100)
    caller._circuit_breakers = {
        "Qwen": {"failures": 0, "last_failure": 0.0, "open": False},
    }
    caller._total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    caller._httpx_client = None  # Force urllib fallback for testability
    caller._model_map = dict(ModelCaller.DEFAULT_MODEL_MAP)
    caller.call_timeout = ModelCaller.DEFAULT_TIMEOUT
    return caller


def make_engine(tmp_path, model_caller=None):
    """Create a ReflectionEngine with isolated temp memory."""
    pm = ProceduralMemory(str(tmp_path / "rules" / "rules.md"))
    em = EpisodicMemory(str(tmp_path / "memory" / "episodic"))
    return ReflectionEngine(pm, em, model_caller=model_caller)


def fake_qwen_response(content="Hello"):
    """Build a fake Qwen-compatible API response."""
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


# ─── ModelCaller.call() — Input Validation ──────────────────────────────────


class TestModelCallerInputValidation:
    def test_empty_prompt_raises_value_error(self):
        """Empty prompt is rejected."""
        caller = make_caller()
        with pytest.raises(ValueError, match="non-empty"):
            caller.call("")

    def test_whitespace_only_prompt_raises_value_error(self):
        """Whitespace-only prompt is rejected."""
        caller = make_caller()
        with pytest.raises(ValueError, match="non-empty"):
            caller.call("   \n\t  ")

    def test_zero_max_tokens_raises_value_error(self):
        """max_tokens=0 is rejected."""
        caller = make_caller()
        with pytest.raises(ValueError, match="max_tokens"):
            caller.call("hello", max_tokens=0)

    def test_negative_max_tokens_raises_value_error(self):
        """Negative max_tokens is rejected."""
        caller = make_caller()
        with pytest.raises(ValueError, match="max_tokens"):
            caller.call("hello", max_tokens=-5)


# ─── ModelCaller.call() — Successful Calls ──────────────────────────────────


class TestModelCallerCallSuccess:
    def test_qwen_returns_content_by_default(self):
        """Successful Qwen call returns the content string."""
        caller = make_caller()
        mock_response = MagicMock()
        mock_response.read.return_value = fake_qwen_response("Test response")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = caller.call("What is 2+2?")
        assert result == "Test response"

    def test_qwen_returns_content(self):
        """Successful Qwen call returns the content string."""
        caller = make_caller()

        mock_response = MagicMock()
        mock_response.read.return_value = fake_qwen_response("Qwen says hi")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = caller.call("Hello")
        assert result == "Qwen says hi"

    def test_custom_qwen_model_passed_to_request(self):
        """Custom model name is included in the request payload."""
        caller = make_caller()

        captured_requests = []

        def capture_urlopen(req, **kwargs):
            captured_requests.append(req)
            mock_resp = MagicMock()
            mock_resp.read.return_value = fake_qwen_response("ok")
            mock_resp.__enter__ = Mock(return_value=mock_resp)
            mock_resp.__exit__ = Mock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=capture_urlopen):
            caller.call("test", model="qwen-plus")

        body = json.loads(captured_requests[0].data.decode())
        assert body["model"] == "qwen-plus"


# ─── ModelCaller.call() — Error Handling & Retries ──────────────────────────


class TestModelCallerRetry:
    def test_records_every_truncated_and_successful_attempt(self):
        caller = make_caller()
        caller._http_request = Mock(
            side_effect=[
                {
                    "choices": [
                        {"message": {"content": "partial"}, "finish_reason": "length"}
                    ],
                    "usage": {"total_tokens": 4},
                },
                {
                    "choices": [
                        {"message": {"content": "complete"}, "finish_reason": "stop"}
                    ],
                    "usage": {"total_tokens": 6},
                },
            ]
        )

        assert caller.call("test") == "complete"
        log = caller.get_call_log()
        assert [entry["status"] for entry in log] == ["truncated", "success"]
        assert [entry["attempt"] for entry in log] == [1, 2]
        assert sum(entry["usage"]["total_tokens"] for entry in log) == 10

    def test_empty_content_retries_with_larger_budget_then_succeeds(self):
        """A reasoning model that returns empty content (budget exhausted on
        internal reasoning tokens) must be retried with a larger max_tokens
        rather than failing the whole run. Mirrors length-truncation retry."""
        caller = make_caller()
        caller._http_request = Mock(
            side_effect=[
                {
                    "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                    "usage": {"total_tokens": 400},
                },
                {
                    "choices": [
                        {"message": {"content": '{"tool":"finish"}'}, "finish_reason": "stop"}
                    ],
                    "usage": {"total_tokens": 12},
                },
            ]
        )

        assert caller.call("test", max_tokens=400) == '{"tool":"finish"}'
        log = caller.get_call_log()
        assert [entry["status"] for entry in log] == ["empty_retry", "success"]

    def test_empty_content_eventually_raises_when_never_resolved(self):
        """If empty content persists across all retry attempts it still raises,
        preserving the original contract for genuinely broken responses."""
        caller = make_caller()
        caller._http_request = Mock(
            return_value={
                "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 1},
            }
        )

        with pytest.raises(ModelCallerError, match="empty content"):
            caller.call("test", max_tokens=400)

    def test_records_retryable_failure_without_prompt_content(self):
        caller = make_caller()
        caller._http_request = Mock(
            side_effect=[
                ModelCallerError("secret provider detail", "Qwen", retryable=True),
                {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
            ]
        )

        with patch("time.sleep"):
            assert caller.call("do not log this") == "ok"
        log = caller.get_call_log()
        assert [entry["status"] for entry in log] == ["retryable_error", "success"]
        assert "do not log this" not in json.dumps(log)
        assert "secret provider detail" not in json.dumps(log)

    def test_retry_after_is_bounded_and_slept_once(self):
        caller = make_caller()
        caller._http_request = Mock(
            side_effect=[
                ModelCallerError(
                    "Qwen: HTTP 429 rate limited",
                    "Qwen",
                    retryable=True,
                    retry_after=ModelCaller._parse_retry_after("999"),
                    category="rate_limited",
                ),
                {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
            ]
        )

        with patch("time.sleep") as sleep:
            assert caller.call("test") == "ok"
        sleep.assert_called_once_with(ModelCaller.MAX_RETRY_DELAY)
        assert caller.get_call_log()[0]["status"] == "rate_limited"

    def test_rate_limit_is_acquired_for_every_provider_attempt(self):
        """Retries consume limiter capacity just like initial requests."""
        caller = make_caller()
        caller._qwen_limiter = Mock()
        caller._qwen_limiter.acquire.return_value = True
        caller._http_request = Mock(
            side_effect=[
                ModelCallerError("retry", provider="Qwen", retryable=True),
                {
                    "choices": [
                        {"message": {"content": "ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                    },
                },
            ]
        )

        assert caller.call("test") == "ok"
        assert caller._qwen_limiter.acquire.call_count == 2

    def test_retries_on_429_then_succeeds(self):
        """Rate limit (429) is retried, then succeeds."""
        caller = make_caller()
        call_count = [0]

        def side_effect(req, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise HTTPError(req.full_url, 429, "Rate Limited", {}, None)
            mock_resp = MagicMock()
            mock_resp.read.return_value = fake_qwen_response("retry ok")
            mock_resp.__enter__ = Mock(return_value=mock_resp)
            mock_resp.__exit__ = Mock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with patch("time.sleep"):  # Skip actual delay
                result = caller.call("test")
        assert result == "retry ok"
        assert call_count[0] == 2

    def test_retries_on_500_then_succeeds(self):
        """Server error (500) is retried."""
        caller = make_caller()
        call_count = [0]

        def side_effect(req, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise HTTPError(req.full_url, 500, "Server Error", {}, None)
            mock_resp = MagicMock()
            mock_resp.read.return_value = fake_qwen_response("recovered")
            mock_resp.__enter__ = Mock(return_value=mock_resp)
            mock_resp.__exit__ = Mock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with patch("time.sleep"):
                result = caller.call("test")
        assert result == "recovered"

    def test_no_retry_on_400(self):
        """Client error (400) is NOT retried — immediate failure."""
        caller = make_caller()

        def side_effect(req, **kwargs):
            raise HTTPError(
                req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"error":"bad"}')
            )

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with pytest.raises(ModelCallerError, match="HTTP 400"):
                caller.call("test")

    def test_no_retry_on_401(self):
        """Auth error (401) is NOT retried."""
        caller = make_caller()

        def side_effect(req, **kwargs):
            raise HTTPError(
                req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"auth"}')
            )

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with pytest.raises(ModelCallerError, match="HTTP 401"):
                caller.call("test")

    def test_exhausted_retries_raises_error(self):
        """After MAX_RETRIES+1 failures, ModelCallerError is raised."""
        caller = make_caller()

        def side_effect(req, **kwargs):
            raise HTTPError(req.full_url, 503, "Unavailable", {}, None)

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with patch("time.sleep"):
                with pytest.raises(ModelCallerError, match="failed after"):
                    caller.call("test")

    def test_network_error_is_retried(self):
        """URLError (network issue) is retried."""
        caller = make_caller()
        call_count = [0]

        def side_effect(req, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise URLError("Connection refused")
            mock_resp = MagicMock()
            mock_resp.read.return_value = fake_qwen_response("net recovered")
            mock_resp.__enter__ = Mock(return_value=mock_resp)
            mock_resp.__exit__ = Mock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with patch("time.sleep"):
                result = caller.call("test")
        assert result == "net recovered"

    def test_invalid_json_response_raises_error(self):
        """Non-JSON response body raises ModelCallerError (not retried)."""
        caller = make_caller()

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json at all"
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as request:
            with pytest.raises(ModelCallerError, match="invalid JSON response"):
                caller.call("test")
        assert request.call_count == 1

    def test_missing_choices_in_response_raises_error(self):
        """Response without 'choices' array raises ModelCallerError."""
        caller = make_caller()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"data": "no choices"}).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(ModelCallerError, match="missing 'choices'"):
                caller.call("test")

    def test_empty_content_in_response_raises_error(self):
        """Response with empty content raises ModelCallerError."""
        caller = make_caller()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"choices": [{"message": {"content": ""}}]}
        ).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(ModelCallerError, match="empty content"):
                caller.call("test")
    def test_null_content_in_response_preserves_model_error(self):
        """Null content raises ModelCallerError instead of being masked by len(None)."""
        caller = make_caller()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"choices": [{"message": {"content": None}}]}
        ).encode()
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(ModelCallerError, match="empty content"):
                caller.call("test")


# ─── ReflectionEngine — Resilience to Model Failures ────────────────────────


class TestReflectionEngineResilience:
    def test_model_caller_raises_exception_falls_back(
        self, tmp_path, mock_model_caller_error
    ):
        """When model_caller raises, reflection falls back gracefully."""
        engine = make_engine(tmp_path, model_caller=mock_model_caller_error)
        result = engine.analyze_correction(
            task="Deploy",
            action="Ran script",
            error="timeout",
            correction="Add retries",
        )
        # Should still produce a valid rule via fallback
        assert "rule_id" in result
        assert len(result["rule"]) > 0
        assert result["confidence"] == 0.7  # fallback confidence

    def test_model_caller_returns_empty(self, tmp_path, mock_model_caller_empty):
        """Empty string from model_caller triggers fallback."""
        engine = make_engine(tmp_path, model_caller=mock_model_caller_empty)
        result = engine.analyze_correction(
            task="Deploy",
            action="Ran script",
            error="timeout",
            correction="Add retries",
        )
        assert "rule_id" in result
        assert len(result["rule"]) > 0

    def test_model_caller_returns_truncated_json(
        self, tmp_path, mock_model_caller_truncated_json
    ):
        """Truncated JSON from model triggers fallback."""
        engine = make_engine(tmp_path, model_caller=mock_model_caller_truncated_json)
        result = engine.analyze_correction(
            task="Deploy",
            action="Ran script",
            error="timeout",
            correction="Add retries",
        )
        assert "rule_id" in result
        assert result["confidence"] == 0.5  # fallback confidence

    def test_model_caller_returns_malformed_json(
        self, tmp_path, mock_model_caller_malformed
    ):
        """Non-JSON response from model triggers fallback."""
        engine = make_engine(tmp_path, model_caller=mock_model_caller_malformed)
        result = engine.analyze_correction(
            task="Deploy",
            action="Ran script",
            error="timeout",
            correction="Add retries",
        )
        assert "rule_id" in result
        # The non-JSON text itself becomes the rule via fallback
        assert len(result["rule"]) > 0


# ─── ProceduralMemory — Edge Cases ─────────────────────────────────────────


class TestProceduralMemoryEdgeCases:
    def test_increment_on_missing_file(self, tmp_path):
        """increment_application on non-existent file doesn't crash."""
        from sage.memory.procedural import ProceduralMemory

        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        # Manually delete the file
        pm.rules_path.unlink()
        pm.increment_application("R001")  # should not raise

    def test_get_all_rules_missing_file(self, tmp_path):
        """get_all_rules on missing file returns empty list."""
        from sage.memory.procedural import ProceduralMemory

        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.rules_path.unlink()
        assert pm.get_all_rules() == []

    def test_special_characters_in_rule_text(self, tmp_path):
        """Rules with special characters (unicode, newlines) are stored."""
        from sage.memory.procedural import ProceduralMemory

        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        special = "Rule with emoji 🔥 and unicode: 日本語 & <html> tags"
        pm.add_rule(special, "ctx", 0.8)
        rules = pm.get_all_rules()
        assert rules[0]["text"] == special

    def test_get_rules_for_prompt_only_high_confidence(self, tmp_path):
        """Only rules with confidence >= 0.5 appear in prompt."""
        from sage.memory.procedural import ProceduralMemory

        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("High conf rule", "ctx", 0.8, dedup=False)
        pm.add_rule("Boundary conf rule", "ctx", 0.5, dedup=False)
        pm.add_rule("Low conf rule", "ctx", 0.49, dedup=False)
        prompt = pm.get_rules_for_prompt()
        assert "High conf rule" in prompt
        assert "Boundary conf rule" in prompt
        assert "Low conf rule" not in prompt


# ─── EpisodicMemory — Edge Cases ────────────────────────────────────────────


class TestEpisodicMemoryEdgeCases:
    def test_get_by_task_case_insensitive(self, episodic_mem):
        """get_by_task is case-insensitive."""
        episodic_mem.log("Deploy ECS", 1, "success")
        episodic_mem.log("DEPLOY oss", 1, "success")
        results = episodic_mem.get_by_task("deploy")
        assert len(results) == 2

    def test_get_by_task_no_match(self, episodic_mem):
        """get_by_task returns empty when nothing matches."""
        episodic_mem.log("Deploy ECS", 1, "success")
        results = episodic_mem.get_by_task("billing")
        assert len(results) == 0

    def test_log_all_fields_populated(self, episodic_mem):
        """All optional fields are stored when provided."""
        entry = episodic_mem.log(
            "Task",
            2,
            "success",
            error="err",
            correction="fix",
            rule_extracted="rule text",
            rule_id="R001",
            metadata={"key": "val"},
        )
        assert entry["error"] == "err"
        assert entry["correction"] == "fix"
        assert entry["rule_extracted"] == "rule text"
        assert entry["rule_id"] == "R001"
        assert entry["metadata"]["key"] == "val"

    def test_get_recent_on_corrupted_file(self, tmp_path):
        """Corrupted JSONL file doesn't crash get_recent."""
        from sage.memory.episodic import EpisodicMemory

        em = EpisodicMemory(str(tmp_path / "ep"))
        em.current_file.write_text('not json\nalso bad\n{"task":"ok","line":1}\n')
        recent = em.get_recent()
        assert len(recent) == 1
        assert recent[0]["task"] == "ok"


# ─── Circuit Breaker Tests ──────────────────────────────────────────────────


class TestCircuitBreaker:
    def test_circuit_breaker_opens_after_threshold(self):
        """Circuit breaker opens after CIRCUIT_BREAKER_THRESHOLD consecutive failures."""
        caller = make_caller()
        caller.use_qwen = False
        caller.MAX_RETRIES = 0  # No retries, fail fast
        caller.CIRCUIT_BREAKER_THRESHOLD = 3

        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = HTTPError(
                url="", code=500, msg="Server Error", hdrs=None, fp=None
            )
            for i in range(3):
                try:
                    caller.call("test")
                except ModelCallerError:
                    pass

            cb = caller._circuit_breakers["Qwen"]
            assert cb["open"] is True, (
                "Circuit breaker should be open after threshold failures"
            )
            assert cb["failures"] == 3

    def test_circuit_breaker_blocks_when_open(self):
        """When circuit breaker is open, calls fail fast without hitting API."""
        caller = make_caller()
        caller.use_qwen = False
        # Force circuit breaker open
        caller._circuit_breakers["Qwen"] = {
            "failures": 10,
            "last_failure": time.monotonic(),  # Recent — still in recovery window
            "open": True,
        }

        with patch("urllib.request.urlopen") as mock_open:
            with pytest.raises(ModelCallerError) as exc_info:
                caller.call("test")
            assert "circuit breaker" in str(exc_info.value).lower()
            mock_open.assert_not_called()  # Should NOT hit the API

    def test_circuit_breaker_resets_on_success(self):
        """Circuit breaker resets when a successful call is made."""
        caller = make_caller()
        caller.use_qwen = False
        caller.MAX_RETRIES = 0
        # Pre-load some failures
        caller._circuit_breakers["Qwen"]["failures"] = 4

        with patch("urllib.request.urlopen") as mock_open:
            mock_response = Mock()
            mock_response.read.return_value = json.dumps(
                {
                    "choices": [
                        {"message": {"content": "Hello"}, "finish_reason": "stop"}
                    ]
                }
            ).encode()
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = Mock(return_value=False)
            mock_open.return_value = mock_response

            result = caller.call("test")
            assert result == "Hello"
            cb = caller._circuit_breakers["Qwen"]
            assert cb["failures"] == 0
            assert cb["open"] is False

    def test_circuit_breaker_allows_probe_after_recovery(self):
        """After recovery window, circuit breaker goes half-open and allows one probe."""
        caller = make_caller()
        caller.use_qwen = False
        caller.MAX_RETRIES = 0
        caller.CIRCUIT_BREAKER_RECOVERY = 1  # 1 second for testing
        # Set last_failure far enough in the past
        caller._circuit_breakers["Qwen"] = {
            "failures": 10,
            "last_failure": time.monotonic() - 2,  # 2 seconds ago > recovery
            "open": True,
        }

        with patch("urllib.request.urlopen") as mock_open:
            mock_response = Mock()
            mock_response.read.return_value = json.dumps(
                {
                    "choices": [
                        {
                            "message": {"content": "Probe worked"},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ).encode()
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = Mock(return_value=False)
            mock_open.return_value = mock_response

            result = caller.call("test")
            assert result == "Probe worked"


# ─── Token Usage Tracking Tests ─────────────────────────────────────────────


class TestTokenUsageTracking:
    def test_truncated_attempt_usage_is_not_lost(self):
        """Every billable response contributes to cumulative usage."""
        caller = make_caller()
        caller.MAX_RETRIES = 1
        caller._http_request = Mock(
            side_effect=[
                {
                    "choices": [
                        {"message": {"content": "partial"}, "finish_reason": "length"}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
                {
                    "choices": [
                        {"message": {"content": "complete"}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 10,
                        "total_tokens": 20,
                    },
                },
            ]
        )

        assert caller.call("test") == "complete"
        assert caller.get_usage() == {
            "prompt_tokens": 20,
            "completion_tokens": 15,
            "total_tokens": 35,
        }

    def test_attempt_budget_stops_additional_provider_calls(self):
        """A run budget caps outbound attempts before another request starts."""
        caller = make_caller()
        caller.start_budget(max_attempts=1, max_tokens=1_000)
        caller._http_request = Mock(
            side_effect=ModelCallerError("retry", provider="Qwen", retryable=True)
        )

        with pytest.raises(ModelCallerError, match="attempt budget exhausted"):
            caller.call("test")
        assert caller._http_request.call_count == 1

    def test_cancelled_budget_stops_before_provider_call(self):
        """Cancellation prevents the next outbound request from starting."""
        caller = make_caller()
        cancelled = threading.Event()
        cancelled.set()
        caller.start_budget(max_attempts=2, max_tokens=1_000, cancel_event=cancelled)
        caller._http_request = Mock()

        with pytest.raises(ModelCallerError, match="cancelled"):
            caller.call("test")
        caller._http_request.assert_not_called()

    def test_usage_increments_on_success(self):
        """Token usage is tracked across successful calls."""
        caller = make_caller()
        caller.use_qwen = False
        caller.MAX_RETRIES = 0

        with patch("urllib.request.urlopen") as mock_open:
            mock_response = Mock()
            mock_response.read.return_value = json.dumps(
                {
                    "choices": [
                        {"message": {"content": "Hi"}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                }
            ).encode()
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = Mock(return_value=False)
            mock_open.return_value = mock_response

            caller.call("test")
            usage = caller.get_usage()
            assert usage["prompt_tokens"] == 10
            assert usage["completion_tokens"] == 5
            assert usage["total_tokens"] == 15

    def test_usage_accumulates_across_calls(self):
        """Token usage accumulates across multiple calls."""
        caller = make_caller()
        caller.use_qwen = False
        caller.MAX_RETRIES = 0

        with patch("urllib.request.urlopen") as mock_open:
            mock_response = Mock()
            mock_response.read.return_value = json.dumps(
                {
                    "choices": [
                        {"message": {"content": "Hi"}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                }
            ).encode()
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = Mock(return_value=False)
            mock_open.return_value = mock_response

            caller.call("first")
            caller.call("second")
            caller.call("third")
            usage = caller.get_usage()
            assert usage["prompt_tokens"] == 30
            assert usage["completion_tokens"] == 15
            assert usage["total_tokens"] == 45

    def test_reset_usage_clears_counters(self):
        """reset_usage() clears all counters."""
        caller = make_caller()
        caller._total_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
        caller.reset_usage()
        usage = caller.get_usage()
        assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def test_usage_missing_usage_field(self):
        """Calls without usage field don't crash."""
        caller = make_caller()
        caller.use_qwen = False
        caller.MAX_RETRIES = 0

        with patch("urllib.request.urlopen") as mock_open:
            mock_response = Mock()
            mock_response.read.return_value = json.dumps(
                {
                    "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}]
                    # No "usage" key
                }
            ).encode()
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = Mock(return_value=False)
            mock_open.return_value = mock_response

            result = caller.call("test")
            assert result == "Hi"
            # Usage should remain at zeros
            assert caller.get_usage()["total_tokens"] == 0


# ─── Finish Reason Detection Tests ──────────────────────────────────────────


class TestFinishReasonDetection:
    def test_truncated_response_logs_warning(self):
        """finish_reason=length is detected and logged (not raised)."""
        caller = make_caller()
        caller.use_qwen = False
        caller.MAX_RETRIES = 0

        with patch("urllib.request.urlopen") as mock_open:
            mock_response = Mock()
            mock_response.read.return_value = json.dumps(
                {
                    "choices": [
                        {
                            "message": {"content": "Truncated..."},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 500,
                        "total_tokens": 505,
                    },
                }
            ).encode()
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = Mock(return_value=False)
            mock_open.return_value = mock_response

            result = caller.call("test", max_tokens=500)
            assert result == "Truncated..."
            # Usage should still be tracked
            assert caller.get_usage()["completion_tokens"] == 500
