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
    7. (v3+) `feats/` ne contient que du Markdown         [FEATS_NOT_MARKDOWN]
    8. (v3+) `stack/` ne contient que STACK.md (+ manifestes déclarés)
                                                         [STACK_DIR_UNEXPECTED_FILE]
    9. (v3+) aucune valeur de secret en clair dans STACK.md — des `${NOM}`,
       les valeurs dans `workspace/assets/.env`, que `install-env` copie
       vers `workspace/src/{App}/.env`                    [STACK_SECRET_IN_CLEAR]

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
    # ── CE QUE L'HUMAIN FOURNIT ─────────────────────────────────────────────
    # stack/ — les choix techniques : STACK.md, seul, des NOMS de variables.
    "stack",
    # feats/ — ses spécifications en Markdown, à plat : brief et roster.
    # Du MARKDOWN, et rien d'autre (`check_feats_markdown_only`).
    "feats",
    # assets/ — les données (racine des stores `kind: local`) et `.env`, les
    # VALEURS des secrets de l'application, copiées par `install-env`.
    "assets",
    # seed/ — la vérité terrain : scénarios annotés, labels.
    "seed",
    # ── CE QUE LE FRAMEWORK PRODUIT ─────────────────────────────────────────
    # pipeline/ — tout ce que le pipeline génère. Les zones qui JUGENT
    # (datasets, suites, baselines, calibration) ne sont jamais écrites par un dev-*.
    "pipeline/missions",
    "pipeline/caps",
    "pipeline/topology",
    "pipeline/contracts/agents",
    "pipeline/contracts/tools",
    "pipeline/contracts/retrieval",
    "pipeline/contracts/memory",
    "pipeline/decisions",
    "pipeline/datasets/golden",
    "pipeline/datasets/holdout",
    "pipeline/datasets/calibration",
    "pipeline/datasets/adversarial",
    "pipeline/suites",
    "pipeline/baselines",
    "pipeline/calibration",
    # src/ — le CODE GÉNÉRÉ : `src/{App}/` est l'application, layout plat, prompts,
    # skills, rules et schémas figés DEDANS (créés par gen-app-skeleton, pas ici).
    "src",
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
    "Active Architecture Pattern",
    "Active Backend Stack",
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
    "Active Architecture Pattern": (1, 1),   # la coquille a UNE architecture (défaut mvc)
    "Active Backend Stack": (0, 1),          # seulement pour DeliverableType: backend-api
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


#: Ce qui a le droit de vivre sous `workspace/stack/` à côté de STACK.md : sa
#: sauvegarde de bootstrap, et les manifestes que STACK.md déclare lui-même.
_STACK_DIR_ALLOWED = {"STACK.md", "STACK.md.bak", ".gitkeep"}
_MANIFEST_PATH_RE = re.compile(r"^\s*-\s*(?:\{\s*)?path:\s*([^\s,}]+)", re.M)

#: Un nom de clé qui désigne un secret. La VALEUR, si elle est en clair dans
#: STACK.md, part en commit : c'est tout ce que `src/{App}/.env` existe pour empêcher.
_SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|PASSWORD|PASSWD|SECRET)", re.I)
_SECRET_LINE_RE = re.compile(r"^\s*-\s*([A-Z][A-Z0-9_]*)\s*:\s*(.*?)\s*(?:#.*)?$", re.M)
_ENV_REF_RE = re.compile(r"^\$\{[A-Z][A-Z0-9_]*\}$")


def check_feats_markdown_only(root: Path, report: Report) -> list[str]:
    """`feats/` est la spécification, et une spécification se relit : du Markdown, seul.

    Un YAML, un JSONL, un `.mmd` posés là sont soit une configuration (-> STACK.md),
    soit de la vérité terrain (-> `seed/`), soit un actif d'exécution
    (-> `src/`). La migration sait les ranger ; ce contrôle dit qu'ils sont là.
    """
    feats = root / "workspace" / "feats"
    if not feats.is_dir():
        return []
    strays = sorted(
        p.relative_to(root / "workspace").as_posix()
        for p in feats.rglob("*")
        if p.is_file() and p.suffix.lower() != ".md" and p.name != ".gitkeep"
    )
    if strays:
        report.error("FEATS_NOT_MARKDOWN",
                     f"{len(strays)} fichier(s) non-Markdown sous workspace/feats/ : " + ", ".join(strays[:5]),
                     f"{MIGRATE_CMD} range roster, graphe, schémas et vérité terrain à leur place ; "
                     "sinon déplacer à la main (config -> STACK.md, données -> assets/, ground truth -> seed/, schémas -> src/)",
                     "workspace/feats/")
    return strays


