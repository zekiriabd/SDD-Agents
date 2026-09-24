"""trajectory-report : le graphe qui tourne est-il le graphe déclaré ?

Défendu ici : les nœuds `function` n'ont pas de span, et une transition qui ne
traverse qu'eux est admise ; une transition hors IR est rouge ; `maxHops`
dépassé est une boucle non bornée ; le routage se mesure PAR CLASSE, et un
misroute vers un agent à effet de bord est critique ; bornes, impasses,
handoffs et repli jamais emprunté sont nommés.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main  # type: ignore
from sdda_lib import paths
from sdda_scripts import ir_compiler, trajectory_report
from trace_builders import write_run  # type: ignore

CLASSIFY, BILLING = "1-intent-classifier", "1-billing-specialist"
OUT = "workspace/.sys/.validation/trajectories-1.json"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = make_project(tmp_path)
    ir_compiler.main(["--root", str(r), "--mission", "1", "--no-report"])
    return r


def _nominal(root: Path, n_billing: int = 20, n_technical: int = 10) -> None:
    """Items du golden de routage : `billing` passe au spécialiste, `technical` part au repli."""
    for k in range(n_billing):
        write_run(root, f"routing-billing-001-{k}", [CLASSIFY, BILLING])
    for k in range(n_technical):
        write_run(root, f"routing-technical-002-{k}", [CLASSIFY])


def _report(root: Path) -> tuple[int, dict, dict]:
    code, out = run_main(trajectory_report.main, ["--root", str(root), "--mission", "1", "--json"])
    return code, json.loads(out), json.loads((root / OUT).read_text(encoding="utf-8"))


def _classes(result: dict) -> set[str]:
    return {f["class"] for f in result["errors"]} | {f["class"] for f in result["warnings"]}


def test_below_the_minimal_volume_the_report_refuses(root: Path) -> None:
    _nominal(root, 3, 2)
    code, result, _ = _report(root)
    assert code == 1 and "MEASUREMENT_MISSING" in _classes(result)


def test_a_conforming_graph_is_green_and_measured_per_class(root: Path) -> None:
    _nominal(root)
    code, result, payload = _report(root)
    assert code == 0, result
    assert payload["distinctPaths"] == 2 and payload["paths"][0] == {"path": "classify > billing", "runs": 20, "share": pytest.approx(20 / 30)}
    assert payload["edgesOutsideIr"] == [] and "billing -> classify" in payload["edgesNeverTaken"]
    assert payload["fallbackTaken"] == 10 and payload["terminalReached"] == 30
    per = payload["routing"]["perClass"]
    assert per["billing"] == {"n": 20, "expected": "billing", "accuracy": 1.0, "observed": {"billing": 20}}
    assert per["technical"]["expected"] == "clarify" and per["technical"]["accuracy"] == 1.0
    assert "ROUTER_FALLBACK_UNTESTED" not in _classes(result)


def test_a_transition_outside_the_ir_and_a_loop_are_red(root: Path) -> None:
    _nominal(root)
    write_run(root, "rogue-001-0", [CLASSIFY, "1-rogue-agent"])
    write_run(root, "loop-001-0", [CLASSIFY, BILLING] * 4)
    code, result, payload = _report(root)
    assert code == 1
    assert {"TRAJECTORY_VIOLATION", "UNBOUNDED_LOOP", "ORCH_PING_PONG"} <= _classes(result)
    assert payload["tail"][0]["runId"] == "loop-001-0"


def test_a_misroute_to_a_destructive_agent_is_critical(root: Path) -> None:
    _nominal(root)
    write_run(root, "routing-technical-002-99", [CLASSIFY, BILLING])     # billing porte zendesk (external-side-effect)
    code, result, payload = _report(root)
    assert code == 1 and "MISROUTE_TO_DESTRUCTIVE" in _classes(result)
    assert payload["routing"]["perClass"]["technical"]["observed"] == {"billing": 1, "clarify": 10}


def test_dead_ends_and_bound_behaviour_are_named(root: Path) -> None:
    _nominal(root)
    write_run(root, "crash-001-0", [CLASSIFY, BILLING], root_status="ERROR")
    write_run(root, "bound-001-0", [CLASSIFY, BILLING], bound={1: "maxIterations"})   # fail-explicit déclaré, statut OK
    code, result, payload = _report(root)
    assert {"TRAJECTORY_DEAD_END", "BOUND_BEHAVIOR_MISMATCH"} <= _classes(result)
    assert payload["boundsExceeded"] == {"maxIterations": 1}


def test_an_edge_without_a_declared_handoff_is_uncontracted(root: Path) -> None:
    path = paths.ir_path(root, 1)
    ir = json.loads(path.read_text(encoding="utf-8"))
    for agent in ir["agents"]:
        if agent["id"] == CLASSIFY:
            agent["handoffs"] = []
    path.write_text(json.dumps(ir), encoding="utf-8")
    _nominal(root)
    _, result, _ = _report(root)
    assert "HANDOFF_UNCONTRACTED" in _classes(result)


def test_a_fallback_never_taken_is_untested(root: Path) -> None:
    _nominal(root, n_billing=30, n_technical=0)
    _, result, payload = _report(root)
    assert "ROUTER_FALLBACK_UNTESTED" in _classes(result)
    assert payload["fallbackTaken"] == 0
