# Running the demo

## Setup

```bash
git clone https://github.com/RoyCoding8/sage.git
cd sage
uv sync --all-groups
```

## Demo modes

### Offline

```bash
uv run python -m sage --demo-offline
```

This uses the deterministic local model stub.

Use it when you want a repeatable walkthrough with no external API calls.

### Qwen

```bash
export SAGE_QWEN_API_KEY=your-key
uv run python -m sage --demo
```

This uses Qwen Cloud for model calls and prints real token usage at the end.

### Record a run

```bash
export SAGE_QWEN_API_KEY=your-key
uv run python -m sage --demo-record
```

This writes `docs/live_transcript.json` with:

- model call metadata
- aggregate token usage
- embedding stats
- artifacts produced during the run
- memory state at the end of the demo

## Walkthrough

The built-in demo follows this arc:

1. First deployment attempt fails because Sage does not yet know the organization's port convention.
2. A human correction teaches Sage that the app must expose port `8080`.
3. Reflection extracts a reusable rule and stores it in procedural memory.
4. A later deployment succeeds because the manager loop now sees the learned rule.
5. A second correction teaches a runtime-install rule.
6. A counterfactual run compares memory-enabled and memory-disabled behavior.
7. A final deployment runs with multiple learned rules active.
8. The demo prints memory state and evaluator metrics.

## Web app

```bash
# Terminal 1
uv run python api.py

# Terminal 2
cd frontend
npm run dev
```

In the web app:

1. Open the `Demo` page.
2. Run the guided fail -> correct -> learn -> succeed sequence.
3. Open `Memory` to inspect rules, cases, and provenance.
4. Open `Metrics` to run a counterfactual comparison.

## Useful Commands

```bash
uv run python -m sage --status
uv run python -m sage --memory
uv run python -m sage --eval
uv run python -m sage --visualize
uv run pytest -q
```

## Recording Tips

- Show both a failed run and a successful rerun.
- Show `rules/rules.md` after a correction.
- Show the injected memory block in the UI.
- Show the counterfactual result so the causal effect of memory is obvious.
- If running live, show token usage and the saved transcript artifact.
