#!/usr/bin/env python3
"""Moteur d'exécution des évaluations — k runs, variance, verdict trois couleurs.

Applique `rules/eval-protocol.md` §2-§3, §6 et §8, et les principes P3
(non-déterminisme comptabilisé), P9 (advisory) et P10 (épinglage).

Ce script **n'appelle aucun LLM**. Il orchestre : il reçoit un `Executor`
(injecté — le code généré du projet, un mock, un replay) qui produit la sortie
d'un item, fait noter cette sortie par un grader, répète k fois **sans aucun
cache**, agrège via `eval_stats`, épingle via `eval_pinning`, écrit :

    workspace/evals/reports/{n}-{RUN_ID}.json     le rapport complet
    workspace/.sys/.validation/G{x}-{mission}.json  un rapport de gate par gate touchée

Ce qu'il refuse par construction :
  - rapporter depuis un seul run sans le dire ([EVAL_SINGLE_RUN_FORBIDDEN]) ;
  - réutiliser la sortie d'un run pour le suivant (ce serait mesurer une fois
    et rapporter k fois) ;
  - aplatir « moyenne au-dessus du seuil » en vert quand `pass_rate < 1.0`
    ou que la variance dépasse `EvalVarianceWarnPct` — c'est le jaune ;
  - laisser une suite `advisory` bloquer, ou la cacher : son verdict réel
    reste dans le rapport, seul son pouvoir de bloquer disparaît.

Usage :
    python .sdda/sdda.py eval-runner --mission 1 --executor workspace.src.evals:Executor
    python .sdda/sdda.py eval-runner --mission 1 --level L4,L5 --cap 1-1-ClassifyIntent --json
    python .sdda/sdda.py eval-runner --mission 1 --suite 1-1-routing_accuracy --runs 5 --no-report

`--executor module:attr` désigne un objet exécuteur ou une fabrique sans
argument. `sdda_scripts.eval_runner:OracleExecutor` renvoie l'attendu de chaque
item : il ne sert qu'à vérifier la plomberie (tout grader doit lui donner 1.0).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.eval_pinning import Baseline, PinTuple, current_pins, load_baselines  # noqa: E402
from sdda_lib.eval_stats import (  # noqa: E402
    GLYPH,
    SEVERITY,
    ItemResult,
    RunResult,
    SuiteResult,
    Threshold,
    aggregate,
    parse_threshold,
    summarize,
)
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import LayeredConfig  # noqa: E402
from sdda_lib.runtime_io import atomic_write_json as _atomic_write_json, now_iso as _now_iso, run_id_now  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, load_config, resolve_root  # noqa: E402

LEVELS = tuple(f"L{i}" for i in range(10))

#: Niveau -> (gate rejouée, part). Les gates composites (`GATE_PARTS`) reçoivent
#: une part : G3 part `suites` (l'autre, `contracts`, vient de
#: `validate_tool_contract.py`), G7 part `suites` (l'autre, `adversarial`, vient
#: de `run_adversarial_suite.py`), G8 part `acceptance`. L5 et L7 partagent UN
#: rapport G6 : ils mesurent la même gate sous deux angles.
#:
#: **L3 est absent, et c'est délibéré.** G4 appartient à `run_retrieval_eval.py`,
#: l'enforcer nommé par INVARIANTS : deux écrivains sur la même gate produisent
#: deux vérités sur le même fait.
LEVEL_GATE: dict[str, tuple[str, str | None]] = {
    "L2": ("G3", "suites"),
    "L4": ("G5", None),
    "L5": ("G6", None),
    "L7": ("G6", None),
    "L8": ("G7", "suites"),
    "L9": ("G8", "acceptance"),
}

#: Gate -> granularité de l'artefact attendue par `compute_status` (LIFECYCLE) :
#: G3 par OUTIL, G5 par CAP, le reste par MISSION. Écrire un G5 par mission
#: laisserait `evaluate_all("G5", cap_ids)` ne trouver aucun rapport — et l'état
#: `Tested` serait inatteignable sans que rien ne le signale.
GATE_ARTIFACT: dict[str, str] = {"G3": "tool", "G5": "cap"}

#: Graders dont le score est une MESURE du run, pas un jugement sur la sortie.
MEASURE_GRADERS = ("cost", "latency")


# ---------------------------------------------------------------------------
# Exécuteur — injecté, jamais un LLM appelé ici
# ---------------------------------------------------------------------------
class Executor(Protocol):
    """Ce que le runner attend du système évalué.

    `run` reçoit UN item et rend un dict : `output` (ce que le système a
    produit), `cost_usd`, `latency_ms`, `trace` (appels d'outils, hops…).
    `seed` suit `EvalSeedPolicy` : il varie d'un run à l'autre par défaut,
    et l'exécuteur est libre de l'ignorer — le runner mesure ce qu'il obtient.
    """

    def run(self, item: dict[str, Any], *, suite: dict[str, Any], run_index: int, seed: int | None) -> dict[str, Any]: ...


class OracleExecutor:
    """Rend l'attendu de l'item. Vérifie la plomberie, jamais un système."""

    name = "oracle"

    def run(self, item: dict[str, Any], *, suite: dict[str, Any], run_index: int, seed: int | None) -> dict[str, Any]:
        expected = item.get("expected")
        trace: dict[str, Any] = {"calls": list((item.get("expected") or {}).get("trajectory") or [])} if isinstance(expected, dict) else {"calls": []}
        return {"output": expected, "cost_usd": 0.0, "latency_ms": 0.0, "trace": trace}


def load_executor(spec: str) -> Any:
    """`module:attr` -> exécuteur. L'attribut peut être une fabrique sans argument."""
    if ":" not in spec:
        raise ValueError(f"`{spec}` : attendu `module:attr`")
    mod_name, attr = spec.rsplit(":", 1)
    module = importlib.import_module(mod_name)
    obj = getattr(module, attr)
    if isinstance(obj, type) or (callable(obj) and not hasattr(obj, "run")):
        obj = obj()
    if not hasattr(obj, "run"):
        raise ValueError(f"`{spec}` ne fournit pas de méthode `run`")
    return obj


# ---------------------------------------------------------------------------
# Graders — registre externe si présent, repli interne minimal sinon
# ---------------------------------------------------------------------------
@dataclass
class Grade:
    score: float
    passed: bool | None = None
    detail: dict[str, Any] = field(default_factory=dict)


#: (item, output, trace, measures) -> Grade | float | dict
GraderFn = Callable[[dict[str, Any], Any, Any, dict[str, float]], Any]


def _calls_of(trace: Any) -> list[str]:
    """Noms des appels d'outils d'une trace, quel que soit son format."""
    if isinstance(trace, dict):
        trace = trace.get("calls") or trace.get("tool_calls") or []
    out: list[str] = []
    for call in trace or []:
        if isinstance(call, str):
            out.append(call)
        elif isinstance(call, dict):
            out.append(str(call.get("tool") or call.get("name") or ""))
    return out


def _grade_exact(item: dict[str, Any], output: Any, trace: Any, measures: dict[str, float]) -> Grade:
    expected = item.get("expected")
    return Grade(1.0 if output == expected else 0.0, detail={"expected": expected})


def _grade_regex(item: dict[str, Any], output: Any, trace: Any, measures: dict[str, float]) -> Grade:
    expected = item.get("expected")
    pattern = expected.get("pattern") if isinstance(expected, dict) else expected
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, sort_keys=True)
    ok = bool(pattern) and re.search(str(pattern), text) is not None
    return Grade(1.0 if ok else 0.0, detail={"pattern": pattern})


