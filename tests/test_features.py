"""
Tests for Feature Dev improvements:
  - ProceduralMemory.increment_application() — rule usage counter
"""

import pytest
from sage.memory.procedural import ProceduralMemory


def tmp_path_for_test():
    """Create an isolated temp directory for tests that don't use pytest fixtures."""
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp())


# ─── ProceduralMemory.increment_application ───────────────────────────────────


class TestIncrementApplication:
    def test_increment_from_zero(self, tmp_path):
        """Counter starts at 0 and increments to 1."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Check ports", "networking", 0.9, rule_id="R001")
        rules = pm.get_all_rules()
        assert rules[0]["times_applied"] == 0

        pm.increment_application("R001")
        rules = pm.get_all_rules()
        assert rules[0]["times_applied"] == 1

    def test_increment_multiple_times(self, tmp_path):
        """Counter increments correctly across multiple calls."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule A", "ctx", 0.8, rule_id="R001", dedup=False)
        pm.add_rule("Rule B", "ctx", 0.7, rule_id="R002", dedup=False)

        pm.increment_application("R001")
        pm.increment_application("R001")
        pm.increment_application("R002")

        rules = pm.get_all_rules()
        r1 = next(r for r in rules if r["id"] == "R001")
        r2 = next(r for r in rules if r["id"] == "R002")
        assert r1["times_applied"] == 2
        assert r2["times_applied"] == 1

    def test_increment_only_affects_target_rule(self, tmp_path):
        """Incrementing one rule doesn't touch others."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule A", "ctx", 0.8, rule_id="R001", dedup=False)
        pm.add_rule("Rule B", "ctx", 0.7, rule_id="R002", dedup=False)

        pm.increment_application("R001")

        rules = pm.get_all_rules()
        r1 = next(r for r in rules if r["id"] == "R001")
        r2 = next(r for r in rules if r["id"] == "R002")
        assert r1["times_applied"] == 1
        assert r2["times_applied"] == 0

    def test_increment_nonexistent_rule_no_crash(self, tmp_path):
        """Incrementing a nonexistent rule is a no-op."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule A", "ctx", 0.8, rule_id="R001")
        pm.increment_application("R999")  # should not crash
        rules = pm.get_all_rules()
        assert rules[0]["times_applied"] == 0

    def test_increment_persists_to_disk(self, tmp_path):
        """Counter value is written to rules.md."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule A", "ctx", 0.8, rule_id="R001")
        pm.increment_application("R001")

        content = pm.rules_path.read_text()
        assert "**Times applied:** 1" in content

    def test_increment_after_multiple_adds(self, tmp_path):
        """Increment works correctly with many rules present."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        for i in range(5):
            pm.add_rule(
                f"Rule {i + 1}", f"ctx{i + 1}", 0.5 + i * 0.1, rule_id=f"R{i + 1:03d}"
            )

        pm.increment_application("R003")

        rules = pm.get_all_rules()
        r3 = next(r for r in rules if r["id"] == "R003")
        assert r3["times_applied"] == 1
        # Others unchanged
        r1 = next(r for r in rules if r["id"] == "R001")
        r5 = next(r for r in rules if r["id"] == "R005")
        assert r1["times_applied"] == 0
        assert r5["times_applied"] == 0


