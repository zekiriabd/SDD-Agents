#!/usr/bin/env python3
"""Régression — un rapport d'évaluation contre la baseline épinglée (L9, G8).

Pour chaque suite du rapport qui a une baseline :

  1. Les tuples d'épinglage doivent être IDENTIQUES. Sinon la comparaison est
     REFUSÉE : [EVAL_BASELINE_STALE], bloquant. Comparer à une baseline qui
     mesurait un autre prompt, un autre modèle ou un autre jeu est pire que ne
     pas comparer : le chiffre aurait l'air d'une information.
  2. `regression_delta` (eval_pinning) donne la variation en % **dans le sens de
     la métrique** : une latence qui baisse est une amélioration, un
     groundedness qui baisse ne l'est pas. Au-delà de `RegressionTolerancePct`
     (3 %) : [REGRESSION], bloquant…
  3. …sauf si la baisse tient dans la **bande de bruit de la baseline**. Une
     baseline est une moyenne sur k runs et porte son écart-type ; une baisse
     de 4 % sur une métrique dont l'écart-type est 3 % n'est pas une régression,
     c'est un tirage. Sous `RegressionNoiseSigma` (2.0) écarts-types :
     WARN [REGRESSION_WITHIN_NOISE], et le verdict reste vert. Déclarer une
     régression sans lire l'écart-type, c'est bloquer un run sur du bruit — et
     un run bloqué sur du bruit, c'est une tolérance qu'on finit par relever
     jusqu'à ce qu'elle ne mesure plus rien.

Une suite sans baseline est signalée WARN [EVAL_BASELINE_MISSING] — rien n'est
comparable, et le dire vaut mieux que rendre un vert vide. Une suite
`advisory` informe sans bloquer.

Usage :
    python .sdda/sdda.py check-regression --mission 1                       # dernier rapport vs baseline
    python .sdda/sdda.py check-regression --mission 1 --report workspace/evals/reports/1-x.json --json
    python .sdda/sdda.py check-regression --mission 1 --tolerance 5 --noise-sigma 0   # strict : l'écart-type est ignoré
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.eval_pinning import Baseline, load_baselines, regression_delta  # noqa: E402
from sdda_lib.eval_reports import baseline_path, find_ir_file, latest_report, load_json, mission_number, report_by_run_id, report_suites, suite_pins  # noqa: E402
from sdda_lib.eval_stats import parse_threshold  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, load_config, resolve_root  # noqa: E402


def compare(
    root: Path,
    ir: dict[str, Any],
    report_path: Path,
    *,
    tolerance_pct: float,
    noise_sigma: float = 2.0,
    baseline_file: Path | None = None,
    require_baseline: bool = False,
) -> Report:
    mid = str(ir.get("missionId", ""))
    number = mission_number(ir)
    report = Report(name="EVAL.regression", target=mid)
    rloc = paths.rel(root, report_path)
    data = load_json(report_path)
    if data is None:
        report.error("EVAL_REPORT_NOT_FOUND", f"rapport `{rloc}` illisible ou absent", "eval_runner.py produit workspace/evals/reports/{n}-{RUN_ID}.json", rloc)
        return report

    bpath = baseline_file or baseline_path(root, number, ir)
    bloc = paths.rel(root, bpath)
    baselines: dict[str, Baseline] = load_baselines(bpath)
    entries = report_suites(data)
    rows: list[dict[str, Any]] = []

    if not baselines:
        emit = report.error if require_baseline else report.warn
        emit("EVAL_BASELINE_MISSING", f"aucune baseline dans `{bloc}` : aucune régression n'est mesurable", "promote_baseline.py --label « … » après un premier résultat lu et accepté", bloc)

    for sid, entry in sorted(entries.items()):
        base = baselines.get(sid)
        advisory = bool(entry.get("advisory"))
        row: dict[str, Any] = {"suiteId": sid, "metric": entry.get("metric"), "advisory": advisory, "mean": entry.get("mean"), "baselineMean": None, "baselineStddev": None, "noiseBand": None, "deltaPct": None, "status": "no-baseline", "moved": []}
        if base is None:
            if baselines:
                report.warn("EVAL_BASELINE_MISSING", f"suite `{sid}` : pas de baseline dans `{bloc}`", "promote_baseline.py --suite {sid}", sid)
            rows.append(row)
            continue
        row["baselineMean"] = base.mean
        row["baselineStddev"] = base.stddev
        moved = base.pins.diff(suite_pins(entry))
        if moved:
            row.update({"status": "stale", "moved": sorted(moved)})
            report.error("EVAL_BASELINE_STALE", f"suite `{sid}` : comparaison refusée — {', '.join(sorted(moved))} diffère entre le rapport et la baseline ; les deux ne mesurent pas la même chose",
                         "ré-exécuter puis promote_baseline.py pour rebaser, ou revenir à l'état épinglé — ne jamais lire un delta entre deux systèmes différents", sid)
            rows.append(row)
            continue
        threshold = parse_threshold(entry.get("threshold", 0.0))
        delta = regression_delta(base, float(entry.get("mean", 0.0)), threshold.lower_is_better)
        row["deltaPct"] = round(delta, 3)
        # La bande de bruit se lit sur l'écart-type de la BASELINE — celui du
        # rapport courant dirait « ce run est instable », ce qui est une autre
        # information (le verdict jaune de eval_runner la porte déjà).
        band = float(noise_sigma) * float(base.stddev or 0.0)
        row["noiseBand"] = round(band, 6) if band else 0.0
        drop_abs = abs(float(entry.get("mean", 0.0)) - base.mean)
        if delta < -tolerance_pct:
            msg = f"suite `{sid}` : {entry.get('metric')} {base.mean:.4f} -> {float(entry.get('mean', 0.0)):.4f} ({delta:+.2f} %) au-delà de RegressionTolerancePct {tolerance_pct:g} %"
            if band > 0 and drop_abs <= band:
                row["status"] = "within-noise"
                report.warn(
                    "REGRESSION_WITHIN_NOISE",
                    msg + f" — mais sous {noise_sigma:g} σ de la baseline (σ = {base.stddev:.4f}, bande ±{band:.4f}) : bruit d'échantillonnage, pas une régression",
                    "relancer avec k plus grand si la variance de la baseline paraît trop large pour juger ; RegressionNoiseSigma: 0 rend le contrôle strict",
                    sid,
                )
                rows.append(row)
                continue
            row["status"] = "regression"
            if band > 0:
                msg += f" et hors de la bande de bruit ({noise_sigma:g} σ = ±{band:.4f})"
            if advisory:
                report.warn("REGRESSION", msg + " (advisory : informe, ne bloque pas)", "", sid)
            else:
                report.error("REGRESSION", msg, "situer l'étage (eval-protocol.md §10) et corriger ; la baseline ne se déplace que par promote_baseline.py", sid)
        elif delta > tolerance_pct:
            row["status"] = "improved"
        else:
            row["status"] = "stable"
        rows.append(row)

    report.data.update({"report": rloc, "baseline": bloc, "tolerancePct": tolerance_pct, "noiseSigma": noise_sigma, "runId": data.get("runId"), "suites": rows,
                        "regressions": sorted(r["suiteId"] for r in rows if r["status"] == "regression"),
                        "withinNoise": sorted(r["suiteId"] for r in rows if r["status"] == "within-noise"),
                        "stale": sorted(r["suiteId"] for r in rows if r["status"] == "stale")})
    return report


def render_rows(report: Report) -> str:
    lines = []
    for r in report.data.get("suites") or []:
        delta = f"{r['deltaPct']:+.2f} %" if r["deltaPct"] is not None else "—"
        base = f"{r['baselineMean']:.4f}" if r["baselineMean"] is not None else "—"
        sigma = f"  σ={r['baselineStddev']:.4f}" if r.get("baselineStddev") else ""
        lines.append(f"  {r['status']:<12} {r['suiteId']:<40} {r['metric'] or '':<24} {base} -> {float(r['mean'] or 0):.4f}  {delta}{sigma}" + ("  [advisory]" if r["advisory"] else ""))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Régression d'un rapport d'évaluation contre la baseline épinglée (L9)")
    p.add_argument("--mission", type=int, default=None, help="numéro de mission ; défaut : l'unique IR compilé")
    p.add_argument("--report", type=Path, default=None, help="rapport à comparer (défaut : le plus récent de la mission)")
    p.add_argument("--run", default=None, help="RUN_ID du rapport")
    p.add_argument("--baseline", type=Path, default=None, help="fichier de baselines (défaut : evaluation.baselineRef)")
    p.add_argument("--tolerance", type=float, default=None, help="tolérance en %% (défaut : RegressionTolerancePct)")
    p.add_argument("--noise-sigma", type=float, default=None, help="bande de bruit en écarts-types de la baseline (défaut : RegressionNoiseSigma ; 0 = strict)")
    p.add_argument("--require-baseline", action="store_true", help="l'absence de baseline devient bloquante")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="EVAL.regression", target=str(root))
    config = load_config(root, report)
    ir_file, why = find_ir_file(root, args.mission)
    if ir_file is None:
        report.error("IR_NOT_FOUND", why, "compiler : python .sdda/sdda.py ir-compiler --mission {n}", str(paths.ir_dir(root)))
        return finish(report, args)
    ir = ir_compiler.load_ir(ir_file)
    number = mission_number(ir)
    if args.report is not None:
        rpath = args.report if args.report.is_absolute() else root / args.report
    elif args.run:
        rpath = report_by_run_id(root, number, args.run) or (paths.evals_dir(root) / "reports" / f"{number}-{args.run}.json")
    else:
        rpath = latest_report(root, number) or (paths.evals_dir(root) / "reports" / f"{number}-<aucun>.json")
    tolerance = args.tolerance if args.tolerance is not None else config.get_float("RegressionTolerancePct", 3.0)
    noise_sigma = args.noise_sigma if args.noise_sigma is not None else config.get_float("RegressionNoiseSigma", 2.0)
    bfile = args.baseline if args.baseline is None or args.baseline.is_absolute() else root / args.baseline
    sub = compare(root, ir, rpath, tolerance_pct=tolerance, noise_sigma=noise_sigma, baseline_file=bfile, require_baseline=args.require_baseline)
    report.extend(sub)
    report.data.update(sub.data)
    report.target = sub.target
    # Pas de rapport de gate ici : G8 est composite (`datasets`, `acceptance`) et
    # la régression est UNE ligne de son verdict, portée par la sortie JSON que
    # /sdda-eval agrège — un rapport à part que compute_status ignorerait
    # ne ferait que rassurer.
    if not args.json:
        detail = render_rows(sub)
        if detail:
            print(detail)
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
