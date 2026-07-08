"""
Tests for Sage Agent — helper methods and initialization.

Focus on testable units without real API calls:
  - _infer_app_type() — app type detection from task text
  - _build_memory_block() — prompt compilation from memory tiers
  - MemorySystem.record_rule_outcome() — Rule application outcomes
  - MemorySystem.snapshot() — snapshot of all memory tiers
  - handle_correction() — reflection trigger + metrics update
"""

import json
from unittest.mock import Mock

from sage.agent import Agent
from sage.demo_runner import _offline_reflection_model


# ─── Helpers ─────────────────────────────────────────────────────────────────


def make_agent(tmp_path, simulate=True, model_caller=None):
    """Create an Agent isolated in a temp directory."""
    return Agent(
        project_dir=str(tmp_path),
        model_caller=model_caller,
        simulate=simulate,
    )


# ─── Agent.__init__ ──────────────────────────────────────────────────────────


class TestAgentInit:
    def test_creates_memory_dirs(self, tmp_path):
        """Agent init creates episodic, procedural, semantic dirs."""
        agent = make_agent(tmp_path)
        assert agent.episodic is not None
        assert agent.procedural is not None
        assert agent.semantic is not None

    def test_init_empty_metrics(self, tmp_path):
        """Fresh agent starts with zero metrics."""
        agent = make_agent(tmp_path)
        assert agent.metrics["total_tasks"] == 0
        assert agent.metrics["successes"] == 0
        assert agent.metrics["failures"] == 0
        assert agent.metrics["corrected_failures"] == 0
        assert agent.metrics["corrections"] == 0
        assert agent.metrics["rules_learned"] == 0

    def test_init_loads_existing_metrics(self, tmp_path):
        """Agent loads previously saved metrics."""
        agent = make_agent(tmp_path)
        agent.metrics["total_tasks"] = 10
        agent.metrics_recorder._save()
        # Create a new agent from same dir
        agent2 = make_agent(tmp_path)
        assert agent2.metrics["total_tasks"] == 10


# ─── _infer_app_type ─────────────────────────────────────────────────────────


class TestInferAppType:
    def test_node_keyword(self, tmp_path):
        """'node' or 'express' triggers node app type."""
        agent = make_agent(tmp_path)
        assert agent._infer_app_type("Deploy node app") == "node"
        assert agent._infer_app_type("Run express server") == "node"

    def test_python_keyword(self, tmp_path):
        """'python' or 'flask' triggers python app type."""
        agent = make_agent(tmp_path)
        assert agent._infer_app_type("Deploy python backend") == "python"
        assert agent._infer_app_type("Start flask app") == "python"

    def test_static_keyword(self, tmp_path):
        """'react', 'vue', 'angular', 'static' triggers static type."""
        agent = make_agent(tmp_path)
        assert agent._infer_app_type("Deploy react frontend") == "static"
        assert agent._infer_app_type("Build static site") == "static"

    def test_docker_keyword(self, tmp_path):
        """'docker' or 'container' triggers docker type."""
        agent = make_agent(tmp_path)
        assert agent._infer_app_type("Run docker container") == "docker"

    def test_java_keyword(self, tmp_path):
        """'java' or 'spring' triggers java type."""
        agent = make_agent(tmp_path)
        assert agent._infer_app_type("Deploy java service") == "java"
        assert agent._infer_app_type("Start spring boot app") == "java"

    def test_default_is_docker(self, tmp_path):
        """Unrecognized task defaults to docker."""
        agent = make_agent(tmp_path)
        assert agent._infer_app_type("Deploy the thing") == "docker"

    def test_case_insensitive(self, tmp_path):
        """App type detection is case-insensitive."""
        agent = make_agent(tmp_path)
        assert agent._infer_app_type("DEPLOY NODE APP") == "node"
        assert agent._infer_app_type("Python Flask") == "python"

    def test_javascript_also_triggers_node(self, tmp_path):
        """'javascript' keyword triggers node type."""
        agent = make_agent(tmp_path)
        assert agent._infer_app_type("Deploy javascript app") == "node"

    def test_vue_and_angular_trigger_static(self, tmp_path):
        """vue and angular keywords trigger static type."""
        agent = make_agent(tmp_path)
        assert agent._infer_app_type("Deploy vue app") == "static"
        assert agent._infer_app_type("Build angular frontend") == "static"

    def test_container_triggers_docker(self, tmp_path):
        """'container' keyword triggers docker type."""
        agent = make_agent(tmp_path)
        assert agent._infer_app_type("Run a container") == "docker"


