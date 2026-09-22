#!/usr/bin/env python3
"""Tout agent porte ses cinq bornes — invariant `no-unbounded-loop` (P12).

Aucun agent généré ne sort sans `max_iterations`, `max_tool_calls`,
`max_delegation_depth`, `timeout_s`, `budget_usd`, et un comportement défini à
l'atteinte de chaque borne. Une boucle ReAct sans plafond est l'équivalent
agentic d'un `while(true)` — sauf qu'elle facture.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import AGENT_BUILDERS, ALLOW, allow, deny, run  # noqa: E402

HOOK = "preflight_agent_bounds"

#: Câblage — lu par `harness_build.py`. Le contrôle porte sur l'IR, pas sur le
#: code : il est donc jouable AVANT que `dev-agent` écrive une ligne, ce qui
#: est le seul moment où la correction ne coûte pas un second passage payant.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": AGENT_BUILDERS}

REQUIRED = ("maxIterations", "maxToolCalls", "maxDelegationDepth", "timeoutSec", "budgetUsd")


def check(root: Path, data: dict) -> int:
    from sdda_lib import paths  # noqa: E402

    mission = data.get("mission")
    ir_files = ([paths.ir_path(root, mission)] if mission is not None
                else sorted(paths.ir_dir(root).glob("*-system.ir.json")))
    ir_files = [p for p in ir_files if p.is_file()]
    if not ir_files:
        return allow("aucun IR compilé — le contrôle bloquant est joué en G2")

    unbounded: list[str] = []
    for path in ir_files:
        try:
            ir = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for agent in ir.get("agents") or []:
            bounds = agent.get("bounds") or {}
            # `0` est une VALEUR, pas une absence — et c'est la plus sûre :
            # `maxDelegationDepth: 0` dit « cet agent ne délègue à personne »,
            # `maxToolCalls: 0` dit « il n'appelle aucun outil ». Le schéma les
            # autorise (`minimum: 0`) et les fixtures de référence les emploient.
            # Les traiter comme manquantes bloquait au spawn une IR parfaitement
            # conforme, en accusant précisément la déclaration la plus stricte.
            missing = [b for b in REQUIRED
                       if bounds.get(b) is None or bounds.get(b) == ""]
            if missing:
                unbounded.append(f"{agent.get('id', '?')} : {missing}")
            if not agent.get("onBoundExceeded"):
                unbounded.append(f"{agent.get('id', '?')} : onBoundExceeded absent")

    if not unbounded:
        return ALLOW
    return deny(HOOK, "AGENT_BOUNDS_MISSING",
                f"{len(unbounded)} agent(s) sans borne complète — {unbounded[0]}",
                "déclarer les cinq bornes ET le comportement à l'atteinte dans le contrat d'agent, "
                "puis recompiler l'IR. Une borne absente est une borne infinie")


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
