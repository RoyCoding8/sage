"""
Memory Consolidation & Forgetting — Ebbinghaus-inspired memory lifecycle.

Based on MARS (Liang et al., 2025) and MemTier (Sidik & Rokach, 2026):
- Retention: R(I, τ) = e^(-τ/S) where S is memory strength
- Memory strength S increases with: usage, confirmation, successful outcomes
- Consolidation: Repeated episodic patterns → candidate rules
- Forgetting: Memories below retention threshold are archived/removed
- Contradiction: New corrections can supersede old rules

Lifecycle:
1. New memory enters with base strength S₀
2. Each access/confirmation increases S (spaced repetition effect)
3. Over time, R decays — low-R memories are candidates for:
   a. Consolidation (episodic → rule) if pattern is repeated
   b. Archival (kept on disk, removed from active retrieval)
   c. Deletion (contradicted by newer evidence)
4. High-utility memories (positive cognitive weight) decay slower
"""

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sage.persistence import AtomicJsonDocument

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

# Base memory strength (determines initial half-life)
BASE_STRENGTH = 7.0  # days — new memories have ~50% retention after 7 days

# Strength increase per reinforcement (access, confirmation, positive outcome)
REINFORCEMENT_DELTA = 2.0  # days added per reinforcement

# Maximum strength (prevents memories from being immortal)
MAX_STRENGTH = 90.0  # 90 days — even reinforced memories eventually fade

# Retention thresholds (from MARS paper)
THETA_RETAIN = 0.7  # Above this: stays in active memory
THETA_ARCHIVE = 0.3  # Below this: archived (removed from retrieval)
THETA_DISCARD = 0.1  # Below this: deleted entirely

# Consolidation: minimum episode count before pattern extraction
MIN_EPISODES_FOR_CONSOLIDATION = 3

# How often to run maintenance (in task executions)
MAINTENANCE_INTERVAL = 5


# ─── Data Structures ──────────────────────────────────────────────────────────


@dataclass
class MemoryStrength:
    """Tracks the strength and retention of a memory entry."""

    memory_id: str
    memory_type: str  # "rule", "episode", "case", "preference"
    strength: float = BASE_STRENGTH
    created_at: float = 0.0
    last_accessed: float = 0.0
    access_count: int = 0
    reinforcement_count: int = 0
    cognitive_weight: float = 0.0  # From outcome tracking
    superseded_by: Optional[str] = None  # If contradicted
    archived: bool = False

    @property
    def age_days(self) -> float:
        """Age of the memory in days."""
        if self.created_at <= 0:
            return 0.0
        return (time.time() - self.created_at) / 86400.0

    @property
    def retention(self) -> float:
        """
        Current retention rate using Ebbinghaus curve.
        R(I, τ) = e^(-τ/S)

        Strength is boosted by:
        - Positive cognitive weight (successful usage)
        - Recent access (spaced repetition)
        """
        tau = self.age_days
        # Effective strength includes cognitive weight bonus
        effective_s = self.strength + max(0, self.cognitive_weight * 10)
        if effective_s <= 0:
            return 0.0
        return math.exp(-tau / effective_s)

    def reinforce(self):
        """Reinforce this memory (increases strength = slower forgetting)."""
        self.reinforcement_count += 1
        self.strength = min(MAX_STRENGTH, self.strength + REINFORCEMENT_DELTA)
        self.last_accessed = time.time()
        self.access_count += 1

    def mark_superseded(self, by_id: str):
        """Mark this memory as superseded by a newer one."""
        self.superseded_by = by_id
        # Rapidly decay superseded memories
        self.strength = min(self.strength, 1.0)


@dataclass
class ConsolidationCandidate:
    """A potential rule extracted from repeated episodic patterns."""

    pattern: str  # The repeated pattern description
    supporting_episodes: list[str] = field(default_factory=list)  # episode IDs
    confidence: float = 0.5
    suggested_rule: str = ""


# ─── Consolidation Engine ─────────────────────────────────────────────────────


