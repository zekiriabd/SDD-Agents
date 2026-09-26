#!/usr/bin/env python3
"""Tout agent porte ses cinq bornes — invariant `no-unbounded-loop` (P12).

Aucun agent généré ne sort sans `max_iterations`, `max_tool_calls`,
`max_delegation_depth`, `timeout_s`, `budget_usd`, et un comportement défini à
l'atteinte de chaque borne. Une boucle ReAct sans plafond est l'équivalent
agentic d'un `while(true)` — sauf qu'elle facture.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import AGENT_BUILDERS, ALLOW, agent_of, allow, deny, mission_of, run  # noqa: E402

#: La pré-passe de `dev-orchestration` (`/sdda-build` 4.0) : `shared/` et
#: l'INTERFACE mémoire, avant `dev-prompt` (4.1). Elle n'implémente aucun
#: prompt ; exiger d'elle un `promptHash` rendait la phase 4 inatteignable —
#: le prompt ne peut pas être épinglé avant d'être écrit.
_PREPASS_RE = re.compile(r"^\s*SDDA-PREPASS\s*$", re.M)


def _is_prepass(data: dict) -> bool:
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    return agent_of(data) == "dev-orchestration" and bool(_PREPASS_RE.search(str((tool_input or {}).get("prompt") or "")))

HOOK = "preflight_agent_bounds"

#: Câblage — lu par `harness_build.py`. Le contrôle porte sur l'IR, pas sur le
#: code : il est donc jouable AVANT que `dev-agent` écrive une ligne, ce qui
#: est le seul moment où la correction ne coûte pas un second passage payant.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": AGENT_BUILDERS}

REQUIRED = ("maxIterations", "maxToolCalls", "maxDelegationDepth", "timeoutSec", "budgetUsd")


def check(root: Path, data: dict) -> int:
    from sdda_lib import paths  # noqa: E402

    mission = mission_of(data)
    ir_files = ([paths.ir_path(root, mission)] if mission is not None
                else sorted(paths.ir_dir(root).glob("*-system.ir.json")))
    ir_files = [p for p in ir_files if p.is_file()]
    if not ir_files:
        return allow("aucun IR compilé — le contrôle bloquant est joué en G2")

    unbounded: list[str] = []
    unpinned: list[str] = []
    for path in ir_files:
        try:
            ir = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            # Un IR illisible n'est pas un IR borné : il était SAUTÉ, et le
            # spawn passait sans qu'aucune borne ait été lue.
            return deny(HOOK, "IR_INVALID", f"`{paths.rel(root, path)}` illisible ({exc.__class__.__name__})",
                        "recompiler l'IR (`python .sdda/sdda.py ir-compiler --mission {n}`)")
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
            # Le prompt épinglé : optionnel à la compilation (PHASE 2, les prompts
            # n'existent pas encore), EXIGÉ ici — `dev-agent` implémente un prompt,
            # et un prompt sans empreinte est un prompt que personne n'a écrit ou
            # que n'importe qui peut réécrire sans que la baseline le voie (P10).
            # C'est le pendant de « holdout absent -> G8 refuse » : l'exigence vit
            # au moment où elle est actionnable.
            if not agent.get("promptHash"):
                unpinned.append(str(agent.get("id", "?")))

    # Les bornes d'abord : une borne absente est une borne infinie, et c'est le
    # nom de ce hook. Le prompt non épinglé vient ensuite.
    if not unbounded and unpinned and not _is_prepass(data):
        return deny(HOOK, "PROMPT_NOT_PINNED",
                    f"{len(unpinned)} agent(s) sans `promptHash` dans l'IR — {unpinned[0]}",
                    "dev-prompt écrit `workspace/src/{App}/prompts/{slug}.system.md`, puis recompiler l'IR "
                    "(`python .sdda/sdda.py ir-compiler --mission {n}`) : l'empreinte s'épingle à la "
                    "recompilation, jamais à la main")
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
