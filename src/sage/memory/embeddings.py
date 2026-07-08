"""
Embedding Store — Vector-backed memory retrieval using Qwen text-embedding-v4.

Provides semantic similarity search over memory entries (rules, cases, episodes,
skills) by embedding text into dense vectors and computing cosine similarity.

Architecture:
- Embeddings computed via Qwen Cloud text-embedding-v4 (OpenAI-compatible API)
- Vectors and metadata stored in one atomic numpy snapshot (.npz)
- Supports batch embedding (up to 10 texts/call, 8192 tokens/text)
- Graceful fallback: if API unavailable, returns empty results (never crashes)

This replaces keyword-overlap retrieval with genuine semantic understanding:
"set up firewall rules" matches "configure security group" even without shared words.
"""

import io
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from sage.persistence import atomic_write_bytes, path_transaction
from sage.security import redact_sensitive

logger = logging.getLogger(__name__)

from sage.closeable import CloseableMixin  # noqa: E402

# Try numpy; provide stub if not installed
try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False
    logger.info(
        "numpy not installed — embedding store disabled (install with: pip install numpy)"
    )

# Try httpx for HTTP calls (same as model_caller.py)
try:
    import httpx

    HAS_HTTPX = True
except ImportError:
    import urllib.request
    import urllib.error

    HAS_HTTPX = False


# ─── Constants ───────────────────────────────────────────────────────────────

DEFAULT_MODEL = "text-embedding-v4"
DEFAULT_DIMENSIONS = 1024
MAX_BATCH_SIZE = 10
MAX_TOKENS_PER_TEXT = 8192
DEFAULT_ENDPOINT = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
ENV_API_KEY = "SAGE_QWEN_API_KEY"
ENV_EMBEDDING_API_KEY = "SAGE_EMBEDDING_API_KEY"
ENV_EMBEDDING_MODEL = "SAGE_EMBEDDING_MODEL"
ENV_EMBEDDING_DIMENSIONS = "SAGE_EMBEDDING_DIMENSIONS"
ENV_EMBEDDING_ENDPOINT = "SAGE_EMBEDDING_ENDPOINT"


