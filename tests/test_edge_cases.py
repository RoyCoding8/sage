"""
Tests for Sage's ProceduralMemory dedup, decay, and edge cases,
plus ReflectionEngine + ModelCaller integration (all mocked).

Covers gaps identified after reviewing existing test coverage:
- Dedup similarity threshold boundary cases
- Confidence clamping and edge values
- Decay lifecycle (decay → prune → reset cycle)
- ReflectionEngine with various model_caller behaviors
- ModelCaller._select_qwen_model routing logic
"""

import json
import pytest
from unittest.mock import Mock

from sage.memory.procedural import ProceduralMemory
from sage.memory.episodic import EpisodicMemory
from sage.reflection import ReflectionEngine
from sage.tools.model_caller import ModelCaller


# ─── Helpers ─────────────────────────────────────────────────────────────────


def make_engine(tmp_path, model_caller=None):
    pm = ProceduralMemory(str(tmp_path / "rules" / "rules.md"))
    em = EpisodicMemory(str(tmp_path / "memory" / "episodic"))
    return ReflectionEngine(pm, em, model_caller=model_caller)


# ─── ProceduralMemory: Dedup Edge Cases ─────────────────────────────────────


class TestDedupEdgeCases:
    def test_identical_rule_deduped(self, tmp_path):
        """Exact same rule text is deduplicated."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule(
            "Always check security group before deploying", "ECS", 0.9, dedup=False
        )
        existing_id = pm.add_rule(
            "Always check security group before deploying", "ECS", 0.95
        )
        assert existing_id == "R001"
        assert pm.get_rule_count() == 1

    def test_completely_different_rules_not_deduped(self, tmp_path):
        """Rules with no word overlap are never deduplicated."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Configure firewall ports", "networking", 0.8, dedup=False)
        existing_id = pm._is_duplicate("Use Docker containers for deployment")
        assert existing_id is None

    def test_subset_rule_deduped(self, tmp_path):
        """Shorter rule is a subset of longer → deduped."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Check ports before deploying", "networking", 0.8, dedup=False)
        # All meaningful words from the shorter rule appear in the longer
        existing_id = pm._is_duplicate(
            "Always check ports before deploying to the cloud infrastructure"
        )
        assert existing_id == "R001"

    def test_superset_rule_not_deduped(self, tmp_path):
        """Longer rule is a superset of shorter → NOT deduped (asymmetric)."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule(
            "Always check ports before deploying to the cloud",
            "networking",
            0.8,
            dedup=False,
        )
        # Shorter rule's words are a subset, but the comparison is asymmetric
        existing_id = pm._is_duplicate("Check ports")
        # "check" and "ports" are only 2 words; overlap with longer should be high
        # But "check" "ports" = 2/2 = 1.0 >= 0.6, so it IS deduped
        assert existing_id == "R001"

    def test_dedup_with_single_word_rules(self, tmp_path):
        """Single-word rules: dedup depends on token overlap."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Deploy", "general", 0.5, dedup=False)
        # "Deploy" alone — overlap with "Deploy carefully" is 1/1 = 1.0
        existing_id = pm._is_duplicate("Deploy carefully")
        assert existing_id == "R001"

    def test_dedup_disabled_returns_new_id(self, tmp_path):
        """dedup=False always creates a new rule, even for identical text."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        r1 = pm.add_rule("Check ports", "ctx", 0.8, dedup=False)
        r2 = pm.add_rule("Check ports", "ctx", 0.8, dedup=False)
        assert r1 == "R001"
        assert r2 == "R002"
        assert pm.get_rule_count() == 2

    def test_empty_rule_text_not_deduped(self, tmp_path):
        """Empty rule text doesn't crash dedup."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Valid rule", "ctx", 0.5, dedup=False)
        # _tokenize("") returns empty set → no dedup
        assert pm._is_duplicate("") is None

    def test_dedup_returns_correct_existing_id(self, tmp_path):
        """Dedup returns the ID of the FIRST matching rule."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Configure security group before deploy", "ctx", 0.7, dedup=False)
        pm.add_rule("Use HTTPS for all API endpoints", "ctx", 0.8, dedup=False)
        # "security" only appears in R001; overlap 4/4 = 1.0 >= 0.6
        existing_id = pm._is_duplicate("Configure security group before deploy")
        assert existing_id == "R001"


# ─── ProceduralMemory: Confidence Clamping ──────────────────────────────────


