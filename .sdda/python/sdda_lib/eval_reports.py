"""Rapports d'évaluation et baselines sur disque — plomberie commune.

    workspace/evals/reports/{n}-{RUN_ID}.json     rapports produits par eval_runner.py
    workspace/evals/baselines/{n}-system.json     baseline épinglée (P10), déplacée
                                                  UNIQUEMENT par promote_baseline.py

Partagé par `check_baseline_freshness.py`, `promote_baseline.py` et
`check_regression.py`. Aucune décision ici : lecture, localisation, écriture
atomique.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sdda_lib import paths
from sdda_lib.eval_pinning import PinTuple

REPORT_SUFFIX = ".json"


def reports_dir(root: Path) -> Path:
    return paths.evals_dir(root) / "reports"


def baselines_dir(root: Path) -> Path:
    return paths.evals_dir(root) / "baselines"


def baseline_path(root: Path, number: int, ir: dict[str, Any] | None = None) -> Path:
    """`evaluation.baselineRef` de l'IR s'il existe, sinon l'emplacement canonique."""
    ref = str(((ir or {}).get("evaluation") or {}).get("baselineRef") or "")
    if ref:
        return paths.resolve_rel(root, ref)
    return baselines_dir(root) / f"{number}-system.json"


def find_ir_file(root: Path, mission: int | None) -> tuple[Path | None, str]:
    """Le fichier IR d'une mission ; sans numéro, l'unique IR compilé.

    Rend (chemin ou None, raison lisible si None).
    """
    if mission is not None:
        p = paths.ir_path(root, mission)
        return (p, "") if p.is_file() else (None, f"IR `{paths.rel(root, p)}` introuvable")
    candidates = sorted(paths.ir_dir(root).glob("*-system.ir.json"))
    if len(candidates) == 1:
        return candidates[0], ""
    return None, f"{len(candidates)} IR compilé(s) dans workspace/.sys/.ir/ : préciser --mission"


def mission_number(ir: dict[str, Any]) -> int:
    head = str(ir.get("missionId", "")).split("-", 1)[0]
    return int(head) if head.isdigit() else 0


def list_reports(root: Path, number: int) -> list[Path]:
    """Rapports d'une mission, du plus ancien au plus récent (ordre lexical = horodatage)."""
    d = reports_dir(root)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob(f"{number}-*{REPORT_SUFFIX}") if p.is_file())


def latest_report(root: Path, number: int) -> Path | None:
    found = list_reports(root, number)
    return found[-1] if found else None


def report_by_run_id(root: Path, number: int, run_id: str) -> Path | None:
    p = reports_dir(root) / f"{number}-{run_id}{REPORT_SUFFIX}"
    return p if p.is_file() else None


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def report_suites(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{suiteId: entrée de suite} d'un rapport d'eval_runner."""
    out: dict[str, dict[str, Any]] = {}
    for s in report.get("suites") or []:
        if isinstance(s, dict) and s.get("suiteId"):
            out[str(s["suiteId"])] = s
    return out


def suite_pins(entry: dict[str, Any]) -> PinTuple:
    return PinTuple.from_dict(entry.get("pins"))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    """Temporaire + `os.replace` : une baseline n'est jamais lue à moitié écrite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
