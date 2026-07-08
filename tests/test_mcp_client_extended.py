"""
Extended tests for MCPClient simulation layer.

Covers:
- Simulated responses for all 10 ALLOWED_TOOL_NAMES
- Trace ID propagation into simulated results
- Context-manager protocol (CloseableMixin)
- JSON-RPC error code classification (retryable vs non-retryable)
- Unknown simulated tool fallback
- Metric routing in get_instance_metrics
- _cleanup_process edge cases
- is_healthy / health_check in non-simulate process-dead paths
- close() with active process
- Credential precedence (constructor > env)
"""

import json
import threading
import time
from unittest.mock import Mock, patch

import pytest

from sage.tools.mcp_client import MCPClient, MCPClientError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(**overrides):
    """Build a bare MCPClient with no __init__ side-effects."""
    client = MCPClient.__new__(MCPClient)
    defaults = dict(
        simulate=False,
        strict=False,
        access_key_id="",
        access_key_secret="",
        region="us-east-1",
        _process=None,
        _request_id=0,
        _lock=threading.Lock(),
        _server_capabilities=None,
        _available_tools=[],
        _last_healthy=0.0,
        DEFAULT_TIMEOUT=1,
        HEALTH_CHECK_TIMEOUT=1,
        MAX_RETRIES=2,
        RETRY_DELAY=0.01,
        MAX_JITTER=0.01,
        _run_trace_id=None,
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(client, k, v)
    return client


class TestServerToolAliases:
    def test_namespaced_server_tool_is_selected(self):
        client = _make_client(
            _available_tools=[{"name": "ECS_DescribeInstances"}]
        )

        assert (
            client._resolve_server_tool_name("DescribeInstances")
            == "ECS_DescribeInstances"
        )

    def test_exact_server_tool_name_is_preferred(self):
        client = _make_client(
            _available_tools=[
                {"name": "DescribeInstances"},
                {"name": "ECS_DescribeInstances"},
            ]
        )

        assert client._resolve_server_tool_name("DescribeInstances") == "DescribeInstances"

    def test_logical_name_is_retained_when_server_has_no_alias(self):
        client = _make_client(_available_tools=[{"name": "UnrelatedTool"}])

        assert client._resolve_server_tool_name("OOS_CodeDeploy") == "OOS_CodeDeploy"

    def test_call_uses_discovered_namespaced_tool(self):
        client = _make_client(
            _available_tools=[{"name": "ECS_DescribeSecurityGroups"}],
            MAX_RETRIES=0,
        )
        client._send_request = Mock(
            return_value={"content": [{"type": "text", "text": '{"TotalCount": 0}'}]}
        )

        result = client._call_mcp_tool("DescribeSecurityGroups", {"RegionId": "cn-hangzhou"})

        assert result["TotalCount"] == 0
        params = client._send_request.call_args.args[1]
        assert params["name"] == "ECS_DescribeSecurityGroups"


# ===================================================================
# Simulated response coverage — every ALLOWED_TOOL_NAME
# ===================================================================

class TestSimulateAllAllowedTools:
    """Each tool in ALLOWED_TOOL_NAMES must return a plausible simulated dict."""

    def test_describe_instances_shape(self):
        client = MCPClient(simulate=True)
        result = client.list_ecs_instances()
        assert result["simulated"] is True
        assert result["TotalCount"] == 1
        inst = result["Instances"]["Instance"][0]
        assert inst["InstanceId"] == "i-simulated12345"
        assert inst["Status"] == "Running"

    def test_describe_security_groups_shape(self):
        client = MCPClient(simulate=True)
        result = client.list_security_groups()
        assert result["simulated"] is True
        sg = result["SecurityGroups"]["SecurityGroup"][0]
        assert sg["SecurityGroupId"] == "sg-demo123"

    def test_create_security_group_shape(self):
        client = MCPClient(simulate=True)
        result = client.create_security_group("my-sg", "test desc")
        assert result["SecurityGroupId"] == "sg-new12345"
        assert result["simulated"] is True

    def test_authorize_security_group_shape(self):
        client = MCPClient(simulate=True)
        result = client.authorize_security_group_ingress(
            "sg-demo123", port_range="443/443", protocol="tcp",
            source_cidr="10.0.0.0/8",
        )
        assert result["RequestId"] == "simulated-request-id"
        assert result["simulated"] is True

    def test_run_instances_shape(self):
        client = MCPClient(simulate=True)
        result = client.create_ecs_instance("test-vm")
        assert result["InstanceId"] == "i-newinstance123"
        assert result["simulated"] is True

    def test_oos_code_deploy_shape(self):
        client = MCPClient(simulate=True)
        result = client.deploy_application("i-simulated12345", "webapp")
        assert result["Status"] == "Deployed"
        assert result["ApplicationId"] == "app-12345"
        assert result["simulated"] is True

    def test_describe_regions_shape(self):
        client = MCPClient(simulate=True)
        result = client.list_regions()
        assert result["simulated"] is True
        regions = result["Regions"]["Region"]
        assert len(regions) == 3
        ids = [r["RegionId"] for r in regions]
        assert "us-east-1" in ids
        assert "cn-hangzhou" in ids

    def test_get_cpu_metrics_shape(self):
        client = MCPClient(simulate=True)
        result = client.get_instance_metrics("i-simulated12345", "cpu")
        assert result["simulated"] is True
        dp = result["DataPoints"]["DataPoint"][0]
        assert dp["Average"] == 23.5

    def test_get_memory_metrics_shape(self):
        """get_instance_metrics(metric="memory") returns memory DataPoints in
        simulation mode. Previously broken because ``GetMemUsedData`` lowercased
        (``getmemuseddata``) contains no ``"memory"`` substring, so the matcher
        fell through to the error branch. Fixed via a tool-name→metric reverse
        map in ``_get_simulated_response`` (src/sage/tools/mcp_client.py).
        """
        client = MCPClient(simulate=True)
        result = client.get_instance_metrics("i-simulated12345", "memory")
        assert result["simulated"] is True
        assert "DataPoints" in result
        dp = result["DataPoints"]["DataPoint"][0]
        assert dp["Average"] == 512.0

    def test_get_disk_metrics_shape(self):
        client = MCPClient(simulate=True)
        result = client.get_instance_metrics("i-simulated12345", "disk")
        assert result["simulated"] is True
        dp = result["DataPoints"]["DataPoint"][0]
        assert dp["Average"] == 12.3

    def test_unknown_metric_defaults_to_cpu(self):
        """get_instance_metrics with unrecognized metric falls back to CPU."""
        client = MCPClient(simulate=True)
        result = client.get_instance_metrics("i-simulated12345", "gpu")
        # Should get CPU data since "gpu" doesn't match any metric key
        assert result["simulated"] is True
        assert "DataPoints" in result

    def test_unknown_tool_returns_error(self):
        """_get_simulated_response for a tool not in SIMULATED or METRIC_TOOLS."""
        client = MCPClient(simulate=True)
        result = client._get_simulated_response("BogusTool", {})
        assert result["error"] == "Unknown simulated tool: BogusTool"
        assert result["simulated"] is True


# ===================================================================
# Trace ID propagation
# ===================================================================

class TestTraceID:

    def test_set_run_trace_id_stores_value(self):
        client = MCPClient(simulate=True)
        client.set_run_trace_id("abc-123")
        assert client._run_trace_id == "abc-123"

    def test_simulated_result_includes_trace_id(self):
        client = MCPClient(simulate=True)
        client.set_run_trace_id("trace-xyz")
        result = client._call_mcp_tool("DescribeInstances", {"RegionId": "us-east-1"})
        assert result["_trace_id"] == "trace-xyz"

    def test_auto_generated_trace_id_when_none_set(self):
        client = MCPClient(simulate=True)
        result = client._call_mcp_tool("DescribeInstances", {"RegionId": "us-east-1"})
        tid = result["_trace_id"]
        assert isinstance(tid, str)
        assert len(tid) == 12

    def test_explicit_trace_id_param_overrides(self):
        client = MCPClient(simulate=True)
        result = client._call_mcp_tool(
            "DescribeInstances", {"RegionId": "us-east-1"}, trace_id="manual-id"
        )
        assert result["_trace_id"] == "manual-id"


# ===================================================================
# Context-manager protocol (CloseableMixin)
# ===================================================================

class TestContextManager:

    def test_enter_returns_self(self):
        client = MCPClient(simulate=True)
        with client as ctx:
            assert ctx is client

    def test_exit_calls_close(self):
        client = MCPClient(simulate=True)
        with patch.object(client, "close") as mock_close:
            with client:
                pass
            mock_close.assert_called_once()


# ===================================================================
# JSON-RPC error code classification
# ===================================================================

class TestJSONRPCErrorClassification:

    def _make_client_with_send(self, error_code):
        """Return (client, call_tracker) where _send_request raises an MCPClientError
        wrapping a JSON-RPC error with the given code."""
        client = _make_client(simulate=False)
        call_count = [0]

        def mock_send(method, params=None, timeout=None):
            call_count[0] += 1
            # Simulate a JSON-RPC error response being translated to MCPClientError
            raise MCPClientError(
                f"MCP error {error_code}: simulated",
                retryable=(-32099 <= error_code <= -32000),
            )

        client._send_request = mock_send
        return client, call_count

    def test_server_error_codes_are_retryable(self):
        """Error codes -32000 to -32099 must be classified retryable."""
        for code in [-32000, -32050, -32099]:
            client, _ = self._make_client_with_send(code)
            err = MCPClientError("", retryable=(-32099 <= code <= -32000))
            assert err.retryable is True, f"code {code} should be retryable"

    def test_non_server_error_codes_not_retryable(self):
        """Error codes outside the JSON-RPC server-error band (-32099..-32000)
        are classified non-retryable. The band is inclusive; codes just below
        -32099 and the well-known JSON-RPC reserved codes are non-retryable.
        """
        for code in [-32100, -32600, -32700, -1, 0, 1, 100]:
            err = MCPClientError("", retryable=(-32099 <= code <= -32000))
            assert err.retryable is False, f"code {code} should not be retryable"

    def test_boundary_codes_retryable(self):
        """Error codes at exact range boundaries are retryable."""
        for code in [-32099, -32050, -32000]:
            err = MCPClientError("", retryable=(-32099 <= code <= -32000))
            assert err.retryable is True, f"code {code} should be retryable"

    def test_non_retryable_error_raises_immediately(self):
        """Non-retryable errors bypass retry loop entirely."""
        client, call_count = self._make_client_with_send(-32600)
        with pytest.raises(MCPClientError, match="not allowed|MCP error"):
            client._call_mcp_tool("DescribeInstances", {})


# ===================================================================
# _cleanup_process edge cases
# ===================================================================

class TestCleanupProcess:

    def test_noop_when_no_process(self):
        client = _make_client(_process=None)
        client._cleanup_process()  # should not raise

    def test_terminate_then_wait(self):
        mock_proc = Mock()
        mock_proc.terminate = Mock()
        mock_proc.wait = Mock()
        mock_proc.kill = Mock()

        client = _make_client(_process=mock_proc)
        client._cleanup_process()
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=5)
        assert client._process is None

    def test_force_kill_on_terminate_timeout(self):
        """If terminate() or wait() raises, kill() is attempted."""
        mock_proc = Mock()
        mock_proc.terminate.side_effect = OSError("already dead")
        mock_proc.kill = Mock()

        client = _make_client(_process=mock_proc)
        client._cleanup_process()
        mock_proc.kill.assert_called_once()
        assert client._process is None


