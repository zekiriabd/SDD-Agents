#!/usr/bin/env python3
"""Fait monter un workspace jusqu'à `WORKSPACE_VERSION` — migrations numérotées, 0 token.

Un workspace amorcé par un bootstrap ancien porte l'arborescence de son époque :
des répertoires que plus rien ne lit (`.sys/.routing`, `.sys/.cache`,
`.sys/.reverse`), d'autres qui manquent depuis qu'un script les attend. Sans
version sur disque, rien ne distingue « pas encore migré » de « cassé ». Ce
script lit `workspace/.sys/workspace.json` (absent = version 0), applique dans
l'ordre chaque migration dont la cible est supérieure, et réécrit la version
après chacune.

Contrat :

    - **idempotent** : relancé sur un workspace à jour, il ne touche à rien et
      sort 0 ;
    - **une ligne par action** (`mkdir`, `rmdir`, `move`, `write`, `edit`,
      `remove`, `keep`) sur stdout — jamais une VALEUR de secret, seulement
      des noms ;
      `--json` remplace ces lignes par le rapport machine (`data.actions`) ;
    - `--dry-run` calcule et affiche les actions, n'écrit rien — pas même la
      version ;
    - une migration qui produit une erreur **ne bumpe pas** la version et
      arrête la chaîne : le prochain run la rejoue depuis le même point, et
      `smoke_check` continue de dire `[WORKSPACE_VERSION_OUTDATED]` tant que
      l'humain n'a pas tranché ;
    - exit 0 si tout est appliqué, 1 à la première erreur.

Ajouter une version = **une fonction + une entrée** dans `MIGRATIONS`, et
incrémenter `sdda_lib.workspace.WORKSPACE_VERSION`. `check_registry()` refuse
une chaîne qui ne va pas de 1 à `WORKSPACE_VERSION` sans trou.

Classes émises :
    [WORKSPACE_GHOST_DIR_NOT_EMPTY]  un répertoire fantôme contient autre chose
                                     que `.gitkeep` : il est conservé, à vider
                                     ou supprimer à la main
    [WORKSPACE_MIGRATION_REGISTRY_INVALID]  la chaîne de migrations est trouée
                                     ou ne rejoint pas WORKSPACE_VERSION

Usage :
    python .sdda/sdda.py migrate-workspace
    python .sdda/sdda.py migrate-workspace --dry-run
    python .sdda/sdda.py migrate-workspace --root <projet> --json
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.layered_config import read_project_section, read_stack_section_kv  # noqa: E402
from sdda_lib.workspace import (  # noqa: E402
    WORKSPACE_JSON_REL,
    WORKSPACE_VERSION,
    framework_version,
    read_workspace_version,
    write_workspace_version,
)
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402
from sdda_scripts.smoke_check import WORKSPACE_TREE  # noqa: E402

#: Répertoires créés par les bootstraps d'avant la v1 et lus par aucun script.
#: `.sys/.reverse` n'a même jamais été dans l'arborescence : c'est un reliquat.
GHOST_DIRS_V1: tuple[str, ...] = (".sys/.routing", ".sys/.cache", ".sys/.reverse")


# ---------------------------------------------------------------------------
# Contexte d'une migration : les opérations, journalisées et neutralisables
# ---------------------------------------------------------------------------
@dataclass
class Context:
    """Ce qu'une fonction de migration reçoit. Toute écriture passe par ici,
    pour que `--dry-run` soit un simple interrupteur et que chaque action
    laisse une ligne."""

    root: Path
    report: Report
    dry_run: bool
    actions: list[dict[str, Any]] = field(default_factory=list)
    #: Chemins qu'un `move` a vidés — réellement, ou qu'il aurait vidés en
    #: simulation. Sans cette mémoire, un `--dry-run` accusait les répertoires
    #: hérités d'être « non vides » alors que c'est LUI qui avait choisi de ne
    #: rien déplacer : la simulation rendait rouge une migration qui passe au
    #: vert dès qu'on l'exécute. Une simulation qui ment sur son propre
    #: résultat ne sert à rien — on la lance précisément pour décider si on
    #: ose.
    emptied: set[str] = field(default_factory=set)

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    def _log(self, op: str, rel: str, detail: str = "", *, at_root: bool = False) -> None:
        shown = rel if at_root else f"workspace/{rel}"
        self.actions.append({"op": op, "path": shown, "applied": not self.dry_run,
                             **({"detail": detail} if detail else {})})

    def write_text(self, rel: str, text: str, detail: str = "", *, at_root: bool = False) -> None:
        """Écrit `workspace/{rel}` (ou `{root}/{rel}` si `at_root`), journalisé, neutralisé en dry-run."""
        target = (self.root if at_root else self.workspace) / rel
        self._log("write" if not target.exists() else "edit", rel, detail, at_root=at_root)
        if not self.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")

    def move_file(self, old: str, new: str) -> bool:
        """Déplace UN fichier `workspace/{old}` -> `workspace/{new}` ; collision = l'original reste."""
        src, dst = self.workspace / old, self.workspace / new
        if not src.is_file():
            return False
        if dst.exists():
            self.report.warn("WORKSPACE_MIGRATION_COLLISION",
                             f"`workspace/{new}` existe déjà : l'original `workspace/{old}` reste en place",
                             "fusionner à la main ; la migration ne tranche pas entre deux versions d'un fichier",
                             f"workspace/{old}")
            return False
        self._log("move", old, f"-> {new}")
        self.emptied.add(old)          # la simulation doit savoir que ce fichier n'est plus là
        if not self.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        return True

    def remove_file(self, rel: str, detail: str = "", *, at_root: bool = False) -> bool:
        """Supprime `workspace/{rel}` (ou `{root}/{rel}`) — uniquement après que son contenu a été reporté ailleurs."""
        target = (self.root if at_root else self.workspace) / rel
        if not target.is_file():
            return False
        self._log("remove", rel, detail, at_root=at_root)
        self.emptied.add(rel)          # sinon `--dry-run` accuse le répertoire parent d'être encore plein
        if not self.dry_run:
            target.unlink()
        return True

    def mkdir(self, rel: str) -> bool:
        """Crée `workspace/{rel}` (+ `.gitkeep`, comme le bootstrap) s'il manque."""
        target = self.workspace / rel
        if target.is_dir():
            return False
        self._log("mkdir", rel)
        if not self.dry_run:
            target.mkdir(parents=True, exist_ok=True)
            if not any(target.iterdir()):
                (target / ".gitkeep").touch()
        return True

    def rmdir_if_empty(self, rel: str, cls: str) -> bool:
        """Supprime `workspace/{rel}` s'il ne contient rien d'autre que `.gitkeep`.

        Sinon : `cls` en erreur, le répertoire reste. Un fichier qu'on ne
        connaît pas n'est pas à nous à jeter.
        """
        target = self.workspace / rel
        if not target.exists():
            return False
        if self.dry_run and any(e == rel or e.startswith(rel + "/") for e in self.emptied):
            self._log("rmdir", rel, "après déplacement (simulation)")
            return True
        leftovers = sorted(p.name for p in target.iterdir() if p.name != ".gitkeep") if target.is_dir() else [target.name]
        if self.dry_run:
            leftovers = [n for n in leftovers if f"{rel}/{n}" not in self.emptied]
        if leftovers:
            self._log("keep", rel, f"{len(leftovers)} entrée(s) : {', '.join(leftovers[:5])}")
            self.report.error(cls, f"`workspace/{rel}` n'est plus dans l'arborescence mais contient "
                              f"{len(leftovers)} entrée(s) hors `.gitkeep` : {', '.join(leftovers[:5])}",
                              "déplacer ou supprimer son contenu à la main, puis relancer la migration",
                              f"workspace/{rel}")
            return False
        self._log("rmdir", rel)
        # Un parent doit savoir que son enfant a disparu. Sans cette ligne,
        # `--dry-run` retirait `evals/suites` puis accusait `evals` de le
        # contenir encore : la simulation se contredisait d'une ligne à l'autre.
        self.emptied.add(rel)
        if not self.dry_run:
            shutil.rmtree(target)
        return True

    def move(self, old: str, new: str) -> bool:
        """Déplace `workspace/{old}` vers `workspace/{new}`, contenu compris.

        Les migrations ne savaient que créer et supprimer. Une réorganisation
        d'arborescence a besoin de la troisième opération, et c'est la seule qui
        ne soit pas idempotente par nature : sans elle, un projet existant
        aurait vu le nouvel arbre apparaître à vide à côté de ses fichiers
        restés en place, et le pipeline aurait cherché une MISSION là où il n'y
        en a plus. Perdre le travail d'un utilisateur est le seul échec qu'une
        migration ne peut pas se permettre.
        """
        src, dst = self.workspace / old, self.workspace / new
        if not src.is_dir():
            return False
        entries = [e for e in src.iterdir() if e.name != ".gitkeep"]
        if not entries:
            self._log("skip", old, "vide : rien à déplacer")
            return False
        self._log("move", old, f"-> {new} ({len(entries)} entrée(s))")
        self.emptied.add(old)
        if self.dry_run:
            return True
        dst.mkdir(parents=True, exist_ok=True)
        for item in entries:
            target = dst / item.name
            if target.exists():
                self.report.warn("WORKSPACE_MIGRATION_COLLISION",
                                 f"`workspace/{new}/{item.name}` existe déjà : l'original reste en place",
                                 "fusionner à la main ; la migration ne tranche pas entre deux versions d'un fichier",
                                 f"workspace/{old}/{item.name}")
                continue
            shutil.move(str(item), str(target))
        if not [e for e in src.iterdir() if e.name != ".gitkeep"]:
            shutil.rmtree(src)
        return True

    def write_version(self, version: int) -> None:
        self._log("write", WORKSPACE_JSON_REL.removeprefix("workspace/"), f"workspaceVersion={version}")
        if not self.dry_run:
            write_workspace_version(self.root, version=version,
                                    written_by=f"migrate_workspace {framework_version()}")


