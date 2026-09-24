#!/usr/bin/env python3
"""Où part l'argent — coût et latence MESURÉS, ventilés, comparés aux seuils.

`review-cost` compare des mesures à des seuils et nomme l'écart ; il n'estime
rien. Ce script produit ces mesures depuis les traces de runs
(`workspace/.sys/traces/runs/*.jsonl`) et les rapports d'eval, sans LLM :

    par run      coût RECALCULÉ depuis les tokens (`tracing.span_cost_usd`),
                 latence du span racine, tokens in/out/cache, hops
    par agent    coût des spans `chat` rattachés par l'arbre, latence, tours
    par CAP      coût de l'agent réparti sur ses CAPs (`sdda.cap.ids` du span,
                 sinon `servesCaps` de l'IR), part du total
    par nœud     projection sur le graphe de l'IR ; part d'orchestration
                 (routeur, superviseur, fusion)
    par outil    appels, échecs, retries, latence, octets rendus
    queue        les runs au-dessus du p95 (coût ou latence) : chemin, outils en
                 échec, bornes atteintes — la queue à 3 % qui casse le p95

Distributions : mean, p50, p95, p99, max. Le p95 compte, pas la moyenne.

Deux factures, jamais additionnées. Le coût du PRODUIT vient des tokens ; celui
de la CONSTRUCTION (`sdda.build.agent`) est DÉCLARÉ par le harnais et reste dans
un champ à part (ARCHITECTURE §8). L'écart entre le coût déclaré par
l'application (`sdda.cost.usd`) et le coût recalculé est rapporté, jamais
arbitré en faveur du déclaré.

Seuils (IR) et classes : p95 > `costPerRunHardCapUsd` -> [BUDGET_EXCEEDED_MEASURED]
(erreur) ; moyenne > `costPerRunTargetUsd` -> [BUDGET_TARGET_MISSED] ; p95 de
latence > `latencyP95TargetMs` -> [LATENCY_P95_EXCEEDED] ; max de tokens >
`tokenCeilingPerRun` -> [TOKEN_CEILING_EXCEEDED] ; hops > `maxHops` ->
[UNBOUNDED_LOOP] (erreur) ; hops au plafond sur ≥ 5 % des runs ->
[HOPS_AT_CEILING] ; p95 d'un agent > `bounds.budgetUsd` -> [AGENT_BUDGET_EXCEEDED] ;
écart > 25 % à l'estimation G2 -> [BUDGET_ESTIMATE_DRIFT] ; CAP `normal` > 40 %
du coût -> [CAP_COST_EXCEEDS_VALUE] ; orchestration > 30 % -> [ORCH_OVERHEAD_HIGH].
Moins de `--min-runs` runs -> [MEASUREMENT_MISSING] : un p95 sur 7 points n'est
pas une mesure, et le rapport ne fait pas semblant.

Usage :
    python .sdda/sdda.py cost-report --mission 1 --traces workspace/.sys/traces/runs --out workspace/.sys/.validation/cost-1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import eval_reports, markdown_io, paths, tracing  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.runtime_io import atomic_write_json, now_iso  # noqa: E402
from sdda_scripts import _run_traces as rt  # noqa: E402
from sdda_scripts._common import add_common_args, finish, resolve_root  # noqa: E402

MIN_RUNS = 30
ESTIMATE_DRIFT = 0.25
CAP_SHARE_MAX = 0.40
ORCH_SHARE_MAX = 0.30
HOPS_CEILING_SHARE = 0.05
TAIL_CAP = 10

#: Nœuds qui orchestrent au lieu de produire : leur coût est un surcoût.
ORCH_KINDS = frozenset({"router", "supervisor", "fusion", "aggregator", "planner"})
ORCH_PATTERNS = frozenset({"router", "supervisor", "hierarchical", "planner-executor"})


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def cap_criticality(root: Path, number: int) -> dict[str, str]:
    """`Criticality:` de chaque CAP de la mission — une CAP critique a le droit de coûter."""
    out: dict[str, str] = {}
    for path in sorted(paths.caps_dir(root).glob(f"{number}-*-*.md")):
        fields = markdown_io.parse_header_fields(markdown_io.read_text(path))
        cid = str(fields.get("ID") or path.stem).strip()
        out[cid] = str(fields.get("Criticality") or "normal").strip().lower()
    return out


def orchestration_nodes(ir: dict[str, Any]) -> set[str]:
    orch = ir.get("orchestration") or {}
    out = {str(n.get("id")) for n in orch.get("nodes") or [] if str(n.get("kind") or "") in ORCH_KINDS}
    if str(orch.get("rootPattern") or "") in ORCH_PATTERNS and orch.get("entryNode"):
        out.add(str(orch["entryNode"]))
    return out


# ---------------------------------------------------------------------------
# Mesure d'un run
# ---------------------------------------------------------------------------
def measure_run(run: rt.RunTrace, ir: dict[str, Any], nodes: dict[str, str]) -> dict[str, Any]:
    serves = {str(a.get("id")): [str(c) for c in a.get("servesCaps") or []] for a in ir.get("agents") or []}
    by_agent: dict[str, dict[str, float]] = {}
    by_cap: dict[str, float] = {}
    tools: list[dict[str, Any]] = []
    cache_read = 0
    unpriced = 0

    for span in run.spans:
        role = run.role(span)
        attrs = tracing.attributes_of(span)
        if role == "llm":
            usd, problem = tracing.span_cost_usd(attrs)
            if problem:
                unpriced += 1
                continue
            cache_read += int(_num(attrs.get(tracing.A_CACHE_READ)) or 0)
            owner = run.owning_agent(span)
            key = rt.agent_key(owner) if owner else "(hors agent)"
            entry = by_agent.setdefault(key, {"costUsd": 0.0, "latencyMs": 0.0, "turns": 0})
            entry["costUsd"] += usd or 0.0
            # La CAP se lit sur le span d'agent (`sdda.cap.ids`) ; à défaut, dans
            # `servesCaps` de l'IR. Un agent qui sert deux CAPs partage son coût
            # à parts égales : une clé de répartition plus fine serait inventée.
            declared = tracing.attributes_of(owner).get(rt.A_CAP_IDS) if owner else None
            if isinstance(declared, list) and declared:
                caps = [str(c) for c in declared]
            else:
                ir_id = key if key in serves else next((a for a in serves if a.endswith("-" + key)), "")
                caps = serves.get(ir_id) or ["(sans CAP)"]
            for cap in caps:
                by_cap[cap] = by_cap.get(cap, 0.0) + (usd or 0.0) / len(caps)
        elif role == "agent":
            entry = by_agent.setdefault(rt.agent_key(span), {"costUsd": 0.0, "latencyMs": 0.0, "turns": 0})
            entry["turns"] += 1
            # Latence propre : un sous-agent imbriqué ne compte pas deux fois.
            owner = run.owning_agent(span)
            if owner is None:
                entry["latencyMs"] += rt.span_duration_ms(span)
        elif role == "tool":
            owner = run.owning_agent(span)
            tools.append({
                "tool": tracing._tool_name(span, attrs), "agent": rt.agent_key(owner) if owner else "",
                "ok": str(span.get("status") or "OK").upper() != "ERROR",
                "retry": (_num(attrs.get(rt.A_TOOL_RETRY)) or 0) > 0,
                "latencyMs": rt.span_duration_ms(span),
                "bytes": int(_num(attrs.get(rt.A_RESULT_BYTES)) or 0),
                "sideEffectClass": str(attrs.get(tracing.A_SIDE_EFFECT) or ""),
                "errorCode": str(attrs.get(rt.A_TOOL_ERROR_CODE) or ""),
            })

    by_node: dict[str, float] = {}
    for key, entry in by_agent.items():
        node = nodes.get(key, key)
        by_node[node] = by_node.get(node, 0.0) + entry["costUsd"]
    s = run.summary
    return {
        "runId": run.run_id, "item": rt.item_of(run.run_id),
        "costUsd": round(s.cost_usd, 6), "costDeclaredUsd": round(s.cost_declared_usd, 6),
        "latencyMs": s.latency_ms, "tokensIn": s.tokens_in, "tokensOut": s.tokens_out, "cacheReadTokens": cache_read,
        "tokens": s.tokens_in + s.tokens_out, "hops": len(rt.visits(run)),
        "trajectory": [nodes.get(k, k) for k, _ in rt.visits(run)],
        "boundsExceeded": s.bounds_exceeded, "complete": s.complete, "unpricedCalls": unpriced,
        "byAgent": by_agent, "byCap": by_cap, "byNode": by_node, "tools": tools,
        "buildCostUsd": round(s.build_cost_usd, 6),
    }


# ---------------------------------------------------------------------------
# Agrégation
# ---------------------------------------------------------------------------
def _share(part: float, total: float) -> float:
    return round(part / total, 6) if total > 0 else 0.0


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(r["costUsd"] for r in rows)

    def breakdown(field: str) -> dict[str, Any]:
        keys = sorted({k for r in rows for k in r[field]})
        out: dict[str, Any] = {}
        for k in keys:
            per_run = [float(r[field][k]["costUsd"] if isinstance(r[field].get(k), dict) else r[field].get(k, 0.0)) for r in rows]
            spent = sum(per_run)
            out[k] = {"costUsd": rt.distribution(per_run), "totalUsd": round(spent, 6), "share": _share(spent, total)}
            if field == "byAgent":
                out[k]["latencyMs"] = rt.distribution([float(r[field].get(k, {}).get("latencyMs", 0.0)) for r in rows], 1)
                out[k]["turns"] = int(sum(r[field].get(k, {}).get("turns", 0) for r in rows))
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["totalUsd"]))

    tool_rows = [t for r in rows for t in r["tools"]]
    tools: dict[str, Any] = {}
    for name in sorted({t["tool"] for t in tool_rows}):
        mine = [t for t in tool_rows if t["tool"] == name]
        tools[name] = {"calls": len(mine), "errors": sum(1 for t in mine if not t["ok"]),
                       "retries": sum(1 for t in mine if t["retry"]),
                       "latencyMs": rt.distribution([float(t["latencyMs"]) for t in mine], 1),
                       "maxBytes": max(t["bytes"] for t in mine),
                       "sideEffectClass": next((t["sideEffectClass"] for t in mine if t["sideEffectClass"]), ""),
                       "errorCodes": sorted({t["errorCode"] for t in mine if t["errorCode"]})}
    tokens_in = sum(r["tokensIn"] for r in rows)
    cache = sum(r["cacheReadTokens"] for r in rows)
    hops = [float(r["hops"]) for r in rows]
    return {
        "runs": len(rows), "totalCostUsd": round(total, 6),
        "costUsd": rt.distribution([r["costUsd"] for r in rows]),
        "latencyMs": rt.distribution([float(r["latencyMs"]) for r in rows], 1),
        "tokens": rt.distribution([float(r["tokens"]) for r in rows], 0),
        "hops": {**rt.distribution(hops, 0), "histogram": {str(int(h)): hops.count(h) for h in sorted(set(hops))}},
        "tokensIn": tokens_in, "tokensOut": sum(r["tokensOut"] for r in rows), "cacheReadTokens": cache,
        "cacheHitRate": _share(cache, tokens_in + cache),
        "byAgent": breakdown("byAgent"), "byCap": breakdown("byCap"), "byNode": breakdown("byNode"),
        "byTool": dict(sorted(tools.items(), key=lambda kv: (-kv[1]["errors"], -kv[1]["retries"], kv[0]))),
        "declaredVsRecalculated": {
            "declaredUsd": round(sum(r["costDeclaredUsd"] for r in rows), 6), "recalculatedUsd": round(total, 6),
            "divergentRuns": sorted(r["runId"] for r in rows if r["costDeclaredUsd"]
                                    and abs(r["costDeclaredUsd"] - r["costUsd"]) > max(tracing.COST_DIVERGENCE_ABS_USD,
                                                                                         tracing.COST_DIVERGENCE_RATIO * r["costUsd"]))[:20],
        },
    }


def tail(rows: list[dict[str, Any]], agg: dict[str, Any]) -> list[dict[str, Any]]:
    """Les runs au-dessus du p95 de coût OU de latence, du plus cher au moins cher."""
    p95c, p95l = agg["costUsd"]["p95"] or 0.0, agg["latencyMs"]["p95"] or 0.0
    out = [r for r in rows if r["costUsd"] > p95c or r["latencyMs"] > p95l or r["costUsd"] == agg["costUsd"]["max"]]
    return [{"runId": r["runId"], "costUsd": r["costUsd"], "latencyMs": r["latencyMs"], "hops": r["hops"],
             "trajectory": r["trajectory"], "boundsExceeded": r["boundsExceeded"],
             "failedTools": sorted({t["tool"] for t in r["tools"] if not t["ok"]}),
             "retriedTools": sorted({t["tool"] for t in r["tools"] if t["retry"]})}
            for r in sorted(out, key=lambda r: (-r["costUsd"], -r["latencyMs"], r["runId"]))[:TAIL_CAP]]


# ---------------------------------------------------------------------------
# Seuils
# ---------------------------------------------------------------------------
def compare(ir: dict[str, Any], agg: dict[str, Any], crit: dict[str, str], report: Report, loc: str) -> list[dict[str, Any]]:
    budget = ir.get("budget") or {}
    orch = ir.get("orchestration") or {}
    rows: list[dict[str, Any]] = []

    def row(measure: str, value: Any, bound: Any, cls: str, exceeded: bool) -> None:
        rows.append({"measure": measure, "value": value, "threshold": bound, "class": cls if exceeded else None, "exceeded": exceeded})

    p95c, meanc = agg["costUsd"]["p95"], agg["costUsd"]["mean"]
    cap_, target = _num(budget.get("costPerRunHardCapUsd")), _num(budget.get("costPerRunTargetUsd"))
    if cap_ is not None:
        row("costUsd.p95", p95c, cap_, "BUDGET_EXCEEDED_MEASURED", p95c > cap_ + 1e-9)
        if p95c > cap_ + 1e-9:
            report.error("BUDGET_EXCEEDED_MEASURED", f"coût p95 ${p95c:.4f}/run > costPerRunHardCapUsd ${cap_} sur {agg['runs']} runs",
                         "rouge même si le score est atteint : voir `byCap`, `byNode` et `tail` pour savoir où couper", loc)
    if target is not None:
        row("costUsd.mean", meanc, target, "BUDGET_TARGET_MISSED", meanc > target + 1e-9)
        if meanc > target + 1e-9:
            report.warn("BUDGET_TARGET_MISSED", f"coût moyen ${meanc:.4f}/run > costPerRunTargetUsd ${target}", "cible manquée : jaune", loc)
    p95l, lat = agg["latencyMs"]["p95"], _num(budget.get("latencyP95TargetMs"))
    if lat is not None:
        row("latencyMs.p95", p95l, lat, "LATENCY_P95_EXCEEDED", p95l > lat)
        if p95l > lat:
            report.warn("LATENCY_P95_EXCEEDED", f"latence p95 {p95l:.0f} ms > latencyP95TargetMs {lat:.0f}", "lire la queue (`tail`) : quel chemin fait le p95", loc)
    ceiling, tmax = _num(budget.get("tokenCeilingPerRun")), agg["tokens"]["max"]
    if ceiling is not None:
        row("tokens.max", tmax, ceiling, "TOKEN_CEILING_EXCEEDED", tmax > ceiling)
        if tmax > ceiling:
            report.warn("TOKEN_CEILING_EXCEEDED", f"tokens max {tmax:.0f}/run > tokenCeilingPerRun {ceiling:.0f}", "le plafond n'a pas coupé : vérifier sa matérialisation", loc)
    max_hops, hmax = _num(orch.get("maxHops")), agg["hops"]["max"]
    if max_hops is not None:
        row("hops.max", hmax, max_hops, "UNBOUNDED_LOOP", hmax > max_hops)
        if hmax > max_hops:
            report.error("UNBOUNDED_LOOP", f"{hmax:.0f} hops observés > maxHops {max_hops:.0f} : la borne n'est pas matérialisée en code",
                         "dev-orchestration : borne dure dans le graphe ; review-orchestration pour le chemin", loc)
        at_ceiling = agg["hops"]["histogram"].get(str(int(max_hops)), 0)
        if agg["runs"] and at_ceiling / agg["runs"] >= HOPS_CEILING_SHARE:
            report.warn("HOPS_AT_CEILING", f"{at_ceiling}/{agg['runs']} runs atteignent maxHops {max_hops:.0f}",
                        "des runs butent sur la borne : la topologie ou le routeur boucle", loc)
    for agent in ir.get("agents") or []:
        aid, bound = str(agent.get("id")), _num((agent.get("bounds") or {}).get("budgetUsd"))
        stats = agg["byAgent"].get(aid) or agg["byAgent"].get(aid.split("-", 1)[-1])
        if bound is None or not stats:
            continue
        v = stats["costUsd"]["p95"]
        row(f"byAgent.{aid}.costUsd.p95", v, bound, "AGENT_BUDGET_EXCEEDED", v > bound + 1e-9)
        if v > bound + 1e-9:
            report.warn("AGENT_BUDGET_EXCEEDED", f"`{aid}` : coût p95 ${v:.4f} > bounds.budgetUsd ${bound}", "architect-topology : tier ou bornes de l'agent", loc)
    est = budget.get("estimated") or {}
    for label, measured, key in (("costUsd.mean", meanc, "nominalCostUsd"), ("latencyMs.p50", agg["latencyMs"]["p50"], "nominalLatencyMs")):
        nominal = _num(est.get(key))
        if nominal and measured is not None:
            drift = abs(measured - nominal) / nominal
            row(f"{label} vs G2", measured, nominal, "BUDGET_ESTIMATE_DRIFT", drift > ESTIMATE_DRIFT)
            if drift > ESTIMATE_DRIFT:
                report.warn("BUDGET_ESTIMATE_DRIFT", f"{label} mesuré {measured:.4f} vs {key} estimé {nominal:.4f} en G2 (écart {drift:.0%})",
                            "l'estimateur ou la topologie a tort : le dire à architect-topology", loc)
    for cap, stats in agg["byCap"].items():
        if stats["share"] > CAP_SHARE_MAX and crit.get(cap, "normal") != "critical" and cap != "(sans CAP)":
            report.warn("CAP_COST_EXCEEDS_VALUE", f"CAP `{cap}` ({crit.get(cap, 'normal')}) pèse {stats['share']:.0%} du coût total",
                        "la nommer à son owner ; review-cost ne la retire pas", loc)
    orch_nodes = orchestration_nodes(ir)
    orch_share = sum(v["share"] for k, v in agg["byNode"].items() if k in orch_nodes)
    agg["orchestrationShare"] = round(orch_share, 6)
    if orch_nodes and orch_share > ORCH_SHARE_MAX:
        report.warn("ORCH_OVERHEAD_HIGH", f"nœuds d'orchestration {sorted(orch_nodes)} = {orch_share:.0%} du coût",
                    "fait pour review-orchestration : à croiser avec la justification P7 de la topologie", loc)
    return rows


def l7_summary(root: Path, number: int) -> dict[str, Any]:
    """Ce que le dernier rapport d'eval dit de la L7 : coût par CAP et suites de mission."""
    latest = eval_reports.latest_report(root, number)
    data = eval_reports.load_json(latest) if latest else None
    if not data:
        return {"report": None}
    suites = [{k: s.get(k) for k in ("suiteId", "level", "verdict", "mean", "passRate", "pass_rate") if k in s}
              for s in data.get("suites") or [] if isinstance(s, dict) and str(s.get("level")) == "L7"]
    return {"report": paths.rel(root, latest), "runId": data.get("runId"), "costByCap": data.get("costByCap") or {}, "suites": suites}


