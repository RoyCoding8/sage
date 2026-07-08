"""
MemoryLifecycleManager — Owns memory maintenance: forgetting, consolidation,
and periodic upkeep.

A deep module: one public method (maybe_run_maintenance), hides Ebbinghaus
decay, episodic consolidation, and rule pruning.
"""

import logging

logger = logging.getLogger(__name__)


class MemoryLifecycleManager:
    """Manages the memory lifecycle: decay, consolidation, index refresh."""

    MAINTENANCE_INTERVAL = 5  # Run maintenance every N tasks

    def __init__(
        self,
        procedural,
        episodic,
        semantic,
        cases,
        skills,
        preferences,
        consolidator,
    ):
        self.procedural = procedural
        self.episodic = episodic
        self.semantic = semantic
        self.cases = cases
        self.skills = skills
        self.preferences = preferences
        self.consolidator = consolidator

    def bootstrap(self):
        """Register persisted memories with the consolidation layer at startup."""
        try:
            for rule in self.procedural.get_all_rules():
                if rule_id := rule.get("id"):
                    self.consolidator.track(rule_id, "rule")
                    self.consolidator.update_cognitive_weight(
                        rule_id, float(rule.get("utility", 0.0))
                    )
            for case in self.cases.get_all():
                if case_id := case.get("case_id"):
                    self.consolidator.track(case_id, "case")
            for skill in self.skills.get_all():
                if skill_id := skill.get("skill_id"):
                    self.consolidator.track(skill_id, "skill")
        except Exception as e:
            logger.warning("Failed to bootstrap memory lifecycle: %s", e)

    def maybe_run_maintenance(self, total_tasks: int):
        """Run maintenance if the interval has elapsed."""
        if total_tasks > 0 and total_tasks % self.MAINTENANCE_INTERVAL == 0:
            self.run_maintenance()

    def run_maintenance(self):
        """Execute full memory maintenance cycle.

        Steps:
        1. Consolidator maintenance (Ebbinghaus forgetting + archival)
        2. Rule confidence decay (half-life = 10 cycles)
        3. Reset application counters
        4. Consolidate episodic memory if large
        5. Detect consolidation candidates (repeated patterns → rules)
        """
        # 1. Ebbinghaus forgetting + archival
        consolidation_report = self.consolidator.run_maintenance()
        if consolidation_report.get("archived"):
            logger.info(
                "Maintenance: archived %d fading memories",
                len(consolidation_report["archived"]),
            )

        # 2. Decay confidence of unused rules
        pruned = self.procedural.decay_confidence(
            half_life_cycles=10, min_confidence=0.15
        )
        if pruned:
            logger.info("Maintenance: pruned %d stale rules: %s", len(pruned), pruned)

        # 3. Reset application counters for next cycle
        self.procedural.reset_application_counts()

        # 4. Consolidate episodic memory
        summary = self.episodic.consolidate(threshold=50)
        if summary:
            self.semantic.append_knowledge("consolidated lessons", summary)
            logger.info("Maintenance: consolidated episodes -> semantic fact")

        # 5. Detect consolidation candidates
        candidates = self.consolidator.find_consolidation_candidates(
            self.episodic.get_recent(20)
        )
        if candidates:
            logger.info(
                "Maintenance: %d consolidation candidates found", len(candidates)
            )

        return {
            "consolidation_report": consolidation_report,
            "pruned_rules": pruned,
            "episodic_consolidated": summary is not None,
            "candidates_found": len(candidates) if candidates else 0,
        }
