"""Regression tests for defects found by the bounded live Qwen suite."""

import json
from unittest.mock import Mock

import pytest

from sage.agent_loop import AgentLoop, DeploymentSandbox, TOOLS
from sage.counterfactual import CounterfactualRunner
from sage.memory.episodic import EpisodicMemory
from sage.memory.procedural import ProceduralMemory
from sage.reflection import ReflectionEngine
from sage.tools.mcp_client import MCPClient, MCPClientError


def _reflection_engine(tmp_path, model_caller):
    return ReflectionEngine(
        ProceduralMemory(str(tmp_path / "rules" / "rules.md")),
        EpisodicMemory(str(tmp_path / "memory" / "episodic")),
        model_caller=model_caller,
    )


class TestReflectionInvariantPreservation:
    def test_analyze_correction_preserves_port_and_cidr(self, tmp_path):
        response = json.dumps(
            {
                "rule": "Configure the security group before deploying internal services.",
                "context": "Internal service deployment",
                "confidence": 0.95,
                "precondition": "security_group_configured",
                "repair": "authorize_security_group_ingress",
                "effect": "security_group_configured",
            }
        )
        engine = _reflection_engine(tmp_path, Mock(return_value=response))

        result = engine.analyze_correction(
            task="Deploy an internal Python API",
            action="Opened public load-balancer ports",
            error="Health check failed",
            correction="Authorize only TCP port 8080 from CIDR 10.20.0.0/16 before deployment.",
        )

        assert "8080" in result["rule"]
        assert "10.20.0.0/16" in result["rule"]
        stored = engine.procedural.get_all_rules()
        assert "8080" in stored[0]["text"]
        assert "10.20.0.0/16" in stored[0]["text"]

    def test_rejected_operational_rule_remains_visible_to_agent(self, tmp_path):
        response = json.dumps(
            {
                "rule": "Use the approved private ingress policy before deployment.",
                "context": "Internal service deployment",
                "confidence": 0.94,
                "precondition": "security_group_configured",
                "repair": "authorize_security_group_ingress",
                "effect": "security_group_configured",
            }
        )
        caller = Mock(side_effect=[response, "REJECT"])
        engine = _reflection_engine(tmp_path, caller)
        engine.procedural.add_rule(
            "Inspect existing resources before creating new ones.",
            "Cloud provisioning",
            0.9,
        )

        result = engine.analyze_correction(
            "Deploy private API",
            "Used generic public ingress",
            "Policy violation",
            "Authorize TCP port 8080 from CIDR 10.20.0.0/16 before deployment.",
        )

        assert result["confidence"] == 0.5
        memory_block = engine.procedural.get_rules_for_prompt()
        assert result["rule_id"] in memory_block
        assert AgentLoop._memory_constraints(memory_block) == {
            "ports": [8080],
            "cidrs": ["10.20.0.0/16"],
        }

    def test_preservation_keeps_existing_invariants_after_prefixing_missing_ones(
        self, tmp_path
    ):
        rule_text = (
            "Apply a carefully reviewed internal deployment policy "
            + ("x" * 125)
            + " CIDR 10.20.0.0/16"
        )
        response = json.dumps(
            {
                "rule": rule_text,
                "context": "Internal service deployment",
                "confidence": 0.9,
            }
        )
        engine = _reflection_engine(tmp_path, Mock(return_value=response))

        result = engine.analyze_correction(
            "Deploy API",
            "Used generic ingress",
            "Health check failed",
            (
                "For security group sg-sageprod7, authorize TCP port 8080 only "
                "from CIDR 10.20.0.0/16 before deployment."
            ),
        )

        assert len(result["rule"]) <= 200
        assert "8080" in result["rule"]
        assert "10.20.0.0/16" in result["rule"]
        assert "sg-sageprod7" in result["rule"]

    def test_forbidden_alternatives_are_not_preserved_as_requirements(self, tmp_path):
        response = json.dumps(
            {
                "rule": "Restrict private API ingress to the approved internal network.",
                "context": "Internal service deployment",
                "confidence": 0.9,
            }
        )
        engine = _reflection_engine(tmp_path, Mock(return_value=response))

        result = engine.analyze_correction(
            "Deploy API",
            "Used public ingress",
            "Policy violation",
            (
                "Authorize TCP port 8080 from CIDR 10.20.0.0/16. "
                "Do not use CIDR 0.0.0.0/0."
            ),
        )

        assert "8080" in result["rule"]
        assert "10.20.0.0/16" in result["rule"]
        assert "0.0.0.0/0" not in result["rule"]

    def test_positive_values_before_same_sentence_negation_are_preserved(self, tmp_path):
        response = json.dumps(
            {
                "rule": "Apply the approved private ingress configuration.",
                "context": "Internal service deployment",
                "confidence": 0.9,
            }
        )
        engine = _reflection_engine(tmp_path, Mock(return_value=response))

        result = engine.analyze_correction(
            "Deploy API",
            "Used public ingress",
            "Policy violation",
            (
                "Authorize TCP port 8080 from CIDR 10.20.0.0/16 and never use "
                "ports 80 or 443 from CIDR 0.0.0.0/0."
            ),
        )

        assert "8080" in result["rule"]
        assert "10.20.0.0/16" in result["rule"]
        assert "0.0.0.0/0" not in result["rule"]

    def test_preservation_skips_tokens_already_present(self, tmp_path):
        response = json.dumps(
            {
                "rule": "Allow TCP port 8080 only from CIDR 10.20.0.0/16 before deployment.",
                "context": "Internal service deployment",
                "confidence": 0.9,
            }
        )
        engine = _reflection_engine(tmp_path, Mock(return_value=response))

        result = engine.analyze_correction(
            "Deploy API",
            "Opened wrong port",
            "Health check failed",
            "Use port 8080 and CIDR 10.20.0.0/16.",
        )

        assert result["rule"].count("8080") == 1
        assert result["rule"].count("10.20.0.0/16") == 1

    def test_correction_without_operational_tokens_is_unchanged(self, tmp_path):
        rule_text = "Inspect the service configuration before deployment."
        response = json.dumps(
            {"rule": rule_text, "context": "Deployments", "confidence": 0.8}
        )
        engine = _reflection_engine(tmp_path, Mock(return_value=response))

        result = engine.analyze_correction(
            "Deploy API", "Skipped review", "Invalid config", "Review configuration first."
        )

        assert result["rule"] == rule_text

    def test_prompt_handles_literal_braces_in_user_input(self, tmp_path):
        response = json.dumps(
            {
                "rule": "Preserve literal configuration syntax when reflecting.",
                "context": "Configuration corrections",
                "confidence": 0.8,
            }
        )
        caller = Mock(return_value=response)
        engine = _reflection_engine(tmp_path, caller)

        result = engine.analyze_correction(
            task="Deploy service {api}",
            action='Applied config {"replicas": 2}',
            error="Template value ${PORT} was unresolved",
            correction="Keep the literal mapping {service: api} in the configuration.",
        )

        prompt = caller.call_args.args[0]
        assert "Deploy service {api}" in prompt
        assert '{"replicas": 2}' in prompt
        assert "${PORT}" in prompt
        assert "{service: api}" in prompt
        assert result["rule"] == "Preserve literal configuration syntax when reflecting."


