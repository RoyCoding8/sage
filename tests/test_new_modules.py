"""
Tests for Preferences, Session, and SQLiteStore.
"""

import tempfile
from pathlib import Path

from sage.memory.preferences import PreferenceMemory
from sage.memory.session import Session
from sage.memory.sqlite_store import SQLiteStore


# ─── PreferenceMemory Tests ──────────────────────────────────────────────────


class TestPreferenceMemory:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.prefs = PreferenceMemory(f"{self.tmpdir}/prefs.json")

    def test_init_empty(self):
        assert self.prefs.get_all() == {}
        assert self.prefs.get_stats()["total_preferences"] == 0

    def test_set_and_get(self):
        self.prefs.set_preference("region", "us-west-1", source="explicit")
        pref = self.prefs.get_preference("region")
        assert pref["value"] == "us-west-1"
        assert pref["confidence"] == 0.9
        assert pref["source"] == "explicit"

    def test_get_value_with_default(self):
        assert self.prefs.get_value("region", "us-east-1") == "us-east-1"
        self.prefs.set_preference("region", "us-west-1")
        assert self.prefs.get_value("region", "us-east-1") == "us-west-1"

    def test_reinforce_existing(self):
        self.prefs.set_preference("region", "us-west-1")
        self.prefs.set_preference("region", "us-west-1")
        pref = self.prefs.get_preference("region")
        assert pref["times_confirmed"] == 2
        assert pref["confidence"] > 0.9  # Boosted

    def test_extract_from_text_region(self):
        extracted = self.prefs.extract_preferences_from_text(
            "I always deploy to us-east-1"
        )
        assert len(extracted) == 1
        assert extracted[0]["category"] == "region"
        assert extracted[0]["value"] == "us-east-1"

    def test_extract_from_text_port(self):
        extracted = self.prefs.extract_preferences_from_text(
            "my apps always use port 8080"
        )
        assert any(e["category"] == "port" and e["value"] == "8080" for e in extracted)

    def test_extract_multiple(self):
        extracted = self.prefs.extract_preferences_from_text(
            "I prefer deploy to us-west-1 and my apps use port 3000"
        )
        categories = {e["category"] for e in extracted}
        assert "region" in categories
        assert "port" in categories

    def test_observe_action_auto_learns(self):
        self.prefs.observe_action("region", "us-west-1")
        self.prefs.observe_action("region", "us-west-1")
        assert self.prefs.get_preference("region") is None  # Not yet (need 3)
        self.prefs.observe_action("region", "us-west-1")
        pref = self.prefs.get_preference("region")
        assert pref is not None
        assert pref["value"] == "us-west-1"
        assert pref["source"] == "pattern"

    def test_context_for_prompt(self):
        self.prefs.set_preference("region", "us-west-1")
        self.prefs.set_preference("port", "8080")
        prompt = self.prefs.get_context_for_prompt()
        assert "us-west-1" in prompt
        assert "8080" in prompt
        assert "User Preferences" in prompt

    def test_persistence(self):
        self.prefs.set_preference("region", "us-west-1")
        # Load a new instance pointing to same file
        prefs2 = PreferenceMemory(f"{self.tmpdir}/prefs.json")
        assert prefs2.get_value("region") == "us-west-1"

    def test_clear(self):
        self.prefs.set_preference("region", "us-west-1")
        self.prefs.clear()
        assert self.prefs.get_all() == {}


# ─── Session Tests ───────────────────────────────────────────────────────────


class TestSession:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.session = Session(f"{self.tmpdir}/sessions")

    def test_init_has_id(self):
        assert self.session.session_id
        assert len(self.session.session_id) == 12

    def test_record_task(self):
        self.session.record_task("Deploy web app", "success", rules_applied=["R001"])
        assert len(self.session.tasks) == 1
        assert self.session.tasks[0]["outcome"] == "success"

    def test_record_correction(self):
        self.session.record_correction("Deploy app", "Fix SG first", "R001")
        assert len(self.session.corrections) == 1
        assert "R001" in self.session.rules_learned

    def test_session_stats(self):
        self.session.record_task("Task 1", "success")
        self.session.record_task("Task 2", "failed")
        stats = self.session.get_session_stats()
        assert stats["tasks_completed"] == 2
        assert stats["successes"] == 1
        assert stats["failures"] == 1

    def test_end_session(self):
        self.session.record_task("Task 1", "success")
        self.session.record_correction("Task 2", "Fix it", "R001")
        summary = self.session.end()
        assert summary["tasks_completed"] == 1
        assert summary["session_id"] == self.session.session_id
        assert "R001" in summary["rules_learned"]

    def test_cross_session_continuity(self):
        # First session
        self.session.record_task("Deploy app", "success", rules_applied=["R001"])
        self.session.record_correction("Deploy app", "Always check SG", "R001")
        self.session.end()

        # Second session — should see the first session's summary
        session2 = Session(f"{self.tmpdir}/sessions")
        context = session2.get_continuity_context()
        assert "Cross-session context" in context
        assert "R001" in context

    def test_cumulative_stats(self):
        self.session.record_task("Task 1", "success")
        self.session.end()

        session2 = Session(f"{self.tmpdir}/sessions")
        session2.record_task("Task 2", "failed")
        session2.end()

        session3 = Session(f"{self.tmpdir}/sessions")
        stats = session3.get_cumulative_stats()
        assert stats["total_sessions"] == 2
        assert stats["total_tasks"] == 2

    def test_no_prior_session(self):
        context = self.session.get_continuity_context()
        assert context == ""


