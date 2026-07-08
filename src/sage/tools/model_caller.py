"""
Model Caller — Interface to LLMs for agent reasoning.

Uses httpx for async HTTP with connection pooling.
Live mode uses Qwen Cloud (qwen-max for reflection, qwen-turbo for execution).
Offline demo mode is handled by deterministic local stubs outside this class.

Architecture inspired by OpenAI Agents SDK:
- Async-first with sync wrapper for compatibility
- Structured output parsing via Pydantic
- Proper connection pooling (reuse TCP+TLS)
- Retry on truncation (finish_reason=length)
"""

import json
import logging
import os
import random
import re
import threading
import time
from pathlib import Path
from typing import Optional

from sage.security import redact_sensitive

logger = logging.getLogger(__name__)

# Try to import httpx; fall back to urllib if not available
try:
    import httpx

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    logger.info("httpx not installed, falling back to urllib (no connection pooling)")

from sage.closeable import CloseableMixin  # noqa: E402

# Environment variable names for OpenAI-compatible model configuration
ENV_QWEN_API_KEY = "SAGE_QWEN_API_KEY"
ENV_QWEN_ENDPOINT = "SAGE_QWEN_ENDPOINT"
ENV_MODEL_PROVIDER = "SAGE_MODEL_PROVIDER"
# Per-request HTTP timeout in seconds. A slow reasoning model
# emitting 4k-8k tokens can exceed the 30s Qwen default; raise this via env
# without changing the default for Qwen.
ENV_CALL_TIMEOUT = "SAGE_MODEL_CALL_TIMEOUT"
ENV_MODEL_MAP = {
    "reflection": "SAGE_REFLECTION_MODEL",
    "execution": "SAGE_EXECUTION_MODEL",
    "planning": "SAGE_PLANNING_MODEL",
    "default": "SAGE_DEFAULT_MODEL",
}


class ModelCallerError(Exception):
    """Base exception for ModelCaller failures."""

    def __init__(
        self,
        message: str,
        provider: str,
        retryable: bool = False,
        retry_after: float = 0.0,
        category: str = "error",
    ):
        self.provider = provider
        self.retryable = retryable
        self.retry_after = retry_after
        self.category = category
        super().__init__(message)


class TokenBucketRateLimiter:
    """Thread-safe token bucket rate limiter."""

    def __init__(self, rate: float = 5.0, burst: int = 10):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                if self.rate <= 0:
                    return False
                wait = (1.0 - self.tokens) / self.rate
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(wait, max(0, deadline - time.monotonic())))

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now


