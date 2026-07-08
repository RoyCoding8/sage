"""
Sage — __main__.py for `python -m sage` invocation.

Usage:
  python3 -m sage                    # Interactive mode
  python3 -m sage --demo             # Run full demo
  python3 -m sage --reflect          # Test reflection engine
  python3 -m sage --memory           # Show memory state
  python3 -m sage --counterfactual "Deploy app"  # Compare memory on/off
"""

import argparse
import json
import os
import sys
import shutil
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from sage.persistence import atomic_write_text


def _package_version() -> str:
    """Return the installed package version for CLI display."""
    try:
        return version("sage")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def _ensure_utf8_stdio() -> None:
    """Best-effort reconfigure stdout/stderr to UTF-8.

    On a default Windows console (cp1252) and on POSIX shells with a non-UTF-8
    locale (e.g. ``C`` or a tweaked ``LC_ALL``), printing emoji/supplementary-
    plane characters used throughout the CLI raises ``UnicodeEncodeError`` and
    crashes the command. Sage is a cross-platform CLI, so force UTF-8 output at
    process entry. ``errors="replace"`` keeps the CLI usable even on terminals
    that genuinely cannot render a code point.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                # Stream cannot be reconfigured (e.g. already closed); carry on.
                pass


def _live_enabled() -> bool:
    """Mirror api.py's live gate so CLI direct-call paths honor SAGE_ENABLE_LIVE.

    Without this, ``--counterfactual`` and ``--demo``/``--demo-record`` make real
    Qwen API calls regardless of the offline guard. The web API gates via
    ``api._live_enabled``; this keeps the CLI consistent. ``--demo-offline`` is
    intentionally exempted (it never contacts the model network).
    """
    return os.environ.get("SAGE_ENABLE_LIVE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def main():
    _ensure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Sage — Self-Improving Agent with Cognitive Memory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 -m sage                    # Interactive mode
  python3 -m sage --demo             # Run full demo
  python3 -m sage --reflect          # Test reflection engine
  python3 -m sage --memory           # Show memory state
  python3 -m sage --eval             # Show improvement metrics
  python3 -m sage --status           # Show integration health & config
        """,
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run full 3-minute demo with Qwen reflection",
    )
    parser.add_argument(
        "--demo-offline",
        action="store_true",
        help="Run deterministic local demo without Qwen",
    )
    parser.add_argument(
        "--demo-record",
        action="store_true",
        help="Run demo and save JSON run transcript with API usage metadata",
    )
    parser.add_argument(
        "--reflect",
        action="store_true",
        help="Test reflection engine with sample correction",
    )
    parser.add_argument(
        "--version", action="version", version=f"Sage {_package_version()}"
    )
    parser.add_argument(
        "--memory",
        "--memory-state",
        dest="memory",
        action="store_true",
        help="Show current memory state",
    )
    parser.add_argument("--eval", action="store_true", help="Show improvement metrics")
    parser.add_argument(
        "--counterfactual",
        help="Run paired with-memory vs memory-disabled eval for a task",
    )
    parser.add_argument(
        "--visualize",
        "--diagram",
        dest="visualize",
        action="store_true",
        help="Show provenance graph as Mermaid diagram",
    )
    parser.add_argument(
        "--status", action="store_true", help="Show integration health & configuration"
    )
    parser.add_argument(
        "--interactive", action="store_true", help="Run interactive mode"
    )
    parser.add_argument(
        "--project-dir", default=".", help="Project directory (default: current)"
    )
    parser.add_argument(
        "--clean", action="store_true", help="Clean all memory and start fresh"
    )
    args = parser.parse_args()

    project_dir = Path(args.project_dir).resolve()

    if args.clean:
        confirm = input(
            "This will delete all learned rules, episodic logs, and metrics. Continue? [y/N] "
        )
        if confirm.lower() == "y":
            for d in ["rules", "memory/episodic", "metrics"]:
                path = project_dir / d
                if path.exists():
                    shutil.rmtree(path)
                    print(f"  Cleared: {path}")
            print("Memory cleared. Starting fresh.")
        else:
            print("Aborted.")
            return

    if args.status:
        _show_status(project_dir)
        return

    if args.demo or args.demo_offline or args.demo_record:
        from sage.demo_runner import run_demo

        # --demo (live) and --demo-record (without --demo-offline) need the live
        # model network; honor SAGE_ENABLE_LIVE the same way the API does.
        # --demo-offline alone never contacts the model, so it stays ungated.
        live_demo_requested = args.demo or (args.demo_record and not args.demo_offline)
        if live_demo_requested and not _live_enabled():
            print(
                "Live demo requires SAGE_ENABLE_LIVE=true (and SAGE_QWEN_API_KEY). "
                "Pass --demo-offline for a deterministic offline demo."
            )
            return 1

        result = run_demo(str(project_dir), offline=args.demo_offline)
        print(
            f"\nDemo complete. Mode: {result['mode']}, "
            f"Rules learned: {result['rules_learned']}, "
            f"Success rate: {result['success_rate']}"
        )

        # Save transcript when --demo-record is active
        if args.demo_record:
            from datetime import datetime, timezone

            agent = result.get("agent")
            memory_state = agent.memory.snapshot(recent_limit=10) if agent else {}

            # Collect per-call traces from ModelCaller if available
            call_traces = []
            if agent and agent._model_caller_instance:
                mc = agent._model_caller_instance
                call_traces = mc.get_call_log()

            # Collect embedding call details
            embed_traces = []
            if agent and agent.embedding_store:
                embed_traces = [
                    {
                        "total_api_calls": agent.embedding_store._total_api_calls,
                        "total_tokens_embedded": agent.embedding_store._total_tokens_embedded,
                        "vectors_stored": agent.embedding_store.size,
                        "model": agent.embedding_store.model,
                        "dimensions": agent.embedding_store.dimensions,
                    }
                ]

            transcript = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": result["mode"],
                "rules_learned": result["rules_learned"],
                "success_rate": result["success_rate"],
                "summary": {
                    "qwen_usage": memory_state.get("token_usage", {}),
                    "embedding_stats": memory_state.get("embeddings", {}),
                },
                "per_call_traces": {
                    "llm_calls": call_traces,
                    "embedding_calls": embed_traces,
                },
                "artifacts": {
                    "rules": memory_state.get("procedural", {}).get("rules", []),
                    "cases": memory_state.get("cases", {}).get("recent", []),
                    "metrics": memory_state.get("metrics", {}),
                },
                "memory_state": memory_state,
            }
            transcript_path = project_dir / "docs" / "live_transcript.json"
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                transcript_path, json.dumps(transcript, indent=2, default=str)
            )
            print(f"\n  Transcript saved: {transcript_path}")
            print(
                f"  Qwen tokens used: {transcript['summary']['qwen_usage'].get('total_tokens', 0)}"
            )
            print(
                f"  Embedding API calls: {transcript['summary']['embedding_stats'].get('api_calls', 0)}"
            )
            print(
                f"  Per-call traces: {len(call_traces)} LLM calls, {len(embed_traces)} embedding batches"
            )

    elif args.reflect:
        if not _live_enabled():
            print(
                "Reflection test requires live model calls; "
                "set SAGE_ENABLE_LIVE=true (and SAGE_QWEN_API_KEY)."
            )
            return 1
        _test_reflection(project_dir)

    elif args.memory:
        from sage.agent import Agent

        agent = Agent(project_dir=str(project_dir))
        state = agent.memory.snapshot()
        print(json.dumps(state, indent=2))

    elif args.counterfactual:
        from sage.agent import Agent

        if not _live_enabled():
            print(
                "Counterfactual comparison requires live model calls; "
                "set SAGE_ENABLE_LIVE=true (and SAGE_QWEN_API_KEY)."
            )
            return 1
        agent = Agent(project_dir=str(project_dir))
        print(json.dumps(agent.evaluate_counterfactual(args.counterfactual), indent=2))

    elif args.eval:
        from sage.evaluator import Evaluator

        ev = Evaluator(project_dir=str(project_dir))
        print(ev.format_demo_stats())

    elif args.visualize:
        from sage.memory.provenance import ProvenanceGraph

        pg = ProvenanceGraph(str(project_dir / "memory" / "provenance.json"))
        stats = pg.get_stats()
        if stats["edges"] == 0:
            print(
                "No provenance data yet. Run --demo first to generate evidence links."
            )
        else:
            print(f"Provenance Graph: {stats['nodes']} nodes, {stats['edges']} edges\n")
            print(pg.to_mermaid())

    elif args.interactive or len(sys.argv) == 1:
        _interactive_mode(project_dir)

    else:
        parser.print_help()


