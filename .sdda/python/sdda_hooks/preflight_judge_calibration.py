#!/usr/bin/env python3
"""Tout juge LLM bloquant est calibré — invariant `llm-judge-calibrated` (P9).

Utiliser un LLM comme grader est légitime et souvent inévitable. Le laisser
rendre un verdict BLOQUANT sans l'avoir validé contre des labels humains ne
l'est pas : on mesure alors la complaisance d'un modèle envers un autre —
souvent le même — et on appelle ça de la qualité.

Un juge non calibré n'est pas interdit : il est rétrogradé en `advisory`, et
c'est l'eval runner qui le fait, en ne donnant au juge que la calibration
MESURÉE par `calibrate-judge` (`calibration.measured_for_suite`). Ce hook
garde ce que le runner ne peut pas rattraper : lancer un agent d'évaluation
alors que la calibration mesurée de la MISSION est ROUGE — labels synthétiques
(`[JUDGE_CALIBRATION_SYNTHETIC]`), ou juge déclaré bloquant sans repli advisory.

Il lisait une clé `blocking` que rien n'écrit, dans les ENTRÉES de calibration
(`pipeline/calibration/`) : il autorisait donc tout, toujours.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import EVAL_BUILDERS, allow, deny, mission_of, run  # noqa: E402

HOOK = "preflight_judge_calibration"

#: Câblage — lu par `harness_build.py`. Devant ceux qui font rendre un verdict
#: à un juge. Un juge non calibré n'est pas refusé, il est rétrogradé en
#: `advisory` : le refus ne porte que sur une calibration mesurée ROUGE.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": EVAL_BUILDERS}


def check(root: Path, data: dict) -> int:
    from sdda_lib import gate_reports, paths  # noqa: E402 — import tardif : coût de démarrage du hook

    mission = mission_of(data)
    vdir = paths.validation_dir(root)
    candidates = ([gate_reports.report_path(root, "G5", mission, "calibration")] if mission
                  else sorted(vdir.glob("G5-*.calibration.json")) if vdir.is_dir() else [])
    red: list[str] = []
    for path in candidates:
        try:
            report = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(report, dict) and not report.get("ok", True):
            classes = sorted({str(e.get("class", "?")) for e in report.get("errors") or []})
            red.append(f"{path.name} : {', '.join(classes) or 'rouge'}")
    if not red:
        return allow("calibration mesurée non rouge — un juge non calibré reste `advisory`")
    return deny(HOOK, "JUDGE_NOT_CALIBRATED",
                f"calibration mesurée ROUGE — {red[0]}",
                "faire labelliser les items de calibration par un humain et relancer "
                "`python .sdda/sdda.py calibrate-judge --mission {n}` ; un juge sans mesure reste "
                "`advisory` (score informatif), il ne bloque rien")


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
