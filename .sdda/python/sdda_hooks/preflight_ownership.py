#!/usr/bin/env python3
"""Toute écriture reste dans la zone de son agent — matrice `loader.yml`.

Le hook le plus important du niveau A, et le seul qui s'exécute à chaque `Write`
et chaque `Edit`. Le parallélisme des `dev-*` n'est sûr que parce que leurs
répertoires sont disjoints ; rien au runtime ne l'impose — sinon ce contrôle.

Le cas qui coûte le plus cher n'est pas la collision, qui se voit : c'est le
`dev-agent` qui retouche `workspace/proof/datasets/` ou `workspace/src/prompts/`,
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


#: Zone que personne n'écrit avec un outil d'édition, quel qu'en soit l'auteur.
#:
#: `workspace/proof/baselines/` est déclarée « script déterministe uniquement,
#: Write atomique » par la matrice d'ownership. `promote_baseline.py` y écrit en
#: E/S Python, jamais par l'outil `Write` — donc un `Write`/`Edit` sur ce chemin
#: est fautif sans qu'on ait besoin de savoir QUI le tente. C'est le seul
#: contrôle d'ownership qui ne dépend pas de l'identification de l'auteur, et
#: c'est pour cela qu'il est joué avant elle : une baseline retouchée à la main
#: rend toute non-régression tautologique, et rien en aval ne le rattrape.
IDENTITY_FREE_ZONE = "workspace/proof/baselines"


def _relative(root: Path, target: str) -> str:
    try:
        return Path(str(target)).resolve().relative_to(root).as_posix()
    except ValueError:
        return str(target).replace("\\", "/")


def check(root: Path, data: dict) -> int:
    agent = agent_of(data)

    target = (data.get("tool_input") or {}).get("file_path") or data.get("file_path")
    if not target:
        return ALLOW

    from sdda_lib import paths  # noqa: E402  (import tardif : coût de démarrage du hook)
    from sdda_lib.errors import Report  # noqa: E402
    from sdda_scripts import audit_ownership as ao  # noqa: E402

    rel = _relative(root, str(target))

    if rel == IDENTITY_FREE_ZONE or rel.startswith(IDENTITY_FREE_ZONE + "/"):
        return deny(HOOK, "BASELINE_OWNERSHIP_VIOLATION",
                    f"`{rel}` édité à la main{f' par `{agent}`' if agent else ''}",
                    "la baseline s'écrit par `python .sdda/sdda.py promote-baseline`, jamais par Write/Edit : "
                    "déplacer la référence rend toute non-régression tautologique")

    if not agent:
        # Écriture par le fil principal (l'humain, ou une commande) : la matrice
        # ne régit que les agents. Refuser ici bloquerait l'utilisateur.
        return ALLOW

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