def _grade_numeric(item: dict[str, Any], output: Any, trace: Any, measures: dict[str, float]) -> Grade:
    expected = item.get("expected") or {}
    target = expected.get("value") if isinstance(expected, dict) else expected
    tol = float(expected.get("tolerance", 0.0)) if isinstance(expected, dict) else 0.0
    value = output.get("value") if isinstance(output, dict) else output
    try:
        ok = abs(float(value) - float(target)) <= tol
    except (TypeError, ValueError):
        ok = False
    return Grade(1.0 if ok else 0.0, detail={"target": target, "tolerance": tol})


def _grade_trajectory(item: dict[str, Any], output: Any, trace: Any, measures: dict[str, float]) -> Grade:
    """Séquence d'appels attendue (sous-séquence) et observables interdits absents."""
    calls = _calls_of(trace)
    expected = item.get("expected") or {}
    wanted = list(expected.get("trajectory") or []) if isinstance(expected, dict) else []
    forbidden = list((item.get("adversarial") or {}).get("forbidden_observables") or [])
    observed = " ".join(calls) + " " + (output if isinstance(output, str) else json.dumps(output, ensure_ascii=False))
    leaked = [f for f in forbidden if f and f in observed]
    cursor = 0
    for call in calls:
        if cursor < len(wanted) and call == wanted[cursor]:
            cursor += 1
    ordered = cursor == len(wanted)
    return Grade(1.0 if ordered and not leaked else 0.0, detail={"calls": calls, "leaked": leaked})


def _grade_cost(item: dict[str, Any], output: Any, trace: Any, measures: dict[str, float]) -> Grade:
    return Grade(measures.get("cost_usd", 0.0))


def _grade_latency(item: dict[str, Any], output: Any, trace: Any, measures: dict[str, float]) -> Grade:
    return Grade(measures.get("latency_ms", 0.0))


