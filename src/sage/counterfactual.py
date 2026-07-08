"""
CounterfactualRunner — Ablation evaluation: same task WITH and WITHOUT memory.

A deep module: one method (run), hides loop construction, paired execution,
divergence detection, and confidence loop closure.
"""

import logging
from typing import Optional, Callable

from .agent_loop import AgentLoop
from .tools.mcp_client import MCPClient

logger = logging.getLogger(__name__)


class CounterfactualRunner:
    """Runs paired with/without-memory executions to measure memory impact."""

    def __init__(
        self,
        model_caller_fn: Callable,
        simulate: bool = True,
    ):
        self.model_caller_fn = model_caller_fn
        self.simulate = simulate

    def run(
        self,
        task: str,
        app_type: str,
        memory_block: str,
        evaluator=None,
        procedural=None,
    ) -> dict:
        """Run the same task WITH and WITHOUT memory, compare outcomes.

        Args:
            task: The deployment task description.
            app_type: Inferred application type.
            memory_block: The compiled memory block string.
            evaluator: Optional Evaluator for recording the comparison.
            procedural: Optional ProceduralMemory for confidence loop closure.

        Returns:
            dict with: with_memory, without_memory, memory_helped, memory_hurt,
            first_divergence, with_memory_block, without_memory_block, record.
        """
        # WITH memory: inject learned rules
        with_memory = AgentLoop(
            MCPClient(simulate=self.simulate), self.model_caller_fn
        ).run_loop(task, app_type=app_type, memory_block=memory_block)

        # WITHOUT memory: identical model + tools, no rules in context
        without_memory = AgentLoop(
            MCPClient(simulate=self.simulate), self.model_caller_fn
        ).run_loop(task, app_type=app_type, memory_block="")

        memory_helped = (
            with_memory["outcome"] == "success"
            and without_memory["outcome"] != "success"
        )
        memory_hurt = (
            with_memory["outcome"] != "success"
            and without_memory["outcome"] == "success"
        )

        # Record via evaluator if provided
        record = {}
        rules_applied = []
        if procedural:
            rules_applied = [
                r.get("id") for r in procedural.get_all_rules() if r.get("id")
            ]
        if evaluator:
            record = evaluator.record_counterfactual(
                task, with_memory["outcome"], without_memory["outcome"], rules_applied
            )

        # Close the confidence loop
        if procedural and rules_applied:
            for rule_id in rules_applied:
                if memory_helped:
                    procedural.boost_confidence(rule_id, delta=0.1)
                elif memory_hurt:
                    procedural.boost_confidence(rule_id, delta=-0.15)

        return {
            "task": task,
            "with_memory": with_memory,
            "without_memory": without_memory,
            "with_memory_block": memory_block,
            "without_memory_block": "",
            "record": record,
            "memory_helped": memory_helped,
            "memory_hurt": memory_hurt,
            "first_divergence": self._first_divergence(with_memory, without_memory),
        }

    @staticmethod
    def _step_decision_key(step: Optional[dict]) -> Optional[tuple]:
        """Project a telemetry-rich step down to the model's decision."""
        if step is None:
            return None
        return step.get("tool"), step.get("args") or {}

    @staticmethod
    def _first_divergence(with_memory: dict, without_memory: dict) -> Optional[dict]:
        """Return the first decision difference between paired runs."""
        with_steps = with_memory.get("steps", [])
        without_steps = without_memory.get("steps", [])
        max_len = max(len(with_steps), len(without_steps))
        for idx in range(max_len):
            left = with_steps[idx] if idx < len(with_steps) else None
            right = without_steps[idx] if idx < len(without_steps) else None
            if CounterfactualRunner._step_decision_key(
                left
            ) == CounterfactualRunner._step_decision_key(right):
                continue
            return {
                "step_index": idx + 1,
                "reason": (
                    "step_missing" if left is None or right is None else "decision_changed"
                ),
                "with_memory_tool": None if left is None else left.get("tool"),
                "with_memory_args": None if left is None else left.get("args"),
                "without_memory_tool": None if right is None else right.get("tool"),
                "without_memory_args": None if right is None else right.get("args"),
            }
        return None
