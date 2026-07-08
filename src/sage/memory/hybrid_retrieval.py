"""
Hybrid Retrieval Engine — Unified cross-tier search with 5-signal scoring.

Inspired by:
- MemTier (Sidik & Rokach, 2026): 5-signal weighted scoring with cognitive weight
- EvolveMem (Liu et al., 2026): Multi-view retrieval (BM25 + semantic + metadata) with RRF
- MARS (Liang et al., 2025): Forgetting curves and memory strength

Architecture:
    1. Candidate Generation: BM25 keyword scoring across all memory tiers
    2. Candidate Generation: Embedding cosine similarity across all memory tiers
    3. Score Fusion: Reciprocal Rank Fusion merges both ranked lists
    4. Re-scoring: 5-signal weighted combination:
       - φ_bm25: Normalized BM25 relevance
       - φ_semantic: Cosine similarity from embeddings
       - φ_decay: Ebbinghaus-inspired time decay (e^(-λ·Δt))
       - φ_cw: Cognitive weight (utility from past outcomes)
       - φ_tier: Memory tier priority boost
    5. Result: Ranked cross-tier memory hits with citations

This replaces the per-tier retrieval approach where semantic memory had BM25+RRF
but other tiers only had embedding-or-keyword. Now ALL tiers get the same
sophisticated retrieval, and results are ranked globally.
"""

import math
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .bm25 import BM25Index

logger = logging.getLogger(__name__)


# ─── Memory Entry ─────────────────────────────────────────────────────────────


@dataclass
class MemoryEntry:
    """A unified memory entry across all tiers."""

    text: str
    memory_type: str  # "rule", "case", "skill", "semantic", "episode", "preference"
    entry_id: str = ""
    metadata: dict = field(default_factory=dict)
    # Scoring signals
    created_at: float = 0.0  # Unix timestamp
    cognitive_weight: float = 0.0  # [-1, 1]: utility from outcomes
    access_count: int = 0
    confidence: float = 1.0
    # Source tracking
    source_task: str = ""


@dataclass
class RetrievalResult:
    """A single retrieval result with full scoring breakdown."""

    entry: MemoryEntry
    final_score: float
    # Individual signal scores (for transparency/debugging)
    bm25_score: float = 0.0
    semantic_score: float = 0.0
    decay_score: float = 0.0
    cw_score: float = 0.0
    tier_score: float = 0.0
    # RRF components
    bm25_rank: int = 0
    semantic_rank: int = 0
    rrf_score: float = 0.0

    @property
    def citation(self) -> str:
        """Human-readable citation showing why this was retrieved."""
        parts = [f"[{self.entry.memory_type}:{self.entry.entry_id}]"]
        parts.append(f"score={self.final_score:.3f}")
        if self.bm25_score > 0:
            parts.append(f"bm25={self.bm25_score:.3f}")
        if self.semantic_score > 0:
            parts.append(f"sem={self.semantic_score:.3f}")
        if self.entry.cognitive_weight != 0:
            parts.append(f"cw={self.entry.cognitive_weight:+.2f}")
        return " ".join(parts)


# ─── Hybrid Retrieval Engine ──────────────────────────────────────────────────


