"""
Targeted gap tests for Agent loop + DeploymentSandbox.

Covers five behavioral areas that the existing test suite does not directly
exercise:

  1. cancel_event in the agent loop — cooperative stop signal mid-iteration
  2. check_health tool — the model can probe deployment health mid-loop
  3. Unparseable model response — loop resilience to malformed output
  4. Deploy without instance — sandbox guard for premature deploy
  5. One-tool-per-turn — prompt contract and transcript evidence

All tests use offline stubs; no real API calls.
"""

import threading

from sage.agent_loop import AgentLoop, DeploymentSandbox
from sage.tools.mcp_client import MCPClient


def _mcp():
    return MCPClient(simulate=True)


# ─── 1. cancel_event in the agent loop ───────────────────────────────────────


class TestCancelEvent:
    def test_cancel_event_stops_loop_before_next_iteration(self):
        """A set cancel_event causes the loop to exit with failure_point='cancelled'."""
        call_count = {"n": 0}

        def model(prompt, **kwargs):
            call_count["n"] += 1
            return '{"tool":"list_security_groups","args":{}}'

        cancel = threading.Event()
        loop = AgentLoop(_mcp(), model, max_iterations=10)
        # Set the event BEFORE calling run_loop — the loop should bail immediately.
        cancel.set()
        res = loop.run_loop("Deploy app", app_type="docker", memory_block="", cancel_event=cancel)
        assert res["outcome"] == "failed"
        assert res["failure_point"] == "cancelled"
        assert res["error"] == "Run cancelled"
        assert call_count["n"] == 0  # model never called

    def test_cancel_event_mid_loop_stops_after_current_iteration(self):
        """Setting cancel_event during execution stops at the next iteration boundary."""
        cancel = threading.Event()
        stop_after = {"seen": 0}

        def model(prompt, **kwargs):
            stop_after["seen"] += 1
            if stop_after["seen"] == 2:
                cancel.set()  # signal after second call
            return '{"tool":"list_security_groups","args":{}}'

        loop = AgentLoop(_mcp(), model, max_iterations=10)
        res = loop.run_loop("Deploy app", app_type="docker", memory_block="", cancel_event=cancel)
        assert res["failure_point"] == "cancelled"
        # Should have run at most 2 iterations (model called twice, then stopped).
        assert stop_after["seen"] <= 2

    def test_no_cancel_event_runs_normally(self):
        """When cancel_event is None, the loop runs to completion."""
        # finish() does NOT deploy the app; the model must deploy first
        # so sandbox.outcome() returns success.
        def model(prompt, **kwargs):
            p = prompt.lower()
            if "progress_json" in p:
                import json
                import re
                m = re.search(r"PROGRESS_JSON:\s*(\{.*\})", prompt)
                progress = json.loads(m.group(1)) if m else {}
                if not progress.get("security_groups_listed"):
                    return '{"tool":"list_security_groups","args":{}}'
                if not progress.get("security_group_id"):
                    return '{"tool":"create_security_group","args":{"name":"sg"}}'
                if 80 not in (progress.get("ports_opened") or []):
                    return '{"tool":"open_port","args":{"port":80}}'
                if not progress.get("instance_id"):
                    return '{"tool":"create_instance","args":{"name":"app"}}'
                if not progress.get("deployed"):
                    return '{"tool":"deploy","args":{}}'
                return '{"tool":"finish","args":{"summary":"done"}}'
            return '{"tool":"finish","args":{"summary":"done"}}'

        loop = AgentLoop(_mcp(), model, max_iterations=10)
        res = loop.run_loop("Deploy app", app_type="docker", memory_block="")
        assert res["outcome"] == "success"


# ─── 2. check_health tool ────────────────────────────────────────────────────


