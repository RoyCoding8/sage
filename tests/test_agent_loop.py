"""
Tests for the LLM-first execution engine — the OpenClaw-style agent loop and
the ground-truth DeploymentSandbox introduced by the architecture pivot.

These lock in the architecture's core claims, which no other test covered
directly before the audit:

  1. A deployment is only healthy if the app's *real* port is opened.
  2. Learned memory, injected into the prompt, changes which ports the model
     opens — and therefore the outcome. (memory → behavior → result)
  3. The loop parses model actions robustly and always terminates.
  4. Offline mode makes zero real API calls.

No real API calls are made anywhere: the "model" is a local callable in every
test, and the offline guard test actively fails if any network call is attempted.
"""

from sage.agent_loop import AgentLoop, DeploymentSandbox
from sage.tools.mcp_client import MCPClient
from sage.demo_runner import _offline_agent_step


def mcp():
    return MCPClient(simulate=True)


def memory_driven_model(prompt, **kwargs):
    """The offline 'model': picks its next action from PROGRESS_JSON + the
    injected LEARNED MEMORY block. This is the same stub the demo uses, so the
    tests exercise the real memory→ports integration, not a bespoke fake."""
    return _offline_agent_step(prompt)


# ─── DeploymentSandbox: the ground truth ──────────────────────────────────────


class TestDeploymentSandboxVerify:
    def test_not_deployed_is_unhealthy(self):
        sb = DeploymentSandbox(mcp(), "node")
        ok, reason = sb.verify()
        assert ok is False
        assert "not deployed" in reason

    def test_deployed_without_required_port_fails(self):
        sb = DeploymentSandbox(mcp(), "node")  # binds to 8080
        sb.create_security_group()
        sb.open_port(port=80)
        sb.open_port(port=443)
        sb.create_instance()
        sb.deploy()
        ok, reason = sb.verify()
        assert ok is False
        assert "8080" in reason  # names the missing company port

    def test_deployed_with_required_port_succeeds(self):
        sb = DeploymentSandbox(mcp(), "node")
        sb.create_security_group()
        sb.open_port(port=8080)
        sb.create_instance()
        sb.deploy()
        ok, reason = sb.verify()
        assert ok is True
        assert "8080" in reason

    def test_port_convention_by_app_type(self):
        assert DeploymentSandbox(mcp(), "node").required_port == 8080
        assert DeploymentSandbox(mcp(), "python").required_port == 8080
        assert DeploymentSandbox(mcp(), "java").required_port == 8080
        assert DeploymentSandbox(mcp(), "docker").required_port == 80
        assert DeploymentSandbox(mcp(), "static").required_port == 80

    def test_deploy_requires_an_instance(self):
        sb = DeploymentSandbox(mcp(), "docker")
        r = sb.deploy()
        assert r["ok"] is False

    def test_open_port_auto_provisions_security_group(self):
        sb = DeploymentSandbox(mcp(), "node")
        r = sb.open_port(port=8080)  # no SG created yet
        assert r["ok"] is True
        assert sb.sg_id is not None


# ─── Memory changes behavior — the central thesis ─────────────────────────────


class TestMemoryChangesPorts:
    def test_no_memory_opens_only_web_defaults_and_fails(self):
        loop = AgentLoop(mcp(), memory_driven_model)
        res = loop.run_loop("Deploy Node.js web app", app_type="node", memory_block="")
        assert res["opened_ports"] == [80, 443]
        assert res["required_port"] == 8080
        assert res["outcome"] == "failed"

    def test_memory_makes_the_model_open_the_company_port(self):
        loop = AgentLoop(mcp(), memory_driven_model)
        memory = (
            "Learned rules from past corrections:\n"
            "- Open port 8080 in the security group before deploying."
        )
        res = loop.run_loop("Deploy Node.js web app", app_type="node", memory_block=memory)
        assert 8080 in res["opened_ports"]
        assert res["outcome"] == "success"

    def test_docker_needs_no_memory(self):
        # docker binds to 80, so the web defaults already satisfy it — memory
        # neither helps nor is needed. This guards against the sandbox making
        # EVERYTHING require 8080 (which would make the counterfactual dishonest).
        loop = AgentLoop(mcp(), memory_driven_model)
        res = loop.run_loop("Deploy docker container", app_type="docker", memory_block="")
        assert res["outcome"] == "success"
        assert 80 in res["opened_ports"]

    def test_steps_carry_thought_and_observation(self):
        # The UI renders per-turn reasoning; lock the shape in.
        loop = AgentLoop(mcp(), memory_driven_model)
        res = loop.run_loop(
            "Deploy Node.js web app",
            app_type="node",
            memory_block="- Open port 8080 before deploying.",
        )
        assert res["steps"], "expected at least one tool step"
        assert all("thought" in s for s in res["steps"])
        assert any(isinstance(s.get("observation"), dict) for s in res["steps"])
        tool_steps = [s for s in res["steps"] if s.get("tool") != "finish"]
        assert tool_steps, "expected at least one real tool step"
        assert all("args" in s for s in tool_steps)
        assert all("duration_ms" in s for s in tool_steps)
        assert all("progress_before" in s and "progress_after" in s for s in tool_steps)