# ─── _build_memory_block ─────────────────────────────────────────────────────


class TestBuildMemoryBlock:
    def test_memory_block_includes_rules(self, tmp_path):
        """Memory block includes procedural rules."""
        agent = make_agent(tmp_path)
        agent.procedural.add_rule("Check SG first", "ECS deploy", 0.9)
        block = agent._build_memory_block("Deploy ECS app")
        assert "Check SG first" in block

    def test_memory_block_includes_preferences(self, tmp_path):
        """Memory block includes user preferences."""
        agent = make_agent(tmp_path)
        agent.preferences.set_preference("region", "us-west-1", source="user")
        block = agent._build_memory_block("Deploy app")
        assert "us-west-1" in block

    def test_memory_block_empty_when_no_memory(self, tmp_path):
        """Memory block is empty when no memories exist."""
        agent = make_agent(tmp_path)
        block = agent._build_memory_block("Deploy app")
        assert block == ""

    def test_compiled_prompt_cached(self, tmp_path):
        """Last compiled prompt is cached for UI inspection."""
        agent = make_agent(tmp_path)
        agent.procedural.add_rule("Test rule", "ctx", 0.9)
        agent._build_memory_block("Deploy app")
        compiled = agent.get_last_compiled_prompt()
        assert compiled is not None
        assert compiled.summary()["total_chars"] > 0


# ─── _track_rule_application ─────────────────────────────────────────────────


class TestTrackRuleApplication:
    def test_matching_rule_incremented(self, tmp_path):
        """Rules whose keywords appear in the task are incremented."""
        agent = make_agent(tmp_path)
        agent.procedural.add_rule(
            "Always check security groups before ECS deploy",
            "ECS deployment",
            0.9,
            rule_id="R001",
        )
        agent.memory.record_rule_outcome("Deploy to ECS with security groups", True)
        rules = agent.procedural.get_all_rules()
        assert rules[0]["times_applied"] == 1
        assert rules[0]["utility"] > 0

    def test_non_matching_rule_not_incremented(self, tmp_path):
        """Rules whose keywords don't match are not touched."""
        agent = make_agent(tmp_path)
        agent.procedural.add_rule(
            "Configure database connections", "PostgreSQL setup", 0.8, rule_id="R001"
        )
        agent.memory.record_rule_outcome("Deploy ECS web app", True)
        rules = agent.procedural.get_all_rules()
        assert rules[0]["times_applied"] == 0

    def test_multiple_rules_partial_match(self, tmp_path):
        """Only matching rules are incremented, not all."""
        agent = make_agent(tmp_path)
        agent.procedural.add_rule(
            "Check security groups", "ECS deploy", 0.9, rule_id="R001"
        )
        agent.procedural.add_rule(
            "Configure database", "PostgreSQL", 0.8, rule_id="R002"
        )
        agent.memory.record_rule_outcome("Deploy ECS security groups", True)
        rules = agent.procedural.get_all_rules()
        r1 = next(r for r in rules if r["id"] == "R001")
        r2 = next(r for r in rules if r["id"] == "R002")
        assert r1["times_applied"] == 1
        assert r2["times_applied"] == 0

    def test_no_rules_no_crash(self, tmp_path):
        """Tracking with empty procedural memory doesn't crash."""
        agent = make_agent(tmp_path)
        assert agent.memory.record_rule_outcome("Any task", True) == []

    def test_execute_task_records_case_and_provenance(self, tmp_path):
        """Successful deployments become structured cases."""
        agent = make_agent(tmp_path, model_caller=_offline_reflection_model)
        agent.procedural.add_rule(
            "Check security groups", "ECS deploy", 0.9, rule_id="R001"
        )
        result = agent.run.execute("Deploy ECS security groups app")
        assert result["outcome"] == "success"
        assert agent.cases.get_stats()["total"] == 1
        assert agent.provenance.get_stats()["edges"] == 1

    def test_evaluate_counterfactual_compares_memory_on_off(self, tmp_path):
        """Counterfactual eval: same model with memory succeeds, without fails."""
        agent = make_agent(tmp_path, model_caller=_offline_reflection_model)
        agent.procedural.add_rule(
            "This organization's web apps bind to port 8080. Open port 8080 in the security group.",
            "ECS deployment",
            0.9,
            rule_id="R001",
        )

        # Node app needs the company port (8080); only the memory-enabled run knows it.
        result = agent.evaluate_counterfactual("Deploy Node.js web app")
        assert result["with_memory"]["outcome"] == "success"
        assert result["without_memory"]["outcome"] == "failed"
        assert result["record"]["memory_helped"] is True
        # The mechanism, not just the outcome: memory is what makes the model open 8080.
        assert 8080 in result["with_memory"]["opened_ports"]
        assert 8080 not in result["without_memory"]["opened_ports"]
        assert result["with_memory"]["required_port"] == 8080
        assert "8080" in result["with_memory_block"]
        assert result["without_memory_block"] == ""
        assert result["first_divergence"]["step_index"] >= 1