def _grade_schema(item: dict[str, Any], output: Any, trace: Any, measures: dict[str, float]) -> Grade:
    """Conformité minimale : clés `required` présentes. Le vrai `schema` vit dans `graders/`."""
    schema = (item.get("expected") or {}).get("schema") if isinstance(item.get("expected"), dict) else None
    if not isinstance(schema, dict):
        return Grade(1.0 if isinstance(output, dict) else 0.0)
    required = schema.get("required") or []
    ok = isinstance(output, dict) and all(k in output for k in required)
    return Grade(1.0 if ok else 0.0, detail={"required": required})


INTERNAL_GRADERS: dict[str, GraderFn] = {
    "exact": _grade_exact,
    "regex": _grade_regex,
    "numeric-tolerance": _grade_numeric,
    "trajectory": _grade_trajectory,
    "cost": _grade_cost,
    "latency": _grade_latency,
    "schema": _grade_schema,
}

try:  # le paquet `graders/` est écrit en parallèle : on l'utilise s'il existe
    from sdda_lib.graders import get as _external_grader  # type: ignore
except Exception:  # ImportError et tout défaut d'un paquet en cours d'écriture
    _external_grader = None


def resolve_grader(name: str, overrides: dict[str, GraderFn] | None = None, config: dict[str, Any] | None = None) -> GraderFn | None:
    """Priorité : surcharge explicite > registre `sdda_lib.graders` > repli interne.

    `config` est le `graderConfig` de la suite (mode de trajectoire, tolérance,
    client de juge…), transmis tel quel au grader externe.
    """
    if overrides and name in overrides:
        return overrides[name]
    if _external_grader is not None:
        try:
            external = _external_grader(name)
        except Exception:  # registre fermé : `[AC_GRADER_UNKNOWN]` levé par le paquet -> repli
            external = None
        if external is not None:
            return _adapt_external(external, config or {})
    return INTERNAL_GRADERS.get(name)


def _adapt_external(grader: Any, config: dict[str, Any]) -> GraderFn:
    """Accepte un objet `.grade(item, output, trace, measures, config)` ou un callable.

    Une `GradingError` (item impossible à noter) remonte telle quelle : le
    runner la compte en erreur d'exécution, jamais en score de 0.
    """
    target = grader.grade if hasattr(grader, "grade") else grader

    def _call(item: dict[str, Any], output: Any, trace: Any, measures: dict[str, float]) -> Any:
        try:
            return target(item, output, trace, measures, config=config)
        except TypeError as exc:
            if "config" not in str(exc) and "positional" not in str(exc):
                raise
            return target(item, output, trace, measures)

    return _call


def normalize_grade(raw: Any) -> Grade:
    if isinstance(raw, Grade):
        return raw
    if isinstance(raw, bool):
        return Grade(1.0 if raw else 0.0, raw)
    if isinstance(raw, (int, float)):
        return Grade(float(raw))
    if isinstance(raw, dict):
        return Grade(float(raw.get("score", 0.0)), raw.get("passed"), dict(raw.get("detail") or {}))
    if hasattr(raw, "score"):
        return Grade(float(raw.score), getattr(raw, "passed", None), dict(getattr(raw, "detail", {}) or {}))
    raise TypeError(f"résultat de grader inexploitable : {type(raw).__name__}")


