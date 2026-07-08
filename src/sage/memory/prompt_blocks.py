"""
Prompt Blocks — Named, inspectable sections for the live memory prompt.

Each block has a name, a token budget, and a builder function. The prompt
compiler assembles them into the final memory string injected into the
agent loop. Every block is independently visible in the UI.

Block types:
  - org_facts: stable environment conventions (ports, regions, constraints)
  - preferences: user/environment preferences
  - runbook_rules: learned procedural rules from corrections
  - recent_failures: short-lived reminders from recent failed runs
  - relevant_skill: matched skill trajectory summary
  - similar_case: relevant past case summary
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MemoryBlock:
    """A single named memory block ready for prompt injection."""

    name: str
    content: str
    source_ids: list[str] = field(default_factory=list)
    token_estimate: int = 0

    @property
    def empty(self) -> bool:
        return not self.content.strip()


@dataclass
class CompiledPrompt:
    """The result of compiling all memory blocks into a prompt string."""

    blocks: list[MemoryBlock]
    full_text: str
    total_tokens: int = 0

    def get_block(self, name: str) -> Optional[MemoryBlock]:
        for b in self.blocks:
            if b.name == name:
                return b
        return None

    def injected_ids(self) -> list[str]:
        ids = []
        for b in self.blocks:
            ids.extend(b.source_ids)
        return ids

    def summary(self) -> dict:
        return {
            "blocks": [
                {
                    "name": b.name,
                    "chars": len(b.content),
                    "ids": b.source_ids,
                    "empty": b.empty,
                }
                for b in self.blocks
            ],
            "total_chars": len(self.full_text),
            "total_tokens": self.total_tokens,
        }


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return max(len(text) // 4, 0)


class PromptBlockCompiler:
    """
    Assembles named memory blocks into the runtime prompt.

    Usage:
        compiler = PromptBlockCompiler(procedural=..., preferences=..., ...)
        compiled = compiler.compile(task="Deploy Node.js app", app_type="node")
        # compiled.full_text -> inject into agent loop
        # compiled.blocks -> inspect in UI
    """

    # Maximum chars per block (soft limit, not enforced by truncation)
    BLOCK_LIMITS = {
        "org_facts": 600,
        "preferences": 300,
        "runbook_rules": 1200,
        "recent_failures": 400,
        "relevant_skill": 400,
        "similar_case": 400,
    }

    def __init__(
        self, procedural=None, preferences=None, cases=None, skills=None, episodic=None
    ):
        self.procedural = procedural
        self.preferences = preferences
        self.cases = cases
        self.skills = skills
        self.episodic = episodic

    def compile(
        self,
        task: str = "",
        app_type: str = "",
        retrieved: Optional[list] = None,
    ) -> CompiledPrompt:
        blocks = [
            self._build_org_facts(task, app_type),
            self._build_preferences(),
            self._build_runbook_rules(task, app_type, retrieved),
            self._build_recent_failures(task),
            self._build_relevant_skill(task, app_type, retrieved),
            self._build_similar_case(task, app_type, retrieved),
        ]

        # Filter empty blocks
        active_blocks = [b for b in blocks if not b.empty]

        parts = []
        for block in active_blocks:
            parts.append(f"--- {block.name.upper()} ---")
            parts.append(block.content.strip())
            parts.append("")

        full_text = "\n".join(parts).strip()
        total_tokens = _estimate_tokens(full_text)

        return CompiledPrompt(
            blocks=active_blocks,
            full_text=full_text,
            total_tokens=total_tokens,
        )

    def _build_org_facts(self, task: str, app_type: str) -> MemoryBlock:
        """Stable environment conventions extracted from learned rules.

        Org facts are rules with high confidence and high utility that describe
        environment-level truths rather than one-off fixes.
        """
        if not self.procedural:
            return MemoryBlock(name="org_facts", content="")

        rules = self.procedural.get_all_rules()
        org_rules = [
            r
            for r in rules
            if float(r.get("confidence", 0)) >= 0.8
            and int(r.get("times_applied", 0)) >= 1
        ]

        if not org_rules:
            return MemoryBlock(name="org_facts", content="")

        lines = ["Organization conventions (high-confidence, verified):"]
        ids = []
        for r in org_rules[:5]:
            lines.append(f"- {r.get('text', '').strip()}")
            if r.get("id"):
                ids.append(r["id"])

        content = "\n".join(lines)[: self.BLOCK_LIMITS["org_facts"]]
        return MemoryBlock(
            name="org_facts",
            content=content,
            source_ids=ids,
            token_estimate=_estimate_tokens(content),
        )

    def _build_preferences(self) -> MemoryBlock:
        """User and environment preferences."""
        if not self.preferences:
            return MemoryBlock(name="preferences", content="")

        context = self.preferences.get_context_for_prompt()
        if not context or not context.strip():
            return MemoryBlock(name="preferences", content="")

        content = context.strip()[: self.BLOCK_LIMITS["preferences"]]
        return MemoryBlock(
            name="preferences",
            content=content,
            token_estimate=_estimate_tokens(content),
        )

    def _build_runbook_rules(
        self,
        task: str = "",
        app_type: str = "",
        retrieved: Optional[list] = None,
    ) -> MemoryBlock:
        """Learned rules from past corrections, pre-filtered by topic tags.

        Uses topic_tags for O(1) pre-filtering when rules exceed 20.
        Falls back to all rules for small rule sets.
        """
        if not self.procedural:
            return MemoryBlock(name="runbook_rules", content="")

        all_rules = self.procedural.get_all_rules()
        if not all_rules:
            return MemoryBlock(name="runbook_rules", content="")

        if retrieved is not None:
            rules = [
                result.metadata
                for result in retrieved
                if result.memory_type == "rule"
            ]
        elif len(all_rules) > 20 and (task or app_type):
            rules = self.procedural.get_relevant_rules(
                f"{task} {app_type}".strip(),
                top_k=20,
            )
        else:
            rules = all_rules

        lines = ["Learned rules from past corrections:"]
        ids = []
        for r in rules:
            text = r.get("text", "").strip()
            if text:
                lines.append(f"- {text}")
                if r.get("id"):
                    ids.append(r["id"])

        content = "\n".join(lines)[: self.BLOCK_LIMITS["runbook_rules"]]
        return MemoryBlock(
            name="runbook_rules",
            content=content,
            source_ids=ids,
            token_estimate=_estimate_tokens(content),
        )

    def _build_recent_failures(self, task: str) -> MemoryBlock:
        """Short-lived reminders from recent failed runs."""
        if not self.cases:
            return MemoryBlock(name="recent_failures", content="")

        recent = self.cases.get_recent(5)
        failures = [c for c in recent if c.get("outcome") == "failed"]
        if not failures:
            return MemoryBlock(name="recent_failures", content="")

        lines = ["Recent failures to avoid repeating:"]
        ids = []
        for f in failures[-3:]:
            error = f.get("error") or f.get("failure_point") or "unknown"
            lines.append(f"- {f.get('task', '?')}: {error}")
            if f.get("case_id"):
                ids.append(f["case_id"])

        content = "\n".join(lines)[: self.BLOCK_LIMITS["recent_failures"]]
        return MemoryBlock(
            name="recent_failures",
            content=content,
            source_ids=ids,
            token_estimate=_estimate_tokens(content),
        )

    def _build_relevant_skill(
        self,
        task: str,
        app_type: str,
        retrieved: Optional[list] = None,
    ) -> MemoryBlock:
        """Matched skill trajectory summary."""
        if not self.skills:
            return MemoryBlock(name="relevant_skill", content="")

        matches = (
            [
                result.metadata
                for result in retrieved
                if result.memory_type == "skill"
            ][:1]
            if retrieved is not None
            else self.skills.retrieve(task, app_type, limit=1)
        )
        if not matches:
            return MemoryBlock(name="relevant_skill", content="")

        skill = matches[0]
        steps = skill.get("steps", [])
        step_summary = ", ".join(s.get("step", s.get("tool", "?")) for s in steps[:6])
        content = (
            f"Similar successful deployment ({skill.get('name', 'unknown')}):\n"
            f"  Steps: {step_summary}\n"
            f"  Tools: {', '.join(skill.get('tools_used', []))}"
        )
        content = content[: self.BLOCK_LIMITS["relevant_skill"]]
        ids = [skill["skill_id"]] if skill.get("skill_id") else []
        return MemoryBlock(
            name="relevant_skill",
            content=content,
            source_ids=ids,
            token_estimate=_estimate_tokens(content),
        )

    def _build_similar_case(
        self,
        task: str,
        app_type: str,
        retrieved: Optional[list] = None,
    ) -> MemoryBlock:
        """Relevant past case summary."""
        if not self.cases:
            return MemoryBlock(name="similar_case", content="")

        matches = (
            [
                result.metadata
                for result in retrieved
                if result.memory_type == "case"
                and result.metadata.get("outcome") == "success"
            ][:1]
            if retrieved is not None
            else self.cases.retrieve(task, limit=1, outcome="success")
        )
        if not matches:
            return MemoryBlock(name="similar_case", content="")

        case = matches[0]
        tools = case.get("tools_used", [])
        content = (
            f"Similar past success ({case.get('task', '?')}):\n"
            f"  Outcome: {case.get('outcome')}\n"
            f"  Tools used: {', '.join(tools)}"
        )
        content = content[: self.BLOCK_LIMITS["similar_case"]]
        ids = [case["case_id"]] if case.get("case_id") else []
        return MemoryBlock(
            name="similar_case",
            content=content,
            source_ids=ids,
            token_estimate=_estimate_tokens(content),
        )
