"""Tests for the prompt blocks compiler and benchmark module."""

from sage.memory.prompt_blocks import PromptBlockCompiler
from sage.memory.procedural import ProceduralMemory
from sage.memory.preferences import PreferenceMemory
from sage.memory.cases import CaseMemory
from sage.memory.skills import SkillLibrary


# ─── PromptBlockCompiler ──────────────────────────────────────────────────────


class TestPromptBlockCompiler:
    def test_empty_compiler_returns_empty(self, tmp_path):
        """Compiler with no stores produces empty prompt."""
        compiler = PromptBlockCompiler()
        result = compiler.compile("Deploy app", "node")
        assert result.full_text == ""
        assert result.blocks == []

    def test_runbook_rules_block(self, tmp_path):
        """Rules are included in the runbook_rules block."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Open port 8080 before deploying", "ECS", 0.9, rule_id="R001")

        compiler = PromptBlockCompiler(procedural=pm)
        result = compiler.compile("Deploy app")

        block = result.get_block("runbook_rules")
        assert block is not None
        assert "8080" in block.content
        assert "R001" in block.source_ids

    def test_org_facts_requires_high_confidence_and_applied(self, tmp_path):
        """Org facts only include high-confidence rules that have been applied."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Low confidence rule", "ctx", 0.3, rule_id="R001")
        pm.add_rule("High but never applied", "ctx", 0.9, rule_id="R002")

        compiler = PromptBlockCompiler(procedural=pm)
        result = compiler.compile("Deploy app")

        block = result.get_block("org_facts")
        # Neither rule qualifies: R001 too low confidence, R002 never applied
        assert block is None or block.empty

    def test_org_facts_includes_applied_high_conf_rules(self, tmp_path):
        """Org facts include rules with high confidence AND at least one application."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Open port 8080", "ECS", 0.9, rule_id="R001")
        pm.increment_application("R001")

        compiler = PromptBlockCompiler(procedural=pm)
        result = compiler.compile("Deploy app")

        block = result.get_block("org_facts")
        assert block is not None
        assert not block.empty
        assert "8080" in block.content

    def test_recent_failures_block(self, tmp_path):
        """Recent failures are included when failed cases exist."""
        cases = CaseMemory(str(tmp_path / "cases.jsonl"))
        cases.record(
            task="Deploy web app",
            outcome="failed",
            steps=[],
            error="port blocked",
            failure_point="health_check",
        )

        compiler = PromptBlockCompiler(cases=cases)
        result = compiler.compile("Deploy app")

        block = result.get_block("recent_failures")
        assert block is not None
        assert "port blocked" in block.content

    def test_relevant_skill_block(self, tmp_path):
        """Matched skill is included when a skill exists."""
        skills = SkillLibrary(str(tmp_path / "skills.jsonl"))
        skills.record_skill(
            task="Deploy Node.js app",
            app_type="node",
            steps=[{"step": "open_port", "tool": "open_port"}],
            tools_used=["open_port", "create_instance", "deploy"],
        )

        compiler = PromptBlockCompiler(skills=skills)
        result = compiler.compile("Deploy Node.js service", "node")

        block = result.get_block("relevant_skill")
        assert block is not None
        assert "open_port" in block.content

    def test_compiled_prompt_summary(self, tmp_path):
        """Summary includes block metadata."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule A", "ctx", 0.9, rule_id="R001")

        compiler = PromptBlockCompiler(procedural=pm)
        result = compiler.compile("Deploy app")

        summary = result.summary()
        assert "blocks" in summary
        assert summary["total_chars"] > 0
        assert any(b["name"] == "runbook_rules" for b in summary["blocks"])

    def test_injected_ids_tracks_all_sources(self, tmp_path):
        """injected_ids collects IDs from all active blocks."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Open port 8080 for web apps", "ECS deploy", 0.9, rule_id="R001")
        pm.add_rule("Install runtime before deploy", "ECS setup", 0.95, rule_id="R002")
        pm.increment_application("R001")
        pm.increment_application("R002")

        compiler = PromptBlockCompiler(procedural=pm)
        result = compiler.compile("Deploy app")

        ids = result.injected_ids()
        # Both rules should appear (in runbook_rules and possibly org_facts)
        assert "R001" in ids
        assert "R002" in ids

    def test_preferences_block(self, tmp_path):
        """Preferences are included when set."""
        prefs = PreferenceMemory(str(tmp_path / "prefs.json"))
        prefs.set_preference("region", "us-west-1", source="user")

        compiler = PromptBlockCompiler(preferences=prefs)
        result = compiler.compile("Deploy app")

        block = result.get_block("preferences")
        assert block is not None
        assert "us-west-1" in block.content


# ─── Benchmark Module ─────────────────────────────────────────────────────────


class TestBenchmark:
    def test_scenarios_have_required_fields(self):
        from sage.benchmark import SCENARIOS

        for s in SCENARIOS:
            assert s.id
            assert s.name
            assert s.task
            assert s.app_type
            assert s.expected_port > 0

    def test_format_benchmark_summary(self):
        from sage.benchmark import ScenarioResult, Scenario, format_benchmark_summary

        scenario = Scenario(
            id="T01",
            name="Test",
            task="Deploy",
            app_type="docker",
            requires_rule=None,
            expected_port=80,
            description="test",
        )
        results = [
            ScenarioResult(
                scenario=scenario,
                outcome="success",
                expected_outcome="success",
                passed=True,
                opened_ports=[80],
                memory_present=False,
                iterations_used=5,
            )
        ]
        summary = format_benchmark_summary(results)
        assert summary["total"] == 1
        assert summary["passed"] == 1
        assert summary["pass_rate"] == 1.0

    def test_run_benchmark_offline(self, tmp_path):
        """Benchmark runs against a real offline agent."""
        from sage.agent import Agent
        from sage.demo_runner import _offline_reflection_model
        from sage.benchmark import run_benchmark, format_benchmark_summary, SCENARIOS

        agent = Agent(
            project_dir=str(tmp_path),
            model_caller=_offline_reflection_model,
            simulate=True,
        )

        # Only run docker/static (no-memory-needed) scenarios for speed
        no_memory_scenarios = [s for s in SCENARIOS if s.requires_rule is None]
        results = run_benchmark(agent, no_memory_scenarios)
        summary = format_benchmark_summary(results)

        assert summary["total"] == len(no_memory_scenarios)
        # Docker and static should pass without memory
        assert summary["passed"] >= 1
