#!/usr/bin/env python3
"""Toute écriture reste dans la zone de son agent — matrice `loader.yml`.

Le hook le plus important du niveau A, et le seul qui s'exécute à chaque `Write`
et chaque `Edit`. Le parallélisme des `dev-*` n'est sûr que parce que leurs
répertoires sont disjoints ; rien au runtime ne l'impose — sinon ce contrôle.

Le cas qui coûte le plus cher n'est pas la collision, qui se voit : c'est le
`dev-agent` qui retouche `workspace/datasets/` ou `workspace/prompts/`,
c'est-à-dire qui modifie le jeu qui le juge ou le prompt qu'il implémente. La
note devient invérifiable, et personne ne s'en aperçoit avant l'acceptation.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, agent_of, deny, run  # noqa: E402

HOOK = "preflight_ownership"

#: Câblage — lu par `harness_build.py`. `applies_to` vide : la matrice
#: d'ownership vaut pour chaque écriture, quel qu'en soit l'auteur. Le hook
#: laisse lui-même passer le fil principal (voir `check`).
WIRING = {"event": "PreToolUse", "matcher": "Write|Edit", "applies_to": ()}


def check(root: Path, data: dict) -> int:
    agent = agent_of(data)
    if not agent:
        # Écriture par le fil principal (l'humain, ou une commande) : la matrice
        # ne régit que les agents. Refuser ici bloquerait l'utilisateur.
        return ALLOW

    target = (data.get("tool_input") or {}).get("file_path") or data.get("file_path")
    if not target:
        return ALLOW

    from sdda_lib import paths  # noqa: E402  (import tardif : coût de démarrage du hook)
    from sdda_lib.errors import Report  # noqa: E402
    from sdda_scripts import audit_ownership as ao  # noqa: E402

    try:
        rel = Path(str(target)).resolve().relative_to(root).as_posix()
    except ValueError:
        rel = str(target).replace("\\", "/")

    report = Report(name="OWNERSHIP-HOOK", target=str(root))
    loader = ao.load_loader(root)
    if not isinstance(loader.get(agent), dict):
        return ALLOW  # agent hors matrice : ce n'est pas au hook de le trancher

    if ao.check_write(loader, agent, rel, report):
        return ALLOW

    first = report.errors[0] if report.errors else None
    return deny(HOOK, first.cls if first else "OWNERSHIP_VIOLATION",
                first.message if first else f"`{agent}` a écrit `{rel}` hors de sa zone",
                first.fix if first else f"zones autorisées : {ao.writes_of(loader, agent)}")


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
