"""
Benchmark — Built-in evaluation scenarios for Sage.

Provides a set of deterministic deployment scenarios that exercise
the learning loop and measure whether memory improves behavior.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Scenario:
    """A single benchmark scenario."""

    id: str
    name: str
    task: str
    app_type: str
    requires_rule: Optional[str]  # rule text that must exist for success
    expected_port: int
    description: str


# Built-in scenarios covering the main learning patterns
SCENARIOS = [
    Scenario(
        id="S01",
        name="Node.js without learned port",
        task="Deploy Node.js web app to Alibaba Cloud ECS",
        app_type="node",
        requires_rule=None,
        expected_port=8080,
        description="Should fail because the model does not know port 8080 is required.",
    ),
    Scenario(
        id="S02",
        name="Node.js with learned port rule",
        task="Deploy Node.js API service to Alibaba Cloud ECS",
        app_type="node",
        requires_rule="8080",
        expected_port=8080,
        description="Should succeed if the port-8080 rule has been learned.",
    ),
    Scenario(
        id="S03",
        name="Python cross-task transfer",
        task="Deploy Python Flask API to Alibaba Cloud ECS",
        app_type="python",
        requires_rule="8080",
        expected_port=8080,
        description="Tests whether a Node.js-learned rule transfers to Python.",
    ),
    Scenario(
        id="S04",
        name="Docker (memory should not matter)",
        task="Deploy Docker container to Alibaba Cloud ECS",
        app_type="docker",
        requires_rule=None,
        expected_port=80,
        description="Docker binds to 80, so no learned rule is needed. Should always pass.",
    ),
    Scenario(
        id="S05",
        name="Java cross-task transfer",
        task="Deploy Java Spring service to Alibaba Cloud ECS",
        app_type="java",
        requires_rule="8080",
        expected_port=8080,
        description="Tests whether the port rule transfers to Java apps.",
    ),
    Scenario(
        id="S06",
        name="Static site (no memory needed)",
        task="Deploy static HTML site to Alibaba Cloud ECS",
        app_type="static",
        requires_rule=None,
        expected_port=80,
        description="Static sites serve on 80. Should pass without memory.",
    ),
]


@dataclass
class ScenarioResult:
    scenario: Scenario
    outcome: str
    expected_outcome: str
    passed: bool
    opened_ports: list[int]
    memory_present: bool
    iterations_used: int


def expected_outcome(scenario: Scenario, has_rule: bool) -> str:
    """Predict expected outcome for a scenario given current memory state."""
    if scenario.requires_rule is None:
        return "success"  # Should pass without memory
    return "success" if has_rule else "failed"


def run_benchmark(
    agent, scenarios: list[Scenario] | None = None
) -> list[ScenarioResult]:
    """Run a set of benchmark scenarios against an agent.

    Args:
        agent: A Sage Agent instance
        scenarios: Optional list of scenarios (defaults to SCENARIOS)

    Returns:
        List of ScenarioResult with pass/fail for each
    """
    if scenarios is None:
        scenarios = SCENARIOS

    results = []
    memory = agent.memory.snapshot(include={"procedural"})
    rules = memory["procedural"]["rules"]
    rule_texts = " ".join(r.get("text", "") for r in rules).lower()

    for scenario in scenarios:
        has_rule = (
            scenario.requires_rule is not None
            and scenario.requires_rule.lower() in rule_texts
        )
        expected = expected_outcome(scenario, has_rule)

        exec_result = agent.run.execute(scenario.task)
        actual = exec_result.get("outcome", "failed")

        results.append(
            ScenarioResult(
                scenario=scenario,
                outcome=actual,
                expected_outcome=expected,
                passed=(actual == expected),
                opened_ports=exec_result.get("opened_ports", []),
                memory_present=has_rule,
                iterations_used=exec_result.get("iterations_used", 0),
            )
        )

    return results


def format_benchmark_summary(results: list[ScenarioResult]) -> dict:
    """Summarize benchmark results."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    memory_helped = sum(
        1
        for r in results
        if r.memory_present and r.outcome == "success" and r.scenario.requires_rule
    )
    regressions = sum(
        1
        for r in results
        if r.memory_present
        and r.outcome == "failed"
        and r.expected_outcome == "success"
    )

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": passed / total if total else 0.0,
        "memory_helped": memory_helped,
        "regressions": regressions,
        "details": [
            {
                "id": r.scenario.id,
                "name": r.scenario.name,
                "outcome": r.outcome,
                "expected": r.expected_outcome,
                "passed": r.passed,
                "memory_present": r.memory_present,
                "opened_ports": r.opened_ports,
                "iterations": r.iterations_used,
            }
            for r in results
        ],
    }
