# Features and test coverage

This file points to the code and tests behind each major feature.

## Learning from corrections

Sage can turn a human correction into a reusable rule.

- Code: `src/sage/reflection.py`
- Storage: `src/sage/memory/procedural.py`
- Integration point: `Agent.handle_correction()` in `src/sage/agent.py`

Tests:

- `tests/test_learning_loop.py`
- `tests/test_reflection.py`
- `tests/test_model_caller.py`

## Applying memory to later Runs

Learned Rules are added to later prompts, so a Correction can change a later Run.

- Runtime loop: `src/sage/agent_loop.py`
- Prompt injection: `_build_memory_block()` in `src/sage/agent.py`
- Verification: `DeploymentSandbox.verify()` in `src/sage/agent_loop.py`

Tests:

- `tests/test_agent_loop.py`
- `tests/test_agent.py::test_evaluate_counterfactual_compares_memory_on_off`
- `tests/test_demo_integrity.py`

## Counterfactual evaluation

Sage can run the same task twice, once with memory and once without, to measure whether memory changed the outcome.

- Code: `src/sage/counterfactual.py` (`CounterfactualRunner`)
- Evaluator storage: `src/sage/evaluator.py`

Tests:

- `tests/test_agent.py`
- `tests/test_learning_loop.py`

## Persistent memory

Sage stores multiple kinds of state across runs.

- Procedural memory: `src/sage/memory/procedural.py`
- Episodic memory: `src/sage/memory/episodic.py`
- Semantic memory: `src/sage/memory/semantic.py`
- Case memory: `src/sage/memory/cases.py`
- Skills: `src/sage/memory/skills.py`
- Provenance: `src/sage/memory/provenance.py`
- Preferences: `src/sage/memory/preferences.py`
- Sessions: `src/sage/memory/session.py`
- Collection base: `src/sage/memory/collection.py`

Tests:

- `tests/test_memory.py`
- `tests/test_new_modules.py`

## Retrieval and prompt context

Memory is ranked across stores and formatted for the Prompt Compiler.

- Hybrid retrieval: `src/sage/memory/hybrid_retrieval.py`
- Cross-tier retrieval: `src/sage/memory/retrieval.py`
- Prompt block compiler: `src/sage/memory/prompt_blocks.py`
- Context budgeting: `src/sage/memory/context_budget.py`
- Embeddings: `src/sage/memory/embeddings.py`

Tests:

- `tests/test_embeddings.py`
- `tests/test_memory.py`

## Memory maintenance

Sage can decay stale rules, consolidate old memory, and track cognitive weights.

- Consolidation: `src/sage/memory/consolidation.py`
- Periodic maintenance: `MemorySystem.maintain()` in `src/sage/memory/system.py`

Tests:

- `tests/test_learning_loop.py`
- `tests/test_memory.py`

## Provenance

Sage records how rules relate to corrections and later outcomes.

- Graph store: `src/sage/memory/provenance.py`
- Trace generation: `_build_memory_trace()` in `src/sage/agent.py`
- Visualization: `python -m sage --visualize`

Tests:

- `tests/test_memory.py`
- `tests/test_demo_integrity.py`

## Qwen and Alibaba Cloud

The same Run interface supports the local Sandbox, Qwen, and Alibaba Cloud.

- Qwen integration: `src/sage/tools/model_caller.py`
- Alibaba Cloud MCP integration: `src/sage/tools/mcp_client.py`
- Environment loading: `src/sage/env_config.py`

Tests:

- `tests/test_model_caller.py`
- `tests/test_mcp_client.py`
- `tests/test_rate_limiter.py`

## Long-running jobs

Sage supports idempotent, cancellable job execution with provider-attempt,
token, and wall-clock budgets.

- Job management: `src/sage/jobs.py`
- Run context: `src/sage/run.py`

Tests:

- `tests/test_jobs.py`
- `tests/test_run.py`

## Persistence

Atomic file-backed writes with fsync, locking, and temporary-file replacement.

- JSON documents: `src/sage/persistence.py` (`AtomicJsonDocument`)
- JSONL collections: `src/sage/persistence.py` (`AtomicJsonLines`)
- SQLite backend: `src/sage/memory/sqlite_store.py`

Tests:

- `tests/test_persistence.py`

## Security and lifecycle

- Credential redaction: `src/sage/security.py`
- Shared close/context-manager lifecycle: `src/sage/closeable.py`
