"""End-to-end checks for the public demo path."""

import sys

import sage.demo_runner as demo_runner
from sage.__main__ import main
from sage.agent import Agent
from sage.demo_runner import run_demo


def test_execute_task_returns_memory_trace_after_learning(tmp_path):
    from sage.demo_runner import _offline_agent_step

    def model(prompt: str, **kwargs) -> str:
        # Agent-loop turn: drive the loop deterministically.
        if "progress_json:" in prompt.lower():
            return _offline_agent_step(prompt)
        # Reflection turn: return a rule that carries the company port.
        return """{
          "rule": "This organization's web apps bind to port 8080. Open port 8080 in the security group before deploying.",
          "context": "Alibaba Cloud ECS deployment",
          "confidence": 0.95,
          "precondition": "security_group_ports_open",
          "repair": "open_port",
          "effect": "network_service_reachable"
        }"""

    agent = Agent(project_dir=str(tmp_path), model_caller=model, simulate=True)

    first = agent.run.execute("Deploy Node.js web app to Alibaba Cloud ECS")
    assert first["outcome"] == "failed"
    assert first["memory_trace"] == []

    learned = agent.handle_correction(
        task=first["task"],
        action_taken="opened only 80/443, so the app on its real port was unreachable",
        error=first["response"],
        correction="Open port 8080 in the security group before deploying.",
    )

    second = agent.run.execute("Deploy Python Flask API to Alibaba Cloud ECS")
    assert second["outcome"] == "success"
    assert learned["rule_id"] in second["policies_applied"]
    assert any(
        item["memory_id"] == learned["rule_id"] and item["influence"] == "applied"
        for item in second["memory_trace"]
    )


def test_offline_demo_uses_real_agent_execution_path(tmp_path, capsys):
    result = run_demo(str(tmp_path), offline=True)
    captured = capsys.readouterr().out

    assert result["mode"] == "offline"
    assert result["rules_learned"] >= 1  # At least 1, now 2 with multiple corrections
    assert "DEMO COMPLETE" in captured

    history = result["evaluator"].get_history()
    assert history[0]["outcome"] == "failed"  # First task fails
    assert history[-1]["outcome"] == "success"  # Last task succeeds


def test_cli_judge_friendly_aliases(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["sage", "--memory-state", "--project-dir", str(tmp_path)],
    )
    main()
    memory_output = capsys.readouterr().out
    assert '"working"' in memory_output

    monkeypatch.setattr(
        sys,
        "argv",
        ["sage", "--diagram", "--project-dir", str(tmp_path)],
    )
    main()
    diagram_output = capsys.readouterr().out
    assert "No provenance data yet" in diagram_output

    monkeypatch.setattr(sys, "argv", ["sage", "--version"])
    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0
    version_output = capsys.readouterr().out
    assert version_output.startswith("Sage ")


def test_live_demo_loads_qwen_key_from_dotenv(tmp_path, monkeypatch):
    """Live demo mode should honor the repo .env file before failing."""
    monkeypatch.delenv("SAGE_QWEN_API_KEY", raising=False)
    (tmp_path / ".env").write_text("SAGE_QWEN_API_KEY=from-dotenv\n")

    class FakeProcedural:
        def get_rules_for_prompt(self):
            return "Learned rule"

        def get_rule_count(self):
            return 2

    class FakeCases:
        def get_recent(self, _limit):
            return [{"rules_applied": ["R001"], "policies_applied": ["R002"]}]

    class FakeEmbeddingStore:
        def get_stats(self):
            return {"api_calls": 0, "tokens_embedded": 0}

    class FakeSession:
        def end(self):
            pass

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            assert kwargs["use_qwen"] is True
            self.procedural = FakeProcedural()
            self.cases = FakeCases()
            self.embedding_store = FakeEmbeddingStore()
            self.session = FakeSession()
            self.metrics = {"corrections": 2}
            self._calls = 0
            self.run = self
            self.memory = self

        def execute(self, _task):
            self._calls += 1
            if self._calls == 1:
                return {"outcome": "failed", "response": "missing security group"}
            return {
                "outcome": "success",
                "steps": [{"step": "authorize ingress"}],
                "policies_applied": ["R001"],
                "memory_trace": [{"memory_id": "R001"}],
                "rules_applied": ["R001"],
            }

        def handle_correction(self, **_kwargs):
            return {
                "rule_id": "R001",
                "rule": "Open required ports",
                "confidence": 0.95,
            }

        def evaluate_counterfactual(self, _task):
            return {
                "with_memory": {"outcome": "success"},
                "without_memory": {"outcome": "failed"},
            }

        def snapshot(self, **_kwargs):
            return {
                "episodic": {"stats": {"total": 2}},
                "semantic": {"documents": ["doc"]},
                "procedural": {"count": 2, "formatted": "Learned rule"},
                "cases": {
                    "stats": {"total": 1},
                    "recent": self.cases.get_recent(100),
                },
                "provenance": {"stats": {"edges": 1}},
                "embeddings": {"entries": 1},
                "context_budget": {"total_budget": 8000},
            }

        def end_session(self):
            return {}

        def get_token_usage(self):
            return {"total_tokens": 0}

    class FakeEvaluator:
        def __init__(self, _project_dir):
            pass

        def format_demo_stats(self, **_kwargs):
            return "stats"

    monkeypatch.setattr(demo_runner, "Agent", FakeAgent)
    monkeypatch.setattr(demo_runner, "Evaluator", FakeEvaluator)

    result = demo_runner.run_demo(str(tmp_path), offline=False)
    assert result["mode"] == "live"
