#!/usr/bin/env python3
"""Promotion d'un résultat d'évaluation en baseline — action explicite et tracée.

`BaselinePromotionPolicy: explicit` (eval-protocol.md §8) : la baseline ne
bouge JAMAIS d'elle-même. En mode `auto`, chaque run repartirait de son propre
résultat et le système descendrait sans que rien ne l'annonce — ce script est
précisément l'action humaine qui remplace cet automatisme, et il exige une
raison (`--label`) pour qu'elle reste lisible dans six mois.

Ce qu'il refuse :
    [EVAL_PROMOTION_LABEL_MISSING]  pas de --label : une promotion sans raison est un écrasement
    [EVAL_PROMOTION_REFUSED]        une suite ROUGE (verdict bloquant) sans --force
    [EVAL_BASELINE_STALE]           le rapport ne mesure plus le système sur disque
                                    (un prompt a bougé depuis le run) — sans --force
    [EVAL_REPORT_NOT_FOUND]         aucun rapport à promouvoir

`--force` est un bypass : il est journalisé dans `workspace/.sys/.audit/bypasses.jsonl`
(R5) et signalé WARN [EVAL_PROMOTION_FORCED]. Il ne fait pas disparaître le
verdict rouge de la baseline : celui-ci y est écrit tel quel.

Écriture atomique (temporaire + rename) dans `workspace/evals/baselines/{n}-system.json`.
Les suites non promues conservent leur baseline précédente.

Usage :
    python .sdda/sdda.py promote-baseline --mission 1 --label "après correction du chunking"
    python .sdda/sdda.py promote-baseline --mission 1 --run 20260920T101500Z --suite 1-1-routing_accuracy --label "…"
    python .sdda/sdda.py promote-baseline --mission 1 --report workspace/evals/reports/1-x.json --label "…" --force
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.eval_pinning import Baseline, PinTuple, current_pins, load_baselines  # noqa: E402
from sdda_lib.eval_reports import (  # noqa: E402
    atomic_write_json,
    baseline_path,
    find_ir_file,
    latest_report,
    load_json,
    mission_number,
    report_by_run_id,
    report_suites,
    suite_pins,
)
from sdda_lib.gate_reports import append_bypass_audit, now_iso  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, load_config, resolve_root  # noqa: E402


def locate_report(root: Path, number: int, *, report_arg: Path | None, run_id: str | None) -> tuple[Path | None, str]:
    if report_arg is not None:
        p = report_arg if report_arg.is_absolute() else root / report_arg
        return (p, "") if p.is_file() else (None, f"rapport `{paths.rel(root, p)}` introuvable")
    if run_id:
        p = report_by_run_id(root, number, run_id)
        return (p, "") if p else (None, f"aucun rapport `{number}-{run_id}.json` dans workspace/evals/reports/")
    p = latest_report(root, number)
    return (p, "") if p else (None, f"aucun rapport pour la mission {number} dans workspace/evals/reports/")


def promote(
    root: Path,
    ir: dict[str, Any],
    report_path: Path,
    *,
    label: str,
    force: bool = False,
    only: set[str] | None = None,
    operator: str | None = None,
    baseline_file: Path | None = None,
    write: bool = True,
) -> Report:
    """Promeut les suites d'un rapport. Rend le Report ; `data['promoted']` liste ce qui a bougé."""
    mid = str(ir.get("missionId", ""))
    number = mission_number(ir)
    report = Report(name="EVAL.promote", target=mid)
    rloc = paths.rel(root, report_path)

    if not label or not label.strip():
        report.error("EVAL_PROMOTION_LABEL_MISSING", "promotion sans raison", "--label « pourquoi cette baseline remplace la précédente » (ex. « après correction du chunking »)", rloc)
        return report

    data = load_json(report_path)
    if data is None:
        report.error("EVAL_REPORT_NOT_FOUND", f"rapport `{rloc}` illisible ou absent", "eval_runner.py produit workspace/evals/reports/{n}-{RUN_ID}.json", rloc)
        return report
    if str(data.get("missionId", "")) not in ("", mid):
        report.error("EVAL_REPORT_NOT_FOUND", f"rapport `{rloc}` porte `{data.get('missionId')}`, pas `{mid}`", "choisir un rapport de la même mission", rloc)
        return report

    entries = report_suites(data)
    if only:
        unknown = sorted(only - set(entries))
        for sid in unknown:
            report.error("EVAL_SUITE_NOT_FOUND", f"suite `{sid}` absente du rapport `{rloc}`", "vérifier --suite contre `suites[].suiteId` du rapport", sid)
        entries = {k: v for k, v in entries.items() if k in only}
    if not entries:
        report.error("EVAL_SUITE_NOT_FOUND", f"rien à promouvoir dans `{rloc}`", "relancer eval_runner.py avec des suites", rloc)
        return report

    suites_ir = {str(s.get("id")): s for s in ((ir.get("evaluation") or {}).get("suites") or []) if isinstance(s, dict)}
    policy = str(data.get("config", {}).get("promotionPolicy") or "explicit")
    refusals: list[str] = []
    stale: list[str] = []
    for sid, entry in sorted(entries.items()):
        blocking = str(entry.get("blockingVerdict") or entry.get("verdict") or "")
        if blocking == "red":
            refusals.append(sid)
            if not force:
                report.error("EVAL_PROMOTION_REFUSED", f"suite `{sid}` : verdict rouge ({entry.get('reason', '')}) — une baseline rouge institutionnaliserait la panne",
                             "corriger puis relancer eval_runner.py ; --force (audit-loggué) si la régression est assumée et documentée dans --label", sid)
        suite = suites_ir.get(sid)
        if suite is not None:
            now = current_pins(root, ir, suite=suite)
            moved = suite_pins(entry).diff(now)
            if moved:
                stale.append(sid)
                if not force:
                    report.error("EVAL_BASELINE_STALE", f"suite `{sid}` : le rapport ne mesure plus le système sur disque ({', '.join(sorted(moved))} a bougé depuis le run)",
                                 "ré-exécuter eval_runner.py sur l'état courant avant de promouvoir — promouvoir un résultat périmé fige une mesure d'autre chose", sid)

    if report.errors:
        report.data.update({"report": rloc, "promoted": [], "refused": refusals, "stale": stale})
        return report

    if force and (refusals or stale):
        why = f"promote_baseline --force ({mid}, {rloc}) : rouge={refusals} périmé={stale} — {label.strip()}"
        audit = append_bypass_audit(root, "G8", why, operator)
        report.warn("EVAL_PROMOTION_FORCED", f"promotion forcée de {sorted(set(refusals) | set(stale))} — journalisée dans {paths.rel(root, audit)}", "", rloc)
        report.data["bypassAudit"] = paths.rel(root, audit)

    bpath = baseline_file or baseline_path(root, number, ir)
    existing = load_baselines(bpath)
    merged: dict[str, dict[str, Any]] = {sid: b.to_dict() for sid, b in existing.items()}
    for previous in merged.values():
        previous.pop("suite_id", None)  # la clé du mapping porte déjà l'identifiant
    promoted: list[str] = []
    stamp = now_iso()
    for sid, entry in sorted(entries.items()):
        base = Baseline(
            suite_id=sid,
            metric=str(entry.get("metric", "")),
            mean=float(entry.get("mean", 0.0)),
            stddev=float(entry.get("stddev", 0.0)),
            pass_rate=float(entry.get("passRate", 0.0)),
            verdict=str(entry.get("verdict", "")),
            pins=PinTuple.from_dict(entry.get("pins")),
            recorded_at=stamp,
            label=label.strip(),
        )
        payload = base.to_dict()
        payload.pop("suite_id", None)
        payload.update({
            "threshold": entry.get("threshold"),
            "runs": entry.get("runs"),
            "advisory": bool(entry.get("advisory")),
            "blockingVerdict": entry.get("blockingVerdict"),
            "sourceReport": rloc,
            "runId": data.get("runId"),
            "forced": bool(force and (sid in refusals or sid in stale)),
            "promotedBy": operator or _operator(),
        })
        merged[sid] = payload
        promoted.append(sid)

    out = {
        "missionId": mid,
        "promotedAt": stamp,
        "promotionPolicy": policy,
        "lastLabel": label.strip(),
        "lastSourceReport": rloc,
        "baselines": dict(sorted(merged.items())),
    }
    if write:
        atomic_write_json(bpath, out)
    report.data.update({"report": rloc, "baseline": paths.rel(root, bpath), "promoted": promoted, "refused": refusals, "stale": stale, "label": label.strip()})
    return report


