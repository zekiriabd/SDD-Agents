#!/usr/bin/env python3
"""G4 franchie avant les agents — invariant `retrieval-gate-before-agent`.

C'est le second différenciateur revendiqué du framework : presque personne ne
mesure la récupération avant de juger l'agent. Un retriever à recall 0.4 se
présente comme « l'agent hallucine », et on passe des semaines à réécrire un
prompt pour compenser un index.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook import AGENT_BUILDERS, ALLOW, allow, bypassed, deny, gate_status, run  # noqa: E402

HOOK = "preflight_retrieval_gate"

#: Câblage — lu par `harness_build.py`. `dev-retrieval` est hors périmètre pour la
#: même raison que `dev-tools` en G3 : c'est lui qui rend la gate verte.
WIRING = {"event": "PreToolUse", "matcher": "Task", "applies_to": AGENT_BUILDERS}


def _no_retrieval(root: Path) -> bool:
    """La stack active déclare-t-elle `rag/none` ?

    Sans ce contrôle, un projet sans corpus n'a aucun rapport G4 — donc verdict
    `absent`, donc refus de câbler le moindre agent. Un invariant qui bloque les
    projets qu'il ne concerne pas se fait désactiver dans la semaine, et emporte
    avec lui ceux qu'il protégeait vraiment.
    """
    try:
        from sdda_lib.layered_config import active_stacks  # noqa: E402

        # Titre SANS `## ` : `section_body` le préfixe lui-même.
        return active_stacks(root, "Active RAG Pattern") == ["none"]
    except Exception:
        return False


def check(root: Path, data: dict) -> int:
    verdict, reasons = gate_status(root, "G4", data.get("mission"))
    if verdict == "green":
        return ALLOW
    if verdict == "absent" and _no_retrieval(root):
        return ALLOW
    if bypassed("SDDA_BYPASS_RETRIEVAL_GATE"):
        # Le rapport porte `bypassed: true` et remonte en G7 et au récapitulatif :
        # un seuil desserré doit rester visible jusqu'à l'acceptation.
        return allow("G4 court-circuitée par SDDA_BYPASS_RETRIEVAL_GATE — remontera en G7")
    return deny(HOOK, "RETRIEVAL_GATE_NOT_PASSED", f"G4 non franchie — {'; '.join(reasons)}",
                "mesurer recall@k, nDCG et groundedness avant de câbler un agent qui en dépend")


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
