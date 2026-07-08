"""
Semantic Memory — Knowledge base files with hybrid BM25 + vector retrieval.

Static knowledge about Alibaba Cloud, deployment patterns, etc.
Loaded as context into the agent prompt.

Retrieval strategy (hybrid):
1. BM25 scoring (keyword match) — fast, works offline
2. Embedding similarity (via EmbeddingStore) — semantic, handles paraphrases
3. Reciprocal Rank Fusion merges both ranked lists

The write-path auto-indexes new knowledge into the vector store,
so future retrievals benefit from semantic understanding.
"""

import logging
from pathlib import Path
from typing import Optional
import math
import re

from sage.persistence import append_text, atomic_write_text

logger = logging.getLogger(__name__)


class SemanticMemory:
    def __init__(self, knowledge_dir: str = "knowledge", embedding_store=None):
        self.knowledge_dir = Path(knowledge_dir)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self._embedding_store = embedding_store

    def set_embedding_store(self, store):
        """Attach an embedding store for hybrid retrieval."""
        self._embedding_store = store

    def _document_path(self, filename: str) -> Path:
        root = self.knowledge_dir.resolve()
        candidate = (root / filename).resolve()
        if not filename or not candidate.is_relative_to(root) or candidate == root:
            raise ValueError("Semantic document must stay inside the knowledge directory")
        return candidate

    def add_document(self, filename: str, content: str):
        """Add or update a knowledge document."""
        filepath = self._document_path(filename)
        atomic_write_text(filepath, content)
        # Index into vector store for semantic retrieval
        if self._embedding_store:
            self._embedding_store.add(
                f"{filename}: {content[:2000]}", {"type": "semantic", "doc": filename}
            )

    def append_knowledge(self, topic: str, fact: str):
        """Write-path: auto-append a learned fact to the best-matching document.

        If no existing doc matches, creates a new 'learned-<topic>.md' file.
        This turns semantic memory from read-only into a living knowledge base.
        """
        matches = self.retrieve(topic, limit=1)
        if matches:
            doc_name, content, _ = matches[0]
            filepath = self._document_path(doc_name)
            try:
                append_text(filepath, f"\n- {fact}\n")
            except OSError as exc:
                logger.error("Failed to append semantic Fact: %s", exc)
                raise
        else:
            safe_name = re.sub(r"[^a-z0-9]+", "-", topic.lower())[:40]
            self.add_document(f"learned-{safe_name}.md", f"# {topic}\n\n- {fact}\n")

        # Also index the fact into embeddings
        if self._embedding_store:
            self._embedding_store.add(
                f"{topic}: {fact}", {"type": "semantic_fact", "topic": topic}
            )

    def get_document(self, filename: str) -> Optional[str]:
        """Read a knowledge document."""
        filepath = self._document_path(filename)
        if filepath.exists():
            return filepath.read_text(encoding="utf-8")
        return None

    def list_documents(self) -> list[str]:
        """List all knowledge documents."""
        if not self.knowledge_dir.exists():
            return []
        return [f.name for f in self.knowledge_dir.iterdir() if f.is_file()]

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", text.lower()))

    @staticmethod
    def _idf(doc_token_sets: list[set[str]], term: str) -> float:
        """Compute inverse document frequency for a term.

        IDF = log((N + 1) / (df + 1)) + 1  (with smoothing to avoid log(0)).
        """
        N = len(doc_token_sets)
        df = sum(1 for tokens in doc_token_sets if term in tokens)
        return math.log((N + 1) / (df + 1)) + 1

    @staticmethod
    def _bm25_score(
        query_tokens: set[str],
        doc_tokens: list[str],
        doc_token_sets: list[set[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> float:
        """Compute BM25 score for a document against a query.

        Uses term frequency (clamped by k1) and inverse document frequency.
        avgdl is the mean document length across all documents.
        """
        if not query_tokens or not doc_tokens:
            return 0.0
        N = len(doc_token_sets)
        if N == 0:
            return 0.0
        doc_token_set = set(doc_tokens)
        avgdl = sum(len(t) for t in doc_token_sets) / N
        dl = len(doc_tokens)
        score = 0.0
        for term in query_tokens:
            if term not in doc_token_set:
                continue
            tf = doc_tokens.count(term)
            idf_val = SemanticMemory._idf(doc_token_sets, term)
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * dl / max(avgdl, 1))
            score += idf_val * numerator / denominator
        return score

    def retrieve(self, topic: str, limit: int = 3) -> list[tuple[str, str, float]]:
        """Hybrid retrieval: BM25 + embedding similarity with Reciprocal Rank Fusion.

        Falls back to BM25-only if embedding store is unavailable.
        """
        bm25_results = self._retrieve_bm25(topic, limit=limit * 2)

        # If no embedding store, return BM25 directly
        if not self._embedding_store or self._embedding_store.size == 0:
            return bm25_results[:limit]

        # Get vector results
        vector_hits = self._embedding_store.query(
            topic,
            top_k=limit * 2,
            filter_fn=lambda m: m.get("type") in ("semantic", "semantic_fact"),
        )

        # Build reciprocal rank fusion
        # RRF score = sum(1 / (k + rank)) across both lists
        k = 60  # RRF constant (standard value)
        scores: dict[str, float] = {}
        doc_content: dict[str, tuple[str, str]] = {}

        # Score BM25 results
        for rank, (doc, content, bm25_score) in enumerate(bm25_results):
            scores[doc] = scores.get(doc, 0) + 1.0 / (k + rank)
            doc_content[doc] = (doc, content)

        # Score vector results (map back to document names)
        for rank, hit in enumerate(vector_hits):
            doc_name = hit.get("doc", hit.get("topic", f"vec_{rank}"))
            scores[doc_name] = scores.get(doc_name, 0) + 1.0 / (k + rank)
            if doc_name not in doc_content:
                # Try to load the actual document
                content = self.get_document(doc_name)
                if content:
                    doc_content[doc_name] = (doc_name, content)
                else:
                    doc_content[doc_name] = (doc_name, hit.get("text", ""))

        # Sort by fused score and return top-limit
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        results = []
        for doc_name, score in ranked:
            if doc_name in doc_content:
                name, content = doc_content[doc_name]
                results.append((name, content, score))

        return results

    def _retrieve_bm25(
        self, topic: str, limit: int = 6
    ) -> list[tuple[str, str, float]]:
        """Rank knowledge documents using BM25-style TF-IDF scoring.

        Replaces simple token overlap with term frequency * inverse document
        frequency scoring for more accurate retrieval as the knowledge base grows.
        """
        query_tokens = self._tokens(topic)
        if not query_tokens:
            return []

        # Collect all document token lists for IDF computation
        doc_entries: list[tuple[str, str, list[str]]] = []
        for doc in self.list_documents():
            if not (content := self.get_document(doc)):
                continue
            tokens = re.findall(r"[a-z0-9]+", (doc + " " + content).lower())
            doc_entries.append((doc, content, tokens))

        if not doc_entries:
            return []

        # Build token sets for IDF
        doc_token_sets = [set(tokens) for _, _, tokens in doc_entries]

        # Score each document with BM25
        scored = []
        for doc, content, tokens in doc_entries:
            score = self._bm25_score(query_tokens, tokens, doc_token_sets)
            if score > 0:
                scored.append((doc, content, score))

        return sorted(scored, key=lambda item: item[2], reverse=True)[:limit]

    def get_context_for_prompt(self, topic: Optional[str] = None) -> str:
        """Get relevant knowledge for the prompt."""
        docs = (
            self.retrieve(topic)
            if topic
            else [
                (doc, content, 0.0)
                for doc in self.list_documents()
                if (content := self.get_document(doc))
            ]
        )
        if not docs:
            return "No knowledge base loaded yet."
        parts = ["Knowledge Base:"] + [
            f"\n### {doc}\n{(content[:500] + '...') if len(content) > 500 else content}"
            for doc, content, _ in docs
        ]
        return "\n".join(parts)


if __name__ == "__main__":
    sm = SemanticMemory("/tmp/test_knowledge")
    sm.add_document(
        "alibaba-cloud.md", "# Alibaba Cloud Basics\n\nECS = Virtual machines\n..."
    )
    print(sm.get_context_for_prompt())