# ---------------------------------------------------------------------------
# Les migrations — une fonction par version cible
# ---------------------------------------------------------------------------
#: L'arborescence telle qu'elle était EN v1, figée en dur.
#:
#: `migrate_to_v1` créait `WORKSPACE_TREE`, c'est-à-dire l'arbre COURANT. Tant
#: qu'il n'y avait qu'une version, cela ne se voyait pas ; à la deuxième, la
#: migration v1 se mettait à créer les répertoires de la v2, et la v2 déplaçait
#: ensuite du contenu vers des répertoires que la v1 venait d'inventer. Une
#: migration doit produire l'état de SON époque, sinon la chaîne ne décrit plus
#: une histoire mais seulement son dernier chapitre — et un workspace bloqué en
#: v1 par une erreur se retrouverait avec un arbre v2 à moitié créé.
#:
#: Seule la DERNIÈRE migration a le droit de référencer `WORKSPACE_TREE`.
TREE_V1: tuple[str, ...] = (
    "stack", "stack/sources",
    "contracts/dataaccess/schemas",
    "missions", "caps", "topology",
    "contracts/agents", "contracts/tools", "contracts/retrieval", "contracts/memory",
    "prompts",
    "datasets/golden", "datasets/holdout", "datasets/calibration", "datasets/adversarial",
    "evals/suites", "evals/baselines", "evals/reports", "evals/calibration",
    "traces/runs",
    "src", "docs",
    ".sys/.ir", ".sys/.context/adrs", ".sys/.context/packs",
    ".sys/.state", ".sys/.validation", ".sys/.audit",
)


