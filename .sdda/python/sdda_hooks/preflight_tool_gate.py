#!/usr/bin/env python3
"""G3 franchie avant le câblage des agents — invariant `tool-gate-before-agent-wiring`.

Un outil dont le schéma ment se présente en aval comme « le superviseur route
mal ». Prouver la couche basse avant de s'appuyer dessus évite de déboguer le
mauvais étage — et de le déboguer avec un LLM, donc cher (P5).

Le bypass `SDDA_BYPASS_TOOL_GATE` desserre les tests de contrat, **jamais** une
classe d'effet de bord non déclarée : un outil destructif sans stratégie ne se
câble pas, quelle que soit l'urgence.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook import AGENT_BUILDERS, ALLOW, allow, bypassed, deny, gate_status, run  # noqa: E402

HOOK = "preflight_tool_gate"

#: Câblage — lu par `harness_build.py`. Le hook garde l'entrée de la phase 4 :
#: `dev-tools` doit pouvoir travailler pour rendre G3 verte, donc il n'est pas
#: dans la liste. Se prononcer sur lui interdirait de corriger ce qu'on reproche.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": AGENT_BUILDERS}

#: Ce que le bypass ne couvre jamais. Aligné sur `validate_tool_contract.BYPASS_NEVER`.
NEVER_BYPASSED = ("SIDE_EFFECT_UNDECLARED", "SAFETY_STRATEGY_MISSING", "TOOL_RETRY_UNSAFE")


def check(root: Path, data: dict) -> int:
    verdict, reasons = gate_status(root, "G3", data.get("mission"))
    if verdict == "green":
        return ALLOW

    blocking = [r for r in reasons if any(n in r for n in NEVER_BYPASSED)]
    if bypassed("SDDA_BYPASS_TOOL_GATE") and not blocking:
        return allow("G3 court-circuitée par SDDA_BYPASS_TOOL_GATE — tracé dans bypasses.jsonl")

    return deny(
        HOOK, "TOOL_GATE_NOT_PASSED", f"G3 non franchie — {'; '.join(reasons)}",
        "lancer `/sdda-build {n} --layer socle` jusqu'au vert avant de câbler les agents."
        + (" Ces classes ne se court-circuitent jamais : " + ", ".join(NEVER_BYPASSED) if blocking else ""),
    )


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
