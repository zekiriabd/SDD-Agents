"""Audit eval 2026-09-25 — régression, épinglage, rapports, traces, coûts, suites d'outil.

Chaque test échouait avant le correctif :
  - `check-regression` n'écrivait aucune part de gate : `[REGRESSION]` ne
    bloquait pas `compute-status --require-gate G8` ;
  - `latest_report` rendait `1-RUN.json` après `1-RUN-2.json` ;
  - un agent sans retriever épinglait l'index de tout l'IR ;
  - la trace du juge comptait comme un run du produit ;
  - `cost-report` : un modèle hors tarifs sous un plafond n'était qu'un
    avertissement, une latence p95 hors cible aussi ;
  - G3 `suites` : des tests tous `skip`, ou un id cité en commentaire, valaient
    des cas exercés.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_lib import eval_reports, paths
from sdda_lib.eval_pinning import current_pins
from sdda_lib.gate_reports import GATE_PARTS
from sdda_scripts import _run_traces as rt
from sdda_scripts import check_regression, cost_report, run_tool_suites


# ---------------------------------------------------------------------------
# check_regression : la part `regression` de G8
# ---------------------------------------------------------------------------
IR = {"missionId": "1-Audit", "agents": [], "tools": [],
      "evaluation": {"suites": [], "baselineRef": "workspace/pipeline/baselines/1-system.json"}}


def _write(root: Path, rel: str, data: dict) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def regress_root(tmp_path: Path) -> Path:
    root = make_project(tmp_path)
    _write(root, "workspace/.sys/.ir/1-system.ir.json", IR)
    _write(root, "workspace/pipeline/baselines/1-system.json", {"baselines": {
        "s1": {"metric": "hallucination_rate", "mean": 0.0, "stddev": 0.0, "passRate": 1.0, "verdict": "green", "pins": {}},
        "gone": {"metric": "m", "mean": 0.9, "stddev": 0.0, "passRate": 1.0, "verdict": "green", "pins": {}}}})
    _write(root, "workspace/.sys/reports/1-RUN.json", {"missionId": "1-Audit", "runId": "RUN", "suites": [
        {"suiteId": "s1", "metric": "hallucination_rate", "mean": 0.5, "threshold": "<= 0.1", "pins": {}}]})
    return root


def test_g8_requires_the_regression_part() -> None:
    assert "regression" in GATE_PARTS["G8"]


def test_a_regression_writes_a_red_g8_part_that_compute_status_reads(regress_root: Path) -> None:
    code, out = run_main(check_regression.main, ["--root", str(regress_root), "--mission", "1", "--run", "RUN", "--json"])
    assert code == 1
    gate = json.loads((paths.validation_dir(regress_root) / "G8-1-Audit.regression.json").read_text(encoding="utf-8"))
    assert gate["ok"] is False and gate["part"] == "regression"
    assert "REGRESSION" in {e["class"] for e in gate["errors"]}           # 0 -> 0,5 sur `<=` : plus « stable »
    assert "EVAL_SUITE_NOT_FOUND" in {w["class"] for w in gate["warnings"]}  # la baseline `gone` non comparée
    assert any(k.startswith("file:workspace/pipeline/baselines/") for k in gate["pinnedHashes"])


def test_no_report_writes_no_gate(regress_root: Path) -> None:
    run_main(check_regression.main, ["--root", str(regress_root), "--mission", "1", "--run", "RUN", "--json", "--no-report"])
    assert not (paths.validation_dir(regress_root) / "G8-1-Audit.regression.json").exists()


# ---------------------------------------------------------------------------
# eval_reports / eval_pinning
# ---------------------------------------------------------------------------
def test_latest_report_is_the_last_one_written(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    d = paths.reports_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    for name in ("1-20260901T000000Z.json", "1-RUN.json", "1-RUN-2.json", "1-RUN-10.json"):
        (d / name).write_text("{}", encoding="utf-8")
    assert eval_reports.latest_report(root, 1).name == "1-RUN-10.json"
    assert [p.name for p in eval_reports.list_reports(root, 1)][-3:] == ["1-RUN.json", "1-RUN-2.json", "1-RUN-10.json"]


def test_an_agent_without_retriever_pins_no_index(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    ir = {"agents": [{"id": "a", "retrievers": []}], "retrievers": [{"id": "r", "indexHash": "sha256:0123456789abcdef"}]}
    assert current_pins(root, ir, agent_id="a").indexHash == ""


# ---------------------------------------------------------------------------
# Traces : le juge n'est pas le produit ; le coût non tarifable bloque
# ---------------------------------------------------------------------------
def _trace(d: Path, run_id: str, spans: list[dict]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{run_id}.jsonl").write_text("".join(json.dumps(s) + "\n" for s in spans), encoding="utf-8")


def _root_span(run_id: str, attrs: dict) -> dict:
    return {"run_id": run_id, "trace_id": run_id, "span_id": "root", "name": f"sdda.run {run_id}", "status": "OK",
            "attributes": attrs, "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:00:01Z", "duration_ms": 1000}


def _chat(run_id: str, model: str, parent: str = "root", **extra) -> dict:
    return {"run_id": run_id, "trace_id": run_id, "span_id": f"c-{model}", "parent_span_id": parent, "name": "chat",
            "status": "OK", "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:00:01Z",
            "attributes": {"gen_ai.operation.name": "chat", "gen_ai.request.model": model,
                           "gen_ai.usage.input_tokens": 900_000, "gen_ai.usage.output_tokens": 900_000, **extra}}


def test_the_judge_trace_is_not_a_product_run(tmp_path: Path) -> None:
    traces = tmp_path / "runs"
    _trace(traces, "r1", [_root_span("r1", {"sdda.mission.id": "1-X"}), _chat("r1", "claude-sonnet-4-5")])
    _trace(traces, "R-judge", [_root_span("R-judge", {"sdda.run.kind": "eval-judge"}), _chat("R-judge", "gpt-x")])
    # Racine jamais écrite (eval interrompue) : reconnue par ses spans de juge.
    _trace(traces, "R2-judge", [_chat("R2-judge", "gpt-x", parent="absent", **{"sdda.judge.role": "llm-judge"})])
    loaded = rt.load_runs(traces, 1, "1-X")
    assert [r.run_id for r in loaded.runs] == ["r1"]
    assert sorted(r.run_id for r in loaded.judge) == ["R-judge", "R2-judge"]


def test_an_unpriced_call_under_a_cap_is_an_error(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    traces = tmp_path / "runs"
    _trace(traces, "r1", [_root_span("r1", {"sdda.mission.id": "1-X"}), _chat("r1", "modele-hors-table")])
    ir = {"missionId": "1-X", "budget": {"costPerRunHardCapUsd": 1.0}, "agents": [], "orchestration": {}}
    report, _ = cost_report.run(root, ir, traces, min_runs=1)
    assert "BUDGET_PRICING_UNKNOWN" in {f.cls for f in report.errors}


def test_a_latency_p95_over_target_is_an_error(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    traces = tmp_path / "runs"
    _trace(traces, "r1", [_root_span("r1", {"sdda.mission.id": "1-X"}), _chat("r1", "claude-sonnet-4-5")])
    ir = {"missionId": "1-X", "budget": {"latencyP95TargetMs": 10}, "agents": [], "orchestration": {}}
    report, _ = cost_report.run(root, ir, traces, min_runs=1)
    assert "LATENCY_P95_EXCEEDED" in {f.cls for f in report.errors}


# ---------------------------------------------------------------------------
# run_tool_suites : un cas se prouve par un test VERT
# ---------------------------------------------------------------------------
def test_skipped_parameters_and_comment_citations_do_not_cover_a_case(tmp_path: Path) -> None:
    test_file = tmp_path / "test_t.py"
    test_file.write_text("import pytest\n\n# 'timeout-1' à écrire un jour\n"
                         "@pytest.mark.parametrize('case', ['happy-1'])\ndef test_c(case):\n    pytest.skip('plus tard')\n",
                         encoding="utf-8")
    junit = tmp_path / "j.xml"
    junit.write_text('<testsuite><testcase classname="tests.test_t" name="test_c[happy-1]"><skipped message="plus tard"/>'
                     "</testcase></testsuite>", encoding="utf-8")
    results = run_tool_suites.parse_junit(junit)
    assert run_tool_suites.coverage_gaps(["happy-1", "timeout-1"], [test_file], results) == ["happy-1", "timeout-1"]


def test_a_literal_inside_a_passing_test_covers_its_case(tmp_path: Path) -> None:
    test_file = tmp_path / "test_t.py"
    test_file.write_text("def test_timeout():\n    case = 'timeout'\n    assert case\n", encoding="utf-8")
    results = [{"file": "test_t", "classes": ["tests", "test_t"], "name": "test_timeout", "outcome": "passed"}]
    assert run_tool_suites.coverage_gaps(["timeout"], [test_file], results) == []


def test_network_classification_needs_the_same_module(tmp_path: Path) -> None:
    marked = {("test_live", "test_ping")}
    other = {"file": "test_contract", "classes": ["tests", "test_contract"], "name": "test_ping", "outcome": "failed"}
    assert not run_tool_suites.is_network_case(other, marked)


def test_all_skipped_contract_tests_make_the_part_red(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sdda_lib.layered_config import app_name
    from test_compute_status import _pass_g0_g1, _pass_g2

    _pass_g0_g1(project)
    _pass_g2(project)
    monkeypatch.setenv("SDDA_TOOL_SUITES_PYTHON", sys.executable)
    monkeypatch.delenv("CI", raising=False)
    tool, suite_id = "1-invoice-lookup", "tool-1-invoice-lookup"
    suites = project / "workspace/pipeline/suites"
    suites.mkdir(parents=True, exist_ok=True)
    (suites / f"{suite_id}.yaml").write_text(f'id: "{suite_id}"\nlevel: "L2"\ntoolRef: "{tool}"\ncases:\n  - id: "happy-1"\n',
                                             encoding="utf-8")
    tests = paths.app_dir(project, app_name(project)) / "tools" / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_skipped.py").write_text(
        f'import pytest\nSUITE = "{suite_id}"\n\n@pytest.mark.parametrize("case", ["happy-1"])\n'
        "def test_generic(case):\n    pytest.skip('plus tard')\n", encoding="utf-8")
    code, _ = run_main(run_tool_suites.main, ["--root", str(project), "--mission", "1", "--tool", tool])
    gate = json.loads((paths.validation_dir(project) / f"G3-{tool}.suites.json").read_text(encoding="utf-8"))
    assert code != 0 and gate["ok"] is False
    assert "sauté" in " ".join(e["message"] for e in gate["errors"])
