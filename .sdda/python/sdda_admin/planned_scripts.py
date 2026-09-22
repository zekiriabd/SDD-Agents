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

Deux formes d'appel sont reconnues dans la prose scannée : le chemin complet
`.sdda/python/{pkg}/{nom}.py` et la forme courte `python .sdda/sdda.py {cmd}`,
résolue par `sdda_cli.resolve()`. Les compter séparément aurait fait
réapparaître comme « à écrire » tout script migré vers la forme courte.

Usage :
    python .sdda/sdda.py planned-scripts
    python .sdda/sdda.py planned-scripts --json
    python .sdda/sdda.py planned-scripts --write   # écrit le .md
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

import sdda_cli  # noqa: E402
from sdda_lib.runtime_io import ensure_utf8_stdout  # noqa: E402

ensure_utf8_stdout()

OUT = SDDA / "docs" / "PLANNED-SCRIPTS.md"

SCAN = ("agents/*.md", "commands/*.md", "rules/*.md", "INVARIANTS.yml", "loader.yml")
REF_RE = re.compile(r"(?<![~/\w])\.sdda/python/([\w\-./]+\.py)(?![\w.])")
BARE_RE = re.compile(r"`([a-z][a-z0-9_]*\.py)`")

#: La forme courte : `python .sdda/sdda.py validate-mission --mission 1`. Depuis
#: qu'elle a remplacé les chemins à cinq segments dans les prompts, c'est elle
#: que ce scanner doit résoudre — sinon le contrôle qui empêche un prompt
#: d'annoncer un outil inexistant serait devenu muet le jour de la migration.
LAUNCHER_RE = re.compile(r"python \.sdda/sdda\.py\s+([a-z][a-z0-9-]*)(?![\w-])")

#: Un script absent cité par un prompt doit le DIRE au lecteur — l'agent qui le
#: lit croirait sinon à un outil disponible et inventerait sa sortie. La
#: déclaration est une ligne « Planifié » dans les lignes qui suivent l'appel,
#: avec la conduite à tenir tant qu'il manque.
DECLARED_RE = re.compile(r"planifi[ée]", re.IGNORECASE)
DECLARED_WINDOW_LINES = 8

#: Quand un même script est cité plusieurs fois dans un fichier, une seule
#: déclaration suffit : c'est le lecteur du fichier qu'on informe, pas la ligne.


def is_declared_planned(text: str, needles: list[str]) -> bool:
    """Un des appels (chemin complet ou forme courte) est-il suivi d'un « planifié » ?

    Une seule déclaration suffit par fichier : c'est le lecteur qu'on informe,
    pas la ligne. Un appel déclaré et un autre muet, dans le même fichier, ne
    trompent personne — le fichier dit que le script n'existe pas encore.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if any(needle in line for needle in needles):
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
            # {module rel-path: les textes d'appel qui le désignent dans CE fichier}
            calls: dict[str, list[str]] = defaultdict(list)
            for ref in REF_RE.findall(text):
                calls[ref].append(f".sdda/python/{ref}")
            for name in LAUNCHER_RE.findall(text):
                ref = sdda_cli.resolve(name) or sdda_cli.expected_path(name)
                calls[ref].append(f"{sdda_cli.LAUNCHER} {name}")
            for ref, needles in calls.items():
                pathed[ref].add(caller)
                if is_declared_planned(text, needles):
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
