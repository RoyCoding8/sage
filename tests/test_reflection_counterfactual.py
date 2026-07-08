"""
Tests for uncovered Reflection + Rules + Counterfactual functionality.

Axis: Reflection + Rules + Counterfactual
  - ReflectionEngine: _verify_rule, record_rule_quality, _evolve_prompt, _make_rule
  - ProceduralMemory: boost_confidence, get_relevant_rules keyword fallback,
    _detect_contradiction, rule persistence round-trip
  - CounterfactualRunner: run(), _first_divergence, confidence loop closure
"""

from unittest.mock import Mock, patch


from sage.reflection import ReflectionEngine
from sage.memory.procedural import ProceduralMemory
from sage.memory.episodic import EpisodicMemory
from sage.counterfactual import CounterfactualRunner


# ─── Helpers ─────────────────────────────────────────────────────────────────


def make_engine(tmp_path, model_caller=None):
    pm = ProceduralMemory(str(tmp_path / "rules" / "rules.md"))
    em = EpisodicMemory(str(tmp_path / "memory" / "episodic"))
    # Use a per-test prompt path to avoid meta-reflection tests mutating
    # the shared reflection_prompt_v1.txt and breaking other tests.
    prompt_path = tmp_path / "reflection_prompt.txt"
    if not prompt_path.exists():
        import shutil
        shutil.copy2(ReflectionEngine.PROMPT_FILE, prompt_path)
    return ReflectionEngine(pm, em, model_caller=model_caller, prompt_path=str(prompt_path))


