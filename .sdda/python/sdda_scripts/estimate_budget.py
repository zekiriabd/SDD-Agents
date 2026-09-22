#!/usr/bin/env python3
"""G2 (budget) — coût et latence ESTIMÉS sur le graphe de l'IR (0 token).

Deux chemins (budget-and-loop.md §2) :

- **nominal** : chaque nœud visité au plus une fois, un appel LLM par agent —
  le plus coûteux des chemins simples depuis `entryNode` (plus long chemin sur
  le DAG des composantes fortement connexes) ;
- **pire cas** : la marche de `maxHops` hops qui coûte le plus, chaque visite
  d'agent poussée à `maxIterations` appels, plafonnée par son `budgetUsd` et
  son `timeoutSec` ; chaque outil à son `timeoutSec` × (1 + retries).

Résultat écrit dans `budget.estimated` de l'IR (le seul champ qu'un script
autre que le compilateur a le droit d'y écrire) et comparé aux cibles de la
MISSION :

    pire cas > costPerRunHardCapUsd   -> [BUDGET_EXCEEDED_ESTIMATE]  (bloquant)
    nominal  > costPerRunTargetUsd    -> WARN [BUDGET_TARGET_MISSED]
    pire cas > tokenCeilingPerRun     -> WARN [TOKEN_CEILING_EXCEEDED] (le plafond coupera le run)
    p95 estimé > latencyP95TargetMs   -> WARN [LATENCY_P95_EXCEEDED]

HYPOTHÈSES DE PLANIFICATION — pas des mesures, la G6 mesure. Elles sont
volontairement visibles et constantes pour que deux topologies soient
comparables : tokens d'entrée et de sortie par tier (`TIER_INPUT_TOKENS`,
`TIER_OUTPUT_TOKENS`), auxquels s'ajoute la taille réelle du prompt système
(fichier lu, ~4 caractères par token). Le modèle est résolu depuis
`STACK.md ## Runtime Models` (`RuntimeTierMap`), jamais nommé dans l'IR (P11).

Bypass (INVARIANTS budget-estimated-before-code) : `SDDA_BYPASS_BUDGET_ESTIMATE=1`
avec `SDDA_BYPASS_REASON` — audit-loggué dans `.sys/.audit/bypasses.jsonl` (R5).

Usage :
    python estimate_budget.py --ir workspace/.sys/.ir/1-system.ir.json [--json] [--no-write]
    python estimate_budget.py --mission 1

Rapport : `G2-{missionId}.budget.json`.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths, pricing  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import append_bypass_audit, write_gate_report  # noqa: E402
from sdda_lib.graph import Graph  # noqa: E402
from sdda_lib.layered_config import LayeredConfig, read_stack_section_kv  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, finish, load_config, resolve_root  # noqa: E402
from sdda_scripts.validate_ir import source_pins  # noqa: E402

#: Tokens par appel LLM, hors prompt système (contexte conversationnel + entrée).
TIER_INPUT_TOKENS: dict[str, int] = {"fast": 1500, "balanced": 4000, "deep": 8000}
#: Tokens de sortie par appel LLM.
TIER_OUTPUT_TOKENS: dict[str, int] = {"fast": 150, "balanced": 600, "deep": 1200}
#: Un appel d'outil en nominal dure cette fraction de son timeout ; en pire cas, le timeout entier.
TOOL_NOMINAL_TIMEOUT_RATIO = 0.2
#: Latence d'une requête de retrieval (nominal, pire cas), ms.
RETRIEVER_LATENCY_MS: tuple[int, int] = (300, 1500)
#: Tokens d'embedding d'une requête.
QUERY_EMBED_TOKENS = 50
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Visit:
    """Coût d'UNE visite d'un nœud."""
    cost_usd: float
    latency_ms: float
    tokens: int

    def __add__(self, other: "Visit") -> "Visit":
        return Visit(self.cost_usd + other.cost_usd, self.latency_ms + other.latency_ms, self.tokens + other.tokens)

    def better_than(self, other: "Visit") -> bool:
        return (self.cost_usd, self.latency_ms, self.tokens) > (other.cost_usd, other.latency_ms, other.tokens)


ZERO = Visit(0.0, 0.0, 0)


def _prompt_tokens(root: Path | None, agent: dict[str, Any]) -> int:
    if root is None:
        return 0
    p = paths.resolve_rel(root, str(agent.get("promptRef", "")))
    if not p.is_file():
        return 0
    return len(p.read_text(encoding="utf-8-sig", errors="replace")) // CHARS_PER_TOKEN


def _retries(retry_policy: Any) -> int:
    s = str(retry_policy or "none")
    if ":" in s:
        tail = s.rsplit(":", 1)[1]
        return int(tail) if tail.isdigit() else 0
    return 0


def node_visits(ir: dict[str, Any], *, root: Path | None, tier_map: dict[str, str]) -> tuple[dict[str, Visit], dict[str, Visit], list[str]]:
    """(visite nominale, visite pire cas) par nœud, + avertissements de tarification."""
    agents = {a["id"]: a for a in ir.get("agents", [])}
    tools = {t["id"]: t for t in ir.get("tools", [])}
    retrievers = {r["id"]: r for r in ir.get("retrievers", []) or []}
    nominal: dict[str, Visit] = {}
    worst: dict[str, Visit] = {}
    warnings: list[str] = []
    for node in ir.get("orchestration", {}).get("nodes", []):
        nid, kind, ref = node["id"], node.get("kind"), str(node.get("ref", ""))
        if kind == "agent" and ref in agents:
            a = agents[ref]
            tier = str(a.get("modelTier", "balanced"))
            model = pricing.resolve_model(tier, tier_map)
            if not pricing.has_known_pricing(model):
                warnings.append(f"modèle `{model}` (tier {tier}) absent de la table de prix : repli Sonnet")
            in_tok = TIER_INPUT_TOKENS.get(tier, TIER_INPUT_TOKENS["balanced"]) + _prompt_tokens(root, a)
            out_tok = TIER_OUTPUT_TOKENS.get(tier, TIER_OUTPUT_TOKENS["balanced"])
            call = Visit(pricing.estimate_cost_usd(model, in_tok, out_tok), pricing.estimate_latency_ms(tier, out_tok), in_tok + out_tok)
            bounds = a.get("bounds", {})
            iters = max(1, int(bounds.get("maxIterations", 1)))
            budget = float(bounds.get("budgetUsd", call.cost_usd * iters))
            timeout_ms = float(bounds.get("timeoutSec", 0)) * 1000 or call.latency_ms * iters
            # Le nombre d'itérations réellement payables est plafonné par le budget de l'agent.
            eff_iters = iters if call.cost_usd <= 0 else min(iters, budget / call.cost_usd)
            nominal[nid] = call
            worst[nid] = Visit(round(min(call.cost_usd * iters, budget), 6), min(call.latency_ms * iters, timeout_ms), int(round(call.tokens * eff_iters)))
        elif kind == "tool" and ref in tools:
            t = tools[ref]
            timeout_ms = float(t.get("timeoutSec", 0)) * 1000
            nominal[nid] = Visit(0.0, timeout_ms * TOOL_NOMINAL_TIMEOUT_RATIO, 0)
            worst[nid] = Visit(0.0, timeout_ms * (1 + _retries(t.get("retryPolicy"))), 0)
        elif kind == "retriever" and ref in retrievers:
            # Le modèle d'embedding est un composant : il vit dans `binding`,
            # avec le store et le chunking. L'estimation de budget est le seul
            # contrôle qui a légitimement besoin d'y descendre — elle chiffre
            # ce que coûte la réalisation, pas ce qu'exige l'intention.
            emb = str((retrievers[ref].get("binding") or {}).get("embeddingModel", ""))
            cost = pricing.estimate_cost_usd(emb, QUERY_EMBED_TOKENS, 0) if pricing.has_known_pricing(emb) else 0.0
            nominal[nid] = Visit(cost, RETRIEVER_LATENCY_MS[0], QUERY_EMBED_TOKENS)
            worst[nid] = Visit(cost, RETRIEVER_LATENCY_MS[1], QUERY_EMBED_TOKENS)
        else:
            nominal[nid] = worst[nid] = ZERO
    return nominal, worst, warnings


def nominal_path(ir: dict[str, Any], visits: dict[str, Visit]) -> tuple[Visit, list[str]]:
    """Plus coûteux des chemins simples depuis `entryNode` (DAG des SCC, chaque nœud une fois)."""
    orch = ir.get("orchestration", {})
    nodes = [n["id"] for n in orch.get("nodes", [])]
    g = Graph(nodes, [(e["from"], e["to"]) for e in orch.get("edges", []) if e.get("from") in visits and e.get("to") in visits])
    entry = orch.get("entryNode")
    if entry not in g.adj:
        return ZERO, []
    dag, mapping = g.condensation()
    members: dict[str, list[str]] = {}
    for nid, rep in mapping.items():
        members.setdefault(rep, []).append(nid)
    weights = {rep: sum(visits[n].cost_usd for n in ms) for rep, ms in members.items()}
    _, path = dag.longest_path(weights, start=mapping[entry])
    total = ZERO
    expanded: list[str] = []
    for rep in path:
        for n in sorted(members[rep]):
            total = total + visits[n]
            expanded.append(n)
    return total, expanded


def worst_walk(ir: dict[str, Any], visits: dict[str, Visit]) -> tuple[Visit, list[str]]:
    """Marche de coût maximal en <= maxHops hops depuis `entryNode` (programmation dynamique).

    Une arête `countsAsHop: false` ne consomme pas de hop ; un cycle formé
    uniquement de telles arêtes (refusé par validate_ir.py) est coupé ici par
    un garde de récursion pour que l'estimation termine toujours.
    """
    orch = ir.get("orchestration", {})
    entry = orch.get("entryNode")
    max_hops = int(orch.get("maxHops", 0) or 0)
    out_edges: dict[str, list[dict[str, Any]]] = {}
    for e in orch.get("edges", []):
        if e.get("from") in visits and e.get("to") in visits:
            out_edges.setdefault(e["from"], []).append(e)
    if entry not in visits:
        return ZERO, []
    memo: dict[tuple[str, int], tuple[Visit, list[str]]] = {}
    in_progress: set[tuple[str, int]] = set()

    def best(node: str, hops_left: int) -> tuple[Visit, list[str]] | None:
        key = (node, hops_left)
        if key in memo:
            return memo[key]
        if key in in_progress:
            return None
        in_progress.add(key)
        here = visits[node]
        result: tuple[Visit, list[str]] = (here, [node])
        for e in sorted(out_edges.get(node, []), key=lambda x: (x["to"], x.get("condition", ""))):
            counts = e.get("countsAsHop", True) is not False
            if counts and hops_left == 0:
                continue
            sub = best(e["to"], hops_left - 1 if counts else hops_left)
            if sub is None:
                continue
            cand = (here + sub[0], [node] + sub[1])
            if cand[0].better_than(result[0]):
                result = cand
        in_progress.discard(key)
        memo[key] = result
        return result

    res = best(entry, max_hops)
    return res if res else (ZERO, [])


def estimate(ir: dict[str, Any], *, root: Path | None = None, config: LayeredConfig | None = None, ir_location: str = "<ir>") -> tuple[Report, dict[str, Any]]:
    """Estime nominal + pire cas ; renvoie (rapport, bloc `budget.estimated`)."""
    mid = str(ir.get("missionId", ""))
    report = Report(name="G2.budget", target=mid or ir_location)
    loc = ir_location
    tier_map_raw = read_stack_section_kv(root, "Runtime Models").get("RuntimeTierMap") if root else None
    tier_map = {str(k): str(v) for k, v in tier_map_raw.items()} if isinstance(tier_map_raw, dict) else {}
    if not tier_map:
        report.warn("BUDGET_PRICING_STALE", "`RuntimeTierMap` absent de STACK.md : tiers résolus avec la table par défaut", "", loc)
    stale = pricing.pricing_staleness_warning()
    if stale:
        report.warn("BUDGET_PRICING_STALE", stale.split("] ", 1)[1], "", loc)

    nominal_v, worst_v, warns = node_visits(ir, root=root, tier_map=tier_map)
    for w in warns:
        report.warn("BUDGET_PRICING_STALE", w, "", loc)
    nominal, npath = nominal_path(ir, nominal_v)
    worst, wpath = worst_walk(ir, worst_v)
    estimated = {
        "nominalCostUsd": round(nominal.cost_usd, 6),
        "worstCaseCostUsd": round(worst.cost_usd, 6),
        "nominalLatencyMs": int(round(nominal.latency_ms)),
        "worstCaseLatencyMs": int(round(worst.latency_ms)),
        "worstCasePath": wpath,
    }
    report.data = {"missionId": mid, "estimated": estimated, "nominalPath": npath, "nominalTokens": nominal.tokens, "worstCaseTokens": worst.tokens,
                   "tierMap": tier_map, "assumptions": {"tierInputTokens": TIER_INPUT_TOKENS, "tierOutputTokens": TIER_OUTPUT_TOKENS}}

    budget = ir.get("budget", {})
    hard_cap = budget.get("costPerRunHardCapUsd")
    target = budget.get("costPerRunTargetUsd")
    p95 = budget.get("latencyP95TargetMs")
    ceiling = budget.get("tokenCeilingPerRun")
    bypass = os.environ.get("SDDA_BYPASS_BUDGET_ESTIMATE", "").strip().lower() in ("1", "true", "yes", "on")
    reason = os.environ.get("SDDA_BYPASS_REASON", "").strip()

    def blocking(cls: str, msg: str, fix: str) -> None:
        if bypass and reason and root is not None:
            append_bypass_audit(root, "G2.budget", f"[{cls}] {reason}")
            report.warn(cls, f"{msg} — BYPASS audité ({reason})", fix, loc)
        elif bypass and not reason:
            report.error("BYPASS_REASON_MISSING", "SDDA_BYPASS_BUDGET_ESTIMATE=1 sans SDDA_BYPASS_REASON", "un bypass est nominatif, borné et motivé (R5)", loc)
            report.error(cls, msg, fix, loc)
        else:
            report.error(cls, msg, fix, loc)

    if isinstance(hard_cap, (int, float)) and worst.cost_usd > hard_cap + 1e-9:
        blocking("BUDGET_EXCEEDED_ESTIMATE", f"pire cas {worst.cost_usd:.4f} USD/run > costPerRunHardCapUsd {hard_cap} (chemin {' -> '.join(wpath)})",
                 "retirer un agent, abaisser un tier, resserrer maxHops/maxIterations/budget_usd, ou déplacer un jugement vers un outil déterministe")
    if isinstance(target, (int, float)) and nominal.cost_usd > target + 1e-9:
        report.warn("BUDGET_TARGET_MISSED", f"nominal {nominal.cost_usd:.4f} USD/run > costPerRunTargetUsd {target}", "la cible est manquée avant même le pire cas", loc)
    # Le plafond de tokens est lui-même une borne du système généré : un pire cas
    # au-dessus sera COUPÉ par lui (comportement OnBoundExceeded), pas dépassé.
    # C'est une information de dimensionnement, pas un blocage (seul le coût bloque).
    if isinstance(ceiling, int) and worst.tokens > ceiling:
        report.warn("TOKEN_CEILING_EXCEEDED", f"pire cas {worst.tokens} tokens/run > tokenCeilingPerRun {ceiling} : le plafond coupera le run avant maxHops", "résumer au lieu d'accumuler, ou resserrer maxIterations", loc)
    elif isinstance(ceiling, int) and nominal.tokens > ceiling:
        report.warn("TOKEN_CEILING_EXCEEDED", f"nominal {nominal.tokens} tokens/run > tokenCeilingPerRun {ceiling} : même le chemin nominal sera coupé", "", loc)
    if isinstance(p95, int):
        if worst.latency_ms > p95:
            report.warn("LATENCY_P95_EXCEEDED", f"pire cas {int(worst.latency_ms)} ms > latencyP95TargetMs {p95} : dire quelle borne le ramène sous la cible", "", loc)
        elif nominal.latency_ms > p95:
            report.warn("LATENCY_P95_EXCEEDED", f"nominal {int(nominal.latency_ms)} ms > latencyP95TargetMs {p95}", "", loc)
    return report, estimated


def estimate_file(path: Path, root: Path, config: LayeredConfig | None, *, write_report: bool = True, write_ir: bool = True) -> Report:
    loc = paths.rel(root, path)
    if not path.is_file():
        r = Report(name="G2.budget", target=loc)
        r.error("IR_NOT_FOUND", f"IR `{loc}` introuvable", "compiler : python .sdda/python/sdda_scripts/ir_compiler.py", loc)
        return r
    ir = ir_compiler.load_ir(path)
    report, estimated = estimate(ir, root=root, config=config, ir_location=loc)
    if write_ir and isinstance(ir.get("budget"), dict):
        ir["budget"]["estimated"] = estimated
        path.write_bytes(ir_compiler.dump_ir(ir))
    if write_report and ir.get("missionId"):
        write_gate_report(root, "G2", str(ir["missionId"]), report, source_pins(root, ir), part="budget")
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="G2 (budget) — coût et latence estimés sur le graphe de l'IR : nominal et pire cas")
    p.add_argument("--ir", type=Path, default=None, help="fichier IR ; défaut : tous les IR compilés")
    p.add_argument("--mission", type=int, default=None)
    p.add_argument("--no-write", action="store_true", help="ne pas écrire `budget.estimated` dans l'IR")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    combined = Report(name="G2.budget", target=str(root))
    config = load_config(root, combined)
    if args.ir:
        files = [args.ir if args.ir.is_absolute() else root / args.ir]
    elif args.mission is not None:
        files = [paths.ir_path(root, args.mission)]
    else:
        files = sorted(paths.ir_dir(root).glob("*-system.ir.json"))
    if not files:
        combined.error("IR_NOT_FOUND", "aucun IR compilé dans workspace/.sys/.ir/", "compiler d'abord", str(paths.ir_dir(root)))
    for f in files:
        combined.extend(estimate_file(f, root, config, write_report=not args.no_report, write_ir=not args.no_write))
    return finish(combined, args)


if __name__ == "__main__":
    sys.exit(main())
