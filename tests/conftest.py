"""Shared fixtures for Sage test suite."""

import json
import socket

import pytest


@pytest.fixture(autouse=True)
def deny_external_network(monkeypatch, request):
    """Keep ordinary tests from using real credentials or external networks."""
    if request.node.get_closest_marker("live"):
        if request.config.getoption("--run-live"):
            return
        pytest.skip("live test requires --run-live")

    monkeypatch.setenv("SAGE_QWEN_API_KEY", "invalid-test-key-no-billing")
    monkeypatch.setenv("SAGE_ALIBABA_ACCESS_KEY_ID", "")
    monkeypatch.setenv("SAGE_ALIBABA_ACCESS_KEY_SECRET", "")

    original_create_connection = socket.create_connection
    original_socket_connect = socket.socket.connect

    def is_loopback(address):
        if not isinstance(address, tuple) or not address:
            return True
        return str(address[0]).lower() in {"127.0.0.1", "::1", "localhost"}

    def guarded_create_connection(address, *args, **kwargs):
        if not is_loopback(address):
            raise RuntimeError("external network access is disabled in tests")
        return original_create_connection(address, *args, **kwargs)

    def guarded_socket_connect(sock, address):
        if not is_loopback(address):
            raise RuntimeError("external network access is disabled in tests")
        return original_socket_connect(sock, address)

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect", guarded_socket_connect)


def pytest_addoption(parser):
    """Require an explicit command-line opt-in for live integration tests."""
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run tests marked live (may use external services)",
    )


@pytest.fixture
def proc_mem(tmp_path):
    """ProceduralMemory isolated in a temp directory."""
    from sage.memory.procedural import ProceduralMemory

    return ProceduralMemory(str(tmp_path / "rules" / "rules.md"))


@pytest.fixture
def episodic_mem(tmp_path):
    """EpisodicMemory isolated in a temp directory."""
    from sage.memory.episodic import EpisodicMemory

    return EpisodicMemory(str(tmp_path / "memory" / "episodic"))


@pytest.fixture
def semantic_mem(tmp_path):
    """SemanticMemory isolated in a temp directory."""
    from sage.memory.semantic import SemanticMemory

    return SemanticMemory(str(tmp_path / "knowledge"))


@pytest.fixture
def mock_model_caller():
    """A mock model caller that returns valid JSON reflection responses."""
    from unittest.mock import Mock

    caller = Mock()
    caller.return_value = json.dumps(
        {
            "rule": "Always check security groups before deploying to ECS",
            "context": "Alibaba Cloud ECS deployment",
            "confidence": 0.95,
        }
    )
    return caller


@pytest.fixture
def mock_model_caller_malformed():
    """A mock model caller that returns malformed (non-JSON) responses."""
    from unittest.mock import Mock

    caller = Mock()
    caller.return_value = "I think you should check the security group rules first."
    return caller


@pytest.fixture
def mock_model_caller_empty():
    """A mock model caller that returns an empty string."""
    from unittest.mock import Mock

    caller = Mock()
    caller.return_value = ""
    return caller


@pytest.fixture
def mock_model_caller_error():
    """A mock model caller that raises an exception."""
    from unittest.mock import Mock

    caller = Mock()
    caller.side_effect = ConnectionError("API is down")
    return caller


@pytest.fixture
def mock_model_caller_truncated_json():
    """A mock model caller that returns truncated JSON."""
    from unittest.mock import Mock

    caller = Mock()
    caller.return_value = '{"rule": "Check security groups", "context": "ECS'
    return caller
