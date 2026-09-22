#!/usr/bin/env python3
"""Fraîcheur des baselines — enforcer de `eval-baseline-hash-pinned` (P10).

Pour chaque baseline de `workspace/evals/baselines/{n}-system.json`, recalcule
le tuple courant depuis le DISQUE :

    (prompt_hash, model_id, retrieval_index_hash, tool_schema_hash, dataset_hash)

et le compare au tuple épinglé. Une dimension qui a bougé rend la baseline
**périmée** — pas « probablement encore valable ». Le rapport dit laquelle :
c'est l'information qui oriente la reprise (un prompt édité n'appelle pas la
même action qu'un index reconstruit).

    [EVAL_BASELINE_STALE]   erreur si la suite est bloquante, WARN si advisory
    [EVAL_BASELINE_MISSING] WARN : aucune baseline — rien à périmer, rien à comparer
    [EVAL_SUITE_NOT_FOUND]  WARN : une baseline pour une suite que l'IR ne déclare plus

Exit 1 si au moins une baseline BLOQUANTE est périmée. `--strict` rend aussi
bloquantes les baselines advisory périmées.

Usage :
    python .sdda/sdda.py check-baseline-freshness --mission 1 [--json] [--strict]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.eval_pinning import Baseline, PinTuple, check_staleness, current_pins, load_baselines  # noqa: E402
from sdda_lib.eval_reports import baseline_path, find_ir_file, mission_number  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402


def check_freshness(root: Path, ir: dict[str, Any], *, baseline_file: Path | None = None, strict: bool = False) -> Report:
    """Compare chaque baseline au tuple courant. Rend un Report ; `data` porte le détail."""
    mid = str(ir.get("missionId", ""))
    number = mission_number(ir)
    report = Report(name="EVAL.freshness", target=mid)
    bpath = baseline_file or baseline_path(root, number, ir)
    loc = paths.rel(root, bpath)
    baselines: dict[str, Baseline] = load_baselines(bpath)
    suites = {str(s.get("id")): s for s in ((ir.get("evaluation") or {}).get("suites") or []) if isinstance(s, dict)}

    if not baselines:
        report.warn("EVAL_BASELINE_MISSING", f"aucune baseline dans `{loc}` — aucune régression n'est détectable tant qu'un résultat n'a pas été promu",
                    "eval_runner.py puis promote_baseline.py --label « … »", loc)
        report.data.update({"baseline": loc, "checked": 0, "stale": [], "fresh": []})
        return report

    current: dict[str, PinTuple] = {}
    for sid in sorted(baselines):
        suite = suites.get(sid)
        if suite is None:
            report.warn("EVAL_SUITE_NOT_FOUND", f"baseline `{sid}` sans suite correspondante dans l'IR — orpheline", "recompiler l'IR ou retirer la baseline via promote_baseline.py", loc)
            continue
        current[sid] = current_pins(root, ir, suite=suite)

    stale_out: list[dict[str, Any]] = []
    fresh: list[str] = []
    for st in check_staleness(baselines, current):
        if not st.stale:
            fresh.append(st.suite_id)
            continue
        suite = suites[st.suite_id]
        advisory = bool(suite.get("advisory"))
        moved = {k: {"pinned": v[0], "current": v[1]} for k, v in sorted(st.moved.items())}
        stale_out.append({"suiteId": st.suite_id, "advisory": advisory, "moved": moved})
        dims = ", ".join(sorted(st.moved))
        emit = report.warn if (advisory and not strict) else report.error
        emit("EVAL_BASELINE_STALE", f"baseline `{st.suite_id}`{' (advisory)' if advisory else ''} périmée — {dims} a bougé ; ses scores ne mesurent plus le système actuel",
             "ré-exécuter la suite (eval_runner.py) puis promouvoir explicitement (promote_baseline.py) — aucun résultat périmé n'est réutilisé", st.suite_id)

    report.data.update({"baseline": loc, "checked": len(current), "stale": stale_out, "fresh": sorted(fresh)})
    return report


def render_stale(report: Report) -> str:
    lines = []
    for entry in report.data.get("stale") or []:
        dims = " · ".join(f"{k}: {v['pinned'][:15]}… -> {v['current'][:15]}…" for k, v in entry["moved"].items())
        lines.append(f"  périmée  {entry['suiteId']:<40} {dims}")
    for sid in report.data.get("fresh") or []:
        lines.append(f"  à jour   {sid}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fraîcheur des baselines d'évaluation (tuple d'épinglage P10)")
    p.add_argument("--mission", type=int, default=None, help="numéro de mission ; défaut : l'unique IR compilé")
    p.add_argument("--baseline", type=Path, default=None, help="fichier de baselines (défaut : evaluation.baselineRef)")
    p.add_argument("--strict", action="store_true", help="une baseline advisory périmée bloque aussi")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="EVAL.freshness", target=str(root))
    ir_file, why = find_ir_file(root, args.mission)
    if ir_file is None:
        report.error("IR_NOT_FOUND", why, "compiler : python .sdda/sdda.py ir-compiler --mission {n}", str(paths.ir_dir(root)))
        return finish(report, args)
    ir = ir_compiler.load_ir(ir_file)
    bfile = args.baseline if args.baseline is None or args.baseline.is_absolute() else root / args.baseline
    sub = check_freshness(root, ir, baseline_file=bfile, strict=args.strict)
    report.extend(sub)
    report.data.update(sub.data)
    report.target = sub.target
    if not args.json:
        detail = render_stale(sub)
        if detail:
            print(detail)
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