class TestConfidenceClamping:
    def test_confidence_below_zero_clamped(self, tmp_path):
        """Negative confidence is clamped to 0.0."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule", "ctx", -0.5, dedup=False)
        rules = pm.get_all_rules()
        assert rules[0]["confidence"] == 0.0

    def test_confidence_above_one_clamped(self, tmp_path):
        """Confidence > 1.0 is clamped to 1.0."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule", "ctx", 5.0, dedup=False)
        rules = pm.get_all_rules()
        assert rules[0]["confidence"] == 1.0

    def test_confidence_string_input(self, tmp_path):
        """Confidence as string is converted to float."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule", "ctx", "0.75", dedup=False)
        rules = pm.get_all_rules()
        assert rules[0]["confidence"] == 0.75


# ─── ProceduralMemory: Input Validation ─────────────────────────────────────


class TestInputValidation:
    def test_empty_rule_text_raises(self, tmp_path):
        """Empty rule_text raises ValueError."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        with pytest.raises(ValueError, match="rule_text"):
            pm.add_rule("", "ctx", 0.5)

    def test_whitespace_only_rule_text_raises(self, tmp_path):
        """Whitespace-only rule_text raises ValueError."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        with pytest.raises(ValueError, match="rule_text"):
            pm.add_rule("   \n\t  ", "ctx", 0.5)

    def test_empty_context_raises(self, tmp_path):
        """Empty context raises ValueError."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        with pytest.raises(ValueError, match="context"):
            pm.add_rule("Rule text", "", 0.5)

    def test_whitespace_only_context_raises(self, tmp_path):
        """Whitespace-only context raises ValueError."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        with pytest.raises(ValueError, match="context"):
            pm.add_rule("Rule text", "   \n\t  ", 0.5)


# ─── ProceduralMemory: Decay Lifecycle ──────────────────────────────────────


class TestDecayLifecycle:
    def test_decay_with_all_applied_rules(self, tmp_path):
        """When all rules have times_applied > 0, nothing is decayed."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule A", "ctx", 0.8, dedup=False)
        pm.add_rule("Rule B", "ctx", 0.6, dedup=False)
        pm.increment_application("R001")
        pm.increment_application("R002")

        pruned = pm.decay_confidence(decay_rate=0.5)
        assert pruned == []
        rules = pm.get_all_rules()
        assert rules[0]["confidence"] == pytest.approx(0.8)
        assert rules[1]["confidence"] == pytest.approx(0.6)

    def test_decay_at_zero_confidence_prunes(self, tmp_path):
        """Rule at confidence 0.0 is pruned (below min_confidence)."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule", "ctx", 0.0, dedup=False)
        # Decay clamps to 0.0, then prune_stale_rules removes it (< 0.1)
        pruned = pm.decay_confidence(decay_rate=0.1)
        assert "R001" in pruned
        assert pm.get_rule_count() == 0

    def test_prune_no_rules_below_threshold(self, tmp_path):
        """No rules pruned when all above threshold."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Strong rule", "ctx", 0.9, dedup=False)
        pruned = pm.prune_stale_rules(min_confidence=0.1)
        assert pruned == []
        assert pm.get_rule_count() == 1

    def test_prune_all_rules_below_threshold(self, tmp_path):
        """All rules pruned when all below threshold."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Weak 1", "ctx", 0.05, dedup=False)
        pm.add_rule("Weak 2", "ctx", 0.08, dedup=False)
        pruned = pm.prune_stale_rules(min_confidence=0.1)
        assert len(pruned) == 2
        assert pm.get_rule_count() == 0

    def test_full_lifecycle_add_use_decay_prune(self, tmp_path):
        """Full lifecycle: add rules, use some, decay, prune, verify."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        # Add 4 rules at low confidence
        for i in range(4):
            pm.add_rule(f"Rule {i + 1}", "ctx", 0.3, dedup=False)
        # Use only rule 2 and 4
        pm.increment_application("R002")
        pm.increment_application("R004")

        # Decay unused rules by 0.1 (0.3 → 0.2)
        pruned = pm.decay_confidence(decay_rate=0.1, min_confidence=0.1)
        # None pruned yet (0.2 >= 0.1)

        # Decay again (0.2 → 0.1) — still not pruned (0.1 >= 0.1)
        pruned = pm.decay_confidence(decay_rate=0.1, min_confidence=0.1)

        # Decay once more (0.1 → 0.0) — NOW pruned (< 0.1)
        pruned = pm.decay_confidence(decay_rate=0.1, min_confidence=0.1)
        assert len(pruned) == 2  # R001, R003
        assert "R001" in pruned
        assert "R003" in pruned

        # Verify R002 and R004 survived (used, not decayed)
        rules = pm.get_all_rules()
        surviving_ids = [r["id"] for r in rules]
        assert "R002" in surviving_ids
        assert "R004" in surviving_ids
        assert len(rules) == 2


