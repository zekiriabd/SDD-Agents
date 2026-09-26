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
from sdda_scripts import gen_app_context, gen_app_skeleton, install_env  # noqa: E402
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


#: Hors Python — langage -> (manifeste de build qui doit exister, commande de restauration).
#: Au premier `project-init`, le manifeste n'existe pas encore (`dev-backend`
#: l'écrit depuis la fiche de langage) : l'étape se saute et le dit. Au rejeu,
#: l'environnement est restauré une fois, avant les agents.
NATIVE_RESTORE: dict[str, tuple[str, tuple[str, ...]]] = {
    "csharp": ("*.csproj", ("dotnet", "restore")),
    "typescript": ("package.json", ("npm", "install", "--no-audit", "--no-fund")),
    "kotlin": ("build.gradle.kts", ("{gradle}", "dependencies", "--console=plain")),
    "java": ("build.gradle.kts", ("{gradle}", "dependencies", "--console=plain")),
}


def install_native_dependencies(root: Path, app: str, language: str, report: Report) -> dict[str, object]:
    spec = NATIVE_RESTORE.get(language)
    project = paths.app_dir(root, app)
    if spec is None:
        return {"skipped": f"langage `{language}` sans restauration connue"}
    marker, command = spec
    if not list(project.glob(marker)):
        return {"skipped": f"pas encore de `{marker}` (écrit par dev-backend)"}
    wrapper = project / ("gradlew.bat" if sys.platform == "win32" else "gradlew")
    gradle = str(wrapper) if wrapper.is_file() else (shutil.which("gradle") or "gradle")
    argv = [gradle if part == "{gradle}" else part for part in command]
    if language == "typescript" and (project / "pnpm-lock.yaml").is_file():
        argv = ["pnpm", "install", "--frozen-lockfile"]
    exe = shutil.which(argv[0]) or (argv[0] if Path(argv[0]).is_file() else None)
    if exe is None:
        report.warn(CLS_DEPS_NOT_INSTALLED, f"`{argv[0]}` introuvable : dépendances non restaurées",
                    fix=f"installer l'outil du langage `{language}`, puis `{' '.join(argv)}` dans le projet",
                    location=paths.rel(root, project))
        return {"skipped": f"{argv[0]} absent"}
    try:
        done = subprocess.run([exe, *argv[1:]], cwd=project, capture_output=True, text=True,  # noqa: S603
                              encoding="utf-8", errors="replace", timeout=INSTALL_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        report.error(CLS_DEPS_INSTALL_FAILED, f"`{' '.join(argv)}` bloqué au-delà de {INSTALL_TIMEOUT_S} s",
                     fix="vérifier l'accès au registre de paquets, puis relancer project-init",
                     location=paths.rel(root, project))
        return {"ok": False, "timeout": True}
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
        report.error(CLS_DEPS_INSTALL_FAILED, f"`{' '.join(argv)}` a échoué : " + " | ".join(tail),
                     fix="une version épinglée ne se résout pas : corriger le `.libs.json` du catalogue en cause",
                     location=paths.rel(root, project))
        return {"ok": False, "returncode": done.returncode}
    return {"ok": True, "command": argv}


def run(root: Path, *, mission: str | None = None, install: bool = True) -> Report:
    report = Report(name="PROJECT-INIT", target=str(root))
    steps: dict[str, object] = {}

    ctx = gen_app_skeleton.Context.resolve(root, Report(name="CTX", target=str(root)), mission=mission)
    if ctx.language == gen_app_skeleton.LANGUAGE:
        # La MISSION est transmise au squelette : avec plusieurs MISSIONs, il
        # naissait sans `missionId` ni schémas de sortie de l'IR.
        skeleton = gen_app_skeleton.run(root, mode="write", mission=mission)
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
        # Le `.env` part avec l'application, dans TOUS les langages : il n'était
        # copié que par le squelette Python, donc un projet C#, TypeScript ou
        # JVM passait ses smokes de packaging sans clé — alors que les prompts
        # disaient que project-init l'avait copié.
        if ctx.app and not ctx.app.startswith("<"):
            paths.app_dir(root, ctx.app).mkdir(parents=True, exist_ok=True)
            env = install_env.run(root, write=True, require=False)
            report.extend(env)
            steps["env"] = env.data
            steps["dependencies"] = (install_native_dependencies(root, ctx.app, ctx.language, report) if install
                                     else {"skipped": "--no-install"})

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