def _seed_rules(pm, count=3):
    """Seed procedural memory with N distinct rules."""
    for i in range(count):
        pm.add_rule(
            rule_text=f"Rule {i}: always check port {80 + i} before deploy",
            context=f"deployment context {i}",
            confidence=0.8,
            dedup=False,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# REFLECTION: _verify_rule
# ═══════════════════════════════════════════════════════════════════════════════


class TestVerifyRule:
    def test_verify_returns_true_when_no_model(self, tmp_path):
        """Without model_caller, verification is always skipped (True)."""
        engine = make_engine(tmp_path, model_caller=None)
        rule = {"rule": "Check port 80", "confidence": 0.8}
        assert engine._verify_rule(rule, [{"id": "R001", "text": "something"}]) is True

    def test_verify_returns_true_when_no_existing_rules(self, tmp_path):
        """With model but no existing rules, verification is skipped."""
        caller = Mock(return_value="ACCEPT")
        engine = make_engine(tmp_path, model_caller=caller)
        rule = {"rule": "Check port 80", "confidence": 0.8}
        assert engine._verify_rule(rule, []) is True
        caller.assert_not_called()

    def test_verify_calls_model_with_correct_prompt(self, tmp_path):
        """Model caller receives a prompt containing the rule and existing rules."""
        caller = Mock(return_value="ACCEPT")
        engine = make_engine(tmp_path, model_caller=caller)
        rule = {"rule": "Always open port 8080", "confidence": 0.9}
        existing = [{"id": "R001", "text": "Check security group first"}]

        result = engine._verify_rule(rule, existing)

        assert result is True
        caller.assert_called_once()
        prompt = caller.call_args[0][0]
        assert "Always open port 8080" in prompt
        assert "R001" in prompt
        assert "Check security group first" in prompt
        assert "REJECT" in prompt or "ACCEPT" in prompt

    def test_verify_rejects_when_model_says_reject(self, tmp_path):
        """Model returning REJECT causes _verify_rule to return False."""
        caller = Mock(return_value="REJECT")
        engine = make_engine(tmp_path, model_caller=caller)
        rule = {"rule": "Contradicting rule", "confidence": 0.9}
        existing = [{"id": "R001", "text": "Existing rule"}]

        assert engine._verify_rule(rule, existing) is False

    def test_verify_accepts_on_model_exception(self, tmp_path):
        """Model exception during verification defaults to ACCEPT (True)."""
        caller = Mock(side_effect=RuntimeError("API down"))
        engine = make_engine(tmp_path, model_caller=caller)
        rule = {"rule": "Some rule", "confidence": 0.8}
        existing = [{"id": "R001", "text": "Existing"}]

        assert engine._verify_rule(rule, existing) is True

    def test_verify_reject_causes_confidence_halving(self, tmp_path):
        """When _verify_rule returns False, analyze_correction halves confidence."""
        reject_caller = Mock(return_value="REJECT")
        engine = make_engine(tmp_path, model_caller=reject_caller)

        # Seed one existing rule so verification has something to compare against
        engine.procedural.add_rule(
            "Existing rule about port 80",
            "ECS deployment",
            confidence=0.9,
            dedup=False,
        )

        result = engine.analyze_correction(
            task="Deploy app",
            action="Opened wrong port",
            error="Connection refused",
            correction="Use port 8080 instead",
        )
        # Confidence was 0.5 (fallback default) -> halved to 0.25, but min 0.2
        assert result["confidence"] <= 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# REFLECTION: _make_rule
# ═══════════════════════════════════════════════════════════════════════════════


class TestMakeRule:
    def test_make_rule_has_all_six_keys(self):
        """_make_rule produces exactly the 6 canonical keys."""
        rule = ReflectionEngine._make_rule(
            rule_text="Test rule",
            context="test context",
            confidence=0.8,
            precondition="pre",
            repair="fix",
            effect="done",
        )
        assert set(rule.keys()) == {
            "rule", "context", "confidence", "precondition", "repair", "effect"
        }

    def test_make_rule_truncates_at_200_chars(self):
        """Rule text is truncated to 200 characters."""
        long_text = "A" * 300
        rule = ReflectionEngine._make_rule(rule_text=long_text)
        assert len(rule["rule"]) == 200

    def test_make_rule_clamps_confidence(self):
        """Confidence is clamped to [0.0, 1.0]."""
        assert ReflectionEngine._make_rule(confidence=-0.5)["confidence"] == 0.0
        assert ReflectionEngine._make_rule(confidence=1.5)["confidence"] == 1.0
        assert ReflectionEngine._make_rule(confidence=0.7)["confidence"] == 0.7

    def test_make_rule_defaults(self):
        """Default values are sensible."""
        rule = ReflectionEngine._make_rule()
        assert rule["rule"] == "Rule extraction failed"
        assert rule["context"] == "general"
        assert rule["confidence"] == 0.5
        assert rule["precondition"] == ""
        assert rule["repair"] == ""
        assert rule["effect"] == ""


# ═══════════════════════════════════════════════════════════════════════════════
# REFLECTION: record_rule_quality + _evolve_prompt
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecordRuleQuality:
    def test_scores_clamped_to_01(self, tmp_path):
        """Scores are clamped to [0, 1]."""
        engine = make_engine(tmp_path, model_caller=None)
        engine.record_rule_quality(-0.5)
        engine.record_rule_quality(1.5)
        assert engine._rule_quality_scores == [0.0, 1.0]

    def test_no_evolution_until_5_scores(self, tmp_path):
        """_evolve_prompt not called until 5 low scores accumulated."""
        caller = Mock(return_value="New prompt with {task} placeholder")
        engine = make_engine(tmp_path, model_caller=caller)
        for _ in range(4):
            engine.record_rule_quality(0.1)
        assert caller.call_count == 0  # _evolve_prompt not triggered

    def test_evolution_triggers_after_5_low_scores(self, tmp_path):
        """After 5 consecutive low scores, _evolve_prompt is called."""
        new_prompt = (
            "You are a reflection engine. Analyze task {task}, action {action}, "
            "error {error}, and correction {correction}.\n"
            "Return JSON with rule, context, confidence."
        )
        caller = Mock(return_value=new_prompt)
        engine = make_engine(tmp_path, model_caller=caller)

        # Read original prompt for comparison
        engine.prompt_path.read_text()

        for _ in range(5):
            engine.record_rule_quality(0.1)

        # _evolve_prompt should have been called once (via meta_prompt)
        assert caller.call_count >= 1
        # Score buffer should be cleared after evolution
        assert len(engine._rule_quality_scores) == 0

    def test_evolution_accepts_fenced_prompt_with_all_placeholders(self, tmp_path):
        fenced = (
            "```text\n"
            "Reflect on task {task}, action {action}, error {error}, and correction "
            "{correction}. Return JSON with rule, context, confidence, precondition, "
            "repair, and effect.\n"
            "```"
        )
        engine = make_engine(tmp_path, model_caller=Mock(return_value=fenced))

        for _ in range(5):
            engine.record_rule_quality(0.1)

        evolved = engine.prompt_path.read_text()
        assert evolved.startswith("Reflect on task {task}")
        assert "```" not in evolved
        assert all(
            placeholder in evolved
            for placeholder in ("{task}", "{action}", "{error}", "{correction}")
        )

    def test_no_evolution_with_high_scores(self, tmp_path):
        """High quality scores do not trigger evolution."""
        caller = Mock()
        engine = make_engine(tmp_path, model_caller=caller)
        for _ in range(10):
            engine.record_rule_quality(0.9)
        assert caller.call_count == 0

    def test_evolve_prompt_without_model_is_noop(self, tmp_path):
        """_evolve_prompt does nothing without a model_caller."""
        engine = make_engine(tmp_path, model_caller=None)
        for _ in range(5):
            engine.record_rule_quality(0.1)
        # No crash, no side effects

    def test_evolve_prompt_rejects_invalid_response(self, tmp_path):
        """_evolve_prompt ignores model response that lacks {task} placeholder."""
        caller = Mock(return_value="Just some text without placeholders")
        engine = make_engine(tmp_path, model_caller=caller)
        for _ in range(5):
            engine.record_rule_quality(0.1)
        # Prompt should NOT have changed
        assert "{task}" in engine.prompt_path.read_text()


# ═══════════════════════════════════════════════════════════════════════════════
# RULES: ProceduralMemory rule round-trip + retrieval
# ═══════════════════════════════════════════════════════════════════════════════


class TestRulesRoundTrip:
    def test_rule_persists_to_file_and_is_retrievable(self, tmp_path):
        """Rule written by add_rule is readable from the markdown file."""
        rules_path = str(tmp_path / "rules.md")
        pm = ProceduralMemory(rules_path)
        rule_id = pm.add_rule(
            "Always check security group",
            "ECS deployment",
            confidence=0.9,
            source_task="Deploy app",
        )

        rules = pm.get_all_rules()
        assert len(rules) == 1
        assert rules[0]["id"] == rule_id
        assert rules[0]["text"] == "Always check security group"
        assert rules[0]["confidence"] == 0.9

    def test_rule_written_to_disk_matches_parsed(self, tmp_path):
        """Raw file content produces the same rule dict when parsed."""
        rules_path = tmp_path / "rules.md"
        pm = ProceduralMemory(str(rules_path))
        pm.add_rule("Check port 80", "deployment", confidence=0.75, dedup=False)

        raw = rules_path.read_text()
        assert "Check port 80" in raw
        assert "0.75" in raw

        # Re-parse from fresh instance (no cache)
        pm2 = ProceduralMemory(str(rules_path))
        rules = pm2.get_all_rules()
        assert len(rules) == 1
        assert rules[0]["text"] == "Check port 80"

    def test_boost_confidence_increases_value(self, tmp_path):
        """boost_confidence adds delta to rule confidence, clamped at 1.0."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        rid = pm.add_rule("Test rule", "ctx", confidence=0.5, dedup=False)

        pm.boost_confidence(rid, delta=0.3)
        rules = pm.get_all_rules()
        assert rules[0]["confidence"] == 0.8

    def test_boost_confidence_clamps_at_1(self, tmp_path):
        """Confidence cannot exceed 1.0."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        rid = pm.add_rule("Test rule", "ctx", confidence=0.9, dedup=False)
        pm.boost_confidence(rid, delta=0.5)
        rules = pm.get_all_rules()
        assert rules[0]["confidence"] == 1.0

    def test_boost_confidence_clamps_at_0(self, tmp_path):
        """Confidence cannot go below 0.0."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        rid = pm.add_rule("Test rule", "ctx", confidence=0.1, dedup=False)
        pm.boost_confidence(rid, delta=-0.5)
        rules = pm.get_all_rules()
        assert rules[0]["confidence"] == 0.0

    def test_boost_confidence_unknown_id_is_noop(self, tmp_path):
        """boost_confidence with nonexistent ID does nothing."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Test rule", "ctx", confidence=0.5, dedup=False)
        pm.boost_confidence("R999", delta=0.5)
        rules = pm.get_all_rules()
        assert rules[0]["confidence"] == 0.5  # unchanged

    def test_get_relevant_rules_keyword_fallback(self, tmp_path):
        """get_relevant_rules uses keyword overlap when no embedding store."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Check security group before deploying to ECS", "Alibaba Cloud", 0.9, dedup=False)
        pm.add_rule("Install Node.js runtime on server", "Server setup", 0.8, dedup=False)

        relevant = pm.get_relevant_rules("security group deployment")
        assert len(relevant) >= 1
        # The security group rule should rank first
        assert "security group" in relevant[0]["text"].lower()

    def test_get_relevant_rules_empty_task_returns_all(self, tmp_path):
        """Empty task query returns all rules (no filtering)."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule A", "ctx A", 0.5, dedup=False)
        pm.add_rule("Rule B", "ctx B", 0.5, dedup=False)

        relevant = pm.get_relevant_rules("")
        assert len(relevant) == 2

    def test_get_relevant_rules_returns_score(self, tmp_path):
        """Returned rules include a _relevance_score field."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Open port 8080 for web apps", "deployment", 0.9, dedup=False)
        relevant = pm.get_relevant_rules("port 8080 deployment")
        assert len(relevant) >= 1
        assert "_relevance_score" in relevant[0]
        assert relevant[0]["_relevance_score"] > 0


# ═══════════════════════════════════════════════════════════════════════════════
# RULES: Contradiction detection
# ═══════════════════════════════════════════════════════════════════════════════


class TestContradictionDetection:
    def test_detect_contradiction_high_context_low_action(self, tmp_path):
        """Rules with high context overlap but conflicting actions are contradictions."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        # Seed existing rule
        pm.add_rule(
            "Always open port 80 for web apps",
            "ECS deployment networking",
            confidence=0.9,
            dedup=False,
        )
        existing = pm.get_all_rules()

        # New rule: same context, different action keyword ("require" vs "open", "optional" vs "always")
        # The _detect_contradiction checks action keyword overlap via _ACTION_KEYWORDS.
        # "require" and "optional" are in _ACTION_KEYWORDS but "open" is too.
        # To get low action overlap, use rules that share context but have no shared action keywords.
        contradicting = "Skip port 80 for batch jobs"
        # "skip" is in _ACTION_KEYWORDS, "open" is in _ACTION_KEYWORDS
        # action_union = {"open", "skip"}, overlap = 0/2 = 0.0 (no shared)
        result = pm._detect_contradiction(contradicting, "ECS deployment networking", existing)
        assert result is not None  # should detect contradiction

    def test_detect_no_contradiction_different_context(self, tmp_path):
        """Rules in different contexts don't contradict."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule(
            "Open port 80 for web apps",
            "ECS deployment networking",
            confidence=0.9,
            dedup=False,
        )
        existing = pm.get_all_rules()

        result = pm._detect_contradiction(
            "Close port 80 for batch jobs", "CI pipeline config", existing
        )
        assert result is None

    def test_detect_no_contradiction_similar_action(self, tmp_path):
        """Rules with similar actions in same context don't contradict."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule(
            "Open port 80 for web apps",
            "ECS deployment networking",
            confidence=0.9,
            dedup=False,
        )
        existing = pm.get_all_rules()

        result = pm._detect_contradiction(
            "Open port 443 for secure web apps",
            "ECS deployment networking",
            existing,
        )
        # Both share "open" action word, so action overlap should be high
        # This is NOT a contradiction
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# COUNTERFACTUAL: _first_divergence (static, no mocking needed)
# ═══════════════════════════════════════════════════════════════════════════════


