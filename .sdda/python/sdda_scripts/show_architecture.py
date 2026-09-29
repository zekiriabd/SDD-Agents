#!/usr/bin/env python3
"""L'architecture qu'une spec EXIGE, lisible avant de payer un agent (0 token).

`STACK.md` dit ce que le Tech Lead autorise ; la MISSION (`## Architecture
Needs`), le roster et, s'il existe, l'IR disent ce qui est exigé. Ce script rend
l'intersection — chaque capacité avec la preuve qui la justifie, le reste
listé comme omis — et le dimensionnement qui en découle (`micro` | `standard`).
C'est ce que `/sdda-topology` lit pour décider quels architectes de contrat
appeler, et ce qu'un humain relit quand le pipeline lui paraît trop lourd.

Usage :
    python .sdda/sdda.py show-architecture --mission 1 [--json]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import effective_architecture  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.spec_needs import load_needs, sizing  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402


def run(root: Path, mission: str) -> Report:
    report = Report(name="ARCHITECTURE", target=str(mission))
    needs = load_needs(root, mission)
    if not needs.declared:
        report.warn("MISSION_NEEDS_INVALID", f"MISSION {mission} sans `## Architecture Needs` : STACK.md décide seul "
                    "(repli historique), aucun dimensionnement possible",
                    "déclarer les besoins de la spec (templates/mission.template.md) — c'est elle qui choisit, "
                    "pas les activations de STACK.md")
    arch = effective_architecture.derive(root=root, mission=mission)
    report.data.update({
        "needs": needs.to_dict(),
        "sizing": sizing(root, mission).to_dict(),
        "architecture": arch.to_ir(),
    })
    return report


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="L'architecture exigée par la spec, et son dimensionnement.")
    parser.add_argument("--mission", required=True, help="numéro de MISSION")
    add_common_args(parser)
    args = parser.parse_args(argv)
    report = run(resolve_root(args), args.mission)
    if not args.json:
        data = report.data
        print(f"📐 {data['sizing']['complexity']} — "
              + "; ".join(data["sizing"]["reasons"] or data["sizing"]["blockers"]))
        for capability, reasons in data["architecture"]["required"].items():
            print(f"  + {capability:32} {reasons[0]}")
        print(f"  omis : {', '.join(data['architecture']['omitted'])}")
    return finish(report, args) if args.json or not report.ok else 0


if __name__ == "__main__":
    sys.exit(main())