class _HybridIndex:
    """
    Unified cross-tier hybrid retrieval with 5-signal scoring.

    Implements the full pipeline:
    1. BM25 candidate generation (keyword match)
    2. Embedding candidate generation (semantic similarity)
    3. Reciprocal Rank Fusion (RRF) to merge both lists
    4. 5-signal re-scoring: BM25 + semantic + decay + cognitive weight + tier boost
    5. Returns ranked results with citations

    Signal weights (default, from MemTier paper adapted for our use case):
        w_bm25 = 0.30     (keyword relevance)
        w_semantic = 0.30  (semantic similarity)
        w_decay = 0.15     (recency preference)
        w_cw = 0.15        (utility from past outcomes)
        w_tier = 0.10      (memory type priority)
    """

    # Default signal weights
    DEFAULT_WEIGHTS = {
        "bm25": 0.30,
        "semantic": 0.30,
        "decay": 0.15,
        "cw": 0.15,
        "tier": 0.10,
    }

    # Tier priority multipliers (higher = more relevant for deployment tasks)
    TIER_PRIORITY = {
        "rule": 1.4,  # Learned rules are highest priority
        "preference": 1.3,  # User preferences are important
        "case": 1.2,  # Past execution evidence
        "skill": 1.1,  # Reusable procedures
        "episode": 1.0,  # Historical interactions
        "semantic": 0.9,  # Background knowledge (lowest)
    }

    # RRF constant (standard value from literature)
    RRF_K = 60

    # Time decay parameter (half-life ≈ 14 days, from MemTier)
    DECAY_LAMBDA = 0.05  # per day

    # BM25 bypass threshold: strong lexical match overrides decay
    BM25_BYPASS_THRESHOLD = 2.0

    def __init__(self, embedding_store=None, weights: Optional[dict] = None):
        """
        Initialize the hybrid retrieval engine.

        Args:
            embedding_store: EmbeddingStore instance for semantic search.
            weights: Optional dict of signal weights (keys: bm25, semantic, decay, cw, tier).
        """
        self._embedding_store = embedding_store
        self._weights = weights or self.DEFAULT_WEIGHTS.copy()
        self._bm25_index = BM25Index()
        self._entries: list[MemoryEntry] = []
        self._entry_map: dict[str, int] = {}  # entry_id → index

    @property
    def size(self) -> int:
        return len(self._entries)

    def set_embedding_store(self, store):
        """Attach or update the embedding store."""
        self._embedding_store = store

    def set_weights(self, weights: dict):
        """Update signal weights."""
        self._weights.update(weights)

    # ─── Index Management ────────────────────────────────────────────────────

    def add_entry(self, entry: MemoryEntry) -> int:
        """Add a memory entry to the index."""
        idx = len(self._entries)
        self._entries.append(entry)
        self._bm25_index.add_document(entry.text)
        if entry.entry_id:
            self._entry_map[entry.entry_id] = idx
        return idx

    def add_entries(self, entries: list[MemoryEntry]):
        """Batch-add memory entries."""
        for entry in entries:
            self.add_entry(entry)

    def rebuild_from_stores(
        self,
        procedural=None,
        cases=None,
        skills=None,
        semantic=None,
        episodic=None,
        preferences=None,
    ):
        """
        Rebuild the unified index from all memory tier stores.

        This is called once at agent startup and after significant updates.
        """
        self._entries = []
        self._bm25_index = BM25Index()
        self._entry_map = {}

        # Procedural rules
        if procedural:
            for rule in procedural.get_all_rules():
                entry = MemoryEntry(
                    text=f"{rule.get('text', '')} Context: {rule.get('context', '')}",
                    memory_type="rule",
                    entry_id=rule.get("id", ""),
                    metadata=rule,
                    created_at=rule.get("created_at", 0),
                    cognitive_weight=rule.get("cognitive_weight", 0.0),
                    confidence=rule.get("confidence", 0.9),
                    source_task=rule.get("source_task", ""),
                )
                self.add_entry(entry)

        # Cases
        if cases:
            for case in cases.get_recent(20):  # Last 20 cases
                case_text = (
                    f"Task: {case.get('task', '')} | "
                    f"Outcome: {case.get('outcome', '')} | "
                    f"Tools: {', '.join(case.get('tools_used', []))}"
                )
                if case.get("error"):
                    case_text += f" | Error: {case['error']}"
                entry = MemoryEntry(
                    text=case_text,
                    memory_type="case",
                    entry_id=case.get("case_id", ""),
                    metadata=case,
                    created_at=case.get("created_at", 0),
                    cognitive_weight=case.get("cognitive_weight", 0.0),
                    confidence=1.0 if case.get("outcome") == "success" else 0.5,
                )
                self.add_entry(entry)

        # Skills
        if skills:
            for skill in skills.get_all():
                skill_text = (
                    f"Skill: {skill.get('name', '')} | "
                    f"Task: {skill.get('task', '')} | "
                    f"Tools: {', '.join(skill.get('tools_used', []))}"
                )
                entry = MemoryEntry(
                    text=skill_text,
                    memory_type="skill",
                    entry_id=skill.get("skill_id", ""),
                    metadata=skill,
                    created_at=skill.get("created_at", 0),
                    cognitive_weight=skill.get("cognitive_weight", 0.0),
                    confidence=1.0,
                )
                self.add_entry(entry)

        # Semantic knowledge docs
        if semantic:
            for doc_name in semantic.list_documents():
                content = semantic.get_document(doc_name)
                if content:
                    entry = MemoryEntry(
                        text=content[:500],  # Truncate for indexing
                        memory_type="semantic",
                        entry_id=doc_name,
                        metadata={"doc": doc_name},
                        confidence=1.0,
                    )
                    self.add_entry(entry)

        # Episodic memory (recent)
        if episodic:
            for ep in episodic.get_recent(10):
                ep_text = (
                    f"Task: {ep.get('task', '')} | "
                    f"Outcome: {ep.get('outcome', '')} | "
                    f"Correction: {ep.get('correction', '')}"
                )
                entry = MemoryEntry(
                    text=ep_text,
                    memory_type="episode",
                    entry_id=ep.get("id", ""),
                    metadata=ep,
                    created_at=ep.get("timestamp", 0),
                    cognitive_weight=ep.get("cognitive_weight", 0.0),
                )
                self.add_entry(entry)

        # Preferences
        if preferences:
            for category, pref in preferences.get_all().items():
                pref_text = (
                    f"User preference: {category} = {pref.get('value', '')} "
                    f"(source: {pref.get('source', 'unknown')})"
                )
                entry = MemoryEntry(
                    text=pref_text,
                    memory_type="preference",
                    entry_id=f"pref_{category}",
                    metadata=pref,
                    created_at=pref.get("last_updated", 0),
                    cognitive_weight=pref.get("cognitive_weight", 0.0),
                    confidence=pref.get("confidence", 0.7),
                )
                self.add_entry(entry)

        logger.info("Hybrid index rebuilt: %d entries across all tiers", self.size)

    # ─── Query Pipeline ──────────────────────────────────────────────────────

    def query(
        self,
        text: str,
        top_k: int = 10,
        types: Optional[list[str]] = None,
        min_score: float = 0.1,
    ) -> list[RetrievalResult]:
        """
        Execute the full hybrid retrieval pipeline.

        Args:
            text: Query string.
            top_k: Maximum results to return.
            types: Optional filter to specific memory types.
            min_score: Minimum final score threshold.

        Returns:
            Ranked list of RetrievalResult objects with full score breakdown.
        """
        if not self._entries:
            return []

        # Step 1: BM25 candidate generation
        bm25_candidates = self._bm25_candidates(text, top_k=top_k * 3)

        # Step 2: Embedding candidate generation
        semantic_candidates = self._semantic_candidates(
            text, top_k=top_k * 3, types=types
        )

        # Step 3: Reciprocal Rank Fusion
        rrf_scores = self._reciprocal_rank_fusion(bm25_candidates, semantic_candidates)

        # Step 4: Full 5-signal scoring on the union of candidates
        candidate_indices = set()
        for idx, _ in bm25_candidates:
            candidate_indices.add(idx)
        for idx, _ in semantic_candidates:
            candidate_indices.add(idx)

        # If no candidates from either, return empty
        if not candidate_indices:
            return []

        # Apply type filter
        if types:
            candidate_indices = {
                idx
                for idx in candidate_indices
                if self._entries[idx].memory_type in types
            }

        # Score all candidates with the 5-signal formula
        results = []
        now = time.time()

        # Build lookup dicts for individual scores
        bm25_dict = {idx: score for idx, score in bm25_candidates}
        sem_dict = {idx: score for idx, score in semantic_candidates}

        # Normalize BM25 scores (critical insight from MemTier: raw BM25 dominates)
        max_bm25 = max(bm25_dict.values()) if bm25_dict else 1.0
        if max_bm25 == 0:
            max_bm25 = 1.0

        for idx in candidate_indices:
            entry = self._entries[idx]

            # Signal 1: Normalized BM25
            raw_bm25 = bm25_dict.get(idx, 0.0)
            phi_bm25 = raw_bm25 / max_bm25  # Normalize to [0, 1]

            # Signal 2: Semantic similarity (already [0, 1])
            phi_semantic = sem_dict.get(idx, 0.0)

            # Signal 3: Time decay
            phi_decay = self._compute_decay(entry, now, raw_bm25)

            # Signal 4: Cognitive weight (map [-1, 1] to [0, 1])
            phi_cw = (entry.cognitive_weight + 1.0) / 2.0

            # Signal 5: Tier boost
            phi_tier = self.TIER_PRIORITY.get(entry.memory_type, 1.0) / 1.4  # Normalize

            # Weighted combination
            w = self._weights
            final_score = (
                w["bm25"] * phi_bm25
                + w["semantic"] * phi_semantic
                + w["decay"] * phi_decay
                + w["cw"] * phi_cw
                + w["tier"] * phi_tier
            )

            # Confidence multiplier (low-confidence memories get dampened)
            final_score *= entry.confidence

            if final_score >= min_score:
                # Track BM25/semantic ranks for RRF citation
                bm25_rank = next(
                    (rank for rank, (i, _) in enumerate(bm25_candidates) if i == idx),
                    len(bm25_candidates),
                )
                sem_rank = next(
                    (
                        rank
                        for rank, (i, _) in enumerate(semantic_candidates)
                        if i == idx
                    ),
                    len(semantic_candidates),
                )

                results.append(
                    RetrievalResult(
                        entry=entry,
                        final_score=final_score,
                        bm25_score=phi_bm25,
                        semantic_score=phi_semantic,
                        decay_score=phi_decay,
                        cw_score=phi_cw,
                        tier_score=phi_tier,
                        bm25_rank=bm25_rank,
                        semantic_rank=sem_rank,
                        rrf_score=rrf_scores.get(idx, 0.0),
                    )
                )

        # Sort by final score descending
        results.sort(key=lambda r: r.final_score, reverse=True)
        return results[:top_k]

    # ─── Cognitive Weight Updates ────────────────────────────────────────────

    def update_cognitive_weight(
        self, entry_id: str, reward: float, attribution: float = 1.0, alpha: float = 0.1
    ):
        """
        Update cognitive weight for a memory entry based on task outcome.

        From MemTier: CW_i ← clip(CW_i + α · r · â_i, -1, 1)

        Args:
            entry_id: The memory entry to update.
            reward: Task outcome reward (-0.5 for failure, 0 for neutral, +1 for success).
            attribution: How much this entry contributed (0 to 1).
            alpha: Learning rate (default 0.1).
        """
        if entry_id in self._entry_map:
            idx = self._entry_map[entry_id]
            entry = self._entries[idx]
            delta = alpha * reward * attribution
            entry.cognitive_weight = max(-1.0, min(1.0, entry.cognitive_weight + delta))
            entry.access_count += 1
            logger.debug(
                "CW update: %s %+.3f → %.3f", entry_id, delta, entry.cognitive_weight
            )

    def update_batch_cognitive_weight(self, entry_ids: list[str], reward: float):
        """Update cognitive weight for all entries retrieved during a task."""
        n = len(entry_ids)
        if n == 0:
            return
        # Distribute attribution proportionally (first = most attributed)
        for i, eid in enumerate(entry_ids):
            attribution = 1.0 / (i + 1)  # Rank-based attribution
            normalized_attr = attribution / sum(1.0 / (j + 1) for j in range(n))
            self.update_cognitive_weight(eid, reward, normalized_attr)

    # ─── Private: Candidate Generation ───────────────────────────────────────

    def _bm25_candidates(self, query: str, top_k: int = 30) -> list[tuple[int, float]]:
        """Generate BM25 candidates."""
        return self._bm25_index.query(query, top_k=top_k)

    def _semantic_candidates(
        self, query: str, top_k: int = 30, types: Optional[list[str]] = None
    ) -> list[tuple[int, float]]:
        """Generate embedding-based candidates."""
        if not self._embedding_store or self._embedding_store.size == 0:
            return []

        # Build filter
        filter_fn = None
        if types:

            def filter_fn(metadata):
                return metadata.get("type") in types

        hits = self._embedding_store.query(
            query,
            top_k=top_k,
            filter_fn=filter_fn,
            min_score=0.2,
        )

        # Map embedding hits back to our entry indices
        candidates = []
        for hit in hits:
            # Try to find matching entry by ID or text
            hit_id = (
                hit.get("rule_id")
                or hit.get("case_id")
                or hit.get("skill_id")
                or hit.get("doc")
                or ""
            )
            if hit_id and hit_id in self._entry_map:
                candidates.append((self._entry_map[hit_id], hit.get("score", 0.0)))
            else:
                # Fallback: match by text prefix
                hit_text = hit.get("text", "")[:100]
                for idx, entry in enumerate(self._entries):
                    if entry.text[:100] == hit_text:
                        candidates.append((idx, hit.get("score", 0.0)))
                        break

        return candidates

    def _reciprocal_rank_fusion(
        self, list_a: list[tuple[int, float]], list_b: list[tuple[int, float]]
    ) -> dict[int, float]:
        """
        Merge two ranked lists using Reciprocal Rank Fusion.

        RRF score = Σ 1/(k + rank) across both lists.
        k=60 is the standard value (robust across score scales).
        """
        k = self.RRF_K
        scores: dict[int, float] = {}

        for rank, (idx, _) in enumerate(list_a):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)

        for rank, (idx, _) in enumerate(list_b):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)

        return scores

    def _compute_decay(self, entry: MemoryEntry, now: float, raw_bm25: float) -> float:
        """
        Compute time decay signal.

        From MemTier:
        - φ_decay = 1.0 if BM25 bypass (strong lexical match overrides recency)
        - φ_decay = e^(-λ·Δt) otherwise
        """
        # BM25 bypass: strong keyword match overrides recency penalty
        if raw_bm25 > self.BM25_BYPASS_THRESHOLD:
            return 1.0

        created_at = self._coerce_timestamp(entry.created_at)
        if created_at <= 0:
            return 0.7  # Unknown age: moderate decay

        age_days = (now - created_at) / 86400.0
        if age_days < 0:
            age_days = 0

        return math.exp(-self.DECAY_LAMBDA * age_days)

    @staticmethod
    def _coerce_timestamp(value) -> float:
        """Normalize persisted timestamp formats to Unix seconds."""
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return 0.0
            try:
                return float(text)
            except ValueError:
                pass
            for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(text[: len(fmt)], fmt).timestamp()
                except ValueError:
                    continue
        return 0.0

    # ─── Utility ─────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return engine statistics."""
        type_counts = {}
        for entry in self._entries:
            type_counts[entry.memory_type] = type_counts.get(entry.memory_type, 0) + 1

        return {
            "total_entries": self.size,
            "type_counts": type_counts,
            "weights": self._weights,
            "bm25_docs": self._bm25_index.n_docs,
            "has_embeddings": self._embedding_store is not None
            and self._embedding_store.size > 0,
        }
