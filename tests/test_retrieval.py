from sage.memory.retrieval import MemoryRetrieval


class StubStore:
    def __init__(self, entries):
        self.entries = entries

    def get_all_rules(self):
        return self.entries


class AccessTracker:
    def __init__(self):
        self.accessed = []

    def access(self, entry_id):
        self.accessed.append(entry_id)


def test_query_builds_index_and_reinforces_retrieved_memory():
    tracker = AccessTracker()
    retrieval = MemoryRetrieval(
        procedural=StubStore(
            [
                {
                    "id": "R001",
                    "text": "Open the security group port before deployment",
                    "context": "ECS deployment",
                    "confidence": 0.95,
                    "utility": 0.8,
                },
                {
                    "id": "R002",
                    "text": "Use gzip compression for static assets",
                    "context": "web optimization",
                    "confidence": 0.9,
                },
            ]
        ),
        consolidator=tracker,
    )

    results = retrieval.query("security group deployment", top_k=1)

    assert [result.memory_id for result in results] == ["R001"]
    assert tracker.accessed == ["R001"]
    assert results[0].citation.startswith("[rule:R001]")


def test_rebuild_replaces_stale_entries_and_reports_index_shape():
    store = StubStore(
        [
            {
                "id": "R001",
                "text": "Open port 8080",
                "context": "python deployment",
                "confidence": 0.9,
            }
        ]
    )
    retrieval = MemoryRetrieval(procedural=store)
    assert retrieval.query("port 8080")[0].memory_id == "R001"

    store.entries = [
        {
            "id": "R002",
            "text": "Open port 3000",
            "context": "node deployment",
            "confidence": 0.9,
        }
    ]
    stats = retrieval.rebuild()

    assert stats["total_entries"] == 1
    assert stats["type_counts"] == {"rule": 1}
    assert retrieval.query("port 3000")[0].memory_id == "R002"
    assert retrieval.query("redis cluster") == []


def test_format_for_prompt_keeps_ranked_citations_within_budget():
    retrieval = MemoryRetrieval(
        procedural=StubStore(
            [
                {
                    "id": "R001",
                    "text": "Open the security group port before deployment",
                    "context": "ECS deployment",
                    "confidence": 0.95,
                }
            ]
        )
    )

    prompt = retrieval.format_for_prompt(
        retrieval.query("security group deployment"), max_tokens=80
    )

    assert "## Learned Rules (MUST follow)" in prompt
    assert "[rule:R001]" in prompt
    assert len(prompt) <= 320