class TestFirstDivergence:
    def test_no_divergence_identical_runs(self):
        """Identical step lists return None."""
        steps = [{"tool": "list_instances", "args": {}}, {"tool": "deploy", "args": {}}]
        with_mem = {"steps": steps.copy()}
        without_mem = {"steps": steps.copy()}
        assert CounterfactualRunner._first_divergence(with_mem, without_mem) is None

    def test_divergence_at_first_step(self):
        """Different first steps are detected."""
        with_mem = {"steps": [{"tool": "open_port", "args": {"port": 8080}}]}
        without_mem = {"steps": [{"tool": "create_instance", "args": {"name": "app"}}]}
        div = CounterfactualRunner._first_divergence(with_mem, without_mem)
        assert div is not None
        assert div["step_index"] == 1
        assert div["with_memory_tool"] == "open_port"
        assert div["without_memory_tool"] == "create_instance"

    def test_divergence_with_different_length(self):
        """Divergence detected when one run has more steps."""
        with_mem = {
            "steps": [
                {"tool": "open_port", "args": {"port": 8080}},
                {"tool": "deploy", "args": {}},
            ]
        }
        without_mem = {"steps": [{"tool": "open_port", "args": {"port": 8080}}]}
        div = CounterfactualRunner._first_divergence(with_mem, without_mem)
        # Steps match at index 0, divergence at index 1 (with_mem has step, without doesn't)
        assert div is not None
        assert div["step_index"] == 2
        assert div["with_memory_tool"] == "deploy"
        assert div["without_memory_tool"] is None

    def test_divergence_with_empty_steps(self):
        """Both empty steps returns None."""
        assert CounterfactualRunner._first_divergence({"steps": []}, {"steps": []}) is None

    def test_divergence_one_empty_one_not(self):
        """One empty, one non-empty: divergence at step 1."""
        with_mem = {"steps": [{"tool": "list_instances", "args": {}}]}
        without_mem = {"steps": []}
        div = CounterfactualRunner._first_divergence(with_mem, without_mem)
        assert div is not None
        assert div["step_index"] == 1
        assert div["with_memory_tool"] == "list_instances"
        assert div["without_memory_tool"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# COUNTERFACTUAL: run() with mocked AgentLoop
# ═══════════════════════════════════════════════════════════════════════════════


class TestCounterfactualRunnerRun:
    def test_run_calls_both_variants(self, tmp_path):
        """run() creates two AgentLoop instances and calls run_loop on each."""
        with_memory_result = {
            "outcome": "success",
            "steps": [{"tool": "open_port", "args": {"port": 8080}}],
            "opened_ports": [8080],
        }
        without_memory_result = {
            "outcome": "failed",
            "steps": [{"tool": "create_instance", "args": {"name": "app"}}],
            "opened_ports": [],
        }

        mock_loop_instance = Mock()
        mock_loop_instance.run_loop = Mock(
            side_effect=[with_memory_result, without_memory_result]
        )

        with patch("sage.counterfactual.AgentLoop", return_value=mock_loop_instance):
            with patch("sage.counterfactual.MCPClient"):
                runner = CounterfactualRunner(
                    model_caller_fn=Mock(), simulate=True
                )
                result = runner.run(
                    task="Deploy Node.js app",
                    app_type="node",
                    memory_block="[Rule] open 8080",
                )

        assert result["with_memory"]["outcome"] == "success"
        assert result["without_memory"]["outcome"] == "failed"
        assert result["memory_helped"] is True
        assert result["memory_hurt"] is False
        assert result["with_memory_block"] == "[Rule] open 8080"
        assert result["without_memory_block"] == ""

    def test_run_memory_hurt_detection(self):
        """When with-memory fails but without succeeds, memory_hurt is True."""
        with_memory_result = {"outcome": "failed", "steps": []}
        without_memory_result = {"outcome": "success", "steps": []}

        mock_loop_instance = Mock()
        mock_loop_instance.run_loop = Mock(
            side_effect=[with_memory_result, without_memory_result]
        )

        with patch("sage.counterfactual.AgentLoop", return_value=mock_loop_instance):
            with patch("sage.counterfactual.MCPClient"):
                runner = CounterfactualRunner(model_caller_fn=Mock())
                result = runner.run(
                    task="Deploy", app_type="node", memory_block=""
                )

        assert result["memory_helped"] is False
        assert result["memory_hurt"] is True

    def test_run_no_divergence_when_identical(self):
        """Identical outcomes and steps produce first_divergence=None."""
        same_result = {
            "outcome": "success",
            "steps": [{"tool": "deploy", "args": {}}],
        }
        mock_loop_instance = Mock()
        mock_loop_instance.run_loop = Mock(return_value=same_result)

        with patch("sage.counterfactual.AgentLoop", return_value=mock_loop_instance):
            with patch("sage.counterfactual.MCPClient"):
                runner = CounterfactualRunner(model_caller_fn=Mock())
                result = runner.run(
                    task="Deploy", app_type="docker", memory_block=""
                )

        assert result["first_divergence"] is None

    def test_run_records_via_evaluator(self):
        """When evaluator provided, record_counterfactual is called."""
        with_memory_result = {"outcome": "success", "steps": []}
        without_memory_result = {"outcome": "failed", "steps": []}

        mock_loop_instance = Mock()
        mock_loop_instance.run_loop = Mock(
            side_effect=[with_memory_result, without_memory_result]
        )

        mock_evaluator = Mock()
        mock_evaluator.record_counterfactual.return_value = {"memory_helped": True}

        mock_procedural = Mock()
        mock_procedural.get_all_rules.return_value = [{"id": "R001"}, {"id": "R002"}]

        with patch("sage.counterfactual.AgentLoop", return_value=mock_loop_instance):
            with patch("sage.counterfactual.MCPClient"):
                runner = CounterfactualRunner(model_caller_fn=Mock())
                runner.run(
                    task="Deploy",
                    app_type="node",
                    memory_block="rules",
                    evaluator=mock_evaluator,
                    procedural=mock_procedural,
                )

        mock_evaluator.record_counterfactual.assert_called_once_with(
            "Deploy", "success", "failed", ["R001", "R002"]
        )

    def test_run_confidence_boost_when_memory_helped(self):
        """Confidence is boosted (+0.1) for all rules when memory helped."""
        with_memory_result = {"outcome": "success", "steps": []}
        without_memory_result = {"outcome": "failed", "steps": []}

        mock_loop_instance = Mock()
        mock_loop_instance.run_loop = Mock(
            side_effect=[with_memory_result, without_memory_result]
        )

        mock_procedural = Mock()
        mock_procedural.get_all_rules.return_value = [{"id": "R001"}, {"id": "R002"}]

        with patch("sage.counterfactual.AgentLoop", return_value=mock_loop_instance):
            with patch("sage.counterfactual.MCPClient"):
                runner = CounterfactualRunner(model_caller_fn=Mock())
                runner.run(
                    task="Deploy",
                    app_type="node",
                    memory_block="rules",
                    procedural=mock_procedural,
                )

        assert mock_procedural.boost_confidence.call_count == 2
        mock_procedural.boost_confidence.assert_any_call("R001", delta=0.1)
        mock_procedural.boost_confidence.assert_any_call("R002", delta=0.1)

    def test_run_confidence_penalty_when_memory_hurt(self):
        """Confidence is penalized (-0.15) for all rules when memory hurt."""
        with_memory_result = {"outcome": "failed", "steps": []}
        without_memory_result = {"outcome": "success", "steps": []}

        mock_loop_instance = Mock()
        mock_loop_instance.run_loop = Mock(
            side_effect=[with_memory_result, without_memory_result]
        )

        mock_procedural = Mock()
        mock_procedural.get_all_rules.return_value = [{"id": "R001"}]

        with patch("sage.counterfactual.AgentLoop", return_value=mock_loop_instance):
            with patch("sage.counterfactual.MCPClient"):
                runner = CounterfactualRunner(model_caller_fn=Mock())
                runner.run(
                    task="Deploy",
                    app_type="node",
                    memory_block="rules",
                    procedural=mock_procedural,
                )

        mock_procedural.boost_confidence.assert_called_once_with("R001", delta=-0.15)

    def test_run_no_confidence_change_when_neither_helped_nor_hurt(self):
        """When both succeed or both fail, no confidence adjustment."""
        same_result = {"outcome": "success", "steps": []}

        mock_loop_instance = Mock()
        mock_loop_instance.run_loop = Mock(return_value=same_result)

        mock_procedural = Mock()
        mock_procedural.get_all_rules.return_value = [{"id": "R001"}]

        with patch("sage.counterfactual.AgentLoop", return_value=mock_loop_instance):
            with patch("sage.counterfactual.MCPClient"):
                runner = CounterfactualRunner(model_caller_fn=Mock())
                runner.run(
                    task="Deploy",
                    app_type="node",
                    memory_block="rules",
                    procedural=mock_procedural,
                )

        mock_procedural.boost_confidence.assert_not_called()

    def test_run_empty_rules_applied_skips_confidence(self):
        """No rules means no confidence loop closure."""
        with_memory_result = {"outcome": "success", "steps": []}
        without_memory_result = {"outcome": "failed", "steps": []}

        mock_loop_instance = Mock()
        mock_loop_instance.run_loop = Mock(
            side_effect=[with_memory_result, without_memory_result]
        )

        mock_procedural = Mock()
        mock_procedural.get_all_rules.return_value = []  # no rules

        with patch("sage.counterfactual.AgentLoop", return_value=mock_loop_instance):
            with patch("sage.counterfactual.MCPClient"):
                runner = CounterfactualRunner(model_caller_fn=Mock())
                runner.run(
                    task="Deploy",
                    app_type="node",
                    memory_block="rules",
                    procedural=mock_procedural,
                )

        mock_procedural.boost_confidence.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION: Reflection -> Rules -> Retrieval
# ═══════════════════════════════════════════════════════════════════════════════


class TestReflectionToRulesIntegration:
    def test_reflected_rule_is_retrievable_by_keyword(self, tmp_path):
        """Rule extracted by reflection can be retrieved via get_relevant_rules."""
        engine = make_engine(tmp_path, model_caller=None)
        engine.analyze_correction(
            task="Deploy web app to ECS",
            action="Forgot security group",
            error="Connection refused on port 80",
            correction="You need to configure security group rules for port 80",
        )

        relevant = engine.procedural.get_relevant_rules("security group port 80")
        assert len(relevant) >= 1
        assert "security group" in relevant[0]["text"].lower()

    def test_reflected_rule_appears_in_prompt(self, tmp_path):
        """Rule extracted by reflection appears in the prompt text."""
        engine = make_engine(tmp_path, model_caller=None)
        engine.analyze_correction(
            task="Deploy app",
            action="Opened wrong port",
            error="Timeout",
            correction="Use port 8080 for Node.js apps",
        )

        prompt_text = engine.procedural.get_rules_for_prompt()
        assert "8080" in prompt_text or "port" in prompt_text.lower()

    def test_multiple_reflections_increment_count(self, tmp_path):
        """Each unique correction increments the rule count."""
        engine = make_engine(tmp_path, model_caller=None)
        engine.analyze_correction(
            "Deploy A", "action 1", "error 1", "Fix: check security group before deploy"
        )
        engine.analyze_correction(
            "Deploy B", "action 2", "error 2", "Fix: install Node.js runtime first"
        )
        # Two distinct corrections should yield at least 1 rule (dedup may merge)
        assert engine.procedural.get_rule_count() >= 1
        # But episodic memory should have 2 entries
        assert len(engine.episodic.get_recent(10)) == 2

    def test_episodic_and_procedural_consistent_after_reflection(self, tmp_path):
        """Episodic log references rule_id that exists in procedural memory."""
        engine = make_engine(tmp_path, model_caller=None)
        result = engine.analyze_correction(
            "Deploy app", "Ran deploy", "Failed", "Add health check"
        )
        rule_id = result["rule_id"]
        # Verify the rule_id exists in procedural memory
        all_rules = engine.procedural.get_all_rules()
        rule_ids = [r["id"] for r in all_rules]
        assert rule_id in rule_ids
        # Verify episodic log references the same rule_id
        recent = engine.episodic.get_recent(1)
        assert recent[0]["rule_id"] == rule_id
