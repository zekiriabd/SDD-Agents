#!/usr/bin/env python3
"""Moteur d'exécution des évaluations — k runs, variance, verdict trois couleurs.

Applique `rules/eval-protocol.md` §2-§3, §6 et §8, et les principes P3
(non-déterminisme comptabilisé), P9 (advisory) et P10 (épinglage).

Ce script **n'appelle aucun LLM**. Il orchestre : il reçoit un `Executor`
(injecté — le code généré du projet, un mock, un replay) qui produit la sortie
d'un item, fait noter cette sortie par un grader, répète k fois **sans aucun
cache**, agrège via `eval_stats`, épingle via `eval_pinning`, écrit :

    workspace/.sys/reports/{n}-{RUN_ID}.json     le rapport complet
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
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import graders as graders_registry  # noqa: E402 — LE registre, plus de repli interne
from sdda_lib import executors, hashing, markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report, SddaError  # noqa: E402
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
from sdda_scripts._run_traces import percentile  # noqa: E402 — le même p95 que cost_report : rang le plus proche

LEVELS = tuple(f"L{i}" for i in range(10))

#: `error_class` qu'un exécuteur rend quand le FOURNISSEUR du modèle n'a pas
#: répondu (`models.provider_error_class` du runtime généré). Un tel run n'a rien
#: mesuré de l'agent : erreur d'exécution, et `[INFRA_BLOCKED]` au rapport.
PROVIDER_ERROR_CLASSES = frozenset({"LLM_PROVIDER_AUTH_FAILED", "LLM_PROVIDER_UNAVAILABLE"})

#: Niveau -> (gate rejouée, part). Les gates composites (`GATE_PARTS`) reçoivent
#: une part : G3 part `suites` (l'autre, `contracts`, vient de
#: `validate_tool_contract.py`), G7 part `suites` (l'autre, `adversarial`, vient
#: de `run_adversarial_suite.py`), G8 part `acceptance`. L5 et L7 partagent UN
#: rapport G6 : ils mesurent la même gate sous deux angles.
#:
#: **L3 est absent, et c'est délibéré.** G4 appartient à `run_retrieval_eval.py`,
#: l'enforcer nommé par INVARIANTS : deux écrivains sur la même gate produisent
#: deux vérités sur le même fait.
#:
#: **L2 est absent aussi**, pour la même raison : la part `suites` de G3
#: appartient à `run_tool_suites.py`, qui joue les tests pytest de `qa-tests`.
#: Une suite d'outil déclare des CAS inline, pas un dataset d'items.
LEVEL_GATE: dict[str, tuple[str, str | None]] = {
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

#: Niveaux qui font tourner le système ENTIER : le plafond de coût et la cible
#: de latence de la MISSION s'y appliquent. L9 en fait partie : l'acceptation
#: sur holdout est un run de production, pas une mesure de qualité seule.
SYSTEM_LEVELS = frozenset({"L5", "L6", "L7", "L9"})

#: k minimal pour qu'un rapport de GATE soit écrit. Un run unique mesure un
#: tirage, pas une distribution (eval-protocol.md §2) : il reste rapporté,
#: jamais promu au rang de verdict de gate.
GATE_MIN_RUNS = 2

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


def load_executor(spec: str, root: Path | None = None) -> Any:
    """`module:attr` -> exécuteur (`sdda_lib.executors`, commun aux trois runners)."""
    return executors.load_executor(spec, method="run", root=root)


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


def resolve_grader(name: str, overrides: dict[str, GraderFn] | None = None, config: dict[str, Any] | None = None) -> GraderFn | None:
    """Priorité : surcharge explicite > registre `sdda_lib.graders`. Pas de repli.

    Ce runner portait naguère sept graders « internes » qui doublaient ceux du
    paquet, et s'y repliait en silence si l'import échouait. Deux implémentations
    de `trajectory` notaient donc la même trace différemment — la version interne
    ignorait les spans OTel, comptait une trace illisible comme un **zéro** au
    lieu d'une erreur, et faisait ainsi baisser une moyenne pour une raison
    étrangère à la qualité du système évalué. Un repli invisible sur un grader
    plus faible est pire qu'une panne : la mesure continue, plus basse, et rien
    ne le dit.

    Le paquet est maintenant une dépendance dure. Un grader hors liste close, ou
    enregistré mais inutilisable (`available` faux — un juge LLM sans client),
    rend `None` : l'appelant émet `[AC_GRADER_UNKNOWN]` et ne mesure rien, ce qui
    est le seul résultat honnête.

    `config` est le `graderConfig` de la suite (mode de trajectoire, tolérance,
    client de juge…), transmis tel quel au grader.
    """
    if overrides and name in overrides:
        return overrides[name]
    try:
        grader = graders_registry.get(name)
    except SddaError:            # [AC_GRADER_UNKNOWN] — la liste est close
        return None
    # Un juge sans client propre devient disponible quand la configuration lui
    # en apporte un (le client réel construit par `judge_clients`, ou un faux).
    if not getattr(grader, "available", True) and (config or {}).get("client") is None:
        return None
    return _adapt(grader, config or {})


def _evaluated_models(root: Path, ir: dict[str, Any] | None, suite: dict[str, Any], config: LayeredConfig | None) -> list[str]:
    """Le(s) modèle(s) qu'une suite évalue : le tier de son agent, résolu par la `RuntimeTierMap`.

    Sans `agentRef` (suite de système), tous les modèles des agents de l'IR :
    le juge ne doit être AUCUN d'eux (`JudgeMustDifferFromEvaluated`).
    """
    from sdda_lib.layered_config import read_runtime_tier_map  # noqa: PLC0415

    try:
        tier_map = read_runtime_tier_map(root)
    except Exception:  # noqa: BLE001 — une section illisible ne doit pas empêcher de mesurer
        tier_map = {}
    default_tier = str(config.get("DefaultTier", "balanced") if config else "balanced")
    agents = [a for a in ((ir or {}).get("agents") or []) if isinstance(a, dict)]
    ref = str(suite.get("agentRef") or "")
    chosen = [a for a in agents if a.get("id") == ref] if ref else agents
    # L'IR porte `modelTier` (ir_compiler) : lire `tier` seul voyait chaque
    # agent en `balanced`, et le juge pouvait être le modèle d'un agent `fast`.
    tiers = (str(a.get("modelTier") or a.get("tier") or default_tier) for a in chosen)
    return sorted({tier_map[t] for t in tiers if tier_map.get(t)})


class JudgeTrace:
    """Les spans des appels de juge d'un run d'eval — un fichier de trace à part, complet.

    Le juge est un appel LLM facturé : il laisse une trace comme le produit,
    au même format, sous `{run_id}-judge.jsonl`, avec son propre span racine
    — un fichier sans racine serait signalé « run sans début ni fin » par
    `tracing.summarize`. Il n'est PAS mêlé à la trace du produit : son coût
    est un coût d'évaluation, et l'additionner à celui du système évalué
    ferait dépasser un plafond que le produit respecte.
    """

    def __init__(self, root: Path, run_id: str) -> None:
        import threading  # noqa: PLC0415

        self.root = root
        self.run_id = f"{run_id}-judge"
        self.root_span_id = f"{run_id}-judge-root"
        self.started = _now_iso()
        self._t0 = time.monotonic()
        self._lock = threading.Lock()
        self._writer: Any = None
        self._seq = 0

    def __call__(self, name: str, attrs: dict[str, Any], status: str, duration_ms: float) -> None:
        from sdda_lib import tracing  # noqa: PLC0415

        with self._lock:
            if self._writer is None:
                self._writer = tracing.TraceWriter(self.root, self.run_id)
            self._seq += 1
            span_id = f"{self.run_id}-{self._seq}"
        self._writer.emit(name, span_id=span_id, parent_span_id=self.root_span_id, attributes=attrs,
                          status=status, duration_ms=round(duration_ms))

    @property
    def path(self) -> Path | None:
        return self._writer.path if self._writer is not None else None

    def close(self) -> None:
        from sdda_lib import tracing  # noqa: PLC0415

        if self._writer is None:
            return
        self._writer.emit(f"{tracing.RUN_SPAN} eval-judge", span_id=self.root_span_id,
                          attributes={"sdda.run.kind": "eval-judge"}, start=self.started, end=_now_iso(),
                          duration_ms=round((time.monotonic() - self._t0) * 1000))


def _adapt(grader: Any, config: dict[str, Any]) -> GraderFn:
    """`Grader.grade(item, output, trace, measures, config)` vu comme le callable du runner.

    Une `GradingError` (item impossible à noter) remonte telle quelle : la
    boucle d'exécution la compte en erreur d'item, jamais en score de 0.
    """
    def _call(item: dict[str, Any], output: Any, trace: Any, measures: dict[str, float]) -> Any:
        return grader.grade(item, output, trace, measures, config=config)

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
    return load_items_counted(path)[0]


def load_items_counted(path: Path) -> tuple[list[dict[str, Any]], list[int]]:
    """(items, numéros des lignes rejetées). Une ligne illisible n'est pas un item.

    Les rejets étaient ignorés en silence : un jeu dont toutes les lignes étaient
    invalides devenait un jeu VIDE, et une suite sans item sous un seuil `<=`
    (latence, coût) rendait un vert. Les compter est la moitié du correctif ;
    `execute_suite` refuse l'autre moitié, le jeu vide.
    """
    items: list[dict[str, Any]] = []
    rejected: list[int] = []
    for lineno, raw in enumerate(markdown_io.read_text(path).split("\n"), start=1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except ValueError:
            rejected.append(lineno)
            continue
        if isinstance(item, dict):
            item.setdefault("id", f"ligne-{lineno}")
            items.append(item)
        else:
            rejected.append(lineno)
    return items, rejected


def recomputed_cost(produced: dict[str, Any]) -> tuple[float, float, str | None]:
    """(coût RECALCULÉ depuis les tokens, coût déclaré, problème) d'un item-run.

    `cost_usd` est ce que l'application ANNONCE (`run_finished.cost_usd`,
    `RunResult.cost_usd`) : le confronter au plafond, c'était relire
    `sdda.cost.usd` — ce qu'ARCHITECTURE §8 interdit. Le coût se recalcule sur
    les spans `chat` de la trace rendue (`tracing.span_cost_usd`, la même table
    que `cost_report`). Un appel non tarifable, ou un coût déclaré sans aucun
    span à recalculer, est un PROBLÈME : le coût retenu est alors le plus haut
    des deux, jamais un zéro qui passerait sous n'importe quel plafond.
    """
    from sdda_lib import tracing  # noqa: PLC0415

    declared = float(produced.get("cost_usd", 0.0) or 0.0)
    trace = produced.get("trace")
    spans: Any = trace.get("spans") if isinstance(trace, dict) else trace
    if not spans and isinstance(trace, dict) and trace.get("trace_path"):
        spans = list(tracing.read_spans(Path(str(trace["trace_path"]))))
    llm = [s for s in spans or [] if isinstance(s, dict) and tracing.span_role(s) == "llm"]
    if not llm:
        if declared > 0:
            return declared, declared, "coût déclaré sans aucun span `chat` à recalculer"
        return 0.0, declared, None
    total, problems = 0.0, []
    for span in llm:
        usd, problem = tracing.span_cost_usd(tracing.attributes_of(span))
        if problem:
            problems.append(problem)
        else:
            total += usd or 0.0
    if problems:
        return max(total, declared), declared, problems[0]
    return round(total, 6), declared, None


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
    #: Fragments de chemin de dataset (`holdout`, `golden`, ou un chemin entier).
    datasets: set[str] = field(default_factory=set)
    #: G5 : ne retenir que les suites qui nomment UN agent, et le dire à
    #: l'exécuteur. Isoler n'est pas un filtre de confort — c'est ce qui
    #: distingue « cet agent tient ses AC » de « le système y arrive ». Sans
    #: isolation, un agent faible passe parce qu'un autre rattrape derrière.
    isolated: bool = False

    def accepts(self, suite: dict[str, Any]) -> bool:
        if self.suites and str(suite.get("id")) not in self.suites:
            return False
        if self.levels and str(suite.get("level")) not in self.levels:
            return False
        if self.caps and str(suite.get("capRef")) not in self.caps:
            return False
        if self.agents and str(suite.get("agentRef")) not in self.agents:
            return False
        if self.datasets:
            ds = str(suite.get("dataset") or "")
            if not any(frag in ds for frag in self.datasets):
                return False
        if self.isolated and not str(suite.get("agentRef") or "").strip():
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"suites": sorted(self.suites), "levels": sorted(self.levels), "caps": sorted(self.caps),
                "agents": sorted(self.agents), "datasets": sorted(self.datasets), "isolated": self.isolated}


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
    ir: dict[str, Any] | None = None,
    judge_trace: JudgeTrace | None = None,
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

    items, rejected = load_items_counted(dataset)
    if rejected and report is not None:
        report.warn("DATASET_ITEM_INVALID", f"suite `{sid}` : {len(rejected)} ligne(s) illisible(s) dans `{suite.get('dataset')}` "
                    f"(lignes {rejected[:5]}) — exclues de la mesure", "corriger le JSONL (validate-datasets le dit aussi)", sid)
    if item_limit is not None:
        items = items[:item_limit]
    if not items:
        # Un jeu vide ne mesure rien, et sous un seuil `<=` la moyenne nulle
        # d'aucun item passait le seuil : un vert pour une suite jamais jouée.
        if report is not None:
            report.error("EVAL_DATASET_EMPTY", f"suite `{sid}` : aucun item exploitable dans `{suite.get('dataset')}`",
                         "produire le jeu (qa-evals) : une suite sans item n'est pas une mesure", sid)
        result.notes.append("dataset vide : rien mesuré")
        return result, {}, [], 0
    grader_name = str(suite.get("grader", ""))
    grader_config = suite.get("graderConfig") if isinstance(suite.get("graderConfig"), dict) else None
    if graders_registry.normalize_name(grader_name) == "llm-judge" and not (graders and grader_name in graders):
        # Le juge est construit ICI, depuis STACK.md (`JudgeModel`, clé nommée
        # par la fiche provider) — sauf si la suite ou un test injecte le sien.
        from sdda_lib.graders import judge_clients  # noqa: PLC0415

        grader_config, problem = judge_clients.prepare_config(
            root, grader_config, layered=config,
            evaluated_model_id=_evaluated_models(root, ir, suite, config) or None,
            span_sink=judge_trace)
        if problem is not None and report is not None:
            report.error(problem.cls, f"suite `{sid}` : {problem.error}", problem.fix, sid)
        # Le juge ne croit que la calibration MESURÉE par calibrate-judge, jamais
        # un `calibration:` déclaré dans la suite (P9) : sans elle, il est advisory.
        from sdda_lib import calibration as _calibration  # noqa: PLC0415

        grader_config = dict(grader_config or {})
        measured = _calibration.measured_for_suite(root, (ir or {}).get("missionId"), sid)
        if measured is None:
            grader_config.pop("calibration", None)
        else:
            grader_config["calibration"] = measured
    grader = resolve_grader(grader_name, graders, grader_config)
    if grader is None:
        if report is not None:
            report.error("AC_GRADER_UNKNOWN", f"suite `{sid}` : grader `{grader_name}` indisponible",
                         "corriger le grader de l'AC (liste close d'eval-protocol.md §4), ou fournir ce qu'il exige "
                         "— un `llm-judge` sans client est enregistré mais inutilisable", sid)
        result.notes.append(f"grader `{grader_name}` indisponible : rien mesuré")
        return result, {}, [], 0

    critical_cap = plan.criticality == "critical"
    class_scores: dict[str, list[float]] = {}
    critical_classes: set[str] = set()
    detail_rows: list[dict[str, Any]] = []
    exec_errors = 0

    # Parallélisme des items — `EvalMaxParallel`, 4 par défaut.
    #
    # Un item était mesuré après l'autre, et c'est le poste qui domine la durée
    # d'un run complet : k=3 sur 50 items, pour chaque agent en G5, puis en G6,
    # puis en PHASE 6, puis en G8, fait plus d'un millier d'appels au système
    # évalué — des heures, quand les agents de construction, eux, se comptent en
    # dizaines de minutes. Le pipeline passait donc l'essentiel de son temps
    # mural à attendre en série des appels sans dépendance entre eux.
    #
    # Ce qui ne change pas, et qui doit être dit : l'ORDRE des résultats. Les
    # items sont replacés à leur rang avant agrégation, donc le rapport, les
    # moyennes par classe et le détail sont identiques à ceux de l'exécution
    # série. Une mesure dont l'ordre dépend de l'ordonnanceur ne serait pas
    # comparable d'un run à l'autre, et c'est précisément ce que P10 interdit.
    #
    # Contrat imposé à l'exécuteur : `run()` doit supporter des appels
    # concurrents. Les deux exécuteurs du squelette généré l'honorent (l'un
    # shelle la surface console, l'autre est sans état partagé). Un exécuteur
    # qui ne le peut pas se déclare en `EvalMaxParallel: 1`.
    max_parallel = max(1, config.get_int("EvalMaxParallel", 4) if config else 4)
    #: (run, item) -> (coût déclaré, problème de recalcul) : ce que le rapport
    #: montre à côté du coût recalculé, et ce que le plafond de coût refuse.
    cost_meta: dict[tuple[int, str], tuple[float, str | None]] = {}

    def measure(run_index: int, seed: int | None, item: dict[str, Any]) -> ItemResult:
        item_id = str(item.get("id"))
        # Pas de mémo : l'exécuteur est rappelé à chaque run, seed distinct.
        try:
            produced = executor.run(item, suite=suite, run_index=run_index, seed=seed) or {}
            provider_down = str(produced.get("error_class") or "")
            if provider_down in PROVIDER_ERROR_CLASSES:
                # Le modèle n'a jamais répondu : c'est une erreur d'EXÉCUTION,
                # comptée comme telle — pas une mauvaise réponse de l'agent.
                return ItemResult(item_id, run_index, 0.0, False, {}, f"{provider_down}: le fournisseur du modèle n'a pas répondu")
            cost, declared, cost_problem = recomputed_cost(produced)
            cost_meta[(run_index, item_id)] = (declared, cost_problem)
            measures = {"cost_usd": cost, "latency_ms": float(produced.get("latency_ms", 0.0) or 0.0)}
            grade = normalize_grade(grader(item, produced.get("output"), produced.get("trace"), measures))
            passed = grade.passed if grade.passed is not None else plan.threshold.holds(grade.score)
            return ItemResult(item_id, run_index, grade.score, bool(passed), grade.detail, None, measures["cost_usd"], measures["latency_ms"])
        except Exception as exc:  # une exception de l'exécuteur ou du grader est une erreur d'item, pas un score
            return ItemResult(item_id, run_index, 0.0, False, {}, f"{type(exc).__name__}: {exc}")

    for run_index, seed in enumerate(plan.seeds):
        run = RunResult(run_index=run_index)
        if max_parallel > 1 and len(items) > 1:
            with ThreadPoolExecutor(max_workers=min(max_parallel, len(items))) as pool:
                measured = list(pool.map(lambda it: measure(run_index, seed, it), items))
        else:
            measured = [measure(run_index, seed, it) for it in items]

        for item, ir_item in zip(items, measured):
            item_id = str(item.get("id"))
            if ir_item.error is not None:
                exec_errors += 1
            run.items.append(ir_item)
            cls = item_class(item)
            if cls is not None and ir_item.error is None:
                class_scores.setdefault(cls, []).append(ir_item.score)
                if critical_cap or item_is_critical(item):
                    critical_classes.add(cls)
            declared, cost_problem = cost_meta.get((run_index, item_id), (0.0, None))
            detail_rows.append({
                "itemId": item_id, "run": run_index, "class": cls, "score": round(ir_item.score, 6), "passed": ir_item.passed,
                "costUsd": round(ir_item.cost_usd, 6), "costDeclaredUsd": round(declared, 6), "costProblem": cost_problem,
                "latencyMs": round(ir_item.latency_ms, 2), "error": ir_item.error,
            })
        result.runs.append(run)

    # Un grader peut retirer au verdict son pouvoir de bloquer, item par item :
    # le juge LLM le fait quand il n'est pas calibré (P9) ou quand il est le
    # modèle évalué (`JudgeMustDifferFromEvaluated`). La suite le reprend — sans
    # quoi `detail["advisory"]` serait une annotation que rien ne lit, et le
    # score d'un juge qui se note lui-même bloquerait comme une mesure.
    reasons = sorted({str(it.detail.get("advisory_reason") or "advisory")
                      for run in result.runs for it in run.items if it.detail.get("advisory")})
    if reasons and not result.advisory:
        result.advisory = True
        result.notes.append("advisory (grader) : " + " | ".join(reasons[:3]))

    per_class_all = {cls: sum(v) / len(v) for cls, v in class_scores.items() if v}
    # Seules les classes CRITIQUES entrent dans le verdict : une classe
    # critique sous son seuil rend rouge même si la moyenne globale passe.
    result.per_class = {cls: per_class_all[cls] for cls in sorted(critical_classes) if cls in per_class_all}
    if exec_errors and report is not None:
        report.warn("AGENT_EVAL_FAILED", f"suite `{sid}` : {exec_errors} item-run(s) en erreur d'exécution (comptés comme échecs, jamais ignorés)", "", sid)
    provider_rows = [r for r in detail_rows if str(r.get("error") or "").split(":", 1)[0] in PROVIDER_ERROR_CLASSES]
    if provider_rows and report is not None:
        first = str(provider_rows[0]["error"]).split(":", 1)[0]
        report.error("INFRA_BLOCKED",
                     f"suite `{sid}` : {len(provider_rows)}/{len(detail_rows)} item-run(s) sans réponse du fournisseur ({first}) — "
                     "ce qui est rouge ici n'a pas été mesuré",
                     "vérifier la clé du fournisseur actif dans workspace/assets/.env (`install-env --require`) et le "
                     "`RuntimeProvider` de STACK.md, puis rejouer : aucun score n'est à interpréter", sid)
    return result, per_class_all, detail_rows, exec_errors


def _metric_of(suite_id: str) -> str:
    """`1-2-groundedness` -> `groundedness` ; `x-injection` -> `injection`."""
    return suite_id.rsplit("-", 1)[-1] if "-" in suite_id else suite_id


# ---------------------------------------------------------------------------
# Orchestration complète
# ---------------------------------------------------------------------------
def reports_dir(root: Path) -> Path:
    return paths.reports_dir(root)


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
    if filters.isolated:
        # L'isolement est porté par la suite transmise à l'exécuteur : c'est lui
        # qui sait câbler des outils mockés et un retrieval figé, le runner
        # n'appelant jamais rien lui-même. Le dire dans la suite plutôt que dans
        # une variable d'environnement garde la mesure lisible dans le rapport :
        # on voit, item par item, si ce score a été obtenu isolé ou en système.
        selected = [{**s, "isolated": True} for s in selected]
    if not selected:
        report.error("EVAL_SUITE_NOT_FOUND", f"aucune suite de `{mid}` ne correspond aux filtres {filters.to_dict()}",
                     "vérifier --suite / --level / --cap / --agent contre `evaluation.suites` de l'IR", mid)

    bpath = baseline_path or paths.resolve_rel(root, str((ir.get("evaluation") or {}).get("baselineRef") or f"workspace/pipeline/baselines/{number}-system.json"))
    baselines: dict[str, Baseline] = load_baselines(bpath)
    policy = str(config.get("EvalSeedPolicy", "vary") if config else "vary").strip().lower()
    if policy == "fixed":
        report.warn("EVAL_SINGLE_RUN_FORBIDDEN", "EvalSeedPolicy: fixed — un seed fixe masque la variance ; ce rapport ne vaut pas un verdict", "", mid)

    hard_cap = (ir.get("budget") or {}).get("costPerRunHardCapUsd")
    # La cible de latence : celle de la MISSION (IR, `## Execution Budget`),
    # sinon celle du Project Config (`LatencyP95TargetMs`, null par défaut).
    # Même ordre que le coût : la MISSION dit ce qu'elle exige, le projet dit
    # ce qu'il tolère faute de mieux.
    latency_target: Any = (ir.get("budget") or {}).get("latencyP95TargetMs")
    if latency_target is None and config is not None:
        latency_target = config.get("LatencyP95TargetMs")
    executed: list[ExecutedSuite] = []
    rid = run_id or run_id_now()
    # Écrit seulement si un juge RÉEL est appelé : ni les faux des tests, ni
    # les graders déterministes n'ouvrent de fichier.
    judge_trace = JudgeTrace(root, rid) if write_report else None
    for suite in selected:
        if suite.get("threshold") in (None, ""):
            # Le défaut était `>= 0` : toute suite sans seuil était verte, quoi
            # qu'elle mesure. Le schéma d'IR l'exige ; un IR édité ou ancien non.
            report.error("AC_NOT_EVALUABLE", f"suite `{suite.get('id')}` sans `threshold` : rien à franchir, donc aucun verdict",
                         "déclarer le seuil de l'AC dans la CAP, puis recompiler l'IR", str(suite.get("id")))
            continue
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
        result, per_class_all, rows, exec_errors = execute_suite(root, plan, executor, config=config, graders=graders, report=report,
                                                                 item_limit=item_limit, ir=ir, judge_trace=judge_trace)
        pins = current_pins(root, ir, suite=suite)
        ex = ExecutedSuite(plan=plan, result=result, pins=pins, per_class_all=per_class_all, items=rows, executor_errors=exec_errors)
        base = baselines.get(sid)
        if base is not None:
            ex.stale_dimensions = base.pins.diff(pins)
            if ex.stale_dimensions:
                result.notes.append(f"baseline périmée : {', '.join(sorted(ex.stale_dimensions))} a bougé")
                report.warn("EVAL_BASELINE_STALE", f"suite `{sid}` : la baseline mesurait autre chose ({', '.join(sorted(ex.stale_dimensions))} a bougé) — ce résultat n'est pas comparable",
                            "promote_baseline.py après lecture du résultat, sinon aucune régression n'est détectable", sid)
        # Le plafond vaut pour tout niveau qui fait tourner le SYSTÈME entier :
        # L5 et L9 (holdout, G8) en étaient exclus, et une acceptation pouvait
        # passer à dix fois le budget de la MISSION.
        if isinstance(hard_cap, (int, float)) and rows and str(suite.get("level")) in SYSTEM_LEVELS:
            unverified = [r for r in rows if r["error"] is None and r.get("costProblem")]
            if unverified:
                report.error("BUDGET_PRICING_UNKNOWN",
                             f"suite `{sid}` : {len(unverified)} item-run(s) au coût non recalculable depuis les tokens "
                             f"({unverified[0]['costProblem']}) — le plafond {hard_cap} USD ne peut pas être attesté",
                             "tracer chaque appel LLM (`gen_ai.request.model` + tokens) et compléter la table de tarifs "
                             "(`.sdda/providers/*.yaml`) ; un coût déclaré n'est pas une mesure (ARCHITECTURE §8)", sid)
            worst = max((r["costUsd"] for r in rows if r["error"] is None), default=0.0)
            if worst > float(hard_cap) + 1e-9:
                result.notes.append(f"coût max {worst:.4f} USD > costPerRunHardCapUsd {hard_cap}")
                report.error("BUDGET_EXCEEDED_MEASURED", f"suite `{sid}` : un item a coûté {worst:.4f} USD > plafond {hard_cap} USD",
                             "un budget dépassé est rouge, pas jaune (P6) : réduire la trajectoire ou revoir le plafond de la MISSION", sid)
        # Contrôle 6 de l'ORCH GATE : la latence MESURÉE, en p95, contre la
        # cible de la MISSION. Le coût était confronté à son plafond ; la
        # latence, déclarée au même endroit (`## Execution Budget`) et portée
        # par l'IR, n'était confrontée à rien — G6 promettait un contrôle que
        # personne ne jouait. Le p95 et non la moyenne : c'est la queue qui
        # fait attendre l'utilisateur, et une moyenne la cache (cost_report).
        # Seuls les runs qui ont MESURÉ une latence comptent : un exécuteur
        # qui rend 0 ms (oracle, replay) n'a rien mesuré, et 0 < cible ne
        # prouverait rien.
        if rows and str(suite.get("level")) in SYSTEM_LEVELS:
            latencies =[r["latencyMs"] for r in rows if r["error"] is None and r["latencyMs"] > 0]
            if latencies and isinstance(latency_target, (int, float)) and latency_target > 0:
                p95 = percentile(latencies, 0.95)
                if p95 > float(latency_target) + 1e-9:
                    result.notes.append(f"latence p95 {p95:.0f} ms > LatencyP95TargetMs {latency_target:.0f}")
                    report.error("LATENCY_EXCEEDED_MEASURED",
                                 f"suite `{sid}` : latence p95 mesurée {p95:.0f} ms > cible {latency_target:.0f} ms "
                                 f"(sur {len(latencies)} item-run(s))",
                                 "une cible de latence dépassée est rouge, pas jaune (P6) : raccourcir la trajectoire "
                                 "(hops, appels d'outils, retrieval) ou revoir `LatencyP95TargetMs` de la MISSION", sid)
        executed.append(ex)
        _emit_verdict_findings(report, ex)

    # Un run de DÉBOGAGE (`--limit`, k=1) reste rapporté, mais n'écrit aucun
    # rapport de gate. `--limit 1` écrivait la part `acceptance` de G8 sur un
    # seul item du holdout, et `--runs 1` une G5 verte sur un tirage : hors CI,
    # k=1 n'était qu'un avertissement, et le pipeline tourne hors CI.
    thin = sorted(ex.plan.id for ex in executed if ex.plan.runs < GATE_MIN_RUNS)
    if write_gates and executed and item_limit is not None:
        report.warn("EVAL_PARTIAL_RUN", f"--limit {item_limit} : run de débogage, aucun rapport de gate écrit",
                    "relancer sans --limit pour rendre un verdict de gate", mid)
    elif write_gates and thin:
        report.warn("EVAL_SINGLE_RUN_FORBIDDEN", f"k < {GATE_MIN_RUNS} sur {thin[:5]} : la gate de ces suites n'est pas écrite",
                    "EvalRuns >= 3 (5 si critique) : un tirage n'est pas un verdict", mid)
    results = [ex.result for ex in executed]
    verdict = aggregate(results)
    if report.errors and SEVERITY[verdict] < SEVERITY["red"]:
        verdict = "red"  # une erreur d'exécution n'est pas un score : elle bloque
    summary = summarize(results)
    summary["verdict"] = verdict

    if judge_trace is not None:
        judge_trace.close()
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
    if judge_trace is not None and judge_trace.path is not None:
        payload["judgeTrace"] = paths.rel(root, judge_trace.path)

    written: dict[str, str] = {}
    if write_report and executed:
        out = unique_report_path(root, number, rid)
        out.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(out, payload)
        written["report"] = paths.rel(root, out)
    if write_gates and executed and mid and item_limit is None:
        pins_all = gate_pins(root, ir, executed)
        by_gate: dict[tuple[str, str | None, str], list[ExecutedSuite]] = {}
        for ex in executed:
            key = LEVEL_GATE.get(str(ex.plan.suite.get("level")))
            if key:
                by_gate.setdefault((key[0], key[1], gate_artifact(ir, ex.plan.suite, key[0], mid)), []).append(ex)
        for (gate, part, artifact), members in sorted(by_gate.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2])):
            ids = {m.plan.id for m in members}
            if ids & set(thin):
                continue  # une gate écrite sans ses suites k=1 serait verte sur ce qu'elle n'a pas mesuré
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
    p.add_argument("--executor", default=None, help="`cli` | `cmd:<commande>` | `module:attr` — l'exécuteur du système évalué (objet ou fabrique sans argument)")
    p.add_argument("--suite", action="append", default=None, help="identifiant(s) de suite, répétable ou séparés par des virgules")
    p.add_argument("--level", "--levels", dest="level", default=None, help="niveaux L0..L9, séparés par des virgules")
    p.add_argument("--cap", action="append", default=None, help="CAP(s) à évaluer")
    p.add_argument("--agent", action="append", default=None, help="agent(s) à évaluer")
    p.add_argument("--dataset", action="append", default=None,
                   help="ne retenir que les suites dont le `dataset` contient ce fragment "
                        "(`holdout`, `golden`, ou un chemin entier). G8 : --level L9 --dataset holdout")
    p.add_argument("--isolated", action="store_true",
                   help="G5 : ne retenir que les suites qui nomment UN agent et transmettre `isolated: true` "
                        "à l'exécuteur (outils mockés, retrieval figé). Un agent mesuré en système "
                        "n'est pas mesuré : un pair rattrape sa faiblesse")
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
        filters = Filters(suites=_split(args.suite), levels=parse_levels(args.level), caps=_split(args.cap),
                          agents=_split(args.agent), datasets=_split(args.dataset), isolated=args.isolated)
    except ValueError as exc:
        report.error("EVAL_SUITE_NOT_FOUND", str(exc), "--level accepte L0..L9", "--level")
        return finish(report, args)

    if executor is None:
        if not args.executor:
            report.error("EVAL_EXECUTOR_MISSING", "aucun exécuteur : ce script n'appelle aucun LLM lui-même",
                         "--executor cli (l'application livrée, tout langage), cmd:<commande> ou module:attr (sdda_scripts.eval_runner:OracleExecutor vérifie la plomberie)", str(root))
            return finish(report, args)
        try:
            executor = load_executor(args.executor, root)
        except Exception as exc:
            report.error("EVAL_EXECUTOR_MISSING", f"`{args.executor}` inutilisable : {type(exc).__name__}: {exc}", "`--executor cli` lance l'application livrée par sa CLI, quel que soit son langage (stacks/serving/cli.md §3.5) ; `cmd:<commande>` l'impose ; `module:attr` (Python en processus) : le paquet est cherché sous workspace/src/, une dépendance absente se règle en lançant le runner par `uv run --project workspace/src/{App} …`", args.executor)
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
