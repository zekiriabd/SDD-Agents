"""Vues d'IR par agent — le contexte que chaque agent de construction lit de l'IR.

L'audit du 2026-09-28 : chaque `dev-*`, `qa-*` et reviewer lisait l'IR entière
(100 à 175 Ko sur un projet réel), repayée à chaque tour de chaque agent. Une
vue est une projection déterministe qui dit ce qu'elle a retiré.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdda_lib import ir_views
from sdda_scripts import ir_view

IR = {
    "irVersion": "1",
    "missionId": "1-Demo",
    "compiledFrom": {"missionHash": "sha256:aa"},
    "budget": {"costPerRunTargetUsd": 0.05},
    "orchestration": {"rootPattern": "router", "entryNode": "1-router", "maxHops": 4,
                      "nodes": [], "edges": [], "terminalNodes": []},
    "agents": [
        {"id": "1-router", "role": "route", "modelTier": "fast", "servesCaps": ["1-1"],
         "tools": [], "inputSchema": {"type": "object"}, "outputSchema": {"type": "object"},
         "trustPosture": "untrusted-input", "refusalPolicy": "refuse", "bounds": {"maxIterations": 2}},
        {"id": "1-billing", "role": "answer", "modelTier": "balanced", "servesCaps": ["1-2"],
         "tools": ["1-invoices"], "retrievers": ["1-kb"], "inputSchema": {}, "outputSchema": {},
         "trustPosture": "untrusted-input", "refusalPolicy": "refuse", "bounds": {"maxIterations": 5}},
    ],
    "tools": [
        {"id": "1-invoices", "name": "invoices", "sideEffectClass": "read-only",
         "description": "x" * 4000, "inputSchema": {"type": "object"}, "errors": []},
        {"id": "1-data-orders", "name": "orders", "sideEffectClass": "read-only",
         "description": "y" * 4000, "inputSchema": {"type": "object"}, "errors": []},
    ],
    "retrievers": [{"id": "1-kb", "pattern": "hybrid", "binding": {"store": "pgvector"}, "topK": 8}],
    "evaluation": {"suites": [], "holdout": None},
    "traceability": {"1-1": {}, "1-2": {}},
}


def test_a_view_keeps_the_header_and_declares_what_it_dropped() -> None:
    view = ir_views.project(IR, "dev-api", source_hash="sha256:ir")
    assert view["missionId"] == "1-Demo" and view["compiledFrom"] == IR["compiledFrom"]
    assert view["view"]["agent"] == "dev-api" and view["view"]["sourceIrHash"] == "sha256:ir"
    assert "tools" in view["view"]["omitted"] and "tools" not in view
    # l'interface de chaque agent, pas son contrat
    assert set(view["agents"][0]) <= set(ir_views.AGENT_INTERFACE)
    assert "agents" in view["view"]["reduced"]


def test_dev_data_sees_only_the_data_tools() -> None:
    view = ir_views.project(IR, "dev-data", source_hash="h")
    assert [t["id"] for t in view["tools"]] == ["1-data-orders"]


def test_an_agent_instance_sees_its_agent_its_tools_and_only_the_interface_of_peers() -> None:
    view = ir_views.agent_view(IR, "1-billing", source_hash="h")
    assert [a["id"] for a in view["agents"]] == ["1-billing"]
    assert [t["id"] for t in view["tools"]] == ["1-invoices"]
    assert [r["id"] for r in view["retrievers"]] == ["1-kb"]
    assert view["view"]["instance"] == "1-billing"
    (peer,) = view["peers"]
    assert peer["id"] == "1-router" and "refusalPolicy" not in peer


def test_an_unknown_instance_is_refused() -> None:
    with pytest.raises(KeyError):
        ir_views.agent_view(IR, "1-ghost", source_hash="h")


def test_views_are_smaller_than_the_ir_they_project() -> None:
    full = len(json.dumps(IR))
    for view in ir_views.all_views(IR, Path("1-system.ir.json"), source_hash="h").values():
        if view["view"]["agent"] not in {"qa-tests", "dev-tools", "dev-prompt"}:
            assert len(ir_view.dump_view(view)) < full, view["view"]["agent"]


def _project(tmp_path: Path) -> Path:
    ir_dir = tmp_path / "workspace" / ".sys" / ".ir"
    ir_dir.mkdir(parents=True)
    (ir_dir / "1-system.ir.json").write_text(json.dumps(IR), encoding="utf-8")
    return tmp_path


def test_write_then_check_is_green_and_an_ir_change_makes_the_views_stale(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert ir_view.main(["--mission", "1", "--write", "--root", str(root)]) == 0
    views = root / "workspace" / ".sys" / ".ir" / "views"
    assert (views / "1-dev-agent.1-billing.ir.json").is_file()
    assert (views / "1-dev-tools.ir.json").is_file()
    assert ir_view.main(["--mission", "1", "--check", "--root", str(root)]) == 0

    changed = dict(IR, budget={"costPerRunTargetUsd": 0.10})
    (root / "workspace" / ".sys" / ".ir" / "1-system.ir.json").write_text(json.dumps(changed), encoding="utf-8")
    assert ir_view.main(["--mission", "1", "--check", "--root", str(root)]) == 1


def test_a_removed_agent_leaves_no_orphan_view(tmp_path: Path) -> None:
    root = _project(tmp_path)
    ir_view.main(["--mission", "1", "--write", "--root", str(root)])
    smaller = dict(IR, agents=[IR["agents"][0]])
    (root / "workspace" / ".sys" / ".ir" / "1-system.ir.json").write_text(json.dumps(smaller), encoding="utf-8")
    ir_view.main(["--mission", "1", "--write", "--root", str(root)])
    assert not (root / "workspace" / ".sys" / ".ir" / "views" / "1-dev-agent.1-billing.ir.json").exists()


def test_every_view_agent_reads_its_view_in_loader_yml() -> None:
    from sdda_scripts import context_pack

    loader = context_pack.load_loader(Path(__file__).resolve().parents[3])
    for agent in ir_views.view_agents():
        reads = [str(r) for r in (loader.get(agent) or {}).get("reads") or []]
        assert "workspace/.sys/.ir/{n}-system.ir.json" not in reads, agent
        expected = "{n}-dev-agent.{agent}.ir.json" if agent == "dev-agent" else f"{{n}}-{agent}.ir.json"
        assert f"workspace/.sys/.ir/views/{expected}" in reads, agent


def test_a_stale_view_is_refused_by_the_context_resolution(tmp_path: Path) -> None:
    """Le hook de spawn passe par `resolve_context` : une vue périmée y est une ERREUR."""
    from sdda_lib.errors import Report
    from sdda_scripts import context_pack

    root = _project(tmp_path)
    ir_view.main(["--mission", "1", "--write", "--root", str(root)])
    loader = {"demo": {"budget_bytes": 10**6, "reads": ["workspace/.sys/.ir/views/{n}-dev-api.ir.json"]}}
    report = Report(name="t", target="t")
    context_pack.resolve_context(root, loader, "demo", report=report, mission="1")
    assert not report.errors

    (root / "workspace" / ".sys" / ".ir" / "1-system.ir.json").write_text(json.dumps(dict(IR, budget={})), encoding="utf-8")
    report = Report(name="t", target="t")
    context_pack.resolve_context(root, loader, "demo", report=report, mission="1")
    assert any("IR_VIEW_STALE" in str(e) for e in report.errors)


STACK = """# STACK — demo
> bannière

