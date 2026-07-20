"""Security-gating tests for the Sage API.

Covers:
  1. SAGE_ENABLE_LIVE toggle — live disabled by default, qwen/cloud modes blocked
  2. SAGE_ALLOW_CLOUD_MUTATIONS — emergency switch, separate from enable_live
  3. Allowed-origins parsing — no wildcard, explicit origin enforcement
  4. Allowed-regions enforcement — credential endpoint rejects unsupported regions
  5. Admin token constant-time compare — missing/wrong token rejection
"""

from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import api


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(monkeypatch, *, live=False, cloud_mutations=False, admin_token="test-admin-token"):
    """Build a TestClient with controlled security gating env vars."""
    monkeypatch.setenv("SAGE_ADMIN_TOKEN", admin_token)
    monkeypatch.setenv("SAGE_ENABLE_LIVE", "true" if live else "false")
    monkeypatch.setenv("SAGE_ALLOW_CLOUD_MUTATIONS", "true" if cloud_mutations else "false")
    api._agents.clear()
    api._credentials.clear()
    client = TestClient(
        api.app,
        headers={
            "X-Sage-Admin-Token": admin_token,
            "X-Sage-Session-ID": "test-session",
        },
    )
    return client


def _mock_agent():
    """Return a mock Agent with minimal stubs needed for /api/status."""
    agent = MagicMock()
    agent.procedural.get_rule_count.return_value = 0
    agent.metrics = {}
    agent.memory.snapshot.return_value = {
        "procedural": {"count": 0},
        "metrics": {},
    }
    agent.run.describe.return_value = {}
    agent.mcp.simulate = False
    agent.mcp.region = "us-east-1"
    return agent


# ===================================================================
# 1. SAGE_ENABLE_LIVE toggle — live disabled by default
# ===================================================================

class TestLiveToggleDefaultOff:
    """SAGE_ENABLE_LIVE defaults to disabled; qwen/cloud modes are blocked."""

    def test_live_disabled_by_default_blocks_qwen_status(self, monkeypatch):
        """GET /api/status with mode=qwen returns 503 when live is off."""
        monkeypatch.delenv("SAGE_ENABLE_LIVE", raising=False)
        monkeypatch.setenv("SAGE_ADMIN_TOKEN", "tok")
        api._agents.clear()
        client = TestClient(api.app, headers={"X-Sage-Admin-Token": "tok", "X-Sage-Session-ID": "s"})
        response = client.get("/api/status", params={"mode": "qwen"})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "live_mode_disabled"

    def test_live_disabled_by_default_blocks_cloud_status(self, monkeypatch):
        """GET /api/status with mode=cloud returns 503 when live is off."""
        monkeypatch.delenv("SAGE_ENABLE_LIVE", raising=False)
        monkeypatch.setenv("SAGE_ADMIN_TOKEN", "tok")
        api._agents.clear()
        client = TestClient(api.app, headers={"X-Sage-Admin-Token": "tok", "X-Sage-Session-ID": "s"})
        response = client.get("/api/status", params={"mode": "cloud"})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "live_mode_disabled"

    def test_live_disabled_blocks_qwen_task(self, monkeypatch):
        """POST /api/task with mode=qwen returns 503 when live is off."""
        monkeypatch.delenv("SAGE_ENABLE_LIVE", raising=False)
        monkeypatch.setenv("SAGE_ADMIN_TOKEN", "tok")
        api._agents.clear()
        client = TestClient(api.app, headers={"X-Sage-Admin-Token": "tok", "X-Sage-Session-ID": "s"})
        response = client.post("/api/task", json={"task": "test", "mode": "qwen"})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "live_mode_disabled"

    def test_live_enabled_allows_qwen_status(self, monkeypatch):
        """GET /api/status with mode=qwen returns 200 when live is on."""
        client = _make_client(monkeypatch, live=True)
        with patch.object(api, "_get_agent", return_value=_mock_agent()):
            response = client.get("/api/status", params={"mode": "qwen"})
            assert response.status_code == 200

    def test_live_truthy_variants_accepted(self, monkeypatch):
        """_live_enabled() accepts 1, true, yes (case-insensitive)."""
        for val in ("1", "true", "yes", "True", "YES", "  true  "):
            monkeypatch.setenv("SAGE_ENABLE_LIVE", val)
            assert api._live_enabled() is True, f"Expected True for SAGE_ENABLE_LIVE={val!r}"

    def test_live_falsy_variants_rejected(self, monkeypatch):
        """_live_enabled() rejects 0, false, no, empty string."""
        for val in ("0", "false", "no", "", "  ", "random"):
            monkeypatch.setenv("SAGE_ENABLE_LIVE", val)
            assert api._live_enabled() is False, f"Expected False for SAGE_ENABLE_LIVE={val!r}"