# ---------------------------------------------------------------------------
# Datasets et criticité
# ---------------------------------------------------------------------------
def load_items(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for lineno, raw in enumerate(markdown_io.read_text(path).split("\n"), start=1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict):
            item.setdefault("id", f"ligne-{lineno}")
            items.append(item)
    return items


def item_class(item: dict[str, Any]) -> str | None:
    meta = item.get("metadata") or {}
    cls = meta.get("class") or item.get("expected_class")
    if cls is None and isinstance(item.get("expected"), dict):
        cls = item["expected"].get("intent") or item["expected"].get("class")
    return str(cls) if cls not in (None, "") else None


def item_is_critical(item: dict[str, Any]) -> bool:
    return str((item.get("metadata") or {}).get("criticality", "")).lower() == "critical"


def cap_criticality(root: Path, cap_id: str | None) -> str:
    """`Criticality:` de la CAP sur disque ; `normal` si absente."""
    if not cap_id:
        return "normal"
    p = paths.caps_dir(root) / f"{cap_id}.md"
    if not p.is_file():
        return "normal"
    return markdown_io.parse_header_fields(markdown_io.read_text(p)).get("Criticality", "normal").strip().lower()


# ---------------------------------------------------------------------------
# Plan d'exécution d'une suite
# ---------------------------------------------------------------------------
@dataclass
class SuitePlan:
    suite: dict[str, Any]
    runs: int
    criticality: str
    threshold: Threshold
    seeds: list[int | None]

    @property
    def id(self) -> str:
        return str(self.suite.get("id"))


@dataclass
class Filters:
    suites: set[str] = field(default_factory=set)
    levels: set[str] = field(default_factory=set)
    caps: set[str] = field(default_factory=set)
    agents: set[str] = field(default_factory=set)

    def accepts(self, suite: dict[str, Any]) -> bool:
        if self.suites and str(suite.get("id")) not in self.suites:
            return False
        if self.levels and str(suite.get("level")) not in self.levels:
            return False
        if self.caps and str(suite.get("capRef")) not in self.caps:
            return False
        if self.agents and str(suite.get("agentRef")) not in self.agents:
            return False
        return True

    def to_dict(self) -> dict[str, list[str]]:
        return {"suites": sorted(self.suites), "levels": sorted(self.levels), "caps": sorted(self.caps), "agents": sorted(self.agents)}


def plan_suite(
    root: Path,
    suite: dict[str, Any],
    config: LayeredConfig | None,
    *,
    runs_override: int | None = None,
    base_seed: int | None = None,
) -> SuitePlan:
    """k, seuil, seeds. `runs` de la suite ; sinon EvalRuns / EvalRunsCritical."""
    criticality = cap_criticality(root, suite.get("capRef"))
    if suite.get("level") == "L8":
        criticality = "critical"  # une injection réussie n'est jamais « normale »
    default_runs = (config.get_int("EvalRunsCritical", 5) if config else 5) if criticality == "critical" else (config.get_int("EvalRuns", 3) if config else 3)
    declared = suite.get("runs")
    runs = runs_override if runs_override is not None else (declared if isinstance(declared, int) and declared >= 1 else default_runs)
    policy = str(config.get("EvalSeedPolicy", "vary") if config else "vary").strip().lower()
    seed0 = base_seed if base_seed is not None else int(time.time()) % 1_000_000
    seeds: list[int | None] = [seed0] * runs if policy == "fixed" else [seed0 + i for i in range(runs)]
    return SuitePlan(suite=suite, runs=runs, criticality=criticality, threshold=parse_threshold(suite.get("threshold", 0.0)), seeds=seeds)


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------
@dataclass
class ExecutedSuite:
    plan: SuitePlan
    result: SuiteResult
    pins: PinTuple
    stale_dimensions: dict[str, tuple[str, str]] = field(default_factory=dict)
    per_class_all: dict[str, float] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    executor_errors: int = 0

    @property
    def stale(self) -> bool:
        return bool(self.stale_dimensions)

    def to_dict(self) -> dict[str, Any]:
        d = self.result.to_dict()
        s = self.plan.suite
        d.update({
            "level": s.get("level"),
            "capRef": s.get("capRef"),
            "agentRef": s.get("agentRef"),
            "grader": s.get("grader"),
            "dataset": s.get("dataset"),
            "criticality": self.plan.criticality,
            "seeds": list(self.plan.seeds),
            "pins": self.pins.to_dict(),
            "pinDigest": self.pins.digest(),
            "stale": self.stale,
            "staleDimensions": sorted(self.stale_dimensions),
            "perClassAll": {k: round(v, 6) for k, v in sorted(self.per_class_all.items())},
            "executorErrors": self.executor_errors,
            "items": self.items,
        })
        return d


def execute_suite(
    root: Path,
    plan: SuitePlan,
    executor: Any,
    *,
    config: LayeredConfig | None,
    graders: dict[str, GraderFn] | None = None,
    report: Report | None = None,
    item_limit: int | None = None,
) -> tuple[SuiteResult, dict[str, float], list[dict[str, Any]], int]:
    """k passages complets du dataset. Aucun cache : chaque run rappelle l'exécuteur.

    Rend (résultat agrégé, moyennes par classe — toutes —, détail des items,
    nombre d'erreurs d'exécution).
    """
    suite = plan.suite
    sid = plan.id
    dataset = paths.resolve_rel(root, str(suite.get("dataset", "")))
    variance_warn = config.get_float("EvalVarianceWarnPct", 15.0) if config else 15.0
    result = SuiteResult(
        suite_id=sid,
        metric=str(suite.get("metric") or _metric_of(sid)),
        threshold=plan.threshold,
        variance_warn_pct=variance_warn,
        advisory=bool(suite.get("advisory")),
        per_class_threshold=parse_threshold(suite["perClassThreshold"]) if suite.get("perClassThreshold") else None,
    )
    if not dataset.is_file():
        if report is not None:
            report.error("EVAL_DATASET_MISSING", f"suite `{sid}` : dataset `{suite.get('dataset')}` absent sur disque",
                         "produire le jeu (qa-evals) ou corriger la référence de l'AC", sid)
        result.notes.append("dataset absent : rien mesuré")
        return result, {}, [], 0

    items = load_items(dataset)
    if item_limit is not None:
        items = items[:item_limit]
    grader_name = str(suite.get("grader", ""))
    grader = resolve_grader(grader_name, graders, suite.get("graderConfig") if isinstance(suite.get("graderConfig"), dict) else None)
    if grader is None:
        if report is not None:
            report.error("AC_GRADER_UNKNOWN", f"suite `{sid}` : grader `{grader_name}` indisponible",
                         "installer sdda_lib/graders ou corriger le grader de l'AC (liste close d'eval-protocol.md §4)", sid)
        result.notes.append(f"grader `{grader_name}` indisponible : rien mesuré")
        return result, {}, [], 0

    critical_cap = plan.criticality == "critical"
    class_scores: dict[str, list[float]] = {}
    critical_classes: set[str] = set()
    detail_rows: list[dict[str, Any]] = []
    exec_errors = 0

    for run_index, seed in enumerate(plan.seeds):
        run = RunResult(run_index=run_index)
        for item in items:
            item_id = str(item.get("id"))
            # Pas de mémo : l'exécuteur est rappelé à chaque run, seed distinct.
            try:
                produced = executor.run(item, suite=suite, run_index=run_index, seed=seed) or {}
                measures = {"cost_usd": float(produced.get("cost_usd", 0.0) or 0.0), "latency_ms": float(produced.get("latency_ms", 0.0) or 0.0)}
                grade = normalize_grade(grader(item, produced.get("output"), produced.get("trace"), measures))
                passed = grade.passed if grade.passed is not None else plan.threshold.holds(grade.score)
                ir_item = ItemResult(item_id, run_index, grade.score, bool(passed), grade.detail, None, measures["cost_usd"], measures["latency_ms"])
            except Exception as exc:  # une exception de l'exécuteur ou du grader est une erreur d'item, pas un score
                exec_errors += 1
                ir_item = ItemResult(item_id, run_index, 0.0, False, {}, f"{type(exc).__name__}: {exc}")
            run.items.append(ir_item)
            cls = item_class(item)
            if cls is not None and ir_item.error is None:
                class_scores.setdefault(cls, []).append(ir_item.score)
                if critical_cap or item_is_critical(item):
                    critical_classes.add(cls)
            detail_rows.append({
                "itemId": item_id, "run": run_index, "class": cls, "score": round(ir_item.score, 6), "passed": ir_item.passed,
                "costUsd": round(ir_item.cost_usd, 6), "latencyMs": round(ir_item.latency_ms, 2), "error": ir_item.error,
            })
        result.runs.append(run)

    per_class_all = {cls: sum(v) / len(v) for cls, v in class_scores.items() if v}
    # Seules les classes CRITIQUES entrent dans le verdict : une classe
    # critique sous son seuil rend rouge même si la moyenne globale passe.
    result.per_class = {cls: per_class_all[cls] for cls in sorted(critical_classes) if cls in per_class_all}
    if exec_errors and report is not None:
        report.warn("AGENT_EVAL_FAILED", f"suite `{sid}` : {exec_errors} item-run(s) en erreur d'exécution (comptés comme échecs, jamais ignorés)", "", sid)
    return result, per_class_all, detail_rows, exec_errors


def _metric_of(suite_id: str) -> str:
    """`1-2-groundedness` -> `groundedness` ; `x-injection` -> `injection`."""
    return suite_id.rsplit("-", 1)[-1] if "-" in suite_id else suite_id


# ---------------------------------------------------------------------------
# Orchestration complète
# ---------------------------------------------------------------------------
def reports_dir(root: Path) -> Path:
    return paths.evals_dir(root) / "reports"


def unique_report_path(root: Path, number: int, run_id: str) -> Path:
    base = reports_dir(root) / f"{number}-{run_id}.json"
    candidate, k = base, 2
    while candidate.exists():
        candidate = base.with_name(f"{number}-{run_id}-{k}.json")
        k += 1
    return candidate


def gate_pins(root: Path, ir: dict[str, Any], executed: list[ExecutedSuite]) -> dict[str, str]:
    """Hashes lisibles par `compute_status.stale_keys` : IR, prompts, datasets (fichiers)."""
    pins: dict[str, str] = {"ir": ir_compiler.ir_identity_hash(ir)}
    agents = {a["id"]: a for a in ir.get("agents") or []}
    for ex in executed:
        s = ex.plan.suite
        ds = str(s.get("dataset", ""))
        p = paths.resolve_rel(root, ds)
        if ds and p.is_file():
            pins[ds] = hashing.sha256_file(p)
        agent = agents.get(str(s.get("agentRef", "")))
        ref = str((agent or {}).get("promptRef") or "")
        pp = paths.resolve_rel(root, ref) if ref else None
        if pp is not None and pp.is_file():
            pins[ref] = hashing.sha256_file(pp)
        pins[f"suite:{ex.plan.id}"] = ex.pins.digest()
    return pins


def run_evals(
    root: Path,
    ir: dict[str, Any],
    executor: Any,
    *,
    config: LayeredConfig | None = None,
    filters: Filters | None = None,
    runs_override: int | None = None,
    base_seed: int | None = None,
    graders: dict[str, GraderFn] | None = None,
    baseline_path: Path | None = None,
    run_id: str | None = None,
    write_report: bool = True,
    write_gates: bool = True,
    item_limit: int | None = None,
) -> tuple[Report, dict[str, Any]]:
    """Exécute les suites retenues et rend (findings, rapport JSON)."""
    mid = str(ir.get("missionId", ""))
    number = int(mid.split("-", 1)[0]) if mid.split("-", 1)[0].isdigit() else 0
    report = Report(name="EVAL", target=mid or str(root))
    filters = filters or Filters()
    suites = [s for s in ((ir.get("evaluation") or {}).get("suites") or []) if isinstance(s, dict)]
    selected = [s for s in suites if filters.accepts(s)]
    if not selected:
        report.error("EVAL_SUITE_NOT_FOUND", f"aucune suite de `{mid}` ne correspond aux filtres {filters.to_dict()}",
                     "vérifier --suite / --level / --cap / --agent contre `evaluation.suites` de l'IR", mid)

    bpath = baseline_path or paths.resolve_rel(root, str((ir.get("evaluation") or {}).get("baselineRef") or f"workspace/evals/baselines/{number}-system.json"))
    baselines: dict[str, Baseline] = load_baselines(bpath)
    policy = str(config.get("EvalSeedPolicy", "vary") if config else "vary").strip().lower()
    if policy == "fixed":
        report.warn("EVAL_SINGLE_RUN_FORBIDDEN", "EvalSeedPolicy: fixed — un seed fixe masque la variance ; ce rapport ne vaut pas un verdict", "", mid)

    hard_cap = (ir.get("budget") or {}).get("costPerRunHardCapUsd")
    executed: list[ExecutedSuite] = []
    for suite in selected:
        plan = plan_suite(root, suite, config, runs_override=runs_override, base_seed=base_seed)
        sid = plan.id
        if plan.runs < 1:
            report.error("EVAL_SINGLE_RUN_FORBIDDEN", f"suite `{sid}` : k={plan.runs}", "k runs, toujours (P3) : EvalRuns >= 1", sid)
            continue
        if plan.runs == 1:
            in_ci = os.environ.get("CI", "").strip().lower() in ("1", "true", "yes", "on")
            (report.error if in_ci else report.warn)(
                "EVAL_SINGLE_RUN_FORBIDDEN", f"suite `{sid}` : k=1 — un run n'est pas une mesure, la variance est inconnue",
                "EvalRuns >= 3 (5 si critique) ; k=1 n'est toléré qu'en dev local", sid)
        result, per_class_all, rows, exec_errors = execute_suite(root, plan, executor, config=config, graders=graders, report=report, item_limit=item_limit)
        pins = current_pins(root, ir, suite=suite)
        ex = ExecutedSuite(plan=plan, result=result, pins=pins, per_class_all=per_class_all, items=rows, executor_errors=exec_errors)
        base = baselines.get(sid)
        if base is not None:
            ex.stale_dimensions = base.pins.diff(pins)
            if ex.stale_dimensions:
                result.notes.append(f"baseline périmée : {', '.join(sorted(ex.stale_dimensions))} a bougé")
                report.warn("EVAL_BASELINE_STALE", f"suite `{sid}` : la baseline mesurait autre chose ({', '.join(sorted(ex.stale_dimensions))} a bougé) — ce résultat n'est pas comparable",
                            "promote_baseline.py après lecture du résultat, sinon aucune régression n'est détectable", sid)
        if isinstance(hard_cap, (int, float)) and rows and str(suite.get("level")) in ("L6", "L7"):
            worst = max((r["costUsd"] for r in rows if r["error"] is None), default=0.0)
            if worst > float(hard_cap) + 1e-9:
                result.notes.append(f"coût max {worst:.4f} USD > costPerRunHardCapUsd {hard_cap}")
                report.error("BUDGET_EXCEEDED_MEASURED", f"suite `{sid}` : un item a coûté {worst:.4f} USD > plafond {hard_cap} USD",
                             "un budget dépassé est rouge, pas jaune (P6) : réduire la trajectoire ou revoir le plafond de la MISSION", sid)
        executed.append(ex)
        _emit_verdict_findings(report, ex)

    results = [ex.result for ex in executed]
    verdict = aggregate(results)
    if report.errors and SEVERITY[verdict] < SEVERITY["red"]:
        verdict = "red"  # une erreur d'exécution n'est pas un score : elle bloque
    summary = summarize(results)
    summary["verdict"] = verdict

    rid = run_id or run_id_now()
    payload: dict[str, Any] = {
        "missionId": mid,
        "runId": rid,
        "generatedAt": _now_iso(),
        "executor": getattr(executor, "name", type(executor).__name__),
        "config": {
            "evalRuns": config.get_int("EvalRuns", 3) if config else 3,
            "evalRunsCritical": config.get_int("EvalRunsCritical", 5) if config else 5,
            "evalVarianceWarnPct": config.get_float("EvalVarianceWarnPct", 15.0) if config else 15.0,
            "evalSeedPolicy": policy,
            "runsOverride": runs_override,
        },
        "filters": filters.to_dict(),
        "baselineRef": paths.rel(root, bpath),
        "verdict": verdict,
        "summary": {k: v for k, v in summary.items() if k != "results"},
        "costByCap": _cost_by_cap(executed),
        "staleSuites": sorted(ex.plan.id for ex in executed if ex.stale),
        "suites": [ex.to_dict() for ex in executed],
        "findings": {"errors": [f.to_dict() for f in report.errors], "warnings": [f.to_dict() for f in report.warnings]},
    }

    written: dict[str, str] = {}
    if write_report and executed:
        out = unique_report_path(root, number, rid)
        out.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(out, payload)
        written["report"] = paths.rel(root, out)
    if write_gates and executed and mid:
        pins_all = gate_pins(root, ir, executed)
        by_gate: dict[tuple[str, str | None, str], list[ExecutedSuite]] = {}
        for ex in executed:
            key = LEVEL_GATE.get(str(ex.plan.suite.get("level")))
            if key:
                by_gate.setdefault((key[0], key[1], gate_artifact(ir, ex.plan.suite, key[0], mid)), []).append(ex)
        for (gate, part, artifact), members in sorted(by_gate.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2])):
            ids = {m.plan.id for m in members}
            sub = Report(name=f"{gate}.eval", target=artifact, data={"runId": rid, "suites": sorted(ids), "verdict": aggregate([m.result for m in members])})
            for f in report.findings:
                if f.location in ids:
                    sub.findings.append(f)
            p = write_gate_report(root, gate, artifact, sub, pins_all, part=part)
            written[f"{gate}{'.' + part if part else ''}:{artifact}"] = paths.rel(root, p)
    payload["written"] = written
    report.data.update({"verdict": verdict, "runId": rid, "suites": len(executed), "written": written, "summary": payload["summary"], "lines": [ex.result.render_line() for ex in executed]})
    return report, payload