class MemoryConsolidator:
    """
    Manages memory lifecycle: forgetting, consolidation, contradiction detection.

    Runs periodically (every MAINTENANCE_INTERVAL tasks) to:
    1. Compute retention for all tracked memories
    2. Archive low-retention memories
    3. Detect repeated patterns for consolidation
    4. Identify contradictions between old and new rules
    """

    def __init__(self, store_path: str = "memory/consolidation.json"):
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._strengths: dict[str, MemoryStrength] = {}
        self._task_counter = 0
        self._document = AtomicJsonDocument(self.store_path, self._empty_state)
        self._load()

    @staticmethod
    def _empty_state() -> dict:
        return {"task_counter": 0, "strengths": {}}

    # ─── Public API ──────────────────────────────────────────────────────────

    def track(
        self, memory_id: str, memory_type: str, initial_strength: float = BASE_STRENGTH
    ):
        """Start tracking a new memory entry."""
        def add() -> None:
            if memory_id not in self._strengths:
                self._strengths[memory_id] = MemoryStrength(
                    memory_id=memory_id,
                    memory_type=memory_type,
                    strength=initial_strength,
                    created_at=time.time(),
                    last_accessed=time.time(),
                )

        self._transaction(add)

    def access(self, memory_id: str):
        """Record an access (retrieval) of a memory — reinforces it."""
        def reinforce() -> None:
            if memory_id in self._strengths:
                self._strengths[memory_id].reinforce()

        self._transaction(reinforce)

    def update_cognitive_weight(self, memory_id: str, cw: float):
        """Update the cognitive weight from outcome tracking."""
        def update() -> None:
            if memory_id in self._strengths:
                self._strengths[memory_id].cognitive_weight = cw

        self._transaction(update)

    def get_retention(self, memory_id: str) -> float:
        """Get current retention for a memory entry."""
        self._load()
        if memory_id in self._strengths:
            return self._strengths[memory_id].retention
        return 1.0  # Unknown memories assumed fresh

    def is_superseded(self, memory_id: str) -> bool:
        """Check if a memory has been superseded by a newer one."""
        self._load()
        if memory_id in self._strengths:
            return self._strengths[memory_id].superseded_by is not None
        return False

    def on_task_complete(self):
        """Called after each task execution. Triggers periodic maintenance."""
        def complete() -> dict:
            self._task_counter += 1
            if self._task_counter >= MAINTENANCE_INTERVAL:
                self._task_counter = 0
                return self._run_maintenance()
            return {
                "action": "skipped",
                "next_in": MAINTENANCE_INTERVAL - self._task_counter,
            }

        return self._transaction(complete)

    def run_maintenance(self) -> dict:
        """Run one durable maintenance transaction."""
        return self._transaction(self._run_maintenance)

    def _run_maintenance(self) -> dict:
        """
        Run the full maintenance cycle.

        Returns a report of actions taken:
        - archived: list of memory IDs moved to archive
        - reinforced: memories that were accessed recently
        - consolidation_candidates: repeated patterns ready for rule extraction
        """
        report = {
            "archived": [],
            "discarded": [],
            "active": 0,
            "retention_stats": {},
        }

        for mid, ms in list(self._strengths.items()):
            retention = ms.retention

            if ms.superseded_by:
                # Already superseded — skip
                continue

            if retention < THETA_DISCARD and not ms.archived:
                ms.archived = True
                report["discarded"].append(mid)
            elif retention < THETA_ARCHIVE and not ms.archived:
                ms.archived = True
                report["archived"].append(mid)
            else:
                report["active"] += 1

        # Track retention distribution
        retentions = [
            ms.retention for ms in self._strengths.values() if not ms.archived
        ]
        if retentions:
            report["retention_stats"] = {
                "min": round(min(retentions), 3),
                "max": round(max(retentions), 3),
                "mean": round(sum(retentions) / len(retentions), 3),
                "count": len(retentions),
            }

        logger.info(
            "Consolidation maintenance: %d active, %d archived, %d discarded",
            report["active"],
            len(report["archived"]),
            len(report["discarded"]),
        )
        return report

    def detect_contradiction(
        self, new_rule_text: str, existing_rules: list[dict], embedding_store=None
    ) -> Optional[str]:
        """
        Detect if a new rule contradicts an existing one.

        Returns the ID of the contradicted rule, or None.

        Algorithm (dual-signal, from contradiction retrieval literature):
        1. Topic similarity: embedding cosine > 0.6 identifies same-topic pairs
           (falls back to BM25-based topic overlap if no embeddings)
        2. Value extraction: for same-topic pairs, extract structured values
           (ports, regions, CIDRs, instance types) and check for conflicts
        3. Polarity analysis: detect opposing directives on the same subject

        This handles:
        - "Use port 3000" vs "Use port 8080" (value conflict)
        - "Deploy to us-east-1" vs "Deploy to eu-west-1" (region conflict)
        - "Always open port 80" vs "Never open port 80" (polarity conflict)
        """
        self._load()
        import re

        new_lower = new_rule_text.lower()

        for rule in existing_rules:
            rule_id = rule.get("id", "")
            existing_text = rule.get("text", "")
            existing_lower = existing_text.lower()
            existing_context = rule.get("context", "").lower()
            full_existing = f"{existing_lower} {existing_context}"

            # ─── Pass 1: Topic Similarity ─────────────────────────────
            topic_similar = False

            if embedding_store and embedding_store.api_key:
                # Embedding-based topic similarity (best)
                try:
                    new_vecs = embedding_store._embed([new_rule_text])
                    old_vecs = embedding_store._embed([existing_text])
                    if new_vecs and old_vecs:
                        import numpy as np

                        new_arr = np.array(new_vecs[0], dtype=np.float32)
                        old_arr = np.array(old_vecs[0], dtype=np.float32)
                        new_arr = new_arr / max(np.linalg.norm(new_arr), 1e-8)
                        old_arr = old_arr / max(np.linalg.norm(old_arr), 1e-8)
                        sim = float(np.dot(new_arr, old_arr))
                        topic_similar = sim >= 0.6
                except Exception:
                    pass

            if not topic_similar:
                # BM25-based topic overlap (offline fallback)
                new_tokens = set(re.findall(r"[a-z0-9]+", new_lower))
                old_tokens = set(re.findall(r"[a-z0-9]+", full_existing))
                # Remove stopwords for topic comparison
                stopwords = {
                    "the",
                    "a",
                    "an",
                    "to",
                    "in",
                    "on",
                    "for",
                    "of",
                    "is",
                    "it",
                    "and",
                    "or",
                    "that",
                    "this",
                    "be",
                    "with",
                    "from",
                }
                new_content = new_tokens - stopwords
                old_content = old_tokens - stopwords
                if not new_content or not old_content:
                    continue
                # Jaccard on content words (not as a final classifier, but as topic gate)
                overlap = len(new_content & old_content)
                union = len(new_content | old_content)
                topic_similar = (overlap / max(union, 1)) >= 0.25

            if not topic_similar:
                continue  # Different topics — cannot contradict

            # ─── Pass 2: Structured Value Extraction ──────────────────
            # Extract specific values that can conflict
            value_patterns = {
                "port": r"\b(?:port)\s*(\d{2,5})\b",
                "region": r"\b([a-z]{2}-(?:east|west|central|south|north|southeast|northeast)-\d)\b",
                "cidr": r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2})\b",
                "instance_type": r"\b(ecs\.[a-z0-9.-]+)\b",
            }

            for value_type, pattern in value_patterns.items():
                new_matches = set(re.findall(pattern, new_lower))
                old_matches = set(re.findall(pattern, full_existing))

                # Both mention the same value type but with different values
                if new_matches and old_matches and new_matches != old_matches:
                    logger.info(
                        "Value contradiction detected: %s %s=%s vs %s=%s",
                        rule_id,
                        value_type,
                        old_matches,
                        value_type,
                        new_matches,
                    )
                    self._mark_superseded(rule_id, f"new_{time.time():.0f}")
                    return rule_id

            # ─── Pass 3: Polarity Analysis ────────────────────────────
            # Detect opposing directives on the same subject
            # More sophisticated than just negation word lists:
            # Check if both rules address the same ACTION+OBJECT but with opposing stance

            # Extract action-object pairs
            action_patterns = [
                (
                    r"\b(always|must|should|ensure|configure|enable|open|install|use)\s+(\w+(?:\s+\w+)?)",
                    "positive",
                ),
                (
                    r"\b(never|don\'t|do not|avoid|disable|close|remove|stop)\s+(\w+(?:\s+\w+)?)",
                    "negative",
                ),
            ]

            new_stances = []
            old_stances = []

            for pattern, polarity in action_patterns:
                for match in re.finditer(pattern, new_lower):
                    new_stances.append((polarity, match.group(2).strip()))
                for match in re.finditer(pattern, full_existing):
                    old_stances.append((polarity, match.group(2).strip()))

            # Check for opposing polarity on overlapping objects
            for new_pol, new_obj in new_stances:
                for old_pol, old_obj in old_stances:
                    # Same or similar object, different polarity
                    obj_words_new = set(new_obj.split())
                    obj_words_old = set(old_obj.split())
                    if obj_words_new & obj_words_old and new_pol != old_pol:
                        logger.info(
                            "Polarity contradiction: '%s' (%s) vs '%s' (%s) on '%s'",
                            new_rule_text[:40],
                            new_pol,
                            existing_text[:40],
                            old_pol,
                            obj_words_new & obj_words_old,
                        )
                        self._mark_superseded(rule_id, f"new_{time.time():.0f}")
                        return rule_id

        return None

    def find_consolidation_candidates(
        self, episodes: list[dict], min_count: int = MIN_EPISODES_FOR_CONSOLIDATION
    ) -> list[ConsolidationCandidate]:
        """
        Find repeated patterns in episodic memory that could become rules.

        Looks for:
        - Same error occurring multiple times
        - Same correction being applied to similar tasks
        - Same tools being used in the same order
        """
        # Group by error type
        error_groups: dict[str, list[dict]] = {}
        for ep in episodes:
            error = ep.get("error", "")
            if error:
                key = error[:50]  # Group by error prefix
                error_groups.setdefault(key, []).append(ep)

        # Group by correction text
        correction_groups: dict[str, list[dict]] = {}
        for ep in episodes:
            correction = ep.get("correction", "")
            if correction:
                key = correction[:80]
                correction_groups.setdefault(key, []).append(ep)

        candidates = []

        # Repeated errors → candidate rule about prevention
        for error_key, eps in error_groups.items():
            if len(eps) >= min_count:
                candidates.append(
                    ConsolidationCandidate(
                        pattern=f"Repeated error: {error_key}",
                        supporting_episodes=[e.get("id", "") for e in eps],
                        confidence=min(0.9, 0.5 + len(eps) * 0.1),
                        suggested_rule=f"Prevent: {error_key} (occurred {len(eps)} times)",
                    )
                )

        # Repeated corrections → candidate rule
        for correction_key, eps in correction_groups.items():
            if len(eps) >= min_count:
                candidates.append(
                    ConsolidationCandidate(
                        pattern=f"Repeated correction: {correction_key}",
                        supporting_episodes=[e.get("id", "") for e in eps],
                        confidence=min(0.9, 0.5 + len(eps) * 0.1),
                        suggested_rule=correction_key,
                    )
                )

        return candidates

    def get_memory_health(self) -> dict:
        """Get a summary of memory health across all tracked entries."""
        self._load()
        if not self._strengths:
            return {"total": 0, "healthy": 0, "fading": 0, "archived": 0}

        healthy = sum(
            1
            for ms in self._strengths.values()
            if ms.retention >= THETA_RETAIN and not ms.archived
        )
        fading = sum(
            1
            for ms in self._strengths.values()
            if THETA_ARCHIVE <= ms.retention < THETA_RETAIN and not ms.archived
        )
        archived = sum(1 for ms in self._strengths.values() if ms.archived)

        return {
            "total": len(self._strengths),
            "healthy": healthy,
            "fading": fading,
            "archived": archived,
            "superseded": sum(
                1 for ms in self._strengths.values() if ms.superseded_by is not None
            ),
        }

    # ─── Persistence ─────────────────────────────────────────────────────────

    def _mark_superseded(self, memory_id: str, replacement_id: str) -> None:
        def mark() -> None:
            if memory_id in self._strengths:
                self._strengths[memory_id].mark_superseded(replacement_id)

        self._transaction(mark)

    def _transaction(self, mutate):
        def update(data: dict):
            self._hydrate(data)
            result = mutate()
            data.clear()
            data.update(self._serialize())
            return result

        return self._document.update(update)

    def _hydrate(self, data: dict) -> None:
        self._strengths.clear()
        for mid, ms_data in data.get("strengths", {}).items():
            self._strengths[mid] = MemoryStrength(
                memory_id=mid,
                memory_type=ms_data.get("memory_type", "unknown"),
                strength=ms_data.get("strength", BASE_STRENGTH),
                created_at=ms_data.get("created_at", 0),
                last_accessed=ms_data.get("last_accessed", 0),
                access_count=ms_data.get("access_count", 0),
                reinforcement_count=ms_data.get("reinforcement_count", 0),
                cognitive_weight=ms_data.get("cognitive_weight", 0.0),
                superseded_by=ms_data.get("superseded_by"),
                archived=ms_data.get("archived", False),
            )
        self._task_counter = data.get("task_counter", 0)

    def _serialize(self) -> dict:
        return {
            "task_counter": self._task_counter,
            "strengths": {
                mid: {
                    "memory_type": memory.memory_type,
                    "strength": memory.strength,
                    "created_at": memory.created_at,
                    "last_accessed": memory.last_accessed,
                    "access_count": memory.access_count,
                    "reinforcement_count": memory.reinforcement_count,
                    "cognitive_weight": memory.cognitive_weight,
                    "superseded_by": memory.superseded_by,
                    "archived": memory.archived,
                }
                for mid, memory in self._strengths.items()
            },
        }

    def _load(self):
        """Refresh consolidation state from the durable document."""
        try:
            self._hydrate(self._document.read())
        except (ValueError, OSError) as e:
            logger.warning("Failed to load consolidation state: %s", e)
