#!/usr/bin/env python3
"""Aucun prompt inline dans le code généré — invariant `prompts-are-files` (P1).

Joué après chaque `SubagentStop` : c'est le moment où le code vient d'être écrit
et où la faute est encore attribuable à un agent précis. Un prompt noyé dans une
f-string n'a pas de hash, donc pas d'épinglage P10, donc aucune eval rejouable.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, deny, run  # noqa: E402

HOOK = "postflight_no_inline_prompt"

#: Câblage — lu par `harness_build.py`. Après tout agent : un prompt inline
#: peut être écrit par n'importe quel `dev-*`, et c'est au SubagentStop que la
#: faute est encore attribuable.
WIRING = {"event": "SubagentStop", "matcher": "*", "applies_to": ()}


def check(root: Path, data: dict) -> int:
    from sdda_lib.errors import Report  # noqa: E402
    from sdda_scripts import lint_prompts  # noqa: E402

    report = Report(name="PROMPTS-HOOK", target=str(root))
    lint_prompts.scan_inline_prompts(root, report)
    inline = [f for f in report.errors if f.cls == "PROMPT_INLINE_FORBIDDEN"]
    if not inline:
        return ALLOW
    return deny(HOOK, "PROMPT_INLINE_FORBIDDEN",
                f"{len(inline)} prompt(s) en dur dans le code — {inline[0].message}",
                "déplacer le texte dans `workspace/src/{App}/prompts/{slug}.system.md` et le charger au "
                "démarrage : un prompt sans fichier n'a pas de hash, donc pas d'eval rejouable")


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