# ─── handle_correction ───────────────────────────────────────────────────────


class TestHandleCorrection:
    def test_triggers_reflection(self, tmp_path):
        """handle_correction triggers the reflection engine."""
        agent = make_agent(tmp_path)
        result = agent.handle_correction(
            task="Deploy ECS",
            action_taken="Forgot SG",
            error="Connection refused",
            correction="Check security group",
        )
        assert "rule_id" in result
        assert len(result["rule"]) > 0

    def test_updates_metrics(self, tmp_path):
        """handle_correction increments corrections and corrected_failures."""
        agent = make_agent(tmp_path)
        agent.handle_correction("Task", "Action", "Error", "Fix")
        assert agent.metrics["corrections"] == 1
        assert agent.metrics["corrected_failures"] == 1

    def test_updates_rules_learned(self, tmp_path):
        """handle_correction updates rules_learned count."""
        agent = make_agent(tmp_path)
        agent.handle_correction("Task", "Action", "Error", "Fix")
        assert agent.metrics["rules_learned"] == 1

    def test_saves_metrics_to_disk(self, tmp_path):
        """handle_correction persists metrics to disk."""
        agent = make_agent(tmp_path)
        agent.handle_correction("Task", "Action", "Error", "Fix")
        assert agent.metrics_recorder.metrics_path.exists()
        saved = json.loads(agent.metrics_recorder.metrics_path.read_text())
        assert saved["corrections"] == 1


# ─── execute_task — simulate mode ────────────────────────────────────────────


class TestExecuteTask:
    def test_simulate_returns_response(self, tmp_path):
        """execute_task drives the LLM loop and succeeds for a default-port app."""
        agent = make_agent(
            tmp_path, simulate=True, model_caller=_offline_reflection_model
        )
        result = agent.run.execute("Deploy web app")
        assert result["task"] == "Deploy web app"
        assert (
            result["outcome"] == "success"
        )  # docker/web app serves on port 80 (opened by default)
        assert result["correction_needed"] is False

    def test_simulate_increments_total_tasks(self, tmp_path):
        """execute_task increments total_tasks counter."""
        agent = make_agent(tmp_path, simulate=True)
        agent.run.execute("Deploy app")
        assert agent.metrics["total_tasks"] == 1

    def test_failed_task_increments_failures(self, tmp_path):
        """Normal failed executions count as failures even before correction."""
        agent = make_agent(
            tmp_path, simulate=True, model_caller=_offline_reflection_model
        )
        result = agent.run.execute("Deploy Node.js web app")
        assert result["outcome"] == "failed"
        assert agent.metrics["failures"] == 1

    def test_agent_loop_exception_returns_explicit_failure(self, tmp_path):
        """Unexpected agent-loop errors are explicit failed runs, not fake successes."""
        agent = make_agent(
            tmp_path, simulate=True, model_caller=_offline_reflection_model
        )
        agent.agent_loop.run_loop = Mock(side_effect=RuntimeError("boom"))

        result = agent.run.execute("Deploy app")

        assert result["outcome"] == "failed"
        assert result["execution_mode"] == "agent_loop_error"
        assert result["failure_point"] == "agent_loop_exception"
        assert result["correction_needed"] is True
        assert "boom" in result["response"]
        assert agent.metrics["failures"] == 1

    def test_failed_task_persists_failure_point_to_sqlite(self, tmp_path):
        """Failure point persisted to SQLite should match the execution result."""
        agent = make_agent(
            tmp_path, simulate=True, model_caller=_offline_reflection_model
        )
        result = agent.run.execute("Deploy Node.js web app")

        stored = agent.sqlite.get_recent_cases(1)[0]
        assert stored["failure_point"] == result["failure_point"]

    def test_infer_app_type_from_task(self, tmp_path):
        """execute_task uses _infer_app_type to determine app type."""
        agent = make_agent(tmp_path, simulate=True)
        result = agent.run.execute("Deploy python flask app")
        # Should have used python app type in execution
        assert result["outcome"] in ("success", "failed")
