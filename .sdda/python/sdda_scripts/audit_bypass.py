#!/usr/bin/env python3
"""Journalise un bypass posé par une commande — R5, 0 token.

Un bypass est **nominatif, borné et motivé**. Les commandes en posent par
variable d'environnement (`SDDA_ALLOW_LARGE_MISSION=1`, `SDDA_BYPASS_BUDGET_ESTIMATE=1`)
et doivent l'inscrire au journal `workspace/.sys/.audit/bypasses.jsonl`, que
`compute_status` liste et que `sdda_state` rattache au run. Sans cette ligne,
un contournement est indistinguable d'un oubli — et six mois plus tard, d'une
décision.

Ce que ce script REFUSE : une raison absente ou de remplissage
(`non renseignée`, `<à préciser>`, `TODO`) → [BYPASS_REASON_MISSING], exit 1,
rien n'est écrit. Une commande qui pose un bypass sans raison doit STOP : c'est
le même contrat que `preflight_force_cumul`.

Usage :
    python .sdda/sdda.py audit-bypass --command "/sdda-caps 1" --bypass CapGranularityHardCap --reason "..."
    python .sdda/sdda.py audit-bypass --command "/sdda-topology 1" --bypass SDDA_BYPASS_BUDGET_ESTIMATE \\
        --gate G2 --reason "${SDDA_BYPASS_REASON}"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io, paths  # noqa: E402
from sdda_lib.runtime_io import slash_command  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import append_bypass_audit  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

#: Valeurs de remplissage qui ne sont pas une raison.
PLACEHOLDER_REASONS = frozenset({"", "non renseignée", "non renseignee", "todo", "n/a", "na", "-", "…", "..."})

#: Nom de bypass -> gate concernée, quand `--gate` n'est pas donné. Le nom
#: suffit dans les cas connus ; un nom inconnu tombe sur `config`, qui dit
#: « politique de projet » plutôt que d'inventer une gate.
GATE_BY_BYPASS = (
    ("cap", "G1"),
    ("mission", "G0"),
    ("budget", "G2"),
    ("topology", "G2"),
    ("tool", "G3"),
    ("retrieval", "G4"),
    ("agent", "G5"),
    ("orch", "G6"),
    ("safety", "G7"),
    ("combo", "stack"),
    ("stack", "stack"),
)


def gate_for(bypass: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    low = bypass.lower()
    for needle, gate in GATE_BY_BYPASS:
        if needle in low:
            return gate
    return "config"


def is_real_reason(reason: str | None) -> bool:
    text = (reason or "").strip()
    return bool(text) and text.lower() not in PLACEHOLDER_REASONS and not markdown_io.is_placeholder(text)


def run(root: Path, *, command: str, bypass: str, reason: str | None, gate: str | None,
        operator: str | None, write: bool = True) -> Report:
    report = Report(name="BYPASS-AUDIT", target=str(root))
    if not bypass.strip():
        report.error("INVALID_ARG", "`--bypass` vide", "nommer le bypass (variable d'environnement ou clé de config contournée)")
        return report
    if not is_real_reason(reason):
        report.error(
            "BYPASS_REASON_MISSING",
            f"bypass `{bypass}` posé par `{command}` sans raison ({reason!r})",
            fix="poser SDDA_BYPASS_REASON=\"<pourquoi, en une phrase>\" — un bypass anonyme est "
                "indistinguable d'un oubli, et la commande doit STOP tant qu'il n'est pas motivé",
        )
        return report
    resolved_gate = gate_for(bypass, gate)
    report.data.update({"command": command, "bypass": bypass, "gate": resolved_gate, "reason": reason.strip()})
    if write:
        path = append_bypass_audit(root, resolved_gate, reason.strip(), operator,
                                   extra={"command": command, "bypass": bypass})
        report.data["audit"] = paths.rel(root, path)
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Journalise un bypass nominatif et motivé (R5, 0 token)")
    p.add_argument("--command", required=True, help="la commande qui pose le bypass, ex. « /sdda-caps 1 »")
    p.add_argument("--bypass", required=True, help="nom du bypass : variable d'environnement ou clé de config contournée")
    p.add_argument("--reason", default=None, help="pourquoi, en une phrase — obligatoire")
    p.add_argument("--gate", default=None, help="gate concernée (G0..G8, stack, config) ; déduite du nom sinon")
    p.add_argument("--operator", default=None, help="identité pour l'audit (défaut : $USERNAME)")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = run(root, command=slash_command(args.command), bypass=args.bypass, reason=args.reason,
                 gate=args.gate, operator=args.operator, write=not args.no_report)
    if report.ok and not args.json:
        print(f"  bypass `{args.bypass}` ({report.data['gate']}) journalisé -> {report.data.get('audit', '(non écrit)')}")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
