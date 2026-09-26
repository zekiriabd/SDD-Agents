#!/usr/bin/env python3
"""G1 franchie avant la phase 2 — invariant `cap-ac-must-be-evaluable`.

Architecturer sur des CAPs dont les critères ne sont pas mesurables produit une
topologie qu'aucune eval ne pourra juger. On le découvre en phase 6, après avoir
payé les contrats, le code et les prompts.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook import PHASE2_ARCHITECTS, mission_of, require_gate, run  # noqa: E402

HOOK = "preflight_cap_gate"

#: Câblage — lu par `harness_build.py` pour générer `settings.json`.
#: Restreint aux architectes de phase 2 : avant eux, aucune gate ne peut être
#: verte, et se prononcer sur tout `Task` refuserait le premier agent du
#: pipeline.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": PHASE2_ARCHITECTS}


def check(root: Path, data: dict) -> int:
    return require_gate(
        HOOK, root, "G1", mission_of(data),
        "CAP_GATE_NOT_PASSED",
        "lancer `/sdda-caps {n}` et obtenir un verdict vert avant `/sdda-topology`. "
        "Un AC non mesurable est [AC_NOT_EVALUABLE] : il se corrige dans la CAP, pas plus tard",
    )


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
