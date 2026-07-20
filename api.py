"""FastAPI backend for Sage — wraps the existing Agent for the React frontend."""

import json
import asyncio
import hashlib
import hmac
import os
import re
import threading
from contextlib import asynccontextmanager
from contextvars import ContextVar
from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, StringConstraints

from sage.agent import Agent
from sage.benchmark import run_benchmark, format_benchmark_summary
from sage.demo_runner import run_demo
from sage.jobs import JobManager
from sage.run import RunContext
from sage.env_config import load_dotenv
from sage.tools.mcp_client import MCPClientError
from sage.tools.model_caller import ModelCallerError

REPO_ROOT = Path(__file__).resolve().parent
MODELS_CONFIG_PATH = REPO_ROOT / "config" / "models.json"

# Available Qwen models for selection
AVAILABLE_MODELS = ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-long"]

# Default model config (matches ModelCaller.DEFAULT_MODEL_MAP)
DEFAULT_MODEL_CONFIG = {
    "execution": "qwen-turbo",
    "reflection": "qwen-max",
    "planning": "qwen-plus",
}


def _load_model_config() -> dict[str, str]:
    """Load model config from file and environment, falling back to defaults."""
    config = dict(DEFAULT_MODEL_CONFIG)
    try:
        if MODELS_CONFIG_PATH.exists():
            data = json.loads(MODELS_CONFIG_PATH.read_text(encoding="utf-8"))
            # Merge with defaults so new task types are always present
            config.update(data)
    except (json.JSONDecodeError, OSError):
        pass

    environment_models = {
        "execution": os.environ.get("SAGE_EXECUTION_MODEL", "").strip(),
        "reflection": os.environ.get("SAGE_REFLECTION_MODEL", "").strip(),
        "planning": os.environ.get("SAGE_PLANNING_MODEL", "").strip(),
    }
    config.update({key: value for key, value in environment_models.items() if value})
    return config


def _save_model_config(config: dict[str, str]) -> None:
    """Persist model config to disk."""
    MODELS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODELS_CONFIG_PATH.write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )


def load_api_environment(project_dir: Path | str = REPO_ROOT) -> None:
    """Load project configuration before authentication and safety checks run."""
    load_dotenv(str(project_dir))


load_api_environment()

PROJECT_DIR = REPO_ROOT / ".local" / "ui"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    shutdown_resources()


app = FastAPI(
    title="Sage API",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


def _configured_origins() -> list[str]:
    """Return explicit browser origins; wildcard credentialed CORS is unsafe."""
    raw = os.environ.get(
        "SAGE_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _allowed_regions() -> set[str]:
    raw = os.environ.get(
        "SAGE_ALLOWED_REGIONS",
        "us-east-1,us-west-1,eu-central-1",
    )
    return {region.strip() for region in raw.split(",") if region.strip()}


app.add_middleware(
    CORSMiddleware,
    allow_origins=_configured_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Content-Type", "X-Sage-Admin-Token", "X-Sage-Session-ID"],
)

# Resolve frontend dist path
FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"


class ExecutionMode(str, Enum):
    offline = "offline"  # Heuristic model, in-memory sandbox
    qwen = "qwen"  # Qwen LLM, in-memory sandbox
    cloud = "cloud"  # Qwen LLM + real Alibaba Cloud MCP


READ_ONLY_TASK_TOOLS = [
    "list_instances",
    "list_security_groups",
    "get_state",
    "finish",
]


_current_session: ContextVar[str] = ContextVar("sage_session_id", default="default")
_agents: dict[tuple[str, str], Agent] = {}
_credentials: dict[str, dict[str, str]] = {}
_state_lock = threading.RLock()
_request_locks: dict[tuple[int, str], asyncio.Lock] = {}
_agent_execution_locks: dict[tuple[str, str], threading.RLock] = {}
_jobs = JobManager(
    max_workers=int(os.environ.get("SAGE_JOB_WORKERS", "2")),
    max_retained=int(os.environ.get("SAGE_MAX_RETAINED_JOBS", "500")),
)

_PUBLIC_API_PATHS = {"/api/health/live", "/api/health/ready", "/api/openapi.json"}
_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"detail": {"code": code, "message": message}}
    )