def gate_artifact(ir: dict[str, Any], suite: dict[str, Any], gate: str, mission_id: str) -> str:
    """L'artefact sous lequel ce rapport de gate doit être écrit.

    `compute_status` cherche `G3-{outil}`, `G5-{cap}`, et la MISSION pour le
    reste. Un rapport écrit sous la mauvaise clé n'est pas lu — il est vert dans
    un fichier que personne n'ouvre.
    """
    kind = GATE_ARTIFACT.get(gate)
    if kind == "cap":
        return str(suite.get("capRef") or mission_id)
    if kind == "tool":
        haystack = f"{suite.get('id', '')} {suite.get('dataset', '')}"
        for tool in ir.get("tools") or []:
            tid = str(tool.get("id"))
            if tid and tid in haystack:
                return tid
    return mission_id


def _emit_verdict_findings(report: Report, ex: ExecutedSuite) -> None:
    """Le verdict d'une suite devient un finding : rouge bloquant, jaune WARN."""
    r, sid = ex.result, ex.plan.id
    tag = f"{GLYPH[r.verdict]} {r.metric} mean {r.mean:.3f} ±{r.stddev:.3f} pass_rate {r.pass_rate:.2f} k={len(r.runs)}"
    if r.advisory:
        if r.verdict == "red":
            report.warn("EVAL_ADVISORY_RED", f"suite `{sid}` (advisory) : {tag} — {r.reason} ; informe, ne bloque pas (P9)", "", sid)
        return
    if r.verdict == "red":
        crit = " (critical)" if ex.plan.criticality == "critical" else ""
        report.error("EVAL_RED", f"suite `{sid}`{crit} : {tag} — {r.reason}",
                     "corriger la couche fautive (eval-protocol.md §10 situe l'étage) puis relancer", sid)
    elif r.verdict == "yellow":
        report.warn("EVAL_YELLOW", f"suite `{sid}` : {tag} — {r.reason} ; livrer maintenant est un pari sur le prochain tirage", "", sid)


