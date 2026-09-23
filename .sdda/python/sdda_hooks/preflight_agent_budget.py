#!/usr/bin/env python3
"""Le contexte d'un agent tient dans son budget — AVANT le spawn.

Un agent qui déborde son `budget_bytes` produit une sortie tronquée et
confiante, ce qui est pire qu'un échec net : personne ne voit qu'il a raisonné
sur la moitié de ses entrées. Le contrôle est volontairement brutal — il ne
part pas.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, agent_of, deny, run  # noqa: E402

HOOK = "preflight_agent_budget"

#: Câblage — lu par `harness_build.py`. Tout spawn : chaque agent a un budget
#: de contexte dans loader.yml, et c'est avant le spawn qu'il faut le vérifier.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": ()}


_MISSION_RE = re.compile(r"\bMISSION\s*:?\s*(\d+)\b")


def _mission_and_target(data: dict) -> tuple[str | None, str | None]:
    """La MISSION et la cible du spawn — du payload s'il les porte, sinon du prompt.

    Le harnais ne transmet que `tool_input.prompt` : il n'y a jamais de clé
    `mission`. Sans elle, chaque `{n}` de `loader.yml` s'élargissait à TOUTES
    les missions et le hook mesurait un contexte que l'agent ne lira jamais —
    il refusait tous les agents de construction au premier projet réel. Le
    brief assemblé par `spawn-brief` écrit toujours `MISSION : {n}` ; à défaut,
    `SDDA_MISSION` dans l'environnement.
    """
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    mission = data.get("mission") or tool_input.get("mission")
    if not mission:
        m = _MISSION_RE.search(str(tool_input.get("prompt") or ""))
        mission = m.group(1) if m else os.environ.get("SDDA_MISSION") or None
    return (str(mission) if mission else None), (data.get("target") or tool_input.get("target"))


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
    mission, target = _mission_and_target(data)
    resolution = context_pack.resolve_context(root, loader, agent, report=report,
                                              mission=mission, target=target)
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