# ===================================================================
# is_healthy in non-simulate mode
# ===================================================================

class TestIsHealthyNonSimulate:

    def test_healthy_when_process_alive_and_recent(self):
        client = _make_client(simulate=False)
        mock_proc = Mock()
        mock_proc.poll.return_value = None
        client._process = mock_proc
        client._last_healthy = time.time()
        assert client.is_healthy() is True

    def test_unhealthy_when_process_dead(self):
        client = _make_client(simulate=False)
        mock_proc = Mock()
        mock_proc.poll.return_value = 1  # exited
        client._process = mock_proc
        assert client.is_healthy() is False

    def test_unhealthy_when_no_process(self):
        client = _make_client(simulate=False, _process=None)
        assert client.is_healthy() is False

    def test_unhealthy_when_stale(self):
        """Healthy if last_healthy > 50s ago."""
        client = _make_client(simulate=False)
        mock_proc = Mock()
        mock_proc.poll.return_value = None
        client._process = mock_proc
        client._last_healthy = time.time() - 100  # 100 seconds ago
        assert client.is_healthy() is False


# ===================================================================
# health_check in non-simulate mode
# ===================================================================

class TestHealthCheckNonSimulate:

    def test_process_dead_returns_structured_error(self):
        client = _make_client(simulate=False, _process=None)
        result = client.health_check()
        assert result["healthy"] is False
        assert result["error_type"] == "process_dead"
        assert "retry_after_seconds" in result
        assert "suggestions" in result

    def test_ping_success(self):
        client = _make_client(simulate=False)
        mock_proc = Mock()
        mock_proc.poll.return_value = None
        client._process = mock_proc
        client._send_request = Mock(return_value={})
        result = client.health_check()
        assert result["healthy"] is True
        assert result["mode"] == "connected"
        assert isinstance(result["latency_ms"], float)

    def test_ping_timeout_returns_error(self):
        client = _make_client(simulate=False)
        mock_proc = Mock()
        mock_proc.poll.return_value = None
        client._process = mock_proc
        client._send_request = Mock(side_effect=MCPClientError("timed out", retryable=True))
        result = client.health_check()
        assert result["healthy"] is False
        assert result["error_type"] == "timeout"
        assert result["retry_after_seconds"] == 10


