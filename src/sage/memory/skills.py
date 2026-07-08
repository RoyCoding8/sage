"""
Skill Library — Persists successful execution trajectories as reusable skills.

Inspired by Voyager (2023) and SAGE (2025): agents that accumulate executable
procedures from past successes compound capabilities and cut token usage.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sage.memory.collection import JsonDocumentCollection
from sage.persistence import AtomicJsonLines

logger = logging.getLogger(__name__)


class SkillLibrary(JsonDocumentCollection):
    def __init__(self, skills_path: str = "memory/skills.jsonl", embedding_store=None):
        self.skills_path = Path(skills_path)
        self.skills_path.parent.mkdir(parents=True, exist_ok=True)
        self._embedding_store = embedding_store
        self._document = AtomicJsonLines[dict](self.skills_path)

    def set_embedding_store(self, store):
        """Attach an embedding store for semantic skill retrieval."""
        self._embedding_store = store

    def record_skill(
        self,
        task: str,
        app_type: str,
        steps: list[dict],
        tools_used: list[str],
        preconditions: list[str] | None = None,
        policies_applied: list[str] | None = None,
    ) -> dict:
        """Persist a successful trajectory as a named, reusable skill."""
        def append_skill(skills: list[dict]) -> dict:
            numeric_ids = [
                int(skill_id[1:])
                for skill in skills
                if (skill_id := str(skill.get("skill_id", ""))).startswith("S")
                and skill_id[1:].isdigit()
            ]
            skill = {
                "skill_id": f"S{max(numeric_ids, default=0) + 1:03d}",
                "name": self._derive_name(task, app_type),
                "task": task,
                "app_type": app_type,
                "steps": steps,
                "tools_used": tools_used,
                "preconditions": preconditions or [],
                "policies_applied": policies_applied or [],
                "verified": True,
                "times_used": 0,
                "created": datetime.now(timezone.utc).isoformat(),
            }
            skills.append(skill)
            return skill

        skill = self._document.update(append_skill)

        # Index into embedding store
        if self._embedding_store:
            embed_text = f"{task} | {app_type} | tools: {', '.join(tools_used)}"
            self._embedding_store.add(
                embed_text,
                {"type": "skill", "skill_id": skill["skill_id"], "app_type": app_type},
            )

        return skill

    def retrieve(self, task: str, app_type: str = "", limit: int = 1) -> list[dict]:
        """Find the best matching skill using embeddings (with keyword fallback)."""
        # Try embedding-based retrieval
        if self._embedding_store and self._embedding_store.size > 0:
            hits = self._embedding_store.query(
                f"{task} {app_type}",
                top_k=limit,
                filter_fn=lambda m: m.get("type") == "skill",
                min_score=0.4,
            )
            if hits:
                all_skills = {s["skill_id"]: s for s in self.get_all()}
                results = []
                for hit in hits:
                    skill_id = hit.get("skill_id")
                    if skill_id and skill_id in all_skills:
                        results.append(all_skills[skill_id])
                if results:
                    return results

        # Fallback: BM25 scoring (proper TF-IDF, not raw token overlap)
        from .bm25 import BM25Index

        query_text = f"{task} {app_type}"
        bm25 = BM25Index()
        all_skills = self.get_all()
        for skill in all_skills:
            bm25.add_document(
                f"{skill.get('task', '')} {skill.get('app_type', '')} {skill.get('name', '')}"
            )

        hits = bm25.query(query_text, top_k=limit)
        results = []
        for idx, score in hits:
            if score > 0 and idx < len(all_skills):
                results.append(all_skills[idx])
        return results

    def increment_usage(self, skill_id: str):
        """Track how often a skill is reused."""
        def increment(skills: list[dict]) -> None:
            for skill in skills:
                if skill.get("skill_id") == skill_id:
                    skill["times_used"] = skill.get("times_used", 0) + 1
                    return

        self._document.update(increment)

    def _rewrite(self, skills: list[dict]):
        replacement = list(skills)

        def replace(current: list[dict]) -> None:
            current.clear()
            current.extend(replacement)

        self._document.update(replace)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", text.lower())) - {
            "the",
            "a",
            "an",
            "to",
            "of",
            "in",
            "on",
            "for",
            "and",
            "or",
            "is",
        }

    @staticmethod
    def _derive_name(task: str, app_type: str) -> str:
        return f"deploy_{app_type}" if app_type else task[:40].lower().replace(" ", "_")
