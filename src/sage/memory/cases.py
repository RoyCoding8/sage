"""
Case Memory — structured execution trajectories for case-based learning.

Each case records task context, ordered tool steps, outcome, failure point, and
rules applied. This turns episodic memory from a flat log into reusable
state-action-outcome evidence.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sage.memory.collection import JsonDocumentCollection
from sage.persistence import AtomicJsonLines

logger = logging.getLogger(__name__)


class CaseMemory(JsonDocumentCollection):
    def __init__(self, case_path: str = "memory/cases.jsonl", embedding_store=None):
        self.case_path = Path(case_path)
        self.case_path.parent.mkdir(parents=True, exist_ok=True)
        self._embedding_store = embedding_store
        self._document = AtomicJsonLines[dict](self.case_path)

    def set_embedding_store(self, store):
        """Attach an embedding store for semantic case retrieval."""
        self._embedding_store = store

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", text.lower()))

    def record(
        self,
        task: str,
        outcome: str,
        steps: list[dict],
        app_type: str = "",
        tools_used: Optional[list[str]] = None,
        error: Optional[str] = None,
        failure_point: Optional[str] = None,
        rules_applied: Optional[list[str]] = None,
        policies_applied: Optional[list[str]] = None,
        correction: Optional[str] = None,
    ) -> dict:
        def append_case(cases: list[dict]) -> dict:
            numeric_ids = [
                int(case_id[1:])
                for case in cases
                if (case_id := str(case.get("case_id", ""))).startswith("C")
                and case_id[1:].isdigit()
            ]
            case = {
                "case_id": f"C{max(numeric_ids, default=0) + 1:03d}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task": task,
                "app_type": app_type,
                "outcome": outcome,
                "steps": steps,
                "tools_used": tools_used or [],
                "error": error,
                "failure_point": failure_point,
                "rules_applied": rules_applied or [],
                "policies_applied": policies_applied or [],
                "correction": correction,
            }
            cases.append(case)
            return case

        case = self._document.update(append_case)

        # Index into embedding store
        if self._embedding_store:
            embed_text = f"{task} | {app_type} | outcome: {outcome}"
            if error:
                embed_text += f" | error: {error}"
            if failure_point:
                embed_text += f" | failed at: {failure_point}"
            self._embedding_store.add(
                embed_text,
                {"type": "case", "case_id": case["case_id"], "outcome": outcome},
            )

        return case

    def retrieve(
        self, task: str, limit: int = 3, outcome: Optional[str] = None
    ) -> list[dict]:
        """Retrieve relevant cases using embeddings (with keyword fallback).

        Embedding-based retrieval finds semantically similar cases even when
        the exact words differ (e.g., "launch server" matches "deploy app").
        """
        # Try embedding-based retrieval first
        if self._embedding_store and self._embedding_store.size > 0:
            filter_fn = None
            if outcome:

                def filter_fn(metadata):
                    return (
                        metadata.get("type") == "case"
                        and metadata.get("outcome") == outcome
                    )
            else:

                def filter_fn(metadata):
                    return metadata.get("type") == "case"

            hits = self._embedding_store.query(task, top_k=limit, filter_fn=filter_fn)
            if hits:
                # Map hits back to full case records
                all_cases = {c.get("case_id"): c for c in self.get_all()}
                results = []
                for hit in hits:
                    case_id = hit.get("case_id")
                    if case_id and case_id in all_cases:
                        results.append(all_cases[case_id])
                if results:
                    return results

        # Fallback: BM25 scoring (proper TF-IDF weighted matching)
        from .bm25 import BM25Index

        bm25 = BM25Index()
        all_cases = self.get_all()
        filtered_cases = [
            c for c in all_cases if not outcome or c.get("outcome") == outcome
        ]

        for case in filtered_cases:
            doc_text = " ".join(
                [
                    case.get("task", ""),
                    case.get("app_type", ""),
                    case.get("failure_point") or "",
                    " ".join(case.get("tools_used", [])),
                ]
            )
            bm25.add_document(doc_text)

        hits = bm25.query(task, top_k=limit)
        return [
            filtered_cases[idx]
            for idx, score in hits
            if score > 0 and idx < len(filtered_cases)
        ]

    def get_recent(self, n: int = 5) -> list[dict]:
        return self.get_all()[-n:]

    def get_stats(self) -> dict:
        cases = self.get_all()
        success = sum(1 for c in cases if c.get("outcome") == "success")
        return {"total": len(cases), "success": success, "failed": len(cases) - success}
