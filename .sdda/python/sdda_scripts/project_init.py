#!/usr/bin/env python3
"""Initialise le projet applicatif AVANT le premier `dev-*` — 0 token.

Ce que ce script défend : *la mise en place d'un projet est une procédure, pas
un jugement.* Avant lui, `dev-backend` (un agent LLM) lançait lui-même
`gen-app-skeleton --write` en PHASE 3.0 — treize minutes mesurées sur le premier
projet réel pour ce qui tient en trois appels de script — puis chaque `dev-*`
relisait STACK.md pour reconstituer le projet. SDD_Pro fait tourner cette
étape par son architecte (`/arch-init`) ; ici elle n'a besoin d'aucun LLM.

Trois étapes, dans cet ordre, chacune idempotente :

1. **Squelette** — `gen-app-skeleton --write` (Python seulement ; dans un autre
   langage, `dev-backend` écrit le squelette depuis la fiche de langage).
2. **Dépendances** — `uv sync` dans le projet : l'environnement est installé une
   fois, avant les agents, et non par le premier qui en a besoin.
3. **Contexte** — `gen-app-context --write` : le fichier que le harnais charge
   nativement en entrant dans `workspace/src/{App}/`.

`--no-install` saute l'étape 2 (CI, tests, poste hors ligne) : le projet reste
initialisé, l'environnement s'installe au premier `uv sync`.

Usage :
    python .sdda/sdda.py project-init --mission 1
    python .sdda/sdda.py project-init --mission 1 --no-install --json
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_scripts import gen_app_context, gen_app_skeleton  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

#: `uv` absent du PATH : le projet est initialisé, son environnement ne l'est pas.
CLS_DEPS_NOT_INSTALLED = "PROJECT_DEPS_NOT_INSTALLED"
#: `uv sync` a échoué : une dépendance épinglée ne se résout pas.
CLS_DEPS_INSTALL_FAILED = "PROJECT_DEPS_INSTALL_FAILED"

#: Au-delà, `uv sync` est considéré bloqué (réseau, index privé injoignable).
INSTALL_TIMEOUT_S = 600


def install_dependencies(root: Path, app: str, report: Report) -> dict[str, object]:
    project = paths.app_dir(root, app)
    if not (project / "pyproject.toml").is_file():
        return {"skipped": "pas de pyproject.toml"}
    uv = shutil.which("uv")
    if uv is None:
        report.warn(CLS_DEPS_NOT_INSTALLED, "`uv` introuvable : environnement non installé",
                    fix="installer uv (https://docs.astral.sh/uv/) puis `uv sync` dans le projet",
                    location=paths.rel(root, project))
        return {"skipped": "uv absent"}
    try:
        done = subprocess.run([uv, "sync"], cwd=project, capture_output=True, text=True,  # noqa: S603
                              timeout=INSTALL_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        report.error(CLS_DEPS_INSTALL_FAILED, f"`uv sync` bloqué au-delà de {INSTALL_TIMEOUT_S} s",
                     fix="vérifier l'accès à l'index de paquets, puis relancer project-init",
                     location=paths.rel(root, project))
        return {"ok": False, "timeout": True}
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
        report.error(CLS_DEPS_INSTALL_FAILED, "`uv sync` a échoué : " + " | ".join(tail),
                     fix="une version épinglée ne se résout pas : corriger le `.libs.json` du catalogue "
                         "en cause, jamais le pyproject à la main", location=paths.rel(root, project))
        return {"ok": False, "returncode": done.returncode}
    return {"ok": True}


def run(root: Path, *, mission: str | None = None, install: bool = True) -> Report:
    report = Report(name="PROJECT-INIT", target=str(root))
    steps: dict[str, object] = {}

    ctx = gen_app_skeleton.Context.resolve(root, Report(name="CTX", target=str(root)))
    if ctx.language == gen_app_skeleton.LANGUAGE:
        skeleton = gen_app_skeleton.run(root, mode="write")
        report.extend(skeleton)
        steps["skeleton"] = {"written": len(skeleton.data.get("written") or [])}
        if not skeleton.ok:
            report.data["steps"] = steps
            return report
        steps["dependencies"] = install_dependencies(root, ctx.app, report) if install else {"skipped": "--no-install"}
        if not report.ok:
            report.data["steps"] = steps
            return report
    else:
        steps["skeleton"] = {"skipped": f"langage `{ctx.language or '?'}` : squelette écrit par dev-backend"}

    context = gen_app_context.run(root, mode="write", mission=mission)
    report.extend(context)
    steps["context"] = context.data
    report.data["steps"] = steps
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Initialise le projet applicatif : squelette, dépendances, contexte")
    p.add_argument("--mission", default=None, help="numéro de la MISSION (défaut : l'unique MISSION du workspace)")
    p.add_argument("--no-install", action="store_true", help="ne pas lancer `uv sync`")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    return finish(run(resolve_root(args), mission=args.mission, install=not args.no_install), args)


if __name__ == "__main__":
    sys.exit(main())
