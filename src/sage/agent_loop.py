"""
Agent Loop — LLM-first execution (OpenClaw / MemGPT style).

The LLM is the decision-maker. Each turn it observes the current progress and
picks ONE tool to call. Learned memory (rules) is injected into the prompt, so
the model's decisions get better as it learns. There is no deterministic
pipeline: online mode drives a real Qwen model, offline mode drives a
deterministic stub that plays the exact same role.

Ground truth lives in DeploymentSandbox. The simulated cloud has a real
requirement — an org port convention — that the model can only satisfy
reliably once it has learned it. That makes the counterfactual honest: same
model, same tools, and memory is the only difference between success and
failure.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .tools.mcp_client import MCPClient, MCPClientError
from .security import redact_sensitive

logger = logging.getLogger(__name__)


# ─── Tool schema exposed to the model ────────────────────────────────────────

TOOLS = [
    {
        "name": "list_instances",
        "signature": "list_instances()",
        "desc": "List existing ECS instances.",
    },
    {
        "name": "list_security_groups",
        "signature": "list_security_groups()",
        "desc": "List existing security groups.",
    },
    {
        "name": "list_vswitches",
        "signature": "list_vswitches()",
        "desc": "List available vSwitches and their VPC/zone IDs.",
    },
    {
        "name": "list_images",
        "signature": "list_images()",
        "desc": "List available ECS system images.",
    },
    {
        "name": "get_state",
        "signature": "get_state()",
        "desc": "Get sandbox progress plus actionable cloud inventory and the next safe step.",
    },
    {
        "name": "create_security_group",
        "signature": "create_security_group(name, vpc_id)",
        "desc": "Create a security group in an explicit VPC. Returns its id.",
    },
    {
        "name": "open_port",
        "signature": "open_port(port, protocol='tcp', cidr='0.0.0.0/0')",
        "desc": "Authorize inbound TCP traffic on a port in the security group.",
    },
    {
        "name": "create_instance",
        "signature": "create_instance(name, image_id, instance_type, vswitch_id)",
        "desc": "Create exactly one private ECS instance using explicit preflight values.",
    },
    {
        "name": "deploy",
        "signature": "deploy(app_path, port=8080)",
        "desc": "Deploy an existing local application artifact onto the instance.",
    },
    {
        "name": "check_health",
        "signature": "check_health()",
        "desc": "Check whether the deployed app is reachable. Returns healthy/unhealthy + reason.",
    },
    {
        "name": "finish",
        "signature": "finish(summary)",
        "desc": "Finish the task. Only call this once the deployment is complete.",
    },
]

TOOL_NAMES = frozenset(tool["name"] for tool in TOOLS)
READ_ONLY_TOOLS = frozenset(
    {
        "list_instances",
        "list_security_groups",
        "list_vswitches",
        "list_images",
        "get_state",
        "finish",
    }
)


class DeploymentSandbox:
    """
    Stateful simulated cloud that holds the ground-truth requirement.

    APP_LISTEN_PORT encodes an *organization convention*: the port each app type
    actually binds to in this company's environment. The model does not know
    this a priori — it learns it through corrections (stored as rules). A
    deployment is only healthy if the security group allows the app's real port.
    """

    # Company/org convention — the "reality" the agent discovers through
    # corrections. 80/443 are reserved for the load balancer, so app services
    # that bind elsewhere are unreachable until their real port is opened.
    # All application services share the company standard (8080); only static
    # sites are served directly on 80. A rule learned for one app type therefore
    # transfers to the others.
    APP_LISTEN_PORT = {
        "node": 8080,
        "python": 8080,
        "java": 8080,
        "docker": 80,
        "static": 80,
    }

    def __init__(self, mcp: MCPClient, app_type: str):
        self.mcp = mcp
        self.app_type = app_type
        self.required_port = self.APP_LISTEN_PORT.get(app_type, 80)
        self.sg_id: Optional[str] = None
        self.opened_ports: set[int] = set()
        self.instance_id: Optional[str] = None
        self.deployed = False
        self.sg_listed = False
        self.instances_listed = False
        # Real-mode preflight selection (populated by the model's list_* calls).
        self.vpc_id: Optional[str] = None
        self.vswitch_id: Optional[str] = None
        self.image_id: Optional[str] = None
        self.instance_type: Optional[str] = None
        self.real_cloud = bool(getattr(self.mcp, "simulate", True)) is False

    # ── Tool handlers (each returns an observation dict) ──────────────────────

    def _state_hint(self) -> str:
        if self.sg_id is None:
            return (
                "Cloud inventory is informational and not attached to this run. "
                "Create one dedicated security group for this run instead of relisting."
            )
        if self.instance_id is None:
            return (
                "A run security group exists. Configure its required ingress, then "
                "create one dedicated instance instead of relisting inventory."
            )
        if not self.deployed:
            return "A run instance exists. Deploy the application next."
        return "The application is deployed. Check health and then finish if healthy."

    def list_instances(self, **_) -> dict:
        self.instances_listed = True
        try:
            response = self.mcp.list_ecs_instances()
        except MCPClientError as exc:
            return {"ok": False, "error": redact_sensitive(exc)}
        raw_instances = response.get("Instances", {}).get("Instance", [])
        if not isinstance(raw_instances, list):
            raw_instances = []
        instances = [
            {
                "id": item.get("InstanceId", ""),
                "name": item.get("InstanceName", ""),
                "status": item.get("Status", ""),
            }
            for item in raw_instances
            if isinstance(item, dict)
        ]
        return {
            "ok": True,
            "instances": instances,
            "count": int(
                response.get("TotalCount", len(instances)) or len(instances)
            ),
            "sandbox_instance": self.instance_id,
            "hint": self._state_hint(),
        }

    def list_security_groups(self, **_) -> dict:
        self.sg_listed = True
        try:
            response = self.mcp.list_security_groups()
        except MCPClientError as exc:
            return {"ok": False, "error": redact_sensitive(exc)}
        raw_groups = response.get("SecurityGroups", {}).get("SecurityGroup", [])
        if not isinstance(raw_groups, list):
            raw_groups = []
        groups = [
            {
                "id": item.get("SecurityGroupId", ""),
                "name": item.get("SecurityGroupName", ""),
                "description": item.get("Description", ""),
            }
            for item in raw_groups
            if isinstance(item, dict)
        ]
        return {
            "ok": True,
            "security_groups": groups,
            "count": int(response.get("TotalCount", len(groups)) or len(groups)),
            "sandbox_security_group": self.sg_id,
            "hint": self._state_hint(),
        }

    def list_vswitches(self, vpc_id: str = "", **_) -> dict:
        try:
            response = self.mcp.list_vswitches(vpc_id=vpc_id)
        except MCPClientError as exc:
            return {"ok": False, "error": redact_sensitive(exc)}
        raw = response.get("VSwitches", {}).get("VSwitch", [])
        if not isinstance(raw, list):
            raw = []
        vswitches = [
            {
                "id": item.get("VSwitchId", ""),
                "vpc_id": item.get("VpcId", ""),
                "zone_id": item.get("ZoneId", ""),
                "status": item.get("Status", ""),
            }
            for item in raw
            if isinstance(item, dict)
        ]
        if vswitches and not self.vswitch_id:
            first = vswitches[0]
            self.vswitch_id = first["id"]
            self.vpc_id = first["vpc_id"] or self.vpc_id
        return {"ok": True, "vswitches": vswitches}

    def list_images(self, image_id: str = "", **_) -> dict:
        try:
            response = self.mcp.list_images(image_id=image_id)
        except MCPClientError as exc:
            return {"ok": False, "error": redact_sensitive(exc)}
        raw = response.get("Images", {}).get("Image", [])
        if not isinstance(raw, list):
            raw = []
        images = [
            {"id": item.get("ImageId", ""), "name": item.get("ImageName", "")}
            for item in raw
            if isinstance(item, dict)
        ]
        if images and not self.image_id:
            self.image_id = images[0]["id"]
        return {"ok": True, "images": images}

    def get_state(self, **_) -> dict:
        instances = self.list_instances()
        security_groups = self.list_security_groups()
        vswitches = self.list_vswitches()
        return {
            "ok": bool(
                instances.get("ok")
                and security_groups.get("ok")
                and vswitches.get("ok")
            ),
            "progress": self.progress(),
            "instances": instances,
            "security_groups": security_groups,
            "vswitches": vswitches,
            "hint": self._state_hint(),
        }

    def create_security_group(self, name: str = "sage-sg", vpc_id: str = "", **_) -> dict:
        if self.sg_id is not None:
            return {
                "ok": True,
                "security_group_id": self.sg_id,
                "already_exists": True,
            }
        resolved_vpc = vpc_id or self.vpc_id or ""
        if self.real_cloud and not resolved_vpc:
            return {
                "ok": False,
                "error": "real security-group creation requires a vpc_id; list vSwitches first or pass vpc_id",
            }
        response = self.mcp.create_security_group(
            name=name, description="Created by Sage", vpc_id=resolved_vpc
        )
        self.sg_id = response.get("SecurityGroupId", "sg-unknown")
        return {"ok": True, "security_group_id": self.sg_id}

    def open_port(
        self, port=None, protocol: str = "tcp", cidr: str = "0.0.0.0/0", **_
    ) -> dict:
        if self.sg_id is None:
            self.create_security_group()  # auto-provision so ordering never blocks
        try:
            port = int(port)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"invalid port: {port!r}"}
        self.mcp.authorize_security_group_ingress(
            security_group_id=self.sg_id,
            port_range=f"{port}/{port}",
            protocol=protocol,
            source_cidr=cidr,
        )
        self.opened_ports.add(port)
        return {"ok": True, "opened_ports": sorted(self.opened_ports)}

    def create_instance(
        self,
        name: str = "sage-app",
        image_id: str = "",
        instance_type: str = "",
        vswitch_id: str = "",
        **_,
    ) -> dict:
        if self.instance_id is not None:
            return {
                "ok": True,
                "instance_id": self.instance_id,
                "already_exists": True,
            }
        if self.sg_id is None:
            self.create_security_group()
        resolved_image = image_id or self.image_id or ""
        resolved_type = instance_type or self.instance_type or "ecs.t6-c1m1.large"
        resolved_vswitch = vswitch_id or self.vswitch_id or ""
        if self.real_cloud:
            missing = [
                label
                for label, value in {
                    "image_id": resolved_image,
                    "vswitch_id": resolved_vswitch,
                }.items()
                if not value
            ]
            if missing:
                return {
                    "ok": False,
                    "error": (
                        "real instance creation requires preflight values for: "
                        + ", ".join(missing)
                        + "; call list_images and list_vswitches first"
                    ),
                }
        response = self.mcp.create_ecs_instance(
            name=name,
            image_id=resolved_image or "ubuntu_22_04_x64_20G_alibase",
            instance_type=resolved_type,
            security_group_id=self.sg_id or "",
            vswitch_id=resolved_vswitch,
        )
        self.instance_id = response.get("InstanceId", "i-unknown")
        return {"ok": True, "instance_id": self.instance_id}

    def deploy(self, app_path: str = "", port: int = 0, **_) -> dict:
        if self.instance_id is None:
            return {"ok": False, "error": "no instance exists; create one first"}
        resolved_port = int(port) if port else self.required_port
        try:
            self.mcp.deploy_application(
                instance_id=self.instance_id,
                app_type=self.app_type,
                app_path=app_path,
                port=resolved_port,
            )
        except MCPClientError as exc:
            return {"ok": False, "error": redact_sensitive(exc)}
        self.deployed = True
        return {"ok": True, "deployed": True, "port": resolved_port}

    def check_health(self, **_) -> dict:
        healthy, reason = self.verify()
        return {"ok": True, "healthy": healthy, "reason": reason}

    # ── Ground-truth verification ─────────────────────────────────────────────

    def verify(self) -> tuple[bool, str]:
        if not self.deployed:
            return False, "application not deployed"
        if self.instance_id is None:
            return False, "no running instance"
        if self.required_port not in self.opened_ports:
            return (
                False,
                f"app binds to port {self.required_port} (company standard) but the "
                f"security group does not allow it — opened ports: {sorted(self.opened_ports)}. "
                f"Ports 80/443 are reserved for the load balancer.",
            )
        return True, f"app reachable on port {self.required_port}"

    def outcome(self) -> tuple[str, str]:
        healthy, reason = self.verify()
        return ("success" if healthy else "failed"), reason

    def progress(self) -> dict:
        return {
            "instances_listed": self.instances_listed,
            "security_groups_listed": self.sg_listed,
            "security_group_id": self.sg_id,
            "ports_opened": sorted(self.opened_ports),
            "instance_id": self.instance_id,
            "deployed": self.deployed,
        }


# ─── The loop ─────────────────────────────────────────────────────────────────

MEMORY_START = "--- LEARNED MEMORY START ---"
MEMORY_END = "--- LEARNED MEMORY END ---"


class AgentLoop:
    """
    Runs the observe → decide → act loop until the model finishes or the
    iteration budget is exhausted. The model chooses one tool per turn.
    """

    def __init__(
        self, mcp: MCPClient, model_caller: Optional[Callable], max_iterations: int = 12
    ):
        self.mcp = mcp
        self.model_caller = model_caller
        self.max_iterations = max_iterations

    @staticmethod
    def _memory_constraints(memory_block: str) -> dict[str, list]:
        """Compile exact learned network values into an enforceable contract."""
        if not memory_block.strip():
            return {"ports": [], "cidrs": []}

        from .reflection import ReflectionEngine

        invariants = ReflectionEngine._extract_operational_invariants(memory_block)
        return {
            "ports": [int(value) for value in invariants if value.isdigit()],
            "cidrs": [
                value
                for value in invariants
                if ReflectionEngine._CIDR_PATTERN.fullmatch(value)
            ],
        }

    @staticmethod
    def _apply_memory_constraints(
        tool: str, args: dict, constraints: dict[str, list]
    ) -> tuple[dict, Optional[dict]]:
        """Apply exact learned ingress values before a cloud mutation executes."""
        safe_args = dict(args)
        if tool != "open_port":
            return safe_args, None

        required_ports = constraints.get("ports") or []
        required_cidrs = constraints.get("cidrs") or []
        try:
            attempted_port = int(safe_args.get("port"))
        except (TypeError, ValueError):
            attempted_port = None
        attempted_cidr = str(safe_args.get("cidr", "0.0.0.0/0"))

        changed = False
        if required_ports and attempted_port not in required_ports:
            safe_args["port"] = required_ports[0]
            changed = True
        if required_cidrs and attempted_cidr not in required_cidrs:
            safe_args["cidr"] = required_cidrs[0]
            changed = True
        if not changed:
            return safe_args, None

        return safe_args, {
            "attempted_port": attempted_port,
            "attempted_cidr": attempted_cidr,
            "applied_port": safe_args.get("port"),
            "applied_cidr": safe_args.get("cidr", "0.0.0.0/0"),
            "reason": "exact learned-memory network constraints override generic defaults",
        }

    def run_loop(
        self,
        task: str,
        app_type: str = "docker",
        memory_block: str = "",
        cancel_event=None,
        allowed_tools: Optional[list[str]] = None,
        read_only: bool = False,
    ) -> dict:
        sandbox = DeploymentSandbox(self.mcp, app_type)
        requested_tools = set(allowed_tools or TOOL_NAMES)
        unknown_tools = requested_tools - TOOL_NAMES
        if unknown_tools:
            raise ValueError(f"unknown allowed tools: {sorted(unknown_tools)}")
        effective_tools = requested_tools
        if read_only:
            effective_tools &= READ_ONLY_TOOLS
        allowed_tool_names = frozenset(effective_tools)
        memory_constraints = self._memory_constraints(memory_block)
        steps: list[dict] = []
        tools_used: list[str] = []
        transcript: list[str] = []
        error: Optional[str] = None
        failure_point: Optional[str] = None
        finished = False
        last_action_key: Optional[tuple[str, str]] = None
        repeated_action_count = 0
        iterations_used = 0
        successful_observations = 0

        for _ in range(self.max_iterations):
            if cancel_event is not None and cancel_event.is_set():
                error = "Run cancelled"
                failure_point = "cancelled"
                break
            iterations_used += 1
            prompt = self._build_prompt(
                task,
                app_type,
                memory_block,
                sandbox,
                transcript,
                allowed_tool_names=allowed_tool_names,
                read_only=read_only,
            )
            try:
                raw = (
                    self.model_caller(prompt, max_tokens=400, task_type="execution")
                    if self.model_caller
                    else ""
                )
            except Exception as e:  # model unavailable / circuit breaker / etc.
                error = f"model call failed: {e}"
                failure_point = "model_call"
                break

            action = self._parse_action(raw)
            if action is None:
                transcript.append(
                    "assistant: (unparseable response)\nobservation: please reply with a single JSON action"
                )
                continue

            tool = action.get("tool", "")
            args = action.get("args") or {}
            thought = action.get("thought", "")
            action_key = (tool, json.dumps(args, sort_keys=True, default=str))
            if action_key == last_action_key:
                repeated_action_count += 1
            else:
                repeated_action_count = 1
            last_action_key = action_key

            if tool == "finish" and tool in allowed_tool_names:
                finished_at = datetime.now(timezone.utc).isoformat()
                steps.append(
                    {
                        "step": args.get("summary", "Finish"),
                        "tool": "finish",
                        "args": args,
                        "result": "done",
                        "thought": thought,
                        "started_at": finished_at,
                        "finished_at": finished_at,
                        "duration_ms": 0.0,
                    }
                )
                finished = True
                break

            requested_args = args
            args, constraint_adjustment = self._apply_memory_constraints(
                tool, args, memory_constraints
            )
            progress_before = sandbox.progress()
            started_at = datetime.now(timezone.utc).isoformat()
            started = time.monotonic()
            if tool not in allowed_tool_names:
                observation = {
                    "ok": False,
                    "error": f"tool not allowed for this run: {tool}",
                }
            else:
                observation = self._execute(sandbox, tool, args)
            if observation.get("ok"):
                successful_observations += 1
            if constraint_adjustment:
                observation = {
                    **observation,
                    "memory_constraint_applied": constraint_adjustment,
                }
            finished_at = datetime.now(timezone.utc).isoformat()
            tools_used.append(tool)
            steps.append(
                {
                    "step": self._describe(tool, args),
                    "tool": tool,
                    "args": args,
                    "requested_args": requested_args,
                    "result": "success" if observation.get("ok") else "error",
                    "thought": thought,
                    "observation": observation,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                    "progress_before": progress_before,
                    "progress_after": sandbox.progress(),
                }
            )
            transcript.append(
                f"assistant: {tool}({json.dumps(args)})\nobservation: {json.dumps(observation)}"
            )
            if constraint_adjustment:
                transcript.append(
                    "system: Exact learned-memory network constraints were applied "
                    "before execution. Continue using the port and CIDR shown in "
                    "MEMORY_CONSTRAINTS_JSON."
                )
            if repeated_action_count >= 2:
                transcript.append(
                    "system: Repeated action detected. The previous observation is "
                    "already authoritative; choose a different action that advances "
                    "PROGRESS_JSON instead of repeating this tool."
                )

        if not finished and failure_point is None:
            failure_point = "max_iterations"

        if read_only:
            inspection_complete = finished and successful_observations > 0
            outcome = "success" if inspection_complete else "failed"
            reason = (
                f"read-only inspection completed after {successful_observations} successful observation(s)"
                if inspection_complete
                else "read-only inspection did not complete a successful observation and finish"
            )
            if outcome == "failed" and failure_point is None:
                failure_point = "inspection"
        else:
            outcome, reason = sandbox.outcome()
            if outcome == "failed" and failure_point is None:
                failure_point = "health_check"

        return {
            "task": task,
            "outcome": outcome,
            "steps": steps,
            "tools_used": tools_used,
            "error": error if error else (None if outcome == "success" else reason),
            "failure_point": None if outcome == "success" else failure_point,
            "policies_applied": [],  # filled in by Agent from applied rules
            "verify_reason": reason,
            "required_port": sandbox.required_port,
            "opened_ports": sorted(sandbox.opened_ports),
            "iterations_used": iterations_used,
            "max_iterations": self.max_iterations,
            "memory_constraints": memory_constraints,
            "read_only": read_only,
            "allowed_tools": sorted(allowed_tool_names),
            "successful_observations": successful_observations,
        }

    # ── Prompt construction ────────────────────────────────────────────────────

    def _build_prompt(
        self,
        task: str,
        app_type: str,
        memory_block: str,
        sandbox: DeploymentSandbox,
        transcript: list[str],
        allowed_tool_names: Optional[frozenset[str]] = None,
        read_only: bool = False,
    ) -> str:
        effective_tools = allowed_tool_names or TOOL_NAMES
        tools_desc = "\n".join(
            f"- {tool['signature']}: {tool['desc']}"
            for tool in TOOLS
            if tool["name"] in effective_tools
        )
        memory = memory_block.strip() or "(no learned rules yet)"
        memory_constraints_json = json.dumps(self._memory_constraints(memory_block))
        recent = "\n".join(transcript[-6:]) or "(none yet)"
        progress_json = json.dumps(sandbox.progress())
        run_contract = (
            "This is a READ-ONLY inventory task. Never request a mutation tool. "
            "Inspect state with the available tools, then call finish(summary)."
            if read_only
            else "This is a deployment task. Complete deployment and verify health before finishing."
        )

        return (
            "You are Sage, an autonomous deployment agent for Alibaba Cloud ECS.\n"
            "Accomplish the task by calling ONE tool per turn and reading the result.\n"
            f"{run_contract}\n"
            "Use the learned memory below to make better decisions than defaults.\n"
            "Treat exact operational parameters in learned memory—such as ports, "
            "CIDRs, protocols, and resource IDs—as authoritative organizational "
            "requirements. When they conflict with generic defaults, follow the "
            "learned values first instead of trying the defaults. Before calling "
            "open_port, scan learned memory for an exact port, protocol, and CIDR. "
            "If present, the first open_port action must match those values exactly; "
            "never probe an unlisted port or a broader CIDR first.\n\n"
            f"{MEMORY_START}\n{memory}\n{MEMORY_END}\n"
            f"MEMORY_CONSTRAINTS_JSON: {memory_constraints_json}\n\n"
            f"## Available tools\n{tools_desc}\n\n"
            f"## Task\n{task}  (app type: {app_type})\n\n"
            "## Progress so far\n"
            f"PROGRESS_JSON: {progress_json}\n\n"
            f"## Recent actions\n{recent}\n\n"
            "## Your next action\n"
            "Reply with ONE JSON object and nothing else:\n"
            '{"thought": "why", "tool": "<tool name>", "args": {<named args>}}\n'
            + (
                "Call finish(summary) after the requested inventory inspection is complete."
                if read_only
                else "Open every port the application actually needs before deploying. "
                "Call finish(summary) only after the app is deployed and healthy."
            )
        )

    def _parse_action(self, raw: str) -> Optional[dict]:
        from .tools.model_caller import ModelCaller

        data = ModelCaller.extract_json(raw)
        if not isinstance(data, dict):
            return None
        # Accept {tool,args} or nested {action:{tool,args}}
        if "tool" not in data and isinstance(data.get("action"), dict):
            action = data["action"]
            return {
                "tool": action.get("tool", ""),
                "args": action.get("args") or {},
                "thought": data.get("thought", action.get("thought", "")),
            }
        if "tool" in data:
            return {
                "tool": data.get("tool", ""),
                "args": data.get("args") or {},
                "thought": data.get("thought", ""),
            }
        return None

    def _execute(self, sandbox: DeploymentSandbox, tool: str, args: dict) -> dict:
        handler = {
            "list_instances": sandbox.list_instances,
            "list_security_groups": sandbox.list_security_groups,
            "list_vswitches": sandbox.list_vswitches,
            "list_images": sandbox.list_images,
            "get_state": sandbox.get_state,
            "create_security_group": sandbox.create_security_group,
            "open_port": sandbox.open_port,
            "create_instance": sandbox.create_instance,
            "deploy": sandbox.deploy,
            "check_health": sandbox.check_health,
        }.get(tool)
        if handler is None:
            return {"ok": False, "error": f"unknown tool: {tool}"}
        try:
            return handler(**args) if isinstance(args, dict) else handler()
        except Exception as e:
            return {"ok": False, "error": redact_sensitive(e)}

    @staticmethod
    def _describe(tool: str, args: dict) -> str:
        if tool == "open_port":
            return f"Open port {args.get('port')}"
        if tool == "create_security_group":
            return f"Create security group {args.get('name', '')}".strip()
        if tool == "create_instance":
            return f"Create instance {args.get('name', '')}".strip()
        if tool == "deploy":
            return "Deploy application"
        if tool == "check_health":
            return "Check deployment health"
        if tool == "list_instances":
            return "List ECS instances"
        if tool == "list_security_groups":
            return "List security groups"
        if tool == "list_vswitches":
            return "List vSwitches"
        if tool == "list_images":
            return "List system images"
        if tool == "get_state":
            return "Get deployment state"
        return tool
