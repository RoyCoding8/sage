"""Behavioral tests for bounded background Runs."""

import time

import pytest

from sage.jobs import JobManager


def wait_for_terminal(manager: JobManager, job_id: str) -> dict:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_idempotency_key_returns_the_original_job():
    """A retried submission cannot duplicate a billable Run."""
    manager = JobManager(max_workers=1)
    try:
        first = manager.submit(
            "session:request-1", lambda _cancel: {"outcome": "success"}
        )
        second = manager.submit(
            "session:request-1", lambda _cancel: {"outcome": "duplicate"}
        )
        assert second["job_id"] == first["job_id"]
        assert wait_for_terminal(manager, first["job_id"])["result"] == {
            "outcome": "success"
        }
    finally:
        manager.close()


def test_cancellation_signal_reaches_running_job():
    """A cancelled Run receives a cooperative stop signal."""
    manager = JobManager(max_workers=1)
    try:
        job = manager.submit(
            "session:request-2",
            lambda cancel: {"cancelled": cancel.wait(timeout=1)},
        )
        deadline = time.monotonic() + 1
        while (
            manager.get_job(job["job_id"])["status"] == "queued"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        manager.cancel(job["job_id"])
        terminal = wait_for_terminal(manager, job["job_id"])
        assert terminal["status"] == "cancelled"
        assert terminal["result"] == {"cancelled": True}
    finally:
        manager.close()


def test_job_failure_does_not_expose_exception_details():
    """Provider or task exceptions cannot leak credentials through job polling."""
    manager = JobManager(max_workers=1)

    def fail(_cancel):
        raise RuntimeError("secret-token-value")

    try:
        job = manager.submit("session:request-3", fail)
        terminal = wait_for_terminal(manager, job["job_id"])
        assert terminal["status"] == "failed"
        assert terminal["error"] == "RuntimeError: Run failed"
        assert "secret-token-value" not in terminal["error"]
    finally:
        manager.close()


def test_job_owner_is_enforced_and_not_exposed():
    manager = JobManager(max_workers=1)
    try:
        job = manager.submit(
            "session-a:request", lambda _cancel: {"ok": True}, owner="session-a"
        )
        assert "owner" not in job
        assert manager.get_job(job["job_id"], owner="session-a")["job_id"] == job["job_id"]
        with pytest.raises(KeyError):
            manager.get_job(job["job_id"], owner="session-b")
    finally:
        manager.close()


def test_idempotency_is_scoped_to_the_job_owner():
    """Different authenticated owners may reuse the same client request key."""
    manager = JobManager(max_workers=1)
    try:
        first = manager.submit(
            "request-1", lambda _cancel: {"owner": "session-a"}, owner="session-a"
        )
        second = manager.submit(
            "request-1", lambda _cancel: {"owner": "session-b"}, owner="session-b"
        )

        assert second["job_id"] != first["job_id"]
        assert wait_for_terminal(manager, first["job_id"])["result"] == {
            "owner": "session-a"
        }
        assert wait_for_terminal(manager, second["job_id"])["result"] == {
            "owner": "session-b"
        }
    finally:
        manager.close()


def test_terminal_job_retention_is_bounded():
    manager = JobManager(max_workers=1, max_retained=1)
    try:
        first = manager.submit("request-1", lambda _cancel: {"ok": True})
        wait_for_terminal(manager, first["job_id"])
        second = manager.submit("request-2", lambda _cancel: {"ok": True})
        with pytest.raises(KeyError):
            manager.get_job(first["job_id"])
        assert wait_for_terminal(manager, second["job_id"])["status"] == "succeeded"
    finally:
        manager.close()