## Project Config
AppName: demo          # commentaire en marge
# - une option non retenue
Profile: poc

## Active Data Sources
Stores: []

## Active Memory Strategy
 - .sdda/stacks/memory/buffer.md
MemoryPIIPolicy: redact-before-write
"""


def test_a_markdown_slice_keeps_only_the_named_sections_without_comments() -> None:
    from sdda_scripts import context_pack

    text, dropped = context_pack.slice_markdown(STACK, ("Project Config", "Active Memory Strategy"), strip_comments=True)
    assert "## Project Config" in text and "## Active Memory Strategy" in text
    assert "Active Data Sources" not in text and dropped == ["Active Data Sources"]
    assert "AppName: demo" in text and "commentaire" not in text and "option non retenue" not in text
    assert "bannière" not in text and "MemoryPIIPolicy: redact-before-write" in text


def test_a_pack_serves_stack_md_sliced_and_declares_the_cut(tmp_path: Path) -> None:
    from sdda_lib.errors import Report
    from sdda_scripts import context_pack

    (tmp_path / "workspace" / "stack").mkdir(parents=True)
    (tmp_path / "workspace" / "stack" / "STACK.md").write_text(STACK, encoding="utf-8")
    loader = {"demo": {"budget_bytes": 10**6,
                       "reads": ["workspace/.sys/.context/packs/demo.md"],
                       "pack_sources": ["workspace/stack/STACK.md#sections=Active Memory Strategy"]}}
    path = context_pack.build_pack(tmp_path, loader, "demo", report=Report(name="t", target="t"))
    assert path is not None
    manifest = context_pack.read_manifest(path)
    (cut,) = manifest["trimmed"]
    assert cut["keptSections"] == ["Active Memory Strategy"]
    assert set(cut["sectionsDropped"]) == {"Project Config", "Active Data Sources"}
    body = path.read_text(encoding="utf-8")
    assert "MemoryPIIPolicy" in body and "AppName" not in body
    # Le hash est celui du fichier ENTIER : une section non retenue qui change périme le pack.
    assert context_pack.pack_state(tmp_path, loader, "demo", path)["fresh"]
    (tmp_path / "workspace" / "stack" / "STACK.md").write_text(STACK.replace("Profile: poc", "Profile: standard"), encoding="utf-8")
    assert not context_pack.pack_state(tmp_path, loader, "demo", path)["fresh"]


def test_the_harness_injected_files_are_counted_in_the_context() -> None:
    """Le fichier mémoire et la façade de l'agent sont dans chaque tour : ils comptent."""
    from sdda_lib.errors import Report
    from sdda_scripts import context_pack

    root = Path(__file__).resolve().parents[3]
    res = context_pack.resolve_context(root, context_pack.load_loader(root), "po-elicitor", report=Report(name="t", target="t"))
    labels = {f.pattern for f in res.files}
    assert {"harness:memory", "harness:agent"} <= labels