def _operator() -> str:
    import os

    return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Promeut un rapport d'évaluation en baseline — action explicite, tracée, atomique")
    p.add_argument("--mission", type=int, default=None, help="numéro de mission ; défaut : l'unique IR compilé")
    p.add_argument("--report", type=Path, default=None, help="rapport à promouvoir (défaut : le plus récent de la mission)")
    p.add_argument("--run", default=None, help="RUN_ID du rapport (workspace/evals/reports/{n}-{RUN_ID}.json)")
    p.add_argument("--suite", action="append", default=None, help="ne promouvoir que ces suites")
    p.add_argument("--label", default=None, help="raison courte, obligatoire (ex. « après correction du chunking »)")
    p.add_argument("--force", action="store_true", help="promouvoir malgré un rouge ou un résultat périmé — audit-loggué")
    p.add_argument("--operator", default=None, help="identité de l'opérateur pour l'audit (défaut : $USERNAME)")
    p.add_argument("--baseline", type=Path, default=None, help="fichier de baselines cible (défaut : evaluation.baselineRef)")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="EVAL.promote", target=str(root))
    config = load_config(root, report)
    ir_file, why = find_ir_file(root, args.mission)
    if ir_file is None:
        report.error("IR_NOT_FOUND", why, "compiler : python .sdda/sdda.py ir-compiler --mission {n}", str(paths.ir_dir(root)))
        return finish(report, args)
    ir = ir_compiler.load_ir(ir_file)
    number = mission_number(ir)
    rpath, why = locate_report(root, number, report_arg=args.report, run_id=args.run)
    if rpath is None:
        report.error("EVAL_REPORT_NOT_FOUND", why, "eval_runner.py produit le rapport à promouvoir", str(paths.evals_dir(root) / "reports"))
        return finish(report, args)
    only = {t.strip() for chunk in (args.suite or []) for t in chunk.split(",") if t.strip()} or None
    bfile = args.baseline if args.baseline is None or args.baseline.is_absolute() else root / args.baseline
    sub = promote(root, ir, rpath, label=args.label or "", force=args.force, only=only, operator=args.operator, baseline_file=bfile, write=not args.no_report)
    report.extend(sub)
    report.data.update(sub.data)
    report.data["promotionPolicy"] = str(config.get("BaselinePromotionPolicy", "explicit"))
    report.target = sub.target
    if not args.json and sub.data.get("promoted"):
        print(f"  promu : {', '.join(sub.data['promoted'])} -> {sub.data['baseline']}  (« {sub.data['label']} »)")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
