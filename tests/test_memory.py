"""
Tests for Sage memory modules:
  - ProceduralMemory (rules storage, retrieval, prompt formatting)
  - EpisodicMemory (logging, querying, stats)
  - SemanticMemory (knowledge documents)
"""

import json
import pytest


# ─── ProceduralMemory ────────────────────────────────────────────────────────


class TestProceduralMemory:
    def test_init_creates_file(self, tmp_path):
        """rules.md is created on init."""
        from sage.memory.procedural import ProceduralMemory

        pm = ProceduralMemory(str(tmp_path / "rules.md"))
        assert pm.rules_path.exists()
        content = pm.rules_path.read_text()
        assert "Learned Rules" in content

    def test_add_rule_returns_id(self, proc_mem):
        """add_rule returns a sequential rule ID."""
        rid = proc_mem.add_rule("Always check ports", "networking", 0.9)
        assert rid.startswith("R")
        assert rid == "R001"

    def test_add_rule_increments_id(self, proc_mem):
        """Each rule gets a unique, incrementing ID."""
        r1 = proc_mem.add_rule("Rule one", "ctx1", 0.8)
        r2 = proc_mem.add_rule("Rule two", "ctx2", 0.9)
        assert r1 == "R001"
        assert r2 == "R002"

    def test_add_rule_custom_id(self, proc_mem):
        """A custom rule_id is preserved."""
        rid = proc_mem.add_rule("Custom rule", "ctx", 1.0, rule_id="CUSTOM")
        assert rid == "CUSTOM"

    def test_add_rule_persists_to_disk(self, proc_mem):
        """Rules are written to the file."""
        proc_mem.add_rule("Deploy carefully", "ECS", 0.85)
        content = proc_mem.rules_path.read_text()
        assert "Deploy carefully" in content
        assert "ECS" in content
        assert "0.85" in content

    def test_get_all_rules_empty(self, proc_mem):
        """No rules yet returns empty list."""
        assert proc_mem.get_all_rules() == []

    def test_get_all_rules_after_add(self, proc_mem):
        """get_all_rules returns all added rules."""
        proc_mem.add_rule("First rule", "ctx1", 0.7)
        proc_mem.add_rule("Second rule", "ctx2", 0.9)
        rules = proc_mem.get_all_rules()
        assert len(rules) == 2
        assert rules[0]["text"] == "First rule"
        assert rules[1]["text"] == "Second rule"
        assert rules[0]["confidence"] == 0.7
        assert rules[1]["confidence"] == 0.9

    def test_get_all_rules_source_task(self, proc_mem):
        """Source task is captured in rule data."""
        proc_mem.add_rule("Rule", "ctx", 0.5, source_task="Deploy app")
        rules = proc_mem.get_all_rules()
        assert rules[0]["source"] == "Deploy app"

    def test_get_rules_for_prompt_empty(self, proc_mem):
        """Empty rules returns a fresh-start message."""
        prompt = proc_mem.get_rules_for_prompt()
        assert "fresh" in prompt.lower() or "no learned rules" in prompt.lower()

    def test_get_rules_for_prompt_with_rules(self, proc_mem):
        """Rules are formatted for prompt injection."""
        proc_mem.add_rule("Check security groups first", "ECS deploy", 0.9, dedup=False)
        proc_mem.add_rule("Use HTTPS for all endpoints", "networking", 0.3, dedup=False)
        prompt = proc_mem.get_rules_for_prompt()
        assert "Check security groups first" in prompt
        assert "0.9" in prompt or "90%" in prompt
        # Low-confidence rules (<0.5) should NOT appear
        assert "Use HTTPS" not in prompt

    def test_get_rule_count(self, proc_mem):
        """get_rule_count reflects added rules."""
        assert proc_mem.get_rule_count() == 0
        proc_mem.add_rule("Rule A", "ctx", 0.5, dedup=False)
        assert proc_mem.get_rule_count() == 1
        proc_mem.add_rule("Rule B", "ctx", 0.6, dedup=False)
        assert proc_mem.get_rule_count() == 2

    def test_clear_rules(self, proc_mem):
        """clear() resets rules."""
        proc_mem.add_rule("Temp rule", "ctx", 0.5)
        assert proc_mem.get_rule_count() == 1
        proc_mem.clear()
        assert proc_mem.get_rule_count() == 0

    def test_long_rule_text_truncated(self, proc_mem):
        """Very long rule text is stored (no crash)."""
        long_text = "A" * 500
        rid = proc_mem.add_rule(long_text, "ctx", 0.5)
        assert rid == "R001"

    def test_confidence_edge_values(self, proc_mem):
        """Boundary confidence values: 0.0 and 1.0."""
        proc_mem.add_rule("Zero conf", "ctx", 0.0)
        proc_mem.add_rule("Max conf", "ctx", 1.0)
        rules = proc_mem.get_all_rules()
        assert rules[0]["confidence"] == 0.0
        assert rules[1]["confidence"] == 1.0

    def test_update_utility_uses_td_style_update(self, proc_mem):
        """Rule utility moves toward observed reward."""
        proc_mem.add_rule("Check ports", "networking", 0.9, rule_id="R001")
        assert proc_mem.update_utility("R001", 1.0, alpha=0.25) == pytest.approx(0.25)
        assert proc_mem.update_utility("R001", -1.0, alpha=0.2) == pytest.approx(0.0)

    def test_update_utility_unknown_rule_is_noop(self, proc_mem):
        """Unknown rules do not crash utility updates."""
        proc_mem.add_rule("Check ports", "networking", 0.9, rule_id="R001")
        assert proc_mem.update_utility("R999", 1.0) is None


