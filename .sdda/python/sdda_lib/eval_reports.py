"""Rapports d'évaluation et baselines sur disque — plomberie commune.

    workspace/.sys/reports/{n}-{RUN_ID}.json     rapports produits par eval_runner.py
    workspace/pipeline/baselines/{n}-system.json     baseline épinglée (P10), déplacée
                                                  UNIQUEMENT par promote_baseline.py

Partagé par `check_baseline_freshness.py`, `promote_baseline.py` et
`check_regression.py`. Aucune décision ici : lecture, localisation, écriture
atomique.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sdda_lib import paths
from sdda_lib.eval_pinning import PinTuple
from sdda_lib.runtime_io import atomic_write_json  # noqa: F401 — ré-exporté : les appelants historiques l'importent d'ici

REPORT_SUFFIX = ".json"


def reports_dir(root: Path) -> Path:
    return paths.reports_dir(root)


def baselines_dir(root: Path) -> Path:
    return paths.baselines_dir(root)


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


_RUN_SUFFIX_RE = re.compile(r"^(?P<run>.+?)(?:-(?P<k>\d+))?$")


def _report_order(number: int, path: Path) -> tuple[str, int]:
    """(RUN_ID, rang dans le run) : l'ordre dans lequel `eval_runner` écrit.

    L'ordre lexical des noms plaçait `1-RUN-2.json` AVANT `1-RUN.json` (`-`
    0x2D < `.` 0x2E) et `-10` avant `-2` : `latest_report` rendait donc le
    premier rapport du dernier run, pas le dernier — celui de la PHASE 4 au lieu
    de l'acceptation, pour `check-regression`, `promote-baseline` et `cost-report`.
    """
    stem = path.name[len(f"{number}-"):-len(REPORT_SUFFIX)]
    m = _RUN_SUFFIX_RE.match(stem)
    run, k = (m.group("run"), int(m.group("k") or 1)) if m else (stem, 1)
    return run, k


def list_reports(root: Path, number: int) -> list[Path]:
    """Rapports d'une mission, du plus ancien au plus récent (RUN_ID horodaté, puis rang)."""
    d = reports_dir(root)
    if not d.is_dir():
        return []
    found = [p for p in d.glob(f"{number}-*{REPORT_SUFFIX}") if p.is_file()]
    return sorted(found, key=lambda p: (*_report_order(number, p), p.name))


def latest_report(root: Path, number: int) -> Path | None:
    found = list_reports(root, number)
    return found[-1] if found else None


def reports_for_run(root: Path, number: int, run_id: str) -> list[Path]:
    """Tous les rapports d'UN run, dans l'ordre où `eval_runner` les a écrits.

    Un `/sdda-full` propage un seul `RUN_ID` à toutes ses sous-commandes, et
    `eval_runner` est appelé plusieurs fois dans ce run (G5, G6, PHASE 6, L8,
    G8). Le premier écrit `{n}-{RUN_ID}.json`, les suivants `{n}-{RUN_ID}-2.json`,
    `-3`… (`unique_report_path`). Chercher le seul `{n}-{RUN_ID}.json` rendait
    donc à `check-regression --run` et `promote-baseline --run` le rapport L4
    de la PHASE 4 — jamais celui de l'acceptation.
    """
    d = reports_dir(root)
    if not d.is_dir():
        return []
    stem = f"{number}-{run_id}"
    found: list[tuple[int, Path]] = []
    base = d / f"{stem}{REPORT_SUFFIX}"
    if base.is_file():
        found.append((1, base))
    for p in d.glob(f"{stem}-*{REPORT_SUFFIX}"):
        tail = p.name[len(stem) + 1:-len(REPORT_SUFFIX)]
        if tail.isdigit() and p.is_file():
            found.append((int(tail), p))
    return [p for _, p in sorted(found)]


def report_by_run_id(root: Path, number: int, run_id: str) -> Path | None:
    """Le DERNIER rapport écrit par ce run (cf. `reports_for_run`)."""
    found = reports_for_run(root, number, run_id)
    return found[-1] if found else None


def merged_run_report(root: Path, number: int, run_id: str) -> dict[str, Any] | None:
    """Les rapports d'un run réunis en un : chaque suite à sa DERNIÈRE mesure du run.

    Ce que `check-regression --run` et `promote-baseline --run` comparent ou
    promeuvent : le système tel que ce run l'a mesuré, suite par suite. Le
    dernier rapport seul (l'acceptation, L9) ne porte que la suite holdout —
    promouvoir « le run » depuis lui laissait toutes les autres suites sans
    baseline, donc sans régression mesurable.
    """
    loaded = [(p, load_json(p)) for p in reports_for_run(root, number, run_id)]
    datas: list[tuple[Path, dict[str, Any]]] = [(p, d) for p, d in loaded if d is not None]
    if not datas:
        return None
    merged = dict(datas[-1][1])
    suites: dict[str, dict[str, Any]] = {}
    for _, data in datas:
        suites.update(report_suites(data))
    merged["suites"] = [suites[sid] for sid in sorted(suites)]
    merged["sourceReports"] = [paths.rel(root, p) for p, _ in datas]
    return merged


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


# `atomic_write_json` vit dans `sdda_lib.runtime_io` (ré-exporté ci-dessus).
