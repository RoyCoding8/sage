"""
MetricsRecorder — Owns agent performance metrics and their persistence.

A deep module: simple interface (record_outcome, get_metrics), hides
file I/O, backup logic, success rate history, and corruption recovery.
"""

import copy
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from sage.persistence import AtomicJsonDocument, path_transaction

logger = logging.getLogger(__name__)


class MetricsRecorder:
    """Records and persists agent performance metrics."""

    def __init__(self, metrics_path: Path | str):
        self.metrics_path = (
            Path(metrics_path) if isinstance(metrics_path, str) else metrics_path
        )
        self._state_lock = threading.RLock()
        self._document = AtomicJsonDocument(self.metrics_path, self._default_metrics)
        self.metrics = self._load()
        self._baseline = copy.deepcopy(self.metrics)

    def record_outcome(self, success: bool, rule_count: int = 0):
        """Record the outcome of a task Run."""
        def record(metrics: dict) -> None:
            metrics["total_tasks"] += 1
            if success:
                metrics["successes"] += 1
            else:
                metrics["failures"] += 1
            metrics["rules_learned"] = rule_count
            self._record_success_rate(metrics)

        self._transaction(record)

    def record_correction(self, rule_count: int = 0):
        """Record that a Correction was processed."""
        def record(metrics: dict) -> None:
            metrics["corrections"] += 1
            metrics["corrected_failures"] += 1
            metrics["rules_learned"] = rule_count
            self._record_success_rate(metrics)

        self._transaction(record)

    def record_success_external(self, rule_count: int = 0):
        """Record a success triggered externally (e.g., by UI)."""
        def record(metrics: dict) -> None:
            metrics["successes"] += 1
            metrics["rules_learned"] = rule_count
            self._record_success_rate(metrics)

        self._transaction(record)

    def record_rule_count(self, rule_count: int) -> None:
        """Persist the current Rule count without replacing concurrent metrics."""
        self._transaction(lambda metrics: metrics.__setitem__("rules_learned", rule_count))

    def get_metrics(self) -> dict:
        """Return a copy of the current metrics."""
        with self._state_lock:
            self._sync_local(self._load())
            return copy.deepcopy(self.metrics)

    @property
    def total_tasks(self) -> int:
        return self.get_metrics()["total_tasks"]

    @property
    def successes(self) -> int:
        return self.get_metrics()["successes"]

    @property
    def failures(self) -> int:
        return self.get_metrics()["failures"]

    @property
    def corrections(self) -> int:
        return self.get_metrics()["corrections"]

    @property
    def corrected_failures(self) -> int:
        return self.get_metrics()["corrected_failures"]

    # ─── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _default_metrics() -> dict:
        return {
            "total_tasks": 0,
            "successes": 0,
            "failures": 0,
            "corrected_failures": 0,
            "corrections": 0,
            "rules_learned": 0,
            "success_rate_history": [],
            "start_time": datetime.now(timezone.utc).isoformat(),
        }

    def _load(self) -> dict:
        def load_or_recover() -> dict:
            try:
                return {**self._default_metrics(), **self._document.read()}
            except (ValueError, OSError) as e:
                logger.warning("Corrupt metrics.json, resetting: %s", e)
                try:
                    backup = self.metrics_path.with_suffix(".json.bak")
                    self.metrics_path.replace(backup)
                    logger.info("Backed up corrupt metrics to %s", backup)
                except OSError:
                    pass
                return self._default_metrics()

        return path_transaction(self.metrics_path, load_or_recover)

    def _transaction(self, mutate):
        with self._state_lock:
            local_overrides = {
                key: copy.deepcopy(value)
                for key, value in self.metrics.items()
                if key not in self._baseline or value != self._baseline[key]
            }

            def update(metrics: dict):
                normalized = {**self._default_metrics(), **metrics}
                metrics.clear()
                metrics.update(normalized)
                metrics.update(local_overrides)
                result = mutate(metrics)
                return result, copy.deepcopy(metrics)

            result, committed = self._document.update(update)
            self._sync_local(committed)
            return result

    def _sync_local(self, metrics: dict) -> None:
        self.metrics.clear()
        self.metrics.update(copy.deepcopy(metrics))
        self._baseline = copy.deepcopy(metrics)

    def _save(self):
        """Persist direct legacy edits without replacing concurrent fields."""
        self._transaction(lambda metrics: None)

    @staticmethod
    def _record_success_rate(metrics: dict) -> None:
        total = metrics["total_tasks"]
        if total > 0:
            metrics["success_rate_history"].append(
                {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "rate": metrics["successes"] / total,
                    "total": total,
                    "rules": metrics["rules_learned"],
                }
            )
