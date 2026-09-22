#!/usr/bin/env python3
"""Inventaire des graders (0 token) : nom, déterminisme, métriques servies.

Sert à `po-capabilities` pour choisir le grader le plus déterministe qui
répond à la question d'un AC (eval-protocol.md §4), et à vérifier qu'une
`metric` déclarée dans une CAP a bien un grader qui la sert.

Usage :
    python list_graders.py                         # tableau
    python list_graders.py --json
    python list_graders.py --metric groundedness   # qui sert cette métrique ? exit 1 si personne
    python list_graders.py --grader trajectory     # fiche d'un grader
    python list_graders.py --caps [--root …]       # chaque AC des CAPs : grader connu, métrique servie

Classes : `[AC_GRADER_UNKNOWN]` (erreur) — grader hors registre ;
`[EVAL_METRIC_UNSERVED]` — aucun grader ne déclare la métrique (erreur en
`--metric`, avertissement en `--caps` : la liste des métriques d'un grader est
indicative, l'AC peut nommer une métrique métier que le grader sert quand même).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io, paths  # noqa: E402
from sdda_lib.runtime_io import ensure_utf8_stdout  # noqa: E402

ensure_utf8_stdout()
from sdda_lib.errors import Report, SddaError  # noqa: E402
from sdda_lib.graders import GRADERS, describe, get, graders_for_metric  # noqa: E402
from sdda_scripts._common import add_common_args, finish, resolve_root  # noqa: E402

CLS_METRIC_UNSERVED = "EVAL_METRIC_UNSERVED"


def render_table(entries: list[dict]) -> str:
    lines = [f"{'grader':<22} {'déterministe':<13} {'score':<16} métriques servies"]
    for e in entries:
        det = "oui" if e["deterministic"] else "NON (k runs)"
        score = "brut (bas=mieux)" if not e["bounded"] else "[0,1]"
        lines.append(f"{e['name']:<22} {det:<13} {score:<16} {', '.join(e['metrics'])}")
    return "\n".join(lines)


def check_metrics(metrics: list[str], report: Report) -> None:
    served: dict[str, list[str]] = {}
    for metric in metrics:
        names = graders_for_metric(metric)
        served[metric] = names
        if not names:
            report.error(CLS_METRIC_UNSERVED, f"aucun grader ne déclare servir la métrique `{metric}`",
                         f"choisir une métrique servie ({', '.join(sorted({m for g in GRADERS.values() for m in g.metrics}))}) ou brancher un grader")
    report.data["metrics"] = served


def check_caps(root: Path, report: Report) -> None:
    """Chaque AC de chaque CAP : grader dans le registre, métrique servie par ce grader."""
    from sdda_scripts.validate_cap import parse_cap  # import tardif : ce mode seul en a besoin

    checked = 0
    for path in sorted(paths.caps_dir(root).glob("*.md")):
        spec = parse_cap(markdown_io.read_text(path), path)
        loc = paths.rel(root, path)
        for ac in spec.acs:
            grader_name = ac.fields.get("grader", "").strip()
            metric = ac.fields.get("metric", "").strip()
            if not grader_name or markdown_io.is_placeholder(grader_name):
                continue
            checked += 1
            try:
                grader = get(grader_name)
            except SddaError as exc:
                report.error(exc.cls, f"{spec.id} {ac.id} : {exc.detail}", exc.fix, loc)
                continue
            if metric and not markdown_io.is_placeholder(metric) and metric.casefold() not in grader.metrics:
                alternatives = graders_for_metric(metric)
                hint = f" — servie par : {', '.join(alternatives)}" if alternatives else " — servie par aucun grader"
                report.warn(CLS_METRIC_UNSERVED, f"{spec.id} {ac.id} : métrique `{metric}` non déclarée par le grader `{grader.name}`{hint}", "", loc)
    report.data["acsChecked"] = checked


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Inventaire des graders : déterminisme, métriques servies, couverture des CAPs")
    p.add_argument("--metric", action="append", default=[], help="métrique à résoudre (répétable) ; exit 1 si aucun grader ne la sert")
    p.add_argument("--grader", default=None, help="fiche d'un grader")
    p.add_argument("--caps", action="store_true", help="vérifier les AC des CAPs sous --root")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = Report(name="graders", target="registry")
    entries = describe()
    report.data["graders"] = entries

    if args.grader:
        try:
            grader = get(args.grader)
            entries = [e for e in entries if e["name"] == grader.name]
            report.data["graders"] = entries
            report.data["doc"] = (sys.modules[type(grader).__module__].__doc__ or "").strip()
        except SddaError as exc:
            report.findings.append(exc.to_finding())
    if args.metric:
        check_metrics(args.metric, report)
    if args.caps:
        root = resolve_root(args)
        report.target = str(root)
        check_caps(root, report)

    if not args.json:
        print(render_table(entries))
        if "doc" in report.data:
            print("\n" + report.data["doc"])
        for metric, names in report.data.get("metrics", {}).items():
            print(f"{metric}: {', '.join(names) if names else '— aucun grader'}")
        if report.findings or args.caps:
            print()
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