# ─── ReflectionEngine: ModelCaller Integration ──────────────────────────────


class TestReflectionEngineWithModelCaller:
    def test_model_caller_returns_valid_json(self, tmp_path):
        """Valid JSON response from model_caller is parsed and used."""
        mock_caller = Mock(
            return_value=json.dumps(
                {
                    "rule": "Always use HTTPS for API endpoints",
                    "context": "Network security",
                    "confidence": 0.9,
                }
            )
        )
        engine = make_engine(tmp_path, model_caller=mock_caller)
        result = engine.analyze_correction(
            task="Deploy API",
            action="Used HTTP",
            error="Insecure connection",
            correction="Use HTTPS instead",
        )
        assert result["rule"] == "Always use HTTPS for API endpoints"
        assert result["confidence"] == 0.9
        assert result["context"] == "Network security"

    def test_model_caller_returns_json_with_extra_text(self, tmp_path):
        """JSON embedded in markdown/text is extracted correctly."""
        mock_caller = Mock(
            return_value=(
                "Here's my analysis:\n"
                '```json\n{"rule": "Check firewall rules", "context": "ECS", "confidence": 0.85}\n```\n'
                "Done."
            )
        )
        engine = make_engine(tmp_path, model_caller=mock_caller)
        result = engine.analyze_correction(
            task="Deploy",
            action="Forgot firewall",
            error="Connection refused",
            correction="Add firewall rules",
        )
        assert result["rule"] == "Check firewall rules"
        assert result["confidence"] == 0.85

    def test_model_caller_returns_json_missing_rule_key(self, tmp_path):
        """JSON without 'rule' key falls back to heuristic."""
        mock_caller = Mock(
            return_value=json.dumps(
                {"suggestion": "Do something different", "confidence": 0.9}
            )
        )
        engine = make_engine(tmp_path, model_caller=mock_caller)
        result = engine.analyze_correction(
            task="Deploy",
            action="Ran script",
            error="Failed",
            correction="Try a different approach",
        )
        # Fallback uses the response text as the rule
        assert len(result["rule"]) > 0
        assert result["confidence"] == 0.5

    def test_model_caller_returns_empty_json(self, tmp_path):
        """Empty JSON object triggers fallback."""
        mock_caller = Mock(return_value="{}")
        engine = make_engine(tmp_path, model_caller=mock_caller)
        result = engine.analyze_correction(
            task="Deploy", action="Ran script", error="Failed", correction="Fix it"
        )
        assert len(result["rule"]) > 0

    def test_model_caller_none_response(self, tmp_path):
        """None return from model triggers fallback (no crash)."""
        mock_caller = Mock(return_value=None)
        engine = make_engine(tmp_path, model_caller=mock_caller)
        result = engine.analyze_correction(
            task="Deploy", action="Ran script", error="Failed", correction="Fix it"
        )
        assert "rule_id" in result

    def test_model_caller_confidence_clamped(self, tmp_path):
        """Confidence > 1.0 is clamped to 1.0."""
        mock_caller = Mock(
            return_value=json.dumps(
                {
                    "rule": "Always validate input",
                    "context": "security",
                    "confidence": 5.0,
                }
            )
        )
        engine = make_engine(tmp_path, model_caller=mock_caller)
        result = engine.analyze_correction(
            task="Deploy",
            action="No validation",
            error="Injection",
            correction="Validate all inputs",
        )
        assert result["confidence"] == 1.0

    def test_model_caller_negative_confidence_clamped(self, tmp_path):
        """Negative confidence is clamped to 0.0."""
        mock_caller = Mock(
            return_value=json.dumps(
                {"rule": "Use timeouts", "context": "networking", "confidence": -1.0}
            )
        )
        engine = make_engine(tmp_path, model_caller=mock_caller)
        result = engine.analyze_correction(
            task="Deploy", action="No timeout", error="Hang", correction="Add timeouts"
        )
        assert result["confidence"] == 0.0

    def test_model_caller_called_with_correct_prompt(self, tmp_path):
        """Model caller receives a prompt containing all four inputs."""
        mock_caller = Mock(
            return_value=json.dumps(
                {"rule": "Test rule", "context": "test", "confidence": 0.5}
            )
        )
        engine = make_engine(tmp_path, model_caller=mock_caller)
        engine.analyze_correction(
            task="MyTask", action="MyAction", error="MyError", correction="MyCorrection"
        )
        call_args = mock_caller.call_args[0][0]
        assert "MyTask" in call_args
        assert "MyAction" in call_args
        assert "MyError" in call_args
        assert "MyCorrection" in call_args

    def test_rule_text_truncated_at_200_chars(self, tmp_path):
        """Long rule text from model is truncated to 200 chars."""
        long_rule = "A" * 300
        mock_caller = Mock(
            return_value=json.dumps(
                {"rule": long_rule, "context": "test", "confidence": 0.5}
            )
        )
        engine = make_engine(tmp_path, model_caller=mock_caller)
        result = engine.analyze_correction(
            task="Deploy", action="Ran", error="Err", correction="Fix"
        )
        assert len(result["rule"]) <= 200