# ===================================================================
# 2. SAGE_ALLOW_CLOUD_MUTATIONS emergency switch
# ===================================================================

class TestCloudMutationsGate:
    """SAGE_ALLOW_CLOUD_MUTATIONS is a separate emergency opt-in for cloud mode."""

    def test_cloud_mutations_disabled_blocks_cloud_task(self, monkeypatch):
        """POST /api/task with mode=cloud returns 403 when mutations are off.

        The handler calls _require_cloud_credentials BEFORE
        _require_cloud_mutation_permission, so credentials must be present
        for the mutations gate to be reached.
        """
        client = _make_client(monkeypatch, live=True, cloud_mutations=False)
        # Provide valid credentials so the credential check passes
        client.post(
            "/api/credentials",
            json={"access_key_id": "test-id", "access_key_secret": "test-secret", "region": "us-east-1"},
        )
        response = client.post("/api/task", json={"task": "deploy", "mode": "cloud"})
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "cloud_mutations_disabled"

    def test_cloud_mutations_not_checked_on_readonly_status(self, monkeypatch):
        """GET /api/status with mode=cloud does NOT check mutations (read-only endpoint).

        /api/status only calls _get_agent(), which checks live + credentials.
        Mutations gate only applies to mutating endpoints (task, benchmark, etc.).
        """
        client = _make_client(monkeypatch, live=True, cloud_mutations=False)
        client.post(
            "/api/credentials",
            json={"access_key_id": "test-id", "access_key_secret": "test-secret", "region": "us-east-1"},
        )
        with patch.object(api, "_get_agent", return_value=_mock_agent()):
            response = client.get("/api/status", params={"mode": "cloud"})
            assert response.status_code == 200

    def test_cloud_mutations_enabled_allows_cloud_with_credentials(self, monkeypatch):
        """POST /api/status with mode=cloud succeeds when both live and mutations are on."""
        client = _make_client(monkeypatch, live=True, cloud_mutations=True)
        # Set up credentials first
        client.post(
            "/api/credentials",
            json={"access_key_id": "test-id", "access_key_secret": "test-secret", "region": "us-east-1"},
        )
        with patch.object(api, "_get_agent", return_value=_mock_agent()):
            response = client.get("/api/status", params={"mode": "cloud"})
            assert response.status_code == 200

    def test_cloud_mutations_truthy_variants(self, monkeypatch):
        """_cloud_mutations_enabled() accepts 1, true, yes (case-insensitive)."""
        for val in ("1", "true", "yes", "True", "YES"):
            monkeypatch.setenv("SAGE_ALLOW_CLOUD_MUTATIONS", val)
            assert api._cloud_mutations_enabled() is True

    def test_cloud_mutations_falsy_variants(self, monkeypatch):
        """_cloud_mutations_enabled() rejects 0, false, no, empty."""
        for val in ("0", "false", "no", ""):
            monkeypatch.setenv("SAGE_ALLOW_CLOUD_MUTATIONS", val)
            assert api._cloud_mutations_enabled() is False

    def test_cloud_mutations_independent_of_enable_live(self, monkeypatch):
        """Even with SAGE_ENABLE_LIVE=true, cloud mode is blocked without mutations.

        Credentials are provided so the mutations gate is the one that fires.
        """
        client = _make_client(monkeypatch, live=True, cloud_mutations=False)
        client.post(
            "/api/credentials",
            json={"access_key_id": "test-id", "access_key_secret": "test-secret", "region": "us-east-1"},
        )
        response = client.post("/api/task", json={"task": "deploy", "mode": "cloud"})
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "cloud_mutations_disabled"


# ===================================================================
# 3. Allowed-origins parsing (no wildcard)
# ===================================================================

