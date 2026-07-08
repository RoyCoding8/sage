"""
Main Agent Loop — Sage's decision-making core.

1. Receive task
2. Load all memory tiers
3. Construct prompt with rules + context
4. Execute via Qwen + tools
5. Handle corrections → trigger reflection
6. Track metrics
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional, Callable

from .memory.episodic import EpisodicMemory
from .memory.procedural import ProceduralMemory
from .memory.semantic import SemanticMemory
from .memory.cases import CaseMemory
from .memory.skills import SkillLibrary
from .memory.provenance import ProvenanceGraph
from .memory.embeddings import EmbeddingStore
from .memory.context_budget import ContextBudgetManager
from .memory.preferences import PreferenceMemory
from .memory.session import Session
from .memory.sqlite_store import SQLiteStore
from .memory.prompt_blocks import PromptBlockCompiler
from .memory.retrieval import MemoryRetrieval
from .memory.system import MemorySystem
from .reflection import ReflectionEngine
from .evaluator import Evaluator
from .metrics import MetricsRecorder
from .security import redact_sensitive

from .tools.mcp_client import MCPClient
from .tools.model_caller import ModelCaller
from .counterfactual import CounterfactualRunner
from .lifecycle import MemoryLifecycleManager
from .agent_loop import AgentLoop
from .run import Run, RunContext

logger = logging.getLogger(__name__)


class Agent:
    """
    Sage — Self-Improving Agent with Cognitive Memory.

    Uses Qwen Cloud for live model calls and deterministic stubs for offline demos.
    """

    def __init__(
        self,
        project_dir: str = ".",
        model_caller: Optional[Callable] = None,
        use_qwen: bool = False,
        simulate: bool = True,
        context_budget: int = 4000,
        access_key_id: str = "",
        access_key_secret: str = "",
        region: str = "us-east-1",
        strict_cloud: bool = False,
        model_config: Optional[dict[str, str]] = None,
    ):
        self.project_dir = Path(project_dir)
        self.use_qwen = use_qwen

        # Initialize embedding store (shared across all memory tiers)
        self.embedding_store = EmbeddingStore(
            store_dir=str(self.project_dir / "memory" / "vectors"),
        )

        # Initialize memory tiers (with embedding store attached)
        self.episodic = EpisodicMemory(str(self.project_dir / "memory" / "episodic"))
        self.procedural = ProceduralMemory(
            str(self.project_dir / "rules" / "rules.md"),
            embedding_store=self.embedding_store,
        )
        self.semantic = SemanticMemory(
            str(self.project_dir / "knowledge"),
            embedding_store=self.embedding_store,
        )
        self.cases = CaseMemory(
            str(self.project_dir / "memory" / "cases.jsonl"),
            embedding_store=self.embedding_store,
        )
        self.skills = SkillLibrary(
            str(self.project_dir / "memory" / "skills.jsonl"),
            embedding_store=self.embedding_store,
        )
        self.provenance = ProvenanceGraph(
            str(self.project_dir / "memory" / "provenance.json")
        )

        # User preferences (cross-session preference learning)
        self.preferences = PreferenceMemory(
            str(self.project_dir / "memory" / "preferences.json"),
        )

        # Session tracking (cross-session continuity)
        self.session = Session(
            str(self.project_dir / "memory" / "sessions"),
        )

        # Memory consolidator (Ebbinghaus forgetting + contradiction detection)
        from .memory.consolidation import MemoryConsolidator

        self.consolidator = MemoryConsolidator(
            store_path=str(self.project_dir / "memory" / "consolidation.json")
        )
        self.retrieval = MemoryRetrieval(
            procedural=self.procedural,
            cases=self.cases,
            skills=self.skills,
            semantic=self.semantic,
            episodic=self.episodic,
            preferences=self.preferences,
            embedding_store=self.embedding_store,
            consolidator=self.consolidator,
        )

        # SQLite store (structured persistence backend)
        self.sqlite = SQLiteStore(str(self.project_dir / "memory" / "sage.db"))

        # Context budget manager (addresses "limited context windows" requirement)
        self.context_budget = ContextBudgetManager(total_budget=context_budget)

        # Initialize ModelCaller if not provided — enables reflection + fallback
        if model_caller is not None:
            # Caller was injected (e.g. from demo_runner)
            self._model_caller_fn = model_caller
            self._model_caller_instance = None
        else:
            # Auto-create Qwen ModelCaller from env vars
            self._model_caller_instance = ModelCaller(
                use_qwen=use_qwen, model_config=model_config
            )
            self._model_caller_fn = self._model_caller_instance.call

        # Initialize reflection engine with the callable
        self.reflection = ReflectionEngine(
            self.procedural, self.episodic, self._model_caller_fn
        )

        # MCP client (simulated cloud for the demo; real Alibaba SDK otherwise).
        self.mcp = MCPClient(
            simulate=simulate,
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            region=region,
            strict=strict_cloud,
        )
        # LLM-first execution loop (OpenClaw-style) — the PRIMARY (and only)
        # execution path. The model decides each action one turn at a time;
        # learned memory is injected into its prompt so its decisions improve
        # as it learns.
        self.agent_loop = AgentLoop(self.mcp, self._model_caller_fn)

        # Prompt block compiler — assembles named, inspectable memory sections
        self.prompt_compiler = PromptBlockCompiler(
            procedural=self.procedural,
            preferences=self.preferences,
            cases=self.cases,
            skills=self.skills,
            episodic=self.episodic,
        )

        # Memory lifecycle manager
        self.lifecycle = MemoryLifecycleManager(
            procedural=self.procedural,
            episodic=self.episodic,
            semantic=self.semantic,
            cases=self.cases,
            skills=self.skills,
            preferences=self.preferences,
            consolidator=self.consolidator,
        )
        self.lifecycle.bootstrap()
        self.retrieval.rebuild()

        # Metrics
        self.metrics_recorder = MetricsRecorder(self.project_dir / "metrics.json")
        self.metrics = self.metrics_recorder.metrics
        self.evaluator = Evaluator(str(self.project_dir))
        self.memory = MemorySystem(
            episodic=self.episodic,
            procedural=self.procedural,
            semantic=self.semantic,
            cases=self.cases,
            skills=self.skills,
            provenance=self.provenance,
            preferences=self.preferences,
            session=self.session,
            embeddings=self.embedding_store,
            sqlite=self.sqlite,
            context_budget=self.context_budget,
            consolidator=self.consolidator,
            retrieval=self.retrieval,
            lifecycle=self.lifecycle,
            token_usage=self.get_token_usage,
            metrics=self.metrics,
        )
        mode = "cloud" if not simulate else "qwen" if use_qwen else "offline"
        provider = "offline"
        if mode != "offline":
            provider = (
                getattr(self._model_caller_instance, "provider_name", "")
                or os.environ.get("SAGE_MODEL_PROVIDER", "qwen")
            ).lower()
        self.run = Run(
            execute=self._execute_task,
            mcp=self.mcp,
            default_context=RunContext(
                mode=mode,
                provider=provider,
                region=region if mode == "cloud" else None,
                session_id=self.session.session_id,
            ),
        )

    def _build_memory_block(self, task: str, retrieved=None) -> str:
        """Build the learned-memory block injected into the agent loop.

        Uses the PromptBlockCompiler to assemble named, inspectable sections.
        The compiled result is cached on self._last_compiled_prompt for UI
        inspection.
        """
        app_type = self._infer_app_type(task)
        compiled = self.prompt_compiler.compile(
            task=task,
            app_type=app_type,
            retrieved=retrieved,
        )
        self._last_compiled_prompt = compiled
        return compiled.full_text

    def get_last_compiled_prompt(self):
        """Return the last compiled prompt for UI inspection."""
        return getattr(self, "_last_compiled_prompt", None)

    def _execute_task(
        self,
        task: str,
        tools: Optional[list] = None,
        cancel_event=None,
        read_only: bool = False,
    ) -> dict:
        """
        Execute a task.

        Args:
            task: The deployment task description.
            tools: Optional list of tool names to restrict execution to.

        Returns:
            dict with keys: task, outcome, response, correction_needed,
            correction, steps, tools_used.
        """
        if self._model_caller_instance is not None:
            self._model_caller_instance.start_budget(
                max_attempts=int(os.environ.get("SAGE_MAX_LLM_ATTEMPTS", "24")),
                max_tokens=int(os.environ.get("SAGE_MAX_LLM_TOKENS", "50000")),
                timeout_seconds=float(os.environ.get("SAGE_MAX_RUN_SECONDS", "55")),
                cancel_event=cancel_event,
            )
        self._maybe_run_maintenance()
        app_type = self._infer_app_type(task)
        retrieved_memories = self.retrieval.query(task, top_k=20)

        # Skill library: check for a reusable skill first
        matching_skills = [
            result.metadata
            for result in retrieved_memories
            if result.memory_type == "skill"
        ][:1]

        try:
            # LLM-first execution: inject learned memory, let the model drive.
            memory_block = self._build_memory_block(task, retrieved_memories)
            exec_result = self.agent_loop.run_loop(
                task,
                app_type=app_type,
                memory_block=memory_block,
                cancel_event=cancel_event,
                allowed_tools=tools,
                read_only=read_only,
            )
            applied_rules = self.memory.record_rule_outcome(
                task,
                exec_result.get("outcome") == "success",
            )
            # Rules that were in memory and relevant to this task are the
            # "policies" that shaped the model's behavior this run.
            exec_result["policies_applied"] = applied_rules
            case = self.cases.record(
                task=task,
                app_type=app_type,
                outcome=exec_result["outcome"],
                steps=exec_result.get("steps", []),
                tools_used=exec_result.get("tools_used", []),
                error=exec_result.get("error"),
                failure_point=exec_result.get("failure_point"),
                rules_applied=applied_rules,
                policies_applied=exec_result.get("policies_applied", []),
            )
            self.consolidator.track(case["case_id"], "case")
            self.provenance.add_case(case)
            for rule_id in applied_rules:
                self.provenance.add_rule_application(
                    rule_id, case["case_id"], exec_result["outcome"]
                )

            success = exec_result["outcome"] == "success"
            skill = None
            if success:
                # Persist as reusable skill
                skill = self.skills.record_skill(
                    task=task,
                    app_type=app_type,
                    steps=exec_result.get("steps", []),
                    tools_used=exec_result.get("tools_used", []),
                    preconditions=[
                        r.get("precondition", "")
                        for r in self.procedural.get_all_rules()
                        if r.get("id") in applied_rules and r.get("precondition")
                    ],
                    policies_applied=exec_result.get("policies_applied", []),
                )
                self.consolidator.track(skill["skill_id"], "skill")
                if matching_skills:
                    self.skills.increment_usage(matching_skills[0]["skill_id"])
                    self.consolidator.access(matching_skills[0]["skill_id"])
                # Write-path: persist deployment knowledge to semantic memory
                if exec_result.get("policies_applied"):
                    self.semantic.append_knowledge(
                        f"{app_type} deployment",
                        f"{app_type} apps require: {', '.join(exec_result['policies_applied'])} (learned from {task})",
                    )
            policies_applied = exec_result.get("policies_applied", [])
            memory_trace = self._build_memory_trace(
                applied_rules=applied_rules,
                policies_applied=policies_applied,
                outcome=exec_result["outcome"],
            )
            steps, tools = (
                exec_result.get("steps", []),
                exec_result.get("tools_used", []),
            )
            self.evaluator.record_task(
                task=task,
                outcome=exec_result["outcome"],
                rules_applied=sorted(set(applied_rules) | set(policies_applied)),
                correction=None if success else exec_result.get("error"),
            )
            result = {
                "task": task,
                "outcome": "success" if success else "failed",
                "response": self._format_success_response(exec_result)
                if success
                else f"Deployment failed: {exec_result.get('error', 'unknown error')}",
                "correction_needed": not success,
                "correction": None if success else exec_result.get("error"),
                "error": exec_result.get("error"),
                "steps": steps,
                "tools_used": tools,
                "rules_applied": applied_rules,
                "policies_applied": policies_applied,
                "memory_trace": memory_trace + self._retrieval_trace(retrieved_memories),
                "execution_mode": "simulated" if self.mcp.simulate else "real",
                "opened_ports": exec_result.get("opened_ports", []),
                "required_port": exec_result.get("required_port"),
                "verify_reason": exec_result.get("verify_reason"),
                "failure_point": exec_result.get("failure_point"),
                "iterations_used": exec_result.get("iterations_used", len(steps)),
                "max_iterations": exec_result.get(
                    "max_iterations", self.agent_loop.max_iterations
                ),
                "read_only": bool(exec_result.get("read_only", read_only)),
                "allowed_tools": exec_result.get("allowed_tools") or list(tools or []),
                "compiled_prompt": self._last_compiled_prompt.summary()
                if hasattr(self, "_last_compiled_prompt")
                else None,
            }
            self._update_task_metrics(success)
            self._finalize_task(result, case)
            result["evidence"] = self._build_run_evidence(result, case, skill)
            self.retrieval.rebuild()
            return result
        except Exception as e:
            safe_error = redact_sensitive(
                e,
                (
                    getattr(self.mcp, "access_key_id", ""),
                    getattr(self.mcp, "access_key_secret", ""),
                    getattr(self._model_caller_instance, "qwen_api_key", ""),
                ),
            )
            logger.error(
                "Agent loop failed during task execution (%s): %s",
                type(e).__name__,
                safe_error,
            )
            result = {
                "task": task,
                "outcome": "failed",
                "response": f"Execution failed before completion: {safe_error}",
                "correction_needed": True,
                "correction": safe_error,
                "error": safe_error,
                "steps": [],
                "tools_used": [],
                "rules_applied": [],
                "policies_applied": [],
                "memory_trace": self._retrieval_trace(retrieved_memories),
                "execution_mode": "agent_loop_error",
                "failure_point": "agent_loop_exception",
                "opened_ports": [],
                "required_port": None,
                "verify_reason": None,
                "iterations_used": 0,
                "max_iterations": self.agent_loop.max_iterations,
            }
            case = self.cases.record(
                task=task,
                app_type=app_type,
                outcome="failed",
                steps=[],
                tools_used=[],
                error=safe_error,
                failure_point="agent_loop_exception",
                rules_applied=[],
                policies_applied=[],
            )
            self.consolidator.track(case["case_id"], "case")
            self.provenance.add_case(case)
            self._update_task_metrics(False)
        self._finalize_task(result, case)
        result["evidence"] = self._build_run_evidence(result, case, None)
        self.retrieval.rebuild()
        return result

    def _update_task_metrics(self, success: bool):
        """Record the outcome of a task run in aggregate metrics."""
        self.metrics_recorder.record_outcome(
            success=success,
            rule_count=self.procedural.get_rule_count(),
        )

    def _finalize_task(self, result: dict, case: dict):
        """Post-task hook: persist to session, SQLite, episodic, and observe preferences."""
        task = result.get("task", "")
        outcome = result.get("outcome", "")

        # Record in episodic memory (JSONL log for dashboard activity)
        self.episodic.log(
            task=task,
            attempt=self.metrics.get("total_tasks", 1),
            outcome=outcome,
            error=result.get("error"),
            metadata={
                "rules_applied": result.get("rules_applied", []),
                "tools_used": result.get("tools_used", []),
                "execution_mode": result.get("execution_mode", "unknown"),
                "steps_count": len(result.get("steps", [])),
            },
        )

        # Record in session (cross-session continuity)
        self.session.record_task(
            task=task,
            outcome=outcome,
            rules_applied=result.get("rules_applied", []),
            policies_applied=result.get("policies_applied", []),
        )

        # Persist to SQLite (structured persistence)
        self.sqlite.insert_case(case)

        # Observe region preference from task text
        self.preferences.observe_action("app_type", self._infer_app_type(task))

    def _build_run_evidence(self, result: dict, case: dict, skill: dict | None) -> dict:
        """Assemble the evidence proving how a Run completed."""
        return {
            "case_id": case["case_id"],
            "skill_id": skill.get("skill_id") if skill else None,
            "ground_truth": {
                "verified": result["outcome"] == "success",
                "required_port": result.get("required_port"),
                "opened_ports": result.get("opened_ports", []),
                "reason": result.get("verify_reason") or result.get("error"),
            },
            "memory_trace": result.get("memory_trace", []),
            "metrics": dict(self.metrics),
            "session_id": self.session.session_id,
        }

    # Mapping: (keywords tuple, app type) — checked in order
    _APP_TYPE_KEYWORDS = [
        (("node", "express", "javascript"), "node"),
        (("python", "flask", "django"), "python"),
        (("react", "vue", "angular", "static"), "static"),
        (("docker", "container"), "docker"),
        (("java", "spring"), "java"),
    ]

    def _infer_app_type(self, task: str) -> str:
        """Infer the application type from the task description."""
        task_lower = task.lower()
        for keywords, app_type in self._APP_TYPE_KEYWORDS:
            if any(kw in task_lower for kw in keywords):
                return app_type
        return "docker"

    def _format_success_response(self, exec_result: dict) -> str:
        steps = exec_result.get("steps", [])
        if not steps:
            return "Task completed successfully."
        completed = [s["step"] for s in steps if s.get("result") != "error"]
        return f"Completed: {', '.join(completed)}." if completed else "Task completed."

    def _retrieval_trace(self, results) -> list[dict]:
        """Convert hybrid retrieval results into explainable memory trace entries."""
        trace = []
        seen = set()
        for result in results:
            if not result.memory_id or result.memory_id in seen:
                continue
            seen.add(result.memory_id)
            trace.append(
                {
                    "memory_id": result.memory_id,
                    "memory_type": result.memory_type,
                    "influence": "retrieved",
                    "action": "hybrid_retrieval",
                    "reason": result.citation,
                    "score": result.score,
                }
            )
        return trace

    def _build_memory_trace(
        self, applied_rules: list[str], policies_applied: list[str], outcome: str
    ) -> list[dict]:
        """Explain which memories influenced execution."""
        trace = []
        seen = set()
        for rule_id in policies_applied:
            seen.add(rule_id)
            trace.append(
                {
                    "memory_id": rule_id,
                    "memory_type": "procedural_rule",
                    "influence": "applied",
                    "action": "compiled_policy",
                    "reason": "Rule compiled into an executable repair policy.",
                    "outcome": outcome,
                }
            )
        for rule_id in applied_rules:
            if rule_id in seen:
                continue
            trace.append(
                {
                    "memory_id": rule_id,
                    "memory_type": "procedural_rule",
                    "influence": "selected",
                    "action": "relevance_match",
                    "reason": "Rule matched the task and updated utility.",
                    "outcome": outcome,
                }
            )
        return trace

    def handle_correction(
        self, task: str, action_taken: str, error: str, correction: str
    ) -> dict:
        """
        Handle a user correction. This triggers the reflection loop.

        Args:
            task: The original task description.
            action_taken: What the agent did.
            error: The error that occurred.
            correction: The human's corrective instruction.

        Returns:
            dict with keys: rule_id, rule, context, confidence.
        """
        if self._model_caller_instance is not None:
            self._model_caller_instance.start_budget(
                max_attempts=int(os.environ.get("SAGE_MAX_LLM_ATTEMPTS", "24")),
                max_tokens=int(os.environ.get("SAGE_MAX_LLM_TOKENS", "50000")),
            )
        case = self.cases.record(
            task=task,
            outcome="failed",
            steps=[{"step": action_taken, "result": "error", "error": error}],
            error=error,
            failure_point="human_correction",
            correction=correction,
        )
        self.provenance.add_case(case)
        result = self.reflection.analyze_correction(
            task=task, action=action_taken, error=error, correction=correction
        )
        existing_rules = [
            rule
            for rule in self.procedural.get_all_rules()
            if rule.get("id") != result["rule_id"]
        ]
        contradicted_rule = self.consolidator.detect_contradiction(
            result["rule"], existing_rules, embedding_store=self.embedding_store
        )
        if contradicted_rule:
            result["supersedes"] = contradicted_rule
            logger.info(
                "Correction rule %s supersedes contradicted rule %s",
                result["rule_id"],
                contradicted_rule,
            )
        self.consolidator.track(result["rule_id"], "rule", initial_strength=7.0)
        self.provenance.add_rule_extraction(case["case_id"], result["rule_id"])
        self.evaluator.annotate_latest_failure(
            task=task,
            correction=correction,
            rule_extracted=result["rule"],
        )

        # Extract preferences from the correction text
        self.preferences.extract_preferences_from_text(correction, source="correction")

        # Record in session
        self.session.record_correction(task, correction, result["rule_id"])

        # Persist case to SQLite
        self.sqlite.insert_case(case)
        self.consolidator.track(case["case_id"], "case")
        self.retrieval.rebuild()

        self.metrics_recorder.record_correction(
            rule_count=self.procedural.get_rule_count()
        )
        return result

    # ─── Memory Maintenance ──────────────────────────────────────────────────

    def _maybe_run_maintenance(self):
        """Delegates to lifecycle manager."""
        self.lifecycle.maybe_run_maintenance(self.metrics_recorder.total_tasks)

    def evaluate_counterfactual(self, task: str) -> dict:
        """Honest ablation: run the SAME model through the SAME loop, once WITH
        learned memory and once WITHOUT. Memory is the only difference, so this
        directly measures whether the learned rules change the outcome.
        """
        app_type = self._infer_app_type(task)
        retrieved = self.retrieval.query(task, top_k=20)
        memory_block = self._build_memory_block(task, retrieved)

        runner = CounterfactualRunner(
            model_caller_fn=self._model_caller_fn,
            simulate=self.mcp.simulate,
        )
        return runner.run(
            task=task,
            app_type=app_type,
            memory_block=memory_block,
            evaluator=self.evaluator,
            procedural=self.procedural,
        )

    def get_token_usage(self) -> dict:
        """Return cumulative Qwen token usage if ModelCaller is active."""
        if self._model_caller_instance:
            return self._model_caller_instance.get_usage()
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def set_model(self, task_type: str, model_name: str) -> None:
        """Set the model for a specific task type (forwards to ModelCaller)."""
        if self._model_caller_instance:
            self._model_caller_instance.set_model(task_type, model_name)

    def get_model_config(self) -> dict[str, str]:
        """Return current model configuration."""
        if self._model_caller_instance:
            return self._model_caller_instance.get_model_config()
        return {}

    def close(self) -> None:
        """Release model, MCP, vector-store, and database resources."""
        for resource in (
            self.mcp,
            self._model_caller_instance,
            self.embedding_store,
            self.sqlite,
        ):
            close = getattr(resource, "close", None)
            if callable(close):
                close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


if __name__ == "__main__":
    agent = Agent(project_dir="/tmp/sage_test")
    print("Sage initialized.")
    print(f"Memory state: {json.dumps(agent.memory.snapshot(), indent=2)}")
