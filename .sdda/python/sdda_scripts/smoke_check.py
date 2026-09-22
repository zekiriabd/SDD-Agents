#!/usr/bin/env python3
"""Smoke d'un projet fraîchement amorcé — `STACK.md` et l'arborescence, 0 token.

Exécuté par `bootstrap.py` en fin d'amorçage et rappelé par `/sdda-bootstrap`
STEP 5. Il répond à une seule question : **le projet peut-il recevoir son
premier agent ?** Pas « la stack est-elle bonne » (c'est `preflight_stack_combo`
au spawn) ni « la config est-elle cohérente » (`validate_packaging`) — juste ce
qui, s'il manque, fait échouer la première commande d'une façon qui ressemble
à un bug du framework.

Ce qu'il vérifie :

    1. `workspace/stack/STACK.md` existe et ne contient aucun `{{…}}` non
       résolu du gabarit                                 [STACK_FILE_MISSING]
                                                         [STACK_PLACEHOLDER_UNRESOLVED]
    2. les sections `## …` obligatoires sont présentes  [STACK_SECTION_MISSING]
    3. chaque catégorie obligatoire porte le NOMBRE attendu de fiches actives —
       exactement une pour lang / orchestration / rag / reranker / dataaccess /
       serving, au moins une pour framework             [STACK_CARDINALITY_INVALID]
    4. chaque fiche activée existe sur disque            [STACK_COMBO_UNLOADABLE]
    5. l'arborescence du workspace est complète          [WORKSPACE_TREE_INCOMPLETE]
    6. `workspace/.sys/workspace.json` porte la version courante
       (`sdda_lib.workspace.WORKSPACE_VERSION`)          [WORKSPACE_VERSION_MISSING]
                                                         [WORKSPACE_VERSION_OUTDATED]

Une ligne activée pour une fiche absente ne charge rien (ARCHITECTURE §2) : la
détecter ici, avant le premier spawn, coûte cinquante millisecondes ; la
détecter au spawn coûte un agent qui a déjà inventé.

Usage :
    python .sdda/sdda.py smoke-check
    python .sdda/sdda.py smoke-check --json
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.workspace import WORKSPACE_JSON_REL, WORKSPACE_VERSION, read_workspace_version  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

MIGRATE_CMD = "python .sdda/sdda.py migrate-workspace"

#: Arborescence attendue sous `workspace/` — SSoT partagée avec `bootstrap.py`
#: (qui l'importe pour la CRÉER) et `migrate_workspace.py` (qui la COMPLÈTE) ;
#: ce script vérifie qu'elle est là. Un répertoire retiré d'ici devient un
#: fantôme : il se retire dans une migration, pas en silence.
WORKSPACE_TREE: tuple[str, ...] = (
    # stack/ — la CONFIGURATION
    "stack",
    "stack/sources",
    # feats/ — la SPÉCIFICATION : ce qu'on écrit et qu'on relit en revue
    "feats/missions",
    "feats/caps",
    "feats/topology",
    "feats/contracts/agents",
    "feats/contracts/tools",
    "feats/contracts/retrieval",
    "feats/contracts/memory",
    "feats/contracts/dataaccess/schemas",
    "feats/decisions",
    "feats/briefs",
    # src/ — le CODE GÉNÉRÉ, prompts compris (un prompt est un actif runtime)
    "src",
    "src/prompts",
    # proof/ — ce qui JUGE : aucun `dev-*` n'y écrit jamais
    "proof/datasets/golden",
    "proof/datasets/holdout",
    "proof/datasets/calibration",
    "proof/datasets/adversarial",
    "proof/suites",
    "proof/baselines",
    "proof/calibration",
    # .sys/ — l'ÉTAT INTERNE et les sorties de run : régénérable, effaçable
    ".sys/.ir",
    ".sys/.context/packs",
    ".sys/.state",
    ".sys/.validation",
    ".sys/.audit",
    ".sys/reports",
    ".sys/traces/runs",
)

#: Sections dont l'absence rend une commande incapable de lire sa config.
REQUIRED_SECTIONS: tuple[str, ...] = (
    "Active Harness",
    "Build Models",
    "Runtime Models",
    "Project Config",
    "Active Language & Runtime",
    "Active Agent Framework",
    "Active Orchestration Pattern",
    "Active RAG Pattern",
    "Active Reranker",
    "Active Data Access",
    "Active Serving Surface",
    "Active Observability",
    "Active Eval Stack",
)

#: Section -> (min, max) de fiches actives. `None` = pas de borne haute.
#: Un langage, un pattern d'orchestration, une surface : deux lignes actives
#: là où il en faut une, c'est un agent qui choisit lui-même, différemment à
#: chaque run. Le framework peut en cumuler (langchain + langgraph).
CARDINALITY: dict[str, tuple[int, int | None]] = {
    "Active Language & Runtime": (1, 1),
    "Active Agent Framework": (1, None),
    "Active Orchestration Pattern": (1, 1),
    "Active RAG Pattern": (1, 1),
    "Active Reranker": (1, 1),
    "Active Data Access": (1, 1),
    "Active Serving Surface": (1, 1),
}

ACTIVE_LINE_RE = re.compile(r"^\s*-\s+(\.sdda/stacks/[\w\-/]+\.md)\s*(?:#.*)?$", re.M)
PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")


def stack_path(root: Path) -> Path:
    return root / "workspace" / "stack" / "STACK.md"


def active_sheets(section_text: str) -> list[str]:
    return ACTIVE_LINE_RE.findall(section_text)


def check_stack(root: Path, report: Report) -> dict:
    path = stack_path(root)
    loc = "workspace/stack/STACK.md"
    if not path.is_file():
        report.error("STACK_FILE_MISSING", f"`{loc}` absent", "python bootstrap.py (interactif) ou --combo c1", loc)
        return {"present": False}

    text = markdown_io.read_text(path)
    summary: dict = {"present": True, "sections": {}, "sheets": 0}

    unresolved = sorted(set(PLACEHOLDER_RE.findall(text)))
    if unresolved:
        report.error("STACK_PLACEHOLDER_UNRESOLVED",
                     f"{len(unresolved)} gabarit(s) non résolu(s) : {', '.join(unresolved[:5])}",
                     "compléter STACK.md — un `{{…}}` résiduel est lu comme une valeur par les agents", loc)

    for title in REQUIRED_SECTIONS:
        body = markdown_io.section_body(text, title)
        if body is None:
            report.error("STACK_SECTION_MISSING", f"section `## {title}` absente",
                         "réaligner STACK.md sur .sdda/templates/STACK.md.template", loc)
            continue
        sheets = active_sheets(body)
        summary["sections"][title] = len(sheets)
        summary["sheets"] += len(sheets)
        bounds = CARDINALITY.get(title)
        if bounds:
            low, high = bounds
            if len(sheets) < low or (high is not None and len(sheets) > high):
                expected = f"exactement {low}" if high == low else f"au moins {low}"
                report.error("STACK_CARDINALITY_INVALID",
                             f"`## {title}` : {len(sheets)} fiche(s) active(s), attendu {expected}",
                             "une ligne active par choix ; les alternatives restent en commentaire", loc)
        for sheet in sheets:
            if not (root / sheet).is_file() and not (Path(__file__).resolve().parents[3] / sheet).is_file():
                report.error("STACK_COMBO_UNLOADABLE", f"`## {title}` active `{sheet}`, fiche absente du disque",
                             "une ligne activée pour une fiche absente ne charge RIEN : choisir une fiche "
                             "existante ou l'écrire (ARCHITECTURE §2)", loc)
    return summary


def check_tree(root: Path, report: Report) -> list[str]:
    missing = [rel for rel in WORKSPACE_TREE if not (root / "workspace" / rel).is_dir()]
    if missing:
        report.error("WORKSPACE_TREE_INCOMPLETE", f"{len(missing)} répertoire(s) manquant(s) : " + ", ".join(f"workspace/{m}" for m in missing[:6]),
                     "python bootstrap.py --force recrée l'arborescence sans toucher aux fichiers", "workspace/")
    return missing


def check_version(root: Path, report: Report) -> int | None:
    version = read_workspace_version(root)
    if version is None:
        report.error("WORKSPACE_VERSION_MISSING",
                     f"`{WORKSPACE_JSON_REL}` absent ou sans `workspaceVersion` (attendu {WORKSPACE_VERSION})",
                     f"{MIGRATE_CMD} — date le workspace et applique les migrations manquantes", WORKSPACE_JSON_REL)
    elif version < WORKSPACE_VERSION:
        report.error("WORKSPACE_VERSION_OUTDATED",
                     f"workspace en version {version}, le framework attend {WORKSPACE_VERSION}",
                     f"{MIGRATE_CMD} (--dry-run pour voir les actions d'abord)", WORKSPACE_JSON_REL)
    return version


def run(root: Path) -> Report:
    report = Report(name="SMOKE", target=str(root))
    report.data["stack"] = check_stack(root, report)
    report.data["missingDirs"] = check_tree(root, report)
    report.data["workspaceVersion"] = check_version(root, report)
    return report


def problems(root: Path) -> list[str]:
    """Les anomalies en phrases — ce que `bootstrap.py` affiche."""
    return [f"[{f.cls}] {f.message}" for f in run(root).errors]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Smoke d'un projet amorcé : STACK.md et arborescence (0 token)")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = run(root)
    if report.ok and not args.json:
        stack = report.data["stack"]
        print(f"  smoke ok — {stack.get('sheets', 0)} fiche(s) active(s), {len(WORKSPACE_TREE)} répertoires présents, "
              f"workspace v{report.data['workspaceVersion']}")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
