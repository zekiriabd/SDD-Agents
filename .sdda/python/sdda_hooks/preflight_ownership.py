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
from _hook import ALLOW, agent_of, deny, run, unknown_subagent  # noqa: E402

HOOK = "preflight_ownership"

#: Câblage — lu par `harness_build.py`. `applies_to` vide : la matrice
#: d'ownership vaut pour chaque écriture, quel qu'en soit l'auteur. Le hook
#: laisse lui-même passer le fil principal (voir `check`).
WIRING = {"event": "PreToolUse", "matcher": "Write|Edit", "applies_to": ()}


#: Zones que personne n'écrit avec un outil d'édition, quel qu'en soit l'auteur.
#:
#: Trois répertoires ne sont écrits QUE par des scripts, en E/S Python, jamais
#: par l'outil `Write` ou `Edit`. Un `Write` sur l'un d'eux est donc fautif sans
#: qu'on ait besoin de savoir QUI le tente — c'est le seul contrôle d'ownership
#: qui ne dépend pas de l'identification de l'auteur, et il est joué avant elle.
#:
#:   proof/baselines/    `promote_baseline.py` — retouchée à la main, la
#:                       référence rend toute non-régression tautologique ;
#:   .sys/.validation/   les rapports de gate. Ils sont du JSON en clair, non
#:                       signé, et `gate_status` ne lit que leur `ok` : un
#:                       rapport `{"ok": true}` déposé par `Write` rendait
#:                       n'importe quelle gate verte, et `.sys/` est gitignoré,
#:                       donc la contrefaçon n'atteignait jamais une revue ;
#:   .sys/.audit/        `bypasses.jsonl`, que la matrice déclare append-only
#:                       « hooks framework » — sans qu'aucun enforcer n'existe.
#:
#: Ce que ce contrôle ne couvre pas, et qu'il faut dire : une écriture par
#: `python -c` ou par un shell contourne l'outil `Write`. C'est la limite
#: lexicale du hook Bash, assumée dans son propre docstring. Ce qui reste vrai
#: est plus modeste et suffisant : aucun agent ne peut le faire par l'outil
#: qu'on lui donne pour écrire, et il doit donc le faire *sciemment*.
#: Nommée en constante pour que `sync_error_registry` la voie : il ne lit que les
#: littéraux (`deny(HOOK, "CLASS", …)` ou `CLS_X = "CLASS"`), et une classe
#: émise depuis la valeur d'un dictionnaire n'entrait jamais au registre — tout
#: en étant réellement émise. C'est la définition même d'une classe orpheline.
CLS_GATE_REPORT_FORGERY = "GATE_REPORT_FORGERY"
CLS_BASELINE_OWNERSHIP_VIOLATION = "BASELINE_OWNERSHIP_VIOLATION"

IDENTITY_FREE_ZONES: dict[str, tuple[str, str]] = {
    "workspace/proof/baselines": (
        CLS_BASELINE_OWNERSHIP_VIOLATION,
        "la baseline s'écrit par `python .sdda/sdda.py promote-baseline`, jamais par Write/Edit : "
        "déplacer la référence rend toute non-régression tautologique"),
    "workspace/.sys/.validation": (
        CLS_GATE_REPORT_FORGERY,
        "un rapport de gate est écrit par le script de la gate, jamais par Write/Edit : "
        "un `{\"ok\": true}` déposé à la main rend verte une gate que rien n'a mesurée"),
    "workspace/.sys/.audit": (
        CLS_GATE_REPORT_FORGERY,
        "le journal des bypasses est append-only et n'est écrit que par les scripts : "
        "un audit qu'on peut réécrire n'est pas un audit"),
}


def identity_free_violation(rel: str) -> tuple[str, str] | None:
    for zone, (cls, fix) in IDENTITY_FREE_ZONES.items():
        if rel == zone or rel.startswith(zone + "/"):
            return cls, fix
    return None


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

    violation = identity_free_violation(rel)
    if violation:
        cls, fix = violation
        return deny(HOOK, cls, f"`{rel}` édité par un outil d'édition{f' (`{agent}`)' if agent else ''}", fix)

    if not agent:
        # Écriture par le fil principal (l'humain, ou une commande) : la matrice
        # ne régit que les agents. Refuser ici bloquerait l'utilisateur.
        return ALLOW

    report = Report(name="OWNERSHIP-HOOK", target=str(root))
    loader = ao.load_loader(root)
    if not isinstance(loader.get(agent), dict):
        return unknown_subagent(HOOK, agent, rel)

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