class EmbeddingStore(CloseableMixin):
    """
    Vector store backed by Qwen text-embedding-v4 + local numpy persistence.

    Usage:
        store = EmbeddingStore("memory/vectors")
        store.add("Always configure security groups first", {"type": "rule", "id": "R001"})
        results = store.query("set up firewall before deploying", top_k=3)
        # → [{"text": "Always configure...", "metadata": {...}, "score": 0.87}, ...]
    """

    def __init__(
        self,
        store_dir: str = "memory/vectors",
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
        endpoint: Optional[str] = None,
    ):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

        self.model = (
            model
            or os.environ.get(ENV_EMBEDDING_MODEL, "").strip()
            or DEFAULT_MODEL
        )
        configured_dimensions = dimensions
        if configured_dimensions is None:
            raw_dimensions = os.environ.get(ENV_EMBEDDING_DIMENSIONS, "").strip()
            configured_dimensions = (
                int(raw_dimensions) if raw_dimensions else DEFAULT_DIMENSIONS
            )
        if configured_dimensions <= 0:
            raise ValueError("embedding dimensions must be positive")
        self.dimensions = configured_dimensions
        self.endpoint = (
            endpoint
            or os.environ.get(ENV_EMBEDDING_ENDPOINT, "").strip()
            or DEFAULT_ENDPOINT
        ).rstrip("/")
        self.api_key = (
            api_key
            if api_key is not None
            else os.environ.get(ENV_EMBEDDING_API_KEY, "").strip()
            or os.environ.get(ENV_API_KEY, "")
        )

        # File paths
        self._vectors_path = self.store_dir / "vectors.npz"
        self._metadata_path = self.store_dir / "metadata.jsonl"

        # In-memory state
        self._vectors: Optional["np.ndarray"] = None  # shape: (N, dimensions)
        self._metadata: list[dict] = []
        self._lock = threading.Lock()

        # Usage tracking
        self._total_api_calls = 0
        self._total_tokens_embedded = 0

        # Persistent httpx client for connection pooling (same pattern as ModelCaller)
        self._httpx_client: Optional["httpx.Client"] = None
        if HAS_HTTPX:
            self._httpx_client = httpx.Client(
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                follow_redirects=True,
            )

        # Load existing store
        self._load()

    # ─── Public API ──────────────────────────────────────────────────────────

    def add(self, text: str, metadata: Optional[dict] = None) -> bool:
        """Embed a text and store it with metadata.

        Returns True on success, False if embedding failed (API unavailable).
        """
        if not text or not text.strip():
            return False
        return self.add_batch([text], [metadata or {}])

    def add_batch(self, texts: list[str], metadatas: list[dict]) -> bool:
        """Embed and store multiple texts at once.

        Handles batching internally (max 10 per API call).
        Returns True if all embeddings succeeded.
        """
        if not HAS_NUMPY:
            logger.debug("Embedding store disabled (numpy not installed)")
            return False
        if not texts:
            return True

        # Filter empty texts, keep metadata aligned
        pairs = [(t, m) for t, m in zip(texts, metadatas) if t and t.strip()]
        if not pairs:
            return True

        clean_texts = [t for t, _ in pairs]
        clean_metas = [m for _, m in pairs]

        # Embed in batches of MAX_BATCH_SIZE
        all_vectors = []
        for i in range(0, len(clean_texts), MAX_BATCH_SIZE):
            batch = clean_texts[i : i + MAX_BATCH_SIZE]
            vectors = self._embed(batch)
            if vectors is None:
                return False
            all_vectors.extend(vectors)

        if len(all_vectors) != len(clean_texts):
            logger.warning(
                "Embedding count mismatch: %d texts → %d vectors",
                len(clean_texts),
                len(all_vectors),
            )
            return False

        # Store
        with self._lock:
            def commit() -> None:
                self._load()
                new_vectors = np.array(all_vectors, dtype=np.float32)
                if self._vectors is not None and len(self._vectors) > 0:
                    self._vectors = np.vstack([self._vectors, new_vectors])
                else:
                    self._vectors = new_vectors

                for text, meta in zip(clean_texts, clean_metas, strict=True):
                    self._metadata.append({"text": text, **meta})

                self._save()

            path_transaction(self._vectors_path, commit)

        return True

    def query(
        self,
        text: str,
        top_k: int = 5,
        filter_fn: Optional[callable] = None,
        min_score: float = 0.0,
    ) -> list[dict]:
        """Retrieve the top-K most similar entries to the query text.

        Args:
            text: Query string to embed and search for.
            top_k: Maximum number of results to return.
            filter_fn: Optional predicate on metadata dicts to pre-filter candidates.
            min_score: Minimum cosine similarity threshold (0.0 to 1.0).

        Returns:
            List of dicts with keys: text, score, and all metadata fields.
            Sorted by descending similarity score.
        """
        if not HAS_NUMPY:
            return []
        with self._lock:
            path_transaction(self._vectors_path, self._load)
            if self._vectors is None or len(self._vectors) == 0:
                return []
        if not text or not text.strip():
            return []

        query_vec = self._embed([text])
        if query_vec is None or len(query_vec) == 0:
            return []

        q = np.array(query_vec[0], dtype=np.float32)

        with self._lock:
            # Cosine similarity: dot(q, v) / (|q| * |v|)
            # Vectors are L2-normalized at storage time, so dot product = cosine sim
            scores = self._vectors @ q

            # Apply filter if provided
            if filter_fn:
                mask = np.array([filter_fn(m) for m in self._metadata], dtype=bool)
                scores = np.where(mask, scores, -1.0)

            # Get top-K indices
            if len(scores) <= top_k:
                top_indices = np.argsort(scores)[::-1]
            else:
                # Partial sort for efficiency
                top_indices = np.argpartition(scores, -top_k)[-top_k:]
                top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        results = []
        for idx in top_indices:
            score = float(scores[idx])
            if score < min_score:
                break
            meta = self._metadata[idx].copy()
            results.append({**meta, "score": round(score, 4)})

        return results

    def forget(self, filter_fn: callable) -> int:
        """Remove entries matching a filter predicate.

        Args:
            filter_fn: Function(metadata_dict) → bool. True = remove.

        Returns:
            Number of entries removed.
        """
        if not HAS_NUMPY:
            return 0

        with self._lock:
            def commit() -> int:
                self._load()
                if self._vectors is None:
                    return 0
                keep_mask = np.array(
                    [not filter_fn(metadata) for metadata in self._metadata],
                    dtype=bool,
                )
                removed = int(np.sum(~keep_mask))

                if removed == 0:
                    return 0

                self._vectors = self._vectors[keep_mask]
                self._metadata = [
                    metadata
                    for metadata, keep in zip(
                        self._metadata, keep_mask, strict=True
                    )
                    if keep
                ]
                self._save()
                return removed

            return path_transaction(self._vectors_path, commit)

    def forget_by_score(self, query: str, threshold: float = 0.3) -> int:
        """Remove entries too similar to a query (for contradiction resolution).

        Removes any entry with cosine similarity > threshold to the query.
        """
        if not HAS_NUMPY or self._vectors is None or len(self._vectors) == 0:
            return 0

        query_vec = self._embed([query])
        if query_vec is None:
            return 0

        q = np.array(query_vec[0], dtype=np.float32)
        scores = self._vectors @ q

        # Use index-based lookup (O(n) not O(n^2))
        with self._lock:
            keep_mask = scores <= threshold
            removed = int(np.sum(~keep_mask))
            if removed == 0:
                return 0
            self._vectors = self._vectors[keep_mask]
            self._metadata = [m for m, keep in zip(self._metadata, keep_mask) if keep]
            self._save()

        return removed

    @property
    def size(self) -> int:
        """Number of entries in the store."""
        return len(self._metadata)

    def get_stats(self) -> dict:
        """Return store statistics."""
        return {
            "entries": self.size,
            "dimensions": self.dimensions,
            "model": self.model,
            "api_calls": self._total_api_calls,
            "tokens_embedded": self._total_tokens_embedded,
            "store_path": str(self.store_dir),
            "has_numpy": HAS_NUMPY,
            "has_api_key": bool(self.api_key),
        }

    def clear(self):
        """Remove all entries."""
        with self._lock:
            def clear_snapshot() -> None:
                self._vectors = None
                self._metadata = []
                self._vectors_path.unlink(missing_ok=True)
                self._metadata_path.unlink(missing_ok=True)

            path_transaction(self._vectors_path, clear_snapshot)

    def close(self):
        """Close the httpx client (release connection pool)."""
        if self._httpx_client:
            self._httpx_client.close()
            self._httpx_client = None

    # ─── Private: Embedding API ──────────────────────────────────────────────

    def _embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        """Call Qwen text-embedding API. Returns list of vectors or None on failure.

        Uses the OpenAI-compatible endpoint at dashscope-intl.
        """
        if not self.api_key:
            logger.debug("No API key — embedding skipped")
            return None

        payload = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
            "dimensions": self.dimensions,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.endpoint}/embeddings"

        try:
            result = self._http_post(url, headers, payload)
            self._total_api_calls += 1

            data = result.get("data", [])
            if not data:
                logger.warning("Embedding API returned empty data")
                return None

            # Track usage
            if usage := result.get("usage"):
                self._total_tokens_embedded += usage.get("total_tokens", 0)

            # Extract and L2-normalize vectors
            vectors = []
            for item in sorted(data, key=lambda x: x.get("index", 0)):
                vec = item.get("embedding", [])
                if len(vec) != self.dimensions:
                    # Handle dimension mismatch gracefully
                    if len(vec) > self.dimensions:
                        vec = vec[: self.dimensions]
                    else:
                        vec.extend([0.0] * (self.dimensions - len(vec)))
                vectors.append(self._normalize(vec))

            return vectors

        except Exception as e:
            logger.warning("Embedding API call failed: %s", redact_sensitive(e))
            return None

    def _http_post(self, url: str, headers: dict, payload: dict) -> dict:
        """HTTP POST with persistent httpx client or urllib fallback."""
        if HAS_HTTPX and self._httpx_client is not None:
            response = self._httpx_client.post(url, json=payload, headers=headers)
            if response.status_code != 200:
                raise RuntimeError(
                    f"Embedding API error {response.status_code}: "
                    f"{redact_sensitive(response.text[:200], (self.api_key,))}"
                )
            return response.json()
        else:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())

    @staticmethod
    def _normalize(vec: list[float]) -> list[float]:
        """L2-normalize a vector (so dot product = cosine similarity)."""
        if not HAS_NUMPY:
            return vec
        arr = np.array(vec, dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr = arr / norm
        return arr.tolist()

    # ─── Private: Persistence ────────────────────────────────────────────────

    def _load(self):
        """Load vectors and metadata from disk."""
        if not HAS_NUMPY:
            return

        metadata_loaded = False
        if self._vectors_path.exists():
            try:
                with np.load(self._vectors_path, allow_pickle=False) as loaded:
                    self._vectors = loaded["vectors"].astype(np.float32)
                    if "metadata_json" in loaded.files:
                        metadata_json = str(loaded["metadata_json"].item())
                        metadata = json.loads(metadata_json)
                        if not isinstance(metadata, list):
                            raise ValueError("embedding metadata must be a list")
                        self._metadata = metadata
                        metadata_loaded = True
            except (ValueError, OSError) as e:
                logger.warning("Failed to load embedding snapshot: %s", e)
                self._vectors = None
                self._metadata = []

        # Backward compatibility for snapshots written before metadata was bundled.
        if not metadata_loaded and self._metadata_path.exists():
            self._metadata = []
            try:
                with open(self._metadata_path) as f:
                    for line in f:
                        if line.strip():
                            try:
                                self._metadata.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
            except (OSError, IOError) as e:
                logger.warning("Failed to load metadata: %s", e)

        # Validate alignment
        if self._vectors is not None and len(self._vectors) != len(self._metadata):
            logger.warning(
                "Vector/metadata mismatch (%d vs %d) — resetting store",
                len(self._vectors),
                len(self._metadata),
            )
            self._vectors = None
            self._metadata = []

    def _save(self):
        """Persist vectors and metadata through one atomic snapshot commit."""
        if not HAS_NUMPY:
            return

        try:
            if self._vectors is not None and len(self._vectors) > 0:
                if len(self._vectors) != len(self._metadata):
                    raise ValueError(
                        "embedding vectors and metadata must have equal lengths"
                    )
                snapshot = io.BytesIO()
                np.savez_compressed(
                    snapshot,
                    vectors=self._vectors,
                    metadata_json=np.array(
                        json.dumps(self._metadata, ensure_ascii=False)
                    ),
                )
                atomic_write_bytes(self._vectors_path, snapshot.getvalue())
                self._metadata_path.unlink(missing_ok=True)
            elif self._vectors_path.exists():
                self._vectors_path.unlink()
                self._metadata_path.unlink(missing_ok=True)
        except (OSError, ValueError) as e:
            logger.error("Failed to save embedding snapshot: %s", e)
            raise


# ─── Convenience: Shared instance factory ────────────────────────────────────

_shared_store: Optional[EmbeddingStore] = None
_shared_lock = threading.Lock()


def get_shared_store(
    store_dir: str = "memory/vectors", api_key: Optional[str] = None
) -> EmbeddingStore:
    """Get or create a shared embedding store instance (singleton per process)."""
    global _shared_store
    with _shared_lock:
        if _shared_store is None:
            _shared_store = EmbeddingStore(store_dir=store_dir, api_key=api_key)
        return _shared_store


if __name__ == "__main__":
    store = EmbeddingStore("/tmp/test_embeddings")
    print(f"Store initialized: {store.get_stats()}")

    if store.api_key:
        success = store.add(
            "Always configure security group before deploying to ECS",
            {"type": "rule", "id": "R001", "confidence": 0.95},
        )
        print(f"Added rule: {success}")

        results = store.query("set up firewall rules before launching server")
        print(f"Query results: {json.dumps(results, indent=2)}")
    else:
        print("No API key — set SAGE_QWEN_API_KEY to test embedding")
