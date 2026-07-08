"""
Context Budget Manager — Controls how much memory is injected into the prompt.

The MemoryAgent track explicitly requires: "recalling critical memories within
limited context windows." This module ensures we never exceed a token budget
when building the system prompt from memory tiers.

Strategy:
1. Each memory tier gets an allocation (proportional to priority)
2. Within each tier, entries are ranked by relevance score (from embeddings)
3. Entries are added until the tier's budget is exhausted
4. Final prompt is guaranteed to fit within the total budget

Token counting uses a simple heuristic (chars/4) to avoid depending on tiktoken.
For Qwen models, this slightly overestimates — which is safe (conservative).
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Token estimation ────────────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Uses chars/4 heuristic which slightly overestimates for English
    and slightly underestimates for CJK. Good enough for budget enforcement.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


# ─── Budget allocation ───────────────────────────────────────────────────────


@dataclass
class TierBudget:
    """Budget allocation for a single memory tier."""

    name: str
    priority: float  # 0.0 to 1.0 — higher = more budget
    max_tokens: int = 0  # Computed at runtime
    used_tokens: int = 0
    entries: list[str] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self.used_tokens)

    def can_fit(self, text: str) -> bool:
        return estimate_tokens(text) <= self.remaining

    def add(self, text: str) -> bool:
        """Add an entry if it fits within budget. Returns True if added."""
        tokens = estimate_tokens(text)
        if tokens > self.remaining:
            return False
        self.entries.append(text)
        self.used_tokens += tokens
        return True


class ContextBudgetManager:
    """
    Manages token budget allocation across memory tiers.

    Default allocations (total_budget=4000 tokens):
    - Procedural rules: 35% (most critical — learned behaviors)
    - Recent cases: 25% (execution evidence)
    - Episodic context: 20% (recent interaction history)
    - Semantic knowledge: 15% (background knowledge)
    - Skills: 5% (reusable procedures)

    Usage:
        budget = ContextBudgetManager(total_budget=4000)
        prompt = budget.build_memory_prompt(
            rules=ranked_rules,
            cases=ranked_cases,
            episodes=recent_episodes,
            knowledge=retrieved_docs,
            skills=matched_skills,
        )
    """

    # Default tier priorities (sum doesn't need to be 1.0 — normalized internally)
    DEFAULT_ALLOCATIONS = {
        "procedural": 0.35,
        "cases": 0.25,
        "episodic": 0.20,
        "semantic": 0.15,
        "skills": 0.05,
    }

    def __init__(
        self, total_budget: int = 4000, allocations: Optional[dict[str, float]] = None
    ):
        """
        Args:
            total_budget: Maximum tokens to allocate across all tiers.
            allocations: Custom tier priority weights (normalized internally).
        """
        self.total_budget = total_budget
        self.allocations = allocations or self.DEFAULT_ALLOCATIONS

        # Normalize allocations
        total_weight = sum(self.allocations.values())
        if total_weight <= 0:
            total_weight = 1.0
        self._normalized = {k: v / total_weight for k, v in self.allocations.items()}

    def build_memory_prompt(
        self,
        rules: list[dict] = None,
        cases: list[dict] = None,
        episodes: list[dict] = None,
        knowledge: list[tuple] = None,
        skills: list[dict] = None,
    ) -> str:
        """Build a context-budgeted memory prompt from ranked entries.

        Each list should be pre-sorted by relevance (highest first).
        Entries are added until their tier's budget is exhausted.

        Args:
            rules: List of rule dicts (keys: id, text, confidence, utility)
            cases: List of case dicts (keys: task, outcome, failure_point)
            episodes: List of episode dicts (keys: task, outcome, correction)
            knowledge: List of (doc_name, content, score) tuples
            skills: List of skill dicts (keys: name, steps, times_used)

        Returns:
            Formatted string ready for system prompt injection.
        """
        rules = rules or []
        cases = cases or []
        episodes = episodes or []
        knowledge = knowledge or []
        skills = skills or []

        # Create tier budgets
        tiers = {
            name: TierBudget(
                name=name,
                priority=self._normalized.get(name, 0),
                max_tokens=int(self.total_budget * self._normalized.get(name, 0)),
            )
            for name in self.allocations
        }

        # Fill each tier (entries are pre-ranked by relevance)
        self._fill_procedural(tiers["procedural"], rules)
        self._fill_cases(tiers["cases"], cases)
        self._fill_episodic(tiers["episodic"], episodes)
        self._fill_semantic(tiers["semantic"], knowledge)
        self._fill_skills(tiers["skills"], skills)

        # Redistribute unused budget to higher-priority tiers
        self._redistribute_unused(tiers)

        # Build final prompt
        parts = []
        for tier in sorted(tiers.values(), key=lambda t: t.priority, reverse=True):
            if tier.entries:
                parts.extend(tier.entries)

        total_used = sum(t.used_tokens for t in tiers.values())
        logger.debug(
            "Context budget: %d/%d tokens used across %d tiers",
            total_used,
            self.total_budget,
            len(tiers),
        )

        return "\n".join(parts)

    def _fill_procedural(self, tier: TierBudget, rules: list[dict]):
        """Fill procedural tier with ranked rules."""
        if not rules:
            return
        tier.add("Learned Rules (from past corrections):")
        for rule in rules:
            conf = rule.get("confidence", 0.5)
            if conf < 0.3:
                continue  # Skip low-confidence rules
            entry = f"- [{rule.get('id', '?')}] {rule.get('text', '')} (confidence: {conf:.0%}, utility: {rule.get('utility', 0.0):+.2f})"
            if not tier.add(entry):
                break

    def _fill_cases(self, tier: TierBudget, cases: list[dict]):
        """Fill cases tier with recent execution trajectories."""
        if not cases:
            return
        tier.add("\nRecent execution cases:")
        for case in cases:
            entry = f"- [{case.get('case_id', '?')}] {case.get('task', '')} → {case.get('outcome', '?')} ({case.get('failure_point') or 'completed'})"
            if not tier.add(entry):
                break

    def _fill_episodic(self, tier: TierBudget, episodes: list[dict]):
        """Fill episodic tier with recent interactions."""
        if not episodes:
            return
        tier.add("\nRecent experience:")
        for ep in episodes:
            entry = f"- Task: {ep.get('task', '')} → {ep.get('outcome', '?')}"
            if ep.get("correction"):
                entry += f" (corrected: {ep['correction'][:80]})"
            if not tier.add(entry):
                break

    def _fill_semantic(self, tier: TierBudget, knowledge: list[tuple]):
        """Fill semantic tier with retrieved knowledge docs."""
        if not knowledge:
            return
        tier.add("\nKnowledge Base:")
        for doc_name, content, score in knowledge:
            # Truncate long docs to fit budget
            max_chars = tier.remaining * 4  # Convert token budget back to chars
            truncated = content[:max_chars] if len(content) > max_chars else content
            entry = f"\n### {doc_name}\n{truncated}"
            if not tier.add(entry):
                break

    def _fill_skills(self, tier: TierBudget, skills: list[dict]):
        """Fill skills tier with relevant reusable procedures."""
        if not skills:
            return
        tier.add("\nReusable skills:")
        for skill in skills:
            steps_summary = " → ".join(
                s.get("step", s.get("tool", "?"))[:30]
                for s in skill.get("steps", [])[:5]
            )
            entry = f"- {skill.get('name', '?')}: {steps_summary} (used {skill.get('times_used', 0)}x)"
            if not tier.add(entry):
                break

    def _redistribute_unused(self, tiers: dict[str, TierBudget]):
        """Give unused tokens from empty tiers to high-priority ones that overflowed.

        Two-pass: first fill normally, then top up hungry tiers with spare budget.
        """
        # Collect unused budget from tiers that have entries but didn't fill up,
        # or tiers with zero entries
        unused = sum(t.remaining for t in tiers.values())
        if unused <= 0:
            return

        # Find tiers that hit their limit (used >= 90% of allocation)
        hungry = sorted(
            [
                t
                for t in tiers.values()
                if t.used_tokens >= t.max_tokens * 0.9 and t.entries
            ],
            key=lambda t: t.priority,
            reverse=True,
        )

        if not hungry:
            return

        # Distribute unused budget proportionally by priority
        total_priority = sum(t.priority for t in hungry)
        for tier in hungry:
            share = (
                int(unused * (tier.priority / total_priority))
                if total_priority > 0
                else 0
            )
            tier.max_tokens += share
            logger.debug("Redistributed %d tokens to %s tier", share, tier.name)

    def get_budget_report(self) -> dict:
        """Return a summary of budget configuration."""
        return {
            "total_budget": self.total_budget,
            "allocations": {
                name: {
                    "weight": weight,
                    "tokens": int(self.total_budget * weight),
                }
                for name, weight in self._normalized.items()
            },
        }


if __name__ == "__main__":
    budget = ContextBudgetManager(total_budget=4000)
    print(f"Budget report: {budget.get_budget_report()}")

    # Example with sample data
    sample_rules = [
        {
            "id": "R001",
            "text": "Configure security group before deploying",
            "confidence": 0.95,
            "utility": 0.8,
        },
        {
            "id": "R002",
            "text": "Check port availability before binding",
            "confidence": 0.7,
            "utility": 0.3,
        },
    ]
    sample_cases = [
        {
            "case_id": "C001",
            "task": "Deploy Node.js app",
            "outcome": "success",
            "failure_point": None,
        },
    ]

    prompt = budget.build_memory_prompt(rules=sample_rules, cases=sample_cases)
    print(f"\nGenerated prompt ({estimate_tokens(prompt)} tokens):")
    print(prompt)