# ─── Action parsing ───────────────────────────────────────────────────────────


class TestActionParsing:
    def test_parses_flat_action(self):
        loop = AgentLoop(mcp(), None)
        a = loop._parse_action(
            '{"thought":"x","tool":"open_port","args":{"port":8080}}'
        )
        assert a["tool"] == "open_port"
        assert a["args"]["port"] == 8080

    def test_parses_nested_action(self):
        loop = AgentLoop(mcp(), None)
        a = loop._parse_action('{"thought":"x","action":{"tool":"deploy","args":{}}}')
        assert a["tool"] == "deploy"

    def test_missing_tool_returns_none(self):
        loop = AgentLoop(mcp(), None)
        assert loop._parse_action('{"thought":"no tool here"}') is None

    def test_non_json_returns_none(self):
        loop = AgentLoop(mcp(), None)
        assert loop._parse_action("completely not json") is None


# ─── Loop control: termination + error handling ───────────────────────────────


class TestLoopTermination:
    def test_finish_ends_the_loop_immediately(self):
        calls = {"n": 0}

        def model(prompt, **kwargs):
            calls["n"] += 1
            return '{"tool":"finish","args":{"summary":"done"}}'

        loop = AgentLoop(mcp(), model, max_iterations=5)
        res = loop.run_loop("task", app_type="docker", memory_block="")
        assert calls["n"] == 1
        assert res["steps"][-1]["tool"] == "finish"

    def test_respects_max_iterations_when_model_never_finishes(self):
        def model(prompt, **kwargs):
            return '{"tool":"list_instances","args":{}}'

        loop = AgentLoop(mcp(), model, max_iterations=4)
        res = loop.run_loop("task", app_type="node", memory_block="")
        assert len(res["tools_used"]) <= 4
        assert res["outcome"] == "failed"
        assert res["failure_point"] == "max_iterations"

    def test_model_exception_is_captured_not_raised(self):
        def model(prompt, **kwargs):
            raise RuntimeError("circuit open")

        loop = AgentLoop(mcp(), model, max_iterations=3)
        res = loop.run_loop("task", app_type="node", memory_block="")
        assert res["outcome"] == "failed"
        assert "circuit open" in (res["error"] or "")

    def test_unknown_tool_is_reported_not_fatal(self):
        seq = iter(
            [
                '{"tool":"teleport","args":{}}',  # bogus tool
                '{"tool":"finish","args":{"summary":"stop"}}',
            ]
        )

        def model(prompt, **kwargs):
            return next(seq)

        loop = AgentLoop(mcp(), model, max_iterations=4)
        res = loop.run_loop("task", app_type="docker", memory_block="")
        # The bad tool produced an error observation but the loop kept going.
        assert any(
            s["tool"] == "teleport" and s["result"] == "error" for s in res["steps"]
        )


# ─── Offline mode makes no real API calls ─────────────────────────────────────


class TestOfflineMakesNoRealCalls:
    def test_offline_agent_never_touches_the_network(self, tmp_path, monkeypatch):
        """Proves the 'no real API calls in tests' contract for the offline path:
        any HTTP call raises, and the full fail→learn→succeed arc still runs."""
        import urllib.request

        def boom(*args, **kwargs):
            raise AssertionError("real network call attempted in offline mode")

        monkeypatch.delenv("SAGE_QWEN_API_KEY", raising=False)
        monkeypatch.setattr(urllib.request, "urlopen", boom)

        from sage.agent import Agent
        from sage.demo_runner import _offline_reflection_model

        agent = Agent(
            project_dir=str(tmp_path),
            model_caller=_offline_reflection_model,
            simulate=True,
        )

        first = agent.run.execute("Deploy Node.js web app")
        assert first["outcome"] == "failed"  # doesn't know the company port yet

        agent.handle_correction(
            task="Deploy Node.js web app",
            action_taken="Opened only 80 and 443",
            error="app unreachable on its port",
            correction="Open port 8080 in the security group before deploying.",
        )

        # A DIFFERENT app type — the learned rule must transfer.
        second = agent.run.execute("Deploy Python Flask API")
        assert second["outcome"] == "success"
        assert 8080 in second["opened_ports"]
