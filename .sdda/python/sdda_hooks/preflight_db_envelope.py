#!/usr/bin/env python3
"""L'enveloppe d'accès aux données est présente et bornée — invariant
`db-safety-envelope-present`.

Un agent avec un accès non enveloppé sur une base de production est un incident
qui attend son heure ; sur un répertoire de fichiers, c'est une traversée vers
`../.env`. Les deux défaillances sont silencieuses.

Délègue au validateur plutôt que de dupliquer sa logique : deux implémentations
d'une même règle divergent, et c'est celle qui ne bloque pas qui survit.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, DATA_BUILDERS, deny, run  # noqa: E402

HOOK = "preflight_db_envelope"

#: Câblage — lu par `harness_build.py`. Se prononce devant ceux dont le code
#: touchera des données. `architect-data` en est exclu : c'est lui qui
#: écrit l'enveloppe qu'on exige.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": DATA_BUILDERS}


def check(root: Path, data: dict) -> int:
    from sdda_scripts import validate_data_access  # noqa: E402

    report = validate_data_access.run(root, mission=data.get("mission"))
    if report.ok:
        return ALLOW
    first = report.errors[0]
    return deny(HOOK, first.cls, first.message,
                first.fix or "compléter l'enveloppe dans STACK.md — une borne absente est une borne infinie")


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
