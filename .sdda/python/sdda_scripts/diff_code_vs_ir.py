#!/usr/bin/env python3
"""Le graphe codé est-il celui de l'IR ? — part `orchestration` de G6, 0 token.

`dev-orchestration` matérialise `orchestration` de l'IR : mêmes nœuds, mêmes
arêtes, mêmes conditions, même entrée, mêmes terminaux — « si le code et le
dessin divergent, c'est le code qui a tort ». Ce script est ce qui rend la
phrase vérifiable.

**Ce qu'il compare, et pourquoi pas le code lui-même.** Lire un graphe dans du
Python LangGraph, du C# Semantic Kernel ou du TypeScript demande un parseur par
framework — trois interprétations, exactement ce que l'IR existe pour éviter.
La convention est donc : le module d'orchestration **émet** un manifeste
machine, neutre framework, depuis le graphe RÉELLEMENT construit :

    workspace/src/**/orchestration/graph.manifest.json
    {
      "generatedBy": "orchestration.dump_graph",   # qui l'a produit
      "entryNode": "router",
      "terminalNodes": ["answer"],
      "nodes": [{"id": "router", "kind": "router"}, …],
      "edges": [{"from": "router", "to": "billing", "condition": "intent == 'billing'"}, …]
    }

Le manifeste est produit par le code (`dump_graph()` introspecte le graphe
compilé), **jamais recopié de l'IR à la main** : un manifeste copié rend la
comparaison tautologique, et `review-orchestration` le verrait aux
trajectoires observées. `generatedBy` dit d'où il vient.

Divergence (nœud ou arête en plus / en moins, condition différente, entrée ou
terminaux différents) → [ORCH_DIVERGES_FROM_IR], bloquant. Manifeste absent →
[ORCH_MANIFEST_MISSING] : rien n'est comparable, donc rien n'est vert.

Usage :
    python .sdda/sdda.py diff-code-vs-ir --mission 1 --scope orchestration
    python .sdda/sdda.py diff-code-vs-ir --mission 1 --scope orchestration --manifest path/to/graph.manifest.json --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.eval_reports import find_ir_file, load_json  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

MANIFEST_NAME = "graph.manifest.json"
SCOPES = ("orchestration",)


def find_manifests(root: Path) -> list[Path]:
    src = root / "workspace" / "src"
    if not src.is_dir():
        return []
    return sorted(p for p in src.rglob(MANIFEST_NAME) if p.parent.name == "orchestration")


def _norm_condition(value: Any) -> str:
    text = str(value if value is not None else "always").strip()
    return re.sub(r"\s+", " ", text) or "always"


def _edges(section: dict[str, Any]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for edge in section.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        key = (str(edge.get("from", "")), str(edge.get("to", "")))
        out[key] = _norm_condition(edge.get("condition"))
    return out


def _nodes(section: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in section.get("nodes") or []:
        if isinstance(node, dict) and node.get("id"):
            out[str(node["id"])] = str(node.get("kind") or "")
        elif isinstance(node, str):
            out[node] = ""
    return out


def diff_orchestration(ir_orch: dict[str, Any], manifest: dict[str, Any], report: Report, loc: str) -> dict[str, Any]:
    """Confronte les deux graphes ; chaque écart est un finding nommé."""
    ir_nodes, code_nodes = _nodes(ir_orch), _nodes(manifest)
    ir_edges, code_edges = _edges(ir_orch), _edges(manifest)

    missing_nodes = sorted(set(ir_nodes) - set(code_nodes))
    extra_nodes = sorted(set(code_nodes) - set(ir_nodes))
    kind_drift = sorted(n for n in set(ir_nodes) & set(code_nodes) if ir_nodes[n] and code_nodes[n] and ir_nodes[n] != code_nodes[n])
    missing_edges = sorted(set(ir_edges) - set(code_edges))
    extra_edges = sorted(set(code_edges) - set(ir_edges))
    cond_drift = sorted(e for e in set(ir_edges) & set(code_edges) if ir_edges[e] != code_edges[e])

    entry_ir, entry_code = str(ir_orch.get("entryNode", "")), str(manifest.get("entryNode", ""))
    term_ir = sorted(str(t) for t in ir_orch.get("terminalNodes") or [])
    term_code = sorted(str(t) for t in manifest.get("terminalNodes") or [])

    def fmt_edges(edges: list[tuple[str, str]]) -> str:
        return ", ".join(f"{a}->{b}" for a, b in edges[:6]) + (" …" if len(edges) > 6 else "")

    fix = ("le code a tort, pas l'IR : aligner workspace/src/**/orchestration/ sur `orchestration` de l'IR ; "
           "si l'IR est celui qui doit changer, éditer la topologie puis /sdda-topology {n} --recompile-only")
    if missing_nodes:
        report.error("ORCH_DIVERGES_FROM_IR", f"nœud(s) de l'IR absents du code : {', '.join(missing_nodes[:6])}", fix, loc)
    if extra_nodes:
        report.error("ORCH_DIVERGES_FROM_IR", f"nœud(s) codés hors IR : {', '.join(extra_nodes[:6])}", fix, loc)
    if kind_drift:
        report.error("ORCH_DIVERGES_FROM_IR", "kind différent pour : " + ", ".join(f"{n} (IR {ir_nodes[n]}, code {code_nodes[n]})" for n in kind_drift[:4]), fix, loc)
    if missing_edges:
        report.error("ORCH_DIVERGES_FROM_IR", f"arête(s) de l'IR absentes du code : {fmt_edges(missing_edges)}", fix, loc)
    if extra_edges:
        report.error("ORCH_DIVERGES_FROM_IR", f"arête(s) codées hors IR : {fmt_edges(extra_edges)}", fix, loc)
    if cond_drift:
        report.error("ORCH_DIVERGES_FROM_IR",
                     "condition différente sur : " + "; ".join(f"{a}->{b} (IR « {ir_edges[(a, b)]} », code « {code_edges[(a, b)]} »)" for a, b in cond_drift[:3]),
                     "la condition se prend TELLE QUE L'IR L'ÉCRIT — une reformulation est une décision non tracée", loc)
    if entry_ir != entry_code:
        report.error("ORCH_DIVERGES_FROM_IR", f"entrée : IR `{entry_ir}`, code `{entry_code or '<absente>'}`", fix, loc)
    if term_ir != term_code:
        report.error("ORCH_DIVERGES_FROM_IR", f"terminaux : IR {term_ir}, code {term_code}", fix, loc)

    generated_by = str(manifest.get("generatedBy") or "")
    if not generated_by:
        report.warn("ORCH_MANIFEST_UNATTRIBUTED", "le manifeste ne dit pas qui l'a produit (`generatedBy`)",
                    "le faire émettre par le code (`dump_graph()`), jamais écrire à la main : un manifeste copié de l'IR rend ce contrôle tautologique", loc)

    return {
        "nodes": {"ir": len(ir_nodes), "code": len(code_nodes), "missing": missing_nodes, "extra": extra_nodes, "kindDrift": kind_drift},
        "edges": {"ir": len(ir_edges), "code": len(code_edges), "missing": [f"{a}->{b}" for a, b in missing_edges],
                  "extra": [f"{a}->{b}" for a, b in extra_edges], "conditionDrift": [f"{a}->{b}" for a, b in cond_drift]},
        "entry": {"ir": entry_ir, "code": entry_code},
        "terminals": {"ir": term_ir, "code": term_code},
        "generatedBy": generated_by,
        "isomorphic": not report.errors,
    }


def run(root: Path, ir: dict[str, Any], *, scope: str, manifest_path: Path | None = None) -> Report:
    report = Report(name=f"DIFF.{scope}", target=str(ir.get("missionId", "")))
    if scope not in SCOPES:
        report.error("INVALID_ARG", f"scope `{scope}` inconnu", f"--scope parmi {list(SCOPES)}")
        return report

    if manifest_path is None:
        found = find_manifests(root)
        if not found:
            report.error("ORCH_MANIFEST_MISSING",
                         f"aucun `{MANIFEST_NAME}` sous workspace/src/**/orchestration/ — le graphe codé n'est pas comparable",
                         "faire émettre le manifeste par le module d'orchestration (`dump_graph()`, cf. fiche dev-orchestration STEP 6) ; "
                         "sans lui, la part `orchestration` de G6 reste absente, donc rouge", "workspace/src/")
            return report
        if len(found) > 1:
            report.error("ORCH_MANIFEST_MISSING", f"{len(found)} manifestes trouvés : {', '.join(paths.rel(root, p) for p in found[:3])}",
                         "un seul graphe d'orchestration par projet ; passer --manifest pour lever l'ambiguïté", "workspace/src/")
            return report
        manifest_path = found[0]

    loc = paths.rel(root, manifest_path)
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        report.error("ORCH_MANIFEST_MISSING", f"`{loc}` illisible ou absent", "régénérer le manifeste depuis le code", loc)
        return report

    ir_orch = ir.get("orchestration") or {}
    report.data.update({"manifest": loc, "diff": diff_orchestration(ir_orch, manifest, report, loc)})
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Le graphe codé est-il isomorphe à l'IR ? (0 token)")
    p.add_argument("--mission", type=int, default=None, help="numéro de mission ; défaut : l'unique IR compilé")
    p.add_argument("--scope", default="orchestration", help=f"ce qu'on compare : {', '.join(SCOPES)}")
    p.add_argument("--manifest", type=Path, default=None, help=f"chemin explicite du {MANIFEST_NAME}")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name=f"DIFF.{args.scope}", target=str(root))
    ir_file, why = find_ir_file(root, args.mission)
    if ir_file is None:
        report.error("IR_NOT_FOUND", why, "compiler : python .sdda/sdda.py ir-compiler --mission {n}", str(paths.ir_dir(root)))
        return finish(report, args)
    ir = ir_compiler.load_ir(ir_file)
    manifest = args.manifest if args.manifest is None or args.manifest.is_absolute() else root / args.manifest
    sub = run(root, ir, scope=args.scope, manifest_path=manifest)
    report.extend(sub)
    report.data.update(sub.data)
    report.target = sub.target
    if report.ok and not args.json:
        d = report.data.get("diff", {})
        print(f"  {args.scope} : isomorphe — {d.get('nodes', {}).get('ir', 0)} nœuds, {d.get('edges', {}).get('ir', 0)} arêtes, entrée `{d.get('entry', {}).get('ir')}`")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
