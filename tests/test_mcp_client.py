"""
Tests for MCPClient — retry logic, reconnect, simulate mode.
"""

import json
import pytest
from unittest.mock import Mock, patch
from sage.tools.mcp_client import MCPClient, MCPClientError


class TestMCPSimulate:
    """Tests for simulate mode (no real MCP server)."""

    def test_simulate_init(self):
        """MCPClient starts in simulate mode when no credentials."""
        client = MCPClient(simulate=True)
        assert client.simulate is True

    def test_simulate_list_instances(self):
        """Simulated list_ecs_instances returns mock data."""
        client = MCPClient(simulate=True)
        result = client.list_ecs_instances()
        assert "Instances" in result or "simulated" in result

    def test_simulate_list_security_groups(self):
        """Simulated list_security_groups returns mock data."""
        client = MCPClient(simulate=True)
        result = client.list_security_groups()
        assert "SecurityGroups" in result or "simulated" in result

    def test_simulate_create_sg(self):
        """Simulated create_security_group returns mock ID."""
        client = MCPClient(simulate=True)
        result = client.create_security_group("test-sg")
        assert "SecurityGroupId" in result or "simulated" in result

    def test_simulate_is_healthy(self):
        """Simulate mode is always healthy."""
        client = MCPClient(simulate=True)
        assert client.is_healthy() is True

    def test_simulate_tool_names(self):
        """get_available_tools returns tool definitions in simulate mode."""
        client = MCPClient(simulate=True)
        tools = client.get_available_tools()
        assert len(tools) > 0
        assert all("name" in t for t in tools)

    def test_simulate_close_noop(self):
        """close() in simulate mode doesn't crash."""
        client = MCPClient(simulate=True)
        client.close()  # should not raise


class TestMCPRetry:
    """Tests for retry logic in _call_mcp_tool."""

    def test_retry_on_transient_error(self, tmp_path):
        """Retry logic retries on transient MCPClientError."""
        client = MCPClient.__new__(MCPClient)
        client.simulate = False
        client._process = Mock()
        client._process.poll.return_value = None
        client._lock = __import__("threading").Lock()
        client._request_id = 0
        client._available_tools = []
        client._last_healthy = 0.0
        client.DEFAULT_TIMEOUT = 1
        client.MAX_RETRIES = 2
        client.RETRY_DELAY = 0.01
        client.MAX_JITTER = 0.01

        call_count = [0]

        def mock_send(method, params=None, timeout=None):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise MCPClientError("transient error", retryable=True)
            return {"content": [{"type": "text", "text": json.dumps({"result": "ok"})}]}

        client._send_request = mock_send

        result = client._call_mcp_tool("DescribeInstances", {"RegionId": "us-east-1"})
        assert call_count[0] == 3  # initial + 2 retries
        assert result is not None

    def test_unknown_tool_is_rejected_before_provider_call(self):
        """Tools outside the fixed policy never reach the MCP provider."""
        client = MCPClient.__new__(MCPClient)
        client.simulate = False
        client._process = Mock()
        client._process.poll.return_value = None
        client._lock = __import__("threading").Lock()
        client._request_id = 0
        client._available_tools = []
        client._last_healthy = 0.0
        client.DEFAULT_TIMEOUT = 1
        client.MAX_RETRIES = 2
        client.RETRY_DELAY = 0.01
        client.MAX_JITTER = 0.01

        call_count = [0]

        def mock_send(method, params=None, timeout=None):
            call_count[0] += 1
            raise MCPClientError("bad tool name", retryable=False)

        client._send_request = mock_send

        with pytest.raises(MCPClientError, match="not allowed"):
            client._call_mcp_tool("InvalidTool", {})
        assert call_count[0] == 0

    def test_exhausted_retries_raises(self):
        """After MAX_RETRIES exhausted, raises MCPClientError."""
        client = MCPClient.__new__(MCPClient)
        client.simulate = False
        client._process = Mock()
        client._process.poll.return_value = None
        client._lock = __import__("threading").Lock()
        client._request_id = 0
        client._available_tools = []
        client._last_healthy = 0.0
        client.DEFAULT_TIMEOUT = 1
        client.MAX_RETRIES = 1
        client.RETRY_DELAY = 0.01
        client.MAX_JITTER = 0.01

        def mock_send(method, params=None, timeout=None):
            raise MCPClientError("persistent failure", retryable=True)

        client._send_request = mock_send
        client._try_reconnect = Mock(return_value=False)

        with pytest.raises(MCPClientError, match="failed after"):
            client._call_mcp_tool("DescribeInstances", {})

    def test_simulate_bypasses_retry(self):
        """Simulate mode bypasses all retry logic."""
        client = MCPClient(simulate=True)
        result = client._call_mcp_tool("DescribeInstances", {"RegionId": "us-east-1"})
        assert isinstance(result, dict)