def _cost_by_cap(executed: list[ExecutedSuite]) -> dict[str, float]:
    out: dict[str, float] = {}
    for ex in executed:
        cap = str(ex.plan.suite.get("capRef") or ex.plan.suite.get("agentRef") or ex.plan.id)
        out[cap] = round(out.get(cap, 0.0) + ex.result.cost_usd, 6)
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_levels(raw: str | None) -> set[str]:
    if not raw:
        return set()
    out = set()
    for tok in raw.replace(",", " ").split():
        tok = tok.strip().upper()
        if tok not in LEVELS:
            raise ValueError(f"niveau inconnu `{tok}` (attendu L0..L9)")
        out.add(tok)
    return out


def _split(raw: list[str] | None) -> set[str]:
    out: set[str] = set()
    for chunk in raw or []:
        out.update(t.strip() for t in chunk.split(",") if t.strip())
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Exécution des évaluations : k runs, variance, verdict trois couleurs (0 appel LLM dans ce script)")
    p.add_argument("--mission", type=int, default=None, help="numéro de mission ; défaut : l'unique IR compilé")
    p.add_argument("--ir", type=Path, default=None, help="fichier IR explicite")
    p.add_argument("--executor", default=None, help="`module:attr` — l'exécuteur du système évalué (objet ou fabrique sans argument)")
    p.add_argument("--suite", action="append", default=None, help="identifiant(s) de suite, répétable ou séparés par des virgules")
    p.add_argument("--level", "--levels", dest="level", default=None, help="niveaux L0..L9, séparés par des virgules")
    p.add_argument("--cap", action="append", default=None, help="CAP(s) à évaluer")
    p.add_argument("--agent", action="append", default=None, help="agent(s) à évaluer")
    p.add_argument("--runs", type=int, default=None, help="forcer k (sinon `runs` de la suite, puis EvalRuns/EvalRunsCritical)")
    p.add_argument("--seed", type=int, default=None, help="seed de base (EvalSeedPolicy: vary le fait varier par run)")
    p.add_argument("--baseline", type=Path, default=None, help="baseline à confronter (défaut : evaluation.baselineRef)")
    p.add_argument("--run-id", default=None, help="identifiant du run (défaut : horodatage UTC)")
    p.add_argument("--limit", type=int, default=None, help="ne prendre que les N premiers items (débogage)")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None, *, executor: Any = None, graders: dict[str, GraderFn] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="EVAL", target=str(root))
    config = load_config(root, report)

    try:
        filters = Filters(suites=_split(args.suite), levels=parse_levels(args.level), caps=_split(args.cap), agents=_split(args.agent))
    except ValueError as exc:
        report.error("EVAL_SUITE_NOT_FOUND", str(exc), "--level accepte L0..L9", "--level")
        return finish(report, args)

    if executor is None:
        if not args.executor:
            report.error("EVAL_EXECUTOR_MISSING", "aucun exécuteur : ce script n'appelle aucun LLM lui-même",
                         "--executor module:attr (le code généré expose l'exécuteur ; sdda_scripts.eval_runner:OracleExecutor vérifie la plomberie)", str(root))
            return finish(report, args)
        try:
            executor = load_executor(args.executor)
        except Exception as exc:
            report.error("EVAL_EXECUTOR_MISSING", f"`{args.executor}` inutilisable : {type(exc).__name__}: {exc}", "corriger le chemin `module:attr` ; le module doit être importable depuis le cwd", args.executor)
            return finish(report, args)

    if args.ir:
        ir_file = args.ir if args.ir.is_absolute() else root / args.ir
    elif args.mission is not None:
        ir_file = paths.ir_path(root, args.mission)
    else:
        candidates = sorted(paths.ir_dir(root).glob("*-system.ir.json"))
        if len(candidates) != 1:
            report.error("IR_NOT_FOUND", f"{len(candidates)} IR compilé(s) dans workspace/.sys/.ir/ : préciser --mission", "python .sdda/sdda.py ir-compiler --mission {n}", str(paths.ir_dir(root)))
            return finish(report, args)
        ir_file = candidates[0]
    if not ir_file.is_file():
        report.error("IR_NOT_FOUND", f"IR `{paths.rel(root, ir_file)}` introuvable", "compiler : python .sdda/sdda.py ir-compiler --mission {n}", paths.rel(root, ir_file))
        return finish(report, args)
    ir = ir_compiler.load_ir(ir_file)

    sub, _payload = run_evals(
        root, ir, executor, config=config, filters=filters, runs_override=args.runs, base_seed=args.seed, graders=graders,
        baseline_path=(args.baseline if args.baseline is None or args.baseline.is_absolute() else root / args.baseline),
        run_id=args.run_id, write_report=not args.no_report, write_gates=not args.no_report, item_limit=args.limit,
    )
    report.extend(sub)
    report.data.update(sub.data)
    if not args.json:
        for line in sub.data.get("lines", []):
            print(line)
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