def run(root: Path, ir: dict[str, Any], traces: Path, *, min_runs: int = MIN_RUNS) -> tuple[Report, dict[str, Any]]:
    mid = str(ir.get("missionId") or "")
    number = eval_reports.mission_number(ir)
    report = Report(name="COST-REPORT", target=mid or str(root))
    loaded = rt.load_runs(traces, number or None, mid)
    nodes = rt.node_index(ir)
    rows = [measure_run(r, ir, nodes) for r in loaded.runs]
    build = round(sum(r.summary.build_cost_usd for r in [*loaded.runs, *loaded.build_only]), 6)
    payload: dict[str, Any] = {
        "missionId": mid, "generatedAt": now_iso(), "traces": paths.rel(root, traces),
        "runsRead": len(rows), "runsOtherMission": len(loaded.other_mission), "runsUnattributed": loaded.unattributed[:20],
        "buildCostUsd": build,
        "note": "buildCostUsd est DÉCLARÉ par le harnais (spans sdda.build.agent) ; il n'est jamais additionné au coût du produit",
    }
    if len(rows) < min_runs:
        report.error("MEASUREMENT_MISSING", f"{len(rows)} run(s) tracé(s) pour {mid or 'la mission'} (< {min_runs}) : un p95 sur si peu de points n'est pas une mesure",
                     "exécuter la suite L7 sur le golden de mission (k runs) avant la revue de coût", paths.rel(root, traces))
    if not rows:
        return report, payload
    if loaded.unattributed:
        report.warn("TRACE_MALFORMED", f"{len(loaded.unattributed)} trace(s) sans `{tracing.A_MISSION_ID}` sur le span racine, comptées pour cette mission",
                    "poser `sdda.mission.id` sur `sdda.run` (observability/otel-genai.md §3.1)", paths.rel(root, traces))
    incomplete = [r["runId"] for r in rows if not r["complete"]]
    if incomplete:
        report.warn("TRACE_MALFORMED", f"{len(incomplete)} trace(s) incomplète(s) ({', '.join(incomplete[:5])}) — span racine, tarif ou champs manquants",
                    "tracing.summarize(...).problems les nomme ; un coût partiel n'est pas un coût", paths.rel(root, traces))
    if any(r["unpricedCalls"] for r in rows):
        report.warn("BUDGET_PRICING_UNKNOWN", f"{sum(r['unpricedCalls'] for r in rows)} appel(s) LLM non recalculables (modèle hors table de tarifs ou tokens absents) — exclus du coût",
                    "compléter `sdda_lib/pricing.py` ; un zéro passerait sous n'importe quel plafond", paths.rel(root, traces))
    agg = aggregate(rows)
    payload.update(agg)
    payload["thresholds"] = compare(ir, agg, cap_criticality(root, number), report, paths.rel(root, traces))
    payload["orchestrationShare"] = agg.get("orchestrationShare", 0.0)
    payload["tail"] = tail(rows, agg)
    payload["l7"] = l7_summary(root, number)
    if agg["declaredVsRecalculated"]["divergentRuns"]:
        report.warn("TRACE_MALFORMED", f"{len(agg['declaredVsRecalculated']['divergentRuns'])} run(s) où `sdda.cost.usd` déclaré ≠ coût recalculé",
                    "le chiffre recalculé fait foi ; corriger la table de tarifs de l'application", paths.rel(root, traces))
    return report, payload


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Coût et latence mesurés depuis les traces — par run, CAP, agent, nœud, outil ; comparés aux seuils de l'IR (0 token)")
    p.add_argument("--mission", type=int, default=None, help="numéro de MISSION ; défaut : l'unique IR compilé")
    p.add_argument("--traces", type=Path, default=None, help="répertoire des traces ; défaut : workspace/.sys/traces/runs")
    p.add_argument("--ir", type=Path, default=None, help="fichier IR explicite")
    p.add_argument("--out", type=Path, default=None, help="défaut : workspace/.sys/.validation/cost-{n}.json")
    p.add_argument("--min-runs", type=int, default=MIN_RUNS, help=f"runs minimum pour qu'un p95 soit une mesure (défaut {MIN_RUNS})")
    add_common_args(p)
    return p