def migrate_to_v1(ctx: Context) -> None:
    """v0 -> v1 : arborescence canonique complète, répertoires fantômes retirés."""
    for rel in TREE_V1:
        ctx.mkdir(rel)
    for rel in GHOST_DIRS_V1:
        ctx.rmdir_if_empty(rel, "WORKSPACE_GHOST_DIR_NOT_EMPTY")


#: v1 -> v2 : les quatre entrées du workspace. L'arbre portait douze
#: répertoires au même niveau qui mélangeaient quatre natures — spécification,
#: configuration, code, preuve — sans que rien ne le dise.
MOVES_V2: tuple[tuple[str, str], ...] = (
    ("missions", "feats/missions"),
    ("caps", "feats/caps"),
    ("topology", "feats/topology"),
    ("contracts", "feats/contracts"),
    ("docs/adr", "feats/decisions"),
    (".sys/.context/adrs", "feats/decisions"),
    ("docs", "feats/briefs"),
    ("prompts", "src/prompts"),
    ("datasets", "proof/datasets"),
    ("evals/suites", "proof/suites"),
    ("evals/baselines", "proof/baselines"),
    ("evals/calibration", "proof/calibration"),
    ("evals/reports", ".sys/reports"),
    ("traces", ".sys/traces"),
)

#: Répertoires vidés par `MOVES_V2` et qui n'existent plus dans l'arbre.
#:
#: **Du plus profond vers le plus haut**, et c'est une contrainte, pas un goût :
#: un sous-répertoire resté vide (jamais peuplé, donc jamais déplacé) empêche
#: son parent d'être retiré, et `rmdir_if_empty` signale alors un « contenu
#: inconnu » qui n'est qu'une coquille. La migration échouait sur un workspace
#: parfaitement sain dont l'utilisateur n'avait simplement pas encore écrit de
#: suite d'eval.
GHOST_DIRS_V2: tuple[str, ...] = (
    "evals/suites", "evals/baselines", "evals/calibration", "evals/reports", "evals",
    "docs/adr", "docs",
    ".sys/.context/adrs",
    "traces/runs", "traces",
)


#: L'arborescence telle qu'elle était EN v2, figée en dur — même raison que
#: `TREE_V1` : une migration produit l'état de son époque.
TREE_V2: tuple[str, ...] = (
    "stack", "stack/sources",
    "feats/missions", "feats/caps", "feats/topology",
    "feats/contracts/agents", "feats/contracts/tools", "feats/contracts/retrieval", "feats/contracts/memory",
    "feats/contracts/dataaccess/schemas", "feats/decisions", "feats/briefs",
    "src", "src/prompts",
    "proof/datasets/golden", "proof/datasets/holdout", "proof/datasets/calibration", "proof/datasets/adversarial",
    "proof/suites", "proof/baselines", "proof/calibration",
    ".sys/.ir", ".sys/.context/packs", ".sys/.state", ".sys/.validation", ".sys/.audit",
    ".sys/reports", ".sys/traces/runs",
)


def migrate_to_v2(ctx: Context) -> None:
    """v1 -> v2 : feats / stack / src / proof / .sys.

    L'ordre compte : on DÉPLACE d'abord le contenu, on crée ensuite ce qui
    manque, on retire enfin les coquilles vides. Créer l'arbre avant de
    déplacer ferait trouver une destination déjà peuplée d'un `.gitkeep`, et
    `move` refuserait la collision sur chaque répertoire.
    """
    for old, new in MOVES_V2:
        ctx.move(old, new)
    for rel in TREE_V2:
        ctx.mkdir(rel)
    for rel in GHOST_DIRS_V2:
        ctx.rmdir_if_empty(rel, "WORKSPACE_GHOST_DIR_NOT_EMPTY")


# ---------------------------------------------------------------------------
# v2 -> v3 : l'entrée de l'utilisateur tient en trois choses
# ---------------------------------------------------------------------------
#: Répertoires vidés par la v3 et absents du nouvel arbre. Du plus profond vers
#: le plus haut (cf. GHOST_DIRS_V2).
GHOST_DIRS_V3: tuple[str, ...] = (
    "stack/sources", "stack/topology",
    "feats/contracts/dataaccess/schemas", "feats/contracts/dataaccess",
)