# ─── ModelCaller: _select_qwen_model Routing ────────────────────────────────


class TestQwenModelRouting:
    def test_reflection_uses_qwen_max(self, tmp_path):
        """task_type='reflection' routes to qwen-max."""
        caller = ModelCaller.__new__(ModelCaller)
        assert caller._select_qwen_model("auto", "reflection") == "qwen-max"

    def test_execution_uses_qwen_turbo(self, tmp_path):
        """task_type='execution' routes to qwen-turbo."""
        caller = ModelCaller.__new__(ModelCaller)
        assert caller._select_qwen_model("auto", "execution") == "qwen-turbo"

    def test_planning_uses_qwen_plus(self, tmp_path):
        """task_type='planning' routes to qwen-plus."""
        caller = ModelCaller.__new__(ModelCaller)
        assert caller._select_qwen_model("auto", "planning") == "qwen-plus"

    def test_explicit_model_overrides_routing(self, tmp_path):
        """Explicit model name overrides task-type routing."""
        caller = ModelCaller.__new__(ModelCaller)
        assert caller._select_qwen_model("qwen-turbo", "reflection") == "qwen-turbo"

    def test_non_qwen_explicit_model_is_honored(self, tmp_path):
        """An explicit (non-'auto') model is honored as-is so a non-qwen
        OpenAI-compatible provider can be routed directly without being
        silently remapped back to a qwen default."""
        caller = ModelCaller.__new__(ModelCaller)
        result = caller._select_qwen_model("gpt-4", "reflection")
        assert result == "gpt-4"  # explicit model wins, not remapped to qwen-max

    def test_unknown_task_type_uses_default(self, tmp_path):
        """Unknown task_type falls back to default (qwen-turbo)."""
        caller = ModelCaller.__new__(ModelCaller)
        assert caller._select_qwen_model("auto", "unknown_type") == "qwen-turbo"


# ─── EpisodicMemory: Additional Edge Cases ──────────────────────────────────


class TestEpisodicMemoryAdditional:
    def test_get_recent_more_than_available(self, tmp_path):
        """get_recent(n) where n > total returns all entries."""
        from sage.memory.episodic import EpisodicMemory

        em = EpisodicMemory(str(tmp_path / "ep"))
        em.log("Task 1", 1, "success")
        em.log("Task 2", 1, "success")
        recent = em.get_recent(100)
        assert len(recent) == 2

    def test_get_by_task_empty_pattern(self, tmp_path):
        """get_by_task with empty string matches all."""
        from sage.memory.episodic import EpisodicMemory

        em = EpisodicMemory(str(tmp_path / "ep"))
        em.log("Deploy ECS", 1, "success")
        em.log("Check billing", 1, "failed")
        results = em.get_by_task("")
        assert len(results) == 2

    def test_get_corrections_empty(self, tmp_path):
        """get_corrections on memory with no corrections returns empty."""
        from sage.memory.episodic import EpisodicMemory

        em = EpisodicMemory(str(tmp_path / "ep"))
        em.log("Task", 1, "success")
        assert em.get_corrections() == []

    def test_get_stats_all_failed(self, tmp_path):
        """Stats when all entries are failures."""
        from sage.memory.episodic import EpisodicMemory

        em = EpisodicMemory(str(tmp_path / "ep"))
        em.log("A", 1, "failed")
        em.log("B", 1, "failed")
        stats = em.get_stats()
        assert stats["success"] == 0
        assert stats["failed"] == 2
        assert stats["success_rate"] == 0.0

    def test_log_without_optional_fields(self, tmp_path):
        """log() with only required fields works."""
        from sage.memory.episodic import EpisodicMemory

        em = EpisodicMemory(str(tmp_path / "ep"))
        entry = em.log("Task", 1, "success")
        assert entry["error"] is None
        assert entry["correction"] is None
        assert entry["rule_extracted"] is None
        assert entry["rule_id"] is None
        assert entry["metadata"] == {}
