"""Regressions for the app-level read-only task contract.

These lock in the new Sage behavior:

* `AgentLoop.run_loop(..., read_only=True)` accepts only the read-only
  inventory toolset and reports a successful inspection once the model has
  emitted at least one successful observation followed by ``finish``.
* Mutation tools proposed by a misbehaving model are rejected by name before
  the MCP layer is reached.
* The same allowed-tools restriction flows through ``Run.execute`` and the
  FastAPI ``/api/task`` read-only request flag.
"""

import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from fastapi.testclient import TestClient  # noqa: E402

import api  # noqa: E402  pylint: disable=wrong-import-position
from sage.agent import Agent  # noqa: E402
from sage.agent_loop import AgentLoop  # noqa: E402
from sage.run import RunContext  # noqa: E402
from sage.tools.mcp_client import MCPClient  # noqa: E402


READ_ONLY_INSPECTION_SEQUENCE = [
    {"tool": "list_instances", "args": {}},
    {"tool": "list_security_groups", "args": {}},
    {"tool": "list_vswitches", "args": {}},
    {"tool": "list_images", "args": {}},
    {"tool": "finish", "args": {"summary": "inventory only"}},
]


def make_mcp():
    return MCPClient(simulate=True)


def make_loop(transcript):
    state = {"i": 0}

    def fake_model(prompt, **kwargs):
        idx = state["i"]
        state["i"] += 1
        if idx >= len(transcript):
            return json.dumps(
                {"tool": "finish", "args": {"summary": "exhausted"}}
            )
        action = transcript[idx]
        return json.dumps({"thought": "test", **action})

    return AgentLoop(make_mcp(), fake_model), fake_model


def test_read_only_loop_succeeds_when_inventory_completes(tmp_path):
    loop, _ = make_loop(READ_ONLY_INSPECTION_SEQUENCE)
    result = loop.run_loop("inspect-only", app_type="node", read_only=True)
    assert result["outcome"] == "success"
    assert result["read_only"] is True
    assert result["successful_observations"] >= 1
    assert set(result["allowed_tools"]).issubset(
        {
            "list_instances",
            "list_security_groups",
            "list_vswitches",
            "list_images",
            "get_state",
            "finish",
        }
    )
    assert "create_instance" not in result["allowed_tools"]
    assert "deploy" not in result["allowed_tools"]


def test_read_only_loop_rejects_mutation_tool(tmp_path):
    transcript = [
        {"tool": "create_instance", "args": {"name": "evil"}},
        {"tool": "finish", "args": {"summary": "abandoned"}},
    ]
    loop, _ = make_loop(transcript)
    result = loop.run_loop("inspect", app_type="node", read_only=True)
    assert result["outcome"] == "failed"
    rejected = [
        step
        for step in result["steps"]
        if step["tool"] == "create_instance" and "not allowed" in str(step.get("observation", {}).get("error", ""))
    ]
    assert rejected, "read-only run must reject the create_instance call before MCP dispatch"


def test_deployment_loop_ignores_unrelated_tool_for_read_only_flag(tmp_path):
    transcript = [
        {"tool": "list_security_groups", "args": {}},
        {"tool": "finish", "args": {"summary": "ok"}},
    ]
    loop, _ = make_loop(transcript)
    result = loop.run_loop("inspect", app_type="node", read_only=False)
    # Deployment runs still require full health. Without an instance + open
    # port, the deployment outcome must fail and the read_only flag stays off.
    assert result["read_only"] is False
    assert result["outcome"] == "failed"


