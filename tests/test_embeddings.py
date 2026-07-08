"""
Tests for EmbeddingStore and ContextBudgetManager.

Tests cover:
- EmbeddingStore initialization, add, query, forget, persistence
- Graceful degradation when numpy/API unavailable
- ContextBudgetManager budget allocation and overflow handling
"""

import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import pytest

from sage.memory.embeddings import EmbeddingStore, HAS_NUMPY
from sage.memory.context_budget import ContextBudgetManager, estimate_tokens, TierBudget


# ─── EmbeddingStore Tests ────────────────────────────────────────────────────


class TestEmbeddingStore:
    """Tests for the vector-backed embedding store."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = EmbeddingStore(
            store_dir=self.tmpdir,
            api_key="test-key-fake",
        )

    def test_init_creates_directory(self):
        assert Path(self.tmpdir).exists()

    def test_init_empty_store(self):
        assert self.store.size == 0
        stats = self.store.get_stats()
        assert stats["entries"] == 0
        assert stats["dimensions"] == 1024
        assert stats["model"] == "text-embedding-v4"

    def test_add_empty_text_returns_false(self):
        assert self.store.add("", {}) is False
        assert self.store.add("   ", {}) is False

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_add_with_mock_api(self):
        """Test add with a mocked embedding API response."""
        import numpy as np

        fake_vector = np.random.randn(1024).tolist()
        mock_response = {
            "data": [{"embedding": fake_vector, "index": 0}],
            "usage": {"total_tokens": 10},
        }

        with patch.object(self.store, "_http_post", return_value=mock_response):
            result = self.store.add(
                "Configure security group before deployment",
                {"type": "rule", "id": "R001"},
            )
            assert result is True
            assert self.store.size == 1

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_query_empty_store_returns_empty(self):
        results = self.store.query("some query")
        assert results == []

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_query_with_stored_vectors(self):
        """Test query returns ranked results by cosine similarity."""
        import numpy as np

        # Use deterministic vectors with known similarity
        v1 = np.zeros(1024, dtype=np.float32)
        v1[0] = 1.0  # Unit vector along dim 0
        v2 = np.zeros(1024, dtype=np.float32)
        v2[0] = 0.7
        v2[1] = 0.714  # Mostly aligned with v1, partially orthogonal
        v2 = v2 / np.linalg.norm(v2)

        self.store._vectors = np.vstack([v1, v2])
        self.store._metadata = [
            {"text": "security group config", "type": "rule", "rule_id": "R001"},
            {"text": "deploy flask app", "type": "case", "case_id": "C001"},
        ]

        # Query vector same as v1 → v1 gets score 1.0, v2 gets ~0.7
        query_vec = v1.tolist()
        mock_response = {
            "data": [{"embedding": query_vec, "index": 0}],
            "usage": {"total_tokens": 5},
        }

        with patch.object(self.store, "_http_post", return_value=mock_response):
            results = self.store.query("security group")
            assert len(results) == 2
            # First result should be the one most similar to query
            assert results[0]["score"] >= results[1]["score"]
            assert results[0]["text"] == "security group config"

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_query_with_filter(self):
        """Test that filter_fn restricts results."""
        import numpy as np

        v1 = np.random.randn(1024).astype(np.float32)
        v1 = v1 / np.linalg.norm(v1)
        v2 = np.random.randn(1024).astype(np.float32)
        v2 = v2 / np.linalg.norm(v2)

        self.store._vectors = np.vstack([v1, v2])
        self.store._metadata = [
            {"text": "rule text", "type": "rule", "rule_id": "R001"},
            {"text": "case text", "type": "case", "case_id": "C001"},
        ]

        mock_response = {
            "data": [{"embedding": v1.tolist(), "index": 0}],
            "usage": {"total_tokens": 5},
        }

        with patch.object(self.store, "_http_post", return_value=mock_response):
            results = self.store.query(
                "test", filter_fn=lambda m: m.get("type") == "rule"
            )
            assert all(r.get("type") == "rule" for r in results)

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_query_with_min_score(self):
        """Test min_score threshold filters low-similarity results."""
        import numpy as np

        v1 = np.array([1.0] + [0.0] * 1023, dtype=np.float32)
        v2 = np.array([0.0, 1.0] + [0.0] * 1022, dtype=np.float32)

        self.store._vectors = np.vstack([v1, v2])
        self.store._metadata = [
            {"text": "high match", "type": "rule"},
            {"text": "low match", "type": "rule"},
        ]

        # Query vector aligned with v1 → high score for v1, low for v2
        query_vec = [1.0] + [0.0] * 1023
        mock_response = {
            "data": [{"embedding": query_vec, "index": 0}],
            "usage": {"total_tokens": 5},
        }

        with patch.object(self.store, "_http_post", return_value=mock_response):
            results = self.store.query("test", min_score=0.5)
            # Only v1 should pass the threshold
            assert len(results) == 1
            assert results[0]["text"] == "high match"

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_forget_removes_entries(self):
        """Test forget with a filter predicate."""
        import numpy as np

        v1 = np.random.randn(1024).astype(np.float32)
        v1 = v1 / np.linalg.norm(v1)
        v2 = np.random.randn(1024).astype(np.float32)
        v2 = v2 / np.linalg.norm(v2)

        self.store._vectors = np.vstack([v1, v2])
        self.store._metadata = [
            {"text": "old rule", "type": "rule", "rule_id": "R001"},
            {"text": "new case", "type": "case", "case_id": "C001"},
        ]

        removed = self.store.forget(lambda m: m.get("type") == "rule")
        assert removed == 1
        assert self.store.size == 1
        assert self.store._metadata[0]["type"] == "case"

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_persistence_save_and_load(self):
        """Test that vectors persist across store instances."""
        import numpy as np

        v1 = np.random.randn(1024).astype(np.float32)
        v1 = v1 / np.linalg.norm(v1)

        self.store._vectors = v1.reshape(1, -1)
        self.store._metadata = [{"text": "test", "type": "rule"}]
        self.store._save()

        # Create new store pointing to same dir
        store2 = EmbeddingStore(store_dir=self.tmpdir, api_key="test")
        assert store2.size == 1
        assert store2._metadata[0]["text"] == "test"
        np.testing.assert_allclose(store2._vectors[0], v1, rtol=1e-5)

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_vector_and_metadata_snapshot_cannot_split(self):
        """A save exposes one aligned snapshot, never half of two files."""
        import builtins

        import numpy as np

        self.store._vectors = np.ones((1, 1024), dtype=np.float32)
        self.store._metadata = [{"text": "first"}]
        self.store._save()

        self.store._vectors = np.ones((2, 1024), dtype=np.float32)
        self.store._metadata = [{"text": "first"}, {"text": "second"}]
        real_open = builtins.open

        def fail_legacy_metadata_write(file, mode="r", *args, **kwargs):
            if Path(file) == self.store._metadata_path and "w" in mode:
                raise OSError("simulated metadata write failure")
            return real_open(file, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=fail_legacy_metadata_write):
            self.store._save()

        reloaded = EmbeddingStore(store_dir=self.tmpdir, api_key="test")
        assert reloaded.size == 2
        assert len(reloaded._metadata) == 2

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_concurrent_store_instances_preserve_every_embedding(self):
        workers = 8
        start = threading.Barrier(workers)
        stores = [
            EmbeddingStore(store_dir=self.tmpdir, api_key="test")
            for _ in range(workers)
        ]
        vector = [1.0] + [0.0] * 1023
        for store in stores:
            store._embed = lambda texts: [vector for _ in texts]

        def add(index: int) -> None:
            start.wait()
            assert stores[index].add(f"entry-{index}", {"index": index})

        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(add, range(workers)))

        reloaded = EmbeddingStore(store_dir=self.tmpdir, api_key="test")
        assert reloaded.size == workers
        assert {metadata["index"] for metadata in reloaded._metadata} == set(
            range(workers)
        )

    def test_clear_removes_all(self):
        self.store._metadata = [{"text": "test"}]
        self.store.clear()
        assert self.store.size == 0

    def test_no_api_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("SAGE_QWEN_API_KEY", raising=False)
        store = EmbeddingStore(store_dir=self.tmpdir, api_key="")
        result = store._embed(["test"])
        assert result is None

    def test_api_failure_returns_none(self):
        """Test graceful handling of API errors."""
        with patch.object(
            self.store, "_http_post", side_effect=RuntimeError("timeout")
        ):
            result = self.store._embed(["test text"])
            assert result is None

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_batch_add(self):
        """Test adding multiple entries at once."""
        import numpy as np

        fake_vectors = [np.random.randn(1024).tolist() for _ in range(3)]
        mock_response = {
            "data": [{"embedding": v, "index": i} for i, v in enumerate(fake_vectors)],
            "usage": {"total_tokens": 30},
        }

        with patch.object(self.store, "_http_post", return_value=mock_response):
            result = self.store.add_batch(
                ["text1", "text2", "text3"],
                [{"id": "1"}, {"id": "2"}, {"id": "3"}],
            )
            assert result is True
            assert self.store.size == 3


# ─── ContextBudgetManager Tests ──────────────────────────────────────────────


class TestContextBudgetManager:
    """Tests for the context window budget manager."""

    def test_estimate_tokens(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("hello") == 1  # 5 chars / 4 = 1
        assert estimate_tokens("a" * 100) == 25  # 100 / 4

    def test_default_allocations(self):
        budget = ContextBudgetManager(total_budget=4000)
        report = budget.get_budget_report()
        assert report["total_budget"] == 4000
        assert "procedural" in report["allocations"]
        assert "cases" in report["allocations"]

    def test_empty_prompt(self):
        budget = ContextBudgetManager(total_budget=4000)
        prompt = budget.build_memory_prompt()
        # With no data, prompt should be minimal
        assert len(prompt) < 100

    def test_rules_included_in_prompt(self):
        budget = ContextBudgetManager(total_budget=4000)
        rules = [
            {
                "id": "R001",
                "text": "Configure security group first",
                "confidence": 0.95,
                "utility": 0.8,
            },
            {
                "id": "R002",
                "text": "Check ports before binding",
                "confidence": 0.7,
                "utility": 0.3,
            },
        ]
        prompt = budget.build_memory_prompt(rules=rules)
        assert "R001" in prompt
        assert "security group" in prompt

    def test_low_confidence_rules_excluded(self):
        budget = ContextBudgetManager(total_budget=4000)
        rules = [
            {"id": "R001", "text": "Good rule", "confidence": 0.9, "utility": 0.5},
            {"id": "R002", "text": "Bad rule", "confidence": 0.2, "utility": -0.3},
        ]
        prompt = budget.build_memory_prompt(rules=rules)
        assert "Good rule" in prompt
        assert "Bad rule" not in prompt

    def test_budget_overflow_truncates(self):
        """Test that exceeding budget causes truncation."""
        budget = ContextBudgetManager(total_budget=50)  # Very tight budget
        rules = [
            {
                "id": f"R{i:03d}",
                "text": f"Rule number {i} with some long text about deployment patterns "
                * 3,
                "confidence": 0.9,
                "utility": 0.5,
            }
            for i in range(20)
        ]
        prompt = budget.build_memory_prompt(rules=rules)
        # Should not include all 20 rules due to budget
        tokens = estimate_tokens(prompt)
        # With redistribution, might go slightly over, but should be reasonable
        assert tokens < 200  # Well under what 20 full rules would cost

    def test_cases_in_prompt(self):
        budget = ContextBudgetManager(total_budget=4000)
        cases = [
            {
                "case_id": "C001",
                "task": "Deploy Flask API",
                "outcome": "success",
                "failure_point": None,
            },
        ]
        prompt = budget.build_memory_prompt(cases=cases)
        assert "C001" in prompt
        assert "Flask" in prompt

    def test_episodes_with_corrections(self):
        budget = ContextBudgetManager(total_budget=4000)
        episodes = [
            {
                "task": "Deploy web app",
                "outcome": "failed",
                "correction": "Add security group first",
            },
        ]
        prompt = budget.build_memory_prompt(episodes=episodes)
        assert "Deploy web app" in prompt
        assert "corrected" in prompt

    def test_knowledge_in_prompt(self):
        budget = ContextBudgetManager(total_budget=4000)
        knowledge = [
            ("alibaba-cloud.md", "ECS is virtual machines on Alibaba Cloud", 0.8),
        ]
        prompt = budget.build_memory_prompt(knowledge=knowledge)
        assert "alibaba-cloud.md" in prompt
        assert "ECS" in prompt

    def test_custom_allocations(self):
        budget = ContextBudgetManager(
            total_budget=1000,
            allocations={
                "procedural": 1.0,
                "cases": 0.0,
                "episodic": 0.0,
                "semantic": 0.0,
                "skills": 0.0,
            },
        )
        report = budget.get_budget_report()
        # Procedural should get all budget
        assert report["allocations"]["procedural"]["tokens"] == 1000

    def test_tier_budget_can_fit(self):
        tier = TierBudget(name="test", priority=1.0, max_tokens=100)
        assert tier.can_fit("short text")  # ~2 tokens
        assert not tier.can_fit("x" * 500)  # ~125 tokens, over budget

    def test_tier_budget_add(self):
        tier = TierBudget(name="test", priority=1.0, max_tokens=50)
        assert tier.add("hello world") is True
        assert tier.used_tokens > 0
        assert tier.remaining < 50


# ─── Integration Tests ───────────────────────────────────────────────────────


class TestEmbeddingIntegration:
    """Test that memory modules correctly use the embedding store."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_procedural_adds_to_embedding_store(self):
        """Test that adding a rule also indexes into embedding store."""
        import numpy as np
        from sage.memory.procedural import ProceduralMemory

        store = EmbeddingStore(store_dir=f"{self.tmpdir}/vectors", api_key="test")

        fake_vector = np.random.randn(1024).tolist()
        mock_response = {
            "data": [{"embedding": fake_vector, "index": 0}],
            "usage": {"total_tokens": 10},
        }

        pm = ProceduralMemory(
            f"{self.tmpdir}/rules.md",
            embedding_store=store,
        )

        with patch.object(store, "_http_post", return_value=mock_response):
            pm.add_rule(
                "Always configure security group",
                "ECS deployment",
                0.95,
            )
            assert store.size == 1
            assert store._metadata[0]["type"] == "rule"

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_case_memory_adds_to_embedding_store(self):
        """Test that recording a case indexes into embedding store."""
        import numpy as np
        from sage.memory.cases import CaseMemory

        store = EmbeddingStore(store_dir=f"{self.tmpdir}/vectors", api_key="test")

        fake_vector = np.random.randn(1024).tolist()
        mock_response = {
            "data": [{"embedding": fake_vector, "index": 0}],
            "usage": {"total_tokens": 10},
        }

        cm = CaseMemory(
            f"{self.tmpdir}/cases.jsonl",
            embedding_store=store,
        )

        with patch.object(store, "_http_post", return_value=mock_response):
            cm.record(
                task="Deploy Node.js app",
                outcome="success",
                steps=[{"step": "create instance", "result": "success"}],
                app_type="node",
            )
            assert store.size == 1
            assert store._metadata[0]["type"] == "case"

    @pytest.mark.skipif(not HAS_NUMPY, reason="numpy not installed")
    def test_skill_library_adds_to_embedding_store(self):
        """Test that recording a skill indexes into embedding store."""
        import numpy as np
        from sage.memory.skills import SkillLibrary

        store = EmbeddingStore(store_dir=f"{self.tmpdir}/vectors", api_key="test")

        fake_vector = np.random.randn(1024).tolist()
        mock_response = {
            "data": [{"embedding": fake_vector, "index": 0}],
            "usage": {"total_tokens": 10},
        }

        sl = SkillLibrary(
            f"{self.tmpdir}/skills.jsonl",
            embedding_store=store,
        )

        with patch.object(store, "_http_post", return_value=mock_response):
            sl.record_skill(
                task="Deploy Python Flask API",
                app_type="python",
                steps=[{"step": "configure sg"}, {"step": "create instance"}],
                tools_used=["AuthorizeSecurityGroup", "RunInstances"],
            )
            assert store.size == 1
            assert store._metadata[0]["type"] == "skill"
