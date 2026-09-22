#!/usr/bin/env python3
"""Évaluation du retrieval **sans agent** — la RETRIEVAL GATE (G4).

Applique P5 et l'invariant `retrieval-gate-before-agent` : aucun agent n'est
câblé à un index qui n'a pas passé son eval. La raison tient en une phrase —
*un retriever à recall 0.4 se présente comme « l'agent hallucine »*. Mesurer le
retrieval séparément est la seule façon de distinguer les deux, et c'est
l'origine de la majorité des diagnostics erronés du domaine.

Ce script **n'appelle aucun LLM** et n'ouvre aucune connexion. Il orchestre :

  - il lit l'IR compilé pour connaître les retrievers, leur `topK` et leurs
    `gateThresholds` (le contrat fait foi, la Project Config ne sert que de
    défaut quand le contrat est muet) ;
  - il obtient les documents ramenés soit d'un **exécuteur injecté**
    (`--executor module:attr`, le code généré du projet), soit d'un **replay**
    (`--replay fichier.jsonl`, des runs enregistrés) ;
  - il calcule recall@k, nDCG@k, context precision, MRR et taux de résolution
    des citations via `sdda_lib.retrieval_metrics` — que du calcul ;
  - il écrit UN rapport par retriever, `workspace/.sys/.validation/G4-{retriever}.json`,
    avec les hashes épinglés (index, golden, contrat) — c'est la clé que lit
    `compute_status`, et c'est aussi la bonne unité de décision : un index peut
    être vert pendant qu'un autre est rouge.

**La groundedness n'est pas mesurée ici.** Elle exige un juge, donc une
calibration (P9). Quand l'exécuteur ou le replay ne la fournit pas, elle reste
absente et le verdict passe au jaune avec la raison écrite — jamais au vert par
omission. C'est exactement le faux vert que ce framework existe pour empêcher.

Usage :
    python .sdda/sdda.py run-retrieval-eval --mission 1 --executor workspace.src.rag:Retriever
    python .sdda/sdda.py run-retrieval-eval --mission 1 --replay workspace/.sys/reports/runs/retrieval.jsonl --json
    python .sdda/sdda.py run-retrieval-eval --mission 1 --retriever 1-contracts-index --k 10

Bypass : `SDDA_BYPASS_RETRIEVAL_GATE=1` dégrade les erreurs en avertissements,
journalise dans `workspace/.sys/.audit/bypasses.jsonl` et marque le rapport
`bypassed: true`. Un contournement se voit ; il ne se cache pas.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, paths, retrieval_metrics  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.eval_stats import GLYPH, SEVERITY  # noqa: E402
from sdda_lib.gate_reports import append_bypass_audit, write_gate_report  # noqa: E402
from sdda_lib.layered_config import LayeredConfig  # noqa: E402
from sdda_lib.retrieval_metrics import QueryOutcome  # noqa: E402
from sdda_lib.runtime_io import atomic_write_json as _atomic_write_json, now_iso as _now_iso, run_id_now  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, load_config, resolve_root  # noqa: E402

BYPASS_ENV = "SDDA_BYPASS_RETRIEVAL_GATE"

#: (métrique mesurée, clé du contrat, clé de Project Config, défaut, classe si KO).
#: `contextPrecision` n'est pas requis par le schéma d'IR : absent des deux, son
#: défaut informe sans surprendre — mesurer le bruit servi reste utile.
THRESHOLDS: tuple[tuple[str, str, str, float, str], ...] = (
    ("recallAtK", "recallAtK", "RetrievalRecallAtK", 0.80, "RETRIEVAL_BELOW_THRESHOLD"),
    ("ndcgAtK", "ndcg", "RetrievalNdcgMin", 0.70, "RETRIEVAL_BELOW_THRESHOLD"),
    ("contextPrecision", "contextPrecision", "RetrievalContextPrecisionMin", 0.60, "RETRIEVAL_BELOW_THRESHOLD"),
    ("citationResolveRate", "citationResolveRate", "CitationResolveRateMin", 0.98, "CITATION_UNRESOLVED"),
)


# ---------------------------------------------------------------------------
# Exécuteur — injecté, jamais un appel réseau depuis ce script
# ---------------------------------------------------------------------------
class RetrievalExecutor(Protocol):
    """Ce que le script attend du retriever évalué.

    `retrieve` reçoit UNE requête et rend un dict : `docIds` (identifiants de
    DOCUMENT ramenés, dans l'ordre du classement), et facultativement
    `servedIds` (ce qui est réellement entré dans le contexte), `answer`,
    `latencyMs`, `groundedness`.
    """

    def retrieve(self, query: str, *, retriever: dict[str, Any], k: int, item: dict[str, Any]) -> dict[str, Any]: ...


class ReplayExecutor:
    """Rejoue des runs enregistrés : un JSONL `{id, docIds, servedIds, answer, …}`.

    Sert à deux choses qu'un exécuteur vivant ne sait pas faire : tourner en CI
    sans index, et comparer deux configurations de chunking sur EXACTEMENT les
    mêmes requêtes.
    """

    name = "replay"

    def __init__(self, records: dict[str, dict[str, Any]], source: str = ""):
        self.records = records
        self.source = source
        self.missing: list[str] = []

    @classmethod
    def from_file(cls, path: Path) -> "ReplayExecutor":
        records: dict[str, dict[str, Any]] = {}
        for line in markdown_io.read_text(path).split("\n"):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("id"):
                records[str(rec["id"])] = rec
        return cls(records, source=path.as_posix())

    def retrieve(self, query: str, *, retriever: dict[str, Any], k: int, item: dict[str, Any]) -> dict[str, Any]:
        rec = self.records.get(str(item.get("id")))
        if rec is None:
            # Une requête sans enregistrement n'est PAS un recall de 0 : ce
            # serait fabriquer une mesure. Elle est signalée et exclue.
            self.missing.append(str(item.get("id")))
            return {}
        return rec


def load_executor(spec: str) -> Any:
    """`module:attr` -> exécuteur. L'attribut peut être une fabrique sans argument."""
    if ":" not in spec:
        raise ValueError(f"`{spec}` : attendu `module:attr`")
    mod_name, attr = spec.rsplit(":", 1)
    obj = getattr(importlib.import_module(mod_name), attr)
    if isinstance(obj, type) or (callable(obj) and not hasattr(obj, "retrieve")):
        obj = obj()
    if not hasattr(obj, "retrieve"):
        raise ValueError(f"`{spec}` ne fournit pas de méthode `retrieve`")
    return obj


# ---------------------------------------------------------------------------
# Lecture des jeux
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


def query_of(item: dict[str, Any]) -> str:
    """La requête soumise au retriever, quelle que soit la forme de `input`."""
    raw = item.get("input")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("question", "query", "text"):
            if isinstance(raw.get(key), str):
                return raw[key]
        messages = raw.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if isinstance(last, dict) and isinstance(last.get("content"), str):
                return last["content"]
    return json.dumps(raw, ensure_ascii=False, sort_keys=True)


def golden_files(root: Path) -> list[Path]:
    d = paths.datasets_dir(root, "golden")
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def resolve_datasets(root: Path, ir: dict[str, Any], retriever_id: str, explicit: Path | None) -> list[Path]:
    """Le golden de retrieval d'un retriever, par ordre de préférence.

    1. `--dataset` : l'opérateur sait ce qu'il mesure ;
    2. une suite L3 de l'IR (le niveau RETRIEVAL de la pyramide) dont le nom de
       jeu porte l'identifiant du retriever — ou toutes les suites L3 quand il
       n'y a qu'un seul retriever ;
    3. repli : les jeux `golden/` qui portent réellement des `expected_documents`.

    Le repli est délibérément le dernier : deviner le jeu qui rend un verdict
    est acceptable pour démarrer, jamais pour livrer.
    """
    if explicit is not None:
        return [explicit if explicit.is_absolute() else root / explicit]

    retrievers = ir.get("retrievers") or []
    suites = [s for s in (ir.get("evaluation") or {}).get("suites") or [] if str(s.get("level")) == "L3"]
    matched = [s for s in suites if retriever_id in str(s.get("dataset", ""))]
    if not matched and len(retrievers) == 1:
        matched = suites
    if matched:
        return [root / str(s["dataset"]) for s in matched]

    return [p for p in golden_files(root) if any("expected_documents" in it for it in load_items(p))]


# ---------------------------------------------------------------------------
# Mesure d'un retriever
# ---------------------------------------------------------------------------
@dataclass
class RetrieverMeasure:
    retriever: dict[str, Any]
    k: int
    datasets: list[str]
    report: retrieval_metrics.RetrievalReport
    groundedness: float | None
    groundedness_n: int
    skipped: list[str]
    verdict: str
    thresholds: dict[str, float]

    @property
    def id(self) -> str:
        return str(self.retriever.get("id"))

    def to_dict(self) -> dict[str, Any]:
        measured = self.report.to_dict()
        d = dict(measured)
        d.update({
            "retrieverId": self.id,
            "pattern": self.retriever.get("pattern"),
            "indexHash": self.retriever.get("indexHash"),
            "datasets": self.datasets,
            "groundedness": round(self.groundedness, 6) if self.groundedness is not None else None,
            "groundednessQueries": self.groundedness_n,
            "skippedQueries": self.skipped[:20],
            "verdict": self.verdict,
            "thresholds": {k: v for k, v in sorted(self.thresholds.items())},
            "diagnosis": retrieval_metrics.diagnose(self.report.recall_at_k, self.groundedness, self.thresholds),
        })
        return d

    def render_line(self) -> str:
        g = f" groundedness {self.groundedness:.3f}" if self.groundedness is not None else " groundedness non mesurée"
        return (f"{GLYPH[self.verdict]} {self.id} — recall@{self.k} {self.report.recall_at_k:.3f} "
                f"nDCG {self.report.ndcg_at_k:.3f} précision {self.report.context_precision:.3f} "
                f"citations {self.report.citation_resolve_rate:.3f}{g} ({self.report.queries} requêtes)")


def threshold_for(retriever: dict[str, Any], contract_key: str, config_key: str, default: float, config: LayeredConfig | None) -> float:
    """Le contrat d'abord, la Project Config ensuite, le défaut en dernier."""
    declared = (retriever.get("gateThresholds") or {}).get(contract_key)
    if isinstance(declared, (int, float)):
        return float(declared)
    return config.get_float(config_key, default) if config else default


def measure(
    root: Path,
    ir: dict[str, Any],
    retriever: dict[str, Any],
    executor: Any,
    *,
    config: LayeredConfig | None,
    report: Report,
    k_override: int | None = None,
    dataset: Path | None = None,
    item_limit: int | None = None,
) -> RetrieverMeasure | None:
    rid = str(retriever.get("id"))
    k = k_override or int(retriever.get("topK") or (config.get_int("RetrievalK", 8) if config else 8))

    files = [p for p in resolve_datasets(root, ir, rid, dataset) if p.is_file()]
    if not files:
        report.error("GOLDEN_SET_MISSING", f"retriever `{rid}` : aucun golden de retrieval (aucune suite L3, aucun jeu porteur d'`expected_documents`)",
                     "produire le golden via qa-evals (/sdda-eval {n} --datasets-only), ou désigner le jeu avec --dataset", rid)
        return None

    outcomes: list[QueryOutcome] = []
    skipped: list[str] = []
    grounded: list[float] = []

    for path in files:
        for item in load_items(path)[: item_limit or None]:
            if not item.get("expected_documents"):
                continue  # item de réponse, pas de retrieval : il relève de L4
            raw = executor.retrieve(query_of(item), retriever=retriever, k=k, item=item) or {}
            docs = raw.get("docIds") or raw.get("doc_ids") or raw.get("retrieved") or raw.get("documents")
            if docs is None:
                skipped.append(str(item.get("id")))
                continue
            served = raw.get("servedIds") or raw.get("served_ids") or docs
            g = raw.get("groundedness")
            if isinstance(g, (int, float)):
                grounded.append(float(g))
            meta = item.get("metadata") or {}
            outcomes.append(QueryOutcome(
                query_id=str(item.get("id")),
                retrieved=[str(d) for d in docs],
                expected=item.get("expected_documents") or [],
                answer=str(raw.get("answer") or ""),
                served_ids=[str(s) for s in served],
                tags=[str(t) for t in (meta.get("tags") or [])] or [str(meta.get("class") or "(sans classe)")],
                latency_ms=float(raw.get("latencyMs") or raw.get("latency_ms") or 0.0),
            ))

    if not outcomes:
        report.error("RETRIEVAL_GATE_FAILED", f"retriever `{rid}` : 0 requête mesurée sur {len(files)} jeu(x), dont {len(skipped)} sans résultat de l'exécuteur",
                     "vérifier que le golden porte des `expected_documents` et que l'exécuteur rend `docIds`", rid)
        return None
    if skipped:
        report.warn("MEASUREMENT_MISSING", f"retriever `{rid}` : {len(skipped)} requête(s) sans résultat, exclues — le taux porte sur {len(outcomes)} requêtes, pas sur le jeu entier",
                    "compléter le replay ou corriger l'exécuteur ; une requête absente n'est pas un recall de 0", rid)

    min_queries = config.get_int("RetrievalGoldenMinQueries", 50) if config else 50
    if len(outcomes) < min_queries:
        report.warn("EVAL_DATASET_TOO_SMALL", f"retriever `{rid}` : {len(outcomes)} requêtes mesurées < RetrievalGoldenMinQueries {min_queries} — l'intervalle de confiance est large",
                    "étoffer le golden de retrieval avant de traiter ce résultat comme un verdict", rid)

    metrics = retrieval_metrics.evaluate(outcomes, k)
    values = metrics.to_dict()
    groundedness = (sum(grounded) / len(grounded)) if grounded else None

    verdict = "green"
    thresholds: dict[str, float] = {}
    for metric_key, contract_key, config_key, default, cls in THRESHOLDS:
        bound = threshold_for(retriever, contract_key, config_key, default, config)
        thresholds[contract_key] = bound
        measured = float(values.get(metric_key, 0.0))
        if measured + 1e-9 < bound:
            verdict = "red"
            fix = (retrieval_metrics.diagnose(metrics.recall_at_k, groundedness, {"recallAtK": bound})
                   if metric_key == "recallAtK"
                   else "corriger la couche fautive avant de câbler un agent sur cet index (P5)")
            report.error(cls, f"retriever `{rid}` : {metric_key} {measured:.3f} < seuil {bound:g} sur {metrics.queries} requêtes", fix, rid)

    ground_min = threshold_for(retriever, "groundedness", "GroundednessMin", 0.85, config)
    thresholds["groundedness"] = ground_min
    if groundedness is None:
        # P9 : un juge absent ou non calibré informe, il ne bloque pas. Il ne
        # rend pas vert non plus — la mesure manque, et le rapport le dit.
        if SEVERITY[verdict] < SEVERITY["yellow"]:
            verdict = "yellow"
        report.warn("RETRIEVAL_GATE_FAILED", f"retriever `{rid}` : groundedness non mesurée (aucun score de juge reçu) — l'étage GÉNÉRATION reste inconnu",
                    "fournir `groundedness` par requête via l'exécuteur ou le replay, juge calibré (calibrate_judge.py) ; sans elle G4 ne peut pas être verte", rid)
    elif groundedness + 1e-9 < ground_min:
        verdict = "red"
        report.error("RETRIEVAL_BELOW_THRESHOLD", f"retriever `{rid}` : groundedness {groundedness:.3f} < seuil {ground_min:g}",
                     retrieval_metrics.diagnose(metrics.recall_at_k, groundedness, thresholds), rid)

    if metrics.dangling_citations:
        report.warn("CITATION_UNRESOLVED", f"retriever `{rid}` : {len(metrics.dangling_citations)} citation(s) ne résolvent vers aucun passage servi — {metrics.dangling_citations[:5]}",
                    "une citation qui ne résout pas est une hallucination qui rassure : vérifier le mapping passage -> identifiant cité", rid)
    if not retriever.get("indexHash"):
        report.warn("RETRIEVAL_CONFIG_DRIFT", f"retriever `{rid}` : aucun `indexHash` déclaré — ce résultat ne pourra être rattaché à aucun index",
                    "renseigner `## Index / indexHash` dans le contrat de retrieval, puis recompiler l'IR", rid)

    return RetrieverMeasure(
        retriever=retriever, k=k, datasets=[paths.rel(root, p) for p in files], report=metrics,
        groundedness=groundedness, groundedness_n=len(grounded), skipped=skipped, verdict=verdict, thresholds=thresholds,
    )


# ---------------------------------------------------------------------------
# Épinglage et rapport de gate
# ---------------------------------------------------------------------------
def pinned_hashes(root: Path, m: "RetrieverMeasure") -> dict[str, str]:
    """Ce qui rend ce rapport périmé s'il bouge : index, contrat, golden."""
    pins: dict[str, str] = {}
    if m.retriever.get("indexHash"):
        pins[f"index:{m.id}"] = str(m.retriever["indexHash"])
    contract = paths.contracts_dir(root, "retrieval") / f"{m.id}.retrieval.md"
    if contract.is_file():
        pins[f"contract:{m.id}"] = hashing.sha256_file(contract)
    for ds in m.datasets:
        p = root / ds
        if p.is_file():
            pins[f"dataset:{ds}"] = hashing.sha256_file(p)
    return dict(sorted(pins.items()))


def run(
    root: Path,
    ir: dict[str, Any],
    executor: Any,
    *,
    config: LayeredConfig | None = None,
    only: set[str] | None = None,
    k_override: int | None = None,
    dataset: Path | None = None,
    item_limit: int | None = None,
    write_report: bool = True,
    run_id: str | None = None,
) -> tuple[Report, dict[str, Any]]:
    mid = str(ir.get("missionId") or "")
    report = Report(name="RETRIEVAL", target=mid or str(root))
    declared = ir.get("retrievers") or []

    if not declared:
        # Pas de RAG : la gate est sans objet. Une gate non applicable n'est pas
        # une gate franchie — le rapport le dit au lieu de mentir en vert.
        line = "G4 sans objet : aucun retriever dans l'IR"
        report.data.update({"verdict": "green", "applicable": False, "lines": [line]})
        return report, {"missionId": mid, "applicable": False, "verdict": "green", "retrievers": []}

    retrievers = [r for r in declared if not only or str(r.get("id")) in only]
    if not retrievers:
        report.error("RETRIEVAL_GATE_FAILED", f"aucun retriever ne correspond au filtre {sorted(only or [])}",
                     "vérifier --retriever ; les identifiants viennent de l'IR compilé", mid)
        return report, {"missionId": mid, "applicable": True, "verdict": "red", "retrievers": []}

    measures: list[RetrieverMeasure] = []
    for retriever in retrievers:
        m = measure(root, ir, retriever, executor, config=config, report=report,
                    k_override=k_override, dataset=dataset, item_limit=item_limit)
        if m is not None:
            measures.append(m)

    verdict = "green"
    for m in measures:
        if SEVERITY[m.verdict] > SEVERITY[verdict]:
            verdict = m.verdict
    if report.errors:
        verdict = "red"

    bypassed = os.environ.get(BYPASS_ENV, "") == "1"
    if bypassed and report.errors:
        reason = os.environ.get("SDDA_BYPASS_REASON", "")
        append_bypass_audit(root, "G4", reason or f"{BYPASS_ENV}=1 sans raison déclarée")
        if not reason:
            report.warn("BYPASS_REASON_MISSING", f"{BYPASS_ENV}=1 sans SDDA_BYPASS_REASON : le journal d'audit portera « sans raison déclarée »", "")
        for f in report.errors:
            # Dégradé, jamais effacé : le verdict du payload reste rouge et le
            # rapport porte `bypassed: true`. Contourner, c'est assumer.
            f.severity = "warn"

    rid = run_id or run_id_now()
    payload: dict[str, Any] = {
        "missionId": mid,
        "runId": rid,
        "generatedAt": _now_iso(),
        "executor": getattr(executor, "name", type(executor).__name__),
        "applicable": True,
        "bypassed": bypassed,
        "verdict": verdict,
        "retrievers": [m.to_dict() for m in measures],
        "findings": {"errors": [f.to_dict() for f in report.errors], "warnings": [f.to_dict() for f in report.warnings]},
    }

    written: dict[str, str] = {}
    if write_report and measures:
        out = paths.reports_dir(root) / f"retrieval-{mid or 'system'}-{rid}.json"
        _atomic_write_json(out, payload)
        written["report"] = paths.rel(root, out)
        # UN rapport PAR RETRIEVER : `compute_status` lit `G4-{retriever}`.
        # C'est aussi la bonne unité de décision — un index peut être vert
        # pendant qu'un autre est rouge, et les câbler ensemble n'aurait pas de
        # sens.
        for m in measures:
            sub = Report(name="G4.retrieval", target=m.id, data={"runId": rid, "verdict": m.verdict})
            for finding in report.findings:
                if finding.location == m.id:
                    sub.findings.append(finding)
            p = write_gate_report(root, "G4", m.id, sub, pinned_hashes(root, m))
            written[f"G4:{m.id}"] = paths.rel(root, p)
    payload["written"] = written
    report.data.update({"verdict": verdict, "runId": rid, "applicable": True, "bypassed": bypassed,
                        "written": written, "lines": [m.render_line() for m in measures]})
    return report, payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="RETRIEVAL GATE (G4) : recall@k, nDCG, context precision, citations — 0 agent, 0 appel LLM")
    p.add_argument("--mission", type=int, default=None, help="numéro de mission ; défaut : l'unique IR compilé")
    p.add_argument("--ir", type=Path, default=None, help="fichier IR explicite")
    p.add_argument("--executor", default=None, help="`module:attr` — le retriever évalué (objet ou fabrique sans argument)")
    p.add_argument("--replay", type=Path, default=None, help="JSONL de runs enregistrés `{id, docIds, servedIds, answer, groundedness}`")
    p.add_argument("--retriever", action="append", default=None, help="identifiant(s) de retriever, répétable ou séparés par des virgules")
    p.add_argument("--dataset", type=Path, default=None, help="golden de retrieval explicite (sinon : suite L3 de l'IR, puis détection)")
    p.add_argument("--k", type=int, default=None, help="forcer k (sinon `topK` du contrat, puis RetrievalK)")
    p.add_argument("--limit", type=int, default=None, help="ne prendre que les N premiers items par jeu (débogage)")
    p.add_argument("--run-id", default=None, help="identifiant du run (défaut : horodatage UTC)")
    add_common_args(p)
    return p