class ModelCaller(CloseableMixin):
    """
    Calls LLMs for agent reasoning.

    Strategy:
    - Live mode: Qwen Cloud (qwen-max for reflection, qwen-turbo for execution)
    - No third-party LLM routers are used.

    Features:
    - Connection pooling via httpx (reuses TCP+TLS across calls)
    - Circuit breaker per provider
    - Retry with exponential backoff + jitter
    - Retry on truncation (finish_reason=length)
    - Token usage tracking
    - Robust JSON extraction from model responses
    """

    # Available Qwen models for selection
    AVAILABLE_MODELS = ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-long"]

    # Default model routing (overridden per-instance)
    DEFAULT_MODEL_MAP = {
        "reflection": "qwen-max",
        "execution": "qwen-turbo",
        "planning": "qwen-plus",
        "default": "qwen-turbo",
    }

    DEFAULT_TIMEOUT = 30
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0
    MAX_JITTER = 0.5
    MAX_RETRY_DELAY = 10.0
    CIRCUIT_BREAKER_THRESHOLD = 5
    CIRCUIT_BREAKER_RECOVERY = 60
    RATE_LIMIT_RPS = 5.0
    RATE_LIMIT_BURST = 10

    def __init__(
        self,
        use_qwen: bool = False,
        qwen_endpoint: str = "",
        model_config: dict | None = None,
    ):
        self.use_qwen = use_qwen
        self.provider_name = os.environ.get(ENV_MODEL_PROVIDER, "Qwen").strip() or "Qwen"
        # Per-request HTTP timeout (env-overridable; class default stays 30s for
        # the fast Qwen path so unrelated callers see no behavior change).
        _raw_timeout = os.environ.get(ENV_CALL_TIMEOUT, "").strip()
        try:
            self.call_timeout = float(_raw_timeout) if _raw_timeout else self.DEFAULT_TIMEOUT
        except ValueError:
            self.call_timeout = self.DEFAULT_TIMEOUT
        if self.call_timeout <= 0:
            self.call_timeout = self.DEFAULT_TIMEOUT
        self.qwen_endpoint = (
            qwen_endpoint
            or os.environ.get(ENV_QWEN_ENDPOINT, "").strip()
            or "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        # Instance-level model map (configurable, not class-level). Environment
        # overrides let the real app use another OpenAI-compatible provider
        # without replacing Agent construction; explicit constructor config wins.
        self._model_map: dict[str, str] = {**self.DEFAULT_MODEL_MAP}
        self._model_map.update(
            {
                task_type: value
                for task_type, env_name in ENV_MODEL_MAP.items()
                if (value := os.environ.get(env_name, "").strip())
            }
        )
        if model_config:
            self._model_map.update(model_config)
        self.qwen_api_key = ""

        # Qwen rate limiter
        self._qwen_limiter = TokenBucketRateLimiter(
            rate=self.RATE_LIMIT_RPS, burst=self.RATE_LIMIT_BURST
        )

        # Circuit breaker state per provider
        self._circuit_breakers: dict[str, dict] = {
            self.provider_name: {"failures": 0, "last_failure": 0.0, "open": False},
        }

        # Token usage tracking
        self._total_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self._call_log: list[dict] = []
        self._state_lock = threading.RLock()
        self.start_budget(
            max_attempts=int(os.environ.get("SAGE_MAX_LLM_ATTEMPTS", "24")),
            max_tokens=int(os.environ.get("SAGE_MAX_LLM_TOKENS", "50000")),
        )

        # httpx client with connection pooling (eager init if available)
        self._httpx_client: Optional["httpx.Client"] = None
        if HAS_HTTPX:
            self._httpx_client = httpx.Client(
                timeout=httpx.Timeout(self.call_timeout, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
                follow_redirects=True,
            )

        self._load_keys()

    def _load_key(self, env_var: str, file_name: str) -> str:
        if val := os.environ.get(env_var, ""):
            return val
        path = Path.home() / ".openclaw" / "secrets" / file_name
        if path.exists():
            return path.read_text().strip()
        return ""

    def _load_keys(self):
        try:
            from sage.env_config import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        self.qwen_api_key = self._load_key(ENV_QWEN_API_KEY, "qwen-cloud-api-key.txt")

    def _select_qwen_model(
        self, model: str = "auto", task_type: str = "execution"
    ) -> str:
        if model and model != "auto":
            return model
        model_map = getattr(self, "_model_map", self.DEFAULT_MODEL_MAP)
        return model_map.get(task_type, model_map["default"])

    def set_model(self, task_type: str, model_name: str):
        """Set the model for a specific task type."""
        self._model_map[task_type] = model_name

    def get_model_config(self) -> dict[str, str]:
        """Return current model config (excluding 'default' key)."""
        return {k: v for k, v in self._model_map.items() if k != "default"}

    def call(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 500,
        model: str = "auto",
        task_type: str = "execution",
        temperature: float | None = None,
    ) -> str:
        """
        Call an LLM with the given prompt. Supports provider fallback.

        Args:
            prompt: The user prompt
            system: Optional system prompt
            max_tokens: Max tokens to generate
            model: Model name or 'auto' for intelligent routing
            task_type: 'reflection', 'execution', 'planning', or 'default'
            temperature: Override temperature (default: task-dependent)
        """
        if not prompt or not prompt.strip():
            raise ValueError("Prompt must be non-empty")
        if max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")

        # Task-dependent temperature defaults
        if temperature is None:
            temperature = {"reflection": 0.3, "planning": 0.5, "execution": 0.7}.get(
                task_type, 0.7
            )

        provider = getattr(self, "provider_name", "Qwen")
        if not self.qwen_api_key:
            raise ModelCallerError(
                f"{provider}: missing SAGE_QWEN_API_KEY; use --demo-offline for local deterministic mode",
                provider=provider,
                retryable=False,
            )

        return self._call_provider(
            prompt,
            system,
            max_tokens,
            task_type,
            temperature,
            provider=provider,
            endpoint=f"{self.qwen_endpoint}/chat/completions",
            api_key=self.qwen_api_key,
            model_name=self._select_qwen_model(model, task_type),
            limiter=self._qwen_limiter,
        )

    def _call_provider(
        self,
        prompt: str,
        system: str,
        max_tokens: int,
        task_type: str,
        temperature: float,
        provider: str,
        endpoint: str,
        api_key: str,
        model_name: str,
        limiter: TokenBucketRateLimiter,
    ) -> str:
        """Unified provider call with retry, rate limiting, and truncation handling."""

        # Circuit breaker check
        cb = self._circuit_breakers.get(provider)
        if cb and cb["open"]:
            elapsed = time.monotonic() - cb["last_failure"]
            if elapsed < self.CIRCUIT_BREAKER_RECOVERY:
                raise ModelCallerError(
                    f"{provider}: circuit breaker OPEN — {self.CIRCUIT_BREAKER_RECOVERY - elapsed:.0f}s until probe",
                    provider=provider,
                    retryable=True,
                )
            logger.info("%s: circuit breaker half-open, allowing probe", provider)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        current_max_tokens = max_tokens
        last_error: Optional[Exception] = None

        for attempt in range(self.MAX_RETRIES + 1):
            started = time.monotonic()
            usage: dict = {}
            content = ""
            finish_reason = ""
            payload = {
                "model": model_name,
                "messages": messages,
                "max_tokens": current_max_tokens,
                "temperature": temperature,
            }

            try:
                self._reserve_budget_attempt(provider)
                if not limiter.acquire(timeout=10.0):
                    raise ModelCallerError(
                        f"{provider}: rate limiter timeout",
                        provider=provider,
                        retryable=True,
                    )
                result = self._http_request(endpoint, api_key, payload, provider)
                usage = result.get("usage") or {}
                self._record_usage(usage)

                # Validate response
                choices = result.get("choices")
                if not choices or not isinstance(choices, list):
                    raise ModelCallerError(
                        f"{provider}: response missing 'choices'", provider=provider
                    )

                content = choices[0].get("message", {}).get("content")
                if not content:
                    # A reasoning model can spend the whole budget on
                    # internal reasoning tokens and surface zero visible content.
                    # That is a budget-exhaustion condition, not a hard failure:
                    # retry with a reasoning-safe token floor (4096) before giving
                    # up, the same way a "length" truncation is retried below.
                    finish_reason = choices[0].get("finish_reason", "")
                    if attempt < self.MAX_RETRIES and current_max_tokens < 8192:
                        self._log_attempt(
                            provider=provider,
                            model_name=model_name,
                            task_type=task_type,
                            status="empty_retry",
                            attempt=attempt + 1,
                            started=started,
                            finish_reason=finish_reason or "empty",
                            usage=usage,
                            response_chars=0,
                        )
                        current_max_tokens = min(max(current_max_tokens * 2, 4096), 8192)
                        logger.info(
                            "%s: empty content (reasoning budget), retrying with max_tokens=%d",
                            provider,
                            current_max_tokens,
                        )
                        continue
                    raise ModelCallerError(
                        f"{provider}: empty content", provider=provider
                    )

                # Handle truncation: retry with doubled max_tokens
                finish_reason = choices[0].get("finish_reason", "")
                if finish_reason == "length" and attempt < self.MAX_RETRIES:
                    self._log_attempt(
                        provider=provider,
                        model_name=model_name,
                        task_type=task_type,
                        status="truncated",
                        attempt=attempt + 1,
                        started=started,
                        finish_reason=finish_reason,
                        usage=usage,
                        response_chars=len(content or ""),
                    )
                    current_max_tokens = min(current_max_tokens * 2, 4096)
                    logger.info(
                        "%s: truncated, retrying with max_tokens=%d",
                        provider,
                        current_max_tokens,
                    )
                    continue

                self._log_attempt(
                    provider=provider,
                    model_name=model_name,
                    task_type=task_type,
                    status="success",
                    attempt=attempt + 1,
                    started=started,
                    finish_reason=finish_reason,
                    usage=usage,
                    response_chars=len(content or ""),
                )

                # Reset circuit breaker on success
                if cb:
                    cb["failures"] = 0
                    cb["open"] = False

                return content

            except ModelCallerError as e:
                self._log_attempt(
                    provider=provider,
                    model_name=model_name,
                    task_type=task_type,
                    status=(
                        "rate_limited"
                        if e.category == "rate_limited"
                        else "retryable_error"
                        if e.retryable
                        else "permanent_error"
                    ),
                    attempt=attempt + 1,
                    started=started,
                    finish_reason=type(e).__name__,
                    usage=usage,
                    response_chars=len(content or ""),
                )
                if not e.retryable:
                    raise
                last_error = e
            except Exception as e:
                self._log_attempt(
                    provider=provider,
                    model_name=model_name,
                    task_type=task_type,
                    status="retryable_error",
                    attempt=attempt + 1,
                    started=started,
                    finish_reason=type(e).__name__,
                    usage=usage,
                    response_chars=len(content or ""),
                )
                last_error = e
                logger.warning(
                    "%s error (attempt %d/%d): %s",
                    provider,
                    attempt + 1,
                    self.MAX_RETRIES + 1,
                    e,
                )

            # Retry with backoff
            if attempt < self.MAX_RETRIES:
                base_delay = self.RETRY_DELAY * (2**attempt)
                jitter = random.uniform(0, base_delay * self.MAX_JITTER)
                retry_after = getattr(last_error, "retry_after", 0.0)
                delay = min(self.MAX_RETRY_DELAY, max(base_delay + jitter, retry_after))
                time.sleep(delay)

        # Exhausted retries — circuit breaker
        if cb:
            cb["failures"] += 1
            cb["last_failure"] = time.monotonic()
            if cb["failures"] >= self.CIRCUIT_BREAKER_THRESHOLD:
                cb["open"] = True
                logger.warning(
                    "%s: circuit breaker OPEN after %d failures",
                    provider,
                    cb["failures"],
                )

        raise ModelCallerError(
            f"{provider}: failed after {self.MAX_RETRIES + 1} attempts — {last_error}",
            provider=provider,
            retryable=True,
        )

    def _http_request(
        self, endpoint: str, api_key: str, payload: dict, provider: str
    ) -> dict:
        """Execute HTTP request using httpx (preferred) or urllib (fallback)."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Use httpx if client is initialized; otherwise fall back to urllib
        if HAS_HTTPX and self._httpx_client is not None:
            client = self._httpx_client
            try:
                response = client.post(endpoint, json=payload, headers=headers)
                if response.status_code == 429:
                    raise ModelCallerError(
                        f"{provider}: HTTP 429 rate limited",
                        provider=provider,
                        retryable=True,
                        retry_after=self._parse_retry_after(
                            response.headers.get("Retry-After")
                        ),
                        category="rate_limited",
                    )
                if response.status_code >= 500:
                    raise ModelCallerError(
                        f"{provider}: HTTP {response.status_code}",
                        provider=provider,
                        retryable=True,
                    )
                if response.status_code >= 400:
                    raise ModelCallerError(
                        f"{provider}: HTTP {response.status_code} — "
                        f"{redact_sensitive(response.text[:300], (api_key,))}",
                        provider=provider,
                        retryable=False,
                    )
                try:
                    return response.json()
                except ValueError as e:
                    raise ModelCallerError(
                        f"{provider}: invalid JSON response",
                        provider=provider,
                        retryable=False,
                        category="response_shape",
                    ) from e
            except httpx.TimeoutException as e:
                raise ModelCallerError(
                    f"{provider}: timeout — {e}", provider=provider, retryable=True
                ) from e
            except httpx.ConnectError as e:
                raise ModelCallerError(
                    f"{provider}: connection error — {e}",
                    provider=provider,
                    retryable=True,
                ) from e
        else:
            # urllib fallback
            import urllib.request
            import urllib.error

            data = json.dumps(payload).encode()
            req = urllib.request.Request(endpoint, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.call_timeout) as resp:
                    try:
                        return json.loads(resp.read().decode())
                    except (UnicodeDecodeError, json.JSONDecodeError) as e:
                        raise ModelCallerError(
                            f"{provider}: invalid JSON response",
                            provider=provider,
                            retryable=False,
                            category="response_shape",
                        ) from e
            except urllib.error.HTTPError as e:
                retryable = e.code == 429 or e.code >= 500
                retry_after = (
                    self._parse_retry_after(e.headers.get("Retry-After"))
                    if e.code == 429 and e.headers
                    else 0.0
                )
                body = ""
                try:
                    body = e.read().decode()[:300]
                except Exception:
                    pass
                raise ModelCallerError(
                    f"{provider}: HTTP {e.code} — {body}",
                    provider=provider,
                    retryable=retryable,
                    retry_after=retry_after,
                    category="rate_limited" if e.code == 429 else "error",
                ) from e
            except urllib.error.URLError as e:
                raise ModelCallerError(
                    f"{provider}: {e.reason}", provider=provider, retryable=True
                ) from e

    # ─── JSON Extraction ─────────────────────────────────────────────────────

    @classmethod
    def _parse_retry_after(cls, value: str | None) -> float:
        """Parse delta-seconds Retry-After with a defensive upper bound."""
        try:
            return min(cls.MAX_RETRY_DELAY, max(0.0, float(value or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def _log_attempt(
        self,
        *,
        provider: str,
        model_name: str,
        task_type: str,
        status: str,
        attempt: int,
        started: float,
        finish_reason: str,
        usage: dict,
        response_chars: int,
    ) -> None:
        """Record non-sensitive metadata for exactly one provider attempt."""
        entry = {
            "timestamp": time.time(),
            "provider": provider,
            "model": model_name,
            "task_type": task_type,
            "status": status,
            "attempt": attempt,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "response_chars": response_chars,
            "finish_reason": finish_reason,
            "usage": {
                key: int(usage.get(key, 0) or 0)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            },
        }
        lock = getattr(self, "_state_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._state_lock = lock
        with lock:
            if not hasattr(self, "_call_log"):
                self._call_log = []
            self._call_log.append(entry)

    @staticmethod
    def extract_json(text: str) -> dict | None:
        """Extract JSON from model response, handling code fences and nested objects.

        Strategy (ordered by reliability):
        1. Try code fence extraction (```json ... ```)
        2. Try outermost balanced braces
        3. Return None if no valid JSON found
        """
        if not text:
            return None

        # Strategy 1: Code fence
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1))
            except json.JSONDecodeError:
                pass

        # Strategy 2: Balanced brace extraction
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        start = -1
                        depth = 0

        return None

    # ─── Utility ─────────────────────────────────────────────────────────────

    def start_budget(
        self,
        max_attempts: int = 24,
        max_tokens: int = 50_000,
        timeout_seconds: float = 55.0,
        cancel_event=None,
    ) -> None:
        """Start a bounded provider-attempt and token budget for a Run."""
        if max_attempts < 1 or max_tokens < 1:
            raise ValueError("budget limits must be positive")
        self._budget = {
            "max_attempts": max_attempts,
            "attempts": 0,
            "max_tokens": max_tokens,
            "tokens": 0,
            "deadline": time.monotonic() + timeout_seconds,
            "cancel_event": cancel_event,
        }

    def _reserve_budget_attempt(self, provider: str) -> None:
        budget = getattr(self, "_budget", None)
        if budget is None:
            return
        cancel_event = budget.get("cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            raise ModelCallerError(
                f"{provider}: Run cancelled",
                provider=provider,
                retryable=False,
            )
        if time.monotonic() >= budget["deadline"]:
            raise ModelCallerError(
                f"{provider}: Run deadline exhausted",
                provider=provider,
                retryable=False,
            )
        if budget["attempts"] >= budget["max_attempts"]:
            raise ModelCallerError(
                f"{provider}: attempt budget exhausted",
                provider=provider,
                retryable=False,
            )
        if budget["tokens"] >= budget["max_tokens"]:
            raise ModelCallerError(
                f"{provider}: token budget exhausted",
                provider=provider,
                retryable=False,
            )
        budget["attempts"] += 1

    def _record_usage(self, usage: dict) -> None:
        if not usage:
            return
        lock = getattr(self, "_state_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._state_lock = lock
        with lock:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                self._total_usage[key] += int(usage.get(key, 0) or 0)
            budget = getattr(self, "_budget", None)
            if budget is not None:
                budget["tokens"] += int(usage.get("total_tokens", 0) or 0)

    def get_usage(self) -> dict:
        """Return cumulative token usage."""
        return dict(self._total_usage)

    def get_call_log(self) -> list[dict]:
        """Return non-sensitive per-call metadata for demo transcripts."""
        return list(getattr(self, "_call_log", []))

    def reset_usage(self):
        self._total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self._call_log = []

    def close(self):
        """Close the httpx client (release connection pool)."""
        if self._httpx_client:
            self._httpx_client.close()
            self._httpx_client = None



if __name__ == "__main__":
    caller = ModelCaller(use_qwen=False)
    print("Model caller initialized")
    print(f"Using httpx: {HAS_HTTPX}")
    print(f"Qwen key: {'loaded' if caller.qwen_api_key else 'not found'}")
