"""
Tests for Sage Evaluator — tracks improvement over time.

Also covers Agent helper methods (_infer_app_type, _track_rule_application,
_get_memory_state).
"""

import json
import pytest

from sage.evaluator import Evaluator


# ─── Evaluator: core record/read cycle ───────────────────────────────────────


class TestEvaluatorRecord:
    def test_record_task_creates_file(self, tmp_path):
        """First record_task creates the JSONL metrics file."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("Deploy app", "success", ["R001"])
        assert ev.metrics_path.exists()

    def test_record_task_writes_jsonl(self, tmp_path):
        """Each record_task appends a JSON line."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("Task A", "success", ["R001"])
        ev.record_task("Task B", "failed", [], correction="Fix SG")
        lines = ev.metrics_path.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            data = json.loads(line)
            assert "timestamp" in data
            assert "task" in data
            assert "outcome" in data

    def test_record_task_all_fields(self, tmp_path):
        """Optional fields are stored when provided."""
        ev = Evaluator(str(tmp_path))
        ev.record_task(
            "Deploy ECS",
            "failed",
            ["R001"],
            correction="Configure security group",
            rule_extracted="Always check SG first",
        )
        entry = ev.get_history()[0]
        assert entry["correction"] == "Configure security group"
        assert entry["rule_extracted"] == "Always check SG first"
        assert entry["had_correction"] is True

    def test_record_task_without_optional_fields(self, tmp_path):
        """Optional fields default to None when omitted."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("Deploy", "success", ["R001"])
        entry = ev.get_history()[0]
        assert entry["correction"] is None
        assert entry["rule_extracted"] is None
        assert entry["had_correction"] is False

    def test_record_counterfactual_marks_memory_win(self, tmp_path):
        """Counterfactual eval stores paired memory-disabled results."""
        ev = Evaluator(str(tmp_path))
        entry = ev.record_counterfactual("Deploy app", "success", "failed", ["R001"])
        assert entry["memory_helped"] is True
        assert ev.get_counterfactual_summary()["win_rate"] == 1.0


# ─── Evaluator: get_history ──────────────────────────────────────────────────


class TestEvaluatorHistory:
    def test_get_history_empty(self, tmp_path):
        """No records returns empty list."""
        ev = Evaluator(str(tmp_path))
        assert ev.get_history() == []

    def test_get_history_preserves_order(self, tmp_path):
        """History is returned in chronological order."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("First", "success", [])
        ev.record_task("Second", "failed", [])
        ev.record_task("Third", "success", [])
        history = ev.get_history()
        assert [h["task"] for h in history] == ["First", "Second", "Third"]

    def test_get_history_after_clear(self, tmp_path):
        """Clear removes all history."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("Task", "success", [])
        ev.clear()
        assert ev.get_history() == []

    def test_get_counterfactuals_empty(self, tmp_path):
        """No counterfactual records returns empty list."""
        ev = Evaluator(str(tmp_path))
        assert ev.get_counterfactuals() == []
        assert ev.get_counterfactual_summary()["total"] == 0


# ─── Evaluator: get_improvement_summary ──────────────────────────────────────


class TestEvaluatorSummary:
    def test_summary_empty(self, tmp_path):
        """Empty history returns zero summary."""
        ev = Evaluator(str(tmp_path))
        summary = ev.get_improvement_summary()
        assert summary["total_tasks"] == 0
        assert "No tasks" in summary["message"]

    def test_summary_single_task(self, tmp_path):
        """Single task — no improvement calculated."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("Deploy", "success", ["R001"])
        summary = ev.get_improvement_summary()
        assert summary["total_tasks"] == 1
        assert summary["successes"] == 1
        assert summary["failures"] == 0
        assert summary["success_rate"] == 1.0
        # Midpoint = 0, so no improvement data
        assert summary["improvement_rate"] == 0

    def test_summary_all_successes(self, tmp_path):
        """All successes yields 100% rate."""
        ev = Evaluator(str(tmp_path))
        for i in range(4):
            ev.record_task(f"Task {i}", "success", ["R001"])
        summary = ev.get_improvement_summary()
        assert summary["total_tasks"] == 4
        assert summary["success_rate"] == 1.0
        assert summary["corrections_received"] == 0

    def test_summary_all_failures(self, tmp_path):
        """All failures yields 0% rate."""
        ev = Evaluator(str(tmp_path))
        for i in range(4):
            ev.record_task(f"Task {i}", "failed", [])
        summary = ev.get_improvement_summary()
        assert summary["success_rate"] == 0.0

    def test_summary_improvement_calculation(self, tmp_path):
        """Second half better than first half shows positive improvement."""
        ev = Evaluator(str(tmp_path))
        # First 3 tasks: all fail
        for i in range(3):
            ev.record_task(f"Fail {i}", "failed", [])
        # Last 3 tasks: all succeed
        for i in range(3):
            ev.record_task(f"Win {i}", "success", ["R001"])
        summary = ev.get_improvement_summary()
        assert summary["first_half_success_rate"] == 0.0
        assert summary["second_half_success_rate"] == 1.0
        assert summary["improvement_rate"] == 1.0

    def test_summary_rules_applied(self, tmp_path):
        """Unique rules_applied are collected across all tasks."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("Task 1", "success", ["R001", "R002"])
        ev.record_task("Task 2", "success", ["R001", "R003"])
        summary = ev.get_improvement_summary()
        assert set(summary["unique_rules_applied"]) == {"R001", "R002", "R003"}

    def test_summary_corrections_counted(self, tmp_path):
        """Corrections are counted correctly."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("A", "failed", [], correction="fix 1")
        ev.record_task("B", "success", [])
        ev.record_task("C", "failed", [], correction="fix 2")
        summary = ev.get_improvement_summary()
        assert summary["corrections_received"] == 2

    def test_summary_rules_extracted_counted(self, tmp_path):
        """Rules extracted count is correct."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("A", "failed", [], rule_extracted="rule 1")
        ev.record_task("B", "success", [])
        ev.record_task("C", "failed", [], rule_extracted="rule 2")
        summary = ev.get_improvement_summary()
        assert summary["rules_extracted"] == 2

    def test_summary_timeline(self, tmp_path):
        """Timeline contains task + outcome + timestamp for each entry."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("Task A", "success", [])
        ev.record_task("Task B", "failed", [])
        summary = ev.get_improvement_summary()
        timeline = summary["timeline"]
        assert len(timeline) == 2
        assert timeline[0]["task"] == "Task A"
        assert timeline[0]["outcome"] == "success"
        assert "timestamp" in timeline[0]


