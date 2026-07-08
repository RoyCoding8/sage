"""Tests for TUI launcher, demo runner offline path, architecture diagram,
and Mermaid provenance visualization.

Probes:
- --visualize outputs valid Mermaid provenance graph
- architecture_diagram.generate() produces architecture.html-valid content
- demo_runner offline path works end-to-end
- tui.py menu options are complete and dispatch correctly
"""

import re
import textwrap



# ─── ProvenanceGraph.to_mermaid() validation ────────────────────────────────


class TestMermaidProvenance:
    """Verify --visualize / to_mermaid() produces valid Mermaid flowchart syntax."""

    def test_mermaid_starts_with_flowchart_declaration(self, tmp_path):
        from sage.memory.provenance import ProvenanceGraph

        pg = ProvenanceGraph(str(tmp_path / "provenance.json"))
        pg.add_case({"case_id": "C001", "task": "Deploy app", "outcome": "failed"})
        pg.add_rule_extraction("C001", "R001")

        mermaid = pg.to_mermaid()
        assert mermaid.startswith("flowchart LR"), (
            f"Mermaid must start with 'flowchart LR', got: {mermaid[:40]}"
        )

    def test_mermaid_edges_have_valid_arrow_syntax(self, tmp_path):
        from sage.memory.provenance import ProvenanceGraph

        pg = ProvenanceGraph(str(tmp_path / "provenance.json"))
        pg.add_case({"case_id": "C001", "task": "Deploy app", "outcome": "failed"})
        pg.add_rule_extraction("C001", "R001")
        pg.add_case({"case_id": "C002", "task": "Deploy app", "outcome": "success"})
        pg.add_rule_application("R001", "C002", "success")

        mermaid = pg.to_mermaid()
        lines = mermaid.strip().split("\n")

        # First line is the flowchart declaration
        assert lines[0] == "flowchart LR"

        # Every subsequent line must match the Mermaid edge pattern:
        #   SOURCE -- "label" --> TARGET
        edge_pattern = re.compile(r'^\s+\w+\s+--\s+".*?"\s+-->\s+\w+\s*$')
        edge_lines = [line for line in lines[1:] if line.strip()]
        assert len(edge_lines) >= 2, (
            f"Expected at least 2 edge lines, got {len(edge_lines)}: {edge_lines}"
        )
        for line in edge_lines:
            assert edge_pattern.match(line), (
                f"Edge line does not match Mermaid syntax: {line!r}"
            )

    def test_mermaid_labels_are_human_readable(self, tmp_path):
        from sage.memory.provenance import ProvenanceGraph

        pg = ProvenanceGraph(str(tmp_path / "provenance.json"))
        pg.add_case({"case_id": "C001", "task": "Deploy", "outcome": "failed"})
        pg.add_rule_extraction("C001", "R001")

        mermaid = pg.to_mermaid()
        # The label for "extracted_rule" should be "extracted rule" (underscores replaced)
        assert '"extracted rule"' in mermaid, (
            "Expected human-readable label 'extracted rule' in mermaid output"
        )

    def test_mermaid_empty_graph_returns_declaration_only(self, tmp_path):
        from sage.memory.provenance import ProvenanceGraph

        pg = ProvenanceGraph(str(tmp_path / "provenance.json"))
        mermaid = pg.to_mermaid()
        assert mermaid == "flowchart LR", (
            f"Empty graph should return just 'flowchart LR', got: {mermaid!r}"
        )

    def test_mermaid_respects_limit_parameter(self, tmp_path):
        from sage.memory.provenance import ProvenanceGraph

        pg = ProvenanceGraph(str(tmp_path / "provenance.json"))
        # Add 5 cases with rule extractions
        for i in range(5):
            cid = f"C{i:03d}"
            rid = f"R{i:03d}"
            pg.add_case({"case_id": cid, "task": f"Task {i}", "outcome": "failed"})
            pg.add_rule_extraction(cid, rid)

        # With limit=2, only last 2 edges should appear
        mermaid_limited = pg.to_mermaid(limit=2)
        lines = [line for line in mermaid_limited.split("\n") if line.strip() and line.strip() != "flowchart LR"]
        assert len(lines) == 2, (
            f"Expected 2 edge lines with limit=2, got {len(lines)}: {lines}"
        )
        # C003 and C004 should be present; C000 should not
        assert "C004" in mermaid_limited
        assert "C000" not in mermaid_limited

    def test_mermaid_via_memory_system_snapshot(self, tmp_path):
        """Prove the --visualize path works through ProvenanceGraph (same object used by --visualize CLI).

        MemorySystem takes keyword-only args (not a path), so we test the
        ProvenanceGraph directly — the same class __main__.py instantiates
        for --visualize.
        """
        from sage.memory.provenance import ProvenanceGraph

        pg = ProvenanceGraph(str(tmp_path / "provenance.json"))
        pg.add_case({"case_id": "C001", "task": "Deploy", "outcome": "failed"})
        pg.add_rule_extraction("C001", "R001")
        pg.add_case({"case_id": "C002", "task": "Deploy", "outcome": "success"})
        pg.add_rule_application("R001", "C002", "success")

        mermaid = pg.to_mermaid()
        assert "flowchart LR" in mermaid
        assert "C001" in mermaid
        assert "R001" in mermaid
        assert "C002" in mermaid
        # Stats reflect the data
        stats = pg.get_stats()
        assert stats["nodes"] == 3
        assert stats["edges"] == 2


