#!/usr/bin/env python3
"""Le profil effectif du projet — ce que `/sdda-full` et `/sdda-build` lisent pour choisir leur chemin.

`Profile: auto | micro | poc | standard | production` (`## Project Config`).

`auto` (défaut du gabarit) : la SPEC fixe le profil. Une MISSION dont les
besoins (`## Architecture Needs`) n'exigent qu'un agent, des outils de lecture et
une console est `micro` — pipeline court, application en un module ; sinon
`standard`. Le dimensionnement et ses raisons se lisent en `--json`.
Le profil est une couche de config (`.sdda/profiles/{profil}.yml`, entre la base
et l'équipe) ET un aiguillage des commandes : sous `poc`, `/sdda-build` prend son
STEP P (deux agents au lieu de huit) et `/sdda-full` saute la revue et
l'acceptation. Une commande ne lit pas STACK.md pour le savoir : elle demande ici,
et une seule réponse vaut pour toutes.

Usage :
    python .sdda/sdda.py project-profile            # imprime `poc` | `standard` | `production`
    python .sdda/sdda.py project-profile --json     # + provenance de la valeur et fichier de défauts
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.layered_config import SHORT_PATH_PROFILES, profile_config_path, resolve_profile  # noqa: E402
from sdda_lib.spec_needs import sizing  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, load_config, resolve_root  # noqa: E402

PROFILES = ("auto", "micro", "poc", "standard", "production")


def run(root: Path, mission: str | None = None) -> Report:
    report = Report(name="PROFILE", target=str(root))
    config = load_config(root, report)
    declared = str(config.get("Profile", "standard")).strip()
    if declared not in PROFILES:
        report.error("CONFIG_VALUE_INVALID", f"`Profile: {declared}` hors de {list(PROFILES)}",
                     fix="écrire `Profile: auto` (la spec décide), `micro`, `poc`, `standard` ou `production` "
                         "dans `## Project Config`",
                     location="workspace/stack/STACK.md ## Project Config")
    profile = resolve_profile(root, declared, mission)
    defaults = profile_config_path(root, profile)
    if declared == "auto":
        missions = sorted({p.name.split("-", 1)[0] for p in paths.missions_dir(root).glob("*-*.md")})
        target = mission or (missions[0] if len(missions) == 1 else None)
        report.data["sizing"] = sizing(root, target).to_dict() if target else {
            "complexity": "standard", "decided": False, "reasons": [],
            "blockers": ["aucune MISSION unique : le profil se décide après la PHASE 0"]}
    report.data.update({
        "declared": declared,
        "shortPath": profile in SHORT_PATH_PROFILES,
        "profile": profile,
        "source": config.sources.get("Profile", "défaut"),
        "defaults": paths.rel(root, defaults) if defaults.is_relative_to(root) else str(defaults),
        "defaultsPresent": defaults.is_file(),
    })
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Affiche le profil effectif du projet (micro | poc | standard | production)")
    p.add_argument("--mission", default=None, help="MISSION dont les besoins résolvent `Profile: auto`")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    report = run(resolve_root(args), args.mission)
    if not args.json and report.ok:
        print(report.data["profile"])
        return 0
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
