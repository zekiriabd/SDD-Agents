#!/usr/bin/env python3
"""Le contexte d'un agent tient dans son budget — AVANT le spawn.

Un agent qui déborde son `budget_bytes` produit une sortie tronquée et
confiante, ce qui est pire qu'un échec net : personne ne voit qu'il a raisonné
sur la moitié de ses entrées. Le contrôle est volontairement brutal — il ne
part pas.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, agent_of, deny, run  # noqa: E402

HOOK = "preflight_agent_budget"

#: Câblage — lu par `harness_build.py`. Tout spawn : chaque agent a un budget
#: de contexte dans loader.yml, et c'est avant le spawn qu'il faut le vérifier.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": ()}


def check(root: Path, data: dict) -> int:
    agent = agent_of(data)
    if not agent:
        return ALLOW

    from sdda_lib.errors import Report  # noqa: E402
    from sdda_scripts import context_pack  # noqa: E402

    loader = context_pack.load_loader(root)
    if not isinstance(loader.get(agent), dict):
        return ALLOW

    report = Report(name="CONTEXT-HOOK", target=str(root))
    resolution = context_pack.resolve_context(root, loader, agent, report=report,
                                              mission=data.get("mission"), target=data.get("target"))
    if resolution is None:
        return ALLOW
    budget = int(resolution.budget_bytes or 0)
    total = resolution.total_bytes
    if budget and total > budget:
        return deny(HOOK, "CONTEXT_BUDGET_EXCEEDED",
                    f"`{agent}` : {total} octets de contexte pour un budget de {budget}",
                    "réduire les `reads:` de cet agent dans loader.yml, reconstruire son pack "
                    "(`context_pack.py build`), ou relever son budget — explicitement")
    return ALLOW


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
