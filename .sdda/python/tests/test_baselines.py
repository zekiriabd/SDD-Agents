"""Baselines — fraîcheur (P10), promotion explicite (§8), régression (L9).

Ce que ces scripts REFUSENT : promouvoir un rouge en silence, comparer à une
baseline qui mesurait autre chose, laisser une baseline périmée passer pour
valable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import run_main
from sdda_lib import paths
from sdda_lib.eval_pinning import load_baselines
from sdda_lib.eval_reports import atomic_write_json
from sdda_lib.layered_config import read_layered_config
from sdda_scripts import check_baseline_freshness, check_regression, eval_runner, ir_compiler, promote_baseline
from sdda_scripts.eval_runner import Filters, run_evals

FIXED_AT = "2026-09-20T10:00:00Z"
sid_routing = "1-1-routing_accuracy"
sid_citations = "1-2-citation_resolve_rate"
sid_groundedness = "1-2-groundedness"
BASELINE = "workspace/pipeline/baselines/1-system.json"


class ScoredExecutor:
    name = "scored"

    def __init__(self, score: float) -> None:
        self.score = score

    def run(self, item: dict[str, Any], *, suite: dict[str, Any], run_index: int, seed: int | None) -> dict[str, Any]:
        return {"output": {"score": self.score}, "cost_usd": 0.0, "latency_ms": 0.0, "trace": {}}


GRADERS = {"exact": lambda item, output, trace, m: float(output["score"]), "llm-judge": lambda item, output, trace, m: float(output["score"])}


@pytest.fixture
def compiled(project: Path) -> tuple[Path, dict]:
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    return project, ir_compiler.load_ir(paths.ir_path(project, 1))


def _run(root: Path, ir: dict, score: float, run_id: str, suites: set[str] | None = None) -> dict:
    cfg = read_layered_config(root)
    _, payload = run_evals(root, ir, ScoredExecutor(score), config=cfg, filters=Filters(suites=suites or {sid_routing, sid_citations}),
                           graders=GRADERS, run_id=run_id, write_gates=False)
    return payload


def _promote(root: Path, *extra: str) -> tuple[int, str]:
    return run_main(promote_baseline.main, ["--root", str(root), "--mission", "1", *extra])


def _edit_prompt(root: Path, slug: str = "intent-classifier") -> None:
    p = root / f"workspace/src/SupportAssistant/prompts/{slug}.system.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nUne ligne de plus.\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# promote_baseline
# ---------------------------------------------------------------------------
def test_promote_requires_a_label(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 1.0, "G")
    code, out = _promote(root, "--run", "G")
    assert code == 1 and "[EVAL_PROMOTION_LABEL_MISSING]" in out
    assert not (root / BASELINE).exists()


def test_promote_green_writes_baseline_atomically_with_pins_and_label(compiled) -> None:
    root, ir = compiled
    payload = _run(root, ir, 1.0, "G")
    code, out = _promote(root, "--run", "G", "--label", "après correction du chunking", "--json")
    assert code == 0, out
    data = json.loads(out)
    assert sorted(data["data"]["promoted"]) == [sid_routing, sid_citations] and data["data"]["promotionPolicy"] == "explicit"
    bpath = root / BASELINE
    assert bpath.is_file() and not list(bpath.parent.glob("*.tmp"))
    raw = json.loads(bpath.read_text(encoding="utf-8"))
    assert raw["missionId"] == "1-SupportAssistant" and raw["lastLabel"] == "après correction du chunking"
    entry = raw["baselines"][sid_routing]
    assert entry["label"] == "après correction du chunking" and entry["verdict"] == "green" and entry["forced"] is False
    assert entry["pins"] == next(s for s in payload["suites"] if s["suiteId"] == sid_routing)["pins"]
    assert entry["sourceReport"] == "workspace/.sys/reports/1-G.json" and entry["pinDigest"].startswith("sha256:")
    loaded = load_baselines(bpath)
    assert loaded[sid_routing].mean == 1.0 and loaded[sid_routing].pins.promptHash.startswith("sha256:")


def test_promote_refuses_red_without_force(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 0.5, "R")
    code, out = _promote(root, "--run", "R", "--label", "tentative")
    assert code == 1 and "[EVAL_PROMOTION_REFUSED]" in out and "rouge" in out
    assert not (root / BASELINE).exists()
    assert not (paths.audit_dir(root) / "bypasses.jsonl").exists()


def test_promote_red_with_force_is_audit_logged_and_keeps_the_red_verdict(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 0.5, "R")
    code, out = _promote(root, "--run", "R", "--label", "régression assumée : nouveau dataset plus dur", "--force", "--operator", "alice")
    assert code == 0, out
    assert "[EVAL_PROMOTION_FORCED]" in out
    audit = (paths.audit_dir(root) / "bypasses.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(audit) == 1
    entry = json.loads(audit[0])
    assert entry["gate"] == "G8" and entry["operator"] == "alice" and "régression assumée" in entry["reason"] and sid_routing in entry["reason"]
    raw = json.loads((root / BASELINE).read_text(encoding="utf-8"))
    assert raw["baselines"][sid_routing]["verdict"] == "red" and raw["baselines"][sid_routing]["forced"] is True


def test_promote_refuses_a_report_that_no_longer_measures_the_disk(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 1.0, "G")
    _edit_prompt(root)
    code, out = _promote(root, "--run", "G", "--label", "après édition")
    assert code == 1 and "[EVAL_BASELINE_STALE]" in out and "promptHash" in out
    assert "[EVAL_PROMOTION_REFUSED]" not in out       # le résultat est vert : c'est la fraîcheur qui refuse
    assert not (root / BASELINE).exists()


def test_promote_merges_suites_and_keeps_previous_entries(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 1.0, "A", suites={sid_routing})
    assert _promote(root, "--run", "A", "--label", "routing")[0] == 0
    _run(root, ir, 1.0, "B", suites={sid_citations})
    assert _promote(root, "--run", "B", "--label", "citations")[0] == 0
    raw = json.loads((root / BASELINE).read_text(encoding="utf-8"))
    assert set(raw["baselines"]) == {sid_routing, sid_citations}
    assert raw["baselines"][sid_routing]["label"] == "routing" and raw["baselines"][sid_citations]["label"] == "citations"


def test_promote_defaults_to_latest_report_and_filters_suites(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 1.0, "20260920T100000Z")
    _run(root, ir, 0.99, "20260920T110000Z")
    code, out = _promote(root, "--suite", sid_routing, "--label", "dernier run", "--json")
    assert code == 0, out
    raw = json.loads((root / BASELINE).read_text(encoding="utf-8"))
    assert list(raw["baselines"]) == [sid_routing] and raw["baselines"][sid_routing]["runId"] == "20260920T110000Z"
    code, out = _promote(root, "--suite", "ghost", "--label", "x")
    assert code == 1 and "[EVAL_SUITE_NOT_FOUND]" in out


def test_promote_without_any_report(compiled) -> None:
    root, _ = compiled
    code, out = _promote(root, "--label", "rien")
    assert code == 1 and "[EVAL_REPORT_NOT_FOUND]" in out


# ---------------------------------------------------------------------------
# check_baseline_freshness
# ---------------------------------------------------------------------------
def test_freshness_without_baseline_is_a_warning_not_a_pass(compiled) -> None:
    root, _ = compiled
    code, out = run_main(check_baseline_freshness.main, ["--root", str(root), "--mission", "1"])
    assert code == 0 and "[EVAL_BASELINE_MISSING]" in out


def test_edited_prompt_makes_baseline_stale_and_names_the_dimension(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 1.0, "G")
    assert _promote(root, "--run", "G", "--label", "base")[0] == 0
    code, out = run_main(check_baseline_freshness.main, ["--root", str(root), "--mission", "1"])
    assert code == 0 and "EVAL_BASELINE_STALE" not in out and "à jour" in out

    _edit_prompt(root)
    code, out = run_main(check_baseline_freshness.main, ["--root", str(root), "--mission", "1", "--json"])
    assert code == 1
    data = json.loads(out)
    assert [e["class"] for e in data["errors"]] == ["EVAL_BASELINE_STALE"]
    stale = data["data"]["stale"]
    assert [s["suiteId"] for s in stale] == [sid_routing] and list(stale[0]["moved"]) == ["promptHash"]
    assert stale[0]["moved"]["promptHash"]["pinned"] != stale[0]["moved"]["promptHash"]["current"]
    assert data["data"]["fresh"] == [sid_citations]


def test_edited_dataset_moves_the_dataset_dimension(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 1.0, "G")
    assert _promote(root, "--run", "G", "--label", "base")[0] == 0
    ds = root / "workspace/pipeline/datasets/golden/billing-v1.jsonl"
    ds.write_text(ds.read_text(encoding="utf-8") + json.dumps({"id": "billing-new", "input": {"question": "?"}, "expected": {"answer_contains": "x"}}) + "\n", encoding="utf-8")
    code, out = run_main(check_baseline_freshness.main, ["--root", str(root), "--mission", "1"])
    assert code == 1 and "[EVAL_BASELINE_STALE]" in out and "datasetHash" in out and sid_citations in out


def test_reordering_a_dataset_does_not_stale_the_baseline(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 1.0, "G")
    assert _promote(root, "--run", "G", "--label", "base")[0] == 0
    ds = root / "workspace/pipeline/datasets/golden/routing-v1.jsonl"
    lines = [l for l in ds.read_text(encoding="utf-8").splitlines() if l.strip()]
    ds.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    code, out = run_main(check_baseline_freshness.main, ["--root", str(root), "--mission", "1"])
    assert code == 0, out


def test_advisory_stale_baseline_warns_unless_strict(compiled) -> None:
    root, ir = compiled
    for s in ir["evaluation"]["suites"]:
        if s["id"] == sid_groundedness:
            s["advisory"] = True
    ir_compiler.dump_ir(ir)
    paths.ir_path(root, 1).write_bytes(ir_compiler.dump_ir(ir))
    _run(root, ir, 1.0, "G", suites={sid_groundedness})
    assert _promote(root, "--run", "G", "--label", "base")[0] == 0
    _edit_prompt(root, "billing-specialist")
    code, out = run_main(check_baseline_freshness.main, ["--root", str(root), "--mission", "1"])
    assert code == 0 and "WARN [EVAL_BASELINE_STALE]" in out
    code, out = run_main(check_baseline_freshness.main, ["--root", str(root), "--mission", "1", "--strict"])
    assert code == 1 and "CAUSE: [EVAL_BASELINE_STALE]" in out


def test_orphan_baseline_is_reported(compiled) -> None:
    root, ir = compiled
    atomic_write_json(root / BASELINE, {"baselines": {"ghost-suite": {"metric": "x", "mean": 1.0, "stddev": 0, "pass_rate": 1, "verdict": "green", "pins": {}}}})
    code, out = run_main(check_baseline_freshness.main, ["--root", str(root), "--mission", "1"])
    assert code == 0 and "[EVAL_SUITE_NOT_FOUND]" in out and "ghost-suite" in out


# ---------------------------------------------------------------------------
# check_regression
# ---------------------------------------------------------------------------
def test_regression_beyond_tolerance_blocks(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 1.0, "G")
    assert _promote(root, "--run", "G", "--label", "base")[0] == 0
    _run(root, ir, 0.9, "DROP")                       # -10 % > RegressionTolerancePct 3 %
    code, out = run_main(check_regression.main, ["--root", str(root), "--mission", "1", "--run", "DROP", "--json"])
    assert code == 1
    data = json.loads(out)
    assert {e["class"] for e in data["errors"]} == {"REGRESSION"}
    assert sorted(data["data"]["regressions"]) == [sid_routing, sid_citations]
    routing = next(r for r in data["data"]["suites"] if r["suiteId"] == sid_routing)
    assert routing["status"] == "regression" and routing["deltaPct"] == pytest.approx(-10.0)


def test_small_drop_within_tolerance_passes(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 1.0, "G")
    assert _promote(root, "--run", "G", "--label", "base")[0] == 0
    _run(root, ir, 0.99, "TINY")                      # -1 %
    code, out = run_main(check_regression.main, ["--root", str(root), "--mission", "1", "--run", "TINY"])
    assert code == 0 and "stable" in out and "REGRESSION" not in out
    code, out = run_main(check_regression.main, ["--root", str(root), "--mission", "1", "--run", "TINY", "--tolerance", "0.5"])
    assert code == 1 and "[REGRESSION]" in out       # la tolérance est un paramètre, pas une constante cachée


def test_regression_refuses_to_compare_when_pins_moved(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 1.0, "G")
    assert _promote(root, "--run", "G", "--label", "base")[0] == 0
    _edit_prompt(root)                                 # seul l'agent du routing bouge
    _run(root, ir, 0.5, "AFTER")                       # une chute énorme… sur un autre système
    code, out = run_main(check_regression.main, ["--root", str(root), "--mission", "1", "--run", "AFTER", "--json"])
    assert code == 1
    data = json.loads(out)
    by_class = {e["class"]: e for e in data["errors"]}
    assert "EVAL_BASELINE_STALE" in by_class and "promptHash" in by_class["EVAL_BASELINE_STALE"]["message"]
    rows = {r["suiteId"]: r for r in data["data"]["suites"]}
    assert rows[sid_routing]["status"] == "stale" and rows[sid_routing]["deltaPct"] is None      # refus : aucun delta rendu
    assert rows[sid_citations]["status"] == "regression"                                       # l'autre agent, lui, est comparable


def test_regression_without_baseline_warns_unless_required(compiled) -> None:
    root, ir = compiled
    _run(root, ir, 1.0, "G")
    code, out = run_main(check_regression.main, ["--root", str(root), "--mission", "1"])
    assert code == 0 and "[EVAL_BASELINE_MISSING]" in out
    code, out = run_main(check_regression.main, ["--root", str(root), "--mission", "1", "--require-baseline"])
    assert code == 1 and "[EVAL_BASELINE_MISSING]" in out


def test_regression_honours_metric_direction(compiled) -> None:
    """Une latence qui BAISSE est une amélioration ; le même delta brut sur une accuracy est une régression."""
    root, ir = compiled
    pins = {"promptHash": "sha256:" + "a" * 64, "modelId": "m", "indexHash": "", "toolSchemaHash": "", "datasetHash": "sha256:" + "b" * 64}
    ir["evaluation"]["suites"] = [
        {"id": "lat", "level": "L7", "dataset": "workspace/pipeline/datasets/golden/billing-v1.jsonl", "grader": "latency", "threshold": "<= 300", "runs": 3},
        {"id": "acc", "level": "L7", "dataset": "workspace/pipeline/datasets/golden/billing-v1.jsonl", "grader": "exact", "threshold": ">= 0.9", "runs": 3},
    ]
    paths.ir_path(root, 1).write_bytes(ir_compiler.dump_ir(ir))
    atomic_write_json(root / BASELINE, {"baselines": {
        "lat": {"metric": "latency_ms", "mean": 200.0, "stddev": 0, "pass_rate": 1, "verdict": "green", "pins": pins},
        "acc": {"metric": "accuracy", "mean": 1.0, "stddev": 0, "pass_rate": 1, "verdict": "green", "pins": pins},
    }})
    report = root / "workspace/.sys/reports/1-DIR.json"
    atomic_write_json(report, {"missionId": "1-SupportAssistant", "runId": "DIR", "suites": [
        {"suiteId": "lat", "metric": "latency_ms", "mean": 180.0, "threshold": "<= 300", "pins": pins, "advisory": False},
        {"suiteId": "acc", "metric": "accuracy", "mean": 0.9, "threshold": ">= 0.9", "pins": pins, "advisory": False},
    ]})
    code, out = run_main(check_regression.main, ["--root", str(root), "--mission", "1", "--run", "DIR", "--json"])
    assert code == 1
    rows = {r["suiteId"]: r for r in json.loads(out)["data"]["suites"]}
    assert rows["lat"]["status"] == "improved" and rows["lat"]["deltaPct"] == pytest.approx(10.0)
    assert rows["acc"]["status"] == "regression" and rows["acc"]["deltaPct"] == pytest.approx(-10.0)


def _pins_and_baseline(root: Path, ir, *, stddev: float) -> dict:
    """Une baseline `acc` à 1.0 avec l'écart-type demandé, et un rapport à 0.95 (-5 %)."""
    pins = {"promptHash": "sha256:" + "a" * 64, "modelId": "m", "indexHash": "", "toolSchemaHash": "", "datasetHash": "sha256:" + "b" * 64}
    ir["evaluation"]["suites"] = [
        {"id": "acc", "level": "L7", "dataset": "workspace/pipeline/datasets/golden/billing-v1.jsonl", "grader": "exact", "threshold": ">= 0.9", "runs": 3},
    ]
    paths.ir_path(root, 1).write_bytes(ir_compiler.dump_ir(ir))
    atomic_write_json(root / BASELINE, {"baselines": {
        "acc": {"metric": "accuracy", "mean": 1.0, "stddev": stddev, "pass_rate": 1, "verdict": "green", "pins": pins},
    }})
    atomic_write_json(root / "workspace/.sys/reports/1-NOISE.json", {"missionId": "1-SupportAssistant", "runId": "NOISE", "suites": [
        {"suiteId": "acc", "metric": "accuracy", "mean": 0.95, "threshold": ">= 0.9", "pins": pins, "advisory": False},
    ]})
    return pins


