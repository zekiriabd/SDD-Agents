#!/usr/bin/env python3
"""
Inventaire des scripts référencés mais pas encore écrits.

Les agents, les commandes et les invariants nomment des scripts déterministes.
Écrits par plusieurs auteurs en parallèle, ces noms divergent : le même travail
peut apparaître sous deux noms, et un nom peut n'être cité qu'une fois — signe
qu'il a été inventé au fil de la plume plutôt que décidé.

Ce script transforme la dette en backlog : qui réclame quoi, et combien de fois.
Un script réclamé par un seul appelant mérite une question ; un script réclamé
par six est un vrai besoin.

Usage :
    python .sdda/python/sdda_admin/planned_scripts.py
    python .sdda/python/sdda_admin/planned_scripts.py --json
    python .sdda/python/sdda_admin/planned_scripts.py --write   # écrit le .md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

SDDA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SDDA / "python"))

from sdda_lib.runtime_io import ensure_utf8_stdout  # noqa: E402

ensure_utf8_stdout()

OUT = SDDA / "docs" / "PLANNED-SCRIPTS.md"

SCAN = ("agents/*.md", "commands/*.md", "rules/*.md", "INVARIANTS.yml", "loader.yml")
REF_RE = re.compile(r"(?<![~/\w])\.sdda/python/([\w\-./]+\.py)(?![\w.])")
BARE_RE = re.compile(r"`([a-z][a-z0-9_]*\.py)`")

#: Un script absent cité par un prompt doit le DIRE au lecteur — l'agent qui le
#: lit croirait sinon à un outil disponible et inventerait sa sortie. La
#: déclaration est une ligne « Planifié » dans les lignes qui suivent l'appel,
#: avec la conduite à tenir tant qu'il manque.
DECLARED_RE = re.compile(r"planifi[ée]", re.IGNORECASE)
DECLARED_WINDOW_LINES = 8

#: Quand un même script est cité plusieurs fois dans un fichier, une seule
#: déclaration suffit : c'est le lecteur du fichier qu'on informe, pas la ligne.


def is_declared_planned(text: str, ref: str) -> bool:
    """La référence `ref` (chemin sous python/) est-elle suivie d'un « planifié » ?"""
    lines = text.splitlines()
    needle = f".sdda/python/{ref}"
    for i, line in enumerate(lines):
        if needle in line:
            window = "\n".join(lines[i: i + DECLARED_WINDOW_LINES + 1])
            if DECLARED_RE.search(window):
                return True
    return False


def collect() -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    """(scripts avec chemin complet, scripts cités par nom nu, refs déclarées planifiées).

    Le troisième dictionnaire donne, par script, les appelants qui déclarent
    l'attente ; un script absent cité sans déclaration est une promesse muette.
    """
    pathed: dict[str, set[str]] = defaultdict(set)
    bare: dict[str, set[str]] = defaultdict(set)
    declared: dict[str, set[str]] = defaultdict(set)

    for pattern in SCAN:
        for path in SDDA.glob(pattern):
            if "fixtures" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            caller = str(path.relative_to(SDDA))
            for ref in REF_RE.findall(text):
                pathed[ref].add(caller)
                if is_declared_planned(text, ref):
                    declared[ref].add(caller)
            for ref in BARE_RE.findall(text):
                bare[ref].add(caller)
    return pathed, bare, declared


def undeclared_missing(pathed: dict[str, set[str]], declared: dict[str, set[str]]) -> dict[str, set[str]]:
    """Scripts absents du disque cités par au moins un appelant qui ne le dit pas."""
    out: dict[str, set[str]] = {}
    for ref, callers in pathed.items():
        if (SDDA / "python" / ref).is_file():
            continue
        silent = callers - declared.get(ref, set())
        if silent:
            out[ref] = silent
    return out