class TestCounterfactualDecisionProjection:
    def test_identical_decisions_ignore_telemetry(self):
        with_memory = {
            "steps": [
                {
                    "tool": "list_instances",
                    "args": {},
                    "started_at": "2026-01-01T00:00:00Z",
                    "duration_ms": 1.0,
                    "observation": {"instances": 1},
                }
            ]
        }
        without_memory = {
            "steps": [
                {
                    "tool": "list_instances",
                    "args": {},
                    "started_at": "2026-01-01T00:00:01Z",
                    "duration_ms": 99.0,
                    "observation": {"instances": 2},
                }
            ]
        }

        assert CounterfactualRunner._first_divergence(with_memory, without_memory) is None

    def test_identical_decisions_ignore_thought_text(self):
        left = {
            "steps": [
                {
                    "tool": "open_port",
                    "args": {"port": 8080},
                    "thought": "The learned rule requires the company port.",
                }
            ]
        }
        right = {
            "steps": [
                {
                    "tool": "open_port",
                    "args": {"port": 8080},
                    "thought": "I will try this port.",
                }
            ]
        }

        assert CounterfactualRunner._first_divergence(left, right) is None

    def test_different_args_are_a_decision_divergence(self):
        left = {"steps": [{"tool": "open_port", "args": {"port": 8080}}]}
        right = {"steps": [{"tool": "open_port", "args": {"port": 443}}]}

        divergence = CounterfactualRunner._first_divergence(left, right)

        assert divergence["step_index"] == 1
        assert divergence["with_memory_args"] == {"port": 8080}
        assert divergence["without_memory_args"] == {"port": 443}
        assert divergence["reason"] == "decision_changed"

    def test_one_sided_step_reports_missing_step(self):
        left = {"steps": [{"tool": "finish", "args": {"summary": "done"}}]}
        right = {"steps": []}

        divergence = CounterfactualRunner._first_divergence(left, right)

        assert divergence["reason"] == "step_missing"


