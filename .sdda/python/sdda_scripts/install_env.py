#!/usr/bin/env python3
"""Copie `workspace/assets/.env` vers `workspace/src/{App}/.env` — 0 token, aucune valeur affichée.

C'est l'étape qui CRÉE le projet qui l'appelle : `gen-app-skeleton --write`,
lancé par `dev-backend` en phase 3.0, pose le `.env` dans le répertoire qu'il
vient d'écrire. La commande reste pour la reprise à la main (clé changée après
le build) et pour `/sdda-eval`, qui l'exige avec `--require`.

L'humain dépose ses entrées là où il dépose le reste : `stack/`, `feats/`,
`assets/`, `seed/`. Le runtime, lui, lit `src/{App}/.env` : c'est de là que
l'application part en exécutable ou en conteneur, avec le fichier qui porte les
clés de ses Runtime Models. Ce script fait le pont, sans LLM, parce qu'aucun
agent ne lit un fichier de secrets (`audit_ownership.is_secret_file`, tenu par
les hooks de lecture).

Ce qu'il écrit et ce qu'il dit :

- il copie l'octet près, et ne réécrit pas une cible déjà identique ;
- il ne rapporte que des NOMS de variables, jamais une valeur ;
- il confronte ces noms à ceux que `STACK.md` déclare (`## Active Secrets`,
  et les `*_env` des stores) : une variable déclarée absente du fichier est
  `[SECRET_VAR_UNDECLARED]`, parce que l'application échouerait au premier
  appel qui en a besoin — longtemps après le build, en production ;
- `assets/.env` absent : un avertissement, pas une erreur. La clé n'est
  nécessaire qu'à partir des évaluations ; exiger le fichier dès la phase 0
  empêcherait d'éliciter une MISSION sans compte fournisseur.

Usage :
    python .sdda/sdda.py install-env            # copie si besoin
    python .sdda/sdda.py install-env --check    # dit s'il faut copier, n'écrit rien
    python .sdda/sdda.py install-env --require  # absent = erreur (avant les evals)
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.layered_config import app_name  # noqa: E402
from sdda_scripts._common import add_common_args, finish, resolve_root  # noqa: E402

_NAME_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", re.M)
_DECLARED_RE = re.compile(r"^\s*-\s*([A-Z][A-Z0-9_]*)\s*:", re.M)
_STORE_ENV_RE = re.compile(r"\b[a-z_]*_env\s*:\s*\$?\{?([A-Z][A-Z0-9_]*)\}?")


def env_names(path: Path) -> set[str]:
    """Les NOMS définis dans un fichier `.env`. La valeur n'est jamais retenue."""
    if not path.is_file():
        return set()
    return set(_NAME_RE.findall(path.read_text(encoding="utf-8", errors="replace")))


def _uncommented(text: str) -> str:
    """Le texte sans ses lignes `# …` : les exemples du gabarit ne déclarent rien.

    `## Active Data Sources` porte en commentaire des stores d'exemple
    (`key_env: CRM_API_KEY`, `access_key_env: S3_ACCESS_KEY`) : les lire
    réclamait au `.env` de chaque projet des clés qu'aucune source active ne cite.
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def declared_names(root: Path) -> set[str]:
    """Les variables que STACK.md déclare : `## Active Secrets` et les `*_env` des stores."""
    stack = paths.stack_md_path(root)
    if not stack.is_file():
        return set()
    text = markdown_io.read_text(stack)
    names = set(_DECLARED_RE.findall(_uncommented(markdown_io.section_body(text, "Active Secrets") or "")))
    names |= set(_STORE_ENV_RE.findall(_uncommented(markdown_io.section_body(text, "Active Data Sources") or "")))
    return names


def run(root: Path, *, write: bool, require: bool, target: Path | None = None) -> Report:
    """`target` : le `.env` du projet généré ; défaut `workspace/src/{AppName}/.env`."""
    report = Report(name="INSTALL-ENV", target=str(root))
    source = paths.env_source_path(root)
    target = target or paths.env_path(root, app_name(root))
    report.data.update({"source": paths.rel(root, source), "target": paths.rel(root, target)})

    if not source.is_file():
        msg = f"`{paths.ENV_SOURCE_REL}` absent : l'application générée n'aura pas ses clés"
        fix = (f"déposer le fichier de secrets dans {paths.ENV_SOURCE_REL} (gitignoré), "
               "avec les variables que STACK.md nomme ; puis relancer install-env")
        if require:
            report.error("SECRET_FILE_MISSING", msg, fix, paths.ENV_SOURCE_REL)
        else:
            report.warn("SECRET_FILE_MISSING", msg, fix, paths.ENV_SOURCE_REL)
        report.data["copied"] = False
        return report

    have = env_names(source)
    missing = sorted(declared_names(root) - have)
    for name in missing:
        report.error("SECRET_VAR_UNDECLARED",
                     f"`{name}` est déclarée dans STACK.md mais absente de {paths.ENV_SOURCE_REL}",
                     f"ajouter `{name}=…` dans {paths.ENV_SOURCE_REL}", paths.ENV_SOURCE_REL)
    report.data["names"] = sorted(have)

    same = target.is_file() and target.read_bytes() == source.read_bytes()
    report.data["upToDate"] = same
    if same:
        report.data["copied"] = False
        return report
    if not write:
        report.warn("SECRET_FILE_MISSING",
                    f"{paths.rel(root, target)} {'diffère de' if target.is_file() else 'absent, à copier depuis'} "
                    f"{paths.ENV_SOURCE_REL}", "python .sdda/sdda.py install-env", paths.rel(root, target))
        report.data["copied"] = False
        return report
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    report.data["copied"] = True
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sdda install-env", description=__doc__.splitlines()[0])
    p.add_argument("--check", action="store_true", help="dire s'il faut copier, sans rien écrire")
    p.add_argument("--require", action="store_true", help="assets/.env absent = erreur (avant les evals)")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = run(root, write=not args.check, require=args.require)
    if not args.json:
        d = report.data
        state = "copié" if d.get("copied") else ("à jour" if d.get("upToDate") else "non copié")
        print(f"{d.get('source')} -> {d.get('target')} : {state} · variables : {', '.join(d.get('names') or []) or '—'}")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
