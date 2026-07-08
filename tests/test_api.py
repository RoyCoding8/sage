"""Tests for the FastAPI backend (api.py)."""

import pytest
import time
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, Mock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import api


@pytest.fixture
def client(monkeypatch):
    """Create a test client for the API."""
    monkeypatch.setenv("SAGE_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("SAGE_ENABLE_LIVE", "true")
    api._agents.clear()
    api._credentials.clear()
    test_client = TestClient(
        api.app,
        headers={
            "X-Sage-Admin-Token": "test-admin-token",
            "X-Sage-Session-ID": "test-session",
        },
    )
    yield test_client
    test_client.close()
    api._agents.clear()
    api._credentials.clear()


@pytest.fixture
def unauthenticated_client(monkeypatch):
    """Create a client without the required administration token."""
    monkeypatch.setenv("SAGE_ADMIN_TOKEN", "test-admin-token")
    return TestClient(api.app, raise_server_exceptions=False)


@pytest.fixture
def mock_agent():
    """Create a mock agent with common methods."""
    agent = Mock()
    agent.procedural = Mock()
    agent.procedural.get_rule_count.return_value = 5
    agent.procedural.get_all_rules.return_value = [
        {
            "id": "R001",
            "text": "Test rule",
            "confidence": 0.9,
            "utility": 0.8,
            "times_applied": 10,
            "source": "correction",
            "pinned": False,
            "status": "active",
        }
    ]
    agent.procedural.pin_rule = Mock()
    agent.procedural.retire_rule = Mock()

    agent.metrics = {
        "total_tasks": 20,
        "successes": 15,
        "failures": 5,
        "corrections": 3,
        "corrected_failures": 3,
    }

    agent.episodic = Mock()
    agent.episodic.get_recent.return_value = [
        {"task": "Deploy app", "outcome": "success", "timestamp": "2024-01-01T00:00:00"}
    ]

    agent.skills = Mock()
    agent.skills.get_all.return_value = []

    agent.get_memory_state.return_value = {
        "procedural": {"count": 5},
        "episodic": {"count": 20},
    }
    agent.memory = Mock()
    agent.memory.snapshot.return_value = {
        "procedural": {
            "count": 5,
            "rules": agent.procedural.get_all_rules.return_value,
        },
        "episodic": {"recent": agent.episodic.get_recent.return_value},
        "cases": {"recent": [{"case_id": "C001", "task": "Deploy app"}]},
        "skills": {"items": []},
        "provenance": {"stats": {}, "mermaid": "graph TD"},
        "lifecycle": {"memory_health": {}},
        "preferences": {
            "values": {"deployment.region": {"value": "us-west-1"}}
        },
        "session": {"history": [], "cumulative": {}, "current": {}},
        "metrics": agent.metrics,
    }
    agent.memory.pin_rule.return_value = True
    agent.memory.retire_rule.return_value = True
    agent.memory.edit_rule.return_value = True
    agent.memory.maintain.return_value = {"pruned_rules": []}
    agent.memory.refresh.return_value = {"total_entries": 1}

    agent.execute_task.return_value = {
        "task": "Deploy app",
        "outcome": "success",
        "response": "Deployed successfully",
    }
    agent.run = Mock()
    agent.run.execute.return_value = agent.execute_task.return_value
    agent.run.describe.return_value = {
        "mode": "offline",
        "provider": "offline",
        "region": None,
        "simulated": True,
        "session_id": "test-session",
        "trace_id": None,
    }

    agent.handle_correction.return_value = {
        "rule_id": "R002",
        "rule": "New rule from correction",
        "confidence": 0.85,
    }

    agent.evaluate_counterfactual.return_value = {
        "with_memory": {"outcome": "success"},
        "without_memory": {"outcome": "failed"},
        "memory_helped": True,
    }

    return agent


class TestStatusEndpoint:
    def test_api_security_configuration_loads_from_project_dotenv(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("SAGE_ADMIN_TOKEN", raising=False)
        monkeypatch.delenv("SAGE_QWEN_API_KEY", raising=False)
        (tmp_path / ".env").write_text(
            "SAGE_ADMIN_TOKEN=dotenv-admin\n"
            "SAGE_QWEN_API_KEY=dotenv-qwen\n",
            encoding="utf-8",
        )

        api.load_api_environment(tmp_path)

        assert api.os.environ["SAGE_ADMIN_TOKEN"] == "dotenv-admin"
        assert api.os.environ["SAGE_QWEN_API_KEY"] == "dotenv-qwen"

    def test_readiness_fails_when_live_model_key_is_missing(self, client, monkeypatch):
        monkeypatch.setenv("SAGE_ENABLE_LIVE", "true")
        monkeypatch.delenv("SAGE_QWEN_API_KEY", raising=False)

        response = client.get("/api/health/ready")

        assert response.status_code == 503
        assert response.json()["ready"] is False

    def test_invalid_mode_is_a_validation_error(self, client):
        """Unknown execution modes are client errors, not HTTP 500s."""
        response = client.get("/api/status", params={"mode": "bogus"})
        assert response.status_code == 422

    def test_whitespace_only_task_is_a_validation_error(self, client):
        response = client.post("/api/task", json={"task": "  \n\t ", "mode": "offline"})
        assert response.status_code == 422

    def test_get_status_offline(self, client, mock_agent):
        """GET /api/status returns agent status."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.get("/api/status", params={"online": False})
            assert response.status_code == 200
            data = response.json()
            assert data["mode"] == "offline"
            assert data["rules_learned"] == 5
            assert data["total_tasks"] == 20
            assert data["successes"] == 15

    def test_get_status_online(self, client, mock_agent):
        """GET /api/status with online=True."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.get("/api/status", params={"online": True})
            assert response.status_code == 200
            data = response.json()
            assert data["mode"] == "qwen"


class TestTaskEndpoint:
    def test_cloud_mode_requires_credentials(self, client):
        """Real cloud mode fails closed when credentials are absent."""
        api._credentials.clear()
        response = client.post(
            "/api/task", json={"task": "Deploy app", "mode": "cloud"}
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "cloud_credentials_missing"

    def test_execute_task(self, client, mock_agent):
        """POST /api/task executes a deployment task."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.post(
                "/api/task", json={"task": "Deploy Node.js app", "online": False}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["outcome"] == "success"
            mock_agent.run.execute.assert_called_once()
            assert mock_agent.run.execute.call_args.args == ("Deploy Node.js app",)

    def test_execute_task_online(self, client, mock_agent):
        """POST /api/task with online=True."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.post(
                "/api/task", json={"task": "Deploy app", "online": True}
            )
            assert response.status_code == 200

    def test_job_submission_is_idempotent(self, client, mock_agent):
        """Repeating a task submission key starts only one background Run."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            headers = {"Idempotency-Key": "task-request-1"}
            first = client.post(
                "/api/jobs/task",
                headers=headers,
                json={"task": "Deploy app", "mode": "offline"},
            )
            second = client.post(
                "/api/jobs/task",
                headers=headers,
                json={"task": "Deploy app", "mode": "offline"},
            )

            assert first.status_code == 202
            assert second.status_code == 202
            assert first.json()["job_id"] == second.json()["job_id"]

            job_id = first.json()["job_id"]
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                job = client.get(f"/api/jobs/{job_id}").json()
                if job["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(0.01)

            assert job["status"] == "succeeded"
            assert job["result"]["outcome"] == "success"
            mock_agent.run.execute.assert_called_once()


class TestCorrectionEndpoint:
    def test_handle_correction(self, client, mock_agent):
        """POST /api/correction handles user corrections."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.post(
                "/api/correction",
                json={
                    "task": "Deploy app",
                    "action_taken": "Opened port 80",
                    "error": "Connection refused",
                    "fix": "Should open port 8080",
                    "online": False,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert "rule_id" in data
            assert "rule" in data
            mock_agent.handle_correction.assert_called_once()


class TestMemoryEndpoints:
    def test_get_memory(self, client, mock_agent):
        """GET /api/memory returns memory state."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.get("/api/memory", params={"online": False})
            assert response.status_code == 200
            data = response.json()
            assert "procedural" in data
            assert "episodic" in data

    def test_get_rules(self, client, mock_agent):
        """GET /api/memory/rules returns all rules."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.get("/api/memory/rules", params={"online": False})
            assert response.status_code == 200
            data = response.json()
            assert "rules" in data
            assert len(data["rules"]) == 1
            assert data["rules"][0]["id"] == "R001"

    def test_pin_rule(self, client, mock_agent):
        """POST /api/memory/rules/pin pins a rule."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.post(
                "/api/memory/rules/pin", json={"rule_id": "R001", "online": False}
            )
            assert response.status_code == 200
            mock_agent.memory.pin_rule.assert_called_once_with("R001")

    def test_retire_rule(self, client, mock_agent):
        """POST /api/memory/rules/retire retires a rule."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.post(
                "/api/memory/rules/retire", json={"rule_id": "R001", "online": False}
            )
            assert response.status_code == 200
            mock_agent.memory.retire_rule.assert_called_once_with("R001")

    def test_get_skills(self, client, mock_agent):
        """GET /api/memory/skills returns all skills."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.get("/api/memory/skills", params={"online": False})
            assert response.status_code == 200
            data = response.json()
            assert "skills" in data

    def test_get_episodes(self, client, mock_agent):
        """GET /api/memory/episodes returns recent episodes."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.get("/api/memory/episodes", params={"online": False})
            assert response.status_code == 200
            data = response.json()
            assert "episodes" in data

    def test_get_cases_reads_case_memory_snapshot(self, client, mock_agent):
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.get("/api/memory/cases", params={"online": False})

        assert response.status_code == 200
        assert response.json()["cases"][0]["case_id"] == "C001"


class TestCounterfactualEndpoint:
    def test_cloud_counterfactual_is_blocked_to_prevent_duplicate_resources(
        self, client
    ):
        response = client.post(
            "/api/counterfactual",
            json={"task": "Deploy app", "mode": "cloud"},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "cloud_bulk_run_unsupported"

    def test_run_counterfactual(self, client, mock_agent):
        """POST /api/counterfactual runs counterfactual analysis."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.post(
                "/api/counterfactual", json={"task": "Deploy app", "online": False}
            )
            assert response.status_code == 200
            data = response.json()
            assert "with_memory" in data
            assert "without_memory" in data
            mock_agent.evaluate_counterfactual.assert_called_once_with("Deploy app")


class TestDashboardEndpoint:
    def test_get_dashboard(self, client, mock_agent):
        """GET /api/dashboard returns dashboard data."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.get("/api/dashboard", params={"online": False})
            assert response.status_code == 200
            data = response.json()
            assert "status" in data
            assert "memory_summary" in data
            assert "recent_activity" in data


class TestMetricsEndpoints:
    def test_get_metrics(self, client, mock_agent):
        """GET /api/metrics returns metrics."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.get("/api/metrics", params={"online": False})
            assert response.status_code == 200
            data = response.json()
            assert "metrics" in data

    def test_get_metrics_history(self, client, mock_agent, tmp_path):
        """GET /api/metrics/history returns evaluation history."""
        mock_agent.project_dir = tmp_path
        with patch.object(api, "_get_agent", return_value=mock_agent):
            with patch.object(api, "PROJECT_DIR", tmp_path):
                response = client.get("/api/metrics/history", params={"online": False})
                assert response.status_code == 200
                data = response.json()
                assert "history" in data


class TestBenchmarkEndpoint:
    def test_cloud_benchmark_is_blocked_to_prevent_resource_fanout(self, client):
        response = client.post("/api/benchmark", params={"mode": "cloud"})

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "cloud_bulk_run_unsupported"

    def test_run_benchmark(self, client, mock_agent):
        """POST /api/benchmark runs the benchmark suite."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            with patch("api.run_benchmark") as mock_run:
                with patch("api.format_benchmark_summary") as mock_format:
                    mock_run.return_value = []
                    mock_format.return_value = {"total": 0, "passed": 0}
                    response = client.post("/api/benchmark", params={"online": False})
                    assert response.status_code == 200
                    data = response.json()
                    assert "summary" in data
                    assert "results" in data


class TestDemoEndpoint:
    def test_run_demo(self, client):
        """POST /api/demo runs the demo."""
        with patch("api.run_demo") as mock_demo:
            mock_demo.return_value = {"mode": "offline", "rules_learned": 2}
            response = client.post("/api/demo", params={"online": False})
            assert response.status_code == 200
            data = response.json()
            assert "rules_learned" in data
            assert data["mode"] == "offline"


class TestPreferencesEndpoint:
    def test_get_preferences(self, client, mock_agent):
        """GET /api/preferences returns preferences."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.get("/api/preferences", params={"online": False})
            assert response.status_code == 200
            data = response.json()
            assert "preferences" in data

    def test_set_preference(self, client, mock_agent):
        """POST /api/preferences sets a preference."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.post(
                "/api/preferences",
                json={
                    "category": "deployment",
                    "key": "region",
                    "value": "us-west-1",
                    "online": False,
                },
            )
            assert response.status_code == 200
            mock_agent.memory.set_preference.assert_called_once_with(
                "deployment", "us-west-1", key="region"
            )


class TestSessionsEndpoint:
    def test_get_sessions(self, client, mock_agent):
        """GET /api/sessions returns session data."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.get("/api/sessions", params={"online": False})
            assert response.status_code == 200
            data = response.json()
            assert "sessions" in data
            assert "cumulative" in data
            assert "current" in data


class TestMaintenanceEndpoints:
    def test_run_maintenance(self, client, mock_agent):
        """POST /api/memory/maintenance runs maintenance."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.post("/api/memory/maintenance", params={"online": False})
            assert response.status_code == 200
            mock_agent.memory.maintain.assert_called_once()

    def test_refresh_index(self, client, mock_agent):
        """POST /api/memory/refresh-index refreshes memory retrieval."""
        with patch.object(api, "_get_agent", return_value=mock_agent):
            response = client.post(
                "/api/memory/refresh-index", params={"online": False}
            )
            assert response.status_code == 200
            mock_agent.memory.refresh.assert_called_once()


class TestCredentialSecurity:
    def test_cors_preflight_does_not_require_api_token(self, unauthenticated_client):
        """Browsers can complete CORS preflight before sending authentication."""
        response = unauthenticated_client.options(
            "/api/credentials",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-sage-admin-token,x-sage-session-id",
            },
        )
        assert response.status_code == 200
        assert (
            response.headers["access-control-allow-origin"] == "http://localhost:5173"
        )

    def test_anonymous_caller_cannot_set_credentials(self, unauthenticated_client):
        """Credential mutation requires the configured administration token."""
        response = unauthenticated_client.post(
            "/api/credentials",
            json={"access_key_id": "id", "access_key_secret": "secret"},
        )
        assert response.status_code == 401

    def test_region_reaches_agent_construction(self, client):
        """The effective region selected by the user reaches the cloud client."""
        with patch.object(api, "Agent") as agent_class:
            agent = MagicMock()
            agent.procedural.get_rule_count.return_value = 0
            agent.metrics = {}
            agent.memory.snapshot.return_value = {
                "procedural": {"count": 0},
                "metrics": {},
            }
            agent.run.describe.return_value = {}
            agent.mcp.simulate = False
            agent.mcp.region = "eu-central-1"
            agent_class.return_value = agent
            api._agents.clear()
            response = client.post(
                "/api/credentials",
                json={
                    "access_key_id": "test-id",
                    "access_key_secret": "test-secret",
                    "region": "eu-central-1",
                },
            )
            assert response.status_code == 200

            response = client.get("/api/status", params={"mode": "cloud"})
            assert response.status_code == 200
            assert agent_class.call_args.kwargs["region"] == "eu-central-1"

    def test_credential_status_never_returns_key_prefix(self, client):
        """Credential status reveals readiness but no part of the key."""
        client.post(
            "/api/credentials",
            json={"access_key_id": "LTAI-sensitive", "access_key_secret": "secret"},
        )
        response = client.get("/api/credentials/status")
        assert response.status_code == 200
        assert "access_key_id_preview" not in response.json()

    def test_credential_status_distinguishes_qwen_key_from_admin_token(
        self, client, monkeypatch
    ):
        monkeypatch.setenv("SAGE_ENABLE_LIVE", "true")
        monkeypatch.delenv("SAGE_QWEN_API_KEY", raising=False)

        response = client.get("/api/credentials/status")

        assert response.status_code == 200
        assert response.json()["qwen_key_configured"] is False

    def test_credentials_are_isolated_by_session(self, client):
        """One authenticated browser session cannot observe another's credentials."""
        session_a = {"X-Sage-Session-ID": "session-a"}
        session_b = {"X-Sage-Session-ID": "session-b"}
        response = client.post(
            "/api/credentials",
            headers=session_a,
            json={"access_key_id": "test-id", "access_key_secret": "test-secret"},
        )
        assert response.status_code == 200

        assert (
            client.get("/api/credentials/status", headers=session_a).json()[
                "has_credentials"
            ]
            is True
        )
        assert (
            client.get("/api/credentials/status", headers=session_b).json()[
                "has_credentials"
            ]
            is False
        )


class TestSessionIsolation:
    def test_sessions_construct_agents_with_distinct_persistence_roots(self, client):
        """Runs from separate sessions cannot share a persistence directory."""
        agents = []

        def build_agent(**kwargs):
            agent = MagicMock()
            agent.procedural.get_rule_count.return_value = 0
            agent.metrics = {}
            agent.memory.snapshot.return_value = {
                "procedural": {"count": 0},
                "metrics": {},
            }
            agent.run.describe.return_value = {}
            agent.mcp.simulate = True
            agent.project_dir = Path(kwargs["project_dir"])
            agents.append(agent)
            return agent

        with patch.object(api, "Agent", side_effect=build_agent):
            first = client.get(
                "/api/status", headers={"X-Sage-Session-ID": "session-one"}
            )
            second = client.get(
                "/api/status", headers={"X-Sage-Session-ID": "session-two"}
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert agents[0].project_dir != agents[1].project_dir
