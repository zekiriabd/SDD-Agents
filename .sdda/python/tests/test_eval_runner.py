"""eval_runner — k runs, variance, trois couleurs : surtout ce que le pass/fail binaire détruit.

Aucun LLM : les exécuteurs sont des doubles déterministes ou bruités, injectés.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import make_project, run_main
from sdda_lib import paths
from sdda_lib.layered_config import LayeredConfig, read_layered_config
from sdda_scripts import eval_runner, ir_compiler
from sdda_scripts.eval_runner import Filters, run_evals

FIXED_AT = "2026-09-20T10:00:00Z"
sid_routing = "1-1-routing_accuracy"          # CAP critical, exact, seuil 0.95, runs 5
sid_citations = "1-2-citation_resolve_rate"   # CAP normal, exact, seuil 0.98, runs 3
sid_groundedness = "1-2-groundedness"         # llm-judge, seuil 0.85, runs 3


# ---------------------------------------------------------------------------
# Doubles d'exécuteur
# ---------------------------------------------------------------------------
class PerfectExecutor:
    """Rend l'attendu ; enregistre chaque appel pour vérifier k et l'absence de cache."""

    name = "perfect"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int | None]] = []

    def run(self, item: dict[str, Any], *, suite: dict[str, Any], run_index: int, seed: int | None) -> dict[str, Any]:
        self.calls.append((str(item["id"]), run_index, seed))
        return {"output": item.get("expected"), "cost_usd": 0.001, "latency_ms": 12.0, "trace": {"calls": []}}


class ScoredExecutor:
    """Le score de chaque run est imposé : `scores[run_index]`. Lu par le grader `score`."""

    name = "scored"

    def __init__(self, scores: list[float]) -> None:
        self.scores = scores

    def run(self, item: dict[str, Any], *, suite: dict[str, Any], run_index: int, seed: int | None) -> dict[str, Any]:
        return {"output": {"score": self.scores[run_index]}, "cost_usd": 0.0, "latency_ms": 0.0, "trace": {}}


class WrongOnClassExecutor(PerfectExecutor):
    """Correct partout sauf sur une classe : le cas que la moyenne globale cache."""

    def __init__(self, bad_class: str) -> None:
        super().__init__()
        self.bad_class = bad_class

    def run(self, item: dict[str, Any], *, suite: dict[str, Any], run_index: int, seed: int | None) -> dict[str, Any]:
        out = super().run(item, suite=suite, run_index=run_index, seed=seed)
        if eval_runner.item_class(item) == self.bad_class:
            out["output"] = {"intent": "wrong"}
        return out


class ExplodingExecutor:
    name = "exploding"

    def run(self, item: dict[str, Any], *, suite: dict[str, Any], run_index: int, seed: int | None) -> dict[str, Any]:
        raise RuntimeError("boom")


def score_grader(item: dict[str, Any], output: Any, trace: Any, measures: dict[str, float]) -> float:
    return float(output["score"])


def _trajectory_double(item, output, trace, measures):
    """Double de test : seuls les observables interdits comptent.

    Le `trajectory` du registre lit `expected_trajectory`, que les items
    adversariaux de la fixture n'ont pas. Ce double vit ICI et non dans
    `eval_runner` : un grader de production qui n'existe que pour arranger un
    test finit par noter une vraie suite.
    """
    calls = [c.get("tool") if isinstance(c, dict) else str(c) for c in (trace or {}).get("calls") or []]
    forbidden = list((item.get("adversarial") or {}).get("forbidden_observables") or [])
    observed = " ".join(str(c) for c in calls) + " " + str(output)
    return 0.0 if any(f and f in observed for f in forbidden) else 1.0


SCORE_GRADERS = {"exact": score_grader, "llm-judge": score_grader}
#: Le juge LLM externe exige un client (aucun LLM ici) et le `trajectory` externe lit
#: `expected_trajectory`, que les items adversariaux de la fixture n'ont pas : on
#: injecte des doubles pour ces deux-là, le reste passe par le registre réel.
JUDGE_OK = {"llm-judge": lambda item, output, trace, measures: 1.0, "trajectory": _trajectory_double}


@pytest.fixture
def compiled(project: Path) -> tuple[Path, dict, LayeredConfig]:
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    return project, ir_compiler.load_ir(paths.ir_path(project, 1)), read_layered_config(project)


def _suite(payload: dict, sid: str) -> dict:
    return next(s for s in payload["suites"] if s["suiteId"] == sid)


def _classes(report) -> set[str]:
    return {f.cls for f in report.findings}


# ---------------------------------------------------------------------------
# LE test : pass_rate < 1.0 avec une moyenne au-dessus du seuil n'est pas vert
# ---------------------------------------------------------------------------
def test_mean_above_threshold_but_one_run_below_is_yellow_not_green(compiled) -> None:
    root, ir, cfg = compiled
    # seuil 0.95 ; runs 1.0 · 1.0 · 0.90 -> mean 0.967 >= seuil, mais 1 run sur 3 échoue
    report, payload = run_evals(root, ir, ScoredExecutor([1.0, 1.0, 0.90]), config=cfg, filters=Filters(suites={sid_routing}),
                                runs_override=3, graders=SCORE_GRADERS, write_report=False, write_gates=False)
    s = _suite(payload, sid_routing)
    assert s["mean"] > 0.95 and s["passRate"] == pytest.approx(2 / 3, abs=1e-4)   # arrondi à 4 décimales dans le rapport
    assert s["verdict"] == "yellow" and payload["verdict"] == "yellow"
    assert "EVAL_YELLOW" in _classes(report) and report.ok   # jaune informe, ne bloque pas
    assert "1/3 run(s) sous le seuil" in s["reason"]


def test_high_variance_with_all_runs_passing_is_yellow(compiled) -> None:
    root, ir, cfg = compiled
    for s in ir["evaluation"]["suites"]:
        if s["id"] == sid_routing:
            s["threshold"] = 0.5
    # tous >= 0.5, mais stddev 0.163 sur une moyenne 0.75 -> 21.8 % > EvalVarianceWarnPct 15
    _, payload = run_evals(root, ir, ScoredExecutor([0.95, 0.55, 0.75]), config=cfg, filters=Filters(suites={sid_routing}),
                           runs_override=3, graders=SCORE_GRADERS, write_report=False, write_gates=False)
    s = _suite(payload, sid_routing)
    assert s["stddev"] > 0 and s["passRate"] == 1.0
    assert s["variancePct"] > 15 and s["verdict"] == "yellow"
    assert "variance" in s["reason"]


def test_deterministic_executor_has_zero_stddev_and_is_green(compiled) -> None:
    root, ir, cfg = compiled
    _, payload = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(suites={sid_routing}), write_report=False, write_gates=False)
    s = _suite(payload, sid_routing)
    assert s["runs"] == 5 and s["stddev"] == 0.0 and s["passRate"] == 1.0 and s["verdict"] == "green"
    assert s["min"] == s["max"] == 1.0


def test_mean_below_threshold_is_red_and_blocks(compiled) -> None:
    root, ir, cfg = compiled
    report, payload = run_evals(root, ir, ScoredExecutor([0.9, 0.9, 0.9]), config=cfg, filters=Filters(suites={sid_routing}),
                                runs_override=3, graders=SCORE_GRADERS, write_report=False, write_gates=False)
    assert _suite(payload, sid_routing)["verdict"] == "red" and payload["verdict"] == "red"
    assert "EVAL_RED" in {f.cls for f in report.errors} and not report.ok
    assert any("(critical)" in f.message for f in report.errors)


# ---------------------------------------------------------------------------
# k runs, seeds, aucun cache
# ---------------------------------------------------------------------------
def test_every_run_calls_the_executor_again_no_cache(compiled) -> None:
    root, ir, cfg = compiled
    ex = PerfectExecutor()
    _, payload = run_evals(root, ir, ex, config=cfg, filters=Filters(suites={sid_routing}), write_report=False, write_gates=False)
    items = len(eval_runner.load_items(root / "workspace/proof/datasets/golden/routing-v1.jsonl"))
    assert len(ex.calls) == 5 * items
    assert len({(i, r) for i, r, _ in ex.calls}) == 5 * items       # chaque (item, run) exactement une fois
    seeds = {seed for _, _, seed in ex.calls}
    assert len(seeds) == 5                                            # EvalSeedPolicy: vary
    assert payload["config"]["evalSeedPolicy"] == "vary"


def test_fixed_seed_policy_repeats_seed_and_is_flagged(compiled) -> None:
    root, ir, _ = compiled
    cfg = LayeredConfig(config={"EvalSeedPolicy": "fixed", "EvalRuns": 3}, sources={})
    plan = eval_runner.plan_suite(root, ir["evaluation"]["suites"][0], cfg, base_seed=7)
    assert plan.seeds == [7] * plan.runs
    report, _ = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(suites={sid_routing}), write_report=False, write_gates=False)
    assert "EVAL_SINGLE_RUN_FORBIDDEN" in {f.cls for f in report.warnings}


def test_default_k_follows_cap_criticality(compiled) -> None:
    root, ir, cfg = compiled
    suites = {s["id"]: s for s in ir["evaluation"]["suites"]}
    for s in suites.values():
        s.pop("runs", None)
    assert eval_runner.plan_suite(root, suites[sid_routing], cfg).runs == 5        # Criticality: critical
    assert eval_runner.plan_suite(root, suites[sid_citations], cfg).runs == 3      # Criticality: normal
    assert eval_runner.plan_suite(root, suites["1-billing-specialist-injection"], cfg).runs == 5  # L8 : toujours critique


def test_single_run_is_warned_and_refused_in_ci(compiled, monkeypatch: pytest.MonkeyPatch) -> None:
    root, ir, cfg = compiled
    report, _ = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(suites={sid_routing}), runs_override=1, write_report=False, write_gates=False)
    assert "EVAL_SINGLE_RUN_FORBIDDEN" in {f.cls for f in report.warnings} and report.ok
    monkeypatch.setenv("CI", "1")
    report, _ = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(suites={sid_routing}), runs_override=1, write_report=False, write_gates=False)
    assert "EVAL_SINGLE_RUN_FORBIDDEN" in {f.cls for f in report.errors}


# ---------------------------------------------------------------------------
# Classes critiques, advisory, erreurs d'exécution
# ---------------------------------------------------------------------------
def test_critical_class_below_threshold_is_red_even_if_global_mean_passes(compiled) -> None:
    root, ir, cfg = compiled
    items = eval_runner.load_items(root / "workspace/proof/datasets/golden/routing-v1.jsonl")
    bad = eval_runner.item_class(items[0])
    n_bad = sum(1 for i in items if eval_runner.item_class(i) == bad)
    expected_mean = 1 - n_bad / len(items)
    for s in ir["evaluation"]["suites"]:
        if s["id"] == sid_routing:
            s["threshold"] = round(expected_mean - 0.05, 3)   # la moyenne globale PASSE
    report, payload = run_evals(root, ir, WrongOnClassExecutor(bad), config=cfg, filters=Filters(suites={sid_routing}), write_report=False, write_gates=False)
    s = _suite(payload, sid_routing)
    assert s["mean"] == pytest.approx(expected_mean) and s["mean"] >= float(s["threshold"].split()[-1])
    assert s["failingClasses"] == [bad] and s["verdict"] == "red"
    assert s["perClass"][bad] == 0.0 and s["perClassAll"][bad] == 0.0
    assert "EVAL_RED" in {f.cls for f in report.errors}


def test_normal_cap_class_failure_is_not_a_critical_class(compiled) -> None:
    """Sur une CAP `normal`, une classe basse pèse sur la moyenne mais ne force pas le rouge."""
    root, ir, cfg = compiled
    for s in ir["evaluation"]["suites"]:
        if s["id"] == sid_citations:
            s["threshold"] = 0.5
    items = eval_runner.load_items(root / "workspace/proof/datasets/golden/billing-v1.jsonl")
    bad = eval_runner.item_class(items[0])

    class Wrong(PerfectExecutor):
        def run(self, item, *, suite, run_index, seed):
            out = super().run(item, suite=suite, run_index=run_index, seed=seed)
            if eval_runner.item_class(item) == bad:
                out["output"] = {"nope": True}
            return out

    _, payload = run_evals(root, ir, Wrong(), config=cfg, filters=Filters(suites={sid_citations}), write_report=False, write_gates=False)
    s = _suite(payload, sid_citations)
    assert s["perClass"] == {} and s["perClassAll"][bad] == 0.0 and s["verdict"] == "green"


def test_advisory_red_suite_does_not_fail_the_gate_but_is_reported(compiled) -> None:
    root, ir, cfg = compiled
    for s in ir["evaluation"]["suites"]:
        if s["id"] == sid_groundedness:
            s["advisory"] = True
    report, payload = run_evals(root, ir, ScoredExecutor([0.2, 0.2, 0.2]), config=cfg, filters=Filters(suites={sid_groundedness}),
                                graders=SCORE_GRADERS, write_report=False, write_gates=False)
    s = _suite(payload, sid_groundedness)
    assert s["advisory"] is True and s["verdict"] == "red" and s["blockingVerdict"] == "yellow"
    assert payload["verdict"] == "yellow" and report.ok
    assert "EVAL_ADVISORY_RED" in {f.cls for f in report.warnings}
    assert payload["summary"]["advisorySuites"] == [sid_groundedness]


def test_executor_exceptions_are_failures_not_skips(compiled) -> None:
    root, ir, cfg = compiled
    report, payload = run_evals(root, ir, ExplodingExecutor(), config=cfg, filters=Filters(suites={sid_routing}), runs_override=2, write_report=False, write_gates=False)
    s = _suite(payload, sid_routing)
    assert s["errors"] == s["executorErrors"] > 0 and s["verdict"] == "red"
    assert "AGENT_EVAL_FAILED" in {f.cls for f in report.warnings} and "EVAL_RED" in {f.cls for f in report.errors}
    assert all(row["error"] and row["error"].startswith("RuntimeError") for row in s["items"])


def test_unknown_grader_is_an_error_not_a_silent_green(compiled) -> None:
    root, ir, cfg = compiled
    for s in ir["evaluation"]["suites"]:
        if s["id"] == sid_groundedness:
            s["grader"] = "vibes"
    report, payload = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(suites={sid_groundedness}), write_report=False, write_gates=False)
    assert "AC_GRADER_UNKNOWN" in {f.cls for f in report.errors}
    assert payload["verdict"] == "red"


def test_grader_exception_is_an_execution_error_not_a_score(compiled) -> None:
    """Un grader qui ne peut pas noter (juge sans client, item mal formé) ÉCHOUE visiblement — jamais un 0 silencieux."""
    root, ir, cfg = compiled

    def judge_without_client(item, output, trace, measures):
        raise RuntimeError("[JUDGE_CLIENT_MISSING] aucun client de jugement injecté")

    report, payload = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(suites={sid_groundedness}),
                                graders={"llm-judge": judge_without_client}, write_report=False, write_gates=False)
    s = _suite(payload, sid_groundedness)
    assert s["executorErrors"] == s["errors"] > 0 and s["verdict"] == "red"
    assert "AGENT_EVAL_FAILED" in {f.cls for f in report.warnings}
    assert all("JUDGE_CLIENT_MISSING" in (row["error"] or "") for row in s["items"])
    assert "aucun score mesuré" in s["reason"]


def test_the_registry_is_the_only_source_of_graders(compiled) -> None:
    grader = eval_runner.resolve_grader("exact")
    result = eval_runner.normalize_grade(grader({"expected": {"intent": "billing"}}, {"intent": "billing"}, None, {}))
    assert result.score == 1.0
    assert eval_runner.resolve_grader("vibes") is None


def test_measured_cost_over_hard_cap_is_red(compiled) -> None:
    root, ir, cfg = compiled
    ir["budget"]["costPerRunHardCapUsd"] = 0.0005
    for s in ir["evaluation"]["suites"]:
        if s["id"] == sid_citations:
            s["level"] = "L7"
    report, payload = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(suites={sid_citations}), write_report=False, write_gates=False)
    assert "BUDGET_EXCEEDED_MEASURED" in {f.cls for f in report.errors}
    assert payload["verdict"] == "red" and _suite(payload, sid_citations)["verdict"] == "green"   # le score passe, le budget non


# ---------------------------------------------------------------------------
# Rapports, épinglage, filtres, CLI
# ---------------------------------------------------------------------------
def test_report_and_gate_reports_are_written_with_pins(compiled) -> None:
    root, ir, cfg = compiled
    report, payload = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(levels={"L4", "L8"}), graders=JUDGE_OK, run_id="RUN1")
    assert report.ok, report.render_text()
    rep = root / "workspace/.sys/reports/1-RUN1.json"
    assert rep.is_file() and not rep.with_name(rep.name + ".tmp").exists()
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert data["verdict"] == "green" and {s["suiteId"] for s in data["suites"]} == {sid_routing, sid_citations, sid_groundedness, "1-billing-specialist-injection", "1-intent-classifier-injection"}
    routing = _suite(data, sid_routing)
    assert routing["pins"]["promptHash"].startswith("sha256:") and routing["pins"]["modelId"] == "claude-haiku-4-5"
    assert routing["pins"]["datasetHash"].startswith("sha256:") and routing["stale"] is False
    assert set(routing) >= {"mean", "stddev", "passRate", "min", "max", "runs", "seeds", "pinDigest"}
    assert data["costByCap"]["1-1-ClassifyIntent"] > 0
    # G5 est écrite PAR CAP : c'est la granularité que `compute_status` lit
    # (`evaluate_all("G5", cap_ids)`). Une G5 par MISSION rendrait `Tested`
    # inatteignable sans que rien ne le signale.
    g5 = json.loads((paths.validation_dir(root) / "G5-1-1-ClassifyIntent.json").read_text(encoding="utf-8"))
    g7 = json.loads((paths.validation_dir(root) / "G7-1-SupportAssistant.suites.json").read_text(encoding="utf-8"))
    assert g5["ok"] is True and g5["artifact"] == "1-1-ClassifyIntent"
    assert "ir" in g5["pinnedHashes"] and "workspace/src/prompts/intent-classifier.system.md" in g5["pinnedHashes"]
    assert (paths.validation_dir(root) / "G5-1-2-ExplainInvoiceLine.json").is_file()
    assert g7["ok"] is True and payload["written"]["G5:1-1-ClassifyIntent"].endswith("G5-1-1-ClassifyIntent.json")


def test_second_report_same_run_id_does_not_overwrite(compiled) -> None:
    root, ir, cfg = compiled
    run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(suites={sid_routing}), run_id="R", write_gates=False)
    run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(suites={sid_routing}), run_id="R", write_gates=False)
    names = sorted(p.name for p in (root / "workspace/.sys/reports").glob("*.json"))
    assert names == ["1-R-2.json", "1-R.json"]


def test_edited_prompt_marks_result_stale_against_baseline(compiled) -> None:
    from sdda_scripts import promote_baseline

    root, ir, cfg = compiled
    run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(suites={sid_routing, sid_citations}), run_id="BASE", write_gates=False)
    assert run_main(promote_baseline.main, ["--root", str(root), "--mission", "1", "--run", "BASE", "--label", "première baseline"])[0] == 0
    prompt = root / "workspace/src/prompts/intent-classifier.system.md"
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\nRéponds toujours en majuscules.\n", encoding="utf-8")
    report, payload = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(suites={sid_routing, sid_citations}), write_report=False, write_gates=False)
    routing, citations = _suite(payload, sid_routing), _suite(payload, sid_citations)
    assert routing["stale"] is True and routing["staleDimensions"] == ["promptHash"]
    assert citations["stale"] is False                                  # l'autre agent n'a pas bougé
    assert payload["staleSuites"] == [sid_routing]
    stale = [f for f in report.warnings if f.cls == "EVAL_BASELINE_STALE"]
    assert len(stale) == 1 and "promptHash" in stale[0].message


def test_filters_by_level_cap_and_agent(compiled) -> None:
    root, ir, cfg = compiled
    _, p = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(levels={"L8"}), write_report=False, write_gates=False)
    assert {s["suiteId"] for s in p["suites"]} == {"1-billing-specialist-injection", "1-intent-classifier-injection"}
    _, p = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(caps={"1-1-ClassifyIntent"}), write_report=False, write_gates=False)
    assert {s["suiteId"] for s in p["suites"]} == {sid_routing}
    _, p = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(agents={"1-intent-classifier"}), write_report=False, write_gates=False)
    assert {s["suiteId"] for s in p["suites"]} == {sid_routing, "1-intent-classifier-injection"}
    report, _ = run_evals(root, ir, PerfectExecutor(), config=cfg, filters=Filters(suites={"nope"}), write_report=False, write_gates=False)
    assert "EVAL_SUITE_NOT_FOUND" in {f.cls for f in report.errors}


def test_cli_with_oracle_executor_and_filters(compiled) -> None:
    root, _, _ = compiled
    code, out = run_main(eval_runner.main, ["--root", str(root), "--mission", "1", "--executor", "sdda_scripts.eval_runner:OracleExecutor",
                                            "--level", "L4", "--suite", f"{sid_routing},{sid_citations}", "--run-id", "CLI", "--json"])
    assert code == 0, out
    data = json.loads(out)
    assert data["ok"] is True and data["data"]["verdict"] == "green" and data["data"]["suites"] == 2
    assert (root / "workspace/.sys/reports/1-CLI.json").is_file()
    code, out = run_main(eval_runner.main, ["--root", str(root), "--mission", "1", "--executor", "sdda_scripts.eval_runner:OracleExecutor", "--suite", sid_routing, "--no-report"])
    assert code == 0 and "🟢" in out and sid_routing in out


def test_cli_refuses_missing_or_broken_executor(compiled) -> None:
    root, _, _ = compiled
    code, out = run_main(eval_runner.main, ["--root", str(root), "--mission", "1", "--no-report"])
    assert code == 1 and "[EVAL_EXECUTOR_MISSING]" in out and "LLM" in out
    code, out = run_main(eval_runner.main, ["--root", str(root), "--mission", "1", "--executor", "no.such.module:Thing", "--no-report"])
    assert code == 1 and "[EVAL_EXECUTOR_MISSING]" in out


def test_cli_rejects_unknown_level_and_missing_ir(project: Path) -> None:
    code, out = run_main(eval_runner.main, ["--root", str(project), "--mission", "1", "--executor", "sdda_scripts.eval_runner:OracleExecutor", "--level", "L42", "--no-report"])
    assert code == 1 and "[EVAL_SUITE_NOT_FOUND]" in out
    code, out = run_main(eval_runner.main, ["--root", str(project), "--mission", "1", "--executor", "sdda_scripts.eval_runner:OracleExecutor", "--no-report"])
    assert code == 1 and "[IR_NOT_FOUND]" in out


def test_the_runner_keeps_no_grader_of_its_own() -> None:
    """Garde-fou de non-régression : deux implémentations d'un grader, c'est zéro.

    `eval_runner` en portait sept qui doublaient `sdda_lib/graders`, avec repli
    silencieux si l'import échouait : la mesure continuait, plus basse, sans que
    rien ne le dise. Les sémantiques des graders se testent dans
    `test_graders.py` — ici on ne teste que le câblage.
    """
    assert not hasattr(eval_runner, "INTERNAL_GRADERS")
    assert not [n for n in dir(eval_runner) if n.startswith("_grade_")]


def test_every_grader_of_the_closed_list_resolves_through_the_registry() -> None:
    from sdda_lib import graders as registry

    for name in registry.GRADERS:
        resolved = eval_runner.resolve_grader(name)
        # `llm-judge` est enregistré mais indisponible sans client : le runner
        # doit le voir comme absent, pas l'appeler item par item.
        assert (resolved is None) == (not registry.GRADERS[name].available), name


def test_a_registered_but_unusable_grader_is_treated_as_absent() -> None:
    assert eval_runner.resolve_grader("llm-judge") is None
    injected = eval_runner.resolve_grader("llm-judge", {"llm-judge": lambda i, o, t, m: 1.0})
    assert injected is not None                      # une surcharge explicite reste prioritaire


def test_an_ungradable_item_is_an_error_not_a_zero() -> None:
    """La différence qui motivait la déduplication : le grader interne rendait 0."""
    from sdda_lib.graders import GradingError

    grader = eval_runner.resolve_grader("numeric-tolerance")
    with pytest.raises(GradingError):
        grader({"expected": {"value": 10, "tolerance": 0.5}}, "pas un nombre", None, {})


def test_normalize_grade_accepts_the_shapes_a_grader_can_return() -> None:
    assert eval_runner.normalize_grade({"score": 0.5, "passed": True}).passed is True
    assert eval_runner.normalize_grade(True).score == 1.0