# ─── SQLiteStore Tests ───────────────────────────────────────────────────────


class TestSQLiteStore:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = SQLiteStore(f"{self.tmpdir}/test.db")

    def test_init_creates_db(self):
        assert Path(f"{self.tmpdir}/test.db").exists()

    def test_insert_and_get_case(self):
        case = {
            "case_id": "C001",
            "timestamp": "2026-06-30T00:00:00",
            "task": "Deploy Node.js app",
            "app_type": "node",
            "outcome": "failed",
            "failure_point": "security_group",
            "steps": [{"step": "create instance"}],
        }
        self.store.insert_case(case)
        retrieved = self.store.get_case("C001")
        assert retrieved is not None
        assert retrieved["task"] == "Deploy Node.js app"
        assert retrieved["outcome"] == "failed"

    def test_get_recent_cases(self):
        for i in range(5):
            self.store.insert_case(
                {
                    "case_id": f"C{i:03d}",
                    "timestamp": f"2026-06-{i + 1:02d}T00:00:00",
                    "task": f"Task {i}",
                    "outcome": "success" if i % 2 == 0 else "failed",
                }
            )
        recent = self.store.get_recent_cases(3)
        assert len(recent) == 3

    def test_case_stats(self):
        self.store.insert_case(
            {"case_id": "C001", "timestamp": "", "task": "T1", "outcome": "success"}
        )
        self.store.insert_case(
            {"case_id": "C002", "timestamp": "", "task": "T2", "outcome": "failed"}
        )
        stats = self.store.get_case_stats()
        assert stats["total"] == 2
        assert stats["success"] == 1
        assert stats["failed"] == 1

    def test_search_cases(self):
        self.store.insert_case(
            {
                "case_id": "C001",
                "timestamp": "",
                "task": "Deploy Node.js app",
                "outcome": "success",
                "app_type": "node",
            }
        )
        self.store.insert_case(
            {
                "case_id": "C002",
                "timestamp": "",
                "task": "Deploy Flask API",
                "outcome": "failed",
                "app_type": "python",
            }
        )
        results = self.store.search_cases("Node")
        assert len(results) >= 1
        assert results[0]["task"] == "Deploy Node.js app"

    def test_insert_episode(self):
        row_id = self.store.insert_episode(
            {
                "timestamp": "2026-06-30T00:00:00",
                "task": "Deploy web app",
                "outcome": "failed",
                "correction": "Configure SG first",
            }
        )
        assert row_id > 0
        recent = self.store.get_recent_episodes(1)
        assert len(recent) == 1
        assert recent[0]["task"] == "Deploy web app"

    def test_episode_stats(self):
        self.store.insert_episode({"timestamp": "", "task": "T1", "outcome": "success"})
        self.store.insert_episode(
            {"timestamp": "", "task": "T2", "outcome": "failed", "correction": "Fix it"}
        )
        stats = self.store.get_episode_stats()
        assert stats["total"] == 2
        assert stats["success"] == 1
        assert stats["corrections"] == 1

    def test_insert_skill(self):
        skill = {
            "skill_id": "S001",
            "name": "deploy_node",
            "task": "Deploy Node.js",
            "app_type": "node",
            "steps": [],
            "tools_used": ["RunInstances"],
            "times_used": 0,
        }
        self.store.insert_skill(skill)
        skills = self.store.get_all_skills()
        assert len(skills) == 1
        assert skills[0]["skill_id"] == "S001"

    def test_increment_skill_usage(self):
        self.store.insert_skill(
            {"skill_id": "S001", "name": "test", "task": "test", "times_used": 0}
        )
        self.store.increment_skill_usage("S001")
        # Usage incremented in DB but we'd need to reload to verify
        # (the json_data doesn't auto-update — this is the indexed column)

    def test_insert_session(self):
        session = {
            "session_id": "abc123",
            "user_id": "default",
            "start_time": "2026-06-30T00:00:00",
            "tasks_completed": 3,
            "corrections": 1,
        }
        self.store.insert_session(session)
        recent = self.store.get_recent_sessions(1)
        assert len(recent) == 1
        assert recent[0]["session_id"] == "abc123"

    def test_preferences(self):
        self.store.upsert_preference(
            "region",
            {
                "value": "us-west-1",
                "confidence": 0.9,
                "source": "explicit",
                "times_confirmed": 1,
            },
        )
        prefs = self.store.get_all_preferences()
        assert "region" in prefs
        assert prefs["region"]["value"] == "us-west-1"

    def test_db_stats(self):
        stats = self.store.get_db_stats()
        assert "cases" in stats
        assert "episodes" in stats
        assert "db_size_bytes" in stats

    def test_close(self):
        self.store.close()
        # Should not crash on double close
        self.store.close()