STACK_REL = "stack/STACK.md"
_SECTION_RE = r"^## {title}\s*$"
_BULLET_KV_RE = re.compile(r"^(\s*-\s*)([A-Z][A-Z0-9_]*)(\s*:\s*)(.*?)(\s*(?:#.*)?)$")
_ENV_REF_RE = re.compile(r"^\$\{[A-Z][A-Z0-9_]*\}$")
_TOP_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*):")
_MANIFEST_CHILD_RE = re.compile(r"^\s*(?:#\s*)?-\s*(?:path:|\{)")
_MMD_LINE_RE = re.compile(r"^Fichier\s*:\s*`workspace/feats/topology/\d+-topology\.mmd`.*$", re.M)


def _section_span(text: str, title: str) -> tuple[int, int] | None:
    """(début, fin) du CORPS de `## {title}` — jusqu'au prochain `## ` ou la fin."""
    m = re.search(_SECTION_RE.format(title=re.escape(title)), text, re.M)
    if not m:
        return None
    start = m.end()
    nxt = re.compile(r"^## ", re.M).search(text, start)
    return start, (nxt.start() if nxt else len(text))


def _replace_section_body(text: str, title: str, new_body: str) -> str:
    span = _section_span(text, title)
    if span is None:
        return text
    start, end = span
    body = new_body if new_body.startswith("\n") else "\n" + new_body
    if not body.endswith("\n"):
        body += "\n"
    return text[:start] + body + text[end:]


def _section_body(text: str, title: str) -> str | None:
    span = _section_span(text, title)
    return text[span[0]:span[1]] if span else None


def _template_section_body(title: str) -> str | None:
    """Le corps d'une section du gabarit courant — pour réaligner un STACK.md sans deviner."""
    tmpl = paths.FRAMEWORK_SDDA_DIR / "templates" / "STACK.md.template"
    if not tmpl.is_file():
        return None
    return _section_body(markdown_io.read_text(tmpl), title)


def _is_clear_value(value: str) -> bool:
    v = value.strip()
    if not v or _ENV_REF_RE.match(v) or v.startswith("<") or v.startswith("{{"):
        return False
    return v.lower() not in ("n/a", "none", "-", "~", "null")


def _env_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            names.add(s.split("=", 1)[0].strip().removeprefix("export").strip())
    return names


def _app_env_rel(ctx: Context) -> str:
    """`workspace/src/{App}/.env` — le `.env` vit avec le livrable, jamais à la racine du dépôt.

    L'application générée consomme la clé et part de `workspace/src/{App}/` en
    exécutable ou en conteneur ; un `.env` à la racine restait hors du paquet.
    Sans `AppName`, on retombe sur `App`, comme `gen_app_skeleton`.
    """
    app = str(read_project_section(ctx.root).get("AppName") or "App").strip() or "App"
    return paths.env_rel(app)


def v3_secrets_to_env(ctx: Context) -> None:
    """Les VALEURS de `## Active Secrets` et des `DB_*` sortent vers `src/{App}/.env` ; STACK.md garde `${NOM}`.

    STACK.md devient versionnable à cet instant précis : c'est la seule étape de
    la v3 qui touche à un secret, et elle ne le journalise jamais — le détail de
    l'action ne porte que des NOMS. Un `.env` laissé à la racine du dépôt par
    une v3 antérieure est d'abord rapatrié avec l'application.
    """
    stack = ctx.workspace / STACK_REL
    if not stack.is_file():
        return
    text = markdown_io.read_text(stack)
    env_rel = _app_env_rel(ctx)
    env_path = ctx.root / env_rel
    _v3_env_to_app(ctx, env_rel)
    known = _env_names(env_path)
    moved: list[tuple[str, str]] = []
    for title in ("Active Secrets", "Active Data Access"):
        body = _section_body(text, title)
        if body is None:
            continue
        new_lines: list[str] = []
        for line in body.split("\n"):
            m = _BULLET_KV_RE.match(line)
            if m and not line.lstrip().startswith("#") and _is_clear_value(m.group(4)):
                name = m.group(2)
                if name not in known and name not in {n for n, _ in moved}:
                    moved.append((name, m.group(4).strip()))
                line = f"{m.group(1)}{name}{m.group(3)}${{{name}}}{m.group(5)}"
            new_lines.append(line)
        text = _replace_section_body(text, title, "\n".join(new_lines))
    if not moved:
        return
    existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    chunk = "".join(f"{n}={v}\n" for n, v in moved)
    header = "" if existing else ("# SDD_Agents — valeurs des secrets de L'APPLICATION. Gitignoré. "
                                  "STACK.md n'en porte que les noms (${NOM}).\n")
    sep = "" if not existing or existing.endswith("\n") else "\n"
    ctx.write_text(env_rel, existing + sep + header + "# extrait de workspace/stack/STACK.md par migrate-workspace (v3)\n" + chunk,
                   f"{len(moved)} variable(s) : {', '.join(n for n, _ in moved)}", at_root=True)
    ctx.write_text(STACK_REL, text, "valeurs remplacées par ${NOM} : " + ", ".join(n for n, _ in moved))


