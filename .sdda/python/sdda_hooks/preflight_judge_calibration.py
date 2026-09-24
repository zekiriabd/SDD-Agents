#!/usr/bin/env python3
"""Tout juge LLM bloquant est calibré — invariant `llm-judge-calibrated` (P9).

Utiliser un LLM comme grader est légitime et souvent inévitable. Le laisser
rendre un verdict BLOQUANT sans l'avoir validé contre des labels humains ne
l'est pas : on mesure alors la complaisance d'un modèle envers un autre —
souvent le même — et on appelle ça de la qualité.

Un juge non calibré n'est pas interdit : il est rétrogradé en `advisory`. C'est
la nuance qui rend la règle tenable — sinon elle se contourne le jour où elle
gêne, et elle ne revient jamais.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, EVAL_BUILDERS, allow, deny, run  # noqa: E402

HOOK = "preflight_judge_calibration"

#: Câblage — lu par `harness_build.py`. Devant ceux qui font rendre un verdict
#: à un juge. Un juge non calibré n'est pas refusé, il est rétrogradé en
#: `advisory` : le refus ne porte que sur un juge déclaré BLOQUANT sans mesure.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": EVAL_BUILDERS}


def check(root: Path, data: dict) -> int:
    from sdda_lib.layered_config import read_layered_config  # noqa: E402
    from sdda_scripts import calibrate_judge  # noqa: E402

    try:
        config = read_layered_config(root)
        min_kappa = config.get_float("JudgeCalibrationMinKappa", 0.6)
        min_items = config.get_int("JudgeCalibrationMinItems", 50)
    except Exception:
        min_kappa, min_items = 0.6, 50

    from sdda_lib import paths  # noqa: E402 — import tardif : coût de démarrage du hook

    # Par `paths`, jamais par un chemin assemblé à la main. Assemblé ici, il a
    # survécu intact à la réorganisation de l'arbre : le hook cherchait dans un
    # répertoire disparu, n'y trouvait rien, concluait « aucun rapport de
    # calibration » et AUTORISAIT. Un juge bloquant non calibré serait passé,
    # et le message de sortie aurait dit que tout allait bien.
    cal_dir = paths.calibration_dir(root)
    reports = list(cal_dir.glob("*.json")) if cal_dir.is_dir() else []
    if not reports:
        return allow("aucun rapport de calibration — les juges restent en `advisory` jusqu'à mesure")

    import json  # noqa: E402

    uncalibrated: list[str] = []
    for path in sorted(reports):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not payload.get("blocking", False):
            continue  # juge `advisory` : la calibration n'est pas exigée
        kappa = payload.get("kappa")
        items = payload.get("items") or payload.get("n") or 0
        if not isinstance(kappa, (int, float)) or kappa < min_kappa or int(items) < min_items:
            uncalibrated.append(f"{path.stem} (kappa={kappa}, n={items})")

    if not uncalibrated:
        return ALLOW
    return deny(HOOK, "JUDGE_NOT_CALIBRATED",
                f"{len(uncalibrated)} juge(s) bloquant(s) non calibré(s) — {uncalibrated[0]}",
                f"calibrer contre >= {min_items} labels humains jusqu'à kappa >= {min_kappa} "
                f"(`calibrate_judge.py`), ou basculer le juge en `advisory`. "
                f"Module : {calibrate_judge.__name__}")


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
