"""Inférence locale : le tarif par token est nul, le temps machine ne l'est pas.

`LocalComputeCostPerHourUsd` et `LocalComputeThroughputTokensPerSec` vivaient
dans config.base.yml — avec la promesse qu'`estimate_budget.py` les lirait —
sans être dans le schéma ni lus par personne : un projet `local-ollama` avait un
budget « gratuit », et la TOPOLOGY GATE laissait passer n'importe quelle
topologie. C'est le faux vert budgétaire que P6 refuse.
"""
from __future__ import annotations

from pathlib import Path

from conftest import make_project
from sdda_lib import paths
from sdda_lib.layered_config import read_layered_config
from sdda_scripts import estimate_budget, ir_compiler

STACK = Path("workspace") / "stack" / "STACK.md"
RATE, THROUGHPUT = 3.6, 100.0          # 3.6 USD/h à 100 tok/s = 1e-5 USD par token


def _local(tmp_path: Path, rate: float | None = RATE, throughput: float | None = THROUGHPUT) -> Path:
    project = make_project(tmp_path)
    stack = project / STACK
    text = stack.read_text(encoding="utf-8")
    assert "RuntimeProvider: anthropic\n" in text and "AppName: SupportAssistant\n" in text
    text = text.replace("RuntimeProvider: anthropic\n", "RuntimeProvider: local-ollama\n", 1)
    lines = ""
    if rate is not None:
        lines += f"LocalComputeCostPerHourUsd: {rate}\n"
    if throughput is not None:
        lines += f"LocalComputeThroughputTokensPerSec: {throughput}\n"
    stack.write_text(text.replace("AppName: SupportAssistant\n", "AppName: SupportAssistant\n" + lines, 1),
                     encoding="utf-8")
    return project


def _ir(project: Path) -> dict:
    ir_compiler.compile_to_file(project, 1, compiled_at="2026-09-20T10:00:00Z")
    return ir_compiler.load_ir(paths.ir_path(project, 1))


def test_a_cloud_provider_is_not_local(project: Path) -> None:
    assert estimate_budget.local_compute(project, read_layered_config(project)) is None


def test_the_keys_are_read_from_the_layered_config(tmp_path: Path) -> None:
    project = _local(tmp_path)
    assert estimate_budget.local_compute(project, read_layered_config(project)) == (RATE, THROUGHPUT)
    # Sans déclaration : les défauts de la base, 0 et 0.
    bare = _local(tmp_path / "b", rate=None, throughput=None)
    assert estimate_budget.local_compute(bare, read_layered_config(bare)) == (0.0, 0.0)


def test_each_agent_visit_is_priced_as_machine_time(tmp_path: Path) -> None:
    project = _local(tmp_path)
    ir = _ir(project)
    nominal, _, unknown = estimate_budget.node_visits(ir, root=project, tier_map={}, local=(RATE, THROUGHPUT))
    assert unknown == [], "aucune table de prix n'est consultée en local : rien ne peut y manquer"
    agents = {n["id"] for n in ir["orchestration"]["nodes"] if n.get("kind") == "agent"}
    assert agents
    for nid in agents:
        visit = nominal[nid]
        seconds = visit.tokens / THROUGHPUT
        assert visit.cost_usd == round(seconds / 3600 * RATE, 6) > 0
        assert visit.latency_ms == seconds * 1000


def test_the_estimate_reports_the_local_model_and_stays_deterministic(tmp_path: Path) -> None:
    project = _local(tmp_path)
    ir = _ir(project)
    cfg = read_layered_config(project)
    report, est = estimate_budget.estimate(ir, root=project, config=cfg)
    assert report.data["localCompute"] == {"costPerHourUsd": RATE, "throughputTokensPerSec": THROUGHPUT, "modelled": True}
    assert estimate_budget.CLS_LOCAL_COMPUTE_UNMODELLED not in {w.cls for w in report.warnings}
    assert est["nominalCostUsd"] > 0 and est["worstCaseCostUsd"] >= est["nominalCostUsd"]
    assert estimate_budget.estimate(ir, root=project, config=cfg)[1] == est


def test_an_unknown_throughput_is_a_zero_that_is_said(tmp_path: Path) -> None:
    project = _local(tmp_path, rate=RATE, throughput=0)
    ir = _ir(project)
    report, _ = estimate_budget.estimate(ir, root=project, config=read_layered_config(project))
    assert estimate_budget.CLS_LOCAL_COMPUTE_UNMODELLED in {w.cls for w in report.warnings}
    assert report.data["localCompute"]["modelled"] is False
    nominal, _, _ = estimate_budget.node_visits(ir, root=project, tier_map={}, local=(RATE, 0.0))
    agents = [n["id"] for n in ir["orchestration"]["nodes"] if n.get("kind") == "agent"]
    assert all(nominal[a].cost_usd == 0.0 and nominal[a].latency_ms > 0 for a in agents)
