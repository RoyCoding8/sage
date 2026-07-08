"""
Reflection Engine — The core innovation.

When a correction is received, this engine:
1. Analyzes what went wrong
2. Extracts a general rule
3. Stores it in procedural memory
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional, Protocol

from .memory.procedural import ProceduralMemory
from .memory.episodic import EpisodicMemory
from .persistence import atomic_write_text
from .security import redact_sensitive


class ModelCallerFn(Protocol):
    """Protocol for model caller — accepts prompt + optional kwargs."""

    def __call__(self, prompt: str, **kwargs) -> str: ...


logger = logging.getLogger(__name__)


class ReflectionEngine:
    """
    Reflection engine that extracts rules from corrections.

    The reflection prompt is a versioned artifact that the engine can
    rewrite (meta-reflection) based on rule quality feedback.
    """

    PROMPT_FILE = Path(__file__).parent / "reflection_prompt_v1.txt"
    _CIDR_PATTERN = re.compile(
        r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)"
        r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}/(?:[0-9]|[12]\d|3[0-2])\b"
    )
    _PORT_CLAUSE_PATTERN = re.compile(
        r"\b(?:tcp|udp|ports?)\b((?:\s+port)?[^.;\n]{0,80})",
        re.IGNORECASE,
    )
    _RESOURCE_ID_PATTERN = re.compile(
        r"\b(?:sg|i|vpc|vsw|eip)-[A-Za-z0-9][A-Za-z0-9-]*\b",
        re.IGNORECASE,
    )
    _NEGATED_CLAUSE_PATTERN = re.compile(
        r"\b(?:do not|don't|never|must not|should not|avoid|without|"
        r"prohibit(?:s|ed|ing)?|forbid(?:s|den|ding)?|"
        r"disallow(?:s|ed|ing)?|deny(?:ing|ied|ies)?|"
        r"exclude(?:s|d|ing)?|not allow(?:ed|s|ing)?|"
        r"not permit(?:ted|s|ting)?|"
        r"block(?:s|ed|ing)?\s+(?:direct\s+)?"
        r"(?:exposure|ingress|traffic|access|ports?|cidrs?)|"
        r"prevent(?:s|ed|ing)?\s+(?:direct\s+)?"
        r"(?:exposure|ingress|traffic|access))\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        procedural: ProceduralMemory,
        episodic: EpisodicMemory,
        model_caller: Optional[ModelCallerFn] = None,
        prompt_path: Optional[str] = None,
    ):
        self.procedural = procedural
        self.episodic = episodic
        self.model_caller = model_caller
        self.prompt_path = Path(prompt_path) if prompt_path else self.PROMPT_FILE
        self._prompt_version = 1
        self._rule_quality_scores: list[float] = []

    def analyze_correction(
        self, task: str, action: str, error: str, correction: str
    ) -> dict:
        prompt = self._build_reflection_prompt(task, action, error, correction)

        try:
            response = self.model_caller(prompt) if self.model_caller else None
            rule = (
                self._parse_reflection_response(response)
                if response
                else self._fallback_rule(task, error, correction)
            )
        except Exception as e:
            # Catch broad Exception because model_caller is a generic callable
            logger.warning(
                "Reflection model call failed, using fallback: %s",
                redact_sensitive(e),
            )
            rule = self._fallback_rule(task, error, correction)

        self._preserve_operational_invariants(correction, rule)

        # Adversarial quality check: verify rule before storing. Exact operational
        # values come directly from the user's correction, so a model verifier
        # may lower their confidence but must not make them disappear from the
        # prompt that enforces those values.
        if not self._verify_rule(rule, self.procedural.get_all_rules()):
            confidence_floor = (
                0.5 if self._extract_operational_invariants(correction) else 0.2
            )
            rule["confidence"] = max(
                rule.get("confidence", 0.5) * 0.5, confidence_floor
            )
            logger.info("Rule failed verification debate, confidence halved")

        rule_id = self.procedural.add_rule(
            rule_text=rule["rule"],
            context=rule["context"],
            confidence=rule["confidence"],
            source_task=task,
            precondition=rule.get("precondition", ""),
            repair=rule.get("repair", ""),
            effect=rule.get("effect", ""),
        )

        self.episodic.log(
            task=task,
            attempt=1,
            outcome="failed",
            error=error,
            correction=correction,
            rule_extracted=rule["rule"],
            rule_id=rule_id,
        )

        return {"rule_id": rule_id, **rule}

    def _build_reflection_prompt(
        self, task: str, action: str, error: str, correction: str
    ) -> str:
        try:
            template = self.prompt_path.read_text()
        except (OSError, IOError):
            template = (
                "Analyze this correction and extract a general rule as JSON with keys: "
                "rule, context, confidence, precondition, repair, effect.\n"
                "Task: {task}\nAction: {action}\nError: {error}\nCorrection: {correction}"
            )
        return template.format(
            task=task, action=action, error=error, correction=correction
        )

    @staticmethod
    def _make_rule(
        rule_text: str = "Rule extraction failed",
        context: str = "general",
        confidence: float = 0.5,
        precondition: str = "",
        repair: str = "",
        effect: str = "",
    ) -> dict[str, str | float]:
        """Build a canonical 6-key rule dict — the only place defaults are set."""
        return {
            "rule": rule_text[:200],
            "context": context,
            "confidence": min(max(confidence, 0.0), 1.0),
            "precondition": precondition,
            "repair": repair,
            "effect": effect,
        }

    @classmethod
    def _extract_operational_invariants(cls, correction: str) -> list[str]:
        """Extract required ports, CIDRs, and cloud resource IDs.

        Explicitly forbidden alternatives are constraints, not values that should
        be promoted into the learned rule as required configuration.
        """
        clauses = re.split(r";|\n+|(?<=[.!?])\s+", correction)
        positive_clauses = []
        for clause in clauses:
            negated = cls._NEGATED_CLAUSE_PATTERN.search(clause)
            if negated:
                clause = clause[: negated.start()]
            if clause.strip():
                positive_clauses.append(clause)
        positive_text = " ".join(positive_clauses)
        cidrs = cls._CIDR_PATTERN.findall(positive_text)
        without_cidrs = cls._CIDR_PATTERN.sub(" ", positive_text)
        ports: list[str] = []
        for match in cls._PORT_CLAUSE_PATTERN.finditer(without_cidrs):
            for value in re.findall(r"\b\d{1,5}\b", match.group(1)):
                if 0 <= int(value) <= 65535:
                    ports.append(value)
        resource_ids = cls._RESOURCE_ID_PATTERN.findall(positive_text)
        return list(dict.fromkeys([*ports, *cidrs, *resource_ids]))

    @classmethod
    def _preserve_operational_invariants(cls, correction: str, rule: dict) -> bool:
        """Ensure operational values from the correction survive generalization.

        The LLM may correctly generalize the strategy while accidentally dropping
        the exact port, CIDR, or resource identifier needed to apply it. Weave any
        missing values into the bounded rule text before persistence.
        """
        invariants = cls._extract_operational_invariants(correction)
        rule_text = str(rule.get("rule", ""))
        missing = [value for value in invariants if value.lower() not in rule_text.lower()]
        if not missing:
            return True

        labels = []
        for value in invariants:
            if cls._CIDR_PATTERN.fullmatch(value):
                labels.append(f"CIDR {value}")
            elif value.isdigit():
                labels.append(f"port {value}")
            else:
                labels.append(f"resource {value}")
        prefix = f"Required parameters: {'; '.join(labels)}. "
        available = max(0, 200 - len(prefix))
        rule["rule"] = prefix + rule_text[:available].strip()
        logger.warning(
            "Reflection rule omitted operational invariants; restored: %s",
            ", ".join(missing),
        )
        return False

    def _parse_reflection_response(self, response: str) -> dict[str, str | float]:
        from .tools.model_caller import ModelCaller

        try:
            parsed = ModelCaller.extract_json(response)
            if parsed and "rule" in parsed:
                return self._make_rule(
                    rule_text=str(parsed["rule"]),
                    context=str(parsed.get("context", "general")),
                    confidence=float(parsed.get("confidence", 0.5)),
                    precondition=str(parsed.get("precondition", "")),
                    repair=str(parsed.get("repair", "")),
                    effect=str(parsed.get("effect", "")),
                )
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(
                "Failed to parse reflection response: %s", redact_sensitive(e)
            )
        return self._make_rule(
            rule_text=response[:200] if response else "Rule extraction failed"
        )

    def _fallback_rule(self, task: str, error: str, correction: str) -> dict:
        return self._make_rule(
            rule_text=f"When {task.lower()}: {correction}",
            context=f"Task: {task}",
            confidence=0.7,
        )

    def _verify_rule(self, rule: dict, existing_rules: list[dict]) -> bool:
        """Adversarial quality check: reject contradictory, overly specific, or vague rules."""
        if not self.model_caller or not existing_rules:
            return True  # Skip check when no model or no existing rules to compare

        existing_summary = "\n".join(
            f"- [{r.get('id')}] {r.get('text', '')}" for r in existing_rules[:10]
        )
        prompt = (
            f"You are a rule quality verifier. Given this proposed rule:\n"
            f'"{rule.get("rule", "")}"\n\n'
            f"And existing rules:\n{existing_summary}\n\n"
            f"Does this rule: 1) Contradict any existing rule? "
            f"2) Is it too specific to one situation to generalize? "
            f"3) Is it too vague to be actionable?\n"
            f"Return ONLY: ACCEPT or REJECT"
        )
        try:
            response = self.model_caller(prompt, max_tokens=20, task_type="execution")
            return "REJECT" not in (response or "").upper()
        except Exception as e:
            logger.debug("Rule verification skipped: %s", e)
            return True

    def record_rule_quality(self, score: float):
        """Record a rule quality score (0-1). Triggers meta-reflection if quality drops."""
        self._rule_quality_scores.append(min(max(score, 0.0), 1.0))
        # If last 5 rules averaged below 0.4, evolve the prompt
        if len(self._rule_quality_scores) >= 5:
            recent_avg = sum(self._rule_quality_scores[-5:]) / 5
            if recent_avg < 0.4:
                self._evolve_prompt()

    @staticmethod
    def _clean_meta_prompt_response(response: str) -> str:
        """Remove an optional Markdown fence around a rewritten prompt."""
        candidate = (response or "").strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines.pop()
            candidate = "\n".join(lines).strip()
        return candidate

    def _evolve_prompt(self):
        """Meta-reflection: ask the LLM to rewrite the reflection prompt itself."""
        if not self.model_caller:
            return
        try:
            current = self.prompt_path.read_text()
        except (OSError, IOError):
            return

        meta_prompt = (
            f"You are a meta-reflection engine. The following reflection prompt has been producing "
            f"low-quality rules (avg score {sum(self._rule_quality_scores[-5:]) / 5:.2f}/1.0).\n\n"
            f"Current prompt:\n---\n{current}\n---\n\n"
            f"Rewrite it to produce better rules. Keep the JSON output format. "
            f"Make it more specific about what makes a good rule. Preserve these "
            f"literal placeholders exactly: {{task}}, {{action}}, {{error}}, and "
            f"{{correction}}. Do not replace them with examples or rename them. "
            f"Return ONLY the raw prompt text, without Markdown fences or commentary."
        )
        try:
            response = self.model_caller(
                meta_prompt, max_tokens=500, task_type="reflection"
            )
            new_prompt = self._clean_meta_prompt_response(response)
            required_placeholders = ("{task}", "{action}", "{error}", "{correction}")
            if len(new_prompt) > 50 and all(
                placeholder in new_prompt for placeholder in required_placeholders
            ):
                # Backup current prompt before overwriting
                backup = self.prompt_path.with_suffix(f".v{self._prompt_version}.bak")
                atomic_write_text(backup, current)
                self._prompt_version += 1
                atomic_write_text(self.prompt_path, new_prompt)
                self._rule_quality_scores.clear()
                logger.info(
                    "Meta-reflection: prompt evolved to v%d (backup: %s)",
                    self._prompt_version,
                    backup.name,
                )
        except Exception as e:
            logger.debug("Meta-reflection failed: %s", e)


if __name__ == "__main__":
    # Test with offline mode
    from memory.procedural import ProceduralMemory
    from memory.episodic import EpisodicMemory

    pm = ProceduralMemory("/tmp/test_reflection_rules.md")
    em = EpisodicMemory("/tmp/test_reflection_ep")

    engine = ReflectionEngine(pm, em, model_caller=None)
    result = engine.analyze_correction(
        task="Deploy web app to ECS",
        action="Created instance but forgot security group",
        error="Connection refused on port 80",
        correction="You need to configure security group rules for port 80 first",
    )
    print(json.dumps(result, indent=2))
    print(f"\nRules in memory: {pm.get_rule_count()}")
