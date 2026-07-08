"""
Procedural Memory — Self-learned rules from reflection.

Rules are stored in rules.md and loaded into the agent's prompt.
The reflection engine writes new rules here.

Supports two retrieval modes:
- Keyword-based (default, always works offline)
- Embedding-based (semantic similarity via EmbeddingStore for better recall)

Input validation:
- add_rule() validates rule_text and context are non-empty strings.
- Confidence is clamped to [0.0, 1.0].
- File I/O failures are logged but do not crash the caller.
"""

import logging
import json
import math
import re
import time
from pathlib import Path

from sage.persistence import append_text, atomic_write_text
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class ProceduralMemory:
    def __init__(self, rules_path: str = "rules/rules.md", embedding_store=None):
        self.rules_path = Path(rules_path)
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        self._embedding_store = embedding_store
        # In-memory cache: avoids re-reading file on every get_all_rules() call
        self._cache: list[dict] | None = None
        self._cache_mtime: float = 0.0
        if not self.rules_path.exists():
            self._init_empty()

    def set_embedding_store(self, store):
        """Attach an embedding store for semantic rule retrieval."""
        self._embedding_store = store

    def _init_empty(self):
        atomic_write_text(self.rules_path, self._RULES_HEADER)
        self._invalidate_cache()

    def _invalidate_cache(self):
        """Invalidate the in-memory cache (called after any write)."""
        self._cache = None
        self._cache_mtime = 0.0

    _RULES_HEADER = (
        "# Learned Rules\n\n"
        "Rules extracted by the reflection engine from past corrections.\n"
        "Loaded into the agent prompt at task start.\n\n"
        "---\n\n"
    )

    _STOP_WORDS = frozenset(
        {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "for",
            "to",
            "of",
            "in",
            "on",
            "at",
            "by",
            "with",
            "from",
            "that",
            "this",
            "and",
            "or",
            "not",
            "if",
            "when",
            "before",
            "after",
            "you",
            "your",
            "it",
            "its",
            "be",
            "all",
            "any",
            "every",
            "must",
            "should",
            "can",
            "will",
            "do",
            "have",
            "has",
            "had",
        }
    )

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Tokenize text into a set of lowercase words, stripping punctuation."""
        return {
            w
            for w in re.findall(r"[a-z0-9]+", text.lower())
            if w not in ProceduralMemory._STOP_WORDS
        }

    def _is_duplicate(
        self,
        rule_text: str,
        existing: Optional[list[dict]] = None,
        threshold: float = 0.6,
    ) -> Optional[str]:
        """Check if a rule is too similar to an existing one.

        Uses asymmetric containment: what fraction of the shorter rule's
        meaningful words appear in the longer one?  This catches paraphrases
        and expansions that Jaccard similarity misses.

        Returns the existing rule ID when a duplicate is found, ``None``
        otherwise.
        """
        if existing is None:
            existing = self.get_all_rules()
        if not existing:
            return None

        new_words = self._tokenize(rule_text)
        if not new_words:
            return None

        # Strategy 1: Embedding cosine similarity (semantic dedup, best accuracy)
        # Uses the embedding store's indexed vectors — O(1) query, not O(n) API calls
        if self._embedding_store and self._embedding_store.size > 0:
            try:
                # Query the store for similar rules (vectors already indexed)
                hits = self._embedding_store.query(
                    rule_text,
                    top_k=3,
                    filter_fn=lambda m: m.get("type") == "rule",
                    min_score=0.85,  # Near-duplicate threshold from literature
                )
                if hits:
                    # Found a near-duplicate via stored vectors
                    for hit in hits:
                        rule_id = hit.get("rule_id", "")
                        if rule_id:
                            return rule_id
                    # Fallback: match by text against existing rules
                    hit_text = hits[0].get("text", "")[:80]
                    for rule in existing:
                        if rule.get("text", "")[:80] == hit_text:
                            return rule.get("id")
            except Exception:
                pass  # Fall through to BM25 method

        # Strategy 2: BM25 pre-filter + asymmetric containment (offline fallback)
        # Uses IDF-weighted overlap instead of raw Jaccard
        from .bm25 import BM25Index

        bm25 = BM25Index()
        for rule in existing:
            bm25.add_document(rule.get("text", ""))

        # Get BM25 candidates (rules with term overlap)
        candidates = bm25.query(rule_text, top_k=5)

        for idx, score in candidates:
            if idx >= len(existing):
                continue
            rule = existing[idx]
            existing_words = self._tokenize(rule.get("text", ""))
            if not existing_words:
                continue
            # Asymmetric containment on the filtered candidates only
            shorter, longer = (
                (new_words, existing_words)
                if len(new_words) <= len(existing_words)
                else (existing_words, new_words)
            )
            if not shorter:
                continue
            overlap = len(shorter & longer) / len(shorter)
            if overlap >= threshold:
                return rule.get("id")

        return None

    def add_rule(
        self,
        rule_text: str,
        context: str,
        confidence: float,
        rule_id: Optional[str] = None,
        source_task: str = "",
        dedup: bool = True,
        precondition: str = "",
        repair: str = "",
        effect: str = "",
    ) -> str:
        """Add a new rule. Returns the rule ID.

        When *dedup* is ``True`` (default), checks for similar rules first.
        If a duplicate is found, increments its application count and
        returns the existing rule ID instead of creating a new entry.

        Raises ``ValueError`` if *rule_text* or *context* are empty.
        """
        # Input validation
        if not rule_text or not rule_text.strip():
            raise ValueError("rule_text must be a non-empty string")
        if not context or not context.strip():
            raise ValueError("context must be a non-empty string")

        # Clamp confidence to [0.0, 1.0]
        confidence = min(max(float(confidence), 0.0), 1.0)

        existing = self.get_all_rules()

        # Deduplication: if a similar rule exists, reinforce it instead
        if dedup:
            existing_id = self._is_duplicate(rule_text, existing)
            if existing_id:
                self.increment_application(existing_id)
                return existing_id

        # Contradiction detection: if a rule conflicts, scope-narrow both
        if dedup and existing:
            contradiction_id = self._detect_contradiction(rule_text, context, existing)
            if contradiction_id:
                self._scope_narrow(contradiction_id, context, existing)

        if rule_id is None:
            rule_id = f"R{len(existing) + 1:03d}"

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        topic_tags = self._infer_topic_tags(rule_text, context)

        rule_block = (
            f"## {rule_id}: {rule_text.strip()}\n"
            f"**Learned:** {timestamp}\n"
            f"**Context:** {context.strip()}\n"
            f"**Confidence:** {confidence:.2f}\n"
            f"**Source:** {source_task}\n"
            f"**Times applied:** 0\n"
            f"**Access history:** []\n"
            f"**Topic tags:** {json.dumps(topic_tags)}\n"
            f"**Precondition:** {precondition}\n"
            f"**Repair:** {repair}\n"
            f"**Effect:** {effect}\n"
            f"\n---\n\n"
        )

        try:
            append_text(self.rules_path, rule_block)
        except (OSError, IOError) as e:
            logger.warning("Failed to write rule to %s: %s", self.rules_path, e)

        # Index into embedding store for semantic retrieval
        if self._embedding_store:
            self._embedding_store.add(
                f"{rule_text} | Context: {context}",
                {"type": "rule", "rule_id": rule_id, "confidence": confidence},
            )

        self._invalidate_cache()
        return rule_id

    @staticmethod
    def _strip_md(text: str) -> str:
        """Remove markdown bold markers (**) from a string."""
        return text.replace("**", "").strip()

    _FIELD_ALIASES = {
        "times applied": "times_applied",
        "access history": "access_history",
        "topic tags": "topic_tags",
    }
    _FIELD_MAP = {
        "learned": str,
        "context": str,
        "source": str,
        "confidence": float,
        "utility": float,
        "times_applied": int,
        "precondition": str,
        "repair": str,
        "effect": str,
        "access_history": lambda s: json.loads(s) if s.strip() else [],
        "topic_tags": lambda s: json.loads(s) if s.strip() else [],
    }
    _FIELD_DEFAULTS = {
        "confidence": 0.5,
        "utility": 0.0,
        "times_applied": 0,
        "precondition": "",
        "repair": "",
        "effect": "",
        "access_history": [],
        "topic_tags": [],
    }

    def get_all_rules(self) -> list[dict]:
        """Parse rules.md into structured data. Uses in-memory cache."""
        if not self.rules_path.exists():
            return []

        # Cache hit: return cached rules if file hasn't changed
        try:
            mtime = self.rules_path.stat().st_mtime
            if self._cache is not None and mtime == self._cache_mtime:
                return self._cache
        except OSError:
            pass

        rules = []
        current_rule: dict = {}

        for line in self.rules_path.read_text(encoding="utf-8").split("\n"):
            if line.startswith("## "):
                if current_rule:
                    rules.append(current_rule)
                parts = line[3:].split(": ", 1)
                current_rule = {
                    "id": parts[0] if len(parts) > 1 else "",
                    "text": parts[1] if len(parts) > 1 else parts[0],
                }
            else:
                m = re.match(r"\*\*(.+?):\*\*", line)
                if m and current_rule:
                    key = self._FIELD_ALIASES.get(
                        m.group(1).lower(), m.group(1).lower()
                    )
                    if key in self._FIELD_MAP:
                        try:
                            current_rule[key] = self._FIELD_MAP[key](
                                self._strip_md(line.split(":", 1)[1])
                            )
                        except (ValueError, IndexError):
                            current_rule[key] = self._FIELD_DEFAULTS.get(key)

        if current_rule:
            rules.append(current_rule)

        # Update cache
        self._cache = rules
        try:
            self._cache_mtime = self.rules_path.stat().st_mtime
        except OSError:
            self._cache_mtime = 0.0

        return rules

    def get_rules_for_prompt(self) -> str:
        """Get rules formatted for injection into the agent prompt."""
        rules = self.get_all_rules()
        if not rules:
            return "No learned rules yet. You're starting fresh."

        lines = ["Learned Rules (from past corrections):"]
        for r in rules:
            conf = r.get("confidence", 0.5)
            if conf >= 0.5:
                lines.append(
                    f"- [{r['id']}] {r['text']} (confidence: {conf:.0%}, utility: {r.get('utility', 0.0):+.2f})"
                )

        return "\n".join(lines)

    def get_relevant_rules(self, task: str, top_k: int = 5) -> list[dict]:
        """Retrieve rules most relevant to a task using embedding similarity.

        Falls back to keyword matching if embedding store is unavailable.
        Returns rules sorted by relevance (highest first).
        """
        all_rules = self.get_all_rules()
        if not all_rules:
            return []

        # Try embedding-based retrieval first
        if self._embedding_store and self._embedding_store.size > 0:
            hits = self._embedding_store.query(
                task,
                top_k=top_k,
                filter_fn=lambda m: m.get("type") == "rule",
                min_score=0.3,
            )
            if hits:
                # Map hits back to full rule dicts
                rule_map = {r.get("id"): r for r in all_rules}
                ranked = []
                for hit in hits:
                    rule_id = hit.get("rule_id")
                    if rule_id and rule_id in rule_map:
                        rule = rule_map[rule_id].copy()
                        rule["_relevance_score"] = hit["score"]
                        ranked.append(rule)
                if ranked:
                    return ranked

        # Fallback: keyword overlap (original behavior)
        task_words = self._tokenize(task)
        if not task_words:
            return all_rules[:top_k]

        scored = []
        for rule in all_rules:
            rule_words = self._tokenize(
                f"{rule.get('text', '')} {rule.get('context', '')}"
            )
            if not rule_words:
                continue
            overlap = len(task_words & rule_words) / max(
                len(task_words | rule_words), 1
            )
            if overlap > 0:
                rule_copy = rule.copy()
                rule_copy["_relevance_score"] = overlap
                scored.append(rule_copy)

        scored.sort(key=lambda r: r["_relevance_score"], reverse=True)
        return scored[:top_k]

    def update_utility(
        self, rule_id: str, reward: float, alpha: float = 0.3
    ) -> float | None:
        """Update a rule's utility with a one-step TD-style reward target."""
        rules = self.get_all_rules()
        updated = None
        alpha = min(max(float(alpha), 0.0), 1.0)
        reward = min(max(float(reward), -1.0), 1.0)
        for rule in rules:
            if rule.get("id") == rule_id:
                old = float(rule.get("utility", 0.0))
                rule["utility"] = updated = old + alpha * (reward - old)
                break
        if updated is not None:
            self._rewrite_rules(rules)
        return updated

    def record_outcome(self, task: str, success: bool) -> list[dict]:
        """Apply one Run outcome to every relevant Rule in one transaction."""
        task_words = self._tokenize(task)
        reward = 1.0 if success else -1.0
        applied = []
        rules = self.get_all_rules()
        for rule in rules:
            rule_words = self._tokenize(
                rule.get("text", "") + " " + rule.get("context", "")
            )
            if not rule_words & task_words:
                continue
            rule["times_applied"] = rule.get("times_applied", 0) + 1
            history = rule.get("access_history", [])
            history.append(time.time())
            rule["access_history"] = history[-50:]
            old_utility = float(rule.get("utility", 0.0))
            rule["utility"] = old_utility + 0.3 * (reward - old_utility)
            if rule_id := rule.get("id"):
                applied.append(
                    {"rule_id": rule_id, "utility": float(rule["utility"])}
                )
        if applied:
            self._rewrite_rules(rules)
        return applied

    def boost_confidence(self, rule_id: str, delta: float = 0.1):
        """Adjust a rule's confidence by delta (positive or negative), clamped to [0, 1]."""
        rules = self.get_all_rules()
        for rule in rules:
            if rule.get("id") == rule_id:
                old = float(rule.get("confidence", 0.5))
                rule["confidence"] = min(max(old + delta, 0.0), 1.0)
                self._rewrite_rules(rules)
                return

    def increment_application(self, rule_id: str):
        """Record a rule application with timestamp for ACT-R activation."""
        rules = self.get_all_rules()
        for rule in rules:
            if rule.get("id") == rule_id:
                rule["times_applied"] = rule.get("times_applied", 0) + 1
                # Track access timestamps for ACT-R base-level activation
                history = rule.get("access_history", [])
                history.append(time.time())
                # Keep last 50 accesses to bound memory
                rule["access_history"] = history[-50:]
                self._rewrite_rules(rules)
                return

    def get_rule_count(self) -> int:
        return len(self.get_all_rules())

    def decay_confidence(
        self,
        half_life_cycles: int = 10,
        min_confidence: float = 0.1,
        decay_rate: float | None = None,
    ) -> list[str]:
        """
        ACT-R activation-based decay for rules.

        Uses base-level activation B = ln(Σ tᵢ⁻⁰·⁵) where tᵢ is age of
        the i-th access in days. Rules with no access history fall back to
        the legacy half-life exponential decay.

        Rules with negative utility after 5+ applications are evicted
        immediately regardless of activation.

        Args:
            half_life_cycles: Cycles for confidence to halve (fallback mode)
            min_confidence: Floor below which rules are pruned
            decay_rate: Legacy parameter for backward compat tests.

        Returns list of rule IDs that were pruned.
        """
        if not self.rules_path.exists():
            return []

        min_confidence = min(max(float(min_confidence), 0.0), 1.0)
        rules = self.get_all_rules()
        pruned = []

        for rule in rules:
            # Negative-utility eviction: harmful rules die fast
            if (
                rule.get("times_applied", 0) >= 5
                and float(rule.get("utility", 0.0)) < -0.3
            ):
                pruned.append(rule.get("id", ""))
                continue

            # ACT-R activation-based decay
            access_history = rule.get("access_history", [])
            if access_history and decay_rate is None:
                activation = self._compute_activation(access_history)
                # Map activation to confidence: sigmoid squash to [0, 1]
                # activation of 0 → confidence ~0.5, negative → below 0.5
                new_conf = 1.0 / (1.0 + math.exp(-activation))
                rule["confidence"] = max(new_conf, 0.0)
            elif rule.get("times_applied", 0) == 0:
                # Fallback: legacy decay for rules without access history
                old_conf = float(rule.get("confidence", 0.5))
                if decay_rate is not None:
                    rule["confidence"] = max(old_conf - float(decay_rate), 0.0)
                else:
                    decay_factor = 0.5 ** (1.0 / max(half_life_cycles, 1))
                    rule["confidence"] = max(old_conf * decay_factor, 0.0)

            if rule.get("confidence", 0.5) < min_confidence:
                pruned.append(rule.get("id", ""))

        # Remove pruned rules, rewrite the rest
        kept = [r for r in rules if r.get("id", "") not in pruned]
        self._rewrite_rules(kept)
        return pruned

    @staticmethod
    def _compute_activation(access_history: list[float], d: float = 0.5) -> float:
        """Compute ACT-R base-level activation.

        B = ln(Σ tᵢ⁻ᵈ) where tᵢ is age of i-th access in days.
        d=0.5 is the standard ACT-R decay parameter.

        Returns activation value (can be negative for rarely-accessed rules).
        """
        now = time.time()
        total = 0.0
        for ts in access_history:
            age_days = max((now - ts) / 86400.0, 0.001)  # min 1 minute
            total += age_days ** (-d)
        if total <= 0:
            return -5.0  # Very low activation
        return math.log(total)

    def prune_stale_rules(self, min_confidence: float = 0.1) -> list[str]:
        """Remove rules whose confidence is below the threshold."""
        if not self.rules_path.exists():
            return []

        min_confidence = min(max(float(min_confidence), 0.0), 1.0)
        rules = self.get_all_rules()
        kept = [r for r in rules if r.get("confidence", 0.5) >= min_confidence]
        pruned_ids = [
            r.get("id", "") for r in rules if r.get("confidence", 0.5) < min_confidence
        ]

        if pruned_ids:
            self._rewrite_rules(kept)
        return pruned_ids

    def reset_application_counts(self):
        """Reset all times_applied counters to 0 (for a new decay cycle)."""
        if not self.rules_path.exists():
            return

        rules = self.get_all_rules()
        for rule in rules:
            rule["times_applied"] = 0
        self._rewrite_rules(rules)

    # ─── Topic Tag Inference ─────────────────────────────────────────────────

    _TOPIC_KEYWORDS = {
        "node": ["node", "express", "npm", "javascript", "js"],
        "python": ["python", "flask", "django", "pip", "uvicorn", "gunicorn"],
        "java": ["java", "spring", "maven", "gradle", "jar"],
        "docker": ["docker", "container", "dockerfile", "image"],
        "static": ["static", "html", "nginx", "apache", "cdn"],
        "security": ["security", "sg", "firewall", "port", "ingress", "egress"],
        "networking": ["port", "8080", "80", "443", "3000", "tcp", "http"],
        "deployment": ["deploy", "ecs", "instance", "launch", "provision"],
        "health": ["health", "check", "probe", "liveness", "readiness"],
    }

    @classmethod
    def _infer_topic_tags(cls, rule_text: str, context: str) -> list[str]:
        """Infer topic tags from rule text and context via keyword matching."""
        combined = f"{rule_text} {context}".lower()
        tags = []
        for tag, keywords in cls._TOPIC_KEYWORDS.items():
            if any(kw in combined for kw in keywords):
                tags.append(tag)
        return tags[:5]  # Cap at 5 tags

    def get_rules_by_tags(self, tags: list[str]) -> list[dict]:
        """Return rules that match any of the given topic tags.

        Used by PromptBlockCompiler for pre-filtering before injection.
        Falls back to all rules if no tags provided or no matches found.
        """
        if not tags:
            return self.get_all_rules()
        all_rules = self.get_all_rules()
        tag_set = set(tags)
        matched = [r for r in all_rules if tag_set & set(r.get("topic_tags", []))]
        # Fall back to all rules if no tag matches
        return matched if matched else all_rules

    # ─── Contradiction Detection ─────────────────────────────────────────────

    # Action words: if two rules share context but differ on these, they conflict
    _ACTION_KEYWORDS = frozenset(
        {
            "port",
            "8080",
            "80",
            "3000",
            "443",
            "open",
            "close",
            "create",
            "delete",
            "remove",
            "add",
            "before",
            "after",
            "first",
            "last",
            "always",
            "never",
            "skip",
            "require",
            "optional",
        }
    )

    def _detect_contradiction(
        self, new_rule_text: str, new_context: str, existing: list[dict]
    ) -> Optional[str]:
        """Detect if the new rule contradicts an existing one.

        Contradiction signal: high context overlap + low action overlap.
        Returns the ID of the conflicting rule, or None.
        """
        new_tokens = self._tokenize(new_rule_text)
        new_context_tokens = self._tokenize(new_context)
        new_action_tokens = new_tokens & self._ACTION_KEYWORDS

        for rule in existing:
            rule_tokens = self._tokenize(rule.get("text", ""))
            rule_context_tokens = self._tokenize(rule.get("context", ""))
            rule_action_tokens = rule_tokens & self._ACTION_KEYWORDS

            # Context similarity: high overlap means same domain
            context_union = new_context_tokens | rule_context_tokens
            if not context_union:
                continue
            context_overlap = len(new_context_tokens & rule_context_tokens) / len(
                context_union
            )

            # Action divergence: different action words = potential conflict
            action_union = new_action_tokens | rule_action_tokens
            if not action_union:
                continue
            action_overlap = len(new_action_tokens & rule_action_tokens) / len(
                action_union
            )

            # High context similarity + low action overlap = contradiction
            if context_overlap > 0.3 and action_overlap <= 0.4:
                return rule.get("id")

        return None

    def _scope_narrow(
        self, conflicting_rule_id: str, new_context: str, existing: list[dict]
    ):
        """Narrow the scope of a conflicting rule by adding context as precondition.

        Per ADR-0004: don't delete, scope-narrow. Add the context as a
        precondition so the old rule only fires in its original context.
        """
        for rule in existing:
            if rule.get("id") == conflicting_rule_id:
                old_precondition = rule.get("precondition", "")
                old_context = rule.get("context", "")
                if old_precondition:
                    # Already has a precondition; don't make it more complex
                    return
                # Add the original context as a scoping precondition
                rule["precondition"] = f"when context is: {old_context}"
                self._rewrite_rules(existing)
                logger.info(
                    "Scope-narrowed rule %s with precondition: %s",
                    conflicting_rule_id,
                    rule["precondition"],
                )
                return

    # ─── Rule Persistence ─────────────────────────────────────────────────────

    def _rewrite_rules(self, rules: list[dict]):
        """Rewrite the rules file with the given rules list."""
        blocks = [self._RULES_HEADER]
        for rule in rules:
            access_history = rule.get("access_history", [])
            history_str = json.dumps(access_history[-50:]) if access_history else "[]"
            topic_tags = rule.get("topic_tags", [])
            tags_str = json.dumps(topic_tags) if topic_tags else "[]"
            blocks.append(
                f"## {rule.get('id', '')}: {rule.get('text', '')}\n"
                f"**Learned:** {rule.get('learned', '')}\n"
                f"**Context:** {rule.get('context', '')}\n"
                f"**Confidence:** {rule.get('confidence', 0.5):.2f}\n"
                f"**Utility:** {rule.get('utility', 0.0):.2f}\n"
                f"**Source:** {rule.get('source', '')}\n"
                f"**Times applied:** {rule.get('times_applied', 0)}\n"
                f"**Access history:** {history_str}\n"
                f"**Topic tags:** {tags_str}\n"
                f"**Precondition:** {rule.get('precondition', '')}\n"
                f"**Repair:** {rule.get('repair', '')}\n"
                f"**Effect:** {rule.get('effect', '')}\n"
                f"\n---\n\n"
            )

        try:
            atomic_write_text(self.rules_path, "".join(blocks))
        except (OSError, IOError) as e:
            logger.warning("Failed to rewrite rules to %s: %s", self.rules_path, e)
        self._invalidate_cache()

    def clear(self):
        """Clear all rules (for testing)."""
        self._init_empty()

    def retire_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID. Returns True if found and removed."""
        rules = self.get_all_rules()
        before = len(rules)
        rules = [r for r in rules if r.get("id") != rule_id]
        if len(rules) < before:
            self._rewrite_rules(rules)
            return True
        return False

    def update_rule(
        self,
        rule_id: str,
        *,
        text: str | None = None,
        confidence: float | None = None,
    ) -> bool:
        """Update a Rule's text and Confidence in one persisted mutation."""
        if text is not None and not text.strip():
            return False
        if text is None and confidence is None:
            return False
        rules = self.get_all_rules()
        for rule in rules:
            if rule.get("id") == rule_id:
                if text is not None:
                    rule["text"] = text.strip()[:200]
                if confidence is not None:
                    rule["confidence"] = min(max(float(confidence), 0.0), 1.0)
                self._rewrite_rules(rules)
                return True
        return False

    def pin_rule(self, rule_id: str) -> bool:
        """Pin a rule so it is never decayed or pruned."""
        rules = self.get_all_rules()
        for rule in rules:
            if rule.get("id") == rule_id:
                rule["confidence"] = max(float(rule.get("confidence", 0.5)), 0.95)
                rule["utility"] = max(float(rule.get("utility", 0.0)), 0.5)
                self._rewrite_rules(rules)
                return True
        return False


if __name__ == "__main__":
    pm = ProceduralMemory("/tmp/test_rules.md")
    pm.add_rule(
        "Always configure security group before deploying to ECS",
        "Alibaba Cloud ECS deployment",
        0.95,
        source_task="Deploy web app",
    )
    print(pm.get_rules_for_prompt())
    print(f"\nRules count: {pm.get_rule_count()}")
