"""Credential redaction tests."""

import pytest

from sage.memory.semantic import SemanticMemory
from sage.security import redact_sensitive


def test_redacts_environment_and_explicit_secrets(monkeypatch):
    monkeypatch.setenv("SAGE_QWEN_API_KEY", "qwen-secret-value")

    result = redact_sensitive(
        "qwen-secret-value and cloud-secret-value",
        ("cloud-secret-value",),
    )

    assert result == "[REDACTED] and [REDACTED]"


def test_redacts_authorization_values():
    result = redact_sensitive("Authorization: Bearer opaque-token")

    assert result == "Authorization: Bearer [REDACTED]"


def test_semantic_documents_cannot_escape_the_knowledge_directory(tmp_path):
    knowledge = tmp_path / "knowledge"
    secret = tmp_path / "secret.txt"
    secret.write_text("must-not-be-readable", encoding="utf-8")
    memory = SemanticMemory(str(knowledge))

    with pytest.raises(ValueError, match="knowledge directory"):
        memory.get_document("../secret.txt")
    with pytest.raises(ValueError, match="knowledge directory"):
        memory.add_document("../overwrite.txt", "escaped")

    assert not (tmp_path / "overwrite.txt").exists()
