"""Concurrency and atomicity checks for file-backed persistence."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from sage.memory.provenance import ProvenanceGraph
from sage.memory.cases import CaseMemory
from sage.memory.consolidation import MemoryConsolidator
from sage.memory.preferences import PreferenceMemory
from sage.memory.skills import SkillLibrary
from sage.metrics import MetricsRecorder
from sage.persistence import (
    AtomicJsonDocument,
    AtomicJsonLines,
    append_jsonl,
    atomic_write_text,
)


def test_concurrent_jsonl_appends_remain_complete(tmp_path):
    """Concurrent writers produce complete, independently parseable records."""
    path = tmp_path / "events.jsonl"
    threads = [
        threading.Thread(target=append_jsonl, args=(path, {"index": index}))
        for index in range(40)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert {record["index"] for record in records} == set(range(40))


def test_atomic_write_replaces_the_complete_document(tmp_path):
    """Replacement writes leave one complete document at the public path."""
    path = tmp_path / "state.json"
    atomic_write_text(path, '{"version": 1}')
    atomic_write_text(path, '{"version": 2}')
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 2}


def test_failed_document_mutation_does_not_commit_partial_state(tmp_path):
    path = tmp_path / "state.json"
    document = AtomicJsonDocument(path, dict)
    document.update(lambda state: state.update(version=1))

    def fail(state: dict) -> None:
        state["version"] = 2
        raise RuntimeError("mutation failed")

    with pytest.raises(RuntimeError, match="mutation failed"):
        document.update(fail)

    assert document.read() == {"version": 1}


def test_jsonl_document_rejects_corruption_without_rewriting_it(tmp_path):
    path = tmp_path / "records.jsonl"
    original = '{"valid": true}\n{"partial":'
    path.write_text(original, encoding="utf-8")
    document = AtomicJsonLines[dict](path)

    with pytest.raises(ValueError, match="Invalid JSONL record"):
        document.update(lambda records: records.append({"new": "记录"}))

    assert path.read_text(encoding="utf-8") == original


def test_jsonl_document_round_trips_unicode(tmp_path):
    document = AtomicJsonLines[dict](tmp_path / "records.jsonl")
    document.update(lambda records: records.append({"fact": "部署记录 🚀"}))
    assert document.read() == [{"fact": "部署记录 🚀"}]


def test_clear_waits_for_an_active_document_update(tmp_path):
    document = AtomicJsonDocument(tmp_path / "state.json", dict)
    update_started = threading.Event()
    finish_update = threading.Event()
    clear_finished = threading.Event()

    def slow_update() -> None:
        def mutate(state: dict) -> None:
            state["complete"] = True
            update_started.set()
            assert finish_update.wait(timeout=2)

        document.update(mutate)

    def clear() -> None:
        document.clear()
        clear_finished.set()

    update_thread = threading.Thread(target=slow_update)
    clear_thread = threading.Thread(target=clear)
    update_thread.start()
    assert update_started.wait(timeout=2)
    clear_thread.start()
    assert not clear_finished.wait(timeout=0.05)
    finish_update.set()
    update_thread.join(timeout=2)
    clear_thread.join(timeout=2)

    assert clear_finished.is_set()
    assert not document.path.exists()


def test_concurrent_provenance_updates_preserve_every_node(tmp_path):
    """Concurrent Runs cannot corrupt Provenance or erase another completed update."""
    path = tmp_path / "provenance.json"
    seed_nodes = {
        f"seed-{index}": {"kind": "seed", "payload": "x" * 2_000}
        for index in range(50)
    }
    path.write_text(json.dumps({"nodes": seed_nodes, "edges": []}), encoding="utf-8")
    workers = 16
    start = threading.Barrier(workers)

    def add_node(index: int) -> None:
        graph = ProvenanceGraph(str(path))
        start.wait()
        graph.add_node(f"case-{index}", "case", task=f"Run {index}")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(add_node, range(workers)))

    graph = json.loads(path.read_text(encoding="utf-8"))
    assert {f"case-{index}" for index in range(workers)} <= set(graph["nodes"])


def test_provenance_refuses_to_overwrite_a_corrupt_document(tmp_path):
    path = tmp_path / "provenance.json"
    original = '{"nodes":'
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError):
        ProvenanceGraph(str(path)).add_node("case-1", "case")

    assert path.read_text(encoding="utf-8") == original


def test_concurrent_skill_promotions_are_complete_and_uniquely_identified(tmp_path):
    """Every successful promotion persists one independently addressable Skill."""
    path = tmp_path / "skills.jsonl"
    workers = 16
    start = threading.Barrier(workers)

    def record_skill(index: int) -> dict:
        library = SkillLibrary(str(path))
        start.wait()
        return library.record_skill(
            task=f"Deploy app {index}",
            app_type="python",
            steps=[],
            tools_used=[],
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        returned = list(executor.map(record_skill, range(workers)))

    stored = SkillLibrary(str(path)).get_all()
    assert len(stored) == workers
    assert len({skill["skill_id"] for skill in returned}) == workers
    assert {skill["task"] for skill in stored} == {
        f"Deploy app {index}" for index in range(workers)
    }


def test_concurrent_preference_updates_preserve_every_category(tmp_path):
    """Independent Preference writers merge at the durable document seam."""
    path = tmp_path / "preferences.json"
    workers = 12
    start = threading.Barrier(workers)
    memories = [PreferenceMemory(str(path)) for _ in range(workers)]

    def set_preference(index: int) -> None:
        start.wait()
        memories[index].set_preference(f"category-{index}", f"value-{index}")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(set_preference, range(workers)))

    stored = PreferenceMemory(str(path)).get_all()
    assert set(stored) == {f"category-{index}" for index in range(workers)}


def test_preference_decay_is_durable_even_when_nothing_is_pruned(tmp_path):
    path = tmp_path / "preferences.json"
    memory = PreferenceMemory(str(path))
    memory.set_preference("region", "us-east-1", confidence=0.4)

    assert memory.decay_unused(min_confidence=0.2) == []

    reloaded = PreferenceMemory(str(path)).get_preference("region")
    assert reloaded is not None
    assert reloaded["confidence"] == pytest.approx(0.3)


def test_concurrent_consolidation_tracking_preserves_every_memory(tmp_path):
    """Consolidation writers cannot replace another writer's tracked memory."""
    path = tmp_path / "consolidation.json"
    workers = 12
    start = threading.Barrier(workers)
    consolidators = [MemoryConsolidator(str(path)) for _ in range(workers)]

    def track(index: int) -> None:
        start.wait()
        consolidators[index].track(f"R{index:03d}", "rule")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(track, range(workers)))

    assert MemoryConsolidator(str(path)).get_memory_health()["total"] == workers


def test_concurrent_case_recording_is_complete_and_uniquely_identified(tmp_path):
    """Every completed Run produces one durable, uniquely identified Case."""
    path = tmp_path / "cases.jsonl"
    workers = 16
    start = threading.Barrier(workers)

    def record(index: int) -> dict:
        memory = CaseMemory(str(path))
        start.wait()
        return memory.record(f"Run {index}", "success", [])

    with ThreadPoolExecutor(max_workers=workers) as executor:
        returned = list(executor.map(record, range(workers)))

    stored = CaseMemory(str(path)).get_all()
    assert len(stored) == workers
    assert len({case["case_id"] for case in returned}) == workers


def test_concurrent_metric_recorders_preserve_every_run_outcome(tmp_path):
    path = tmp_path / "metrics.json"
    workers = 12
    start = threading.Barrier(workers)
    recorders = [MetricsRecorder(path) for _ in range(workers)]

    def record(index: int) -> None:
        start.wait()
        recorders[index].record_outcome(success=index % 2 == 0)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(record, range(workers)))

    stored = MetricsRecorder(path).get_metrics()
    assert stored["total_tasks"] == workers
    assert stored["successes"] == workers // 2
    assert stored["failures"] == workers // 2
