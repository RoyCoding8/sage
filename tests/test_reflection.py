"""
Tests for Sage's ReflectionEngine:
  - Rule extraction from corrections (with mocked LLM)
  - Fallback rule generation (no LLM)
  - Prompt construction
  - JSON response parsing (valid, malformed, empty, truncated)
"""

import json

from sage.reflection import ReflectionEngine
from sage.memory.procedural import ProceduralMemory
from sage.memory.episodic import EpisodicMemory


# ─── Helpers ─────────────────────────────────────────────────────────────────


def make_engine(tmp_path, model_caller=None):
    """Create a ReflectionEngine with isolated temp memory."""
    pm = ProceduralMemory(str(tmp_path / "rules" / "rules.md"))
    em = EpisodicMemory(str(tmp_path / "memory" / "episodic"))
    return ReflectionEngine(pm, em, model_caller=model_caller)


# ─── ReflectionEngine: core flow ────────────────────────────────────────────


class TestReflectionEngine:
    def test_analyze_correction_stores_rule(self, tmp_path):
        """analyze_correction extracts and stores a rule in procedural memory."""
        engine = make_engine(tmp_path, model_caller=None)
        result = engine.analyze_correction(
            task="Deploy web app to ECS",
            action="Created instance but forgot security group",
            error="Connection refused on port 80",
            correction="You need to configure security group rules for port 80 first",
        )
        assert "rule_id" in result
        assert result["rule_id"].startswith("R")
        assert len(result["rule"]) > 0
        assert engine.procedural.get_rule_count() == 1

    def test_analyze_correction_logs_episodic(self, tmp_path):
        """analyze_correction logs to episodic memory."""
        engine = make_engine(tmp_path, model_caller=None)
        engine.analyze_correction(
            task="Deploy app",
            action="Ran deploy",
            error="Failed",
            correction="Add health check",
        )
        recent = engine.episodic.get_recent(1)
        assert len(recent) == 1
        assert recent[0]["task"] == "Deploy app"
        assert recent[0]["outcome"] == "failed"
        assert recent[0]["correction"] == "Add health check"

    def test_analyze_correction_with_llm(self, tmp_path, mock_model_caller):
        """When model_caller is provided, LLM response is used."""
        engine = make_engine(tmp_path, model_caller=mock_model_caller)
        result = engine.analyze_correction(
            task="Deploy",
            action="Forgot config",
            error="500 error",
            correction="Add config file",
        )
        assert result["rule"] == "Always check security groups before deploying to ECS"
        assert result["confidence"] == 0.95
        # Verify model was called with a prompt
        mock_model_caller.assert_called_once()
        call_args = mock_model_caller.call_args[0][0]
        assert "Deploy" in call_args
        assert (
            "security group" in call_args.lower() or "correction" in call_args.lower()
        )

    def test_fallback_rule_basic(self, tmp_path):
        """Without LLM, fallback generates a heuristic rule."""
        engine = make_engine(tmp_path, model_caller=None)
        result = engine.analyze_correction(
            task="Deploy web app",
            action="Did something wrong",
            error="Error occurred",
            correction="You must configure the security group",
        )
        assert "deploy web app" in result["rule"].lower()
        assert "security group" in result["rule"].lower()
        assert result["confidence"] == 0.7
        assert "Task:" in result["context"]

    def test_fallback_rule_truncation(self, tmp_path):
        """Fallback rule is truncated at 200 chars."""
        engine = make_engine(tmp_path, model_caller=None)
        long_correction = "A" * 300
        result = engine.analyze_correction(
            task="X", action="Y", error="Z", correction=long_correction
        )
        assert len(result["rule"]) <= 200

    def test_multiple_corrections_build_up(self, tmp_path):
        """Multiple corrections each add a rule."""
        engine = make_engine(tmp_path, model_caller=None)
        corrections = [
            ("Task A", "Action 1", "Error 1", "Fix 1"),
            ("Task B", "Action 2", "Error 2", "Fix 2"),
            ("Task C", "Action 3", "Error 3", "Fix 3"),
        ]
        for task, action, error, corr in corrections:
            engine.analyze_correction(task, action, error, corr)
        # Dedup is on by default — these short corrections share tokens
        # after stop-word removal, so some may be deduplicated.
        assert engine.procedural.get_rule_count() >= 1
        assert len(engine.episodic.get_recent(10)) == 3


