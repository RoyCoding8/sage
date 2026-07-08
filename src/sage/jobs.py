"""Bounded, idempotent background Run execution with cooperative cancellation."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable


class JobManager:
    """Own background Run lifecycle without exposing executor internals."""

    def __init__(self, max_workers: int = 2, max_retained: int = 500):
        if max_workers < 1 or max_retained < 1:
            raise ValueError("job limits must be positive")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="sage-run"
        )
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._max_retained = max_retained

    def submit(
        self,
        idempotency_key: str,
        run: Callable[[threading.Event], dict],
        owner: str = "",
    ) -> dict:
        """Submit once for an idempotency key and return a public snapshot."""
        if not idempotency_key:
            raise ValueError("idempotency_key must be non-empty")
        with self._lock:
            idempotency_scope = (owner, idempotency_key)
            existing_id = self._idempotency.get(idempotency_scope)
            if existing_id:
                return self._snapshot(self._jobs[existing_id])
            self._prune_terminal_jobs()
            if len(self._jobs) >= self._max_retained:
                raise RuntimeError("Run queue retention limit reached")

            job_id = uuid.uuid4().hex
            job = {
                "job_id": job_id,
                "status": "queued",
                "result": None,
                "error": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "cancel_event": threading.Event(),
                "future": None,
                "owner": owner,
            }
            self._jobs[job_id] = job
            self._idempotency[idempotency_scope] = job_id
            job["future"] = self._executor.submit(self._run, job, run)
            return self._snapshot(job)

    def get_job(self, job_id: str, owner: str | None = None) -> dict:
        """Return a job snapshot or raise KeyError when it is unknown."""
        with self._lock:
            job = self._jobs[job_id]
            if owner is not None and job["owner"] != owner:
                raise KeyError(job_id)
            return self._snapshot(job)

    def cancel(self, job_id: str, owner: str | None = None) -> dict:
        """Request cooperative cancellation and cancel queued work when possible."""
        with self._lock:
            job = self._jobs[job_id]
            if owner is not None and job["owner"] != owner:
                raise KeyError(job_id)
            job["cancel_event"].set()
            future = job.get("future")
            if future is not None and future.cancel():
                job["status"] = "cancelled"
                job["finished_at"] = datetime.now(timezone.utc).isoformat()
            return self._snapshot(job)

    def close(self) -> None:
        """Cancel outstanding Runs and release worker threads."""
        with self._lock:
            for job in self._jobs.values():
                if job["status"] in {"queued", "running"}:
                    job["cancel_event"].set()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _run(self, job: dict, run: Callable[[threading.Event], dict]) -> None:
        with self._lock:
            if job["cancel_event"].is_set():
                job["status"] = "cancelled"
                job["finished_at"] = datetime.now(timezone.utc).isoformat()
                return
            job["status"] = "running"
        try:
            result = run(job["cancel_event"])
            with self._lock:
                job["result"] = result
                job["status"] = (
                    "cancelled" if job["cancel_event"].is_set() else "succeeded"
                )
        except Exception as exc:
            with self._lock:
                job["error"] = f"{type(exc).__name__}: Run failed"
                job["status"] = (
                    "cancelled" if job["cancel_event"].is_set() else "failed"
                )
        finally:
            with self._lock:
                job["finished_at"] = datetime.now(timezone.utc).isoformat()

    def _prune_terminal_jobs(self) -> None:
        """Evict oldest completed Runs before accepting more retained state."""
        terminal = [
            job
            for job in self._jobs.values()
            if job["status"] in {"succeeded", "failed", "cancelled"}
        ]
        terminal.sort(key=lambda job: job["finished_at"] or job["created_at"])
        while len(self._jobs) >= self._max_retained and terminal:
            job = terminal.pop(0)
            self._jobs.pop(job["job_id"], None)
            for key, job_id in tuple(self._idempotency.items()):
                if job_id == job["job_id"]:
                    self._idempotency.pop(key, None)

    @staticmethod
    def _snapshot(job: dict) -> dict:
        return {
            key: value
            for key, value in job.items()
            if key not in {"cancel_event", "future", "owner"}
        }