def _test_reflection(project_dir: Path):
    """Test the reflection engine with a sample correction."""
    from sage.tools.model_caller import ModelCaller
    from sage.memory.procedural import ProceduralMemory
    from sage.memory.episodic import EpisodicMemory
    from sage.reflection import ReflectionEngine

    caller = ModelCaller(use_qwen=True)
    pm = ProceduralMemory(str(project_dir / "rules" / "rules.md"))
    em = EpisodicMemory(str(project_dir / "memory" / "episodic"))
    engine = ReflectionEngine(pm, em, model_caller=caller.call)

    print("🧪 Testing Reflection Engine")
    print("=" * 50)
    print("Task: Deploy web app to Alibaba Cloud ECS")
    print("Action: Created instance without security group")
    print("Error: Connection refused on port 80")
    print("Correction: Configure security group rules for port 80 first")
    print()

    result = engine.analyze_correction(
        task="Deploy web app to Alibaba Cloud ECS",
        action="Created ECS instance without configuring security group",
        error="Connection refused on port 80",
        correction="Configure security group rules for port 80 first. Always set up networking before deploying.",
    )

    print(f"Rule ID: {result['rule_id']}")
    print(f"Rule: {result['rule']}")
    print(f"Confidence: {result['confidence']}")
    print()
    print(f"Total rules in memory: {pm.get_rule_count()}")
    print()
    print(pm.get_rules_for_prompt())