def test_run_propagates_read_only_to_agent(tmp_path):
    """Run.execute threads read_only + tools into the agent loop and succeeds
    when inventory completes — without requiring deployment health."""
    state = {"i": 0}

    def model(prompt, **kwargs):
        idx = state["i"]
        state["i"] += 1
        sequence = [
            {"tool": "list_security_groups", "args": {}},
            {"tool": "list_vswitches", "args": {}},
            {"tool": "finish", "args": {"summary": "ok"}},
        ]
        if idx >= len(sequence):
            return json.dumps({"tool": "finish", "args": {"summary": "done"}})
        return json.dumps({"thought": "ok", **sequence[idx]})

    with Agent(
        project_dir=str(tmp_path),
        model_caller=model,
        simulate=True,
    ) as agent:
        result = agent.run.execute(
            "inspect",
            context=RunContext(mode="offline", provider="offline", session_id="t"),
            tools=[
                "list_instances",
                "list_security_groups",
                "list_vswitches",
                "list_images",
                "finish",
            ],
            read_only=True,
        )
        assert result["outcome"] == "success"
        assert "create_instance" not in result["tools_used"]
        assert "deploy" not in result["tools_used"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SAGE_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("SAGE_ENABLE_LIVE", "false")
    api._agents.clear()
    api._credentials.clear()
    test_client = TestClient(
        api.app,
        headers={
            "X-Sage-Admin-Token": "test-admin-token",
            "X-Sage-Session-ID": "t",
        },
    )
    yield test_client
    test_client.close()
    api._agents.clear()
    api._credentials.clear()


def test_api_read_only_task_passes_through_allowlist(client, monkeypatch):
    captured: dict = {}

    def fake_execute(task, *, context, tools=None, read_only=False, cancel_event=None):
        captured["task"] = task
        captured["tools"] = tools
        captured["read_only"] = read_only
        return {
            "task": task,
            "outcome": "success",
            "read_only": read_only,
            "tools_used": ["list_instances"],
            "execution": context.__dict__,
        }

    def fake_get_agent(_mode, *, read_only=False):
        captured["agent_read_only"] = read_only
        agent = type("A", (), {})()
        agent.run = type("R", (), {"execute": staticmethod(fake_execute)})()
        return agent

    monkeypatch.setattr(api, "_get_agent", fake_get_agent)
    monkeypatch.setattr(api, "_agent_execution_lock", lambda _m: _NoLock())

    response = client.post(
        "/api/task",
        json={"task": "inspect", "mode": "qwen", "read_only": True},
    )
    assert response.status_code == 200, response.text
    assert captured["read_only"] is True
    assert captured["agent_read_only"] is True
    assert set(captured["tools"]) == set(api.READ_ONLY_TASK_TOOLS)


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_api_read_only_task_does_not_require_cloud_mutation(client, monkeypatch):
    def fake_execute(task, *, context, tools=None, read_only=False, cancel_event=None):
        return {"task": task, "outcome": "success", "read_only": read_only}

    def fake_get_agent(_mode, *, read_only=False):
        agent = type("A", (), {})()
        agent.run = type("R", (), {"execute": staticmethod(fake_execute)})()
        return agent

    monkeypatch.setattr(api, "_get_agent", fake_get_agent)
    monkeypatch.setattr(api, "_agent_execution_lock", lambda _m: _NoLock())

    # Cloud mode is requested but no credentials are configured. Read-only
    # tasks must still run on a simulated sandbox; the missing-credentials
    # gate is bypassed only for non-mutating inspections.
    api._credentials.clear()
    response = client.post(
        "/api/task",
        json={"task": "inspect", "mode": "cloud", "read_only": True},
    )
    assert response.status_code == 200, response.text


def test_build_agent_cloud_read_only_without_credentials_falls_back_simulated(
    monkeypatch, tmp_path
):
    """Regression: _get_agent(cloud, read_only=True) with no session credentials
    must NOT raise cloud_credentials_missing. It must build a *simulated*
    agent so read-only inventory runs against the simulated sandbox. This
    locks in the fix for the latent bug where _get_agent's internal credential
    gate fired even for read-only cloud tasks."""
    monkeypatch.setenv("SAGE_ENABLE_LIVE", "true")
    monkeypatch.setenv("SAGE_ALLOW_CLOUD_MUTATIONS", "false")
    api._credentials.clear()
    api._agents.clear()
    monkeypatch.setattr(api, "_session_project_dir", lambda: tmp_path)
    try:
        agent = api._get_agent(api.ExecutionMode.cloud, read_only=True)
        # The fallback agent must be a simulated Agent (mcp.simulate=True),
        # not a real cloud agent that would have required credentials to build.
        assert agent is not None
        assert getattr(agent, "use_qwen", False) is True
        assert agent.mcp.simulate is True, (
            "read-only cloud fallback must use a simulated sandbox, not real cloud"
        )
    finally:
        api._agents.clear()


@pytest.mark.live
def test_build_agent_cloud_read_only_with_credentials_uses_real(monkeypatch, tmp_path):
    """When credentials ARE configured, read-only cloud still gets the real
    cloud agent (credentials present -> no fallback needed)."""
    monkeypatch.setenv("SAGE_ENABLE_LIVE", "true")
    api._credentials.clear()
    api._agents.clear()
    api._credentials["x"] = {
        "access_key_id": "akid",
        "access_key_secret": "aksec",
        "region": "us-east-1",
    }
    monkeypatch.setattr(api, "_session_project_dir", lambda: tmp_path)
    monkeypatch.setattr(api, "_current_session", type(
        "S", (), {"get": staticmethod(lambda: "x")}
    )())
    try:
        agent = api._get_agent(api.ExecutionMode.cloud, read_only=True)
        assert agent is not None
        assert agent.mcp.simulate is False, (
            "cloud with credentials must build the real cloud agent"
        )
    finally:
        api._agents.clear()
        api._credentials.clear()

