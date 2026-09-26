#!/usr/bin/env python3
"""Toute écriture reste dans la zone de son agent — matrice `loader.yml`.

Le hook le plus important du niveau A, et le seul qui s'exécute à chaque `Write`
et chaque `Edit`. Le parallélisme des `dev-*` n'est sûr que parce que leurs
répertoires sont disjoints ; rien au runtime ne l'impose — sinon ce contrôle.

Le cas qui coûte le plus cher n'est pas la collision, qui se voit : c'est le
`dev-agent` qui retouche `workspace/pipeline/datasets/` ou `workspace/src/{App}/prompts/`,
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
#:
#: `NotebookEdit` et `MultiEdit` écrivent un fichier aussi sûrement que `Write` :
#: absents du matcher, un sous-agent pouvait réécrire un notebook — ou, par
#: `MultiEdit`, n'importe quel fichier — sans qu'aucun hook ne s'exécute.
WIRING = {"event": "PreToolUse", "matcher": "Write|Edit|MultiEdit|NotebookEdit", "applies_to": ()}


#: Zones protégées : `pipeline/baselines/`, `.sys/.validation/`, `.sys/.audit/`.
#: Définies dans `audit_ownership.PROTECTED_ZONES` — le hook Bash applique les
#: MÊMES, et deux copies d'une liste de zones protégées finissent par diverger
#: exactement là où l'une des deux protège.
#:
#: Ce que ce contrôle ne couvre pas, et qu'il faut dire : une écriture par un
#: script (`python x.py`) contourne l'outil `Write`. Le hook Bash ferme les
#: formes lexicales et refuse les formes opaques sur les zones régies ; ce qui
#: reste — un script qui écrit de l'intérieur — est rattrapé APRÈS la phase par
#: `audit-ownership --since-snapshot`, qui compare le disque à l'instantané.

#: Les clés du payload qui nomment le fichier écrit, par outil : `Write`, `Edit`
#: et `MultiEdit` portent `file_path`, `NotebookEdit` porte `notebook_path`. Un
#: outil d'écriture dont la clé n'est pas lue est un outil que le hook ne voit
#: pas — il laisse passer sans rien dire.
TARGET_KEYS = ("file_path", "notebook_path", "path")


def target_of(data: dict) -> str:
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    for key in TARGET_KEYS:
        value = tool_input.get(key) or data.get(key)
        if value:
            return str(value)
    return ""


def check(root: Path, data: dict) -> int:
    agent = agent_of(data)

    target = target_of(data)
    if not target:
        return ALLOW

    from sdda_scripts import audit_ownership as ao  # noqa: E402  (import tardif : coût de démarrage)

    loader = ao.load_loader(root) if agent else {}
    # Le chemin tel qu'écrit ET le chemin physique (lien, jonction) : un lien
    # anodin vers le golden est le golden.
    rels = [ao.relative_to_root(root, target, data.get("cwd"))]
    real = ao.real_relative_to_root(root, target, data.get("cwd"))
    if real is not None:
        rels.append(real)
    for rel in rels:
        verdict = verdict_for(root, loader, agent, rel, data)
        if verdict != ALLOW:
            return verdict
    return ALLOW


def bindings_for(root: Path, loader: dict, agent: str, rel: str, data: dict) -> tuple[dict[str, str] | None, int]:
    """Liaison d'instance (`{agent}` de `dev-agent`) — cf. `_instances`.

    Rend `(bindings, verdict)` : un verdict ≠ ALLOW est un refus déjà émis.
    """
    try:
        import _instances  # noqa: E402
    except ImportError:  # pragma: no cover — module absent : pas de liaison
        return None, ALLOW
    return _instances.bindings_for_write(root, loader, agent, rel, data, HOOK)


def verdict_for(root: Path, loader: dict, agent: str, rel: str, data: dict) -> int:
    from sdda_lib.errors import Report  # noqa: E402
    from sdda_scripts import audit_ownership as ao  # noqa: E402

    if ao.protected_zone(rel) is not None:
        verdict = ao.protected_write(loader, agent, rel)
        if verdict is None:
            return ALLOW
        cls, fix = verdict
        return deny(HOOK, cls, f"`{rel}` édité par un outil d'édition{f' (`{agent}`)' if agent else ''}", fix)

    if not agent:
        # Écriture par le fil principal (l'humain, ou une commande) : la matrice
        # ne régit que les agents. Refuser ici bloquerait l'utilisateur.
        return ALLOW

    if not isinstance(loader.get(agent), dict):
        return unknown_subagent(HOOK, agent, rel, write=True)

    bindings, verdict = bindings_for(root, loader, agent, rel, data)
    if verdict != ALLOW:
        return verdict

    report = Report(name="OWNERSHIP-HOOK", target=str(root))
    if ao.check_write(loader, agent, rel, report, bindings):
        return ALLOW

    first = report.errors[0] if report.errors else None
    return deny(HOOK, first.cls if first else "OWNERSHIP_VIOLATION",
                first.message if first else f"`{agent}` a écrit `{rel}` hors de sa zone",
                first.fix if first else f"zones autorisées : {ao.writes_of(loader, agent)}")


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
