"""
BM25 — In-memory Okapi BM25 index for text retrieval.

A shared utility used by:
  - MemoryRetrieval's internal cross-tier index
  - ProceduralMemory (rule deduplication)
  - CaseMemory (keyword fallback)
  - SkillLibrary (skill matching)
"""

import math
import re


def tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer with lowercasing."""
    return re.findall(r"[a-z0-9]+", text.lower())


def tokenize_set(text: str) -> set[str]:
    """Return unique tokens for IDF computation."""
    return set(tokenize(text))


class BM25Index:
    """
    In-memory BM25 index over a corpus of documents.

    Uses Okapi BM25 with standard parameters (k1=1.5, b=0.75).
    Supports incremental addition and batch scoring.

    Interface:
        add_document(text) -> int
        add_documents(texts) -> list[int]
        query(query_text, top_k) -> list[(doc_idx, score)]
        score(query, doc_idx) -> float
        score_all(query) -> list[float]
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs: list[list[str]] = []  # tokenized documents
        self.doc_freqs: dict[str, int] = {}  # term -> document frequency
        self.avg_dl: float = 0.0
        self.n_docs: int = 0

    def add_document(self, text: str) -> int:
        """Add a document to the index. Returns document index."""
        tokens = tokenize(text)
        self.docs.append(tokens)
        # Update doc frequencies
        unique_terms = set(tokens)
        for term in unique_terms:
            self.doc_freqs[term] = self.doc_freqs.get(term, 0) + 1
        # Update average document length
        self.n_docs = len(self.docs)
        total_tokens = sum(len(d) for d in self.docs)
        self.avg_dl = total_tokens / self.n_docs if self.n_docs > 0 else 0
        return self.n_docs - 1

    def add_documents(self, texts: list[str]) -> list[int]:
        """Batch-add documents."""
        indices = []
        for text in texts:
            indices.append(self.add_document(text))
        return indices

    def score(self, query: str, doc_idx: int) -> float:
        """Score a single document against a query."""
        query_tokens = tokenize(query)
        if not query_tokens or doc_idx >= len(self.docs):
            return 0.0
        doc_tokens = self.docs[doc_idx]
        return self._bm25_score(query_tokens, doc_tokens)

    def score_all(self, query: str) -> list[float]:
        """Score all documents against a query. Returns list of scores."""
        query_tokens = tokenize(query)
        if not query_tokens:
            return [0.0] * self.n_docs
        return [self._bm25_score(query_tokens, doc) for doc in self.docs]

    def query(self, query_text: str, top_k: int = 20) -> list[tuple[int, float]]:
        """Return top-K (doc_idx, score) pairs sorted by descending score."""
        scores = self.score_all(query_text)
        scored = [(i, s) for i, s in enumerate(scores) if s > 0]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def _bm25_score(self, query_tokens: list[str], doc_tokens: list[str]) -> float:
        """Compute BM25 score for a document against query tokens."""
        if not doc_tokens:
            return 0.0

        dl = len(doc_tokens)
        score = 0.0

        for term in query_tokens:
            if term not in self.doc_freqs:
                continue
            df = self.doc_freqs[term]
            # IDF with smoothing
            idf = math.log((self.n_docs - df + 0.5) / (df + 0.5) + 1.0)
            # Term frequency in document
            tf = doc_tokens.count(term)
            # BM25 TF component
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (
                1 - self.b + self.b * dl / max(self.avg_dl, 1)
            )
            score += idf * numerator / denominator

        return score
