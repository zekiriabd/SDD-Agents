#!/usr/bin/env python3
"""Trajectoires observées contre le graphe déclaré — le système jugé sur la trace.

Une réponse correcte obtenue par une trajectoire aberrante est un faux vert :
elle coûte dix fois le budget et cassera au prochain changement de prompt.
`review-orchestration` le cherche ; ce script reconstruit ce qu'il lit, depuis
l'arbre des spans (`parent_span_id`) et sans LLM :

    chemin        la suite des nœuds traversés (deux tours consécutifs d'un même
                  agent sont UN passage : `_run_traces.visits`)
    arêtes        chaque transition confrontée au graphe de l'IR. Les nœuds non
                  agents (`function`) n'émettent pas de span : une transition
                  a -> b est admise si l'IR relie a à b en ne traversant QUE des
                  nœuds invisibles. Sinon : [TRAJECTORY_VIOLATION]
    fin           un run terminé en erreur, ou sur un nœud d'où aucun chemin
                  invisible ne mène à un `terminalNode` : [TRAJECTORY_DEAD_END]
    bornes        hops > `maxHops` : [UNBOUNDED_LOOP] ; borne atteinte sans le
                  comportement déclaré (`onBoundExceeded`) : [BOUND_BEHAVIOR_MISMATCH]
    boucles       nœuds revisités et longueur des cycles ; x,y,x,y : [ORCH_PING_PONG]
    handoffs      transition entre agents sans `handoffs[].to` déclaré : [HANDOFF_UNCONTRACTED]
    routage       matrice de confusion PAR CLASSE quand l'item du jeu porte
                  `expected_class` ou `expected_trajectory.route` (l'identifiant
                  de run `{item}-{k}` rattache la trace à l'item) ; un misroute
                  vers un agent à outil non `read-only` : [MISROUTE_TO_DESTRUCTIVE]
    couverture    nœuds jamais atteints [NODE_UNREACHED], arêtes jamais
                  empruntées, repli jamais emprunté [ROUTER_FALLBACK_UNTESTED],
                  superviseur à un seul spécialiste effectif [TOPOLOGY_REDUNDANT_HOP]
    distribution  chemins distincts et fréquences, hops (p50/p95/max), queue au-
                  dessus du p95, séquences d'outils par agent

Ce que le script ne voit pas : l'état transmis dans un handoff (le schéma du
§13 se confronte dans le rapport de l'agent), le delta d'état entre deux
passages d'un ping-pong. Il nomme la forme ; l'agent tranche le fond.

Usage :
    python .sdda/sdda.py trajectory-report --mission 1 --traces workspace/.sys/traces/runs \\
      --ir workspace/.sys/.ir/1-system.ir.json --out workspace/.sys/.validation/trajectories-1.json
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, deque
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import eval_reports, paths, tracing  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.runtime_io import atomic_write_json, now_iso  # noqa: E402
from sdda_scripts import _run_traces as rt  # noqa: E402
from sdda_scripts import run_retrieval_eval  # noqa: E402
from sdda_scripts._common import add_common_args, finish, resolve_root  # noqa: E402
from sdda_scripts.cost_report import MIN_RUNS, load_ir  # noqa: E402

LIST_CAP = 20
_LITERAL_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")
SUPERVISOR_PATTERNS = frozenset({"supervisor", "hierarchical", "router"})


# ---------------------------------------------------------------------------
# Le graphe de l'IR, projeté sur ce qu'une trace peut voir
# ---------------------------------------------------------------------------
class Graph:
    """Le graphe d'orchestration vu depuis les traces : seuls les nœuds agents ont des spans."""

    def __init__(self, ir: dict[str, Any]):
        orch = ir.get("orchestration") or {}
        self.entry = str(orch.get("entryNode") or "")
        self.max_hops = orch.get("maxHops")
        self.pattern = str(orch.get("rootPattern") or "")
        self.terminals = {str(t) for t in orch.get("terminalNodes") or []}
        self.nodes = {str(n.get("id")): n for n in orch.get("nodes") or [] if n.get("id")}
        self.edges = [e for e in orch.get("edges") or [] if e.get("from") and e.get("to")]
        self.visible = {nid for nid, n in self.nodes.items() if str(n.get("kind") or "") == "agent"}
        self.agents = {str(a.get("id")): a for a in ir.get("agents") or []}
        self.projected: dict[tuple[str, str], bool] = {}     # (a, b) -> atteint par une arête de repli
        self.can_end: set[str] = set(self.terminals)
        #: Par nœud visible : les chemins invisibles qui mènent à un terminal
        #: partent-ils d'une arête de repli ({True}), d'une arête normale
        #: ({False}), ou des deux ? Un run qui s'arrête sur un nœud dont la
        #: SEULE fin est le repli a emprunté le repli.
        self.end_modes: dict[str, set[bool]] = {}
        for a in self.visible | {self.entry}:
            for b, fallback, ends in self._reach(a):
                if b is not None:
                    self.projected.setdefault((a, b), fallback)
                elif ends:
                    self.can_end.add(a)
                    self.end_modes.setdefault(a, set()).add(fallback)

    def _reach(self, start: str) -> list[tuple[str | None, bool, bool]]:
        """Nœuds visibles atteignables depuis `start` par des nœuds invisibles seulement."""
        out: list[tuple[str | None, bool, bool]] = []
        queue: deque[tuple[str, bool]] = deque()
        for e in self.edges:
            if str(e["from"]) == start:
                queue.append((str(e["to"]), bool(e.get("isFallback"))))
        seen: set[str] = set()
        while queue:
            node, fallback = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            if node in self.visible:
                out.append((node, fallback, node in self.terminals))
                continue
            out.append((None, fallback, node in self.terminals))
            queue.extend((str(e["to"]), fallback) for e in self.edges if str(e["from"]) == node)
        return out

    def fallback_from(self, node: str) -> str | None:
        return next((str(e["to"]) for e in self.edges if str(e["from"]) == node and e.get("isFallback")), None)

    def class_targets(self, node: str) -> dict[str, str]:
        """Classe -> nœud, lue dans les conditions des arêtes (`intent == 'billing'`)."""
        out: dict[str, str] = {}
        for e in self.edges:
            if str(e["from"]) != node or e.get("isFallback"):
                continue
            for a, b in _LITERAL_RE.findall(str(e.get("condition") or "")):
                out.setdefault(a or b, str(e["to"]))
        return out

    def agent_of(self, node: str) -> dict[str, Any]:
        ref = str((self.nodes.get(node) or {}).get("ref") or "")
        return self.agents.get(ref) or {}

    def destructive(self, node: str, tools: dict[str, dict[str, Any]]) -> list[str]:
        return sorted(t for t in self.agent_of(node).get("tools") or []
                      if str((tools.get(t) or {}).get("sideEffectClass") or "read-only") != "read-only")