# ─── CaseMemory ──────────────────────────────────────────────────────────────


class TestCaseMemory:
    def test_record_case_writes_structured_trajectory(self, tmp_path):
        from sage.memory.cases import CaseMemory

        cm = CaseMemory(str(tmp_path / "cases.jsonl"))
        case = cm.record(
            task="Deploy Node app",
            app_type="node",
            outcome="success",
            steps=[{"step": "RunInstances", "result": "created"}],
            tools_used=["RunInstances"],
            rules_applied=["R001"],
        )
        assert case["case_id"] == "C001"
        assert cm.get_all()[0]["rules_applied"] == ["R001"]
        assert cm.get_stats()["success"] == 1

    def test_retrieve_similar_cases(self, tmp_path):
        from sage.memory.cases import CaseMemory

        cm = CaseMemory(str(tmp_path / "cases.jsonl"))
        cm.record("Deploy Node app", "success", [], app_type="node")
        cm.record("Configure database", "failed", [], app_type="postgres")
        assert cm.retrieve("Deploy another node service")[0]["app_type"] == "node"


# ─── ProvenanceGraph ─────────────────────────────────────────────────────────


class TestProvenanceGraph:
    def test_links_case_rule_and_outcome(self, tmp_path):
        from sage.memory.provenance import ProvenanceGraph

        pg = ProvenanceGraph(str(tmp_path / "provenance.json"))
        pg.add_case({"case_id": "C001", "task": "Deploy app", "outcome": "failed"})
        pg.add_rule_extraction("C001", "R001")
        pg.add_case({"case_id": "C002", "task": "Deploy app", "outcome": "success"})
        pg.add_rule_application("R001", "C002", "success")
        assert pg.get_stats() == {"nodes": 3, "edges": 2}
        assert "C001" in pg.to_mermaid()


# ─── EpisodicMemory ──────────────────────────────────────────────────────────