# ===================================================================
# close() with active process
# ===================================================================

class TestCloseWithProcess:

    def test_close_sends_notification_and_terminates(self):
        mock_proc = Mock()
        mock_proc.stdin = Mock()
        mock_proc.stdin.close = Mock()
        mock_proc.wait = Mock()
        mock_proc.terminate = Mock()

        client = _make_client(_process=mock_proc)
        client._send_notification = Mock()

        client.close()

        client._send_notification.assert_called_once_with("notifications/cancelled")
        mock_proc.stdin.close.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=5)
        assert client._process is None

    def test_close_handles_broken_pipe(self):
        """close() swallows errors from broken pipe / dead process."""
        mock_proc = Mock()
        mock_proc.stdin = Mock()
        mock_proc.stdin.close.side_effect = BrokenPipeError("pipe broken")
        mock_proc.terminate = Mock()
        mock_proc.wait = Mock(side_effect=OSError("already dead"))

        client = _make_client(_process=mock_proc)
        client._send_notification = Mock()

        client.close()  # should not raise
        assert client._process is None


# ===================================================================
# Credential precedence
# ===================================================================

class TestCredentialPrecedence:

    def test_constructor_overrides_env(self):
        """Constructor args take priority over environment variables."""
        with patch.dict("os.environ", {
            "SAGE_ALIBABA_ACCESS_KEY_ID": "env-key",
            "SAGE_ALIBABA_ACCESS_KEY_SECRET": "env-secret",
        }):
            client = MCPClient(
                access_key_id="ctor-key",
                access_key_secret="ctor-secret",
                region="ap-southeast-1",
            )
            assert client.access_key_id == "ctor-key"
            assert client.access_key_secret == "ctor-secret"
            assert client.region == "ap-southeast-1"

    def test_empty_env_vars_fall_through_to_empty_string(self):
        """Empty env vars result in empty string, not None."""
        with patch.dict("os.environ", {
            "SAGE_ALIBABA_ACCESS_KEY_ID": "",
            "SAGE_ALIBABA_ACCESS_KEY_SECRET": "",
        }):
            client = MCPClient()
            assert client.access_key_id == ""
            assert client.access_key_secret == ""