@app.middleware("http")
async def require_api_authentication(request: Request, call_next):
    """Protect API state and spend behind one configured administration token."""
    path = request.url.path
    if (
        request.method != "OPTIONS"
        and path.startswith("/api")
        and path not in _PUBLIC_API_PATHS
        and path != "/api/docs"
    ):
        expected = os.environ.get("SAGE_ADMIN_TOKEN", "").strip()
        if not expected:
            return _error(
                503,
                "admin_token_not_configured",
                "Set SAGE_ADMIN_TOKEN in the server environment or .env, then restart; this is separate from SAGE_QWEN_API_KEY",
            )
        supplied = request.headers.get("X-Sage-Admin-Token", "")
        if not hmac.compare_digest(supplied, expected):
            return _error(
                401, "authentication_required", "A valid X-Sage-Admin-Token is required"
            )

        session_id = request.headers.get("X-Sage-Session-ID", "default")
        if not _SESSION_PATTERN.fullmatch(session_id):
            return _error(
                400,
                "invalid_session_id",
                "X-Sage-Session-ID must be 1-64 safe characters",
            )
        token = _current_session.set(session_id)
        try:
            loop_key = (id(asyncio.get_running_loop()), session_id)
            with _state_lock:
                request_lock = _request_locks.setdefault(loop_key, asyncio.Lock())
            async with request_lock:
                return await call_next(request)
        finally:
            _current_session.reset(token)
    return await call_next(request)


def _live_enabled() -> bool:
    return os.environ.get("SAGE_ENABLE_LIVE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _cloud_mutations_enabled() -> bool:
    return os.environ.get("SAGE_ALLOW_CLOUD_MUTATIONS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _require_cloud_mutation_permission(
    mode: ExecutionMode, *, read_only: bool = False
) -> None:
    if mode == ExecutionMode.cloud and not read_only and not _cloud_mutations_enabled():
        raise HTTPException(
            status_code=403,
            detail={
                "code": "cloud_mutations_disabled",
                "message": "Set SAGE_ALLOW_CLOUD_MUTATIONS=true after approving real cloud changes",
            },
        )


def _require_cloud_credentials(mode: ExecutionMode, *, read_only: bool = False) -> None:
    credentials = _session_credentials()
    if mode == ExecutionMode.cloud and not read_only and not (
        credentials.get("access_key_id") and credentials.get("access_key_secret")
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cloud_credentials_missing",
                "message": "Cloud mode requires credentials",
            },
        )


def _reject_bulk_cloud_run(mode: ExecutionMode, feature: str) -> None:
    if mode == ExecutionMode.cloud:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "cloud_bulk_run_unsupported",
                "message": (
                    f"{feature} can create multiple billable resources and is disabled "
                    "in Cloud mode; run it in Offline or Qwen mode"
                ),
            },
        )


def _session_credentials() -> dict[str, str]:
    return _credentials.setdefault(_current_session.get(), {})


def _session_project_dir() -> Path:
    digest = hashlib.sha256(_current_session.get().encode("utf-8")).hexdigest()[:16]
    return PROJECT_DIR / "sessions" / digest


def _close_agent(agent: Agent) -> None:
    close = getattr(agent, "close", None)
    if callable(close):
        close()


def _discard_agent(mode: ExecutionMode) -> None:
    key = (_current_session.get(), mode.value)
    with _agent_execution_lock(mode):
        with _state_lock:
            agent = _agents.pop(key, None)
        if agent is not None:
            _close_agent(agent)


def _agent_execution_lock(mode: ExecutionMode) -> threading.RLock:
    key = (_current_session.get(), mode.value)
    with _state_lock:
        return _agent_execution_locks.setdefault(key, threading.RLock())


def _run_context(mode: ExecutionMode) -> RunContext:
    """Build adapter-owned context for the shared Run interface."""
    credentials = _session_credentials()
    return RunContext(
        mode=mode.value,
        provider=(
            "offline"
            if mode == ExecutionMode.offline
            else os.environ.get("SAGE_MODEL_PROVIDER", "qwen").strip().lower()
            or "qwen"
        ),
        region=credentials.get("region") if mode == ExecutionMode.cloud else None,
        session_id=_current_session.get(),
    )


@app.exception_handler(MCPClientError)
async def handle_mcp_error(_request: Request, exc: MCPClientError):
    return _error(
        503 if exc.retryable else 409,
        "cloud_unavailable",
        "Cloud provider request failed; inspect redacted server logs",
    )