# ---------------------------------------------------------------------------
# Un run
# ---------------------------------------------------------------------------
def ping_pongs(path: list[str]) -> int:
    return sum(1 for i in range(len(path) - 3)
               if path[i] == path[i + 2] and path[i + 1] == path[i + 3] and path[i] != path[i + 1])


def cycle_lengths(path: list[str]) -> list[int]:
    last: dict[str, int] = {}
    out: list[int] = []
    for i, node in enumerate(path):
        if node in last:
            out.append(i - last[node])
        last[node] = i
    return out


def analyse_run(run: rt.RunTrace, graph: Graph, nodes: dict[str, str]) -> dict[str, Any]:
    visits = rt.visits(run)
    path = [nodes.get(k, k) for k, _ in visits]
    violations: list[str] = []
    # Le premier passage est l'entrée — ou, si l'entrée est un nœud invisible,
    # un nœud qu'elle atteint sans traverser d'agent.
    if path and graph.entry:
        entry_ok = (path[0] == graph.entry) if graph.entry in graph.visible else (graph.entry, path[0]) in graph.projected
        if not entry_ok:
            violations.append(f"(entrée {graph.entry}) -> {path[0]}")
    transitions = list(zip(path, path[1:]))
    violations += [f"{a} -> {b}" for a, b in transitions if (a, b) not in graph.projected]
    unknown = sorted({n for n in path if n not in graph.nodes})

    uncontracted: list[str] = []
    for a, b in transitions:
        if (a, b) in graph.projected and a in graph.visible and b in graph.visible:
            if not any(str(h.get("to") or "") == b for h in graph.agent_of(a).get("handoffs") or []):
                uncontracted.append(f"{a} -> {b}")

    # Borne atteinte : le comportement observé est-il celui déclaré ?
    # `fail-explicit` se lit dans le statut ERROR du span (otel-genai.md §3.1),
    # `degrade` / `escalate-human` dans un statut OK — sauf si la trace dit
    # explicitement la politique appliquée, qui prime.
    bounds: list[dict[str, Any]] = []
    for span in run.agents():
        attrs = tracing.attributes_of(span)
        name = attrs.get(tracing.A_BOUND_EXCEEDED)
        if not isinstance(name, str) or not name:
            continue
        node = nodes.get(rt.agent_key(span), rt.agent_key(span))
        declared = str(graph.agent_of(node).get("onBoundExceeded") or "")
        applied = str(attrs.get(rt.A_BOUND_POLICY_APPLIED) or attrs.get(rt.A_BOUND_POLICY) or "")
        status = str(span.get("status") or "OK").upper()
        if applied:
            ok = applied == declared
        else:
            ok = (status == "ERROR") if declared == "fail-explicit" else (status != "ERROR")
        bounds.append({"node": node, "bound": name, "declared": declared, "applied": applied or None,
                       "status": status, "matches": ok})

    last = path[-1] if path else ""
    ended_ok = run.status_ok and (last in graph.can_end or last in graph.terminals)
    fallback_taken = bool(last) and run.status_ok and graph.end_modes.get(last) == {True}
    route = path[1] if len(path) > 1 else (graph.fallback_from(path[0]) if fallback_taken else None)

    tool_seqs: dict[str, list[str]] = {}
    for s in run.spans:
        if run.role(s) != "tool":
            continue
        owner = run.owning_agent(s)
        node = nodes.get(rt.agent_key(owner), rt.agent_key(owner)) if owner else "(hors agent)"
        tool_seqs.setdefault(node, []).append(tracing._tool_name(s, tracing.attributes_of(s)))
    return {
        "runId": run.run_id, "item": rt.item_of(run.run_id), "path": path, "hops": len(path),
        "violations": violations, "unknownNodes": unknown, "uncontracted": uncontracted,
        "bounds": bounds, "endedOk": ended_ok, "fallbackTaken": fallback_taken, "route": route,
        "pingPong": ping_pongs(path), "cycles": cycle_lengths(path),
        "toolSequences": {node: " > ".join(seq) for node, seq in tool_seqs.items()},
    }


