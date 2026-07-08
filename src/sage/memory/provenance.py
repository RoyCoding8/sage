"""
Provenance Graph — links cases, learned rules, and later outcomes.

The graph is a compact DAG-like evidence record:
case -> rule when a correction extracts a rule,
rule -> case when a rule is applied to a later trajectory.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from sage.persistence import AtomicJsonDocument

logger = logging.getLogger(__name__)


class ProvenanceGraph:
    def __init__(self, graph_path: str = "memory/provenance.json"):
        self.graph_path = Path(graph_path)
        self.graph_path.parent.mkdir(parents=True, exist_ok=True)
        self._document = AtomicJsonDocument(self.graph_path, self._empty_graph)

    @staticmethod
    def _empty_graph() -> dict:
        return {"nodes": {}, "edges": []}

    def _load(self) -> dict:
        try:
            return self._document.read()
        except (ValueError, OSError) as e:
            logger.warning("Failed to load provenance graph, resetting: %s", e)
            return self._empty_graph()

    def _update(self, mutate):
        try:
            return self._document.update(mutate)
        except (ValueError, OSError) as e:
            logger.error("Failed to update provenance graph: %s", e)
            raise

    def add_node(self, node_id: str, kind: str, **attrs):
        def mutate(graph):
            graph["nodes"][node_id] = {
                **graph["nodes"].get(node_id, {}),
                "kind": kind,
                "updated": datetime.now(timezone.utc).isoformat(),
                **attrs,
            }

        self._update(mutate)

    def add_edge(self, source: str, target: str, relation: str, **attrs):
        def mutate(graph):
            edge = {
                "source": source,
                "target": target,
                "relation": relation,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **attrs,
            }
            if not any(
                e.get("source") == source
                and e.get("target") == target
                and e.get("relation") == relation
                and e.get("case_outcome") == attrs.get("case_outcome")
                for e in graph["edges"]
            ):
                graph["edges"].append(edge)

        self._update(mutate)

    def add_case(self, case: dict):
        self.add_node(
            case["case_id"], "case", task=case.get("task"), outcome=case.get("outcome")
        )

    def add_rule_extraction(self, case_id: str, rule_id: str):
        self.add_node(rule_id, "rule")
        self.add_edge(case_id, rule_id, "extracted_rule")

    def add_rule_application(self, rule_id: str, case_id: str, outcome: str):
        self.add_edge(rule_id, case_id, "applied_to", case_outcome=outcome)

    def get_stats(self) -> dict:
        graph = self._load()
        return {"nodes": len(graph["nodes"]), "edges": len(graph["edges"])}

    def to_mermaid(self, limit: int = 12) -> str:
        graph = self._load()
        lines = ["flowchart LR"]
        for edge in graph["edges"][-limit:]:
            label = edge["relation"].replace("_", " ")
            lines.append(f'    {edge["source"]} -- "{label}" --> {edge["target"]}')
        return "\n".join(lines)

    def clear(self):
        self._document.clear()