def _v3_env_to_app(ctx: Context, env_rel: str) -> None:
    """Un `.env` à la racine du dépôt (première v3) rejoint `workspace/src/{App}/.env`.

    Déplacé, pas copié : deux fichiers de secrets, c'est un qu'on oublie de
    faire tourner. Si la cible existe déjà, les lignes de la racine dont le NOM
    n'y est pas encore sont ajoutées, et la racine est supprimée. Aucune valeur
    n'est journalisée.
    """
    src = ctx.root / ".env"
    if not src.is_file():
        return
    dst = ctx.root / env_rel
    src_text = src.read_text(encoding="utf-8")
    if dst.is_file():
        known = _env_names(dst)
        extra = [l for l in src_text.split("\n")
                 if "=" in l and not l.lstrip().startswith("#") and l.split("=", 1)[0].strip() not in known]
        if extra:
            base = dst.read_text(encoding="utf-8")
            sep = "" if not base or base.endswith("\n") else "\n"
            ctx.write_text(env_rel, base + sep + "\n".join(extra) + "\n",
                           f"{len(extra)} variable(s) rapatriée(s) depuis .env (racine)", at_root=True)
    else:
        ctx.write_text(env_rel, src_text, "rapatrié depuis .env (racine) — le .env vit avec l'application", at_root=True)
    ctx.remove_file(".env", "remplacé par " + env_rel, at_root=True)


def v3_gitignore(ctx: Context) -> None:
    """STACK.md sort du .gitignore (il ne porte plus de valeur) ; `workspace/src/*/.env` y entre."""
    gi = ctx.root / ".gitignore"
    lines = gi.read_text(encoding="utf-8").split("\n") if gi.is_file() else []
    kept = [l for l in lines if l.strip() not in ("workspace/stack/STACK.md", "workspace/stack/STACK.md*")]
    changed = len(kept) != len(lines)
    present = {l.strip() for l in kept}
    if not present & {"workspace/src/*/.env", "workspace/**/.env", ".env"}:
        if kept and kept[-1].strip():
            kept.append("")
        kept += ["# SDD_Agents — les valeurs des secrets vivent avec l'application ; STACK.md (versionné) n'en porte que les noms",
                 "workspace/src/*/.env"]
        changed = True
    if changed:
        ctx.write_text(".gitignore", "\n".join(kept).rstrip("\n") + "\n",
                       "STACK.md versionné, .env ignoré", at_root=True)


def _mission_name(ctx: Context, number: str) -> str:
    for p in sorted((ctx.workspace / "feats" / "missions").glob(f"{number}-*.md")):
        return p.stem.split("-", 1)[1] if "-" in p.stem else p.stem
    return "Mission"


def v3_roster_to_markdown(ctx: Context) -> None:
    """`stack/topology/{n}-roster.yml` -> `feats/topology/{n}-roster.md` (YAML dans un bloc)."""
    from sdda_scripts.roster import wrap_roster_markdown  # import tardif : chaîne d'imports lourde

    src_dir = ctx.workspace / "stack" / "topology"
    for yml in sorted(src_dir.glob("*-roster.y*ml")) if src_dir.is_dir() else []:
        number = yml.name.split("-", 1)[0]
        target_rel = f"feats/topology/{number}-roster.md"
        if (ctx.workspace / target_rel).exists():
            ctx.report.warn("WORKSPACE_MIGRATION_COLLISION",
                            f"`workspace/{target_rel}` existe déjà : `workspace/stack/topology/{yml.name}` reste en place",
                            "fusionner à la main", f"workspace/stack/topology/{yml.name}")
            continue
        yaml_text = markdown_io.read_text(yml)
        try:
            num = int(number)
        except ValueError:
            num = 0
        ctx.write_text(target_rel, wrap_roster_markdown(num, _mission_name(ctx, number), yaml_text),
                       f"depuis stack/topology/{yml.name}")
        ctx.remove_file(f"stack/topology/{yml.name}", "reporté en Markdown")

    # STACK.md : les clés de localisation du roster n'ont plus d'objet.
    stack = ctx.workspace / STACK_REL
    if not stack.is_file():
        return
    text = markdown_io.read_text(stack)
    body = _section_body(text, "Active Agent Topology")
    if body is None or not re.search(r"^RosterManifest(Root|s)\s*:", body, re.M):
        return
    lines, skip = [], False
    for line in body.split("\n"):
        if re.match(r"^RosterManifests\s*:", line):
            skip = True
            continue
        if re.match(r"^RosterManifestRoot\s*:", line):
            continue
        if skip and _MANIFEST_CHILD_RE.match(line):
            continue
        skip = False
        lines.append(line)
    remaining = "\n".join(lines)
    if not any(l.strip() and not l.lstrip().startswith("#") for l in lines):
        remaining = _template_section_body("Active Agent Topology") or remaining
    ctx.write_text(STACK_REL, _replace_section_body(text, "Active Agent Topology", remaining),
                   "## Active Agent Topology : RosterManifestRoot/RosterManifests retirés (convention feats/topology/{n}-roster.md)")


