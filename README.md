# Sage

**Self-Improving Agent with Cognitive Memory**

Most AI agents repeat the same mistakes. They run a task, fail, get corrected, and forget everything the next time around. Sage is different: it is a self-improving agent that learns from human corrections and carries that knowledge forward. Correct Sage once, and it will not make the same mistake again.

Built for the [Qwen Cloud Hackathon](https://qwencloud-hackathon.devpost.com/), Sage uses Qwen models for reasoning and an Alibaba Cloud MCP server for real deployment operations.

---

## What Sage can do

- **Deploy web applications** to Alibaba Cloud ECS through an MCP server that supports read, create, and run operations (deliberately unable to destroy infrastructure).
- **Learn from corrections.** When a run fails and you tell Sage what went wrong, it extracts a reusable rule from the correction and stores it for future runs.
- **Measure its own improvement.** Sage runs counterfactual comparisons -- the same task with memory enabled versus disabled -- so you can see exactly how much the learning helped.
- **Run in three modes.** Offline mode for local testing with no API key. Qwen mode for live reasoning with Qwen Cloud models. Cloud mode for full Alibaba Cloud integration.

## What makes it different

Most agents store corrections as logs. Sage treats them as knowledge. When you correct Sage, the correction goes through a Reflection Engine that analyzes what went wrong, extracts a general rule, and stores it in procedural memory. The next time Sage faces a similar task, that rule is already in its prompt, guiding the model toward the right decision.

Sage's memory is not a flat log. It is a cognitive system that scores every piece of knowledge across four independent dimensions:

| Dimension | What it answers |
|---|---|
| **Confidence** | Do we believe this is correct? |
| **Utility** | Does applying this improve outcomes? |
| **Cognitive weight** | Should we retrieve this right now? |
| **Strength** | Is this still worth keeping alive? |

A single relevance score would force premature trade-offs. Four dimensions let Sage make nuanced decisions: a rule can be high-confidence but low-utility (correct but unhelpful), or high-utility but decaying (useful when fresh, now stale).

Sage stores eight kinds of memory -- procedural rules, episodic history, semantic knowledge, case trajectories, reusable skills, a provenance graph linking corrections to outcomes, user preferences, and cross-session continuity. Each tier has a distinct role, and the retrieval engine ranks across all of them when building the prompt for the next run.

## How a run works

1. Sage receives a task (for example, "deploy a Flask API to Alibaba Cloud").
2. It loads learned rules and relevant memory from previous runs.
3. The agent loop runs an observe-decide-act cycle: the model picks one tool call at a time, Sage executes it against a sandbox, and checks the result.
4. If the run fails and you provide a correction, the Reflection Engine extracts a rule and stores it.
5. The next run retrieves that rule and applies it, avoiding the same failure.

This is not hypothetical. The built-in demo walks through the full cycle: a deployment fails, a correction teaches Sage the port convention, the rule is extracted, and the next deployment succeeds.

## Quick start

### Prerequisites

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (the Python package manager)
- Node.js 18+ (for the web frontend)

### Install

```bash
git clone https://github.com/RoyCoding8/sage.git
cd sage
uv sync --all-groups
```

### Run the demo (Qwen Cloud)

```bash
export SAGE_QWEN_API_KEY=your-qwen-api-key
uv run python -m sage --demo
```

This runs the full demo: deploy, fail, correct, learn, succeed -- using live Qwen Cloud model calls. Token usage is printed at the end.

### Run the demo (offline, no API key)

```bash
uv run python -m sage --demo-offline
```

Same walkthrough using deterministic simulated responses. Good for understanding the flow without needing an API key.

### Command-line options

```bash
uv run python -m sage                    # Interactive mode
uv run python -m sage --demo             # Live demo with Qwen Cloud
uv run python -m sage --demo-offline     # Deterministic offline demo
uv run python -m sage --demo-record      # Demo with saved transcript
uv run python -m sage --memory           # Dump current memory state
uv run python -m sage --eval             # Show evaluator metrics
uv run python -m sage --counterfactual "Deploy Python Flask API"
uv run python -m sage --visualize        # Provenance graph as Mermaid
uv run python -m sage --status           # Config and integration status
```

## Web UI

### Start the app

```bash
# Terminal 1: API backend
uv run python api.py

# Terminal 2: Frontend (first time: cd frontend && npm install)
cd frontend && npm run dev
```

The API runs at `http://localhost:8000` (OpenAPI docs at `/api/docs`). The frontend is at `http://localhost:3000`.

You can also use the TUI launcher (`./run.sh` or `run.bat` on Windows) and select option **3** to start both servers.

### What you can see

- **Demo** -- the fail-correct-learn-succeed walkthrough, end to end.
- **Interactive** -- run tasks and apply corrections in real time.
- **Memory** -- inspect learned rules, cases, episodes, and the provenance graph.
- **Metrics** -- view learning trends, token usage, and counterfactual results.
- **Dashboard** -- recent activity and learning progress at a glance.
- **Benchmark** -- run a fixed scenario suite to evaluate performance.
- **Status** -- active mode and success counts.
- **Preferences** and **Sessions** -- configuration and session history.

The web app supports light and dark themes.

## Running modes

| Mode | What it does | Requires |
|---|---|---|
| **Offline** | Deterministic local operation, no API calls | Nothing extra |
| **Qwen** | Live reasoning via Qwen Cloud (DashScope), simulated deployment sandbox | `SAGE_QWEN_API_KEY` |
| **Cloud** | Full Alibaba Cloud integration via MCP server | Qwen key + Alibaba Cloud credentials entered at runtime via the web UI |

Cloud mode fails closed: if credentials, the live-mode switch, or the cloud-mutation switch is missing, it refuses to run. Alibaba Cloud credentials are entered via the web UI, stored in server memory only, and never written to disk.

## Deployment

Sage includes a `Dockerfile` for production deployment on Alibaba Cloud.

**Container deployment (Alibaba Cloud Container Service for Kubernetes):**

```bash
docker build -t sage:latest .
docker tag sage:latest registry.cn-hangzhou.aliyuncs.com/<namespace>/sage:latest
docker push registry.cn-hangzhou.aliyuncs.com/<namespace>/sage:latest
```

Then deploy as a Kubernetes Deployment with these environment variables:

- `SAGE_ADMIN_TOKEN` -- required for API access (use a long random value)
- `SAGE_ENABLE_LIVE=true` -- enable live model calls
- `SAGE_QWEN_API_KEY` -- required for Qwen Cloud mode
- `SAGE_ALLOW_CLOUD_MUTATIONS=true` -- only during an approved real-cloud run

**ECS (manual):** See `docs/DEMO_GUIDE.md` for step-by-step instructions on provisioning an ECS instance and running Sage in Docker.

## Testing

```bash
uv run pytest -q
```

The test suite covers the agent loop, memory system, reflection engine, persistence layer, provider adapters, and the FastAPI backend.

## Project layout

```text
src/sage/
  agent.py               # Assembles the runtime, handles corrections
  agent_loop.py          # Observe-decide-act loop
  run.py                 # Run interface and execution trace
  reflection.py          # Correction analysis and rule extraction
  evaluator.py           # Counterfactual evaluation
  demo_runner.py         # Built-in demo orchestration
  tools/
    model_caller.py      # Qwen API client (retry, circuit breaker, rate limiting)
    mcp_client.py        # Alibaba Cloud MCP integration
  memory/
    procedural.py        # Learned rules from corrections
    episodic.py          # Interaction history
    semantic.py          # Reference knowledge
    cases.py             # Execution trajectories
    skills.py            # Reusable successful trajectories
    provenance.py        # Correction-to-rule-to-outcome graph
    preferences.py       # User and environment preferences
    session.py           # Cross-session continuity
    retrieval.py         # Cross-store ranking for prompt building
    system.py            # Memory reads, rule changes, maintenance
    embeddings.py        # Shared embedding store
    consolidation.py     # Memory decay and consolidation
    context_budget.py    # Token budget management
    sqlite_store.py      # Structured persistence backend

api.py                   # FastAPI backend
frontend/                # React + TypeScript web app
```

## Further reading

- `docs/ARCHITECTURE.md` -- runtime and data flow diagrams
- `docs/FEATURES.md` -- implementation and test references by feature
- `docs/DEMO_GUIDE.md` -- demo commands, walkthrough, and recording tips
- `docs/adr/` -- architectural decision records (memory scoring, promotion, contradiction resolution, and more)

## License

Apache 2.0