@app.exception_handler(ModelCallerError)
async def handle_model_error(_request: Request, exc: ModelCallerError):
    return _error(
        503 if exc.retryable else 409,
        "model_unavailable",
        "Model provider request failed; inspect redacted server logs",
    )


def _resolve_mode(
    mode: Optional[ExecutionMode] = None, online: Optional[bool] = None
) -> ExecutionMode:
    """Resolve execution mode from either new `mode` param or legacy `online` bool."""
    if mode:
        return mode
    if online:
        return ExecutionMode.qwen
    return ExecutionMode.offline


def _build_agent(
    mode: ExecutionMode, credentials: dict[str, str], *, read_only: bool = False
) -> Agent:
    """Construct an uncached Agent for the current isolated session."""
    project_dir = str(_session_project_dir())
    model_config = _load_model_config()
    if mode == ExecutionMode.cloud:
        has_creds = bool(credentials.get("access_key_id")) and bool(
            credentials.get("access_key_secret")
        )
        # A read-only inspection without configured credentials must not be
        # blocked: fall back to a simulated sandbox so inventory tools still
        # run. Mutating cloud runs keep requiring real credentials below.
        if not read_only or has_creds:
            return Agent(
                project_dir=project_dir,
                simulate=False,
                use_qwen=True,
                access_key_id=credentials["access_key_id"],
                access_key_secret=credentials["access_key_secret"],
                region=credentials.get("region", "us-east-1"),
                strict_cloud=True,
                model_config=model_config,
            )
        # Read-only cloud with no credentials: simulated inventory sandbox.
        return Agent(
            project_dir=project_dir, simulate=True, use_qwen=True, model_config=model_config
        )
    if mode == ExecutionMode.qwen:
        return Agent(project_dir=project_dir, simulate=True, use_qwen=True, model_config=model_config)

    from sage.demo_runner import _offline_reflection_model

    return Agent(
        project_dir=project_dir,
        model_caller=_offline_reflection_model,
        simulate=True,
    )


def _get_agent(
    mode: ExecutionMode = ExecutionMode.offline, *, read_only: bool = False
) -> Agent:
    """Validate policy and return the cached Agent for this session and mode."""
    if mode != ExecutionMode.offline and not _live_enabled():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "live_mode_disabled",
                "message": "Live model and cloud modes are disabled",
            },
        )

    _require_cloud_credentials(mode, read_only=read_only)
    credentials = _session_credentials()

    key = (_current_session.get(), mode.value)
    with _state_lock:
        existing = _agents.get(key)
    if existing is not None:
        return existing

    with _state_lock:
        if key in _agents:
            return _agents[key]
        _agents[key] = _build_agent(mode, credentials, read_only=read_only)
        return _agents[key]


TaskText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12_000)
]
DetailText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)
]
IdentifierText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class TaskRequest(BaseModel):
    task: TaskText
    mode: Optional[ExecutionMode] = None
    online: Optional[bool] = None  # legacy
    read_only: bool = False


class CorrectionRequest(BaseModel):
    task: TaskText
    action_taken: DetailText
    error: DetailText
    fix: DetailText
    mode: Optional[ExecutionMode] = None
    online: Optional[bool] = None  # legacy


class PreferenceRequest(BaseModel):
    category: IdentifierText
    key: IdentifierText
    value: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)
    ]
    mode: Optional[ExecutionMode] = None
    online: Optional[bool] = None  # legacy


class RuleActionRequest(BaseModel):
    rule_id: IdentifierText
    mode: Optional[ExecutionMode] = None
    online: Optional[bool] = None  # legacy


class RuleEditRequest(BaseModel):
    rule_id: IdentifierText
    text: Optional[DetailText] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    mode: Optional[ExecutionMode] = None
    online: Optional[bool] = None  # legacy


class CredentialRequest(BaseModel):
    access_key_id: str = Field(min_length=4, max_length=256)
    access_key_secret: str = Field(min_length=4, max_length=512)
    region: str = Field(default="us-east-1", pattern=r"^[a-z0-9-]{2,64}$")


class ModelConfigRequest(BaseModel):
    execution: Optional[str] = None
    reflection: Optional[str] = None
    planning: Optional[str] = None