def v3_inline_graphs(ctx: Context) -> None:
    """`{n}-topology.mmd` entre dans le bloc ```mermaid de `{n}-topology.md`, puis disparaît."""
    topo = ctx.workspace / "feats" / "topology"
    for mmd in sorted(topo.glob("*-topology.mmd")) if topo.is_dir() else []:
        number = mmd.name.split("-", 1)[0]
        md_rel = f"feats/topology/{number}-topology.md"
        md = ctx.workspace / md_rel
        graph = markdown_io.read_text(mmd).strip("\n") + "\n"
        if md.is_file():
            text = markdown_io.read_text(md)
            replaced = markdown_io.replace_first_fenced_block(text, "mermaid", graph)
            if replaced is None:
                block = f"\n```mermaid\n{graph}```\n"
                span = _section_span(text, "4. Le graphe")
                replaced = (text[:span[1]].rstrip("\n") + "\n" + block + text[span[1]:]) if span \
                    else text.rstrip("\n") + "\n\n---\n\n## 4. Le graphe\n" + block
            replaced = _MMD_LINE_RE.sub("Le graphe ci-dessous est compilé dans l'IR ; il n'existe nulle part ailleurs.", replaced)
        else:
            replaced = (f"# TOPOLOGY: {number}\n\n## 4. Le graphe\n\n```mermaid\n{graph}```\n")
            ctx.report.warn("WORKSPACE_MIGRATION_COLLISION",
                            f"`workspace/{md_rel}` n'existait pas : créé autour du graphe de `{mmd.name}`",
                            "compléter la topologie", md_rel)
        ctx.write_text(md_rel, replaced, f"graphe de {mmd.name} inline")
        ctx.remove_file(f"feats/topology/{mmd.name}", "reporté dans le bloc ```mermaid")


def v3_schemas_to_src(ctx: Context) -> None:
    """Les schémas figés partent avec le code : `src/{App}/data/schemas/`."""
    old = "feats/contracts/dataaccess/schemas"
    src = ctx.workspace / old
    if not src.is_dir() or not [e for e in src.iterdir() if e.name != ".gitkeep"]:
        return
    app = str(read_project_section(ctx.root).get("AppName") or "").strip()
    if not app:
        ctx.report.error("WORKSPACE_GHOST_DIR_NOT_EMPTY",
                         f"`workspace/{old}` contient des schémas figés mais STACK.md n'a pas d'`AppName` : "
                         "impossible de savoir dans quel paquet les ranger",
                         "renseigner `AppName` dans ## Project Config puis relancer la migration", f"workspace/{old}")
        return
    ctx.move(old, paths.rel(ctx.workspace, paths.app_src_root(ctx.root, app) / "data" / "schemas"))


def _manifest_blocks(text: str) -> list[tuple[str, list[str]]]:
    """Les blocs de premier niveau `Stores:` / `Sources:` d'un manifeste, lignes comprises (commentaires gardés)."""
    blocks: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in text.split("\n"):
        m = _TOP_KEY_RE.match(line)
        if m:
            key = m.group(1)
            current = (key, []) if key.lower() in ("stores", "sources") else None
            if current:
                blocks.append(current)
            continue
        if current is not None:
            current[1].append(line)
    for _key, lines in blocks:
        while lines and not lines[-1].strip():
            lines.pop()
    return blocks


def v3_sources_inline(ctx: Context) -> None:
    """Les manifestes `stack/sources/*.yml` rentrent dans `## Active Data Sources` ; un `mcp.json` monte à côté de STACK.md."""
    stack = ctx.workspace / STACK_REL
    if not stack.is_file():
        return
    section = read_stack_section_kv(ctx.root, "Active Data Sources")
    declared = section.get("SourceManifests") or []
    if not isinstance(declared, list) or not declared:
        return
    old_root = str(section.get("SourceManifestRoot") or "workspace/stack/sources").strip()
    root_dir = ctx.root / old_root
    text = markdown_io.read_text(stack)
    body = _section_body(text, "Active Data Sources")
    if body is None:
        return
    lines = body.split("\n")
    kept_manifests: list[str] = []

    for raw in declared:
        entry = {"path": raw} if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
        rel_path = str(entry.get("path") or "").strip()
        kind = str(entry.get("kind") or "sdda-sources").strip()
        if not rel_path:
            continue
        src = root_dir / rel_path
        if kind == "mcp-config":
            new_rel = f"stack/{Path(rel_path).name}"
            if src.is_file() and paths.rel(ctx.workspace, src) != new_rel:
                ctx.move_file(paths.rel(ctx.workspace, src), new_rel)
            kept_manifests.append(f"  - {{ path: {Path(rel_path).name}, kind: mcp-config }}")
            continue
        if not src.is_file():
            continue
        for key, item_lines in _manifest_blocks(markdown_io.read_text(src)):
            idx = next((i for i, l in enumerate(lines) if re.match(rf"^{re.escape(key)}\s*:\s*(#.*)?$", l)), None)
            if idx is None:
                lines += ["", f"{key}:"]
                idx = len(lines) - 1
            lines[idx + 1:idx + 1] = item_lines
        ctx.remove_file(paths.rel(ctx.workspace, src), "reporté inline dans ## Active Data Sources")

    # Les clés de localisation : retirées, ou réduites aux manifestes MCP conservés.
    out, skip = [], False
    for line in lines:
        if re.match(r"^SourceManifests\s*:", line):
            skip = True
            if kept_manifests:
                out += ["SourceManifestRoot: workspace/stack", "SourceManifests:", *kept_manifests]
            continue
        if re.match(r"^SourceManifestRoot\s*:", line):
            continue
        if skip and _MANIFEST_CHILD_RE.match(line):
            continue
        skip = False
        out.append(line)
    ctx.write_text(STACK_REL, _replace_section_body(text, "Active Data Sources", "\n".join(out)),
                   "## Active Data Sources : manifestes rapatriés inline")