# ===================================================================
# _send_request edge cases
# ===================================================================

class TestSendRequestEdgeCases:

    def test_raises_when_no_process(self):
        client = _make_client(_process=None)
        with pytest.raises(MCPClientError, match="not connected"):
            client._send_request("initialize")

    def test_raises_when_process_terminated(self):
        mock_proc = Mock()
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1
        client = _make_client(_process=mock_proc)
        with pytest.raises(MCPClientError, match="terminated"):
            client._send_request("initialize")

    def test_raises_on_broken_pipe_write(self):
        mock_proc = Mock()
        mock_proc.poll.return_value = None
        mock_proc.stdin = Mock()
        mock_proc.stdin.write.side_effect = BrokenPipeError("broken")
        mock_proc.stdout = Mock()

        client = _make_client(_process=mock_proc)
        with pytest.raises(MCPClientError, match="Failed to write"):
            client._send_request("initialize")

    def test_raises_on_empty_response(self):
        """Server closes connection — readline returns empty string."""
        mock_proc = Mock()
        mock_proc.poll.return_value = None
        mock_proc.stdin = Mock()
        mock_proc.stdout = Mock()
        mock_proc.stdout.readline.return_value = ""

        client = _make_client(_process=mock_proc)
        with pytest.raises(MCPClientError, match="closed connection"):
            client._send_request("initialize")

    def test_raises_on_json_rpc_error_response(self):
        """Server returns a JSON-RPC error object."""
        error_response = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "Invalid Request"},
        })
        mock_proc = Mock()
        mock_proc.poll.return_value = None
        mock_proc.stdin = Mock()
        mock_proc.stdout = Mock()
        mock_proc.stdout.readline.return_value = error_response + "\n"

        client = _make_client(_process=mock_proc)
        with pytest.raises(MCPClientError, match="-32600"):
            client._send_request("initialize")


# ===================================================================
# _call_mcp_tool: allowed tool names enforcement
# ===================================================================

class TestAllowedToolNames:

    def test_all_allowed_tools_pass_gate(self):
        """Every name in ALLOWED_TOOL_NAMES should pass the tool-name gate in simulate."""
        client = MCPClient(simulate=True)
        for tool_name in MCPClient.ALLOWED_TOOL_NAMES:
            result = client._call_mcp_tool(tool_name, {})
            assert isinstance(result, dict), f"{tool_name} should return a dict"

    def test_disallowed_tool_rejected(self):
        client = MCPClient(simulate=True)
        with pytest.raises(MCPClientError, match="not allowed"):
            client._call_mcp_tool("EvilTool", {})

    def test_disallowed_tool_not_in_simulated(self):
        """Disallowed tools never reach _get_simulated_response."""
        client = MCPClient(simulate=True)
        with patch.object(client, "_get_simulated_response") as mock_sim:
            with pytest.raises(MCPClientError):
                client._call_mcp_tool("UnauthorizedDelete", {})
            mock_sim.assert_not_called()
