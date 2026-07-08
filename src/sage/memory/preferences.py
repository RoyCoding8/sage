"""
User Preference Memory — Learns and persists user preferences across sessions.

Tracks deployment preferences like:
- Preferred region ("always deploy to us-west-1")
- Instance types ("use cheaper instances for dev")
- Port conventions ("my apps always use port 8080")
- Security posture ("never open 0.0.0.0/0, use my VPN CIDR")
- Naming patterns ("prefix all resources with 'prod-'")

Preferences are learned from:
1. Explicit corrections ("I prefer us-west-1")
2. Observed patterns (3 consecutive deployments to same region)
3. Direct set commands in interactive mode

Storage: JSON file with typed preference entries + confidence scores.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sage.persistence import AtomicJsonDocument

logger = logging.getLogger(__name__)


# Known preference categories and their extraction patterns
PREFERENCE_CATEGORIES = {
    "region": {
        "patterns": [
            r"(?:prefer|always|use|deploy\s+to)\s+([\w-]+-\d+)",
            r"region\s*[:=]\s*([\w-]+-\d+)",
        ],
        "description": "Preferred Alibaba Cloud region",
    },
    "instance_type": {
        "patterns": [
            r"(?:use|prefer)\s+(ecs\.\w+)",
            r"instance[_ ]type\s*[:=]\s*(ecs\.\w+)",
        ],
        "description": "Preferred ECS instance type",
    },
    "port": {
        "patterns": [
            r"(?:always|usually)\s+(?:use|run\s+on)\s+port\s+(\d+)",
            r"my\s+(?:app|service)s?\s+(?:use|run\s+on)\s+port\s+(\d+)",
        ],
        "description": "Preferred application port",
    },
    "security_cidr": {
        "patterns": [
            r"(?:only|restrict|use)\s+(?:cidr|source)\s+([\d./]+)",
            r"(?:vpn|office)\s+(?:cidr|ip)\s+(?:is\s+)?([\d./]+)",
        ],
        "description": "Preferred source CIDR for security groups",
    },
    "naming_prefix": {
        "patterns": [
            r"(?:prefix|name)\s+(?:with|as)\s+['\"]?([\w-]+)",
            r"(?:always|use)\s+prefix\s+['\"]?([\w-]+)",
        ],
        "description": "Resource naming prefix",
    },
}


class PreferenceMemory:
    """
    Persistent user preference store.

    Preferences have:
    - category: what kind of preference (region, instance_type, etc.)
    - value: the preference value
    - confidence: how sure we are (0.0 to 1.0)
    - source: how it was learned (correction, pattern, explicit)
    - times_confirmed: how often this was reinforced
    - last_used: when it was last applied
    """

    def __init__(
        self, prefs_path: str = "memory/preferences.json", user_id: str = "default"
    ):
        self.prefs_path = Path(prefs_path)
        self.prefs_path.parent.mkdir(parents=True, exist_ok=True)
        self.user_id = user_id
        self._document = AtomicJsonDocument(self.prefs_path, self._empty_preferences)

    def _empty_preferences(self) -> dict:
        return {"user_id": self.user_id, "preferences": {}, "history": []}

    def _load(self) -> dict:
        """Load preferences from disk."""
        try:
            data = self._document.read()
            if not isinstance(data, dict):
                return self._empty_preferences()
            data.setdefault("user_id", self.user_id)
            data.setdefault("preferences", {})
            data.setdefault("history", [])
            return data
        except (ValueError, OSError) as e:
            logger.warning("Failed to load preferences: %s", e)
            return self._empty_preferences()

    @staticmethod
    def _apply_preference(
        document: dict,
        category: str,
        value: str,
        source: str,
        confidence: float,
        now: str,
    ) -> dict:
        preferences = document.setdefault("preferences", {})
        existing = preferences.get(category)
        if existing and existing.get("value") == value:
            existing["times_confirmed"] = existing.get("times_confirmed", 0) + 1
            existing["confidence"] = min(existing.get("confidence", 0.5) + 0.1, 1.0)
            existing["last_used"] = now
        else:
            preferences[category] = {
                "value": value,
                "confidence": confidence,
                "source": source,
                "times_confirmed": 1,
                "learned": now,
                "last_used": now,
            }
        document.setdefault("history", []).append(
            {
                "timestamp": now,
                "category": category,
                "value": value,
                "source": source,
            }
        )
        return dict(preferences[category])

    # ─── Public API ──────────────────────────────────────────────────────────

    def set_preference(
        self,
        category: str,
        value: str,
        source: str = "explicit",
        confidence: float = 0.9,
    ) -> dict:
        """Set or update a preference.

        Args:
            category: Preference category (region, instance_type, port, etc.)
            value: The preference value
            source: How it was learned (explicit, correction, pattern)
            confidence: Confidence level (0.0 to 1.0)

        Returns:
            The stored preference entry.
        """
        confidence = min(max(float(confidence), 0.0), 1.0)
        now = datetime.now(timezone.utc).isoformat()
        return self._document.update(
            lambda document: self._apply_preference(
                document, category, value, source, confidence, now
            )
        )

    def get_preference(self, category: str) -> Optional[dict]:
        """Get a preference by category. Returns None if not set."""
        preference = self._load()["preferences"].get(category)
        return dict(preference) if preference is not None else None

    def get_value(self, category: str, default: str = "") -> str:
        """Get just the preference value, with a default fallback."""
        pref = self.get_preference(category)
        if pref and pref.get("confidence", 0) >= 0.3:
            return pref["value"]
        return default

    def get_all(self) -> dict[str, dict]:
        """Get all current preferences."""
        return {
            category: dict(preference)
            for category, preference in self._load()["preferences"].items()
        }

    def extract_preferences_from_text(
        self, text: str, source: str = "correction"
    ) -> list[dict]:
        """Extract preferences from natural language text (corrections, instructions).

        Scans text against known preference patterns and stores matches.
        Returns list of extracted preferences.
        """
        extracted = []
        text_lower = text.lower()

        for category, config in PREFERENCE_CATEGORIES.items():
            for pattern in config["patterns"]:
                match = re.search(pattern, text_lower)
                if match:
                    value = match.group(1)
                    self.set_preference(
                        category=category,
                        value=value,
                        source=source,
                        confidence=0.7,
                    )
                    extracted.append(
                        {
                            "category": category,
                            "value": value,
                            "source": source,
                        }
                    )
                    break  # One match per category per text

        return extracted

    def observe_action(self, category: str, value: str):
        """Record an observed action for pattern detection.

        After 3 consecutive identical observations, auto-set as preference.
        """
        def observe(document: dict) -> None:
            history = document.setdefault("observations", {})
            observations = history.get(category, [])
            observations.append(value)
            history[category] = observations[-5:]

            if len(observations) >= 3 and len(set(observations[-3:])) == 1:
                existing = document.setdefault("preferences", {}).get(category)
                if not existing or existing.get("value") != value:
                    self._apply_preference(
                        document,
                        category,
                        value,
                        "pattern",
                        0.6,
                        datetime.now(timezone.utc).isoformat(),
                    )

        self._document.update(observe)

    def get_context_for_prompt(self) -> str:
        """Format preferences for injection into the system prompt."""
        prefs = self.get_all()
        if not prefs:
            return ""

        lines = ["User Preferences (learned from past interactions):"]
        for category, pref in prefs.items():
            conf = pref.get("confidence", 0.5)
            if conf >= 0.3:
                desc = PREFERENCE_CATEGORIES.get(category, {}).get(
                    "description", category
                )
                lines.append(
                    f"- {desc}: {pref['value']} "
                    f"(confidence: {conf:.0%}, confirmed {pref.get('times_confirmed', 0)}x)"
                )

        return "\n".join(lines) if len(lines) > 1 else ""

    def decay_unused(self, min_confidence: float = 0.2) -> list[str]:
        """Decay preferences that haven't been confirmed recently."""
        def decay(document: dict) -> list[str]:
            pruned = []
            preferences = document.setdefault("preferences", {})
            for category, preference in list(preferences.items()):
                if (
                    preference.get("times_confirmed", 0) <= 1
                    and preference.get("confidence", 0.5) < 0.5
                ):
                    preference["confidence"] = max(
                        preference.get("confidence", 0.5) - 0.1, 0.0
                    )
                    if preference["confidence"] < min_confidence:
                        del preferences[category]
                        pruned.append(category)
            return pruned

        return self._document.update(decay)

    def get_stats(self) -> dict:
        """Get preference store statistics."""
        document = self._load()
        prefs = document["preferences"]
        return {
            "total_preferences": len(prefs),
            "categories": list(prefs.keys()),
            "history_length": len(document.get("history", [])),
            "user_id": self.user_id,
        }

    def clear(self):
        """Clear all preferences."""
        empty = self._empty_preferences()

        def reset(document: dict) -> None:
            document.clear()
            document.update(empty)

        self._document.update(reset)


if __name__ == "__main__":
    pm = PreferenceMemory("/tmp/test_prefs.json")
    pm.set_preference("region", "us-west-1", source="explicit")
    pm.set_preference("port", "8080", source="correction")
    print(pm.get_context_for_prompt())
    print("\nExtracted from text:")
    extracted = pm.extract_preferences_from_text(
        "I always deploy to us-east-1 and my apps use port 3000"
    )
    print(json.dumps(extracted, indent=2))
    print(pm.get_context_for_prompt())