# ---------------------------------------------------------------------------
# Agrégation et constats
# ---------------------------------------------------------------------------
def expected_routes(root: Path, dataset: Path | None, graph: Graph) -> dict[str, tuple[str, str]]:
    """Item -> (classe attendue, nœud attendu), depuis les jeux golden (et `--dataset`)."""
    files = [dataset if dataset.is_absolute() else root / dataset] if dataset else []
    golden = paths.datasets_dir(root, "golden")
    files += sorted(golden.glob("*.jsonl")) if golden.is_dir() else []
    targets = graph.class_targets(graph.entry)
    fallback = graph.fallback_from(graph.entry)
    out: dict[str, tuple[str, str]] = {}
    for f in files:
        if not f.is_file():
            continue
        for item in run_retrieval_eval.load_items(f):
            route = str((item.get("expected_trajectory") or {}).get("route") or "")
            cls = str(item.get("expected_class") or route or "")
            if not cls:
                continue
            out.setdefault(str(item.get("id")), (cls, route or targets.get(cls) or fallback or "?"))
    return out


def run(root: Path, ir: dict[str, Any], traces: Path, *, min_runs: int = MIN_RUNS,
        dataset: Path | None = None) -> tuple[Report, dict[str, Any]]:
    mid = str(ir.get("missionId") or "")
    report = Report(name="TRAJECTORY-REPORT", target=mid or str(root))
    loc = paths.rel(root, traces)
    graph = Graph(ir)
    nodes = rt.node_index(ir)
    loaded = rt.load_runs(traces, eval_reports.mission_number(ir) or None, mid)
    rows = [analyse_run(r, graph, nodes) for r in loaded.runs]
    payload: dict[str, Any] = {"missionId": mid, "generatedAt": now_iso(), "traces": loc, "runsRead": len(rows),
                               "graph": {"entry": graph.entry, "maxHops": graph.max_hops, "pattern": graph.pattern,
                                         "projectedEdges": sorted(f"{a} -> {b}" for a, b in graph.projected)}}
    if len(rows) < min_runs:
        report.error("MEASUREMENT_MISSING", f"{len(rows)} run(s) tracé(s) (< {min_runs}) : une distribution de trajectoires sur si peu de runs n'est pas une mesure",
                     "exécuter les suites L5/L7 (k runs) avant la revue d'orchestration", loc)
    if not rows:
        return report, payload

    paths_seen = Counter(" > ".join(r["path"]) for r in rows)
    hops = [float(r["hops"]) for r in rows]
    p95 = rt.percentile(hops, 0.95)
    observed_edges = Counter(f"{a} -> {b}" for r in rows for a, b in zip(r["path"], r["path"][1:]))
    visited = {n for r in rows for n in r["path"]}

    for r in rows:
        if r["violations"] or r["unknownNodes"]:
            report.error("TRAJECTORY_VIOLATION", f"run `{r['runId']}` : transition(s) hors IR {r['violations'] or ''}"
                         + (f" ; nœud(s) inconnu(s) de l'IR {r['unknownNodes']}" if r["unknownNodes"] else ""),
                         "le code fait ce que la spec n'a pas validé : dev-orchestration (code) ou architect-topology (graphe)", r["runId"])
        if graph.max_hops is not None and r["hops"] > graph.max_hops:
            report.error("UNBOUNDED_LOOP", f"run `{r['runId']}` : {r['hops']} passages > maxHops {graph.max_hops} ({' > '.join(r['path'])})",
                         "la borne n'existe pas en code : dev-orchestration la matérialise", r["runId"])
        if not r["endedOk"]:
            report.warn("TRAJECTORY_DEAD_END", f"run `{r['runId']}` terminé hors d'un nœud terminal (dernier : `{r['path'][-1] if r['path'] else '?'}`)",
                        "timeout, exception ou état incohérent : lire le span racine et le dernier agent", r["runId"])
        for b in r["bounds"]:
            if not b["matches"]:
                report.warn("BOUND_BEHAVIOR_MISMATCH", f"run `{r['runId']}` : `{b['bound']}` atteinte sur `{b['node']}`, déclaré `{b['declared']}`, observé "
                            f"{b['applied'] or 'statut ' + b['status']}", "le comportement de borne doit être celui de onBoundExceeded", r["runId"])
    for edge in sorted({e for r in rows for e in r["uncontracted"]}):
        report.warn("HANDOFF_UNCONTRACTED", f"transition `{edge}` observée sans `handoffs[].to` déclaré sur l'agent source",
                    "déclarer le handoff (§13 du contrat) ou retirer l'arête", loc)
    pp = [r["runId"] for r in rows if r["pingPong"]]
    if pp:
        report.warn("ORCH_PING_PONG", f"{len(pp)} run(s) avec aller-retour x > y > x > y ({', '.join(pp[:5])})",
                    "vérifier le delta d'état entre deux passages ; sans progression, c'est une boucle", loc)
    for node in sorted(graph.visible - visited):
        report.warn("NODE_UNREACHED", f"nœud `{node}` jamais atteint sur {len(rows)} runs",
                    "nœud mort, ou golden incomplet : à trancher avec qa-evals", node)
    fallbacks = [e for e in graph.edges if e.get("isFallback")]
    taken = sum(1 for r in rows if r["fallbackTaken"])
    if fallbacks and not taken:
        report.warn("ROUTER_FALLBACK_UNTESTED", f"repli {[e['from'] + ' -> ' + e['to'] for e in fallbacks]} jamais emprunté sur {len(rows)} runs",
                    "ajouter au golden des items « aucune classe » ; un repli jamais exercé est un repli non testé", loc)
    if graph.pattern in SUPERVISOR_PATTERNS and graph.entry:
        declared_next = {b for (a, b) in graph.projected if a == graph.entry}
        effective = {r["path"][1] for r in rows if len(r["path"]) > 1 and r["path"][0] == graph.entry}
        if len(declared_next) > 1 and len(effective) == 1:
            report.warn("TOPOLOGY_REDUNDANT_HOP", f"`{graph.entry}` ({graph.pattern}) n'a routé que vers `{next(iter(effective))}` sur {len(rows)} runs "
                        f"(déclarés : {sorted(declared_next)})", "un orchestrateur à un seul spécialiste effectif est un hop de trop — ou un golden trop étroit", graph.entry)

    confusion: dict[str, Counter[str]] = {}
    expected = expected_routes(root, dataset, graph)
    tools = {str(t.get("id")): t for t in ir.get("tools") or []}
    for r in rows:
        exp = expected.get(r["item"])
        if not exp:
            continue
        cls, want = exp
        got = r["route"] or "(aucun)"
        confusion.setdefault(cls, Counter())[got] += 1
        if got != want and graph.destructive(got, tools):
            report.error("MISROUTE_TO_DESTRUCTIVE", f"run `{r['runId']}` : classe `{cls}` routée vers `{got}` (attendu `{want}`), qui porte {graph.destructive(got, tools)}",
                         "silencieux et irrécupérable : seuil de confiance du routeur, repli, et item permanent au golden", r["runId"])
    # Accuracy PAR CLASSE, jamais globale : 0.97 au total peut cacher 0.71 sur
    # la classe critique.
    target_of = {cls: want for cls, want in expected.values()}
    per_class: dict[str, Any] = {}
    for cls, counts in sorted(confusion.items()):
        n = sum(counts.values())
        per_class[cls] = {"n": n, "expected": target_of.get(cls, "?"),
                          "accuracy": round(counts.get(target_of.get(cls, "?"), 0) / n, 6),
                          "observed": dict(sorted(counts.items()))}

    payload.update({
        "runs": len(rows),
        "paths": [{"path": p, "runs": n, "share": round(n / len(rows), 6)} for p, n in paths_seen.most_common()],
        "distinctPaths": len(paths_seen),
        "hops": {**rt.distribution(hops, 0), "histogram": {str(int(h)): hops.count(h) for h in sorted(set(hops))}},
        "tail": [{"runId": r["runId"], "hops": r["hops"], "path": r["path"]}
                 for r in sorted(rows, key=lambda r: (-r["hops"], r["runId"])) if r["hops"] > p95][:LIST_CAP],
        "edgesObserved": dict(sorted(observed_edges.items())),
        "edgesNeverTaken": sorted(e for e in (f"{a} -> {b}" for a, b in graph.projected) if e not in observed_edges),
        "edgesOutsideIr": sorted({v for r in rows for v in r["violations"]}),
        "nodesUnreached": sorted(graph.visible - visited),
        "fallbackTaken": taken,
        "terminalReached": sum(1 for r in rows if r["endedOk"]),
        "boundsExceeded": dict(Counter(b["bound"] for r in rows for b in r["bounds"])),
        "cycles": {"runsWithCycle": sum(1 for r in rows if r["cycles"]),
                   "lengths": dict(sorted(Counter(str(c) for r in rows for c in r["cycles"]).items()))},
        "pingPongRuns": pp[:LIST_CAP],
        "routing": {"itemsMatched": sum(sum(c.values()) for c in confusion.values()), "perClass": per_class},
        "toolSequences": {node: dict(Counter(r["toolSequences"].get(node, "") for r in rows if node in r["toolSequences"]).most_common(10))
                          for node in sorted({n for r in rows for n in r["toolSequences"]})},
    })
    return report, payload


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Trajectoires reconstruites depuis l'arbre des spans, confrontées au graphe de l'IR (0 token)")
    p.add_argument("--mission", type=int, default=None, help="numéro de MISSION ; défaut : l'unique IR compilé")
    p.add_argument("--traces", type=Path, default=None, help="répertoire des traces ; défaut : workspace/.sys/traces/runs")
    p.add_argument("--ir", type=Path, default=None, help="fichier IR explicite")
    p.add_argument("--dataset", type=Path, default=None, help="jeu à `expected_class` / `expected_trajectory.route` (en plus des golden)")
    p.add_argument("--out", type=Path, default=None, help="défaut : workspace/.sys/.validation/trajectories-{n}.json")
    p.add_argument("--min-runs", type=int, default=MIN_RUNS, help=f"runs minimum (défaut {MIN_RUNS})")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="TRAJECTORY-REPORT", target=str(root))
    ir = load_ir(root, args, report)
    if ir is None:
        return finish(report, args)
    traces = args.traces if args.traces else paths.traces_dir(root)
    traces = traces if traces.is_absolute() else root / traces
    sub, payload = run(root, ir, traces, min_runs=args.min_runs, dataset=args.dataset)
    report.extend(sub)
    report.target = sub.target
    payload["findings"] = {"errors": [f.to_dict() for f in report.errors], "warnings": [f.to_dict() for f in report.warnings]}
    if not args.no_report:
        out = args.out or paths.validation_dir(root) / f"trajectories-{eval_reports.mission_number(ir) or 'system'}.json"
        out = out if out.is_absolute() else root / out
        atomic_write_json(out, payload)
        report.data["written"] = paths.rel(root, out)
    if payload.get("paths") and not args.json:
        print(f"  {payload['runs']} runs — {payload['distinctPaths']} chemin(s) distinct(s), hops p95 {payload['hops']['p95']:.0f} "
              f"(max {payload['hops']['max']:.0f}), {len(payload['edgesOutsideIr'])} transition(s) hors IR")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