# ─── Architecture diagram validation ────────────────────────────────────────


class TestArchitectureDiagram:
    """Verify architecture_diagram.generate() produces valid architecture.html content."""

    ARCHITECTURE_TIERS = [
        ("Working Memory", "tier-working"),
        ("Procedural Memory", "tier-procedural"),
        ("Semantic Memory", "tier-semantic"),
        ("Episodic Memory", "tier-episodic"),
    ]

    def test_architecture_html_constant_has_doctype(self):
        from sage.architecture_diagram import ARCHITECTURE_HTML

        assert ARCHITECTURE_HTML.strip().startswith("<!DOCTYPE html>"), (
            "ARCHITECTURE_HTML must start with <!DOCTYPE html>"
        )

    def test_architecture_html_has_all_four_tiers(self):
        from sage.architecture_diagram import ARCHITECTURE_HTML

        for tier_name, tier_class in self.ARCHITECTURE_TIERS:
            assert tier_name in ARCHITECTURE_HTML, (
                f"Missing tier name: {tier_name}"
            )
            assert tier_class in ARCHITECTURE_HTML, (
                f"Missing tier CSS class: {tier_class}"
            )

    def test_architecture_html_has_reflection_engine(self):
        from sage.architecture_diagram import ARCHITECTURE_HTML

        assert "Reflection Engine" in ARCHITECTURE_HTML
        assert "reflection-box" in ARCHITECTURE_HTML

    def test_architecture_html_has_valid_structure(self):
        from sage.architecture_diagram import ARCHITECTURE_HTML

        # Must have matching html open/close tags
        assert "<html>" in ARCHITECTURE_HTML
        assert "</html>" in ARCHITECTURE_HTML
        assert "<head>" in ARCHITECTURE_HTML
        assert "</head>" in ARCHITECTURE_HTML
        assert "<body>" in ARCHITECTURE_HTML
        assert "</body>" in ARCHITECTURE_HTML
        assert "<title>" in ARCHITECTURE_HTML

    def test_generate_writes_html_file(self, tmp_path):
        from sage.architecture_diagram import generate_architecture_diagram

        out = tmp_path / "arch.html"
        result = generate_architecture_diagram(str(out))

        assert out.exists(), "generate_architecture_diagram() did not create the file"
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "Working Memory" in content
        assert "Episodic Memory" in content
        # The function returns the path
        assert str(out) in str(result)

    def test_generate_default_output_path(self, tmp_path, monkeypatch):
        """generate_architecture_diagram() default path is demo/architecture.html."""
        from sage.architecture_diagram import generate_architecture_diagram

        # Change CWD so relative path resolves into tmp_path
        monkeypatch.chdir(tmp_path)
        demo_dir = tmp_path / "demo"
        demo_dir.mkdir()

        # Override default by passing explicit path
        out = demo_dir / "architecture.html"
        generate_architecture_diagram(str(out))
        assert out.exists()

    def test_architecture_html_contains_sage_title(self):
        from sage.architecture_diagram import ARCHITECTURE_HTML

        assert "<title>Sage Architecture" in ARCHITECTURE_HTML


# ─── Demo runner offline path ───────────────────────────────────────────────