def v3_seed_from_feats(ctx: Context) -> None:
    """Tout ce qui n'est pas du Markdown sous `feats/` est de la vérité terrain : `proof/seed/`."""
    feats = ctx.workspace / "feats"
    if not feats.is_dir():
        return
    for p in sorted(feats.rglob("*")):
        if not p.is_file() or p.suffix.lower() == ".md" or p.name == ".gitkeep":
            continue
        rel = paths.rel(ctx.workspace, p)
        if rel.startswith("feats/contracts/dataaccess/schemas/"):
            continue  # les schémas ont leur destination (v3_schemas_to_src) ; s'ils restent, le fantôme le dira
        ctx.move_file(rel, f"proof/seed/{p.name}")


def migrate_to_v3(ctx: Context) -> None:
    """v2 -> v3 : STACK.md versionné (secrets dans .env), feats/ en Markdown seul, proof/seed/.

    L'ordre suit la même règle qu'en v2 — reporter le contenu d'abord, créer
    l'arbre ensuite, retirer les coquilles en dernier — avec une contrainte de
    plus : les schémas et le roster ont une destination PRÉCISE et passent
    avant le balayage générique de `feats/`, qui ne connaît que `proof/seed/`.
    """
    v3_secrets_to_env(ctx)
    v3_gitignore(ctx)
    v3_roster_to_markdown(ctx)
    v3_inline_graphs(ctx)
    v3_schemas_to_src(ctx)
    v3_sources_inline(ctx)
    v3_seed_from_feats(ctx)
    for rel in WORKSPACE_TREE:
        ctx.mkdir(rel)
    for rel in GHOST_DIRS_V3:
        ctx.rmdir_if_empty(rel, "WORKSPACE_GHOST_DIR_NOT_EMPTY")


def v4_flatten_app(ctx: Context) -> None:
    """`src/{App}/*` remonte dans `src/{App}/` ; la coquille `src/{App}/src/` disparaît.

    Layout plat, celui de SDD_Pro : le répertoire de l'application EST le
    paquet. Les fichiers de projet déjà à la racine (`pyproject.toml`, `.env`,
    `README.md`) ne bougent pas ; `app_config.json` est réécrit pour que
    `workspaceRoot` pointe deux niveaux plus haut au lieu de quatre ; un
    `pyproject.toml` d'ancien layout (`packages = ["src/{App}"]`) est signalé
    — il se régénère (`gen-app-skeleton --write`), il ne se corrige pas à la main.
    """
    app = str(read_project_section(ctx.root).get("AppName") or "").strip()
    if not app:
        return
    old_rel = f"src/{app}/src/{app}"
    old = ctx.workspace / old_rel
    if not old.is_dir():
        return

    def lift(rel_dir: str) -> None:
        # Fusion RÉPERTOIRE par répertoire, fichier par fichier : un `data/` déjà
        # présent à destination (les schémas de la v3) n'empêche pas les outils
        # de l'ancien `data/` de le rejoindre. Seule une collision de FICHIER
        # laisse l'original en place (`move_file`) — deux versions d'un même
        # fichier, la migration ne tranche pas.
        for entry in sorted((ctx.workspace / old_rel / rel_dir).iterdir()) if rel_dir else sorted(old.iterdir()):
            if entry.name == ".gitkeep":
                continue
            sub = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            dst = ctx.workspace / "src" / app / sub
            if entry.is_dir():
                if dst.is_dir():
                    lift(sub)
                    ctx.rmdir_if_empty(f"{old_rel}/{sub}", "WORKSPACE_GHOST_DIR_NOT_EMPTY")
                else:
                    ctx.move(f"{old_rel}/{sub}", f"src/{app}/{sub}")
            else:
                ctx.move_file(f"{old_rel}/{sub}", f"src/{app}/{sub}")

    lift("")
    ctx.rmdir_if_empty(old_rel, "WORKSPACE_GHOST_DIR_NOT_EMPTY")
    ctx.rmdir_if_empty(f"src/{app}/src", "WORKSPACE_GHOST_DIR_NOT_EMPTY")

    cfg = ctx.workspace / "src" / app / "app_config.json"
    if cfg.is_file():
        text = cfg.read_text(encoding="utf-8")
        if '"workspaceRoot": "../../../.."' in text:
            ctx.write_text(f"src/{app}/app_config.json", text.replace('"workspaceRoot": "../../../.."', '"workspaceRoot": "../.."'),
                           "workspaceRoot : ../.. (layout plat)")
    pyproject = ctx.workspace / "src" / app / "pyproject.toml"
    if pyproject.is_file() and f'packages = ["src/{app}"]' in pyproject.read_text(encoding="utf-8"):
        ctx.report.warn("WORKSPACE_MIGRATION_COLLISION",
                        f"`workspace/src/{app}/pyproject.toml` déclare encore le src layout",
                        "python .sdda/sdda.py gen-app-skeleton --write — le fichier est généré, il se régénère",
                        f"workspace/src/{app}/pyproject.toml")


def migrate_to_v4(ctx: Context) -> None:
    """v3 -> v4 : layout plat de l'application (`workspace/src/{App}/` est le paquet)."""
    v4_flatten_app(ctx)
    for rel in WORKSPACE_TREE:
        ctx.mkdir(rel)


_PROMPT_REF_RE = re.compile(r"workspace/src/prompts/")


