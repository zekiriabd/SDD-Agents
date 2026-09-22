"""estimate_budget — nominal, pire cas, comparaison aux cibles de la MISSION."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_lib import paths
from sdda_lib.layered_config import read_layered_config
from sdda_scripts import estimate_budget, ir_compiler

FIXED_AT = "2026-09-20T10:00:00Z"


def _compiled(root: Path) -> dict:
    ir_compiler.compile_to_file(root, 1, compiled_at=FIXED_AT)
    return ir_compiler.load_ir(paths.ir_path(root, 1))


def test_project_ok_is_under_budget_and_estimate_is_written(project: Path) -> None:
    _compiled(project)
    code, out = run_main(estimate_budget.main, ["--root", str(project), "--mission", "1", "--json"])
    assert code == 0, out
    ir = ir_compiler.load_ir(paths.ir_path(project, 1))
    est = ir["budget"]["estimated"]
    assert set(est) == {"nominalCostUsd", "worstCaseCostUsd", "nominalLatencyMs", "worstCaseLatencyMs", "worstCasePath"}
    assert 0 < est["nominalCostUsd"] <= est["worstCaseCostUsd"] <= ir["budget"]["costPerRunHardCapUsd"]
    assert est["nominalLatencyMs"] <= est["worstCaseLatencyMs"]
    assert est["worstCasePath"][0] == "classify"
    hops = len(est["worstCasePath"]) - 1
    assert hops <= ir["orchestration"]["maxHops"]
    rep = json.loads((paths.validation_dir(project) / "G2-1-SupportAssistant.budget.json").read_text(encoding="utf-8"))
    assert rep["ok"] is True and rep["part"] == "budget"


def test_estimate_is_deterministic(project: Path) -> None:
    ir = _compiled(project)
    cfg = read_layered_config(project)
    _, a = estimate_budget.estimate(ir, root=project, config=cfg)
    _, b = estimate_budget.estimate(ir, root=project, config=cfg)
    assert a == b


def test_worst_case_walks_the_cycle_up_to_max_hops(project: Path) -> None:
    ir = _compiled(project)
    _, est = estimate_budget.estimate(ir, root=project, config=read_layered_config(project))
    path = est["worstCasePath"]
    # 6 hops : classify -> billing -> classify -> billing -> classify -> billing -> finalize
    assert len(path) - 1 == 6
    assert path.count("billing") == 3


def test_worst_case_over_hard_cap_is_rejected(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_budget_exceeded")
    _compiled(root)
    code, out = run_main(estimate_budget.main, ["--root", str(root), "--mission", "1", "--no-report", "--no-write"])
    assert code == 1
    assert "[BUDGET_EXCEEDED_ESTIMATE]" in out and "0.02" in out
    assert "budget.estimated" not in ir_compiler.load_ir(paths.ir_path(root, 1))["budget"].get("estimated", {})


def test_in_memory_hard_cap_below_worst_case(project: Path) -> None:
    ir = _compiled(project)
    ir["budget"]["costPerRunHardCapUsd"] = 0.001
    report, est = estimate_budget.estimate(ir, root=project, config=read_layered_config(project))
    assert report.has("BUDGET_EXCEEDED_ESTIMATE")
    assert est["worstCaseCostUsd"] > 0.001


def test_nominal_over_target_is_only_a_warning(project: Path) -> None:
    ir = _compiled(project)
    ir["budget"]["costPerRunTargetUsd"] = 0.0001
    report, _ = estimate_budget.estimate(ir, root=project, config=read_layered_config(project))
    assert report.ok
    assert "BUDGET_TARGET_MISSED" in {w.cls for w in report.warnings}


def test_token_ceiling_exceeded_is_a_warning_not_a_block(project: Path) -> None:
    # Le plafond de tokens est une borne du système généré : il coupe, il n'est pas dépassé.
    ir = _compiled(project)
    ir["budget"]["tokenCeilingPerRun"] = 100
    report, _ = estimate_budget.estimate(ir, root=project, config=read_layered_config(project))
    assert report.ok and "TOKEN_CEILING_EXCEEDED" in {w.cls for w in report.warnings}


def test_bypass_without_reason_is_refused(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ir = _compiled(project)
    ir["budget"]["costPerRunHardCapUsd"] = 0.001
    monkeypatch.setenv("SDDA_BYPASS_BUDGET_ESTIMATE", "1")
    report, _ = estimate_budget.estimate(ir, root=project, config=read_layered_config(project))
    assert report.has("BYPASS_REASON_MISSING") and report.has("BUDGET_EXCEEDED_ESTIMATE") and not report.ok


def test_bypass_with_reason_is_audited(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ir = _compiled(project)
    ir["budget"]["costPerRunHardCapUsd"] = 0.001
    monkeypatch.setenv("SDDA_BYPASS_BUDGET_ESTIMATE", "1")
    monkeypatch.setenv("SDDA_BYPASS_REASON", "POC jetable, revue le 2026-10-01")
    report, _ = estimate_budget.estimate(ir, root=project, config=read_layered_config(project))
    assert report.ok and "BUDGET_EXCEEDED_ESTIMATE" in {w.cls for w in report.warnings}
    audit = (paths.audit_dir(project) / "bypasses.jsonl").read_text(encoding="utf-8")
    assert "POC jetable" in audit and "G2.budget" in audit


def test_missing_ir(project: Path) -> None:
    code, out = run_main(estimate_budget.main, ["--root", str(project), "--mission", "1", "--no-report"])
    assert code == 1 and "[IR_NOT_FOUND]" in out
