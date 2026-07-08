"""
Evaluator — Tracks improvement over time.

Records success/failure rates, rule application,
and generates improvement charts for the demo.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sage.persistence import append_jsonl, atomic_write_text

logger = logging.getLogger(__name__)


class Evaluator:
    """
    Tracks agent performance over time.

    For the demo video, we need to show:
    1. Initial failure rate
    2. Rules accumulated
    3. Improvement curve
    """

    def __init__(self, project_dir: str = "."):
        self.project_dir = Path(project_dir)
        self.metrics_path = self.project_dir / "metrics" / "eval_history.jsonl"
        self.counterfactual_path = (
            self.project_dir / "metrics" / "counterfactuals.jsonl"
        )
        self.metrics_path.parent.mkdir(parents=True, exist_ok=True)

    def record_task(
        self,
        task: str,
        outcome: str,
        rules_applied: list[str],
        correction: Optional[str] = None,
        rule_extracted: Optional[str] = None,
    ):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task": task,
            "outcome": outcome,
            "rules_applied": rules_applied,
            "correction": correction,
            "rule_extracted": rule_extracted,
            "had_correction": correction is not None,
        }
        append_jsonl(self.metrics_path, entry)

    def annotate_latest_failure(
        self, task: str, correction: str, rule_extracted: str
    ) -> bool:
        """Attach correction metadata to the latest failed record for a task."""
        history = self.get_history()
        for entry in reversed(history):
            if entry.get("task") == task and entry.get("outcome") == "failed":
                entry["correction"] = correction
                entry["rule_extracted"] = rule_extracted
                entry["had_correction"] = True
                content = "".join(f"{json.dumps(item)}\n" for item in history)
                atomic_write_text(self.metrics_path, content)
                return True
        return False

    def record_counterfactual(
        self, task: str, with_memory: str, without_memory: str, rules_applied: list[str]
    ):
        """Record paired with-memory vs memory-disabled outcomes."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task": task,
            "with_memory": with_memory,
            "without_memory": without_memory,
            "rules_applied": rules_applied,
            "memory_helped": with_memory == "success" and without_memory != "success",
        }
        append_jsonl(self.counterfactual_path, entry)
        return entry

    def get_history(self) -> list[dict]:
        return self._read_jsonl(self.metrics_path)

    def get_counterfactuals(self) -> list[dict]:
        return self._read_jsonl(self.counterfactual_path)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        """Read a JSONL file, skipping malformed lines."""
        if not path.exists():
            return []
        entries = []
        try:
            with open(path) as f:
                for line in f:
                    if line := line.strip():
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            logger.warning("Skipping malformed JSONL line in %s", path)
        except (OSError, IOError) as e:
            logger.warning("Failed to read %s: %s", path, e)
        return entries

    def get_counterfactual_summary(self) -> dict:
        entries = self.get_counterfactuals()
        if not entries:
            return {"total": 0, "memory_wins": 0, "win_rate": 0.0}
        wins = sum(1 for e in entries if e.get("memory_helped"))
        regressions = sum(
            1
            for e in entries
            if e.get("with_memory") != "success"
            and e.get("without_memory") == "success"
        )
        return {
            "total": len(entries),
            "memory_wins": wins,
            "memory_regressions": regressions,
            "win_rate": wins / len(entries),
        }

    def get_improvement_summary(self) -> dict:
        """Generate improvement summary for the demo."""
        history = self.get_history()
        if not history:
            return {"total_tasks": 0, "message": "No tasks recorded yet"}

        total = len(history)
        midpoint = total // 2

        # Single-pass aggregation instead of 4 separate list comprehensions
        successes = failures = corrections = rules_extracted = 0
        unique_rules = set()
        first_half_successes = second_half_successes = 0

        for i, h in enumerate(history):
            if h["outcome"] == "success":
                successes += 1
                if i < midpoint:
                    first_half_successes += 1
                else:
                    second_half_successes += 1
            else:
                failures += 1
            if h["had_correction"]:
                corrections += 1
            if h["rule_extracted"]:
                rules_extracted += 1
            unique_rules.update(h["rules_applied"])

        first_half_count = max(midpoint, 1)
        second_half_count = max(total - midpoint, 1)
        first_half_rate = first_half_successes / first_half_count
        second_half_rate = second_half_successes / second_half_count

        return {
            "total_tasks": total,
            "successes": successes,
            "failures": failures,
            "success_rate": successes / total,
            "corrections_received": corrections,
            "rules_extracted": rules_extracted,
            "unique_rules_applied": list(unique_rules),
            "first_half_success_rate": first_half_rate,
            "second_half_success_rate": second_half_rate,
            "improvement_rate": second_half_rate - first_half_rate
            if midpoint > 0
            else 0,
            "timeline": [
                {
                    "task": h["task"],
                    "outcome": h["outcome"],
                    "timestamp": h["timestamp"],
                }
                for h in history
            ],
        }

    def format_demo_stats(
        self, rules_learned: Optional[int] = None, rules_applied: Optional[int] = None
    ) -> str:
        """Format stats for the demo video."""
        summary = self.get_improvement_summary()

        if summary.get("total_tasks", 0) == 0:
            return "No tasks completed yet."

        lines = [
            "=== Sage Performance Report ===",
            "=" * 40,
            f"Total tasks: {summary['total_tasks']}",
            f"Successes: {summary['successes']}",
            f"Failures: {summary['failures']}",
            f"Overall success rate: {summary['success_rate']:.0%}",
            "",
            "Improvement Over Time:",
            f"  First half success rate: {summary['first_half_success_rate']:.0%}",
            f"  Second half success rate: {summary['second_half_success_rate']:.0%}",
            f"  Improvement: {summary['improvement_rate']:+.0%}",
            "",
            f"Rules learned: {rules_learned if rules_learned is not None else summary['rules_extracted']}",
            f"Rules applied: {rules_applied if rules_applied is not None else len(summary['unique_rules_applied'])}",
        ]
        if counterfactuals := self.get_counterfactual_summary():
            if counterfactuals["total"]:
                lines += [
                    "",
                    "Counterfactual Memory Eval:",
                    f"  Paired trials: {counterfactuals['total']}",
                    f"  Memory wins: {counterfactuals['memory_wins']}",
                    f"  Win rate: {counterfactuals['win_rate']:.0%}",
                ]

        return "\n".join(lines)

    def clear(self):
        """Clear all history (for testing)."""
        if self.metrics_path.exists():
            self.metrics_path.unlink()
        if self.counterfactual_path.exists():
            self.counterfactual_path.unlink()


if __name__ == "__main__":
    ev = Evaluator("/tmp/test_eval")

    # Simulate a learning curve
    ev.record_task(
        "Deploy web app", "failed", [], correction="Configure security group first"
    )
    ev.record_task("Deploy web app", "failed", [], correction="Install Node.js first")
    ev.record_task("Deploy API", "success", ["R001"])
    ev.record_task("Deploy web app", "success", ["R001", "R002"])
    ev.record_task("Deploy container", "success", ["R001", "R003"])

    print(ev.format_demo_stats())