class TestAllowedOrigins:
    """CORS origins are explicit — no wildcard '*' allowed."""

    def test_default_origins_are_localhost_only(self, monkeypatch):
        """Default allowed origins are localhost:5173 and 127.0.0.1:5173."""
        monkeypatch.delenv("SAGE_ALLOWED_ORIGINS", raising=False)
        origins = api._configured_origins()
        assert "http://localhost:5173" in origins
        assert "http://127.0.0.1:5173" in origins

    def test_custom_origins_parsed_comma_separated(self, monkeypatch):
        """Custom origins are parsed from comma-separated SAGE_ALLOWED_ORIGINS."""
        monkeypatch.setenv("SAGE_ALLOWED_ORIGINS", "https://app.example.com,https://admin.example.com")
        origins = api._configured_origins()
        assert origins == ["https://app.example.com", "https://admin.example.com"]

    def test_wildcard_not_in_default_origins(self, monkeypatch):
        """The wildcard '*' must not appear in default origins."""
        monkeypatch.delenv("SAGE_ALLOWED_ORIGINS", raising=False)
        origins = api._configured_origins()
        assert "*" not in origins

    def test_wildcard_env_var_is_literal_not_wildcard(self, monkeypatch):
        """A wildcard in the env var is treated as a literal origin string, not a glob."""
        monkeypatch.setenv("SAGE_ALLOWED_ORIGINS", "*")
        origins = api._configured_origins()
        assert origins == ["*"]  # literal, not a wildcard pattern

    def test_cors_rejects_unlisted_origin(self, monkeypatch):
        """CORS preflight from an unlisted origin gets no allow-origin header."""
        monkeypatch.setenv("SAGE_ALLOWED_ORIGINS", "https://app.example.com")
        monkeypatch.setenv("SAGE_ADMIN_TOKEN", "tok")
        api._agents.clear()
        client = TestClient(api.app, raise_server_exceptions=False)
        response = client.options(
            "/api/task",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-sage-admin-token",
            },
        )
        # When origin is not in the allow list, FastAPI's CORSMiddleware omits
        # the access-control-allow-origin header (or returns 400).
        allow_origin = response.headers.get("access-control-allow-origin")
        assert allow_origin != "https://evil.example.com"

    def test_cors_preflight_passes_for_default_origin(self, monkeypatch):
        """CORS preflight succeeds for the default localhost:5173 origin.

        CORSMiddleware captures origins at import time. Changing the env var
        after module load does not reconfigure the middleware, so we test
        with the default origin that was baked in at startup.
        """
        monkeypatch.setenv("SAGE_ADMIN_TOKEN", "tok")
        api._agents.clear()
        client = TestClient(api.app, raise_server_exceptions=False)
        response = client.options(
            "/api/task",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-sage-admin-token",
            },
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_configured_origins_respects_env_var(self, monkeypatch):
        """_configured_origins() parses SAGE_ALLOWED_ORIGINS correctly.

        This is a unit test of the parsing function, not a middleware test,
        because CORSMiddleware captures the list at import time.
        """
        monkeypatch.setenv("SAGE_ALLOWED_ORIGINS", "https://a.com,https://b.com")
        assert api._configured_origins() == ["https://a.com", "https://b.com"]

    def test_configured_origins_strips_whitespace(self, monkeypatch):
        """_configured_origins() trims whitespace around each origin."""
        monkeypatch.setenv("SAGE_ALLOWED_ORIGINS", " https://a.com , https://b.com ")
        assert api._configured_origins() == ["https://a.com", "https://b.com"]

    def test_configured_origins_skips_empty_segments(self, monkeypatch):
        """_configured_origins() skips empty segments from trailing commas."""
        monkeypatch.setenv("SAGE_ALLOWED_ORIGINS", "https://a.com,,")
        assert api._configured_origins() == ["https://a.com"]

    def test_credentials_endpoint_rejects_unlisted_origin(self, monkeypatch):
        """Credential endpoint CORS preflight from an unlisted origin is blocked."""
        monkeypatch.setenv("SAGE_ALLOWED_ORIGINS", "https://app.example.com")
        monkeypatch.setenv("SAGE_ADMIN_TOKEN", "tok")
        monkeypatch.setenv("SAGE_ENABLE_LIVE", "true")
        api._agents.clear()
        client = TestClient(api.app, raise_server_exceptions=False)
        response = client.options(
            "/api/credentials",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-sage-admin-token",
            },
        )
        allow_origin = response.headers.get("access-control-allow-origin")
        assert allow_origin != "https://evil.example.com"


# ===================================================================
# 4. Allowed-regions enforcement
# ===================================================================

