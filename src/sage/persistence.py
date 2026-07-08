"""Small, thread-safe primitives for file-backed Sage persistence."""

import json
import os
import tempfile
import threading
import weakref
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

Document = TypeVar("Document")
Record = TypeVar("Record")
Result = TypeVar("Result")

_locks_guard = threading.Lock()
_path_locks: weakref.WeakValueDictionary[Path, threading.RLock] = (
    weakref.WeakValueDictionary()
)


def _lock_for(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _locks_guard:
        return _path_locks.setdefault(resolved, threading.RLock())


def path_transaction(path: Path | str, operation: Callable[[], Result]) -> Result:
    """Run an arbitrary file-state operation under the shared resolved-path lock."""
    with _lock_for(Path(path)):
        return operation()


def atomic_write_text(
    path: Path | str, content: str, *, encoding: str = "utf-8"
) -> None:
    """Durably replace a text file without exposing a partial document."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(target):
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding=encoding,
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, target)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


def atomic_write_bytes(path: Path | str, content: bytes) -> None:
    """Durably replace a binary file without exposing partial content."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(target):
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, target)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


def append_text(path: Path | str, content: str, *, encoding: str = "utf-8") -> None:
    """Append one complete text block under a process-wide per-path lock."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(target):
        with target.open("a", encoding=encoding) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())


def append_jsonl(path: Path | str, value: Any) -> None:
    """Append one compact JSON record as an indivisible line."""
    append_text(path, f"{json.dumps(value, ensure_ascii=False)}\n")


def atomic_write_json(path: Path | str, value: Any, *, indent: int = 2) -> None:
    """Serialize and atomically replace a JSON document."""
    atomic_write_text(path, json.dumps(value, indent=indent, ensure_ascii=False))


class AtomicJsonDocument(Generic[Document]):
    """Own one JSON document's in-process transaction and durability semantics.

    ``update`` holds the path lock across read, mutation, serialization, fsync, and
    atomic replacement. Instances pointing at the same resolved path therefore
    share one transaction seam instead of racing through separate load/save calls.
    """

    def __init__(self, path: Path | str, default_factory: Callable[[], Document]):
        self.path = Path(path)
        self._default_factory = default_factory

    def read(self) -> Document:
        """Return the current document, or a fresh default when it does not exist."""
        with _lock_for(self.path):
            return self._read_unlocked()

    def update(self, mutate: Callable[[Document], Result]) -> Result:
        """Commit one mutation atomically and return the mutator's result."""
        with _lock_for(self.path):
            document = self._read_unlocked()
            result = mutate(document)
            atomic_write_json(self.path, document)
            return result

    def clear(self) -> None:
        """Remove the document without racing an in-process reader or writer."""
        with _lock_for(self.path):
            self.path.unlink(missing_ok=True)

    def _read_unlocked(self) -> Document:
        if not self.path.exists():
            return self._default_factory()
        return json.loads(self.path.read_text(encoding="utf-8"))


class AtomicJsonLines(Generic[Record]):
    """Own a JSONL collection's complete in-process mutation transaction."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def read(self) -> list[Record]:
        """Return every record, rejecting partial or malformed lines."""
        with _lock_for(self.path):
            return self._read_unlocked()

    def update(self, mutate: Callable[[list[Record]], Result]) -> Result:
        """Commit one collection mutation as a complete atomic replacement."""
        with _lock_for(self.path):
            records = self._read_unlocked()
            result = mutate(records)
            content = "".join(
                f"{json.dumps(record, ensure_ascii=False)}\n" for record in records
            )
            atomic_write_text(self.path, content)
            return result

    def clear(self) -> None:
        """Remove the collection without racing an in-process transaction."""
        with _lock_for(self.path):
            self.path.unlink(missing_ok=True)

    def _read_unlocked(self) -> list[Record]:
        if not self.path.exists():
            return []
        records: list[Record] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSONL record at {self.path}:{line_number}"
                ) from exc
        return records
