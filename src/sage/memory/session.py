"""
Session Model — Tracks session identity and cross-session continuity.

Each invocation of Sage creates a session with:
- Unique session ID (UUID)
- Start/end timestamps
- User ID (configurable)
- Tasks executed, rules learned, corrections received
- Summary of what happened (for next-session recall)

Cross-session continuity:
- On startup, loads the last session summary
- Provides "welcome back" context (what was learned last time)
- Tracks cumulative stats across all sessions
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sage.persistence import append_jsonl, atomic_write_json

logger = logging.getLogger(__name__)


class Session:
    """
    Represents a single agent session with lifecycle tracking.
    """

    def __init__(self, sessions_dir: str = "memory/sessions", user_id: str = "default"):
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.user_id = user_id

        # Current session state
        self.session_id = uuid.uuid4().hex[:12]
        self.start_time = datetime.now(timezone.utc).isoformat()
        self.end_time: Optional[str] = None
        self.tasks: list[dict] = []
        self.corrections: list[dict] = []
        self.rules_learned: list[str] = []

        # Load session history for continuity
        self._history_path = self.sessions_dir / "session_history.jsonl"
        self._current_path = self.sessions_dir / f"session_{self.session_id}.json"

    # ─── Public API ──────────────────────────────────────────────────────────

    def record_task(
        self,
        task: str,
        outcome: str,
        rules_applied: list[str] = None,
        policies_applied: list[str] = None,
    ):
        """Record a task execution in this session."""
        self.tasks.append(
            {
                "task": task,
                "outcome": outcome,
                "rules_applied": rules_applied or [],
                "policies_applied": policies_applied or [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save_current()

    def record_correction(self, task: str, correction: str, rule_id: str):
        """Record a correction event in this session."""
        self.corrections.append(
            {
                "task": task,
                "correction": correction,
                "rule_id": rule_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.rules_learned.append(rule_id)
        self._save_current()

    def end(self):
        """End the current session and write a summary to history."""
        self.end_time = datetime.now(timezone.utc).isoformat()
        summary = self._build_summary()
        self._save_current()
        self._append_to_history(summary)
        return summary

    def get_last_session_summary(self) -> Optional[dict]:
        """Get the summary from the most recent prior session.

        Used on startup to provide cross-session continuity context.
        """
        history = self._read_history()
        if not history:
            return None
        return history[-1]

    def get_continuity_context(self) -> str:
        """Build a natural-language context string about prior sessions.

        For injection into the system prompt to provide continuity.
        """
        last = self.get_last_session_summary()
        if not last:
            return ""

        lines = ["Cross-session context (from your last session):"]
        lines.append(f"- Last session: {last.get('start_time', 'unknown')}")
        lines.append(f"- Tasks completed: {last.get('tasks_completed', 0)}")
        lines.append(f"- Success rate: {last.get('success_rate', 'unknown')}")

        if last.get("rules_learned"):
            lines.append(f"- Rules learned: {', '.join(last['rules_learned'])}")
        if last.get("corrections"):
            lines.append(f"- Corrections received: {len(last['corrections'])}")
        if last.get("summary_text"):
            lines.append(f"- Summary: {last['summary_text']}")

        return "\n".join(lines)

    def get_session_stats(self) -> dict:
        """Get current session statistics."""
        successes = sum(1 for t in self.tasks if t["outcome"] == "success")
        total = len(self.tasks)
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "start_time": self.start_time,
            "tasks_completed": total,
            "successes": successes,
            "failures": total - successes,
            "success_rate": f"{successes}/{total}" if total > 0 else "0/0",
            "corrections": len(self.corrections),
            "rules_learned": self.rules_learned,
        }

    def get_cumulative_stats(self) -> dict:
        """Get stats across all sessions."""
        history = self._read_history()
        total_tasks = sum(h.get("tasks_completed", 0) for h in history)
        total_corrections = sum(len(h.get("corrections", [])) for h in history)
        total_rules = sum(len(h.get("rules_learned", [])) for h in history)
        return {
            "total_sessions": len(history),
            "total_tasks": total_tasks,
            "total_corrections": total_corrections,
            "total_rules_learned": total_rules,
            "user_id": self.user_id,
        }

    def get_history(self) -> list[dict]:
        """Return prior Session summaries in chronological order."""
        return self._read_history()

    # ─── Private ─────────────────────────────────────────────────────────────

    def _build_summary(self) -> dict:
        """Build a summary of this session for history."""
        successes = sum(1 for t in self.tasks if t["outcome"] == "success")
        total = len(self.tasks)

        # Generate a one-line summary
        if self.corrections:
            corrections_text = "; ".join(
                c["correction"][:60] for c in self.corrections[:3]
            )
            summary_text = f"Learned from {len(self.corrections)} correction(s): {corrections_text}"
        elif total > 0:
            summary_text = f"Completed {successes}/{total} tasks successfully."
        else:
            summary_text = "No tasks executed."

        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "tasks_completed": total,
            "successes": successes,
            "success_rate": f"{successes}/{total}" if total > 0 else "0/0",
            "corrections": self.corrections,
            "rules_learned": self.rules_learned,
            "summary_text": summary_text,
        }

    def _save_current(self):
        """Save current session state to disk."""
        try:
            data = {
                "session_id": self.session_id,
                "user_id": self.user_id,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "tasks": self.tasks,
                "corrections": self.corrections,
                "rules_learned": self.rules_learned,
            }
            atomic_write_json(self._current_path, data)
        except (OSError, IOError) as e:
            logger.warning("Failed to save session: %s", e)

    def _append_to_history(self, summary: dict):
        """Append session summary to history file."""
        try:
            append_jsonl(self._history_path, summary)
        except (OSError, IOError) as e:
            logger.warning("Failed to append session history: %s", e)

    def _read_history(self) -> list[dict]:
        """Read all session summaries from history."""
        if not self._history_path.exists():
            return []
        entries = []
        try:
            with open(self._history_path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except (OSError, IOError) as e:
            logger.warning("Failed to read session history: %s", e)
        return entries

    def clear_history(self):
        """Clear all session history (for testing)."""
        if self._history_path.exists():
            self._history_path.unlink()
        if self._current_path.exists():
            self._current_path.unlink()


if __name__ == "__main__":
    session = Session("/tmp/test_sessions")
    session.record_task("Deploy web app", "failed")
    session.record_correction("Deploy web app", "Configure SG first", "R001")
    session.record_task("Deploy Flask API", "success", rules_applied=["R001"])

    print(f"Session stats: {json.dumps(session.get_session_stats(), indent=2)}")
    summary = session.end()
    print(f"\nSession summary: {json.dumps(summary, indent=2)}")

    # Simulate next session
    session2 = Session("/tmp/test_sessions")
    print(f"\nContinuity context:\n{session2.get_continuity_context()}")