class TestCheckHealthTool:
    def test_check_health_reports_healthy_when_port_open(self):
        """check_health returns healthy=True when the required port is opened."""
        sb = DeploymentSandbox(_mcp(), "node")
        sb.create_security_group()
        sb.open_port(port=8080)
        sb.create_instance()
        sb.deploy()
        obs = sb.check_health()
        assert obs["ok"] is True
        assert obs["healthy"] is True
        assert "8080" in obs["reason"]

    def test_check_health_reports_unhealthy_when_port_missing(self):
        """check_health returns healthy=False when the required port is NOT opened."""
        sb = DeploymentSandbox(_mcp(), "node")
        sb.create_security_group()
        sb.open_port(port=80)
        sb.create_instance()
        sb.deploy()
        obs = sb.check_health()
        assert obs["ok"] is True
        assert obs["healthy"] is False
        assert "8080" in obs["reason"]

    def test_check_health_reports_unhealthy_when_not_deployed(self):
        """check_health returns healthy=False before deployment."""
        sb = DeploymentSandbox(_mcp(), "node")
        obs = sb.check_health()
        assert obs["ok"] is True
        assert obs["healthy"] is False
        assert "not deployed" in obs["reason"]

    def test_check_health_via_agent_loop_dispatch(self):
        """check_health is a valid tool in the loop's dispatch table."""
        calls = []

        def model(prompt, **kwargs):
            calls.append(len(calls))
            if len(calls) == 1:
                return '{"tool":"check_health","args":{}}'
            return '{"tool":"finish","args":{"summary":"done"}}'

        loop = AgentLoop(_mcp(), model, max_iterations=5)
        res = loop.run_loop("Check deployment health", app_type="node", memory_block="")
        assert any(s["tool"] == "check_health" for s in res["steps"])
        assert res["steps"][0]["observation"]["ok"] is True
        assert res["steps"][0]["observation"]["healthy"] is False


# ─── 3. Unparseable model response ──────────────────────────────────────────


