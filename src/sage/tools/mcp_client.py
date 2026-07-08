"""
MCP Client — Interface to Alibaba Cloud Ops MCP Server.

Wraps the alibaba-cloud-ops-mcp-server for use in the agent.
Uses the official MCP Python SDK for protocol communication via stdio.

Supports two modes:
- Simulated: returns mock data (for build phase / no credentials)
- Connected: spawns MCP server subprocess, speaks JSON-RPC 2.0 over stdio
"""

import json
import logging
import os
import random
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from sage.security import redact_sensitive

logger = logging.getLogger(__name__)

# Environment variable names (legacy fallback for local dev; UI is preferred)
ENV_ALIBABA_ACCESS_KEY_ID = "SAGE_ALIBABA_ACCESS_KEY_ID"
ENV_ALIBABA_ACCESS_KEY_SECRET = "SAGE_ALIBABA_ACCESS_KEY_SECRET"
ENV_ALIBABA_REGION = "SAGE_ALIBABA_REGION"

from sage.closeable import CloseableMixin  # noqa: E402


class MCPClientError(Exception):
    """Base exception for MCP client failures."""

    def __init__(self, message: str, tool: str = "", retryable: bool = False):
        self.tool = tool
        self.retryable = retryable
        super().__init__(message)


class MCPClient(CloseableMixin):
    """
    Client for Alibaba Cloud Ops MCP Server.

    The MCP server provides tools for:
    - ECS management (create, start, stop, delete instances)
    - Security group management
    - Application deployment
    - Cloud monitoring

    Uses the MCP protocol (JSON-RPC 2.0 over stdio) to communicate
    with the alibaba-cloud-ops-mcp-server subprocess.
    """

    DEFAULT_TIMEOUT = 120  # seconds — MCP server takes time to register all tools
    HEALTH_CHECK_TIMEOUT = 5  # seconds for health check ping

    # Retry configuration for transient failures
    MAX_RETRIES = 2  # total attempts = MAX_RETRIES + 1 (initial + 2 retries)
    RETRY_DELAY = 0.5  # seconds between retries (base)
    MAX_JITTER = 0.3  # max random jitter factor
    ALLOWED_TOOL_NAMES = frozenset(
        {
            "DescribeInstances",
            "DescribeSecurityGroups",
            "CreateSecurityGroup",
            "AuthorizeSecurityGroup",
            "RunInstances",
            "OOS_CodeDeploy",
            "GetCpuUsageData",
            "GetMemUsedData",
            "GetDiskUsageData",
            "DescribeRegions",
            "DescribeVSwitches",
            "DescribeImages",
            "DescribeAvailableResource",
            "CommonAPICaller",
            "RunCommand",
        }
    )
    NON_RETRYABLE_MUTATION_TOOLS = frozenset(
        {
            "CreateSecurityGroup",
            "AuthorizeSecurityGroup",
            "RunInstances",
            "OOS_CodeDeploy",
            "CommonAPICaller",
            "RunCommand",
        }
    )

    TOOL_NAME_ALIASES = {
        "DescribeInstances": ("DescribeInstances", "ECS_DescribeInstances"),
        "DescribeSecurityGroups": (
            "DescribeSecurityGroups",
            "ECS_DescribeSecurityGroups",
        ),
        "RunInstances": ("RunInstances", "OOS_RunInstances"),
        "GetCpuUsageData": ("GetCpuUsageData", "CMS_GetCpuUsageData"),
        "GetMemUsedData": ("GetMemUsedData", "CMS_GetMemUsedData"),
        "GetDiskUsageData": ("GetDiskUsageData", "CMS_GetDiskUsageData"),
        "DescribeRegions": ("DescribeRegions", "ECS_DescribeRegions"),
        "DescribeVSwitches": ("DescribeVSwitches", "VPC_DescribeVSwitches"),
        "DescribeImages": ("DescribeImages", "ECS_DescribeImages"),
        "DescribeAvailableResource": (
            "DescribeAvailableResource",
            "ECS_DescribeAvailableResource",
        ),
        "CommonAPICaller": ("CommonAPICaller",),
        "RunCommand": ("RunCommand", "OOS_RunCommand"),
        "OOS_CodeDeploy": ("OOS_CodeDeploy",),
    }

    def __init__(
        self,
        access_key_id: Optional[str] = None,
        access_key_secret: Optional[str] = None,
        region: str = "us-east-1",
        simulate: bool = True,
        strict: bool = False,
    ):
        # Auto-load .env file if available
        try:
            from sage.env_config import load_dotenv

            load_dotenv()
        except ImportError:
            pass  # env_config not available, rely on raw env vars

        # Load credentials from env vars first, then constructor args
        self.access_key_id = access_key_id or os.environ.get(
            ENV_ALIBABA_ACCESS_KEY_ID, ""
        )
        self.access_key_secret = access_key_secret or os.environ.get(
            ENV_ALIBABA_ACCESS_KEY_SECRET, ""
        )
        self.region = region or os.environ.get(ENV_ALIBABA_REGION, "us-east-1")
        self.strict = strict
        if (
            strict
            and not simulate
            and not (self.access_key_id and self.access_key_secret)
        ):
            raise MCPClientError(
                "real cloud mode requires Alibaba Cloud credentials",
                tool="connect",
                retryable=False,
            )
        self.simulate = simulate or not (self.access_key_id and self.access_key_secret)
        self._run_trace_id: Optional[str] = None

        # MCP subprocess management
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._server_capabilities: Optional[dict] = None
        self._available_tools: list[dict] = []
        self._last_healthy: float = 0.0  # timestamp of last successful communication

        if not self.simulate:
            self._connect()

    @staticmethod
    def credential_status(project_dir: Optional[str] = None) -> dict:
        """Return Alibaba Cloud credential readiness without opening MCP."""
        try:
            from sage.env_config import load_dotenv

            load_dotenv(project_dir)
        except ImportError:
            pass

        access_key_id = os.environ.get(ENV_ALIBABA_ACCESS_KEY_ID, "")
        access_key_secret = os.environ.get(ENV_ALIBABA_ACCESS_KEY_SECRET, "")
        region = os.environ.get(ENV_ALIBABA_REGION, "us-east-1")
        return {
            "ready": bool(access_key_id and access_key_secret),
            "access_key_id_set": bool(access_key_id),
            "access_key_secret_set": bool(access_key_secret),
            "region": region,
        }

    def _connect(self):
        """Spawn the MCP server subprocess and perform handshake.

        Handles:
        - uvx not found → falls back to simulate with warning
        - Server timeout → falls back to simulate
        - Handshake failure → falls back to simulate
        """
        try:
            # Build environment with credentials
            env = os.environ.copy()
            if self.access_key_id:
                env["ALIBABA_CLOUD_ACCESS_KEY_ID"] = self.access_key_id
            if self.access_key_secret:
                env["ALIBABA_CLOUD_ACCESS_KEY_SECRET"] = self.access_key_secret
            env["ALIBABA_CLOUD_REGION_ID"] = self.region

            # Spawn the MCP server via uvx with timeout
            # --services ecs,vpc limits tools to ECS + VPC (faster startup)
            self._process = subprocess.Popen(
                [
                    "uvx",
                    "alibaba-cloud-ops-mcp-server@latest",
                    "--transport",
                    "stdio",
                    "--services",
                    "ecs,vpc",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )

            # Perform MCP initialize handshake with timeout
            self._handshake_with_timeout(self.DEFAULT_TIMEOUT)

            # List available tools from the server
            self._discover_tools()

            self._last_healthy = time.time()
            logger.info(
                "MCP server connected with %d tools", len(self._available_tools)
            )

        except subprocess.TimeoutExpired:
            self._cleanup_process()
            if self.strict:
                raise MCPClientError(
                    "MCP server startup timed out",
                    tool="connect",
                    retryable=True,
                )
            logger.warning("MCP server startup timed out, falling back to simulate")
            self.simulate = True
        except Exception as e:
            self._cleanup_process()
            if self.strict:
                if isinstance(e, MCPClientError):
                    raise
                raise MCPClientError(
                    f"MCP server connection failed: {e}",
                    tool="connect",
                    retryable=True,
                ) from e
            logger.warning(
                "MCP server connection failed, falling back to simulate: %s", e
            )
            self.simulate = True

    def _send_request(
        self,
        method: str,
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        """Send a JSON-RPC 2.0 request and wait for response.

        Args:
            method: JSON-RPC method name
            params: Optional parameters dict
            timeout: Optional timeout in seconds (default: DEFAULT_TIMEOUT)

        Raises:
            MCPClientError: on communication failure or server error
        """
        if not self._process or not self._process.stdin or not self._process.stdout:
            raise MCPClientError("MCP server not connected")

        if self._process.poll() is not None:
            # Process has terminated
            raise MCPClientError(
                f"MCP server process terminated (exit code: {self._process.returncode})",
                retryable=True,
            )

        effective_timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT

        with self._lock:
            self._request_id += 1
            request_id = self._request_id

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        # Send request
        line = json.dumps(request) + "\n"
        try:
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise MCPClientError(
                f"Failed to write to MCP server: {e}", retryable=True
            ) from e

        # Read response with timeout (using a reader thread)
        result_holder = [None]
        error_holder = [None]

        def _read_response():
            try:
                response_line = self._process.stdout.readline()
                if not response_line:
                    error_holder[0] = MCPClientError(
                        "MCP server closed connection", retryable=True
                    )
                    return
                result_holder[0] = json.loads(response_line)
            except Exception as e:
                error_holder[0] = MCPClientError(
                    f"Read error: {e}",
                    retryable=not isinstance(e, json.JSONDecodeError),
                )

        reader_thread = threading.Thread(target=_read_response, daemon=True)
        reader_thread.start()
        reader_thread.join(timeout=effective_timeout)

        if reader_thread.is_alive():
            # Timeout — reader is stuck, likely server hung
            raise MCPClientError(
                f"MCP server response timed out after {effective_timeout}s",
                retryable=True,
            )

        if error_holder[0] is not None:
            raise error_holder[0]

        response = result_holder[0]
        if response is None:
            raise MCPClientError("No response from MCP server")

        if "error" in response:
            error = response["error"]
            error_code = error.get("code", 0)
            # JSON-RPC errors in range -32000 to -32099 are server errors (retryable)
            retryable = -32099 <= error_code <= -32000
            raise MCPClientError(
                f"MCP error {error_code}: {error.get('message', 'unknown')}",
                retryable=retryable,
            )

        self._last_healthy = time.time()
        return response.get("result", {})

    def _send_notification(self, method: str, params: Optional[dict] = None):
        """Send a JSON-RPC 2.0 notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return

        notification = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            notification["params"] = params

        line = json.dumps(notification) + "\n"
        try:
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            logger.warning("Failed to send notification: %s", e)

    def _handshake_with_timeout(self, timeout: float):
        """Perform MCP initialize handshake with a timeout."""
        self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "sage-agent", "version": "0.1.0"},
            },
            timeout=timeout,
        )

        # Send initialized notification
        self._send_notification("notifications/initialized")

    def _discover_tools(self):
        """Discover available tools from the MCP server."""
        result = self._send_request("tools/list")
        self._available_tools = result.get("tools", [])

    def _parse_tool_response(self, result: dict) -> dict:
        """Parse MCP tool response content into a structured dict.

        Handles the MCP content array format: iterates text items, attempts
        JSON parsing, and falls back to raw text wrapping. MCP tool-level
        errors are raised instead of being mistaken for valid empty results.
        """
        content = result.get("content", [])
        if result.get("isError"):
            message = "MCP tool returned an error"
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        message = item.get("text", message)
                        break
            raise MCPClientError(
                redact_sensitive(message),
                retryable=False,
            )
        if content and isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"result": text}
            return {"content": content}
        return result

    def _resolve_server_tool_name(self, tool_name: str) -> str:
        """Map Sage's stable logical names to the connected server's tool IDs."""
        available_names = {
            str(tool.get("name", ""))
            for tool in self._available_tools
            if isinstance(tool, dict)
        }
        for candidate in self.TOOL_NAME_ALIASES.get(tool_name, (tool_name,)):
            if candidate in available_names:
                return candidate
        return tool_name

    def _call_mcp_tool(
        self, tool_name: str, arguments: dict, trace_id: Optional[str] = None
    ) -> dict:
        """Call an MCP tool by name with retry on transient failures.

        Args:
            tool_name: MCP tool to invoke
            arguments: Tool arguments dict
            trace_id: Optional correlation ID for tracing (auto-generated if None)

        Retry strategy:
        1. Try the call
        2. On transient failure (timeout, server error, connection lost):
           a. Attempt reconnect if needed
           b. Exponential backoff with jitter
        3. After MAX_RETRIES exhausted, raise MCPClientError
        """
        if tool_name not in self.ALLOWED_TOOL_NAMES:
            raise MCPClientError(
                f"MCP tool is not allowed: {tool_name}",
                tool=tool_name,
                retryable=False,
            )
        if trace_id is None:
            trace_id = getattr(self, "_run_trace_id", None) or uuid.uuid4().hex[:12]

        if self.simulate:
            result = self._get_simulated_response(tool_name, arguments)
            result["_trace_id"] = trace_id
            return result

        server_tool_name = self._resolve_server_tool_name(tool_name)
        last_error: Optional[MCPClientError] = None

        max_retries = (
            0 if tool_name in self.NON_RETRYABLE_MUTATION_TOOLS else self.MAX_RETRIES
        )
        for attempt in range(max_retries + 1):
            try:
                result = self._send_request(
                    "tools/call", {"name": server_tool_name, "arguments": arguments}
                )
                parsed = self._parse_tool_response(result)
                parsed["_trace_id"] = trace_id
                return parsed

            except MCPClientError as e:
                last_error = e

                if not e.retryable:
                    raise MCPClientError(
                        e.args[0], tool=tool_name, retryable=False
                    ) from e

                if attempt < max_retries:
                    if not self._process or self._process.poll() is not None:
                        logger.info(
                            "[%s] %s: server dead, reconnecting (attempt %d/%d)",
                            trace_id,
                            tool_name,
                            attempt + 1,
                            max_retries + 1,
                        )
                        self._try_reconnect()

                    base_delay = self.RETRY_DELAY * (2**attempt)
                    jitter = random.uniform(0, base_delay * self.MAX_JITTER)
                    delay = base_delay + jitter
                    logger.info(
                        "[%s] Retrying %s in %.1fs (attempt %d/%d)...",
                        trace_id,
                        tool_name,
                        delay,
                        attempt + 2,
                        max_retries + 1,
                    )
                    time.sleep(delay)

        mutation_suffix = (
            "; automatic retry disabled because the mutation outcome may be ambiguous"
            if tool_name in self.NON_RETRYABLE_MUTATION_TOOLS
            else ""
        )
        raise MCPClientError(
            f"[{trace_id}] {tool_name}: failed after {max_retries + 1} attempts — "
            f"{last_error}{mutation_suffix}",
            tool=tool_name,
            retryable=tool_name not in self.NON_RETRYABLE_MUTATION_TOOLS,
        )

    def set_run_trace_id(self, trace_id: Optional[str]) -> None:
        """Correlate subsequent tool calls with the active API Run."""
        self._run_trace_id = trace_id

    def _get_simulated_response(self, tool_name: str, arguments: dict) -> dict:
        """Return simulated responses for demo/testing."""
        SIMULATED = {
            "DescribeInstances": {
                "Instances": {
                    "Instance": [
                        {
                            "InstanceId": "i-simulated12345",
                            "InstanceName": "sage-demo-instance",
                            "Status": "Running",
                            "RegionId": self.region,
                            "InstanceType": "ecs.t6-c1m1.large",
                            "PublicIpAddress": {"IpAddress": ["203.0.113.42"]},
                            "SecurityGroupIds": {"SecurityGroupId": ["sg-demo123"]},
                        }
                    ]
                },
                "TotalCount": 1,
                "simulated": True,
            },
            "DescribeSecurityGroups": {
                "SecurityGroups": {
                    "SecurityGroup": [
                        {
                            "SecurityGroupId": "sg-demo123",
                            "SecurityGroupName": "sage-demo-sg",
                            "Description": "Demo security group",
                            "VpcId": "vpc-simulated",
                        }
                    ]
                },
                "simulated": True,
            },
            "CreateSecurityGroup": {
                "SecurityGroupId": "sg-new12345",
                "RequestId": "simulated-request-id",
                "simulated": True,
            },
            "AuthorizeSecurityGroup": {
                "RequestId": "simulated-request-id",
                "simulated": True,
            },
            "RunInstances": {
                "InstanceId": "i-newinstance123",
                "RequestId": "simulated-request-id",
                "simulated": True,
            },
            "OOS_CodeDeploy": {
                "Status": "Deployed",
                "ApplicationId": "app-12345",
                "simulated": True,
            },
            "DescribeRegions": {
                "Regions": {
                    "Region": [
                        {"RegionId": "us-east-1", "LocalName": "US East (Virginia)"},
                        {
                            "RegionId": "us-west-1",
                            "LocalName": "US West (Silicon Valley)",
                        },
                        {"RegionId": "cn-hangzhou", "LocalName": "China (Hangzhou)"},
                    ]
                },
                "simulated": True,
            },
            "DescribeVSwitches": {
                "VSwitches": {
                    "VSwitch": [
                        {
                            "VSwitchId": "vsw-simulated",
                            "VpcId": "vpc-simulated",
                            "ZoneId": "cn-hangzhou-j",
                            "Status": "Available",
                        }
                    ]
                },
                "TotalCount": 1,
                "simulated": True,
            },
            "DescribeImages": {
                "Images": {
                    "Image": [
                        {
                            "ImageId": "ubuntu_22_04_x64_simulated",
                            "ImageName": "Ubuntu 22.04 simulated",
                            "Status": "Available",
                        }
                    ]
                },
                "TotalCount": 1,
                "simulated": True,
            },
            "DescribeAvailableResource": {
                "AvailableZones": {"AvailableZone": []},
                "simulated": True,
            },
            "RunCommand": {
                "Status": "Success",
                "Output": "SAGE_HEALTH_OK",
                "simulated": True,
            },
        }
        METRIC_TOOLS = {
            "cpu": {
                "DataPoints": {"DataPoint": [{"Average": 23.5, "Maximum": 45.2}]},
                "simulated": True,
            },
            "memory": {
                "DataPoints": {"DataPoint": [{"Average": 512.0, "Maximum": 1024.0}]},
                "simulated": True,
            },
            "disk": {
                "DataPoints": {"DataPoint": [{"Average": 12.3, "Maximum": 20.0}]},
                "simulated": True,
            },
        }
        # Lowercased MCP tool names → metric key. ``GetMemUsedData`` lowercased
        # (``getmemuseddata``) contains no ``"memory"`` substring, so the
        # substring matcher below cannot resolve it. Map the real tool names
        # back to their metric so simulate mode returns the right payload.
        _TOOL_TO_METRIC = {
            "getcpuusagedata": "cpu",
            "getmemuseddata": "memory",
            "getdiskusagedata": "disk",
        }
        if tool_name in SIMULATED:
            return SIMULATED[tool_name]
        lower = tool_name.lower()
        metric_key = _TOOL_TO_METRIC.get(lower)
        if metric_key is not None and metric_key in METRIC_TOOLS:
            return METRIC_TOOLS[metric_key]
        if resp := next((v for k, v in METRIC_TOOLS.items() if k in lower), None):
            return resp
        return {"error": f"Unknown simulated tool: {tool_name}", "simulated": True}

    # ── Public API (matches original interface) ──────────────────────

    def list_ecs_instances(self) -> dict:
        """List ECS instances in the region."""
        return self._call_mcp_tool("DescribeInstances", {"RegionId": self.region})

    def list_security_groups(self, vpc_id: str = "") -> dict:
        """List security groups in the region, optionally within one VPC."""
        arguments = {"RegionId": self.region}
        if vpc_id:
            arguments["VpcId"] = vpc_id
        return self._call_mcp_tool("DescribeSecurityGroups", arguments)

    def list_vswitches(self, vpc_id: str = "") -> dict:
        """List vSwitches available to real ECS deployments."""
        arguments = {"RegionId": self.region}
        if vpc_id:
            arguments["VpcId"] = vpc_id
        return self._call_mcp_tool("DescribeVSwitches", arguments)

    def list_images(self, image_id: str = "") -> dict:
        """List available system images in the configured region."""
        arguments = {"RegionId": self.region, "Status": "Available"}
        if image_id:
            arguments["ImageId"] = image_id
        return self._call_mcp_tool("DescribeImages", arguments)

    def describe_available_resources(
        self, zone_id: str, instance_charge_type: str = "PostPaid"
    ) -> dict:
        """Describe instance types available in a zone."""
        if not zone_id:
            raise MCPClientError(
                "zone_id is required to describe available resources",
                tool="DescribeAvailableResource",
                retryable=False,
            )
        return self._call_mcp_tool(
            "DescribeAvailableResource",
            {
                "RegionId": self.region,
                "ZoneId": zone_id,
                "InstanceChargeType": instance_charge_type,
            },
        )

    def create_security_group(
        self, name: str, description: str = "", vpc_id: str = ""
    ) -> dict:
        """Create a security group through a fixed ECS API operation."""
        if self.simulate:
            return self._call_mcp_tool(
                "CreateSecurityGroup",
                {
                    "RegionId": self.region,
                    "VpcId": vpc_id or "vpc-simulated",
                    "SecurityGroupName": name,
                    "Description": description or "Created by Sage agent",
                },
            )
        if not vpc_id:
            raise MCPClientError(
                "vpc_id is required to create a real security group",
                tool="CreateSecurityGroup",
                retryable=False,
            )
        return self._call_mcp_tool(
            "CommonAPICaller",
            {
                "service": "ecs",
                "api": "CreateSecurityGroup",
                "parameters": {
                    "RegionId": self.region,
                    "VpcId": vpc_id,
                    "SecurityGroupName": name,
                    "Description": description or "Created by Sage agent",
                },
            },
        )

    def authorize_security_group_ingress(
        self,
        security_group_id: str,
        port_range: str = "80/80",
        protocol: str = "tcp",
        source_cidr: str = "0.0.0.0/0",
    ) -> dict:
        """Add an ingress rule to a security group."""
        arguments = {
            "RegionId": self.region,
            "SecurityGroupId": security_group_id,
            "IpProtocol": protocol,
            "PortRange": port_range,
            "SourceCidrIp": source_cidr,
        }
        if self.simulate:
            return self._call_mcp_tool("AuthorizeSecurityGroup", arguments)
        return self._call_mcp_tool(
            "CommonAPICaller",
            {
                "service": "ecs",
                "api": "AuthorizeSecurityGroup",
                "parameters": arguments,
            },
        )

    def create_ecs_instance(
        self,
        name: str,
        image_id: str = "ubuntu_22_04",
        instance_type: str = "ecs.t6-c1m1.large",
        security_group_id: str = "",
        vswitch_id: str = "",
    ) -> dict:
        """Create exactly one private ECS instance with no public bandwidth."""
        if not self.simulate:
            missing = [
                field
                for field, value in {
                    "image_id": image_id,
                    "instance_type": instance_type,
                    "security_group_id": security_group_id,
                    "vswitch_id": vswitch_id,
                }.items()
                if not value
            ]
            if missing:
                raise MCPClientError(
                    f"real instance creation requires: {', '.join(missing)}",
                    tool="RunInstances",
                    retryable=False,
                )
        return self._call_mcp_tool(
            "RunInstances",
            {
                "RegionId": self.region,
                "InstanceName": name,
                "ImageId": image_id,
                "InstanceType": instance_type,
                "SecurityGroupId": security_group_id,
                "VSwitchId": vswitch_id or "vsw-simulated",
                "InternetMaxBandwidthOut": 0,
                "Amount": 1,
            },
        )

    def deploy_application(
        self,
        instance_id: str,
        app_type: str,
        app_path: str = "",
        *,
        port: int = 8080,
        name: str = "sage-app",
        application_group_name: str = "sage-app-group",
        object_name: str = "",
        project_path: str = "",
    ) -> dict:
        """Deploy a local artifact with the current OOS_CodeDeploy contract."""
        if self.simulate:
            return self._call_mcp_tool(
                "OOS_CodeDeploy",
                {
                    "InstanceId": instance_id,
                    "ApplicationType": app_type,
                    "ApplicationPath": app_path or "/opt/app",
                },
            )

        artifact = Path(app_path) if app_path else None
        if artifact is None or not artifact.exists():
            raise MCPClientError(
                "real deployment requires an existing local app_path artifact",
                tool="OOS_CodeDeploy",
                retryable=False,
            )
        root = Path(project_path) if project_path else artifact.parent
        return self._call_mcp_tool(
            "OOS_CodeDeploy",
            {
                "name": name,
                "deploy_region_id": self.region,
                "application_group_name": application_group_name,
                "object_name": object_name or artifact.name,
                "file_path": str(artifact),
                "deploy_language": app_type,
                "port": int(port),
                "project_path": str(root),
                "instance_ids": [instance_id],
            },
        )

    def run_command(
        self, instance_ids: list[str], command: str, command_type: str = "RunShellScript"
    ) -> dict:
        """Run one bounded OOS command against explicit instance IDs."""
        if not instance_ids or not all(instance_ids):
            raise MCPClientError(
                "at least one instance ID is required",
                tool="RunCommand",
                retryable=False,
            )
        if not command.strip():
            raise MCPClientError(
                "command must be non-empty", tool="RunCommand", retryable=False
            )
        return self._call_mcp_tool(
            "RunCommand",
            {
                "Command": command,
                "InstanceIds": instance_ids,
                "RegionId": self.region,
                "CommandType": command_type,
            },
        )

    def check_application_health(self, instance_id: str, port: int) -> dict:
        """Verify an application from inside the instance over localhost."""
        command = (
            "python3 -c \"import urllib.request; "
            f"body=urllib.request.urlopen('http://127.0.0.1:{int(port)}/health', "
            "timeout=5).read().decode(); "
            "assert body.strip(), 'empty health response'; print(body)\""
        )
        return self.run_command([instance_id], command)

    def get_instance_metrics(self, instance_id: str, metric: str = "cpu") -> dict:
        """Get monitoring metrics for an instance."""
        metric_map = {
            "cpu": "GetCpuUsageData",
            "memory": "GetMemUsedData",
            "disk": "GetDiskUsageData",
        }
        tool_name = metric_map.get(metric, "GetCpuUsageData")
        return self._call_mcp_tool(tool_name, {"InstanceId": instance_id})

    def list_regions(self) -> dict:
        """List available regions."""
        return self._call_mcp_tool("DescribeRegions", {})

    def get_available_tools(self) -> list[dict]:
        """List available tools (from server discovery or static fallback)."""
        if self._available_tools:
            return [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("inputSchema", {}),
                }
                for t in self._available_tools
            ]
        # Fallback tool definitions
        return [
            {
                "name": "list_ecs_instances",
                "description": "List all ECS instances in the region",
            },
            {
                "name": "list_security_groups",
                "description": "List all security groups in the region",
            },
            {
                "name": "create_security_group",
                "description": "Create a new security group",
                "parameters": {"name": "string", "description": "string"},
            },
            {
                "name": "authorize_security_group_ingress",
                "description": "Add an ingress rule to a security group",
                "parameters": {
                    "security_group_id": "string",
                    "port_range": "string",
                    "protocol": "string",
                    "source_cidr": "string",
                },
            },
            {
                "name": "create_ecs_instance",
                "description": "Create an ECS instance",
                "parameters": {
                    "name": "string",
                    "image_id": "string",
                    "instance_type": "string",
                    "security_group_id": "string",
                },
            },
            {
                "name": "deploy_application",
                "description": "Deploy an application to an ECS instance",
                "parameters": {
                    "instance_id": "string",
                    "app_type": "string",
                    "app_path": "string",
                },
            },
            {
                "name": "get_instance_metrics",
                "description": "Get monitoring metrics for an instance",
                "parameters": {"instance_id": "string", "metric": "string"},
            },
        ]

    def _cleanup_process(self):
        """Terminate and clean up the subprocess if it exists."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

    def _try_reconnect(self) -> bool:
        """Attempt to reconnect to the MCP server.

        Returns True if reconnection succeeded.
        """
        try:
            self._cleanup_process()
            self._connect()
            return not self.simulate
        except Exception as e:
            logger.warning("MCP reconnect failed: %s", redact_sensitive(e))
            self.simulate = True
            return False

    def is_healthy(self) -> bool:
        """Check if the MCP server connection is likely alive.

        Returns True if simulate mode or last successful communication
        was within HEALTH_CHECK_TIMEOUT * 10 seconds.
        """
        if self.simulate:
            return True
        if not self._process or self._process.poll() is not None:
            return False
        # Consider unhealthy if no successful comms in 50 seconds
        return (time.time() - self._last_healthy) < (self.HEALTH_CHECK_TIMEOUT * 10)

    def health_check(self) -> dict:
        """Active health check: pings the MCP server and returns structured status.

        Returns a dict with:
            healthy (bool), latency_ms (float), error (str|None), trace_id (str)
        """
        trace_id = uuid.uuid4().hex[:12]
        if self.simulate:
            return {
                "healthy": True,
                "latency_ms": 0.0,
                "error": None,
                "trace_id": trace_id,
                "mode": "simulated",
            }
        if not self._process or self._process.poll() is not None:
            return {
                "healthy": False,
                "latency_ms": 0.0,
                "error": "MCP server process not running",
                "error_type": "process_dead",
                "retry_after_seconds": 5,
                "suggestions": ["Call _try_reconnect()", "Check MCP server binary"],
                "trace_id": trace_id,
                "mode": "connected",
            }
        start = time.monotonic()
        try:
            self._send_request("ping", timeout=self.HEALTH_CHECK_TIMEOUT)
            latency = (time.monotonic() - start) * 1000
            return {
                "healthy": True,
                "latency_ms": round(latency, 1),
                "error": None,
                "trace_id": trace_id,
                "mode": "connected",
            }
        except MCPClientError as e:
            latency = (time.monotonic() - start) * 1000
            safe_error = redact_sensitive(
                e, (self.access_key_id, self.access_key_secret)
            )
            return {
                "healthy": False,
                "latency_ms": round(latency, 1),
                "error": safe_error,
                "error_type": "timeout"
                if "timed out" in safe_error
                else "server_error",
                "retry_after_seconds": 10,
                "suggestions": ["Wait and retry", "Check server logs"],
                "trace_id": trace_id,
                "mode": "connected",
            }

    def close(self):
        """Shut down the MCP server subprocess."""
        if self._process:
            try:
                self._send_notification("notifications/cancelled")
                self._process.stdin.close()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.terminate()
                except Exception:
                    pass
            self._process = None



if __name__ == "__main__":
    client = MCPClient(simulate=True)
    print("Available tools:")
    for tool in client.get_available_tools():
        print(f"  - {tool['name']}: {tool['description']}")

    print("\nSimulated ECS instances:")
    instances = client.list_ecs_instances()
    print(json.dumps(instances, indent=2))

    client.close()