def v5_prompts_into_app(ctx: Context) -> None:
    """`src/prompts/*.system.md` -> `src/{App}/prompts/` ; les contrats d'agents suivent.

    Un prompt est un actif d'exécution : à côté de l'application, il ne partait
    pas avec elle. `skills/`, `rules/` et `memory/` naissent vides à côté (le
    squelette les documente) ; `## 3. Prompt` de chaque contrat d'agent est
    réécrit vers le nouveau chemin — sinon l'IR le refuserait.
    """
    app = str(read_project_section(ctx.root).get("AppName") or "").strip()
    if not app:
        return
    old = ctx.workspace / "src" / "prompts"
    if old.is_dir():
        for p in sorted(old.glob("*.system.md")):
            ctx.move_file(f"src/prompts/{p.name}", f"src/{app}/prompts/{p.name}")
        ctx.rmdir_if_empty("src/prompts", "WORKSPACE_GHOST_DIR_NOT_EMPTY")
    for sub in ("prompts", "skills", "rules", "memory"):
        ctx.mkdir(f"src/{app}/{sub}")
    contracts = ctx.workspace / "feats" / "contracts" / "agents"
    for c in sorted(contracts.glob("*.agent.md")) if contracts.is_dir() else []:
        text = markdown_io.read_text(c)
        if _PROMPT_REF_RE.search(text):
            ctx.write_text(f"feats/contracts/agents/{c.name}", _PROMPT_REF_RE.sub(f"workspace/src/{app}/prompts/", text),
                           "§3 Prompt : chemin du prompt dans l'application")


def migrate_to_v5(ctx: Context) -> None:
    """v4 -> v5 : prompts, skills, rules et memory DANS l'application."""
    v5_prompts_into_app(ctx)
    for rel in WORKSPACE_TREE:
        ctx.mkdir(rel)


@dataclass(frozen=True)
class Migration:
    target: int                       # version atteinte quand `apply` a réussi
    summary: str                      # une ligne, pour le journal
    apply: Callable[[Context], None]


#: Ordonnées par `target`, consécutives de 1 à WORKSPACE_VERSION. La version
#: est écrite par le moteur après chaque migration : une fonction de migration
#: ne touche jamais à `workspace.json` elle-même.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "arborescence canonique + retrait de .sys/.routing, .sys/.cache, .sys/.reverse", migrate_to_v1),
    Migration(2, "quatre entrées : feats/ (spec) · stack/ · src/ (prompts compris) · proof/ (jamais un dev-*) · .sys/", migrate_to_v2),
    Migration(3, "STACK.md versionné (valeurs dans .env) · feats/ en Markdown seul (roster {n}-roster.md, graphe inline) · "
                 "schémas figés sous src/ · sources inline · vérité terrain proof/seed/", migrate_to_v3),
    Migration(4, "layout plat de l'application : workspace/src/{App}/ EST le paquet (comme SDD_Pro) — "
                 "plus de src/{App}/src/{App}/", migrate_to_v4),
    Migration(5, "prompts, skills, rules et memory DANS l'application (src/{App}/prompts/ …) — "
                 "plus de src/prompts/ à côté", migrate_to_v5),
)


def check_registry(report: Report) -> bool:
    targets = [m.target for m in MIGRATIONS]
    expected = list(range(1, WORKSPACE_VERSION + 1))
    if targets != expected:
        report.error("WORKSPACE_MIGRATION_REGISTRY_INVALID",
                     f"MIGRATIONS cible {targets}, attendu {expected} (WORKSPACE_VERSION={WORKSPACE_VERSION})",
                     "une entrée par version, consécutives, jusqu'à sdda_lib.workspace.WORKSPACE_VERSION")
        return False
    return True


# ---------------------------------------------------------------------------
# Moteur
# ---------------------------------------------------------------------------
def run(root: Path, *, dry_run: bool = False) -> Report:
    report = Report(name="MIGRATE", target=str(root))
    current = read_workspace_version(root) or 0
    report.data.update({"from": current, "to": WORKSPACE_VERSION, "dryRun": dry_run,
                        "applied": [], "actions": []})
    if not check_registry(report):
        return report

    ctx = Context(root=root, report=report, dry_run=dry_run, actions=report.data["actions"])
    for migration in MIGRATIONS:
        if migration.target <= current:
            continue
        errors_before = len(report.errors)
        migration.apply(ctx)
        if len(report.errors) > errors_before:
            report.data["stoppedAt"] = migration.target
            break
        ctx.write_version(migration.target)
        report.data["applied"].append(migration.target)
        current = migration.target
    report.data["reached"] = current
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Migre workspace/ vers la version courante du framework (0 token)")
    add_common_args(p)
    p.add_argument("--dry-run", action="store_true", help="afficher les actions sans rien écrire")
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = run(root, dry_run=args.dry_run)
    if not args.json:
        prefix = "(dry-run) " if args.dry_run else ""
        for action in report.data["actions"]:
            detail = f"  — {action['detail']}" if action.get("detail") else ""
            print(f"  {prefix}{action['op']:<6} {action['path']}{detail}")
        if not report.data["actions"] and report.ok:
            print(f"  workspace déjà en v{report.data['reached']} — rien à faire")
        elif report.ok:
            verb = "à appliquer" if args.dry_run else "appliquée(s)"
            print(f"  migration v{report.data['from']} -> v{report.data['reached']} : "
                  f"{len(report.data['actions'])} action(s) {verb}")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