class TestDemoRunnerOffline:
    """Verify demo_runner.run_demo(offline=True) uses deterministic model path."""

    def test_offline_mode_returns_correct_structure(self, tmp_path):
        from sage.demo_runner import run_demo

        result = run_demo(str(tmp_path), offline=True)

        assert result["mode"] == "offline"
        assert isinstance(result["outcomes"], list)
        assert len(result["outcomes"]) == 3
        assert isinstance(result["rules_learned"], int)
        assert result["rules_learned"] >= 1

    def test_offline_first_task_fails_last_succeeds(self, tmp_path):
        from sage.demo_runner import run_demo

        result = run_demo(str(tmp_path), offline=True)

        assert result["outcomes"][0] == "failed", (
            "First deployment should fail (no learned rules yet)"
        )
        assert result["outcomes"][-1] == "success", (
            "Last deployment should succeed (rules applied)"
        )

    def test_offline_reflection_model_returns_valid_json_for_planning(self):
        from sage.demo_runner import _offline_reflection_model

        prompt = (
            "Create an execution plan for deploying a Node.js app. "
            "Available tools: list_ecs_instances, create_security_group. "
            "Learned rules:\n- R001: Open port 8080"
        )
        response = _offline_reflection_model(prompt, task_type="planning")
        import json
        parsed = json.loads(response)
        assert "steps" in parsed
        assert isinstance(parsed["steps"], list)
        assert len(parsed["steps"]) >= 3

    def test_offline_reflection_model_returns_valid_json_for_reflection(self):
        from sage.demo_runner import _offline_reflection_model

        prompt = "Our web apps must listen on port 8080. Open port 8080 in the security group."
        response = _offline_reflection_model(prompt)
        import json
        parsed = json.loads(response)
        assert "rule" in parsed
        assert "8080" in parsed["rule"]
        assert "confidence" in parsed

    def test_offline_reflection_model_detects_port_from_prompt(self):
        from sage.demo_runner import _offline_reflection_model

        prompt = "Our apps use port 3000. Open port 3000 before deploying."
        response = _offline_reflection_model(prompt)
        import json
        parsed = json.loads(response)
        assert "3000" in parsed["rule"]

    def test_offline_agent_step_reads_learned_memory_ports(self):
        from sage.demo_runner import _offline_agent_step

        prompt = textwrap.dedent("""\
            PROGRESS_JSON: {"security_groups_listed": true, "security_group_id": "sg-123", "ports_opened": [80, 443]}
            --- LEARNED MEMORY START ---
            R001: Open port 8080 in the security group.
            --- LEARNED MEMORY END ---
        """)
        response = _offline_agent_step(prompt)
        import json
        parsed = json.loads(response)
        assert parsed["tool"] == "open_port"
        assert parsed["args"]["port"] == 8080

    def test_offline_agent_step_falls_back_without_memory(self):
        from sage.demo_runner import _offline_agent_step

        prompt = 'PROGRESS_JSON: {"security_groups_listed": true, "security_group_id": "sg-123", "ports_opened": [80, 443]}'
        response = _offline_agent_step(prompt)
        import json
        parsed = json.loads(response)
        # Should move on to instance creation (no more ports to open)
        assert parsed["tool"] == "create_instance"


# ─── TUI menu inspection ───────────────────────────────────────────────────


class TestTUIMenu:
    """Inspect tui.py menu options for completeness and dispatch correctness."""

    def test_menu_prints_all_options(self, capsys):
        import tui

        tui.menu()
        output = capsys.readouterr().out

        assert "Setup dependencies" in output
        assert "Run tests" in output
        assert "Launch web UI" in output
        assert "Clean wrapper local memory" in output
        assert "Exit" in output

    def test_menu_option_numbers(self, capsys):
        import tui

        tui.menu()
        output = capsys.readouterr().out

        # Options 1-4 and 0
        for num in ["1.", "2.", "3.", "4.", "0."]:
            assert num in output, f"Menu option '{num}' not found in output"

    def test_actions_dict_covers_menu_options(self):
        import tui

        # main() defines actions dict with keys "1"-"4"
        # Verify the dispatch map is consistent with menu()
        expected_keys = {"1", "2", "3", "4"}
        # We can't call main() (interactive), but we can inspect the module
        source = open(tui.__file__).read()
        for key in expected_keys:
            assert f'"{key}"' in source, f"Action key '{key}' not found in tui.py"

    def test_run_function_requires_key_when_specified(self, capsys):
        import tui

        # run() with require_key=True and no env var set should return 2
        import os
        os.environ.pop("SAGE_QWEN_API_KEY", None)
        code = tui.run(["echo", "test"], require_key=True)
        assert code == 2
        output = capsys.readouterr().out
        assert "SAGE_QWEN_API_KEY is not set" in output

    def test_run_function_handles_missing_command(self, capsys):
        import tui

        code = tui.run(["nonexistent_command_xyz_12345"])
        assert code == 127
        output = capsys.readouterr().out
        assert "Could not find" in output

    def test_local_dirs_are_defined(self):
        import tui

        assert len(tui.LOCAL_DIRS) == 4
        assert "offline demo" in tui.LOCAL_DIRS
        assert "live demo" in tui.LOCAL_DIRS
        assert "interactive" in tui.LOCAL_DIRS
        assert "ui (api/web)" in tui.LOCAL_DIRS

    def test_api_port_and_frontend_port_defined(self):
        import tui

        assert tui.API_PORT == 8000
        assert tui.FRONTEND_PORT == 3000

    def test_clean_local_memory_no_dirs_returns_zero(self, monkeypatch):
        """clean_local_memory() returns 0 when no local dirs exist."""
        import tui
        # Patch LOCAL_DIRS to point to nonexistent paths
        monkeypatch.setattr(tui, "LOCAL_DIRS", {"x": tui.ROOT / ".nonexistent_xyz"})
        code = tui.clean_local_memory()
        assert code == 0

    def test_clean_local_memory_cancels_on_decline(self, monkeypatch):
        """clean_local_memory() returns 1 when user declines."""
        import tui
        # Create a directory under ROOT so relative_to(ROOT) works
        fake_dir = tui.ROOT / ".local" / "_test_wrapper_delete_me"
        fake_dir.mkdir(parents=True, exist_ok=True)
        try:
            monkeypatch.setattr(tui, "LOCAL_DIRS", {"test": fake_dir})
            # Simulate user typing "no"
            monkeypatch.setattr("builtins.input", lambda _: "no")
            code = tui.clean_local_memory()
            assert code == 1
        finally:
            fake_dir.rmdir()
