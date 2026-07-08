"""Operational interface for Sage's complete memory system."""

from __future__ import annotations


class MemorySystem:
    """Concentrate memory reads, Rule transitions, and maintenance."""

    def __init__(
        self,
        *,
        episodic,
        procedural,
        semantic,
        cases,
        skills,
        provenance,
        preferences,
        session,
        embeddings,
        sqlite,
        context_budget,
        consolidator,
        retrieval,
        lifecycle,
        token_usage,
        metrics,
    ):
        self._episodic = episodic
        self._procedural = procedural
        self._semantic = semantic
        self._cases = cases
        self._skills = skills
        self._provenance = provenance
        self._preferences = preferences
        self._session = session
        self._embeddings = embeddings
        self._sqlite = sqlite
        self._context_budget = context_budget
        self._consolidator = consolidator
        self._retrieval = retrieval
        self._lifecycle = lifecycle
        self._token_usage = token_usage
        self._metrics = metrics

    def snapshot(
        self,
        *,
        recent_limit: int = 5,
        include: set[str] | None = None,
    ) -> dict:
        """Return one operational view of every memory tier."""
        limit = max(1, min(int(recent_limit), 100))
        requested = include or {
            "working",
            "episodic",
            "procedural",
            "semantic",
            "cases",
            "skills",
            "provenance",
            "preferences",
            "session",
            "lifecycle",
            "retrieval",
            "embeddings",
            "sqlite",
            "context_budget",
            "token_usage",
            "metrics",
        }
        state = {}
        if "working" in requested:
            state["working"] = "Current session context"
        if "episodic" in requested:
            state["episodic"] = {
                "stats": self._episodic.get_stats(),
                "recent": self._episodic.get_recent(limit),
            }
        if "procedural" in requested:
            rules = self._procedural.get_all_rules()
            state["procedural"] = {
                "rules": rules,
                "count": len(rules),
                "formatted": self._procedural.get_rules_for_prompt(),
            }
        if "semantic" in requested:
            state["semantic"] = {"documents": self._semantic.list_documents()}
        if "cases" in requested:
            state["cases"] = {
                "stats": self._cases.get_stats(),
                "recent": self._cases.get_recent(limit),
            }
        if "skills" in requested:
            skills = self._skills.get_all()
            state["skills"] = {"items": skills, "count": len(skills)}
        if "provenance" in requested:
            state["provenance"] = {
                "stats": self._provenance.get_stats(),
                "mermaid": self._provenance.to_mermaid(),
            }
        if "preferences" in requested:
            state["preferences"] = {
                "stats": self._preferences.get_stats(),
                "values": self._preferences.get_all(),
            }
        if "session" in requested:
            state["session"] = {
                "current": self._session.get_session_stats(),
                "history": self._session.get_history(),
                "cumulative": self._session.get_cumulative_stats(),
            }
        if "lifecycle" in requested:
            state["lifecycle"] = {
                "memory_health": self._consolidator.get_memory_health(),
            }
        if "retrieval" in requested:
            state["retrieval"] = self._retrieval.get_stats()
        if "embeddings" in requested:
            state["embeddings"] = self._embeddings.get_stats()
        if "sqlite" in requested:
            state["sqlite"] = self._sqlite.get_db_stats()
        if "context_budget" in requested:
            state["context_budget"] = self._context_budget.get_budget_report()
        if "token_usage" in requested:
            state["token_usage"] = self._token_usage()
        if "metrics" in requested:
            state["metrics"] = self._metrics
        return state

    def pin_rule(self, rule_id: str) -> bool:
        changed = self._procedural.pin_rule(rule_id)
        if changed:
            self._retrieval.rebuild()
        return changed

    def retire_rule(self, rule_id: str) -> bool:
        changed = self._procedural.retire_rule(rule_id)
        if changed:
            self._retrieval.rebuild()
        return changed

    def edit_rule(
        self,
        rule_id: str,
        text: str | None = None,
        confidence: float | None = None,
    ) -> bool:
        changed = self._procedural.update_rule(
            rule_id,
            text=text,
            confidence=confidence,
        )
        if changed:
            self._retrieval.rebuild()
        return changed

    def record_rule_outcome(self, task: str, success: bool) -> list[str]:
        applied = self._procedural.record_outcome(task, success)
        for result in applied:
            rule_id = result["rule_id"]
            self._consolidator.track(rule_id, "rule")
            self._consolidator.access(rule_id)
            self._consolidator.update_cognitive_weight(
                rule_id,
                result["utility"],
            )
        return [result["rule_id"] for result in applied]

    def set_preference(
        self,
        category: str,
        value: str,
        *,
        key: str | None = None,
    ) -> dict:
        preference_id = f"{category}.{key}" if key else category
        preference = self._preferences.set_preference(
            preference_id,
            value,
            source="explicit",
        )
        self._retrieval.rebuild()
        return preference

    def refresh(self) -> dict:
        return self._retrieval.rebuild()

    def maintain(self) -> dict:
        report = self._lifecycle.run_maintenance()
        report["retrieval"] = self._retrieval.rebuild()
        return report

    def end_session(self) -> dict:
        return self._session.end()