def _split(raw: list[str] | None) -> set[str]:
    out: set[str] = set()
    for chunk in raw or []:
        out.update(t.strip() for t in chunk.split(",") if t.strip())
    return out


def resolve_ir_path(root: Path, args: argparse.Namespace, report: Report) -> Path | None:
    if args.ir:
        return args.ir if args.ir.is_absolute() else root / args.ir
    if args.mission is not None:
        return paths.ir_path(root, args.mission)
    candidates = sorted(paths.ir_dir(root).glob("*-system.ir.json"))
    if len(candidates) != 1:
        report.error("IR_NOT_FOUND", f"{len(candidates)} IR compilé(s) dans workspace/.sys/.ir/ : préciser --mission",
                     "python .sdda/sdda.py ir-compiler --mission {n}", str(paths.ir_dir(root)))
        return None
    return candidates[0]


def main(argv: list[str] | None = None, *, executor: Any = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="RETRIEVAL", target=str(root))
    config = load_config(root, report)

    if executor is None:
        if args.replay:
            path = args.replay if args.replay.is_absolute() else root / args.replay
            if not path.is_file():
                report.error("RETRIEVAL_EXECUTOR_MISSING", f"replay `{paths.rel(root, path)}` introuvable", "vérifier --replay", paths.rel(root, path))
                return finish(report, args)
            executor = ReplayExecutor.from_file(path)
        elif args.executor:
            try:
                executor = load_executor(args.executor)
            except Exception as exc:
                report.error("RETRIEVAL_EXECUTOR_MISSING", f"`{args.executor}` inutilisable : {type(exc).__name__}: {exc}",
                             "corriger le chemin `module:attr` ; le module doit être importable depuis le cwd", args.executor)
                return finish(report, args)
        else:
            report.error("RETRIEVAL_EXECUTOR_MISSING", "aucun retriever à interroger : ce script ne mesure pas un index, il fait mesurer le vôtre (0 appel LLM, 0 connexion)",
                         "--executor module:attr (le code généré expose le retriever) ou --replay fichier.jsonl (runs enregistrés)", str(root))
            return finish(report, args)

    ir_file = resolve_ir_path(root, args, report)
    if ir_file is None:
        return finish(report, args)
    if not ir_file.is_file():
        report.error("IR_NOT_FOUND", f"IR `{paths.rel(root, ir_file)}` introuvable",
                     "compiler : python .sdda/sdda.py ir-compiler --mission {n}", paths.rel(root, ir_file))
        return finish(report, args)
    ir = ir_compiler.load_ir(ir_file)

    sub, payload = run(
        root, ir, executor, config=config, only=_split(args.retriever), k_override=args.k,
        dataset=args.dataset, item_limit=args.limit, write_report=not args.no_report, run_id=args.run_id,
    )
    report.extend(sub)
    report.data.update(sub.data)
    if isinstance(executor, ReplayExecutor) and executor.missing:
        report.data["replayMissing"] = len(executor.missing)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return report.exit_code
    for line in sub.data.get("lines", []):
        print(line)
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