class TestEpisodicMemory:
    def test_log_creates_entry(self, episodic_mem):
        """log() returns an entry dict."""
        entry = episodic_mem.log("Deploy app", 1, "failed", error="timeout")
        assert entry["task"] == "Deploy app"
        assert entry["attempt"] == 1
        assert entry["outcome"] == "failed"
        assert entry["error"] == "timeout"
        assert "timestamp" in entry

    def test_log_writes_jsonl(self, episodic_mem):
        """log() writes JSONL to disk."""
        episodic_mem.log("Task A", 1, "success")
        episodic_mem.log("Task B", 1, "failed", error="err")
        lines = episodic_mem.current_file.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            data = json.loads(line)
            assert "timestamp" in data

    def test_log_with_correction(self, episodic_mem):
        """log() captures correction and rule info."""
        entry = episodic_mem.log(
            "Task X",
            1,
            "failed",
            error="wrong port",
            correction="Use port 443",
            rule_extracted="Always use 443 for HTTPS",
            rule_id="R001",
        )
        assert entry["correction"] == "Use port 443"
        assert entry["rule_extracted"] == "Always use 443 for HTTPS"
        assert entry["rule_id"] == "R001"

    def test_get_recent_empty(self, episodic_mem):
        """No entries yet returns empty."""
        assert episodic_mem.get_recent() == []

    def test_get_recent_limit(self, episodic_mem):
        """get_recent(n) returns at most n entries."""
        for i in range(5):
            episodic_mem.log(f"Task {i}", 1, "success")
        assert len(episodic_mem.get_recent(3)) == 3
        assert len(episodic_mem.get_recent(10)) == 5

    def test_get_recent_most_recent(self, episodic_mem):
        """get_recent returns the most recent entries (not the first)."""
        for i in range(5):
            episodic_mem.log(f"Task {i}", 1, "success")
        recent = episodic_mem.get_recent(2)
        assert recent[0]["task"] == "Task 3"
        assert recent[1]["task"] == "Task 4"

    def test_get_by_task(self, episodic_mem):
        """get_by_task filters by substring match."""
        episodic_mem.log("Deploy ECS", 1, "success")
        episodic_mem.log("Deploy OSS", 1, "success")
        episodic_mem.log("Check billing", 1, "failed")
        results = episodic_mem.get_by_task("deploy")
        assert len(results) == 2
        assert all("deploy" in r["task"].lower() for r in results)

    def test_get_corrections(self, episodic_mem):
        """get_corrections returns only entries with corrections."""
        episodic_mem.log("Task A", 1, "failed", correction="fix it")
        episodic_mem.log("Task B", 1, "success")
        episodic_mem.log("Task C", 1, "failed", correction="try again")
        corrections = episodic_mem.get_corrections()
        assert len(corrections) == 2

    def test_get_stats_empty(self, episodic_mem):
        """Empty memory returns zero stats."""
        stats = episodic_mem.get_stats()
        assert stats["total"] == 0
        assert stats["success"] == 0
        assert stats["failed"] == 0
        assert stats["corrections"] == 0
        assert stats["success_rate"] == 0.0

    def test_get_stats(self, episodic_mem):
        """Stats are computed correctly."""
        episodic_mem.log("A", 1, "success")
        episodic_mem.log("B", 1, "failed")
        episodic_mem.log("C", 1, "success")
        episodic_mem.log("D", 1, "failed", correction="fix")
        stats = episodic_mem.get_stats()
        assert stats["total"] == 4
        assert stats["success"] == 2
        assert stats["failed"] == 2
        assert stats["corrections"] == 1
        assert stats["success_rate"] == 0.5

    def test_get_stats_success_rate_zero_total(self, episodic_mem):
        """Division by zero avoided for success_rate."""
        stats = episodic_mem.get_stats()
        assert stats["success_rate"] == 0.0

    def test_clear(self, episodic_mem):
        """clear() removes the JSONL file."""
        episodic_mem.log("Task", 1, "success")
        assert episodic_mem.current_file.exists()
        episodic_mem.clear()
        assert not episodic_mem.current_file.exists()

    def test_metadata_stored(self, episodic_mem):
        """Custom metadata dict is stored."""
        episodic_mem.log("Task", 1, "success", metadata={"source": "demo"})
        entry = episodic_mem.get_recent(1)[0]
        assert entry["metadata"]["source"] == "demo"

    def test_malformed_jsonl_line_skipped(self, episodic_mem):
        """Corrupted lines are skipped gracefully in get_recent."""
        episodic_mem.current_file.write_text(
            '{"task":"ok","line":1}\nBAD LINE\n{"task":"ok2","line":2}\n'
        )
        recent = episodic_mem.get_recent()
        # Should skip the bad line and return the 2 valid entries
        assert len(recent) == 2
        assert recent[0]["task"] == "ok"
        assert recent[1]["task"] == "ok2"


# ─── SemanticMemory ──────────────────────────────────────────────────────────