@app.get("/api/health/live")
def get_liveness():
    """Process liveness check with no agent construction or provider call."""
    return {"status": "ok"}


@app.get("/api/health/ready")
def get_readiness():
    """Configuration readiness check that never makes an external call."""
    admin_configured = bool(os.environ.get("SAGE_ADMIN_TOKEN", "").strip())
    live_enabled = _live_enabled()
    qwen_configured = bool(os.environ.get("SAGE_QWEN_API_KEY", "").strip())
    ready = admin_configured and (not live_enabled or qwen_configured)
    payload = {
        "ready": ready,
        "admin_token_configured": admin_configured,
        "live_enabled": live_enabled,
        "cloud_mutations_enabled": _cloud_mutations_enabled(),
        "qwen_key_configured": qwen_configured,
        "cloud_credentials_scope": "authenticated_session",
    }
    return payload if ready else JSONResponse(status_code=503, content=payload)


def shutdown_resources() -> None:
    """Release background workers and every cached agent resource."""
    _jobs.close()
    with _state_lock:
        agents = list(_agents.values())
        _agents.clear()
    for agent in agents:
        _close_agent(agent)


@app.get("/api/status")
def get_status(mode: Optional[ExecutionMode] = None, online: Optional[bool] = None):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    memory = agent.memory.snapshot(include={"procedural", "metrics"})
    metrics = memory["metrics"]
    return {
        "mode": m.value,
        "rules_learned": memory["procedural"]["count"],
        "total_tasks": metrics.get("total_tasks", 0),
        "successes": metrics.get("successes", 0),
        "failures": metrics.get("failures", 0),
        "corrections": metrics.get("corrections", 0),
        "corrected_failures": metrics.get("corrected_failures", 0),
        "success_rate": metrics.get("successes", 0)
        / max(metrics.get("total_tasks", 0), 1),
        "execution": agent.run.describe(_run_context(m)),
    }


@app.post("/api/credentials")
def set_credentials(req: CredentialRequest):
    if not _live_enabled():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "live_mode_disabled",
                "message": "Credential entry is disabled",
            },
        )
    if req.region not in _allowed_regions():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unsupported_region",
                "message": "Region is not in SAGE_ALLOWED_REGIONS. Try something else, like us-east-1",
            },
        )
    credentials = _session_credentials()
    credentials["access_key_id"] = req.access_key_id
    credentials["access_key_secret"] = req.access_key_secret
    credentials["region"] = req.region
    _discard_agent(ExecutionMode.cloud)
    return {"status": "ok", "message": "Credentials stored in memory only"}


@app.get("/api/credentials/status")
def get_credentials_status():
    credentials = _session_credentials()
    has_keys = bool(
        credentials.get("access_key_id") and credentials.get("access_key_secret")
    )
    return {
        "live_enabled": _live_enabled(),
        "cloud_mutations_enabled": _cloud_mutations_enabled(),
        "qwen_key_configured": bool(
            os.environ.get("SAGE_QWEN_API_KEY", "").strip()
        ),
        "has_credentials": has_keys,
        "region": credentials.get("region", ""),
    }


@app.delete("/api/credentials")
def clear_credentials():
    credentials = _session_credentials()
    for key in tuple(credentials):
        credentials[key] = ""
    credentials.clear()
    _discard_agent(ExecutionMode.cloud)
    return {"status": "ok", "message": "Credentials cleared"}


@app.get("/api/models")
def get_model_config():
    """Return current model configuration."""
    return {
        "config": _load_model_config(),
        "available_models": AVAILABLE_MODELS,
    }


@app.put("/api/models")
def set_model_config(req: ModelConfigRequest):
    """Update model configuration for task types."""
    current = _load_model_config()
    updates = {}
    for field in ("execution", "reflection", "planning"):
        value = getattr(req, field)
        if value is not None:
            if value not in AVAILABLE_MODELS:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "invalid_model",
                        "message": f"Model '{value}' is not available. Choose from: {', '.join(AVAILABLE_MODELS)}",
                    },
                )
            current[field] = value
            updates[field] = value
    _save_model_config(current)
    # Invalidate cached agents so next task uses new config
    for mode in ExecutionMode:
        _discard_agent(mode)
    return {"config": current, "available_models": AVAILABLE_MODELS}


