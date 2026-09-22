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
    - **une ligne par action** (`mkdir`, `rmdir`, `keep`, `write`) sur stdout ;
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
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib.errors import Report  # noqa: E402
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

    def _log(self, op: str, rel: str, detail: str = "") -> None:
        self.actions.append({"op": op, "path": f"workspace/{rel}", "applied": not self.dry_run,
                             **({"detail": detail} if detail else {})})

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


def migrate_to_v2(ctx: Context) -> None:
    """v1 -> v2 : feats / stack / src / proof / .sys.

    L'ordre compte : on DÉPLACE d'abord le contenu, on crée ensuite ce qui
    manque, on retire enfin les coquilles vides. Créer l'arbre avant de
    déplacer ferait trouver une destination déjà peuplée d'un `.gitkeep`, et
    `move` refuserait la collision sur chaque répertoire.
    """
    for old, new in MOVES_V2:
        ctx.move(old, new)
    for rel in WORKSPACE_TREE:
        ctx.mkdir(rel)
    for rel in GHOST_DIRS_V2:
        ctx.rmdir_if_empty(rel, "WORKSPACE_GHOST_DIR_NOT_EMPTY")


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
            print(f"  {prefix}{action['op']:<5} {action['path']}{detail}")
        if not report.data["actions"] and report.ok:
            print(f"  workspace déjà en v{report.data['reached']} — rien à faire")
        elif report.ok:
            verb = "à appliquer" if args.dry_run else "appliquée(s)"
            print(f"  migration v{report.data['from']} -> v{report.data['reached']} : "
                  f"{len(report.data['actions'])} action(s) {verb}")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