class TestSemanticMemory:
    def test_init_creates_dir(self, tmp_path):
        """Knowledge directory is created on init."""
        from sage.memory.semantic import SemanticMemory

        sm = SemanticMemory(str(tmp_path / "knowledge"))
        assert sm.knowledge_dir.exists()

    def test_add_and_get_document(self, semantic_mem):
        """Documents can be added and retrieved."""
        semantic_mem.add_document("deploy.md", "# Deployment\nDeploy to ECS")
        content = semantic_mem.get_document("deploy.md")
        assert content == "# Deployment\nDeploy to ECS"

    def test_get_nonexistent_document(self, semantic_mem):
        """Missing document returns None."""
        assert semantic_mem.get_document("nope.md") is None

    def test_list_documents(self, semantic_mem):
        """list_documents returns all added docs."""
        semantic_mem.add_document("a.md", "doc A")
        semantic_mem.add_document("b.md", "doc B")
        docs = semantic_mem.list_documents()
        assert "a.md" in docs
        assert "b.md" in docs

    def test_get_context_for_prompt_empty(self, semantic_mem):
        """Empty knowledge base returns a placeholder."""
        ctx = semantic_mem.get_context_for_prompt()
        assert "no knowledge base" in ctx.lower() or "not" in ctx.lower()

    def test_get_context_for_prompt_with_docs(self, semantic_mem):
        """Context includes document content."""
        semantic_mem.add_document("info.md", "Important stuff here")
        ctx = semantic_mem.get_context_for_prompt()
        assert "Important stuff here" in ctx
        assert "info.md" in ctx

    def test_document_truncation(self, semantic_mem):
        """Long documents are truncated in prompt context."""
        long_content = "x" * 1000
        semantic_mem.add_document("big.md", long_content)
        ctx = semantic_mem.get_context_for_prompt()
        # Should be truncated with "..." at 500 chars
        assert "..." in ctx
        assert len(ctx) < len(long_content) + 200  # some overhead for formatting

    def test_retrieve_ranks_relevant_documents(self, semantic_mem):
        """Topic retrieval ranks matching knowledge above unrelated docs."""
        semantic_mem.add_document(
            "security.md", "security group ingress ports firewall"
        )
        semantic_mem.add_document("billing.md", "cost tags budgets invoices")
        results = semantic_mem.retrieve("deploy app security group ports")
        assert results[0][0] == "security.md"

    def test_prompt_context_filters_by_topic(self, semantic_mem):
        """Prompt context can include only topic-relevant knowledge."""
        semantic_mem.add_document(
            "security.md", "security group ingress ports firewall"
        )
        semantic_mem.add_document("billing.md", "cost tags budgets invoices")
        ctx = semantic_mem.get_context_for_prompt("open inbound ports")
        assert "security.md" in ctx
        assert "billing.md" not in ctx

    def test_overwrite_document(self, semantic_mem):
        """Writing the same filename overwrites the old content."""
        semantic_mem.add_document("notes.md", "version 1")
        semantic_mem.add_document("notes.md", "version 2")
        assert semantic_mem.get_document("notes.md") == "version 2"

    # ─── BM25 TF-IDF Scoring Tests ─────────────────────────────────────────

    def test_bm25_score_returns_zero_for_no_query(self, semantic_mem):
        """BM25 score is 0 when query tokens are empty."""
        score = semantic_mem._bm25_score(set(), ["hello"], [{"hello"}])
        assert score == 0.0

    def test_bm25_score_returns_zero_for_no_doc_tokens(self, semantic_mem):
        """BM25 score is 0 when document tokens are empty."""
        score = semantic_mem._bm25_score({"hello"}, [], [{"hello"}])
        assert score == 0.0

    def test_bm25_score_positive_for_matching_term(self, semantic_mem):
        """BM25 score is positive when query term appears in document."""
        score = semantic_mem._bm25_score(
            {"security"}, ["security", "group"], [{"security", "group"}]
        )
        assert score > 0.0

    def test_bm25_score_zero_for_nonmatching_term(self, semantic_mem):
        """BM25 score is 0 when query term is absent from document."""
        score = semantic_mem._bm25_score(
            {"billing"}, ["security", "group"], [{"security", "group"}]
        )
        assert score == 0.0

    def test_bm25_prefers_relevant_doc_over_general(self, semantic_mem):
        """BM25 scores a focused doc higher than a broad doc for a specific query."""
        specific_tokens = ["security", "group", "ingress", "port"]
        general_tokens = ["security", "billing", "cost", "budget"]
        query = {"security", "group"}
        doc_token_sets = [set(specific_tokens), set(general_tokens)]
        score_specific = semantic_mem._bm25_score(
            query, specific_tokens, doc_token_sets
        )
        score_general = semantic_mem._bm25_score(query, general_tokens, doc_token_sets)
        assert score_specific > score_general

    def test_bm25_repeated_term_increases_tf_score(self, semantic_mem):
        """A term appearing multiple times scores higher (TF boost)."""
        single = ["security", "group", "other"]
        repeated = ["security", "security", "security", "group"]
        doc_token_sets = [set(single), set(repeated)]
        query = {"security"}
        score_single = semantic_mem._bm25_score(query, single, doc_token_sets)
        score_repeated = semantic_mem._bm25_score(query, repeated, doc_token_sets)
        assert score_repeated > score_single

    def test_idf_rare_term_scores_higher(self, semantic_mem):
        """A rare term has higher IDF than a common term."""
        doc_token_sets = [
            {"security", "common"},
            {"billing", "common"},
            {"deploy", "common"},
        ]
        idf_rare = semantic_mem._idf(doc_token_sets, "security")
        idf_common = semantic_mem._idf(doc_token_sets, "common")
        assert idf_rare > idf_common

    def test_bm25_retrieve_returns_bm25_scores(self, semantic_mem):
        """The retrieve method now returns BM25-scored results."""
        semantic_mem.add_document("sec.md", "security group ingress ports firewall")
        semantic_mem.add_document("bill.md", "cost tags budgets invoices")
        results = semantic_mem.retrieve("security group")
        assert len(results) > 0
        # security.md should be ranked first
        assert results[0][0] == "sec.md"
        # scores should be positive floats (BM25 scores)
        assert isinstance(results[0][2], float)
        assert results[0][2] > 0.0

    def test_bm25_retrieve_empty_query(self, semantic_mem):
        """Empty query returns no results."""
        semantic_mem.add_document("doc.md", "some content")
        results = semantic_mem.retrieve("")
        assert results == []
