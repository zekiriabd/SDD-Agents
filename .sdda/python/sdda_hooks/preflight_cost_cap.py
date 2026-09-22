#!/usr/bin/env python3
"""Le plafond de coût de CONSTRUCTION n'est pas encore atteint — avant le spawn.

`MaxCostPerRun` plafonne ce que le pipeline SDD_Agents s'autorise à dépenser
pour construire. Ce n'est pas le budget du produit généré (P6, `## Project
Config → CostPerRun*`) : confondre les deux rend les deux incalculables.

Le contrôle porte sur le CUMUL déjà dépensé, lu dans l'état du run. Un plafond
vérifié après coup n'est pas un plafond, c'est une facture.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, deny, run  # noqa: E402

HOOK = "preflight_cost_cap"

#: Câblage — lu par `harness_build.py`. Tout spawn : le plafond de
#: construction porte sur le cumul du run, donc sur chaque agent qui le gonfle.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": ()}


def _spent(root: Path) -> float:
    """Cumul dépensé sur le run courant, depuis l'état écrit par les commandes."""
    from sdda_lib import paths  # noqa: E402

    state_dir = paths.state_dir(root)
    if not state_dir.is_dir():
        return 0.0

    # Deux corrections, et chacune suffisait à rendre le plafond inopérant :
    #
    # 1. Les runs vivent sous `.sys/.state/runs/`, pas à la racine. Un `glob`
    #    non récursif à la racine ne trouvait JAMAIS un fichier de run.
    # 2. `set_phase` range les données sous `phases[].payload`, jamais à plat
    #    sur la phase. On lisait donc une clé qui n'existe pas.
    #
    # Résultat cumulé : `_spent()` retournait toujours 0.0 et le hook autorisait
    # toujours. `MaxCostPerRun: 50.00` était une phrase, pas un plafond — et un
    # plafond qu'on croit actif est plus dangereux qu'un plafond absent.
    run_id = os.environ.get("SDDA_RUN_ID", "").strip()
    candidates = sorted(runs.glob("*.json")) if (runs := state_dir / "runs").is_dir() else []
    if run_id:
        # Le plafond porte sur LE run courant, pas sur l'historique du projet :
        # sommer tous les runs ferait buter le énième run sur la facture des
        # précédents, et on relèverait le cap pour de mauvaises raisons.
        candidates = [p for p in candidates if p.stem == run_id] or candidates

    total = 0.0
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for key in ("costUsd", "cost_usd", "spentUsd"):
            if isinstance(data.get(key), (int, float)):
                total += float(data[key])
                break
        for phase in (data.get("phases") or []):
            if not isinstance(phase, dict):
                continue
            payload = phase.get("payload") if isinstance(phase.get("payload"), dict) else {}
            for key in ("costUsd", "cost_usd", "spentUsd"):
                value = payload.get(key, phase.get(key))
                if isinstance(value, (int, float)):
                    total += float(value)
                    break
    return total


def check(root: Path, data: dict) -> int:
    from sdda_lib.layered_config import read_layered_config  # noqa: E402

    try:
        config = read_layered_config(root)
    except Exception:
        return ALLOW  # config illisible : c'est le travail d'un validateur, pas d'un hook

    cap = config.get_float("MaxCostPerRun", 0.0)
    if cap <= 0:
        return ALLOW

    spent = _spent(root)
    if spent < cap:
        return ALLOW
    return deny(HOOK, "COST_CAP_EXCEEDED",
                f"cumul de construction ${spent:.2f} >= plafond MaxCostPerRun ${cap:.2f}",
                "arrêter le run et décider explicitement : relever `MaxCostPerRun` dans "
                "`## Project Config`, ou réduire le périmètre. Un plafond qu'on relève sans "
                "le dire n'en est plus un")


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
