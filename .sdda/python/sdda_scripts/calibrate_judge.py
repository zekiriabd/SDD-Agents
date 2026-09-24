#!/usr/bin/env python3
"""
calibrate_judge — valide un grader LLM contre des labels humains (P9).

Un grader LLM non calibré ne rend pas de verdict bloquant. Sous le seuil, il
bascule en `advisory` : il informe, il ne bloque plus.

Usage :
    python .sdda/sdda.py calibrate-judge --root .
    python .sdda/sdda.py calibrate-judge --root . --grader groundedness
    python .sdda/sdda.py calibrate-judge --root . --json

Exit : 0 si tous les juges BLOQUANTS sont calibrés · 1 sinon.
Un juge déjà déclaré `advisory` dans l'IR ne fait pas échouer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import calibration, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.layered_config import read_layered_config  # noqa: E402

from sdda_lib.runtime_io import ensure_utf8_stdout  # noqa: E402

try:
    ensure_utf8_stdout()
except Exception:
    # Ignorable : un encodage de console non reconfigurable ne doit pas
    # empêcher la calibration ; `ensure_utf8_stdout` avale déjà ce cas, ce
    # bloc n'est qu'une ceinture en plus.
    pass


def judge_suites(ir: dict) -> list[dict]:
    return [s for s in (ir.get("evaluation") or {}).get("suites") or [] if s.get("grader") == "llm-judge"]


def run(root: Path, *, only: str | None = None, write: bool = True, mission: int | None = None) -> tuple[Report, list[dict]]:
    config = read_layered_config(root)
    report = Report(name="G5.calibration", target=str(root))
    outcomes: list[dict] = []

    min_items = int(config.get("JudgeCalibrationMinItems", 50))
    min_kappa = float(config.get("JudgeCalibrationMinKappa", 0.6))
    advisory_fallback = bool(config.get("JudgeAdvisoryFallback", True))

    # `--mission` restreint aux suites de CETTE mission. Les commandes ne
    # connaissent qu'un numéro et le passaient déjà ; le script ne le lisait
    # pas, donc l'appel sortait en `usage:` argparse au lieu de calibrer — et
    # un juge non calibré bascule en `advisory`, c'est-à-dire cesse de bloquer
    # sans que personne ne l'ait décidé.
    pattern = f"{mission}-system.ir.json" if mission is not None else "*-system.ir.json"
    ir_files = sorted(paths.ir_dir(root).glob(pattern))
    if not ir_files:
        report.error(
            "IR_NOT_FOUND",
            f"aucun IR compilé sous workspace/.sys/.ir/{'' if mission is None else f' pour la mission {mission}'}",
            "lancer /sdda-topology {n} pour compiler l'IR",
            "workspace/.sys/.ir/",
        )
        return report, outcomes

    for ir_path in ir_files:
        try:
            ir = json.loads(ir_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.error("IR_INVALID", f"{ir_path.name} illisible ({exc})", "recompiler l'IR", paths.rel(root, ir_path))
            continue

        for suite in judge_suites(ir):
            suite_id = str(suite.get("id", "?"))
            if only and only not in suite_id:
                continue
            declared_advisory = bool(suite.get("advisory"))
            loc = paths.rel(root, ir_path)

            ref = suite.get("judgeCalibrationRef")
            if not ref:
                # `validate_ir` contrôle 11 l'attrape déjà en amont ; on le
                # redit ici pour que le script soit utilisable seul.
                report.error(
                    "JUDGE_UNCALIBRATED",
                    f"suite `{suite_id}` : grader llm-judge sans `judgeCalibrationRef`",
                    "déclarer le rapport de calibration dans l'AC de la CAP",
                    loc,
                )
                continue

            path = paths.resolve_rel(root, str(ref))
            dataset = calibration.load_calibration_set(path)
            if dataset is None:
                level = report.warn if declared_advisory else report.error
                level(
                    "JUDGE_UNCALIBRATED",
                    f"suite `{suite_id}` : jeu de calibration `{ref}` introuvable ou illisible",
                    f"constituer >= {min_items} items labellisés PAR UN HUMAIN dans `{ref}`",
                    loc,
                )
                outcomes.append({"suiteId": suite_id, "calibrated": False, "reason": "jeu absent",
                                 "advisory": True, "declaredAdvisory": declared_advisory})
                continue

            if dataset.declared_only:
                # Accord DÉCLARÉ, pas recalculé : les labels ne sont pas
                # accessibles. On l'accepte (ils portent souvent des PII) mais
                # on refuse de le confondre avec une mesure.
                result = calibration.CalibrationReport(
                    grader=dataset.grader,
                    items=dataset.declared_items,
                    scale=dataset.scale,
                    agreement=dataset.declared_agreement or 0.0,
                    raw_agreement=dataset.declared_agreement or 0.0,
                    min_items=min_items,
                    min_agreement=min_kappa,
                    labels_are_synthetic=dataset.labels_are_synthetic,
                    notes=["accord DÉCLARÉ dans le rapport, non recalculé (labels absents)"],
                )
                report.warn(
                    "JUDGE_UNCALIBRATED",
                    f"suite `{suite_id}` : accord {result.agreement:.3f} déclaré sans labels vérifiables",
                    "rendre `labelsRef` résolvable pour que l'accord soit recalculé — "
                    "un kappa écrit à la main est une affirmation, pas une mesure",
                    loc,
                )
            else:
                result = calibration.calibrate(
                    dataset.grader,
                    dataset.human,
                    dataset.judge,
                    min_items=min_items,
                    min_agreement=min_kappa,
                    scale=dataset.scale,
                    labels_are_synthetic=dataset.labels_are_synthetic,
                )
            payload = result.to_dict() | {
                "suiteId": suite_id,
                "declaredAdvisory": declared_advisory,
                "verified": not dataset.declared_only,
            }
            outcomes.append(payload)

            if result.labels_are_synthetic:
                # Sans bypass : des labels générés transforment la calibration
                # en accord de deux modèles entre eux.
                report.error(
                    "JUDGE_CALIBRATION_SYNTHETIC",
                    f"suite `{suite_id}` : les labels de `{ref}` sont produits par un modèle",
                    "faire labelliser les items par un humain — un juge calibré sur "
                    "des labels synthétiques n'est pas calibré",
                    loc,
                )
            elif not result.calibrated:
                if declared_advisory or advisory_fallback:
                    report.warn(
                        "JUDGE_UNCALIBRATED",
                        f"suite `{suite_id}` non calibrée ({result.reason}) -> advisory, ne bloque plus",
                        f"retravailler la grille, ou porter le jeu à >= {min_items} items",
                        loc,
                    )
                else:
                    report.error(
                        "JUDGE_UNCALIBRATED",
                        f"suite `{suite_id}` non calibrée : {result.reason}",
                        f"atteindre un accord >= {min_kappa:g} sur >= {min_items} items humains",
                        loc,
                    )
            for note in result.notes:
                report.warn("JUDGE_UNCALIBRATED", f"suite `{suite_id}` : {note}", "", loc)

    if write:
        _write_report(root, report, outcomes)
    return report, outcomes


def _write_report(root: Path, report: Report, outcomes: list[dict]) -> Path:
    from sdda_lib import gate_reports

    report.data["judges"] = outcomes
    return gate_reports.write_gate_report(root, "G5", "calibration", report, {}, part="calibration")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calibre les graders LLM contre des labels humains.")
    parser.add_argument("--root", default=".", help="racine du projet")
    parser.add_argument("--mission", type=int, default=None, help="numéro de mission ; défaut : tous les IR compilés")
    parser.add_argument("--grader", help="ne traiter que les suites dont l'id contient cette chaîne")
    parser.add_argument("--json", action="store_true", help="sortie machine")
    parser.add_argument("--no-report", action="store_true", help="ne pas écrire le rapport de gate")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()

    report, outcomes = run(root, only=args.grader, write=not args.no_report, mission=args.mission)

    if args.json:
        print(json.dumps(report.to_dict() | {"judges": outcomes}, ensure_ascii=False, indent=2))
    else:
        print(report.render_text())
        for outcome in outcomes:
            state = "calibré" if outcome.get("calibrated") else "ADVISORY"
            agreement = outcome.get("agreement")
            detail = f"{agreement:.3f}" if isinstance(agreement, (int, float)) else "n/a"
            print(f"  {state:<9} {outcome['suiteId']:<28} accord {detail}  ({outcome.get('reason', '')})")

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
