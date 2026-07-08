from sage.agent import Agent
from sage.demo_runner import _offline_reflection_model


def make_agent(tmp_path):
    return Agent(
        project_dir=str(tmp_path),
        model_caller=_offline_reflection_model,
        simulate=True,
    )


def test_snapshot_reads_cases_preferences_and_lifecycle_through_one_interface(
    tmp_path,
):
    with make_agent(tmp_path) as agent:
        case = agent.cases.record(
            task="Deploy Python API",
            outcome="failed",
            steps=[],
            error="port blocked",
        )
        agent.memory.set_preference("region", "us-west-1")

        snapshot = agent.memory.snapshot(recent_limit=10)

        assert snapshot["cases"]["recent"][0]["case_id"] == case["case_id"]
        assert snapshot["preferences"]["values"]["region"]["value"] == "us-west-1"
        assert "memory_health" in snapshot["lifecycle"]
        assert snapshot["retrieval"]["total_entries"] >= 0


def test_rule_transitions_refresh_retrieval_and_report_missing_rules(tmp_path):
    with make_agent(tmp_path) as agent:
        agent.procedural.add_rule(
            "Open port 8080 before deployment",
            "python deployment",
            0.5,
            rule_id="R001",
        )

        pinned = agent.memory.pin_rule("R001")
        edited = agent.memory.edit_rule("R001", "Open port 8000 before deployment")
        rejected = agent.memory.edit_rule("R001", "   ")
        missing = agent.memory.retire_rule("R999")

        rule = agent.memory.snapshot()["procedural"]["rules"][0]
        assert pinned is True
        assert edited is True
        assert rejected is False
        assert missing is False
        assert rule["text"] == "Open port 8000 before deployment"
        assert float(rule["confidence"]) >= 0.95
        assert (
            agent.retrieval.query("port 8000", types=["rule"])[0].memory_id
            == "R001"
        )


def test_maintenance_returns_report_and_rebuilds_retrieval(tmp_path):
    with make_agent(tmp_path) as agent:
        report = agent.memory.maintain()

        assert "consolidation_report" in report
        assert "retrieval" in report
        assert report["retrieval"]["total_entries"] == 0
