from sage.agent import Agent
from sage.demo_runner import _offline_reflection_model
from sage.run import RunContext


def test_run_returns_correlated_ground_truth_and_persisted_evidence(tmp_path):
    with Agent(
        project_dir=str(tmp_path),
        model_caller=_offline_reflection_model,
        simulate=True,
    ) as agent:
        result = agent.run.execute(
            "Deploy Docker application",
            context=RunContext(
                mode="offline",
                provider="offline",
                session_id="test-session",
            ),
        )

        assert result["outcome"] == "success"
        assert len(result["execution"]["trace_id"]) == 12
        assert result["execution"]["session_id"] == "test-session"
        assert result["execution"]["simulated"] is True
        assert result["evidence"]["case_id"]
        assert result["evidence"]["ground_truth"]["required_port"] == 80
        assert result["evidence"]["ground_truth"]["verified"] is True
        assert result["evidence"]["metrics"]["total_tasks"] == 1
        assert agent.memory.snapshot(include={"cases"})["cases"]["stats"]["total"] == 1


def test_failed_run_still_records_case_session_metrics_and_provenance(tmp_path):
    def broken_model(_prompt, **_kwargs):
        raise RuntimeError("model unavailable")

    with Agent(
        project_dir=str(tmp_path),
        model_caller=broken_model,
        simulate=True,
    ) as agent:
        result = agent.run.execute("Deploy Python API")
        snapshot = agent.memory.snapshot(
            include={"cases", "session", "provenance", "metrics"}
        )

        assert result["outcome"] == "failed"
        assert result["evidence"]["case_id"]
        assert snapshot["cases"]["stats"]["total"] == 1
        assert snapshot["session"]["current"]["tasks_completed"] == 1
        assert snapshot["provenance"]["stats"]["nodes"] >= 1
        assert snapshot["metrics"]["failures"] == 1