def load_ir(root: Path, args: argparse.Namespace, report: Report) -> dict[str, Any] | None:
    if args.ir:
        ir_file: Path | None = args.ir if args.ir.is_absolute() else root / args.ir
        why = f"IR `{args.ir}` introuvable"
    else:
        ir_file, why = eval_reports.find_ir_file(root, args.mission)
    if ir_file is None or not ir_file.is_file():
        report.error("IR_NOT_FOUND", why, "compiler l'IR : python .sdda/sdda.py ir-compiler --mission {n}", str(paths.ir_dir(root)))
        return None
    try:
        return json.loads(markdown_io.read_text(ir_file))
    except ValueError:
        report.error("IR_SCHEMA_INVALID", f"IR illisible : {paths.rel(root, ir_file)}", "recompiler l'IR", paths.rel(root, ir_file))
        return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="COST-REPORT", target=str(root))
    ir = load_ir(root, args, report)
    if ir is None:
        return finish(report, args)
    traces = args.traces if args.traces else paths.traces_dir(root)
    traces = traces if traces.is_absolute() else root / traces
    sub, payload = run(root, ir, traces, min_runs=args.min_runs)
    report.extend(sub)
    report.target = sub.target
    payload["findings"] = {"errors": [f.to_dict() for f in report.errors], "warnings": [f.to_dict() for f in report.warnings]}
    if not args.no_report:
        out = args.out or paths.validation_dir(root) / f"cost-{eval_reports.mission_number(ir) or 'system'}.json"
        out = out if out.is_absolute() else root / out
        atomic_write_json(out, payload)
        report.data["written"] = paths.rel(root, out)
    if payload.get("costUsd"):
        c, lat = payload["costUsd"], payload["latencyMs"]
        report.data["summary"] = {"runs": payload["runs"], "costMean": c["mean"], "costP95": c["p95"], "latencyP95": lat["p95"]}
        if not args.json:
            print(f"  {payload['runs']} runs — coût mean ${c['mean']:.4f} · p95 ${c['p95']:.4f} · max ${c['max']:.4f} ; "
                  f"latence p95 {lat['p95']:.0f} ms ; hops max {payload['hops']['max']:.0f}")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