def check_stack_dir(root: Path, report: Report, stack_text: str | None) -> list[str]:
    """`stack/` ne porte que STACK.md — et les manifestes qu'il nomme lui-même."""
    stack = root / "workspace" / "stack"
    if not stack.is_dir():
        return []
    declared: set[str] = set()
    if stack_text:
        body = markdown_io.section_body(stack_text, "Active Data Sources") or ""
        declared = {m.strip().strip("'\"") for m in _MANIFEST_PATH_RE.findall(body)}
    strays = sorted(
        p.relative_to(stack).as_posix()
        for p in stack.rglob("*")
        if p.is_file() and p.name not in _STACK_DIR_ALLOWED and p.relative_to(stack).as_posix() not in declared
    )
    if strays:
        report.error("STACK_DIR_UNEXPECTED_FILE",
                     f"{len(strays)} fichier(s) inattendu(s) sous workspace/stack/ : " + ", ".join(strays[:5]),
                     f"{MIGRATE_CMD} rapatrie roster et sources dans leurs sections ; la configuration tient dans STACK.md, "
                     "les valeurs dans workspace/assets/.env (copié vers src/{App}/.env par install-env)",
                     "workspace/stack/")
    return strays


def check_secrets_not_in_clear(stack_text: str | None, report: Report) -> list[str]:
    """Aucune valeur de secret dans STACK.md : un nom `${VAR}`, ou rien.

    STACK.md est versionné depuis la v3 du workspace. Une clé d'API en clair
    dans `## Active Secrets` ou un mot de passe dans `## Active Data Access`
    part donc en commit au premier `git add`. Le contrôle est lexical et
    volontairement étroit : il ne juge que les clés dont le NOM dit qu'elles
    sont sensibles, pour ne pas crier sur `DB_HOST: localhost`.
    """
    if not stack_text:
        return []
    leaks: list[str] = []
    for title in ("Active Secrets", "Active Data Access"):
        body = markdown_io.section_body(stack_text, title) or ""
        for m in _SECRET_LINE_RE.finditer(body):
            name, value = m.group(1), m.group(2).strip()
            if not _SECRET_NAME_RE.search(name):
                continue
            if not value or _ENV_REF_RE.match(value) or value.startswith("<") or value.lower() in ("n/a", "none", "-"):
                continue
            leaks.append(f"{title} > {name}")
    if leaks:
        report.error("STACK_SECRET_IN_CLEAR",
                     f"{len(leaks)} secret(s) en clair dans workspace/stack/STACK.md : " + ", ".join(leaks[:4]),
                     f"écrire `NAME: ${{NAME}}` dans STACK.md et `NAME=valeur` dans workspace/assets/.env (gitignoré), "
                     f"puis `python .sdda/sdda.py install-env` — {MIGRATE_CMD} le fait",
                     "workspace/stack/STACK.md")
    return leaks


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
    # Les trois règles de la v3, vérifiées et non racontées : feats/ en Markdown
    # seul, stack/ réduit à STACK.md, aucun secret en clair. Elles ne s'appliquent
    # qu'à un workspace qui a atteint la version — avant, c'est la migration qui
    # parle, et deux messages pour le même fait en font un qu'on ne lit pas.
    if (report.data["workspaceVersion"] or 0) >= 3:
        stack_text = markdown_io.read_text(stack_path(root)) if stack_path(root).is_file() else None
        report.data["feats_strays"] = check_feats_markdown_only(root, report)
        report.data["stack_strays"] = check_stack_dir(root, report, stack_text)
        report.data["secrets_in_clear"] = check_secrets_not_in_clear(stack_text, report)
    if (report.data["workspaceVersion"] or 0) >= 6:
        report.data["env"] = check_env_installed(root, report)
    return report


def check_env_installed(root: Path, report: Report) -> dict[str, object]:
    """`assets/.env` (source humaine) et `src/{App}/.env` (lu par l'application) disent-ils la même chose ?

    Avertissements seulement : le smoke d'un projet qui vient d'être amorcé ne
    doit pas rougir parce que la clé n'est pas encore fournie. `install-env
    --require`, lui, bloque avant les évaluations.
    """
    from sdda_scripts import install_env  # import tardif : bootstrap importe ce module avant sys.path complet

    sub = install_env.run(root, write=False, require=False)
    for finding in sub.findings:
        report.warn(finding.cls, finding.message, finding.fix, finding.location)
    return {k: sub.data.get(k) for k in ("source", "target", "upToDate")}


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
