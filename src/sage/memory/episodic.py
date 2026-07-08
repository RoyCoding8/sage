"""
Episodic Memory — JSONL log of past interactions.

Each interaction is logged as a JSON line with:
- timestamp, task, attempt, outcome, error, correction, rule_extracted, rule_id

Error handling:
- All file I/O is wrapped in try/except to prevent crashes from
  permission errors, corrupt JSONL lines, or disk full conditions.
- Corrupt lines are silently skipped during reads.
- Log failures are reported via logging but do not raise.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sage.persistence import append_jsonl, atomic_write_text

logger = logging.getLogger(__name__)


class EpisodicMemory:
    def __init__(self, memory_dir: str = "memory/episodic"):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.current_file = self.memory_dir / "interactions.jsonl"

    def log(
        self,
        task: str,
        attempt: int,
        outcome: str,
        error: Optional[str] = None,
        correction: Optional[str] = None,
        rule_extracted: Optional[str] = None,
        rule_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Log an interaction to episodic memory.

        Returns the entry dict. On I/O failure, logs a warning and returns
        the entry anyway (in-memory) so the caller is never broken.
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task": task,
            "attempt": attempt,
            "outcome": outcome,  # "success" or "failed"
            "error": error,
            "correction": correction,
            "rule_extracted": rule_extracted,
            "rule_id": rule_id,
            "metadata": metadata or {},
        }

        try:
            append_jsonl(self.current_file, entry)
        except (OSError, IOError) as e:
            logger.warning("Failed to write to episodic memory: %s", e)

        return entry

    def _read_entries(self, predicate=None):
        """Read all JSONL entries, optionally filtering by predicate."""
        if not self.current_file.exists():
            return []
        entries = []
        try:
            with open(self.current_file, "r") as f:
                for line in f:
                    if not (line := line.strip()):
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug(
                            "Skipping corrupt JSONL line in %s", self.current_file
                        )
                        continue
                    if predicate is None or predicate(entry):
                        entries.append(entry)
        except (OSError, IOError) as e:
            logger.warning("Failed to read episodic memory: %s", e)
        return entries

    def get_recent(self, n: int = 10) -> list[dict]:
        """Get the N most recent interactions."""
        return self._read_entries()[-n:]

    def get_by_task(self, task_pattern: str) -> list[dict]:
        """Get interactions matching a task pattern (substring match)."""
        pattern = task_pattern.lower()
        return self._read_entries(lambda e: pattern in e.get("task", "").lower())

    def get_corrections(self) -> list[dict]:
        """Get all interactions that involved a correction."""
        return self._read_entries(lambda e: bool(e.get("correction")))

    def get_stats(self) -> dict:
        """Get summary statistics of episodic memory."""
        default = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "corrections": 0,
            "success_rate": 0.0,
        }
        entries = self._read_entries()
        if not entries:
            return default
        total = len(entries)
        success = sum(1 for e in entries if e.get("outcome") == "success")
        corrections = sum(1 for e in entries if e.get("correction"))
        return {
            "total": total,
            "success": success,
            "failed": total - success,
            "corrections": corrections,
            "success_rate": success / total,
        }

    def clear(self):
        """Clear all episodic memory (for testing)."""
        if self.current_file.exists():
            self.current_file.unlink()

    def consolidate(self, threshold: int = 50) -> Optional[str]:
        """Consolidate old episodes into a compressed summary and archive raw entries.

        When entries exceed *threshold*, the oldest entries are summarized into
        a one-paragraph lesson and moved to an archive file. This keeps the
        active JSONL lean for prompt injection while preserving history.
        """
        entries = self._read_entries()
        if len(entries) <= threshold:
            return None

        # Split: keep recent, archive old
        keep = entries[-threshold:]
        archive = entries[:-threshold]

        # Build summary from archived entries
        corrections = [e for e in archive if e.get("correction")]
        successes = sum(1 for e in archive if e.get("outcome") == "success")
        summary = (
            f"Consolidated {len(archive)} episodes: "
            f"{successes} successes, {len(archive) - successes} failures, "
            f"{len(corrections)} corrections. "
            f"Key lessons: {'; '.join(c.get('correction', '')[:60] for c in corrections[:5])}"
        )

        # Write archive
        archive_path = self.memory_dir / "archive.jsonl"
        try:
            for entry in archive:
                append_jsonl(archive_path, entry)
        except (OSError, IOError) as e:
            logger.warning("Failed to archive episodes: %s", e)

        # Rewrite current file with only recent entries
        try:
            content = "".join(f"{json.dumps(entry)}\n" for entry in keep)
            atomic_write_text(self.current_file, content)
        except (OSError, IOError) as e:
            logger.warning("Failed to rewrite episodic memory: %s", e)

        return summary


# Convenience
if __name__ == "__main__":
    em = EpisodicMemory()
    em.log("Deploy web app", 1, "failed", error="Security group missing")
    em.log("Deploy web app", 2, "success")
    print(json.dumps(em.get_recent(2), indent=2))
    print(json.dumps(em.get_stats(), indent=2))
