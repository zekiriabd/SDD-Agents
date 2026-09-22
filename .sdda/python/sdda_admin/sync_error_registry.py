#!/usr/bin/env python3
"""
Synchronise le registre canonique des classes d'erreur.

Scanne les émetteurs réels (agents, commandes, règles, scripts), en extrait les
classes `[CLASS]`, et régénère la section « ## 6. Registre canonique » de
`.sdda/rules/error-classification.md`.

Pourquoi un script et pas une liste tenue à la main : une taxonomie maintenue
manuellement dérive dans les deux sens à la fois — des classes émises que
personne n'a documentées, et des classes documentées que plus rien n'émet. Les
deux ruinent le classement automatique, et aucune des deux ne se voit.

Usage :
    python .sdda/python/sdda_admin/sync_error_registry.py           # met à jour
    python .sdda/python/sdda_admin/sync_error_registry.py --check   # CI : exit 1 si dérive
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SDDA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SDDA / "python"))

from sdda_lib.runtime_io import ensure_utf8_stdout  # noqa: E402

ensure_utf8_stdout()

TAXONOMY = SDDA / "rules" / "error-classification.md"

SCAN_GLOBS = ("agents/*.md", "commands/*.md", "rules/*.md", "python/**/*.py")

# Les tests ne définissent PAS le registre. Un `[ROUTING]` dans une fixture ou
# une assertion n'est pas une classe que le framework émet — l'y compter
# fabriquerait des classes que rien n'applique, c'est-à-dire exactement le
# doc-theater que ce registre existe pour empêcher.
EXCLUDE_PARTS = {"tests", "fixtures", "__pycache__"}

# En Markdown une classe s'écrit `[CLASS]`. En Python elle s'écrit le plus
# souvent nue : `report.error("CLASS", …)` ou `CLS_X = "CLASS"`. Ne chercher
# que la forme crochetée laisserait hors registre des classes bel et bien
# émises — et la réciprocité dirait le contraire de la vérité.
#
# `deny(HOOK, "CLASS", …)` est LA forme d'émission des hooks, et elle manquait
# ici : une classe refusée au runtime par un hook n'entrait au registre que si
# un Markdown la citait par ailleurs. Le registre disait donc « à jour » en
# ignorant précisément la couche qui bloque — le contraire de ce qu'il promet.
PY_EMIT_RE = re.compile(
    r"""(?:report\.(?:error|warn)|deny|SddaError|GradingError|raise\s+\w*Error)"""
    r"""\s*\(\s*(?:HOOK\s*,\s*)?["']([A-Z][A-Z0-9_]{2,})["']"""
)
PY_CONST_RE = re.compile(r"""^\s*(?:CLS_\w+|[A-Z_]*CLASS\w*)\s*=\s*["']([A-Z][A-Z0-9_]{2,})["']""", re.M)

# Mots qui apparaissent entre crochets sans être des classes : préfixes de
# sortie chat (`[CAPS] …`), placeholders de documentation, noms de section.
NOT_A_CLASS = {
    "CLASS", "FAMILLE_SUJET_PROBLEME",
    # marqueurs d'exemple de PHILOSOPHY.md P2 : `[REJETE]` / `[OK]` étiquettent
    # un AC bien ou mal écrit, ils ne classent aucune erreur
    "REJETE",
    # marqueur de redaction des traces, pas une classe
    "REDACTED",
    # préfixes de ligne de sortie chat
    "AGENT", "ANALYSIS", "BOOTSTRAP", "BUILD", "CAPS", "EVAL", "MEMORY",
    "MISSION", "ORCHESTRATION", "PROMPT", "RETRIEVAL", "REVIEW", "ROSTER",
    "SAFETY", "STATUS", "TESTS", "TOOL", "TOOLS", "TOPOLOGY", "TRACE",
}

MARKER = "## 6. Registre canonique"
COLUMNS = 4


def collect() -> list[str]:
    found: set[str] = set()
    for pattern in SCAN_GLOBS:
        for path in SDDA.glob(pattern):
            if EXCLUDE_PARTS & set(path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            # On ignore la section du registre lui-même, sinon il s'auto-alimente.
            if path == TAXONOMY:
                text = text.split(MARKER)[0]
            found.update(re.findall(r"\[([A-Z][A-Z0-9_]{2,})\]", text))
            if path.suffix == ".py":
                found.update(PY_EMIT_RE.findall(text))
                found.update(PY_CONST_RE.findall(text))
    return sorted(found - NOT_A_CLASS)


def render(classes: list[str]) -> str:
    rows = (len(classes) + COLUMNS - 1) // COLUMNS
    lines = []
    for r in range(rows):
        cells = []
        for c in range(COLUMNS):
            i = c * rows + r
            cells.append(f"`{classes[i]}`" if i < len(classes) else "")
        lines.append("| " + " | ".join(cells) + " |")

    return (
        f"{MARKER}\n\n"
        f"**{len(classes)} classes.** Liste close, générée depuis les émetteurs réels par\n"
        "`sdda_admin/sync_error_registry.py` et vérifiée par `framework_smoke.py` :\n"
        "toute classe émise doit figurer ici, et toute classe listée ici doit être émise\n"
        "quelque part.\n\n"
        "> Les noms ne sont pas tous préfixés par leur famille. `[SECRET_LEAK]` et\n"
        "> `[UNBOUNDED_LOOP]` disent ce qu'ils sont ; les rebaptiser\n"
        "> `[SAFETY_SECRET_LEAK]` les rendrait plus longs sans les rendre plus clairs.\n"
        "> La famille sert à regrouper dans les tableaux de bord, pas à contraindre le\n"
        "> nommage.\n\n"
        "| | | | |\n|---|---|---|---|\n" + "\n".join(lines) + "\n"
    )


def current_registry(text: str) -> list[str]:
    if MARKER not in text:
        return []
    return sorted(set(re.findall(r"`([A-Z][A-Z0-9_]{2,})`", text.split(MARKER)[1])))


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronise le registre des classes d'erreur.")
    parser.add_argument("--check", action="store_true", help="CI : ne rien écrire, échouer si dérive")
    args = parser.parse_args()

    if not TAXONOMY.is_file():
        print(f"ERROR: taxonomie introuvable — {TAXONOMY}")
        return 1

    text = TAXONOMY.read_text(encoding="utf-8")
    emitted = collect()
    registered = current_registry(text)

    added = [c for c in emitted if c not in registered]
    removed = [c for c in registered if c not in emitted]

    if args.check:
        if added or removed:
            print("ERROR: sync_error_registry — le registre a dérivé")
            print(f"CAUSE: [ERROR_REGISTRY_DRIFT] {len(added)} émise(s) non enregistrée(s), "
                  f"{len(removed)} enregistrée(s) non émise(s)")
            if added:
                print(f"       non enregistrées : {', '.join(added[:12])}")
            if removed:
                print(f"       orphelines       : {', '.join(removed[:12])}")
            print("FIX: python .sdda/python/sdda_admin/sync_error_registry.py")
            return 1
        print(f"  ok — {len(emitted)} classes, registre à jour")
        return 0

    head = text.split(MARKER)[0].rstrip() if MARKER in text else text.rstrip()
    head = re.sub(r"(\n---\s*)+$", "", head).rstrip()  # évite d'empiler les séparateurs
    TAXONOMY.write_text(head + "\n\n---\n\n" + render(emitted), encoding="utf-8")

    print(f"  registre synchronisé — {len(emitted)} classes")
    if added:
        print(f"  + {len(added)} ajoutée(s) : {', '.join(added[:10])}")
    if removed:
        print(f"  - {len(removed)} retirée(s) : {', '.join(removed[:10])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