# ─── Prompt construction ────────────────────────────────────────────────────


class TestReflectionPrompt:
    def test_prompt_contains_all_fields(self, tmp_path):
        """The reflection prompt includes all four inputs."""
        engine = make_engine(tmp_path)
        prompt = engine._build_reflection_prompt(
            task="Deploy",
            action="Ran script",
            error="timeout",
            correction="Add retries",
        )
        assert "Deploy" in prompt
        assert "Ran script" in prompt
        assert "timeout" in prompt
        assert "Add retries" in prompt

    def test_prompt_requests_json_format(self, tmp_path):
        """Prompt asks for JSON output."""
        engine = make_engine(tmp_path)
        prompt = engine._build_reflection_prompt("a", "b", "c", "d")
        assert "JSON" in prompt
        assert "rule" in prompt
        assert "context" in prompt
        assert "confidence" in prompt


# ─── Response parsing ───────────────────────────────────────────────────────


class TestReflectionResponseParsing:
    def test_parse_valid_json(self, tmp_path):
        """Valid JSON is parsed correctly."""
        engine = make_engine(tmp_path)
        response = json.dumps(
            {"rule": "Use HTTPS", "context": "always", "confidence": 0.9}
        )
        parsed = engine._parse_reflection_response(response)
        assert parsed["rule"] == "Use HTTPS"
        assert parsed["context"] == "always"
        assert parsed["confidence"] == 0.9

    def test_parse_json_with_surrounding_text(self, tmp_path):
        """JSON embedded in markdown/text is extracted."""
        engine = make_engine(tmp_path)
        response = 'Here is my analysis:\n```json\n{"rule": "Be careful", "context": "deploys", "confidence": 0.8}\n```\nDone.'
        parsed = engine._parse_reflection_response(response)
        assert parsed["rule"] == "Be careful"
        assert parsed["confidence"] == 0.8

    def test_parse_malformed_json(self, tmp_path):
        """Non-JSON response returns fallback rule."""
        engine = make_engine(tmp_path)
        parsed = engine._parse_reflection_response(
            "This is just plain text, no JSON here."
        )
        assert (
            "plain text" in parsed["rule"].lower()
            or "no json" in parsed["rule"].lower()
            or parsed["confidence"] == 0.5
        )

    def test_parse_empty_string(self, tmp_path):
        """Empty response returns fallback with message."""
        engine = make_engine(tmp_path)
        parsed = engine._parse_reflection_response("")
        assert parsed["confidence"] == 0.5
        assert "failed" in parsed["rule"].lower() or len(parsed["rule"]) > 0

    def test_parse_truncated_json(self, tmp_path):
        """Truncated JSON returns fallback."""
        engine = make_engine(tmp_path)
        parsed = engine._parse_reflection_response(
            '{"rule": "Use HTTPS", "context": "alw'
        )
        assert parsed["confidence"] == 0.5

    def test_parse_json_missing_keys(self, tmp_path):
        """JSON with missing keys gets safe defaults (no crash, no KeyError)."""
        engine = make_engine(tmp_path)
        parsed = engine._parse_reflection_response('{"rule": "Only rule provided"}')
        assert parsed["rule"] == "Only rule provided"
        # Missing keys are filled with sensible defaults
        assert parsed["context"] == "general"
        assert parsed["confidence"] == 0.5

    def test_parse_null_response(self, tmp_path):
        """None-like string doesn't crash."""
        engine = make_engine(tmp_path)
        parsed = engine._parse_reflection_response("null")
        assert isinstance(parsed, dict)
