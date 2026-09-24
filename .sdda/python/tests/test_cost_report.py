"""cost-report : des mesures recalculées, ventilées, et comparées — jamais estimées.

Défendu ici : sous le volume minimal, le rapport refuse de parler de p95 ; le
coût vient des tokens, pas du chiffre déclaré ; la construction reste une
facture à part ; un p95 au-dessus du plafond est rouge ; un run qui dépasse
`maxHops` est une boucle non bornée ; la queue nomme le run qui la fait.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main  # type: ignore
from sdda_lib import tracing
from sdda_scripts import cost_report, ir_compiler
from trace_builders import MODEL, write_build_run, write_run  # type: ignore

ROUTE = ["1-intent-classifier", "1-billing-specialist"]
OUT = "workspace/.sys/.validation/cost-1.json"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = make_project(tmp_path)
    ir_compiler.main(["--root", str(r), "--mission", "1", "--no-report"])
    return r


def _runs(root: Path, n: int, **kw) -> None:
    for i in range(n):
        write_run(root, f"item-{i:03d}-0", ROUTE, **kw)


def _report(root: Path, *extra: str) -> tuple[int, dict, dict]:
    code, out = run_main(cost_report.main, ["--root", str(root), "--mission", "1", "--json", *extra])
    payload = json.loads((root / OUT).read_text(encoding="utf-8"))
    return code, json.loads(out), payload


def _classes(result: dict) -> set[str]:
    return {f["class"] for f in result["errors"]} | {f["class"] for f in result["warnings"]}


def _unit_cost(tokens_in: int = 1000, tokens_out: int = 200) -> float:
    usd, _ = tracing.span_cost_usd({"gen_ai.request.model": MODEL, "gen_ai.usage.input_tokens": tokens_in,
                                    "gen_ai.usage.output_tokens": tokens_out})
    return usd or 0.0


def test_below_the_minimal_volume_there_is_no_p95(root: Path) -> None:
    _runs(root, 5)
    code, result, payload = _report(root)
    assert code == 1 and "MEASUREMENT_MISSING" in _classes(result)
    assert payload["runsRead"] == 5


def test_cost_is_recalculated_and_broken_down(root: Path) -> None:
    _runs(root, 30, declared=0.5)                 # chiffre déclaré faux, à dessein
    code, result, payload = _report(root)
    assert code == 0, result
    per_run = 2 * _unit_cost()
    assert payload["costUsd"]["mean"] == pytest.approx(per_run, rel=1e-6)
    assert payload["declaredVsRecalculated"]["divergentRuns"]            # l'écart est rapporté
    assert set(payload["byAgent"]) == set(ROUTE)
    assert set(payload["byCap"]) == {"1-1-ClassifyIntent", "1-2-ExplainInvoiceLine"}
    assert payload["byCap"]["1-2-ExplainInvoiceLine"]["share"] == pytest.approx(0.5, abs=1e-6)
    assert set(payload["byNode"]) == {"classify", "billing"}
    assert payload["hops"]["max"] == 2
    assert {r["measure"] for r in payload["thresholds"]} >= {"costUsd.p95", "costUsd.mean", "latencyMs.p95", "hops.max"}


def test_build_cost_is_a_separate_bill(root: Path) -> None:
    _runs(root, 30)
    write_build_run(root, "build-001", cost=4.2)
    write_run(root, "other-mission-0", ROUTE, mission="2")
    _, _, payload = _report(root)
    assert payload["buildCostUsd"] == pytest.approx(4.2)
    assert payload["runs"] == 30 and payload["runsOtherMission"] == 1
    assert payload["totalCostUsd"] == pytest.approx(30 * 2 * _unit_cost(), rel=1e-6)


def test_a_p95_above_the_hard_cap_is_red(root: Path) -> None:
    _runs(root, 28)
    write_run(root, "expensive-001-0", ROUTE, tokens_in=400_000, tokens_out=40_000)
    write_run(root, "expensive-002-0", ROUTE, tokens_in=400_000, tokens_out=40_000)
    code, result, payload = _report(root)
    assert code == 1 and "BUDGET_EXCEEDED_MEASURED" in _classes(result)
    assert payload["tail"][0]["runId"].startswith("expensive-")


def test_more_hops_than_max_hops_is_an_unbounded_loop(root: Path) -> None:
    _runs(root, 29)
    write_run(root, "loop-001-0", ROUTE * 4)        # 8 passages > maxHops 6
    code, result, _ = _report(root)
    assert code == 1 and "UNBOUNDED_LOOP" in _classes(result)


def test_iterations_of_one_agent_are_not_hops(root: Path) -> None:
    _runs(root, 29)
    write_run(root, "iter-001-0", ["1-intent-classifier", "1-billing-specialist", "1-billing-specialist", "1-billing-specialist"])
    _, _, payload = _report(root)
    assert payload["hops"]["max"] == 2


def test_without_ir_the_report_refuses(project: Path) -> None:
    code, out = run_main(cost_report.main, ["--root", str(project), "--mission", "1"])
    assert code == 1 and "IR_NOT_FOUND" in out