class TestRuleDeduplication:
    def test_duplicate_rule_is_not_added(self, tmp_path):
        """A rule too similar to an existing one is not added."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule(
            "Always configure security group before deploying",
            "Cloud deployment",
            0.9,
            rule_id="R001",
            dedup=False,
        )

        # This is very similar — should be deduped
        existing_id = pm.add_rule(
            "Always configure security group rules before deploying",
            "Cloud deployment",
            0.95,
        )

        assert existing_id == "R001"  # returned existing ID, not a new one
        assert pm.get_rule_count() == 1  # still only 1 rule

    def test_dedup_increments_application_count(self, tmp_path):
        """Deduped rule gets its application count incremented."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule(
            "Check ports before deploying",
            "networking",
            0.8,
            rule_id="R001",
            dedup=False,
        )
        assert pm.get_all_rules()[0]["times_applied"] == 0

        # Similar rule — should increment, not create new
        pm.add_rule("Always check ports before deploying", "networking", 0.9)
        assert pm.get_rule_count() == 1
        assert pm.get_all_rules()[0]["times_applied"] == 1

    def test_different_rule_is_added(self, tmp_path):
        """A genuinely different rule is added normally."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Check ports", "networking", 0.8, dedup=False)
        pm.add_rule("Use Docker containers", "deployment", 0.7)

        assert pm.get_rule_count() == 2

    def test_dedup_disabled_always_adds(self, tmp_path):
        """dedup=False always creates a new rule."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Check ports", "ctx", 0.8, dedup=False)
        pm.add_rule("Check ports", "ctx", 0.8, dedup=False)
        assert pm.get_rule_count() == 2

    def test_dedup_with_empty_memory(self, tmp_path):
        """First rule is never a duplicate."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        rule_id = pm.add_rule("Some rule", "ctx", 0.5)
        assert rule_id == "R001"
        assert pm.get_rule_count() == 1

    def test_is_duplicate_threshold(self, tmp_path):
        """Similarity must meet threshold to count as duplicate."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Check ports", "networking", 0.8, dedup=False)

        # Low similarity — different enough to not be a duplicate
        existing_id = pm._is_duplicate("Deploy using Kubernetes orchestration")
        assert existing_id is None  # not a duplicate

        # High similarity — should be a duplicate
        existing_id = pm._is_duplicate("Check ports before deploying to cloud")
        assert existing_id == "R001"

    def test_demo_dedup_reduces_rule_bloat(self, tmp_path):
        """Simulates the demo: 4 similar rules collapse to 1 with dedup."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        corrections = [
            "Always configure security group rules for port 80 before deploying",
            "Always verify and configure necessary network access (e.g., security group or firewall rules) for the ports your application will use before attempting to deploy or start the service.",
            "Always configure required network access (e.g., security group rules for needed ports) before deploying an application.",
            "Always verify and configure required inbound network ports before deploying",
        ]
        for c in corrections:
            pm.add_rule(c, "Cloud deployment", 0.95)

        # With dedup, these should collapse to 1 rule
        # (or very few — the overlap threshold is 0.6)
        count = pm.get_rule_count()
        assert count <= 2, f"Expected ≤2 rules after dedup, got {count}"


class TestRuleConfidenceDecay:
    def test_decay_reduces_confidence(self, tmp_path):
        """Rules with 0 applications lose confidence."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Old rule", "context", 0.8, rule_id="R001")
        pm.add_rule("Used rule", "context", 0.9, rule_id="R002")
        pm.increment_application("R002")

        pm.decay_confidence(decay_rate=0.1)
        rules = pm.get_all_rules()
        r1 = next(r for r in rules if r["id"] == "R001")
        r2 = next(r for r in rules if r["id"] == "R002")
        assert r1["confidence"] == pytest.approx(0.7)
        assert r2["confidence"] == pytest.approx(0.9)  # unchanged

    def test_decay_prunes_stale_rules(self, tmp_path):
        """Rules below min_confidence are removed."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Weak rule", "ctx", 0.15, rule_id="R001")
        pm.add_rule("Strong rule", "ctx", 0.9, rule_id="R002")

        pruned = pm.decay_confidence(decay_rate=0.1, min_confidence=0.1)
        assert "R001" in pruned
        rules = pm.get_all_rules()
        assert len(rules) == 1
        assert rules[0]["id"] == "R002"

    def test_decay_does_not_touch_used_rules(self, tmp_path):
        """Rules with times_applied > 0 are not decayed."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Used rule", "ctx", 0.6, rule_id="R001")
        pm.increment_application("R001")

        pm.decay_confidence(decay_rate=0.2)
        rules = pm.get_all_rules()
        assert rules[0]["confidence"] == pytest.approx(0.6)

    def test_prune_stale_rules(self, tmp_path):
        """prune_stale_rules removes rules below threshold."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Keep", "ctx", 0.5, rule_id="R001")
        pm.add_rule("Prune", "ctx", 0.05, rule_id="R002")

        pruned = pm.prune_stale_rules(min_confidence=0.1)
        assert "R002" in pruned
        rules = pm.get_all_rules()
        assert len(rules) == 1

    def test_reset_application_counts(self, tmp_path):
        """reset_application_counts sets all counters to 0."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule A", "ctx", 0.8, rule_id="R001")
        pm.add_rule("Rule B", "ctx", 0.7, rule_id="R002")
        pm.increment_application("R001")
        pm.increment_application("R001")
        pm.increment_application("R002")

        pm.reset_application_counts()
        rules = pm.get_all_rules()
        for r in rules:
            assert r["times_applied"] == 0

    def test_decay_preserves_all_fields(self, tmp_path):
        """Decay preserves rule metadata (learned, source, etc.)."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Rule", "ctx", 0.8, rule_id="R001", source_task="Test task")

        pm.decay_confidence(decay_rate=0.05)
        rules = pm.get_all_rules()
        r = rules[0]
        assert r["id"] == "R001"
        assert r["text"] == "Rule"
        assert r["context"] == "ctx"
        assert "Test task" in r.get("source", "")

    def test_decay_empty_memory_no_crash(self, tmp_path):
        """Decay on empty memory is a no-op."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pruned = pm.decay_confidence()
        assert pruned == []

    def test_full_decay_cycle(self, tmp_path):
        """Simulate a full lifecycle: add, use some, decay, prune."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        # Add 5 rules
        for i in range(5):
            pm.add_rule(f"Rule {i + 1}", "ctx", 0.5, rule_id=f"R{i + 1:03d}")
        # Use only R003 and R005
        pm.increment_application("R003")
        pm.increment_application("R005")

        # Decay: unused rules lose 0.4 confidence (0.5 -> 0.1)
        pruned = pm.decay_confidence(decay_rate=0.4, min_confidence=0.2)
        # R001, R002, R004 should be pruned (0.5 - 0.4 = 0.1 < 0.2)
        assert "R001" in pruned
        assert "R002" in pruned
        assert "R004" in pruned
        # R003 and R005 should survive (used, not decayed)
        rules = pm.get_all_rules()
        surviving_ids = [r["id"] for r in rules]
        assert "R003" in surviving_ids
        assert "R005" in surviving_ids
        assert len(rules) == 2