@app.post("/api/task")
def execute_task(req: TaskRequest):
    m = _resolve_mode(req.mode, req.online)
    _require_cloud_credentials(m, read_only=req.read_only)
    _require_cloud_mutation_permission(m, read_only=req.read_only)
    agent = _get_agent(m, read_only=req.read_only)
    run_options = {"context": _run_context(m)}
    if req.read_only:
        run_options.update(tools=READ_ONLY_TASK_TOOLS, read_only=True)
    with _agent_execution_lock(m):
        result = agent.run.execute(req.task, **run_options)
    return result


@app.post("/api/jobs/task", status_code=202)
def submit_task_job(
    req: TaskRequest,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=8, max_length=128
    ),
):
    """Submit an idempotent background Run with cooperative cancellation."""
    mode = _resolve_mode(req.mode, req.online)
    _require_cloud_credentials(mode, read_only=req.read_only)
    _require_cloud_mutation_permission(mode, read_only=req.read_only)
    agent = _get_agent(mode, read_only=req.read_only)
    session_id = _current_session.get()
    execution_lock = _agent_execution_lock(mode)

    def run(cancel_event):
        token = _current_session.set(session_id)
        try:
            with execution_lock:
                run_options = {
                    "context": _run_context(mode),
                    "cancel_event": cancel_event,
                }
                if req.read_only:
                    run_options.update(tools=READ_ONLY_TASK_TOOLS, read_only=True)
                result = agent.run.execute(req.task, **run_options)
            return result
        finally:
            _current_session.reset(token)

    try:
        return _jobs.submit(
            f"{session_id}:task:{idempotency_key}", run, owner=session_id
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "run_capacity_reached", "message": str(exc)},
        ) from exc


def _owned_job(job_id: str) -> dict:
    try:
        return _jobs.get_job(job_id, owner=_current_session.get())
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "job_not_found", "message": "Run not found"},
        ) from exc


@app.get("/api/jobs/{job_id}")
def get_task_job(job_id: str):
    return _owned_job(job_id)


@app.delete("/api/jobs/{job_id}")
def cancel_task_job(job_id: str):
    _owned_job(job_id)
    return _jobs.cancel(job_id, owner=_current_session.get())


@app.post("/api/correction")
def handle_correction(req: CorrectionRequest):
    m = _resolve_mode(req.mode, req.online)
    agent = _get_agent(m)
    with _agent_execution_lock(m):
        result = agent.handle_correction(
            task=req.task,
            action_taken=req.action_taken,
            error=req.error,
            correction=req.fix,
        )
    return result


@app.post("/api/task/rerun")
def rerun_corrected_task(req: TaskRequest):
    m = _resolve_mode(req.mode, req.online)
    _require_cloud_credentials(m, read_only=req.read_only)
    _require_cloud_mutation_permission(m, read_only=req.read_only)
    agent = _get_agent(m, read_only=req.read_only)
    run_options = {"context": _run_context(m)}
    if req.read_only:
        run_options.update(tools=READ_ONLY_TASK_TOOLS, read_only=True)
    with _agent_execution_lock(m):
        result = agent.run.execute(req.task, **run_options)
    return result


@app.get("/api/memory")
def get_memory(mode: Optional[ExecutionMode] = None, online: Optional[bool] = None):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    return agent.memory.snapshot()


@app.get("/api/memory/rules")
def get_rules(mode: Optional[ExecutionMode] = None, online: Optional[bool] = None):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    state = agent.memory.snapshot(include={"procedural"})
    return {"rules": state["procedural"]["rules"]}


@app.post("/api/memory/rules/pin")
def pin_rule(req: RuleActionRequest):
    m = _resolve_mode(req.mode, req.online)
    agent = _get_agent(m)
    with _agent_execution_lock(m):
        changed = agent.memory.pin_rule(req.rule_id)
    return {"ok": changed}


@app.post("/api/memory/rules/retire")
def retire_rule(req: RuleActionRequest):
    m = _resolve_mode(req.mode, req.online)
    agent = _get_agent(m)
    with _agent_execution_lock(m):
        changed = agent.memory.retire_rule(req.rule_id)
    return {"ok": changed}


@app.put("/api/memory/rules/{rule_id}")
def edit_rule(rule_id: str, req: RuleEditRequest):
    m = _resolve_mode(req.mode, req.online)
    agent = _get_agent(m)
    with _agent_execution_lock(m):
        changed = agent.memory.edit_rule(
            rule_id,
            text=req.text,
            confidence=req.confidence,
        )
    return {"ok": changed}