class TestMCPToolResponseParsing:
    """Tests for _parse_tool_response."""

    def test_parse_text_json(self):
        """Parse JSON content from MCP text response."""
        client = MCPClient.__new__(MCPClient)
        client.simulate = True
        result = client._parse_tool_response(
            {"content": [{"type": "text", "text": '{"key": "value"}'}]}
        )
        assert result == {"key": "value"}

    def test_parse_text_plain(self):
        """Parse plain text content from MCP response."""
        client = MCPClient.__new__(MCPClient)
        client.simulate = True
        result = client._parse_tool_response(
            {"content": [{"type": "text", "text": "hello world"}]}
        )
        assert result == {"result": "hello world"}

    def test_parse_empty_content(self):
        """Parse empty content returns raw result."""
        client = MCPClient.__new__(MCPClient)
        client.simulate = True
        result = client._parse_tool_response({"content": []})
        assert result == {"content": []}

    def test_parse_no_content_key(self):
        """Parse result with no content key returns raw."""
        client = MCPClient.__new__(MCPClient)
        client.simulate = True
        result = client._parse_tool_response({"status": "ok"})
        assert result == {"status": "ok"}


class TestMCPCredentials:
    """Tests for credential loading from environment."""

    def test_env_var_precedence(self):
        """Environment variables take precedence over file-based secrets."""
        with patch.dict("os.environ", {"SAGE_ALIBABA_ACCESS_KEY_ID": "env-key-id"}):
            client = MCPClient(region="us-east-1")
            assert client.access_key_id == "env-key-id"

    def test_fallback_to_constructor_args(self):
        """Constructor args used when env vars not set."""
        with patch.dict("os.environ", {}, clear=False):
            # Remove env vars if present
            import os

            os.environ.pop("SAGE_ALIBABA_ACCESS_KEY_ID", None)
            os.environ.pop("SAGE_ALIBABA_ACCESS_KEY_SECRET", None)
            client = MCPClient(
                access_key_id="ctor-id",
                access_key_secret="ctor-secret",
                region="us-east-1",
            )
            assert client.access_key_id == "ctor-id"

    def test_auto_simulate_without_creds(self):
        """Automatically enters simulate mode when no credentials."""
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("SAGE_ALIBABA_ACCESS_KEY_ID", None)
            os.environ.pop("SAGE_ALIBABA_ACCESS_KEY_SECRET", None)
            client = MCPClient()
            assert client.simulate is True

    def test_strict_real_mode_rejects_missing_credentials(self):
        """Real cloud mode never silently changes into a simulation."""
        with patch.dict(
            "os.environ",
            {
                "SAGE_ALIBABA_ACCESS_KEY_ID": "",
                "SAGE_ALIBABA_ACCESS_KEY_SECRET": "",
            },
        ):
            with pytest.raises(
                MCPClientError, match="requires Alibaba Cloud credentials"
            ):
                MCPClient(simulate=False, strict=True)

    def test_credential_status_reports_ready_without_connecting(self):
        """credential_status reports live readiness without spawning MCP."""
        with patch.dict(
            "os.environ",
            {
                "SAGE_ALIBABA_ACCESS_KEY_ID": "id",
                "SAGE_ALIBABA_ACCESS_KEY_SECRET": "secret",
                "SAGE_ALIBABA_REGION": "us-west-1",
            },
        ):
            status = MCPClient.credential_status()
            assert status == {
                "ready": True,
                "access_key_id_set": True,
                "access_key_secret_set": True,
                "region": "us-west-1",
            }

    def test_credential_status_reports_missing_secret(self):
        """Partial credentials are visible in status diagnostics."""
        with patch.dict(
            "os.environ",
            {
                "SAGE_ALIBABA_ACCESS_KEY_ID": "id",
            },
            clear=False,
        ):
            import os

            os.environ.pop("SAGE_ALIBABA_ACCESS_KEY_SECRET", None)
            os.environ.pop("SAGE_ALIBABA_REGION", None)
            status = MCPClient.credential_status()
            assert status["ready"] is False
            assert status["access_key_id_set"] is True
            assert status["access_key_secret_set"] is False