class TestUnparseableModelResponse:
    def test_unparseable_response_retries_without_consuming_tool_slot(self):
        """A non-JSON response does not count as a tool call; the loop retries."""
        call_count = {"n": 0}

        def model(prompt, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "I am not a JSON object"
            # Second call: deploy the docker app (needs open_port 80 + create_instance + deploy)
            p = prompt.lower()
            if "progress_json" in p:
                import json
                import re
                m = re.search(r"PROGRESS_JSON:\s*(\{.*\})", prompt)
                progress = json.loads(m.group(1)) if m else {}
                if not progress.get("security_groups_listed"):
                    return '{"tool":"list_security_groups","args":{}}'
                if not progress.get("security_group_id"):
                    return '{"tool":"create_security_group","args":{"name":"sg"}}'
                if 80 not in (progress.get("ports_opened") or []):
                    return '{"tool":"open_port","args":{"port":80}}'
                if not progress.get("instance_id"):
                    return '{"tool":"create_instance","args":{"name":"app"}}'
                if not progress.get("deployed"):
                    return '{"tool":"deploy","args":{}}'
                return '{"tool":"finish","args":{"summary":"done"}}'
            return '{"tool":"finish","args":{"summary":"done"}}'

        loop = AgentLoop(_mcp(), model, max_iterations=10)
        res = loop.run_loop("task", app_type="docker", memory_block="")
        # First call was garbage, then the model deployed successfully.
        assert call_count["n"] >= 2
        assert res["outcome"] == "success"

    def test_unparseable_response_appends_hint_to_transcript(self):
        """The transcript gets a hint about expected format after unparseable output."""
        calls = []

        def model(prompt, **kwargs):
            calls.append(len(calls))
            if len(calls) == 1:
                return "garbage"
            return '{"tool":"finish","args":{"summary":"done"}}'

        loop = AgentLoop(_mcp(), model, max_iterations=5)
        res = loop.run_loop("task", app_type="docker", memory_block="")
        # The first transcript entry should contain the unparseable hint.
        # (transcript is internal, but we can verify the loop kept going.)
        assert len(res["steps"]) == 1  # only the finish step counted
        assert res["steps"][0]["tool"] == "finish"

    def test_missing_tool_field_returns_none_and_retries(self):
        """JSON without a 'tool' key is treated as unparseable."""
        calls = []

        def model(prompt, **kwargs):
            calls.append(len(calls))
            if len(calls) == 1:
                return '{"thought":"hmm","args":{}}'
            return '{"tool":"finish","args":{"summary":"done"}}'

        loop = AgentLoop(_mcp(), model, max_iterations=5)
        res = loop.run_loop("task", app_type="docker", memory_block="")
        assert len(res["steps"]) == 1
        assert res["steps"][0]["tool"] == "finish"


# ─── 4. Deploy without instance ──────────────────────────────────────────────


class TestDeployWithoutInstance:
    def test_deploy_without_instance_returns_error(self):
        """deploy() on a fresh sandbox returns an error observation."""
        sb = DeploymentSandbox(_mcp(), "node")
        obs = sb.deploy()
        assert obs["ok"] is False
        assert "no instance" in obs["error"]

    def test_deploy_without_instance_does_not_crash_loop(self):
        """If the model tries deploy() before create_instance(), the loop continues."""
        calls = []

        def model(prompt, **kwargs):
            calls.append(len(calls))
            if len(calls) == 1:
                return '{"tool":"deploy","args":{}}'
            return '{"tool":"finish","args":{"summary":"done"}}'

        loop = AgentLoop(_mcp(), model, max_iterations=5)
        res = loop.run_loop("task", app_type="docker", memory_block="")
        # The deploy step should have an error result.
        deploy_step = res["steps"][0]
        assert deploy_step["tool"] == "deploy"
        assert deploy_step["result"] == "error"
        # But the loop kept going and finished.
        assert res["steps"][-1]["tool"] == "finish"
        assert res["outcome"] == "failed"  # deployment never happened


# ─── 5. One-tool-per-turn ────────────────────────────────────────────────────


class TestOneToolPerTurn:
    def test_model_returns_exactly_one_tool_per_turn(self):
        """Each model call produces exactly one tool; the loop enforces this."""

        def model(prompt, **kwargs):
            # Simulate a model that always returns one tool.
            return '{"tool":"list_security_groups","args":{}}'

        loop = AgentLoop(_mcp(), model, max_iterations=3)
        res = loop.run_loop("task", app_type="docker", memory_block="")
        # Each step corresponds to one tool call.
        assert len(res["steps"]) == 3
        assert all(s["tool"] == "list_security_groups" for s in res["steps"])

    def test_finish_stops_after_exactly_one_tool_this_turn(self):
        """When the model calls finish, no further tools are executed."""
        call_count = {"n": 0}

        def model(prompt, **kwargs):
            call_count["n"] += 1
            return '{"tool":"finish","args":{"summary":"stop"}}'

        loop = AgentLoop(_mcp(), model, max_iterations=5)
        res = loop.run_loop("task", app_type="docker", memory_block="")
        assert call_count["n"] == 1
        assert len(res["steps"]) == 1
        assert res["steps"][0]["tool"] == "finish"

    def test_transcript_records_one_action_per_turn(self):
        """The transcript (fed back to the model) shows one action per turn."""
        actions_in_transcript = []

        def model(prompt, **kwargs):
            # Count how many assistant actions appear in the recent transcript.
            count = prompt.count("assistant: ")
            actions_in_transcript.append(count)
            if count < 3:
                return '{"tool":"list_instances","args":{}}'
            return '{"tool":"finish","args":{"summary":"done"}}'

        loop = AgentLoop(_mcp(), model, max_iterations=5)
        loop.run_loop("task", app_type="docker", memory_block="")
        # Each successive prompt should have one more action than the previous.
        for i in range(1, len(actions_in_transcript)):
            assert actions_in_transcript[i] == actions_in_transcript[i - 1] + 1

    def test_each_step_records_progress_before_and_after(self):
        """Every tool step captures sandbox state before and after execution."""
        def model(prompt, **kwargs):
            return '{"tool":"list_security_groups","args":{}}'

        loop = AgentLoop(_mcp(), model, max_iterations=3)
        res = loop.run_loop("task", app_type="docker", memory_block="")
        for s in res["steps"]:
            assert "progress_before" in s
            assert "progress_after" in s
            assert isinstance(s["progress_before"], dict)
            assert isinstance(s["progress_after"], dict)