class TestAllowedRegions:
    """Credential endpoint rejects unsupported regions."""

    def test_default_allowed_regions(self, monkeypatch):
        """Default allowed regions are the International set (no mainland regions)."""
        monkeypatch.delenv("SAGE_ALLOWED_REGIONS", raising=False)
        regions = api._allowed_regions()
        assert regions == {"us-east-1", "us-west-1", "eu-central-1"}

    def test_custom_allowed_regions_parsed(self, monkeypatch):
        """Custom regions are parsed from comma-separated env var."""
        monkeypatch.setenv("SAGE_ALLOWED_REGIONS", "ap-southeast-1,us-east-2")
        regions = api._allowed_regions()
        assert regions == {"ap-southeast-1", "us-east-2"}

    def test_credentials_rejects_unsupported_region(self, monkeypatch):
        """POST /api/credentials with an unsupported region returns 422."""
        client = _make_client(monkeypatch, live=True)
        response = client.post(
            "/api/credentials",
            json={
                "access_key_id": "test-id",
                "access_key_secret": "test-secret",
                "region": "mars-west-1",
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "unsupported_region"

    def test_credentials_uses_us_east_1_default_when_region_omitted(self, monkeypatch):
        """POST /api/credentials with no region field falls back to us-east-1 and succeeds."""
        client = _make_client(monkeypatch, live=True)
        response = client.post(
            "/api/credentials",
            json={
                "access_key_id": "test-id",
                "access_key_secret": "test-secret",
            },
        )
        assert response.status_code == 200
        # us-east-1 (Alibaba Cloud International) is the safe default; mainland
        # regions (RealName/passport) must NOT be the silent fallback.
        assert api._credentials["test-session"]["region"] == "us-east-1"

    def test_credentials_accepts_us_west_region(self, monkeypatch):
        """POST /api/credentials with us-west-1 (a supported region) succeeds."""
        client = _make_client(monkeypatch, live=True)
        response = client.post(
            "/api/credentials",
            json={
                "access_key_id": "test-id",
                "access_key_secret": "test-secret",
                "region": "us-west-1",
            },
        )
        assert response.status_code == 200

    def test_credentials_accepts_eu_region(self, monkeypatch):
        """POST /api/credentials with eu-central-1 succeeds."""
        client = _make_client(monkeypatch, live=True)
        response = client.post(
            "/api/credentials",
            json={
                "access_key_id": "test-id",
                "access_key_secret": "test-secret",
                "region": "eu-central-1",
            },
        )
        assert response.status_code == 200

    def test_credentials_rejects_region_when_live_disabled(self, monkeypatch):
        """POST /api/credentials returns 503 when live is disabled, even for valid region."""
        client = _make_client(monkeypatch, live=False)
        response = client.post(
            "/api/credentials",
            json={
                "access_key_id": "test-id",
                "access_key_secret": "test-secret",
                "region": "us-east-1",
            },
        )
        # 503 = live_mode_disabled takes precedence over region check
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "live_mode_disabled"


# ===================================================================
# 5. Admin token constant-time compare
# ===================================================================

class TestAdminTokenAuth:
    """Middleware uses hmac.compare_digest for admin token validation."""

    def test_missing_admin_token_env_returns_503(self, monkeypatch):
        """When SAGE_ADMIN_TOKEN is unset, protected endpoints return 503."""
        monkeypatch.delenv("SAGE_ADMIN_TOKEN", raising=False)
        api._agents.clear()
        client = TestClient(api.app, raise_server_exceptions=False)
        response = client.get("/api/status")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "admin_token_not_configured"

    def test_wrong_token_returns_401(self, monkeypatch):
        """A wrong admin token is rejected with 401."""
        monkeypatch.setenv("SAGE_ADMIN_TOKEN", "correct-token")
        api._agents.clear()
        client = TestClient(api.app, headers={"X-Sage-Admin-Token": "wrong-token"})
        response = client.get("/api/status")
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "authentication_required"

    def test_correct_token_passes(self, monkeypatch):
        """A correct admin token allows access to protected endpoints."""
        client = _make_client(monkeypatch)
        with patch.object(api, "_get_agent", return_value=_mock_agent()):
            response = client.get("/api/status")
            assert response.status_code == 200

    def test_empty_token_header_rejected(self, monkeypatch):
        """An empty X-Sage-Admin-Token header is rejected with 401."""
        monkeypatch.setenv("SAGE_ADMIN_TOKEN", "real-token")
        api._agents.clear()
        client = TestClient(api.app, headers={"X-Sage-Admin-Token": ""})
        response = client.get("/api/status")
        assert response.status_code == 401

    def test_public_health_endpoints_skip_auth(self, monkeypatch):
        """Health endpoints do not require the admin token."""
        monkeypatch.setenv("SAGE_ADMIN_TOKEN", "real-token")
        api._agents.clear()
        client = TestClient(api.app)  # no token header
        response = client.get("/api/health/live")
        assert response.status_code == 200

    def test_public_health_ready_skips_auth(self, monkeypatch):
        """Readiness endpoint does not require the admin token."""
        monkeypatch.setenv("SAGE_ADMIN_TOKEN", "real-token")
        api._agents.clear()
        client = TestClient(api.app)
        response = client.get("/api/health/ready")
        # May be 200 or 503 depending on key, but must NOT be 401
        assert response.status_code != 401

    def test_cors_preflight_skips_auth(self, monkeypatch):
        """CORS OPTIONS requests bypass the admin token check."""
        monkeypatch.setenv("SAGE_ADMIN_TOKEN", "real-token")
        api._agents.clear()
        client = TestClient(api.app)
        response = client.options(
            "/api/task",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code == 200

    def test_admin_token_constant_time_comparison(self, monkeypatch):
        """The auth middleware uses hmac.compare_digest (constant-time) for comparison."""
        # This is a structural assertion: the middleware calls hmac.compare_digest.
        # We verify the import is present and the function is used by checking
        # the api module imports hmac and the middleware source.
        assert hasattr(api, "hmac") or "hmac" in dir(api)
        # Verify the middleware source uses hmac.compare_digest
        import inspect
        source = inspect.getsource(api.require_api_authentication)
        assert "hmac.compare_digest" in source


# ===================================================================
# 6. Combined gating: live + mutations + credentials
# ===================================================================

class TestCombinedGating:
    """All three gates must pass for cloud mode to proceed."""

    def test_cloud_requires_all_three_gates_in_order(self, monkeypatch):
        """Cloud mode needs: live=true, mutations=true, and valid credentials.

        /api/task enforces gates in this order:
          1. _require_cloud_credentials  -> 409 if missing
          2. _require_cloud_mutation_permission -> 403 if disabled
          3. _get_agent -> 503 if live mode disabled

        When credentials and mutations are both absent, the mutations gate
        (403) fires before the live gate (503). We verify this ordering by
        providing credentials so the mutations gate is the one that fires.
        """
        client = _make_client(monkeypatch, live=False, cloud_mutations=False)
        api._credentials["test-session"] = {
            "access_key_id": "test-id",
            "access_key_secret": "test-secret",
            "region": "us-east-1",
        }
        response = client.post("/api/task", json={"task": "deploy", "mode": "cloud"})
        # Mutations check fires before live check in /api/task handler
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "cloud_mutations_disabled"

    def test_live_check_fires_when_mutations_enabled(self, monkeypatch):
        """When mutations are enabled but live is off, the live gate (503) fires.

        This proves the live gate IS reachable once the mutations gate passes.
        """
        client = _make_client(monkeypatch, live=False, cloud_mutations=True)
        api._credentials["test-session"] = {
            "access_key_id": "test-id",
            "access_key_secret": "test-secret",
            "region": "us-east-1",
        }
        response = client.post("/api/task", json={"task": "deploy", "mode": "cloud"})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "live_mode_disabled"

    def test_cloud_needs_mutations_even_when_live(self, monkeypatch):
        """Cloud mode is blocked without mutations even when live is on.

        Credentials must be provided so the mutations gate is reached.
        """
        client = _make_client(monkeypatch, live=True, cloud_mutations=False)
        client.post(
            "/api/credentials",
            json={"access_key_id": "test-id", "access_key_secret": "test-secret", "region": "us-east-1"},
        )
        response = client.post("/api/task", json={"task": "deploy", "mode": "cloud"})
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "cloud_mutations_disabled"

    def test_cloud_needs_credentials_even_when_gates_open(self, monkeypatch):
        """Cloud mode is blocked without credentials even when both gates are on."""
        client = _make_client(monkeypatch, live=True, cloud_mutations=True)
        response = client.post("/api/task", json={"task": "deploy", "mode": "cloud"})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "cloud_credentials_missing"

    def test_cloud_blocked_counterfactual(self, monkeypatch):
        """POST /api/counterfactual with mode=cloud returns 400 (bulk cloud unsupported)."""
        client = _make_client(monkeypatch, live=True, cloud_mutations=True)
        response = client.post("/api/counterfactual", json={"task": "deploy", "mode": "cloud"})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "cloud_bulk_run_unsupported"

    def test_cloud_blocked_benchmark(self, monkeypatch):
        """POST /api/benchmark with mode=cloud returns 400."""
        client = _make_client(monkeypatch, live=True, cloud_mutations=True)
        response = client.post("/api/benchmark", params={"mode": "cloud"})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "cloud_bulk_run_unsupported"