class TestActionableInventoryContract:
    def test_list_instances_returns_details_and_guidance(self):
        sandbox = DeploymentSandbox(MCPClient(simulate=True), "python")

        result = sandbox.list_instances()

        assert result["ok"] is True
        assert result["count"] == 1
        assert result["instances"] == [
            {
                "id": "i-simulated12345",
                "name": "sage-demo-instance",
                "status": "Running",
            }
        ]
        assert result["sandbox_instance"] is None
        assert "create" in result["hint"].lower()

    def test_list_instances_uses_items_when_total_count_is_zero(self):
        mcp = Mock()
        mcp.list_ecs_instances.return_value = {
            "TotalCount": 0,
            "Instances": {
                "Instance": [
                    {
                        "InstanceId": "i-visible",
                        "InstanceName": "visible-instance",
                        "Status": "Running",
                    }
                ]
            },
        }
        sandbox = DeploymentSandbox(mcp, "python")

        result = sandbox.list_instances()

        assert result["count"] == 1
        assert result["instances"][0]["id"] == "i-visible"

    def test_list_security_groups_returns_details_and_guidance(self):
        sandbox = DeploymentSandbox(MCPClient(simulate=True), "python")

        result = sandbox.list_security_groups()

        assert result["ok"] is True
        assert result["count"] == 1
        assert result["security_groups"][0]["id"] == "sg-demo123"
        assert result["sandbox_security_group"] is None
        assert "create" in result["hint"].lower()

    def test_create_security_group_is_idempotent(self):
        mcp = Mock()
        mcp.create_security_group.return_value = {"SecurityGroupId": "sg-run"}
        sandbox = DeploymentSandbox(mcp, "python")

        first = sandbox.create_security_group("run-sg")
        second = sandbox.create_security_group("run-sg")

        assert first["security_group_id"] == "sg-run"
        assert second == {
            "ok": True,
            "security_group_id": "sg-run",
            "already_exists": True,
        }
        mcp.create_security_group.assert_called_once()

    def test_create_instance_is_idempotent(self):
        mcp = Mock()
        mcp.create_security_group.return_value = {"SecurityGroupId": "sg-run"}
        mcp.create_ecs_instance.return_value = {"InstanceId": "i-run"}
        sandbox = DeploymentSandbox(mcp, "python")

        first = sandbox.create_instance("run-instance")
        second = sandbox.create_instance("run-instance")

        assert first["instance_id"] == "i-run"
        assert second == {"ok": True, "instance_id": "i-run", "already_exists": True}
        mcp.create_ecs_instance.assert_called_once()

    def test_get_state_combines_progress_inventory_and_hint(self):
        sandbox = DeploymentSandbox(MCPClient(simulate=True), "python")

        state = sandbox.get_state()

        assert state["ok"] is True
        assert state["progress"]["security_group_id"] is None
        assert state["instances"]["count"] == 1
        assert state["security_groups"]["count"] == 1
        assert "create" in state["hint"].lower()
        assert any(tool["name"] == "get_state" for tool in TOOLS)

    def test_prompt_prioritizes_exact_learned_parameters_over_defaults(self):
        prompts = []

        def model(prompt, **kwargs):
            prompts.append(prompt)
            return '{"tool":"finish","args":{"summary":"done"}}'

        AgentLoop(MCPClient(simulate=True), model, max_iterations=1).run_loop(
            "Deploy private API",
            app_type="python",
            memory_block="Use TCP port 8080 from CIDR 10.20.0.0/16.",
        )

        prompt = prompts[0]
        assert "authoritative organizational requirements" in prompt
        assert "follow the learned values first" in prompt
        assert "first open_port action must match those values exactly" in prompt
        assert "never probe an unlisted port or a broader CIDR first" in prompt
        assert "TCP port 8080 from CIDR 10.20.0.0/16" in prompt

    def test_memory_constraints_exclude_negated_network_defaults(self):
        constraints = AgentLoop._memory_constraints(
            "Authorize TCP 8080 from CIDR 10.20.0.0/16 and never expose "
            "ports 80 or 443 from CIDR 0.0.0.0/0."
        )

        assert constraints == {"ports": [8080], "cidrs": ["10.20.0.0/16"]}

    def test_memory_constraints_exclude_prohibited_network_alternatives(self):
        constraints = AgentLoop._memory_constraints(
            "Configure security groups to allow only TCP 8080 from 10.20.0.0/16, "
            "prohibit exposure of ports 22, 80, 443, or 8080 to 0.0.0.0/0."
        )

        assert constraints == {"ports": [8080], "cidrs": ["10.20.0.0/16"]}

    def test_memory_constraints_exclude_blocked_network_alternatives(self):
        constraints = AgentLoop._memory_constraints(
            "Authorize only TCP 8080 from 10.20.0.0/16 and block ports 22, 80, "
            "443, and 8080 from 0.0.0.0/0 before instance creation."
        )

        assert constraints == {"ports": [8080], "cidrs": ["10.20.0.0/16"]}

    def test_conflicting_open_port_is_repaired_before_execution(self):
        actions = iter(
            [
                '{"tool":"open_port","args":{"port":80,"cidr":"0.0.0.0/0"}}',
                '{"tool":"create_instance","args":{"name":"api"}}',
                '{"tool":"deploy","args":{}}',
                '{"tool":"finish","args":{"summary":"healthy"}}',
            ]
        )

        result = AgentLoop(
            MCPClient(simulate=True), lambda prompt, **kwargs: next(actions), max_iterations=4
        ).run_loop(
            "Deploy private API",
            app_type="python",
            memory_block=(
                "Authorize TCP 8080 from CIDR 10.20.0.0/16 and never expose "
                "ports 80 or 443 from CIDR 0.0.0.0/0."
            ),
        )

        assert result["outcome"] == "success"
        assert result["opened_ports"] == [8080]
        assert result["memory_constraints"] == {
            "ports": [8080],
            "cidrs": ["10.20.0.0/16"],
        }
        assert result["steps"][0]["result"] == "success"
        assert result["steps"][0]["requested_args"] == {
            "port": 80,
            "cidr": "0.0.0.0/0",
        }
        assert result["steps"][0]["args"] == {
            "port": 8080,
            "cidr": "10.20.0.0/16",
        }
        applied = result["steps"][0]["observation"]["memory_constraint_applied"]
        assert applied["applied_port"] == 8080
        assert applied["applied_cidr"] == "10.20.0.0/16"

    def test_repeated_decision_injects_corrective_hint(self):
        prompts = []
        actions = iter(
            [
                '{"tool":"list_instances","args":{}}',
                '{"tool":"list_instances","args":{}}',
                '{"tool":"finish","args":{"summary":"done"}}',
            ]
        )

        def model(prompt, **kwargs):
            prompts.append(prompt)
            return next(actions)

        AgentLoop(MCPClient(simulate=True), model, max_iterations=3).run_loop(
            "Inspect then deploy", app_type="python", memory_block=""
        )

        assert "repeated action" in prompts[2].lower()
        assert "choose a different" in prompts[2].lower()

    def test_repeated_action_exhausts_the_iteration_budget(self):
        calls = {"count": 0}

        def model(prompt, **kwargs):
            calls["count"] += 1
            return '{"tool":"list_instances","args":{}}'

        result = AgentLoop(
            MCPClient(simulate=True), model, max_iterations=4
        ).run_loop("Inspect then deploy", app_type="python", memory_block="")

        assert calls["count"] == 4
        assert result["outcome"] == "failed"
        assert result["failure_point"] == "max_iterations"
        assert result["iterations_used"] == 4

    def test_unparseable_responses_consume_the_iteration_budget(self):
        calls = {"count": 0}

        def model(prompt, **kwargs):
            calls["count"] += 1
            return "{invalid"

        result = AgentLoop(
            MCPClient(simulate=True), model, max_iterations=3
        ).run_loop("Deploy", app_type="python", memory_block="")

        assert calls["count"] == 3
        assert result["outcome"] == "failed"
        assert result["failure_point"] == "max_iterations"
        assert result["iterations_used"] == 3

    def test_finish_before_healthy_reports_health_check_failure(self):
        def model(prompt, **kwargs):
            return '{"tool":"finish","args":{"summary":"done"}}'

        result = AgentLoop(
            MCPClient(simulate=True), model, max_iterations=3
        ).run_loop("Deploy", app_type="python", memory_block="")

        assert result["outcome"] == "failed"
        assert result["failure_point"] == "health_check"
        assert result["iterations_used"] == 1
        assert "not deployed" in result["verify_reason"]

    def test_inventory_errors_are_explicit(self):
        mcp = Mock()
        mcp.list_ecs_instances.side_effect = MCPClientError("access denied")
        mcp.list_security_groups.side_effect = MCPClientError("access denied")
        sandbox = DeploymentSandbox(mcp, "python")

        instances = sandbox.list_instances()
        groups = sandbox.list_security_groups()

        assert instances["ok"] is False
        assert groups["ok"] is False
        assert "access denied" in instances["error"]
        assert "access denied" in groups["error"]


class TestMCPToolErrorResponses:
    def test_is_error_true_raises(self):
        client = MCPClient(simulate=True)

        with pytest.raises(MCPClientError, match="Access denied") as exc:
            client._parse_tool_response(
                {
                    "isError": True,
                    "content": [{"type": "text", "text": "Access denied"}],
                }
            )

        assert exc.value.retryable is False

    def test_is_error_false_parses_normally(self):
        client = MCPClient(simulate=True)

        parsed = client._parse_tool_response(
            {
                "isError": False,
                "content": [
                    {"type": "text", "text": '{"TotalCount": 0, "Instances": {"Instance": []}}'}
                ],
            }
        )

        assert parsed["TotalCount"] == 0
        assert parsed["Instances"]["Instance"] == []