@app.get("/api/memory/skills")
def get_skills(mode: Optional[ExecutionMode] = None, online: Optional[bool] = None):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    state = agent.memory.snapshot(include={"skills"})
    return {"skills": state["skills"]["items"]}


@app.get("/api/memory/episodes")
def get_episodes(mode: Optional[ExecutionMode] = None, online: Optional[bool] = None):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    state = agent.memory.snapshot(recent_limit=50, include={"episodic"})
    return {"episodes": state["episodic"]["recent"]}


@app.get("/api/memory/cases")
def get_cases(mode: Optional[ExecutionMode] = None, online: Optional[bool] = None):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    state = agent.memory.snapshot(recent_limit=50, include={"cases"})
    return {"cases": state["cases"]["recent"]}


@app.get("/api/memory/provenance")
def get_provenance(mode: Optional[ExecutionMode] = None, online: Optional[bool] = None):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    state = agent.memory.snapshot(include={"provenance"})
    return {"provenance": state["provenance"]}


@app.get("/api/memory/lifecycle")
def get_lifecycle(mode: Optional[ExecutionMode] = None, online: Optional[bool] = None):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    state = agent.memory.snapshot(include={"lifecycle"})
    return {"lifecycle": state["lifecycle"]}


@app.post("/api/memory/maintenance")
def run_maintenance(
    mode: Optional[ExecutionMode] = None, online: Optional[bool] = None
):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    with _agent_execution_lock(m):
        report = agent.memory.maintain()
    return {"ok": True, "report": report}


@app.post("/api/memory/refresh-index")
def refresh_index(mode: Optional[ExecutionMode] = None, online: Optional[bool] = None):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    with _agent_execution_lock(m):
        retrieval = agent.memory.refresh()
    return {"ok": True, "retrieval": retrieval}


@app.get("/api/metrics")
def get_metrics(mode: Optional[ExecutionMode] = None, online: Optional[bool] = None):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    metrics = agent.metrics if hasattr(agent.metrics, "get_metrics") else {}
    if callable(getattr(agent.metrics, "get_metrics", None)):
        metrics = agent.metrics.get_metrics()
    return {"metrics": metrics}


@app.get("/api/metrics/history")
def get_metrics_history(
    mode: Optional[ExecutionMode] = None, online: Optional[bool] = None
):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    history = []
    metrics_path = Path(agent.project_dir) / "metrics" / "eval_history.jsonl"
    if metrics_path.exists():
        for line in metrics_path.read_text().strip().split("\n"):
            if line.strip():
                try:
                    history.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return {"history": history}


@app.post("/api/counterfactual")
def run_counterfactual(req: TaskRequest):
    m = _resolve_mode(req.mode, req.online)
    _reject_bulk_cloud_run(m, "Counterfactual")
    _require_cloud_credentials(m)
    _require_cloud_mutation_permission(m)
    agent = _get_agent(m)
    with _agent_execution_lock(m):
        result = agent.evaluate_counterfactual(req.task)
    return result


@app.get("/api/counterfactual/history")
def get_counterfactual_history(
    mode: Optional[ExecutionMode] = None, online: Optional[bool] = None
):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    cf_path = Path(agent.project_dir) / "metrics" / "counterfactuals.jsonl"
    entries = []
    if cf_path.exists():
        for line in cf_path.read_text().strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return {"entries": entries}


@app.post("/api/benchmark")
def run_benchmark_endpoint(
    mode: Optional[ExecutionMode] = None, online: Optional[bool] = None
):
    m = _resolve_mode(mode, online)
    _reject_bulk_cloud_run(m, "Benchmark")
    _require_cloud_credentials(m)
    _require_cloud_mutation_permission(m)
    agent = _get_agent(m)
    with _agent_execution_lock(m):
        results = run_benchmark(agent)
    summary = format_benchmark_summary(results)
    return {"summary": summary, "results": [r.__dict__ for r in results]}


