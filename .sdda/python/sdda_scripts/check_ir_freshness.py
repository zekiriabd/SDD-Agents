#!/usr/bin/env python3
"""L'IR compilé reflète-t-il encore ses sources ? (`[IR_STALE]`)

Pré-condition de `/sdda-build` : on ne génère pas de code depuis une
représentation qui ne décrit plus les contrats. Le contrôle existe aussi à
l'intérieur de `validate_ir.py`, mais le sortir ici a une raison précise — une
pré-condition doit coûter quelques millisecondes et répondre par oui ou non.
Rejouer la validation complète de l'IR pour apprendre qu'un hash a bougé rend
la vérification assez chère pour qu'on finisse par la sauter.

Ce qui est comparé : les empreintes de `compiledFrom` recalculées depuis le
disque — MISSION, chaque CAP, la topologie (graphe compris : c'est une section du
`.md`), `STACK.md`, et **chaque contrat**. Un contrat absent de cette liste serait un trou : éditer un
schéma d'outil laisserait l'IR se déclarer frais tout en décrivant autre chose.

Usage :
    python .sdda/sdda.py check-ir-freshness --mission 1
    python .sdda/sdda.py check-ir-freshness --mission 1 --json
    python .sdda/sdda.py check-ir-freshness                 # toutes les MISSIONs compilées

Exit : 0 = frais · 1 = périmé (ou IR absent)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402


def _diff_maps(compiled: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Clés d'un sous-dictionnaire (CAPs, contrats) qui ont bougé, disparu ou apparu."""
    moved = [k for k in sorted(compiled.keys() & current.keys()) if compiled[k] != current[k]]
    gone = [f"{k} (disparu)" for k in sorted(compiled.keys() - current.keys())]
    added = [f"{k} (nouveau)" for k in sorted(current.keys() - compiled.keys())]
    return moved + gone + added


def freshness(root: Path, number: int) -> dict[str, Any]:
    """{fresh, missing, moved: {dimension: [détails]}} pour UNE mission."""
    path = paths.ir_path(root, number)
    state: dict[str, Any] = {"mission": number, "ir": paths.rel(root, path), "fresh": False, "moved": {}}
    if not path.is_file():
        state["missing"] = True
        return state
    state["missing"] = False

    ir = ir_compiler.load_ir(path)
    compiled = dict(ir.get("compiledFrom") or {})
    compiled.pop("compiledAt", None)
    current = ir_compiler.source_hashes(root, number)

    moved: dict[str, list[str]] = {}
    for key, value in sorted(current.items()):
        before = compiled.get(key)
        if isinstance(value, dict):
            details = _diff_maps(dict(before or {}), value)
            if details:
                moved[key] = details
        elif before != value:
            # Un hash absent de l'IR compte comme un mouvement : l'IR a été
            # produit par une version qui ne suivait pas cette source.
            moved[key] = ["absent de l'IR" if before is None else "modifié"]
    state["moved"] = moved
    state["fresh"] = not moved
    return state


def check(root: Path, numbers: list[int], report: Report) -> list[dict[str, Any]]:
    states = []
    for number in numbers:
        state = freshness(root, number)
        states.append(state)
        if state["missing"]:
            report.error("IR_NOT_FOUND", f"MISSION {number} : IR `{state['ir']}` absent",
                         f"compiler : python .sdda/sdda.py ir-compiler --mission {number}", state["ir"])
            continue
        for dimension, details in sorted(state["moved"].items()):
            report.error("IR_STALE", f"MISSION {number} : `{dimension}` a bougé — {', '.join(details[:5])}",
                         f"/sdda-topology {number} --recompile-only (recompile l'IR et rejoue G2) avant toute génération",
                         state["ir"])
    return states


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fraîcheur de l'IR compilé face à ses sources Markdown (0 token)")
    p.add_argument("--mission", type=int, default=None, help="numéro de MISSION ; défaut : toutes les MISSIONs compilées")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="IR-FRESHNESS", target=str(root))

    if args.mission is not None:
        numbers = [args.mission]
    else:
        numbers = sorted(int(p.stem.split("-", 1)[0]) for p in paths.ir_dir(root).glob("*-system.ir.json")
                         if p.stem.split("-", 1)[0].isdigit())
        if not numbers:
            report.error("IR_NOT_FOUND", "aucun IR compilé dans workspace/.sys/.ir/",
                         "compiler : python .sdda/sdda.py ir-compiler --mission {n}", str(paths.ir_dir(root)))
            return finish(report, args)

    states = check(root, numbers, report)
    report.data.update({"missions": states, "fresh": all(s["fresh"] for s in states)})
    if not args.json:
        for state in states:
            glyph = "🟢" if state["fresh"] else "🔴"
            detail = "frais" if state["fresh"] else ("IR absent" if state["missing"] else ", ".join(sorted(state["moved"])) + " ont bougé")
            print(f"{glyph} MISSION {state['mission']} — {detail}")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
