"""Correlated Run interface shared by web, CLI, demo, and Benchmark adapters."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class RunContext:
    mode: str
    provider: str
    session_id: str
    region: str | None = None


class Run:
    """Own Run correlation and execution-path provenance."""

    def __init__(
        self,
        execute: Callable,
        mcp,
        default_context: RunContext,
    ):
        self._execute = execute
        self._mcp = mcp
        self._default_context = default_context

    def execute(
        self,
        task: str,
        *,
        cancel_event=None,
        context: Optional[RunContext] = None,
        tools: Optional[list[str]] = None,
        read_only: bool = False,
    ) -> dict:
        run_id = uuid.uuid4().hex[:12]
        set_trace = getattr(self._mcp, "set_run_trace_id", None)
        if callable(set_trace):
            set_trace(run_id)
        try:
            result = self._execute(
                task,
                tools=tools,
                cancel_event=cancel_event,
                read_only=read_only,
            )
        finally:
            if callable(set_trace):
                set_trace(None)

        result["execution"] = self.describe(context, trace_id=run_id)
        return result

    def describe(
        self,
        context: Optional[RunContext] = None,
        *,
        trace_id: str | None = None,
    ) -> dict:
        """Describe the effective Run path without exposing adapter internals."""
        effective = context or self._default_context
        return {
            "mode": effective.mode,
            "provider": effective.provider,
            "region": effective.region,
            "simulated": bool(getattr(self._mcp, "simulate", True)),
            "session_id": effective.session_id,
            "trace_id": trace_id,
        }