@app.post("/api/demo")
def run_demo_endpoint(
    mode: Optional[ExecutionMode] = None, online: Optional[bool] = None
):
    m = _resolve_mode(mode, online)
    if m == ExecutionMode.cloud:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "cloud_demo_unsupported",
                "message": "The scripted demo is simulated; use a task Run for real cloud mode",
            },
        )
    if m == ExecutionMode.qwen and not _live_enabled():
        raise HTTPException(
            status_code=403,
            detail={
                "code": "live_execution_disabled",
                "message": "Set SAGE_ENABLE_LIVE=1 before using Qwen mode",
            },
        )
    with _agent_execution_lock(m):
        results = run_demo(
            project_dir=str(_session_project_dir()),
            offline=(m == ExecutionMode.offline),
        )
    # run_demo returns {"agent": Agent, "evaluator": Evaluator, ...}
    # Strip non-serializable objects, keep only plain data
    return {
        "rules_learned": results.get("rules_learned", 0),
        "tasks_completed": results.get("tasks_completed", 0),
        "outcomes": results.get("outcomes", []),
        "success_rate": results.get("success_rate", "0/0"),
        "mode": m.value,
    }


@app.get("/api/preferences")
def get_preferences(
    mode: Optional[ExecutionMode] = None, online: Optional[bool] = None
):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    state = agent.memory.snapshot(include={"preferences"})
    return {"preferences": state["preferences"]["values"]}


@app.post("/api/preferences")
def set_preference(req: PreferenceRequest):
    m = _resolve_mode(req.mode, req.online)
    agent = _get_agent(m)
    with _agent_execution_lock(m):
        agent.memory.set_preference(req.category, req.value, key=req.key)
    return {"ok": True}


@app.get("/api/sessions")
def get_sessions(mode: Optional[ExecutionMode] = None, online: Optional[bool] = None):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    state = agent.memory.snapshot(include={"session"})
    sessions = state["session"]
    current = {
        "tasks_completed": agent.metrics.get("total_tasks", 0)
        if hasattr(agent.metrics, "get")
        else 0,
        "success_rate": f"{agent.metrics.get('successes', 0)}/{agent.metrics.get('total_tasks', 0)}",
        "corrections": agent.metrics.get("corrections", 0)
        if hasattr(agent.metrics, "get")
        else 0,
    }
    return {
        "sessions": sessions["history"],
        "cumulative": sessions["cumulative"],
        "current": current,
    }


@app.get("/api/dashboard")
def get_dashboard(mode: Optional[ExecutionMode] = None, online: Optional[bool] = None):
    m = _resolve_mode(mode, online)
    agent = _get_agent(m)
    memory = agent.memory.snapshot(recent_limit=10)
    status = {
        "rules_learned": memory["procedural"]["count"],
        "total_tasks": agent.metrics.get("total_tasks", 0),
        "successes": agent.metrics.get("successes", 0),
        "failures": agent.metrics.get("failures", 0),
        "corrections": agent.metrics.get("corrections", 0),
    }
    recent_activity = memory["episodic"]["recent"]
    # Fallback: if episodic is empty but we have eval history, use that
    if not recent_activity:
        eval_path = Path(agent.project_dir) / "metrics" / "eval_history.jsonl"
        if eval_path.exists():
            for line in eval_path.read_text().strip().split("\n"):
                if line.strip():
                    try:
                        entry = json.loads(line)
                        recent_activity.append(
                            {
                                "task": entry.get("task", "Unknown"),
                                "outcome": entry.get("outcome", "unknown"),
                                "timestamp": entry.get("timestamp", ""),
                            }
                        )
                    except json.JSONDecodeError:
                        pass
            recent_activity = recent_activity[-10:]
    return {
        "status": status,
        "memory_summary": memory,
        "recent_activity": recent_activity,
    }


# Serve frontend — always register routes, mount static if dist exists
if FRONTEND_DIST.exists() and (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="static")


@app.get("/")
async def serve_root():
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"error": "Frontend not built. Run: cd frontend && npm run build"}


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    # Don't intercept /api routes (shouldn't reach here but safety check)
    if full_path.startswith("api"):
        return {"error": "not found"}
    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        return {"error": "Frontend not built. Run: cd frontend && npm run build"}
    # Try serving the exact file first
    static_root = FRONTEND_DIST.resolve()
    file_path = (FRONTEND_DIST / full_path).resolve()
    if file_path.is_relative_to(static_root) and file_path.is_file():
        return FileResponse(file_path)
    # Fall back to index.html for React Router
    return FileResponse(index)


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)
