#!/usr/bin/env python3
"""Génère le fichier de contexte de l'application — 0 token, déterministe.

Ce que ce script défend : *un agent `dev-*` qui commence à coder doit savoir
en une lecture où il est, avec quoi il construit, et ce qu'il n'a pas le droit
de toucher.* Avant lui, chaque `dev-*` relisait `STACK.md` en entier (44 Ko,
dont une majorité de commentaires et de sections inactives) et reconstituait
seul l'arborescence, les libs et les commandes du projet — huit fois par build.
SDD_Pro règle la même question avec un `CLAUDE.md` par projet (arch, STEP 12) ;
ici l'IR existe déjà, donc le fichier est une PROJECTION, écrite par un script
et jamais rédigée par un LLM.

Le fichier porte le nom que le harnais actif charge nativement
(`memory_file` de `capability-matrix.yml` : `CLAUDE.md`, `AGENTS.md`,
`GEMINI.md`) et vit à la racine du projet, `workspace/src/{App}/`. Il contient :
le projet résolu, l'arborescence et le propriétaire de chaque répertoire
(dérivés des `writes:` de `loader.yml`), les commandes, les dépendances
épinglées, l'extrait de l'IR qui sert à coder, la stack résolue (les sections
de STACK.md sans commentaires) et les interdits.

Il ne remplace PAS l'IR : l'IR reste la source close de ce qu'on implémente.
Il remplace la lecture de STACK.md par les `dev-*`, et c'est tout.

Aucun horodatage dans le fichier : `--check` compare à l'octet, et une date
rendrait chaque régénération « divergente ».

    --check    le défaut : régénère en mémoire et compare. Exit 1 si absent ou périmé.
    --write    écrit le fichier s'il est absent ou divergent.
    --json     sortie machine.

Usage :
    python .sdda/sdda.py gen-app-context --mission 1 --check
    python .sdda/sdda.py gen-app-context --mission 1 --write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io, paths, yaml_mini  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.layered_config import active_harness, active_profile, active_stacks, harness_memory_file  # noqa: E402
from sdda_lib.runtime_io import atomic_write_text  # noqa: E402
from sdda_scripts import gen_app_skeleton as gas  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

#: Le fichier de contexte est absent : les `dev-*` n'ont pas de projet initialisé.
CLS_NOT_INIT = "PROJECT_NOT_INIT"
#: Le fichier existe mais ne correspond plus à STACK.md, à l'IR ou au framework.
CLS_STALE = "PROJECT_CONTEXT_STALE"

#: Premier segment de `workspace/src/**/{couche}/**` (ou `src/*/{couche}/`) dans `writes:`.
_LAYER_RE = re.compile(r"^workspace/src/(?:\*\*|\*)(?:/\*\*)?/([a-z]+)/")

#: Ce que chaque couche contient — une ligne, pour qu'un agent sache où chercher.
LAYER_ROLE: dict[str, str] = {
    "app": "composition, configuration, Domaine (règles métier calculables)",
    "agents": "un sous-répertoire par agent du produit",
    "prompts": "prompts système hashés, un par agent",
    "skills": "ce que l'agent sait faire, un fragment par skill",
    "rules": "ce que l'agent doit ou ne doit jamais faire",
    "tools": "outils du produit (schéma, transport, enveloppe de sûreté)",
    "data": "accès aux données et outils de source générés",
    "retrieval": "ingestion, index, retriever",
    "orchestration": "graphe, routeur ou superviseur, bornes",
    "memory": "interface puis implémentation de la mémoire",
    "shared": "types partagés, gelés après la pré-passe",
    "serving": "surface d'entrée (CLI, HTTP, batch)",
    "tests": "tests transverses (chaque couche garde les siens dans `{couche}/tests/`)",
}


# ---------------------------------------------------------------------------
# Résolutions
# ---------------------------------------------------------------------------
def _sdda_dir(root: Path) -> Path:
    return root / ".sdda" if (root / ".sdda" / "loader.yml").is_file() else paths.FRAMEWORK_SDDA_DIR


def context_path(root: Path, app: str) -> Path:
    return paths.app_dir(root, app) / harness_memory_file(root)


def layer_owners(root: Path) -> dict[str, str]:
    """couche -> agent propriétaire, DÉRIVÉ des `writes:` de `loader.yml`.

    Une table écrite ici dériverait de la matrice d'ownership au premier
    changement ; la matrice est la source, ce fichier en est la lecture.
    """
    loader = yaml_mini.parse_mapping(markdown_io.read_text(_sdda_dir(root) / "loader.yml"))
    owners: dict[str, str] = {}
    for agent, spec in loader.items():
        if not isinstance(spec, dict):
            continue
        for pattern in spec.get("writes") or []:
            match = _LAYER_RE.match(str(pattern))
            if match and match.group(1) in LAYER_ROLE:
                owners.setdefault(match.group(1), str(agent))
    return owners


def _ir(root: Path, mission: str) -> dict[str, Any] | None:
    ir_file = paths.ir_path(root, mission)
    if not ir_file.is_file():
        return None
    try:
        data = json.loads(markdown_io.read_text(ir_file))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Rendu
# ---------------------------------------------------------------------------
def resolved_stack(root: Path) -> str:
    """STACK.md sans commentaires ni clés vides, titres `##` abaissés en `###`.

    Les noms de section sont conservés tels quels : une fiche qui dit « lire
    `## Active Serving Surface` » trouve la même section, un niveau plus bas.
    """
    out: list[str] = []

    def drop_empty_section() -> None:
        if out and out[-1].startswith("### "):   # section sans contenu : rien à lire
            out.pop()
            if out and out[-1] == "":
                out.pop()

    for line in gas._stack_text(root).split("\n"):
        stripped = line.rstrip()
        if not stripped.strip():
            continue
        if stripped.startswith("# "):     # le titre du fichier
            continue
        if re.match(r"^\s*-?\s*[A-Za-z][\w-]*:\s*$", stripped):   # `Endpoint:` sans valeur
            continue
        if stripped.startswith("## "):
            drop_empty_section()
            out += ["", "#" + stripped]
            continue
        out.append(stripped)
    drop_empty_section()
    return "\n".join(out).strip()


def _cell(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(f"`{v}`" for v in value) or "—"
    if value in (None, ""):
        return "—"
    return f"`{value}`"


def _bounds(bounds: dict[str, Any]) -> str:
    keys = [("maxIterations", "it"), ("maxToolCalls", "outils"), ("timeoutSec", "s"), ("budgetUsd", "$")]
    parts = [f"{bounds[k]} {unit}" for k, unit in keys if k in bounds]
    return ", ".join(parts) or "—"


def render_system(ir: dict[str, Any] | None) -> str:
    if ir is None:
        return ("_IR absent : ce projet n'a pas été initialisé depuis un IR compilé._ "
                "Lancer `/sdda-topology {n}`, puis régénérer ce fichier.")
    lines: list[str] = []
    orch = ir.get("orchestration") or {}
    lines.append(f"Pattern `{orch.get('rootPattern', '?')}` · entrée `{orch.get('entryNode', '?')}` · "
                 f"maxHops `{orch.get('maxHops', '?')}`.")
    lines.append("")
    lines.append("| Agent | Tier | Outils | Bornes | CAPs servies |")
    lines.append("|---|---|---|---|---|")
    for agent in sorted(ir.get("agents") or [], key=lambda a: str(a.get("id"))):
        lines.append(f"| `{agent.get('id')}` | {_cell(agent.get('modelTier'))} | {_cell(agent.get('tools'))} | "
                     f"{_bounds(agent.get('bounds') or {})} | {_cell(agent.get('servesCaps'))} |")
    tools = sorted(ir.get("tools") or [], key=lambda t: str(t.get("id")))
    if tools:
        lines += ["", "| Outil | Nom exposé | Effet de bord | Timeout |", "|---|---|---|---|"]
        for tool in tools:
            lines.append(f"| `{tool.get('id')}` | {_cell(tool.get('name'))} | "
                         f"{_cell(tool.get('sideEffectClass'))} | {_cell(tool.get('timeoutSec'))} s |")
    retrievers = sorted(str(r.get("id")) for r in ir.get("retrievers") or [] if isinstance(r, dict))
    if retrievers:
        lines += ["", "Retrievers : " + ", ".join(f"`{r}`" for r in retrievers) + "."]
    lines += ["", "Le détail (schémas, erreurs, politique de refus) est dans l'IR, qui reste la source "
                  "close : n'implémenter que ce qui y est déclaré."]
    return "\n".join(lines)


def render_dependencies(ctx: gas.Context, report: Report) -> str:
    if ctx.language != gas.LANGUAGE:
        catalogs = [f"`{paths.rel(ctx.root, c) if c.is_relative_to(ctx.root) else c.name}`"
                    for c in gas.active_libs_catalogs(ctx.root)]
        return ("Épinglées par les catalogues actifs : " + (", ".join(catalogs) or "aucun") +
                ". Les versions du catalogue sont les seules qui fassent foi.")
    deps = gas.resolve_dependencies(ctx, Report(name="DEPS", target=str(ctx.root)))
    lines = ["Dérivées des `.libs.json` actifs et épinglées par `gen-app-skeleton` "
             "(ne jamais changer une version à la main) :", ""]
    lines += [f"- `{r}`" for r in deps.runtime] or ["- aucune"]
    lines += ["", "Atelier (`dev`) : " + ", ".join(f"`{r}`" for r in deps.dev)]
    return "\n".join(lines)


def _launch_and_tests(ctx: gas.Context) -> list[str]:
    """Ce que le FRAMEWORK lancera — lu dans les mêmes tables que le code, jamais recopié.

    `--executor cli` lance l'application par `LAUNCH_COMMANDS` ; la part `suites`
    de G3 joue ses tests par `LANGUAGE_TESTS`. Écrit ici, un `dev-*` sait quelle
    commande et quel point d'entrée il doit rendre vrais — c'est un contrat.
    """
    from sdda_lib.executors import LAUNCH_COMMANDS  # noqa: PLC0415
    from sdda_scripts.run_tool_suites import LANGUAGE_TESTS  # noqa: PLC0415

    out: list[str] = []
    launch = LAUNCH_COMMANDS.get(ctx.language or "")
    if launch:
        out.append("Lancement par les runners (`--executor cli`, `stacks/serving/cli.md` §3.5), depuis la racine "
                   "du dépôt : `" + " ".join(p.replace("{AppName}", ctx.app) for p in launch) + " run --json "
                   "--input-file -`")
    tests = LANGUAGE_TESTS.get(ctx.language or "")
    if tests:
        out.append("Tests L2 joués par G3 (`run-tool-suites`), depuis ce répertoire : `"
                   + " ".join(tests.command).replace("{gradle}", "./gradlew").replace("{junit}", "<rapport.xml>")
                   + "` — le nom affiché d'un test de contrat contient l'id du cas ; un test `network` le dit "
                   "dans sa classe ou son nom.")
    return out


def render_commands(ctx: gas.Context) -> str:
    if ctx.language != gas.LANGUAGE:
        return "\n".join([
            f"Voir la fiche `.sdda/stacks/lang/{ctx.language or '?'}.md` (build, tests, lancement). "
            "Le squelette de ce langage est écrit par `dev-backend`.", "",
            *(f"- {line}" for line in _launch_and_tests(ctx)),
        ])
    return "\n".join([
        f"Depuis `workspace/src/{ctx.app}/` — l'environnement est déjà installé par `project-init` :",
        "",
        "```bash",
        "uv sync                                   # après un changement de dépendances seulement",
        'uv run pytest -m "not network and not eval"   # L0-L2, LLM mocké',
        "uv run ruff check . && uv run mypy .",
        f"uv run {ctx.app} --help                    # la surface console",
        "```",
    ])


def render_tree(root: Path, ctx: gas.Context) -> str:
    owners = layer_owners(root)
    python = ctx.language == gas.LANGUAGE
    # Hors Python, ni `pyproject.toml` ni « le paquet » : affirmer une forme que
    # le langage actif n'a pas fait coder contre elle (`lang/{langage}.md` fait foi).
    root_files = "`pyproject.toml`, `README.md`, `.env`" if python else \
        f"fichiers de build de `lang/{ctx.language or '?'}.md`, `README.md`, `.env`"
    head = (f"Layout plat : `workspace/src/{ctx.app}/` EST le paquet `{ctx.app}`." if python else
            f"`workspace/src/{ctx.app}/` est la racine du projet ({ctx.language or '?'}).")
    lines = [head, "",
             "| Répertoire | Contenu | Propriétaire (seul à y écrire) |", "|---|---|---|",
             f"| racine ({root_files}) | projet et packaging | "
             f"`{owners.get('app', 'dev-backend')}` |"]
    for layer, role in LAYER_ROLE.items():
        if layer in owners:
            lines.append(f"| `{layer}/` | {role} | `{owners[layer]}` |")
    generated = ("le squelette (`gen-app-skeleton`), les outils de source (`gen-source-tools`), ce fichier."
                 if python else "ce fichier (le squelette de ce langage est écrit par `dev-backend`).")
    lines += ["", "Générés par script, jamais édités à la main (`--check` les compare à l'octet) : " + generated]
    return "\n".join(lines)


RULES = """\
- **L'IR est la source close.** Un agent, un outil ou une borne absent de l'IR n'existe pas : le signaler, ne pas l'ajouter.
- **Aucun prompt inline.** Un prompt vit dans `prompts/{agent}.system.md`, chargé par hash.
- **Aucune lecture de `.env`** (`[SECRET_READ_FORBIDDEN]`) : le code lit des NOMS de variables, via `config.py`.
- **Ce qui juge est hors d'atteinte** : `workspace/pipeline/{datasets,suites,baselines,calibration,fixtures}/` et `prompts/`, `skills/`, `rules/` ne s'écrivent par aucun `dev-*`.
- **Écrire dans SA couche seulement** (tableau §2) ; un besoin chez le voisin se dit dans la sortie, il ne s'écrit pas.
- **Toute borne est en code** (itérations, appels d'outil, délai, budget) — jamais dans le prompt seul."""


def render(root: Path, ctx: gas.Context, report: Report) -> str:
    ir = _ir(root, ctx.mission) if ctx.mission else None
    stack = resolved_stack(root)
    system = render_system(ir)
    frameworks = active_stacks(root, "Active Agent Framework")
    header = {
        "generated-by": "gen_app_context.py",
        "app": ctx.app,
        "mission": ctx.mission or "—",
        "harness": active_harness(root),
        "stack-hash": _short_hash(stack),
        "ir-hash": _short_hash(system),
    }
    project_rows = [
        ("AppName", ctx.app), ("MISSION", ctx.mission or "—"), ("Profil", active_profile(root)),
        ("Langage", ctx.language or "—"),
        ("Framework agentic", frameworks), ("Livrable", ctx.deliverable),
        ("Surface", ctx.surfaces), ("Fournisseur runtime", ctx.provider),
        ("Tiers runtime", [f"{k}={v}" for k, v in sorted(ctx.tier_map.items())]),
    ]
    parts = [
        "---",
        *[f"{k}: {v}" for k, v in header.items()],
        "---",
        "<!-- GÉNÉRÉ par gen_app_context.py (`project-init`) — ne pas éditer : toute retouche est "
        "perdue à la régénération, et `--check` la signale d'ici là. -->",
        "",
        f"# {ctx.app} — contexte projet",
        "",
        "> Lu par chaque agent `dev-*` et par `qa-tests` avant d'écrire une ligne. Il remplace la "
        "lecture de `workspace/stack/STACK.md` (la stack résolue est au §6) ; il ne remplace pas l'IR "
        f"(`workspace/.sys/.ir/{ctx.mission or '{n}'}-system.ir.json`), qui reste la source close.",
        "",
        "## 1. Projet",
        "",
        "| Clé | Valeur |",
        "|---|---|",
        *[f"| {k} | {_cell(v)} |" for k, v in project_rows],
        "",
        "## 2. Arborescence et propriétaires",
        "",
        render_tree(root, ctx),
        "",
        "## 3. Commandes",
        "",
        render_commands(ctx),
        "",
        "## 4. Dépendances épinglées",
        "",
        render_dependencies(ctx, report),
        "",
        "## 5. Système (extrait de l'IR)",
        "",
        system,
        "",
        "## 6. Stack résolue",
        "",
        "Sections de `STACK.md`, sans commentaires ni clés vides. Une fiche qui cite "
        "`## Active X` la trouve ici sous `### Active X`.",
        "",
        stack,
        "",
        "## 7. Règles de travail",
        "",
        RULES,
        "",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------
def run(root: Path, *, mode: str = "check", mission: str | None = None) -> Report:
    report = Report(name="GEN-APP-CONTEXT", target=str(root))
    if not paths.stack_md_path(root).is_file():
        report.error("STACK_MISSING", "STACK.md introuvable", fix="lancer `python bootstrap.py`")
        return report

    ctx = gas.Context.resolve(root, report)
    if mission:
        ctx.mission = str(mission)
    if not ctx.app or ctx.app.startswith("<"):
        report.error("STACK_PLACEHOLDER_UNRESOLVED", f"`AppName` absent ou non résolu ({ctx.app or '<vide>'})",
                     fix="renseigner `AppName` dans `## Project Config`",
                     location="workspace/stack/STACK.md ## Project Config")
        return report
    if ctx.mission and _ir(root, ctx.mission) is None:
        report.error("IR_NOT_FOUND", f"MISSION {ctx.mission} : IR absent ou illisible",
                     fix=f"compiler l'IR : python .sdda/sdda.py ir-compiler --mission {ctx.mission}",
                     location=paths.rel(root, paths.ir_path(root, ctx.mission)))
        return report

    problem = gas.app_name_problem(root, ctx.app, package=ctx.language == gas.LANGUAGE)
    if problem:
        report.error("CONFIG_VALUE_INVALID", f"`AppName: {ctx.app}` inutilisable : {problem}",
                     fix="un identifiant simple (`SupportDesk`) dans `## Project Config`",
                     location="workspace/stack/STACK.md ## Project Config")
        return report
    target = context_path(root, ctx.app)
    rel = paths.rel(root, target)
    content = render(root, ctx, report)
    exists = target.is_file()
    same = exists and markdown_io.read_text(target) == content

    written = False
    if mode == "write":
        if not same:
            atomic_write_text(target, content)   # atomique, LF sur tous les postes
            written = True
    elif not exists:
        report.error(CLS_NOT_INIT, f"{rel} introuvable : le projet n'a pas été initialisé",
                     fix=f"python .sdda/sdda.py project-init --mission {ctx.mission or '{n}'} "
                         "(lancé par /sdda-build STEP 3.0, avant tout dev-*)", location=rel)
    elif not same:
        report.error(CLS_STALE, f"{rel} ne correspond plus à STACK.md, à l'IR ou au framework",
                     fix=f"python .sdda/sdda.py gen-app-context --mission {ctx.mission or '{n}'} --write — "
                         "un contexte périmé fait coder contre une stack qui n'est plus la bonne",
                     location=rel)

    report.data.update({"app": ctx.app, "mission": ctx.mission, "path": rel, "bytes": len(content.encode("utf-8")),
                        "harness": active_harness(root), "written": written, "upToDate": same or written})
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Génère le fichier de contexte de l'application (0 token)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="défaut : comparer sans écrire, exit 1 si absent ou périmé")
    mode.add_argument("--write", action="store_true", help="écrire le fichier s'il est absent ou divergent")
    p.add_argument("--mission", default=None, help="numéro de la MISSION (défaut : l'unique MISSION du workspace)")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    return finish(run(resolve_root(args), mode="write" if args.write else "check", mission=args.mission), args)


if __name__ == "__main__":
    sys.exit(main())
