#!/usr/bin/env python3
"""Vues d'IR par agent de construction — écrire, vérifier (0 token).

Chaque agent de construction lit SA vue de l'IR
(`workspace/.sys/.ir/views/{n}-{agent}.ir.json`, une par instance pour
`dev-agent`), pas l'IR entière : cf. `sdda_lib/ir_views.py` pour la raison et
les champs retenus. `ir-compiler` les régénère à chaque compilation ; ce script
les réécrit à la demande et vérifie qu'elles dérivent de l'IR courante.

Usage :
    python .sdda/sdda.py ir-view --mission 1 --write     # (ré)écrire toutes les vues
    python .sdda/sdda.py ir-view --mission 1 --check     # à jour ? (défaut)
    python .sdda/sdda.py ir-view --mission 1 --agent dev-tools
    python .sdda/sdda.py ir-view --mission 1 --agent dev-agent --instance 1-support

Exit : 0 = vues présentes et dérivées de l'IR courante · 1 sinon.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import ir_views, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.runtime_io import atomic_write_text  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402


def dump_view(view: dict[str, Any]) -> str:
    """Compact : une vue est lue par un modèle, pas par un humain — l'indentation
    d'un JSON profond en double la taille sans rien dire de plus."""
    return json.dumps(view, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"


def expected_views(ir_path: Path) -> dict[Path, str]:
    ir = json.loads(ir_path.read_text(encoding="utf-8-sig"))
    source_hash = ir_views.source_hash_of(ir_path)
    return {path: dump_view(view) for path, view in ir_views.all_views(ir, ir_path, source_hash=source_hash).items()}


def _stale_files(ir_path: Path, expected: dict[Path, str]) -> list[Path]:
    """Les vues de CETTE mission qui ne désignent plus rien (agent retiré du roster)."""
    number = ir_path.name.split("-", 1)[0]
    folder = ir_views.views_dir(ir_path)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob(f"{number}-*.ir.json") if p not in expected)


def write_views(root: Path, number: int, report: Report | None = None) -> list[Path]:
    """Écrit toutes les vues de la mission `number` ; retire les orphelines."""
    ir_path = paths.ir_path(root, number)
    expected = expected_views(ir_path)
    for path, text in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            atomic_write_text(path, text)
    for orphan in _stale_files(ir_path, expected):
        orphan.unlink()
    if report is not None:
        report.data["views"] = {paths.rel(root, p): len(t.encode("utf-8")) for p, t in sorted(expected.items())}
    return sorted(expected)


def check_views(root: Path, number: int, report: Report) -> bool:
    ir_path = paths.ir_path(root, number)
    rel_ir = paths.rel(root, ir_path)
    if not ir_path.is_file():
        report.error("IR_NOT_FOUND", f"MISSION {number} : IR `{rel_ir}` absent",
                     f"compiler : python .sdda/sdda.py ir-compiler --mission {number}", rel_ir)
        return False
    expected = expected_views(ir_path)
    fresh = True
    for path, text in sorted(expected.items()):
        rel = paths.rel(root, path)
        if not path.is_file():
            fresh = False
            report.error("IR_VIEW_MISSING", f"MISSION {number} : vue `{rel}` absente",
                         f"python .sdda/sdda.py ir-view --mission {number} --write", rel)
        elif path.read_text(encoding="utf-8") != text:
            fresh = False
            report.error("IR_VIEW_STALE", f"MISSION {number} : vue `{rel}` ne dérive plus de l'IR courante",
                         f"python .sdda/sdda.py ir-view --mission {number} --write (jamais d'édition à la main)", rel)
    for orphan in _stale_files(ir_path, expected):
        report.warn("IR_VIEW_STALE", f"MISSION {number} : vue orpheline `{paths.rel(root, orphan)}` (agent retiré de l'IR)",
                    f"python .sdda/sdda.py ir-view --mission {number} --write la supprime", paths.rel(root, orphan))
    report.data["views"] = {paths.rel(root, p): len(t.encode("utf-8")) for p, t in sorted(expected.items())}
    report.data["irBytes"] = ir_path.stat().st_size
    return fresh


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Vues d'IR par agent de construction : écrire, vérifier (0 token)")
    p.add_argument("--mission", type=int, required=True, help="numéro de MISSION")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="(ré)écrire toutes les vues de la MISSION")
    mode.add_argument("--check", action="store_true", help="vérifier que les vues dérivent de l'IR courante (défaut)")
    p.add_argument("--agent", default=None, help="n'afficher que la vue de cet agent de construction")
    p.add_argument("--instance", default=None, help="avec --agent dev-agent : l'agent du produit")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="IR-VIEW", target=str(args.mission))
    ir_path = paths.ir_path(root, args.mission)

    if args.agent:
        if args.agent not in ir_views.view_agents():
            report.error("INVALID_ARG", f"`{args.agent}` n'a pas de vue d'IR",
                         f"agents à vue : {', '.join(ir_views.view_agents())} — les autres lisent l'IR complète")
            return finish(report, args)
        if (args.agent == ir_views.INSTANCE_VIEW_AGENT) != bool(args.instance):
            report.error("INVALID_ARG", "`--instance` accompagne `--agent dev-agent`, et lui seul",
                         "préciser l'agent du produit : --instance {agents[].id}")
            return finish(report, args)
        path = ir_views.view_path(ir_path, args.agent, args.instance)
        report.data["path"] = paths.rel(root, path)
        if not path.is_file():
            report.error("IR_VIEW_MISSING", f"vue `{paths.rel(root, path)}` absente",
                         f"python .sdda/sdda.py ir-view --mission {args.mission} --write", paths.rel(root, path))
        else:
            report.data["bytes"] = path.stat().st_size
            if not args.json:
                print(f"{paths.rel(root, path)} — {path.stat().st_size} octets (IR complète : {ir_path.stat().st_size})")
        return finish(report, args)

    if args.write:
        if not ir_path.is_file():
            report.error("IR_NOT_FOUND", f"IR `{paths.rel(root, ir_path)}` absent",
                         f"compiler : python .sdda/sdda.py ir-compiler --mission {args.mission}", paths.rel(root, ir_path))
            return finish(report, args)
        written = write_views(root, args.mission, report)
        if not args.json:
            total = sum(report.data["views"].values())
            print(f"🟢 MISSION {args.mission} — {len(written)} vue(s) d'IR, {total} octets "
                  f"(IR complète : {ir_path.stat().st_size} octets)")
        return finish(report, args)

    fresh = check_views(root, args.mission, report)
    if not args.json:
        print(f"{'🟢' if fresh else '🔴'} MISSION {args.mission} — vues d'IR {'à jour' if fresh else 'à régénérer'}")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
