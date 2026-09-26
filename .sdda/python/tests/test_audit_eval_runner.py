"""Audit eval 2026-09-25 — `eval_runner` : les verts qu'il rendait à tort.

Chaque test ici échouait avant le correctif :
  - un jeu vide sous un seuil `<=` était vert ;
  - `--limit` et k=1 écrivaient les rapports de gate (G8 sur un item) ;
  - le coût confronté au plafond était celui que l'application DÉCLARE, jamais
    recalculé depuis les tokens ; un modèle hors tarifs passait à 0 $ ;
  - L9 (holdout) échappait au plafond de coût ;
  - une suite sans seuil valait `>= 0`, donc toujours verte.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import make_project
from sdda_lib import paths
from sdda_lib.layered_config import read_layered_config
from sdda_scripts import eval_runner
from sdda_scripts.eval_runner import Filters, run_evals

DATASET = "workspace/pipeline/datasets/golden/audit-v1.jsonl"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return make_project(tmp_path)


def _dataset(root: Path, rows: list[dict] | None = None, raw: str | None = None) -> None:
    p = root / DATASET
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = rows if rows is not None else [{"id": f"i{i}", "input": "q", "expected": "ok"} for i in range(3)]
    p.write_text(raw if raw is not None else "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _ir(level: str = "L7", threshold: Any = ">= 0.5", **budget: float) -> dict:
    suite = {"id": "1-1-audit", "level": level, "dataset": DATASET, "grader": "exact", "runs": 3}
    if threshold is not None:
        suite["threshold"] = threshold
    return {"missionId": "1-Audit", "agents": [], "tools": [], "budget": dict(budget),
            "evaluation": {"suites": [suite], "baselineRef": "workspace/pipeline/baselines/1-system.json"}}


def _chat(model: str, tokens_in: int, tokens_out: int, declared: float | None = None) -> dict:
    attrs: dict[str, Any] = {"gen_ai.operation.name": "chat", "gen_ai.request.model": model,
                             "gen_ai.usage.input_tokens": tokens_in, "gen_ai.usage.output_tokens": tokens_out}
    if declared is not None:
        attrs["sdda.cost.usd"] = declared
    return {"span_id": "c", "name": "chat", "attributes": attrs}


class Executor:
    name = "audit"

    def __init__(self, *, cost: float = 0.0, spans: list[dict] | None = None, output: Any = "ok") -> None:
        self.cost, self.spans, self.output = cost, spans, output

    def run(self, item, *, suite, run_index, seed):
        trace = {"spans": self.spans} if self.spans is not None else {"calls": []}
        return {"output": self.output, "cost_usd": self.cost, "latency_ms": 5.0, "trace": trace}


def _classes(report) -> set[str]:
    return {f.cls for f in report.errors}


def _gates(root: Path) -> list[str]:
    d = paths.validation_dir(root)
    return sorted(p.name for p in d.glob("G*.json")) if d.is_dir() else []


# ---------------------------------------------------------------------------
# Jeu vide, lignes illisibles, seuil absent
# ---------------------------------------------------------------------------
def test_an_empty_dataset_under_a_ceiling_is_red_not_green(root: Path) -> None:
    _dataset(root, raw="pas du json\n{cassé\n")
    report, payload = run_evals(root, _ir(threshold="<= 300"), Executor(), config=read_layered_config(root),
                                write_report=False, write_gates=False)
    assert "EVAL_DATASET_EMPTY" in _classes(report) and payload["verdict"] == "red"
    assert "DATASET_ITEM_INVALID" in {f.cls for f in report.warnings}


def test_a_suite_without_threshold_is_not_evaluable(root: Path) -> None:
    _dataset(root)
    report, payload = run_evals(root, _ir(threshold=None), Executor(output="faux"), config=read_layered_config(root),
                                write_report=False, write_gates=False)
    assert "AC_NOT_EVALUABLE" in _classes(report) and payload["verdict"] == "red"


# ---------------------------------------------------------------------------
# Runs de débogage : rapportés, jamais promus en gate
# ---------------------------------------------------------------------------
def test_limit_writes_no_gate_report(root: Path) -> None:
    _dataset(root)
    report, payload = run_evals(root, _ir(level="L9"), Executor(), config=read_layered_config(root),
                                write_report=False, item_limit=1)
    assert not _gates(root) and not payload["written"]
    assert "EVAL_PARTIAL_RUN" in {f.cls for f in report.warnings}


def test_a_single_run_writes_no_gate_report(root: Path) -> None:
    _dataset(root)
    report, _ = run_evals(root, _ir(level="L9"), Executor(), config=read_layered_config(root),
                          runs_override=1, write_report=False)
    assert not _gates(root)
    assert "EVAL_SINGLE_RUN_FORBIDDEN" in {f.cls for f in report.warnings}


def test_a_full_run_still_writes_its_gate(root: Path) -> None:
    _dataset(root)
    run_evals(root, _ir(level="L9"), Executor(), config=read_layered_config(root), write_report=False)
    assert _gates(root) == ["G8-1-Audit.acceptance.json"]


# ---------------------------------------------------------------------------
# Coût : recalculé depuis les tokens, jamais relu
# ---------------------------------------------------------------------------
def test_cost_is_recomputed_from_the_spans_not_read_from_the_declared_figure(root: Path) -> None:
    """L'application annonce 0,001 $ ; ses tokens en coûtent bien plus : c'est le recalcul qui juge."""
    _dataset(root)
    spans = [_chat("claude-sonnet-4-5", 1_000_000, 1_000_000)]
    report, payload = run_evals(root, _ir(costPerRunHardCapUsd=1.0), Executor(cost=0.001, spans=spans),
                                config=read_layered_config(root), write_report=False, write_gates=False)
    assert "BUDGET_EXCEEDED_MEASURED" in _classes(report)
    row = payload["suites"][0]["items"][0]
    assert row["costUsd"] > 1.0 and row["costDeclaredUsd"] == 0.001


def test_an_unpriced_model_under_a_cap_is_an_error_not_zero(root: Path) -> None:
    _dataset(root)
    spans = [_chat("modele-hors-table", 900_000, 900_000)]
    report, _ = run_evals(root, _ir(costPerRunHardCapUsd=1.0), Executor(spans=spans),
                          config=read_layered_config(root), write_report=False, write_gates=False)
    assert "BUDGET_PRICING_UNKNOWN" in _classes(report)


def test_a_declared_cost_without_any_span_cannot_attest_the_cap(root: Path) -> None:
    _dataset(root)
    report, _ = run_evals(root, _ir(costPerRunHardCapUsd=1.0), Executor(cost=0.2),
                          config=read_layered_config(root), write_report=False, write_gates=False)
    assert "BUDGET_PRICING_UNKNOWN" in _classes(report)


def test_the_holdout_level_is_capped_too(root: Path) -> None:
    _dataset(root)
    spans = [_chat("claude-sonnet-4-5", 1_000_000, 1_000_000)]
    report, _ = run_evals(root, _ir(level="L9", costPerRunHardCapUsd=1.0), Executor(spans=spans),
                          config=read_layered_config(root), write_report=False, write_gates=False)
    assert "BUDGET_EXCEEDED_MEASURED" in _classes(report)


def test_recomputed_cost_reads_the_trace_file_when_spans_are_not_inline(tmp_path: Path) -> None:
    trace = tmp_path / "run.jsonl"
    trace.write_text(json.dumps(_chat("claude-sonnet-4-5", 1_000_000, 0)) + "\n", encoding="utf-8")
    cost, declared, problem = eval_runner.recomputed_cost({"cost_usd": 0.0, "trace": {"spans": [], "trace_path": str(trace)}})
    assert cost > 0 and declared == 0.0 and problem is None


def test_the_judge_trace_root_marks_its_kind(root: Path) -> None:
    """La trace du juge porte `sdda.run.kind: eval-judge` : c'est ce qui l'exclut des coûts du produit."""
    jt = eval_runner.JudgeTrace(root, "RID")
    jt("sdda.judge m", {"gen_ai.operation.name": "chat", "gen_ai.request.model": "m",
                        "gen_ai.usage.input_tokens": 1, "gen_ai.usage.output_tokens": 1}, "OK", 1.0)
    jt.close()
    from sdda_scripts import _run_traces as rt

    loaded = rt.load_runs(paths.traces_dir(root), 1, "1-Audit")
    assert [r.run_id for r in loaded.judge] == ["RID-judge"] and not loaded.runs


def test_filters_still_apply(root: Path) -> None:
    _dataset(root)
    report, _ = run_evals(root, _ir(), Executor(), config=read_layered_config(root), filters=Filters(suites={"autre"}),
                          write_report=False, write_gates=False)
    assert "EVAL_SUITE_NOT_FOUND" in _classes(report)