def _interactive_mode(project_dir: Path):
    """Run interactive mode."""
    from sage.agent import Agent

    agent = Agent(project_dir=str(project_dir))

    print("🌿 Sage — Self-Improving Deployment Agent")
    print("=" * 50)
    print("Commands:")
    print("  deploy <task>     — Execute a deployment task")
    print("  correct <json>    — Provide a correction")
    print("  memory            — Show memory state")
    print("  rules             — Show learned rules")
    print("  metrics           — Show improvement metrics")
    print("  counterfactual <task> — Compare memory on/off")
    print("  reflect           — Test reflection engine")
    print("  quit              — Exit")
    print()

    while True:
        try:
            user_input = input("sage> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            continue

        if user_input == "quit":
            break

        elif user_input == "memory":
            state = agent.memory.snapshot()
            print(json.dumps(state, indent=2))

        elif user_input == "rules":
            state = agent.memory.snapshot(include={"procedural"})
            print(state["procedural"]["formatted"])

        elif user_input == "metrics":
            print(json.dumps(agent.metrics, indent=2))

        elif user_input.startswith("counterfactual "):
            print(json.dumps(agent.evaluate_counterfactual(user_input[15:]), indent=2))

        elif user_input == "reflect":
            _test_reflection(project_dir)

        elif user_input.startswith("deploy "):
            task = user_input[7:]
            result = agent.run.execute(task)
            print(f"\nResult: {result['outcome']}")
            print(f"Response: {result['response']}")
            if result["correction_needed"]:
                print(f"Correction needed: {result['correction']}")

        elif user_input.startswith("correct "):
            try:
                correction = json.loads(user_input[8:])
                result = agent.handle_correction(
                    task=correction["task"],
                    action_taken=correction["action"],
                    error=correction["error"],
                    correction=correction["correction"],
                )
                print(f"\nRule extracted: {result['rule']}")
                print(f"Rule ID: {result['rule_id']}")
                print(f"Confidence: {result['confidence']}")
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error parsing correction: {e}")
                print(
                    'Format: correct {"task":"...", "action":"...", "error":"...", "correction":"..."}'
                )

        else:
            print(f"Unknown command: {user_input}")

    # End session on exit (persists session summary for cross-session continuity)
    agent.memory.end_session()


def _show_status(project_dir: Path):
    """Show integration health and configuration status."""
    from sage.env_config import load_dotenv, get_env_summary
    from sage.tools.model_caller import ModelCaller
    from sage.tools.mcp_client import MCPClient

    load_dotenv(str(project_dir))

    print("🌿 Sage — Integration Status\n" + "=" * 50)

    # Environment variables
    print("\n📋 Environment Variables:")
    for var_name, info in get_env_summary(str(project_dir)).items():
        print(
            "  %s %s: %s (%s)"
            % ("✅" if info["set"] else "❌", var_name, info["display"], info["source"])
        )

    # Model Caller
    print("\n🧠 Model Caller:")
    try:
        caller = ModelCaller(use_qwen=False)
        qwen_ok = bool(caller.qwen_api_key)
        print("  Qwen API key: %s" % ("✅ loaded" if qwen_ok else "❌ not found"))
        print("  Active provider: %s" % ("Qwen Cloud" if qwen_ok else "none"))
        print(
            "  Retry config: %d attempts, circuit breaker threshold=%d"
            % (caller.MAX_RETRIES + 1, caller.CIRCUIT_BREAKER_THRESHOLD)
        )
    except Exception as e:
        print("  ❌ Error initializing ModelCaller: %s" % e)

    # MCP Client
    print("\n🔌 MCP Client:")
    try:
        tools = MCPClient(simulate=True).get_available_tools()
        print("  Live credentials: enter via web UI (key icon in toolbar)")
        print("  Credentials stored in server memory only — never written to disk")
        print("  Mode: simulated by default; real MCP when credentials provided via UI")
        print("  Available tools: %d" % len(tools))
        for t in tools[:5]:
            print("    - %s: %s" % (t["name"], t["description"]))
        if len(tools) > 5:
            print("    ... and %d more" % (len(tools) - 5))
    except Exception as e:
        print("  ❌ Error: %s" % e)

    # Health
    print("\n🏥 Health:")
    # Check .env in project_dir first, then repo root (mirrors load_dotenv search order)
    repo_root = Path(__file__).resolve().parent.parent.parent
    env_found = (project_dir / ".env").exists() or (repo_root / ".env").exists()
    env_loc = (
        str(project_dir / ".env")
        if (project_dir / ".env").exists()
        else str(repo_root / ".env")
        if (repo_root / ".env").exists()
        else ""
    )
    print(
        "  .env file: %s"
        % (f"✅ {env_loc}" if env_found else "❌ not found (optional)")
    )
    print(
        "  secrets dir: %s"
        % (
            "✅ exists"
            if (Path.home() / ".openclaw" / "secrets").exists()
            else "❌ not found"
        )
    )
    print(
        "\n" + "=" * 50 + "\nTip: Set env vars or create .env to enable live API calls."
    )


if __name__ == "__main__":
    sys.exit(main())