def test_a_drop_within_the_baseline_noise_band_is_not_a_regression(compiled) -> None:
    """-5 % dépasse la tolérance (3 %), mais la baseline a σ = 0.04 : à 2 σ la bande vaut ±0.08 > 0.05."""
    root, ir = compiled
    _pins_and_baseline(root, ir, stddev=0.04)
    code, out = run_main(check_regression.main, ["--root", str(root), "--mission", "1", "--run", "NOISE", "--json"])
    assert code == 0, out
    data = json.loads(out)
    assert data["data"]["regressions"] == [] and data["data"]["withinNoise"] == ["acc"]
    row = data["data"]["suites"][0]
    assert row["status"] == "within-noise" and row["baselineStddev"] == pytest.approx(0.04) and row["noiseBand"] == pytest.approx(0.08)
    assert {w["class"] for w in data["warnings"]} == {"REGRESSION_WITHIN_NOISE"}


def test_a_drop_beyond_the_noise_band_stays_a_regression(compiled) -> None:
    """Même -5 %, mais σ = 0.01 : la bande vaut ±0.02, la chute est réelle."""
    root, ir = compiled
    _pins_and_baseline(root, ir, stddev=0.01)
    code, out = run_main(check_regression.main, ["--root", str(root), "--mission", "1", "--run", "NOISE", "--json"])
    assert code == 1
    data = json.loads(out)
    assert data["data"]["regressions"] == ["acc"] and data["data"]["withinNoise"] == []
    assert "hors de la bande de bruit" in data["errors"][0]["message"]


