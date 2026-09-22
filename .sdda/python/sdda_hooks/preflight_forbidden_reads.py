#!/usr/bin/env python3
"""Aucune lecture hors des `forbidden_reads` de l'agent — matrice `loader.yml`.

`preflight_ownership` ferme les ÉCRITURES ; ce hook ferme les LECTURES, sur les
trois outils qui rendent du contenu ou des noms : `Read`, `Grep`, `Glob`.

Pourquoi une lecture peut être une violation : `forbidden_reads` n'est pas de
la confidentialité, c'est de l'**altitude**. `po-elicitor` qui lit `STACK.md`
écrit une MISSION teintée de choix techniques ; `po-capabilities` qui lit la
topologie découpe des CAPs autour d'agents qui n'existent pas encore. Rien ne
se voit dans le livrable — la teinte est indiscernable d'une décision. Ce que
l'agent doit savoir de ces zones lui est INJECTÉ par le brief (`spawn_brief.py`,
faits injectés) : il n'a pas à aller le chercher.

Ce que chaque outil rend, et donc ce que le hook regarde (cf.
`audit_ownership.read_violation`) :

    Read  -> un fichier            : refusé si le chemin matche un interdit
    Glob  -> des NOMS              : refusé si la racine est DANS une zone interdite
    Grep  -> du CONTENU sous racine : refusé aussi si la racine est un ANCÊTRE
                                      d'une zone interdite (grep `workspace/`
                                      rend STACK.md à qui ne doit pas le lire)

Le fil principal (aucun `subagent_type`) passe : la matrice ne régit que les
agents, et refuser ici bloquerait l'utilisateur.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, agent_of, deny, run, unknown_subagent  # noqa: E402

HOOK = "preflight_forbidden_reads"

#: Câblage — lu par `harness_build.py`. `applies_to` vide : l'interdit de
#: lecture vaut pour chaque agent qui en déclare un.
WIRING = {"event": "PreToolUse", "matcher": "Read|Glob|Grep", "applies_to": ()}


def _target_and_scope(data: dict) -> tuple[str, str]:
    """(chemin visé, portée) depuis le payload du harnais.

    `Read` porte `file_path` ; `Glob` et `Grep` portent `path`, absent quand la
    recherche part du répertoire courant — c'est alors la racine entière, et
    pour `Grep` c'est précisément le cas qui lit tout.
    """
    tool = str(data.get("tool_name") or "").strip()
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    if tool == "Read" or (not tool and tool_input.get("file_path")):
        return str(tool_input.get("file_path") or data.get("file_path") or ""), "file"
    if tool == "Grep":
        return str(tool_input.get("path") or "."), "content"
    if tool == "Glob":
        return str(tool_input.get("path") or "."), "names"
    return "", ""


def check(root: Path, data: dict) -> int:
    agent = agent_of(data)
    if not agent:
        return ALLOW

    target, scope = _target_and_scope(data)
    if not scope or not target:
        return ALLOW

    from sdda_lib.errors import Report  # noqa: E402  (import tardif : coût de démarrage du hook)
    from sdda_scripts import audit_ownership as ao  # noqa: E402

    if target != ".":
        try:
            rel = Path(str(target)).resolve().relative_to(root).as_posix()
        except ValueError:
            rel = str(target).replace("\\", "/")
    else:
        rel = "."

    loader = ao.load_loader(root)
    if not isinstance(loader.get(agent), dict):
        # Sous-agent hors matrice : il ne lit pas `proof/`. Le reste du
        # workspace lui reste lisible — un `Explore` lancé par l'utilisateur
        # doit pouvoir chercher dans les specs. Ce qu'il ne doit jamais voir est
        # le jeu de verdict : un agent blanchi qui lit le holdout est exactement
        # « optimiser contre le jeu qui rend le verdict », par un autre chemin.
        proof = "workspace/proof"
        normalized = rel.replace("\\", "/").lstrip("./")
        if normalized == proof or normalized.startswith(proof + "/") or normalized in (".", "workspace"):
            return unknown_subagent(HOOK, agent, normalized if normalized not in (".", "workspace") else proof)
        return ALLOW
    if not ao.forbidden_reads_of(loader, agent):
        return ALLOW

    report = Report(name="READS-HOOK", target=str(root))
    if ao.check_read(loader, agent, rel, report, scope=scope):
        return ALLOW

    first = report.errors[0] if report.errors else None
    return deny(HOOK, first.cls if first else "OWNERSHIP_READ_FORBIDDEN",
                first.message if first else f"`{agent}` a lu `{rel}` hors de ses `reads:`",
                first.fix if first else f"zones lisibles : {ao.reads_of(loader, agent)}")


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