def render(pathed: dict[str, set[str]], bare: dict[str, set[str]], declared: dict[str, set[str]] | None = None) -> str:
    declared = declared or {}
    existing = {r for r in pathed if (SDDA / "python" / r).is_file()}
    missing = {r: c for r, c in pathed.items() if r not in existing}
    silent = undeclared_missing(pathed, declared)

    # Un nom nu qui ne correspond à aucun chemin complet : le nom a été inventé
    # sans qu'on décide où il vit.
    pathed_names = {Path(r).name for r in pathed}
    orphan_bare = {n: c for n, c in bare.items() if n not in pathed_names}

    by_package: dict[str, list[tuple[str, set[str]]]] = defaultdict(list)
    for ref, callers in sorted(missing.items()):
        by_package[Path(ref).parent.as_posix() or "."].append((ref, callers))

    lines = [
        "# Scripts planifiés",
        "",
        "> **Généré** par `sdda_admin/planned_scripts.py`. Ne pas éditer à la main.",
        "",
        "Inventaire des scripts déterministes que les agents, commandes et",
        "invariants nomment sans qu'ils existent encore. C'est le backlog des",
        "lots d'implémentation, pas une liste de bugs.",
        "",
        "**Lecture** : le nombre d'appelants est un signal. Un script réclamé par",
        "six endroits est un besoin établi ; un script réclamé une seule fois a pu",
        "être inventé au fil de la plume et mérite une question avant d'être écrit.",
        "",
        f"- **{len(existing)}** écrits · **{len(missing)}** à écrire · **{len(silent)}** cité(s) sans déclaration",
        "",
        "**Déclaré** : le prompt qui appelle le script dit « Planifié » à la ligne",
        "suivante, avec la conduite à tenir tant qu'il manque. Un script absent cité",
        "sans le dire fait croire à l'agent qu'il dispose d'un outil — il en invente",
        "la sortie. `framework_smoke` échoue sur toute référence muette.",
        "",
    ]

    for package in sorted(by_package):
        entries = sorted(by_package[package], key=lambda e: (-len(e[1]), e[0]))
        lines += [f"## `{package}/` — {len(entries)} à écrire", "", "| Script | Appelants | Déclaré | Réclamé par |", "|---|---:|:-:|---|"]
        for ref, callers in entries:
            names = ", ".join(f"`{c}`" for c in sorted(callers)[:4])
            if len(callers) > 4:
                names += f" … (+{len(callers) - 4})"
            flag = "✅" if ref not in silent else "❌"
            lines.append(f"| `{Path(ref).name}` | {len(callers)} | {flag} | {names} |")
        lines.append("")

    if orphan_bare:
        lines += [
            "## Noms cités sans chemin",
            "",
            "Ces scripts sont nommés quelque part mais aucun endroit ne dit où ils",
            "vivent. À rattacher à un paquet, ou à fusionner avec un script existant",
            "qui fait déjà le travail.",
            "",
            "| Nom | Cité par |",
            "|---|---|",
        ]
        for name, callers in sorted(orphan_bare.items()):
            lines.append(f"| `{name}` | {', '.join(f'`{c}`' for c in sorted(callers)[:3])} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventaire des scripts planifiés.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write", action="store_true", help="écrit docs/PLANNED-SCRIPTS.md")
    args = parser.parse_args()

    pathed, bare, declared = collect()
    existing = {r for r in pathed if (SDDA / "python" / r).is_file()}
    missing = sorted(set(pathed) - existing)
    silent = undeclared_missing(pathed, declared)

    if args.json:
        print(json.dumps(
            {
                "written": sorted(existing),
                "planned": {r: sorted(pathed[r]) for r in missing},
                "undeclared": {r: sorted(c) for r, c in sorted(silent.items())},
                "bare_names_without_path": {n: sorted(c) for n, c in sorted(bare.items())},
            },
            ensure_ascii=False, indent=2,
        ))
        return 0

    if args.write:
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(render(pathed, bare, declared), encoding="utf-8")
        print(f"  {OUT.relative_to(SDDA)} — {len(existing)} écrits, {len(missing)} à écrire")
        return 0

    print(f"\n  {len(existing)} script(s) écrit(s) · {len(missing)} à écrire\n")
    top = sorted(missing, key=lambda r: (-len(pathed[r]), r))[:15]
    for ref in top:
        print(f"  {len(pathed[ref]):>2} appelant(s)  {ref}")
    if len(missing) > 15:
        print(f"  … et {len(missing) - 15} autres (--write pour l'inventaire complet)")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
