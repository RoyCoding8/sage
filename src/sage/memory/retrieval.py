"""Cross-tier memory retrieval behind one operational interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .hybrid_retrieval import _HybridIndex


@dataclass(frozen=True)
class MemoryHit:
    text: str
    memory_type: str
    memory_id: str
    metadata: dict = field(default_factory=dict)
    score: float = 0.0
    citation: str = ""
    signals: dict = field(default_factory=dict)


class MemoryRetrieval:
    """Own indexing, ranking, access reinforcement, and prompt formatting."""

    def __init__(
        self,
        *,
        procedural=None,
        cases=None,
        skills=None,
        semantic=None,
        episodic=None,
        preferences=None,
        embedding_store=None,
        consolidator=None,
        weights: Optional[dict] = None,
    ):
        self._stores = {
            "procedural": procedural,
            "cases": cases,
            "skills": skills,
            "semantic": semantic,
            "episodic": episodic,
            "preferences": preferences,
        }
        self._consolidator = consolidator
        self._index = _HybridIndex(
            embedding_store=embedding_store,
            weights=weights,
        )

    def rebuild(self) -> dict:
        """Replace the index with a consistent snapshot of every memory tier."""
        self._index.rebuild_from_stores(**self._stores)
        return self._index.get_stats()

    def query(
        self,
        text: str,
        *,
        top_k: int = 10,
        types: Optional[list[str]] = None,
        min_score: float = 0.1,
    ) -> list[MemoryHit]:
        """Return ranked memory and reinforce entries that influenced retrieval."""
        if not text:
            return []
        if self._index.size == 0:
            self.rebuild()
        ranked = self._index.query(
            text,
            top_k=top_k,
            types=types,
            min_score=min_score,
        )
        if self._consolidator is not None:
            for result in ranked:
                if result.entry.entry_id:
                    self._consolidator.access(result.entry.entry_id)
        return [
            MemoryHit(
                text=result.entry.text,
                memory_type=result.entry.memory_type,
                memory_id=result.entry.entry_id,
                metadata=result.entry.metadata,
                score=result.final_score,
                citation=result.citation,
                signals={
                    "bm25": result.bm25_score,
                    "semantic": result.semantic_score,
                    "decay": result.decay_score,
                    "cognitive_weight": result.cw_score,
                    "tier": result.tier_score,
                    "rrf": result.rrf_score,
                },
            )
            for result in ranked
        ]

    def format_for_prompt(
        self,
        results: list[MemoryHit],
        *,
        max_tokens: int = 1500,
    ) -> str:
        """Format ranked memory with score citations for prompt injection."""
        if not results:
            return ""
        labels = {
            "rule": "## Learned Rules (MUST follow)",
            "preference": "## User Preferences (apply to deployment)",
            "case": "## Recent Execution History",
            "skill": "## Reusable Skills",
            "episode": "## Past Interactions",
            "semantic": "## Domain Knowledge",
        }
        sections = {}
        remaining = max_tokens * 4
        for hit in results:
            line = f"- {hit.text.strip()}\n  {hit.citation}"
            if len(line) > remaining:
                break
            sections.setdefault(hit.memory_type, []).append(line)
            remaining -= len(line)
        output = []
        for memory_type in labels:
            if memory_type in sections:
                output.append(labels[memory_type])
                output.extend(sections[memory_type])
                output.append("")
        return "\n".join(output).rstrip()

    def update_outcome(self, entry_ids: list[str], reward: float) -> None:
        """Update Cognitive Weight after the Run outcome is known."""
        self._index.update_batch_cognitive_weight(entry_ids, reward)

    def get_stats(self) -> dict:
        return self._index.get_stats()
