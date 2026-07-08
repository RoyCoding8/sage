"""Tests for ACT-R activation, contradiction detection, and topic tags."""

import time
from sage.memory.procedural import ProceduralMemory


class TestACTRActivation:
    """Tests for the ACT-R base-level activation model."""

    def test_compute_activation_recent_accesses(self, tmp_path):
        """Recent accesses produce high activation."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        now = time.time()
        # 5 accesses in the last hour
        history = [now - 60 * i for i in range(5)]
        activation = pm._compute_activation(history)
        assert activation > 0  # Positive activation for recent accesses

    def test_compute_activation_old_accesses(self, tmp_path):
        """Old accesses produce low activation."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        now = time.time()
        # 2 accesses from 30 days ago
        history = [now - 86400 * 30, now - 86400 * 31]
        activation = pm._compute_activation(history)
        # With only old accesses, activation should be low (possibly negative after sigmoid)
        assert activation < 1.0

    def test_activation_increases_with_more_accesses(self, tmp_path):
        """More accesses = higher activation (frequency effect)."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        now = time.time()
        few = [now - 3600]
        many = [now - 3600 * i for i in range(1, 10)]
        assert pm._compute_activation(many) > pm._compute_activation(few)

    def test_activation_increases_with_recency(self, tmp_path):
        """Recent access > old access (recency effect)."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        now = time.time()
        recent = [now - 60]  # 1 minute ago
        old = [now - 86400 * 7]  # 7 days ago
        assert pm._compute_activation(recent) > pm._compute_activation(old)

    def test_decay_uses_activation_when_history_present(self, tmp_path):
        """decay_confidence uses ACT-R activation when access_history is available."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Port 8080 for node", "ECS deploy", 0.9, rule_id="R001")

        # Simulate some accesses
        rules = pm.get_all_rules()
        now = time.time()
        rules[0]["access_history"] = [now - 60, now - 120, now - 180]
        pm._rewrite_rules(rules)

        # Decay should use activation (not legacy half-life)
        pruned = pm.decay_confidence()
        assert "R001" not in pruned

        # Verify confidence was recalculated (not just decayed by half-life)
        rules = pm.get_all_rules()
        conf = float(rules[0]["confidence"])
        # With 3 recent accesses, sigmoid(activation) should be > 0.5
        assert conf > 0.5

    def test_increment_application_records_timestamp(self, tmp_path):
        """increment_application adds a timestamp to access_history."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Test rule", "ctx", 0.9, rule_id="R001")

        pm.increment_application("R001")
        rules = pm.get_all_rules()
        history = rules[0].get("access_history", [])
        assert len(history) == 1
        assert abs(history[0] - time.time()) < 5  # Within 5 seconds

    def test_access_history_persists_across_reads(self, tmp_path):
        """Access history survives serialization/deserialization."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Test rule", "ctx", 0.9, rule_id="R001")
        pm.increment_application("R001")
        pm.increment_application("R001")

        # Force re-read
        pm._invalidate_cache()
        rules = pm.get_all_rules()
        assert len(rules[0].get("access_history", [])) == 2


class TestContradictionDetection:
    """Tests for contradiction detection at rule ingestion."""

    def test_no_contradiction_for_different_contexts(self, tmp_path):
        """Rules in different contexts don't trigger contradiction."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule(
            "Configure security group with port 8080 ingress rule",
            "Node.js ECS deploy",
            0.9,
            rule_id="R001",
        )
        # Different context entirely — no contradiction
        pm.add_rule(
            "Use Dockerfile healthcheck command for container readiness",
            "Docker container orchestration",
            0.9,
            rule_id="R002",
        )
        # Both should exist
        assert pm.get_rule_count() == 2

    def test_contradiction_detected_same_context(self, tmp_path):
        """Rules with same context but different actions trigger scope-narrowing."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule(
            "Always configure port 8080 as the application ingress before deploying",
            "ECS deployment security group configuration",
            0.9,
            rule_id="R001",
        )
        # Same context domain, contradictory action (port 80 vs 8080)
        pm.add_rule(
            "Skip port 8080 entirely and use port 80 for all web traffic",
            "ECS deployment security group configuration",
            0.9,
            rule_id="R002",
        )
        # Both exist (scope-narrow, not delete)
        assert pm.get_rule_count() == 2
        # The first rule should now have a precondition (scope-narrowed)
        rules = pm.get_all_rules()
        r1 = next(r for r in rules if r["id"] == "R001")
        assert r1.get("precondition") != ""

    def test_scope_narrowing_preserves_existing_precondition(self, tmp_path):
        """If a rule already has a precondition, don't make it more complex."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule(
            "Open port 8080",
            "ECS deploy",
            0.9,
            rule_id="R001",
            precondition="when app_type is node",
        )
        # Same context, different action
        pm.add_rule("Open port 80", "ECS deploy", 0.9, rule_id="R002")
        # R001's precondition should be unchanged (already had one)
        rules = pm.get_all_rules()
        r1 = next(r for r in rules if r["id"] == "R001")
        assert r1["precondition"] == "when app_type is node"


class TestTopicTags:
    """Tests for topic tag inference and filtering."""

    def test_infer_tags_from_node_context(self, tmp_path):
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        tags = pm._infer_topic_tags("Open port 8080 for express app", "Node.js deploy")
        assert "node" in tags
        assert "networking" in tags

    def test_infer_tags_from_docker_context(self, tmp_path):
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        tags = pm._infer_topic_tags("Pull container image first", "Docker deployment")
        assert "docker" in tags

    def test_infer_tags_from_security_context(self, tmp_path):
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        tags = pm._infer_topic_tags(
            "Create security group before opening port", "ECS security"
        )
        assert "security" in tags
        assert "networking" in tags

    def test_tags_are_persisted(self, tmp_path):
        """Topic tags survive write/read cycle."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Open port 8080 for express", "Node.js deploy", 0.9, rule_id="R001")

        pm._invalidate_cache()
        rules = pm.get_all_rules()
        tags = rules[0].get("topic_tags", [])
        assert "node" in tags
        assert "networking" in tags

    def test_get_rules_by_tags(self, tmp_path):
        """get_rules_by_tags filters correctly."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Port 8080 for node", "Node.js deploy", 0.9, rule_id="R001")
        pm.add_rule("Pull docker image", "Docker deploy", 0.9, rule_id="R002")
        pm.add_rule("Create security group", "Security setup", 0.9, rule_id="R003")

        node_rules = pm.get_rules_by_tags(["node"])
        assert any(r["id"] == "R001" for r in node_rules)
        # Docker rule should NOT be in node results (unless it also has networking tag)
        docker_rules = pm.get_rules_by_tags(["docker"])
        assert any(r["id"] == "R002" for r in docker_rules)

    def test_get_rules_by_tags_fallback_when_no_matches(self, tmp_path):
        """Falls back to all rules when no tags match."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        pm.add_rule("Some rule", "generic context", 0.9, rule_id="R001")

        rules = pm.get_rules_by_tags(["nonexistent_tag"])
        assert len(rules) == 1  # Falls back to all rules

    def test_topic_tag_cap_at_5(self, tmp_path):
        """Maximum 5 topic tags per rule."""
        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        # This text would match many categories
        tags = pm._infer_topic_tags(
            "Deploy docker node python flask with port 8080 security health check",
            "ECS deploy",
        )
        assert len(tags) <= 5