# ─── Evaluator: format_demo_stats ────────────────────────────────────────────


class TestEvaluatorFormat:
    def test_format_empty(self, tmp_path):
        """Empty history returns placeholder message."""
        ev = Evaluator(str(tmp_path))
        text = ev.format_demo_stats()
        assert "No tasks" in text

    def test_format_with_tasks(self, tmp_path):
        """Formatted output includes all key stats."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("A", "success", ["R001"])
        ev.record_task("B", "failed", [], correction="Fix")
        ev.record_task("C", "success", ["R001", "R002"])
        text = ev.format_demo_stats()
        assert "Total tasks: 3" in text
        assert "Successes: 2" in text
        assert "Failures: 1" in text
        assert "success rate" in text.lower()

    def test_format_includes_counterfactual_stats(self, tmp_path):
        """Formatted stats include paired memory eval when present."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("A", "success", ["R001"])
        ev.record_counterfactual("A", "success", "failed", ["R001"])
        assert "Counterfactual Memory Eval" in ev.format_demo_stats()

    def test_format_contains_emoji_header(self, tmp_path):
        """Formatted output includes the performance report header."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("A", "success", [])
        text = ev.format_demo_stats()
        assert "Sage Performance Report" in text


# ─── Evaluator: edge cases ───────────────────────────────────────────────────


class TestEvaluatorEdgeCases:
    def test_odd_number_of_tasks_improvement(self, tmp_path):
        """Odd task count: midpoint is total//2, halves may overlap."""
        ev = Evaluator(str(tmp_path))
        for i in range(5):
            ev.record_task(f"Task {i}", "success" if i >= 3 else "failed", [])
        summary = ev.get_improvement_summary()
        # midpoint = 2, first_half = [fail, fail], second_half = [fail, win, win]
        assert summary["first_half_success_rate"] == 0.0
        assert summary["second_half_success_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_malformed_jsonl_skipped(self, tmp_path):
        """Corrupted lines are skipped in get_history."""
        ev = Evaluator(str(tmp_path))
        ev.metrics_path.write_text(
            '{"task":"ok","line":1}\nBAD\n{"task":"ok2","line":2}\n'
        )
        history = ev.get_history()
        assert len(history) == 2

    def test_clear_and_rerecord(self, tmp_path):
        """After clear, new records work correctly."""
        ev = Evaluator(str(tmp_path))
        ev.record_task("Old", "success", [])
        ev.clear()
        ev.record_task("New", "failed", [])
        history = ev.get_history()
        assert len(history) == 1
        assert history[0]["task"] == "New"