def test_noise_sigma_zero_ignores_the_stddev(compiled) -> None:
    """`--noise-sigma 0` (ou RegressionNoiseSigma: 0) : comportement strict d'avant."""
    root, ir = compiled
    _pins_and_baseline(root, ir, stddev=0.04)
    code, out = run_main(check_regression.main, ["--root", str(root), "--mission", "1", "--run", "NOISE", "--noise-sigma", "0"])
    assert code == 1 and "[REGRESSION]" in out and "WITHIN_NOISE" not in out


def test_a_baseline_without_stddev_keeps_the_strict_behaviour(compiled) -> None:
    """σ = 0 (k=1 ou baseline ancienne) : aucune bande, la tolérance seule décide — comme avant."""
    root, ir = compiled
    _pins_and_baseline(root, ir, stddev=0.0)
    code, out = run_main(check_regression.main, ["--root", str(root), "--mission", "1", "--run", "NOISE", "--json"])
    assert code == 1
    assert json.loads(out)["data"]["suites"][0]["noiseBand"] == 0.0


def test_regression_on_advisory_suite_informs_without_blocking(compiled) -> None:
    root, ir = compiled
    for s in ir["evaluation"]["suites"]:
        if s["id"] == sid_groundedness:
            s["advisory"] = True
    paths.ir_path(root, 1).write_bytes(ir_compiler.dump_ir(ir))
    _run(root, ir, 1.0, "G", suites={sid_groundedness})
    assert _promote(root, "--run", "G", "--label", "base")[0] == 0
    _run(root, ir, 0.5, "DROP", suites={sid_groundedness})
    code, out = run_main(check_regression.main, ["--root", str(root), "--mission", "1", "--run", "DROP"])
    assert code == 0 and "WARN [REGRESSION]" in out and "advisory" in out
