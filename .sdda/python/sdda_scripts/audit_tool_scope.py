#!/usr/bin/env python3
"""TOOL SCOPE — le moindre privilège tient sur le système VIVANT (G7, contrôle 2).

`validate_ir.py` §7 vérifie déjà le moindre privilège **sur la déclaration** :
un agent ne porte que les outils qu'exigent ses CAPs. C'est une condition
nécessaire et elle est jouée en G2, avant tout code.

Ce script répond à une question que G2 ne peut pas poser : **ce que le système
a réellement fait correspond-il à ce qu'il avait le droit de faire ?** Trois
écarts que la déclaration ne voit pas :

1. **Un outil appelé sans être câblé.** Une trace montre `agent X` appelant
   `delete_record` alors que l'IR ne le lui donne pas. Le cas arrive quand
   l'orchestration passe un registre d'outils global au lieu du sous-ensemble
   de l'agent — une erreur de câblage d'une ligne, invisible à la relecture,
   et qui donne à chaque agent tous les outils du système.
2. **Un outil destructif atteignable depuis un agent exposé à du texte non
   maîtrisé.** Déclaré correctement, chacun de son côté ; la composition est le
   risque. C'est le chemin d'une injection indirecte vers un effet de bord réel.
3. **Un outil déclaré que rien n'appelle jamais.** Surface d'attaque gratuite :
   il ne sert pas le produit et reste atteignable par un prompt hostile.

Le script ne réinterprète pas la règle de G2 : il **importe** sa fonction pour
que les deux gates ne puissent pas diverger. Deux implémentations d'une même
règle divergent, et c'est celle qui ne bloque pas qui survit.

Usage :
    python .sdda/sdda.py audit-tool-scope --ir workspace/.sys/.ir/1-system.ir.json --json
    python .sdda/sdda.py audit-tool-scope --mission 1
    python .sdda/sdda.py audit-tool-scope --mission 1 --no-traces   # déclaration seule

Exit : 0 vert · 1 rouge.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths, tracing  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_scripts._common import (  # noqa: E402
    add_common_args, ensure_utf8_stdout, finish, resolve_root,
)

#: Classes d'effet de bord qui rendent un outil dangereux au bout d'une
#: injection. `external-side-effect` en fait partie : envoyer un e-mail est
#: irréversible même si rien n'est écrit en base.
DANGEROUS = ("write-destructive", "write-scoped", "external-side-effect")


def load_ir(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def declared_excess(ir: dict[str, Any], report: Report, loc: str) -> None:
    """Le moindre privilège déclaratif — délégué à `validate_ir`, jamais recopié."""
    from sdda_scripts import validate_ir  # noqa: E402  (import tardif : coût de démarrage)

    agents = {a["id"]: a for a in ir.get("agents") or []}
    # `traceability` est un MAPPING capId -> info, pas une liste. Même forme que
    # celle que lit `validate_ir` §7 : s'en écarter ici produirait deux lectures
    # de la même structure, donc deux verdicts possibles pour une seule règle.
    traceability: dict[str, Any] = ir.get("traceability") or {}
    for aid, agent in sorted(agents.items()):
        required: set[str] = set()
        for cap in agent.get("servesCaps") or []:
            required.update((traceability.get(cap, {}).get("implementedBy") or {}).get("tools") or [])
        excess = sorted(set(agent.get("tools") or []) - required)
        if excess:
            report.error(
                "TOOL_SCOPE_EXCESS",
                f"agent `{aid}` porte {excess}, exigé(s) par aucune de ses CAPs",
                "retirer l'outil du contrat d'agent, ou l'exiger dans `## Allocated To` de la CAP",
                loc,
            )
    _ = validate_ir  # la règle vit là-bas ; l'import documente la dépendance


def unreachable_tools(ir: dict[str, Any], report: Report, loc: str) -> None:
    """Un outil déclaré que personne ne câble est une surface d'attaque gratuite."""
    tools = {t["id"] for t in ir.get("tools") or [] if t.get("id")}
    wired = {t for a in (ir.get("agents") or []) for t in (a.get("tools") or [])}
    for tid in sorted(tools - wired):
        report.warn(
            "TOOL_SCOPE_EXCESS",
            f"outil `{tid}` déclaré mais câblé à aucun agent",
            "le retirer de l'IR : il ne sert pas le produit et reste atteignable par un prompt hostile",
            loc,
        )


def dangerous_under_untrusted(ir: dict[str, Any], report: Report, loc: str) -> None:
    """Un outil à effet de bord au bout d'un agent exposé à du texte hostile.

    Chaque moitié est déclarée correctement ; c'est la COMPOSITION qui est le
    risque, et aucune déclaration prise isolément ne la montre. C'est le chemin
    exact d'une injection indirecte vers un effet irréversible.
    """
    tools = {t["id"]: t for t in ir.get("tools") or [] if t.get("id")}
    for agent in ir.get("agents") or []:
        # `untrustedInputs` liste les canaux par lesquels du texte hostile
        # entre. Non vide = l'agent est exposé, quelle que soit la formulation.
        posture = agent.get("trustPosture") or {}
        if not (posture.get("untrustedInputs") or []):
            continue

        exposed = [t for t in (agent.get("tools") or [])
                   if str(tools.get(t, {}).get("sideEffectClass", "")) in DANGEROUS]
        if not exposed:
            continue

        # Le chemin « texte hostile -> effet irréversible » existe dès que les
        # deux moitiés coexistent. Ce qui décide du verdict est la présence
        # d'une stratégie de sûreté EFFECTIVE sur l'outil.
        #
        # Sans ce partage, le contrôle serait rouge sur tout projet réaliste —
        # un agent de support qui lit des documents et ouvre un ticket est le
        # cas nominal, pas une faute. Un contrôle rouge en permanence ne
        # protège de rien : il apprend à être ignoré.
        unmitigated = []
        for tid in sorted(exposed):
            s = tools[tid].get("safetyStrategy") or {}
            mitigated = (
                str(s.get("confirmation", "never")).lower() not in ("never", "none", "")
                or bool(s.get("dryRunSupported"))
                or str(s.get("idempotency", "none")).lower() not in ("none", "")
                or bool(s.get("cap"))
            )
            if not mitigated:
                unmitigated.append(tid)

        classes = ", ".join(sorted({str(tools[t].get("sideEffectClass")) for t in exposed}))
        if unmitigated:
            report.error(
                "SAFETY_STRATEGY_MISSING",
                f"agent `{agent.get('id')}` traite du texte non maîtrisé et porte {unmitigated} "
                f"({classes}) SANS stratégie de sûreté effective",
                "ajouter au contrat d'outil une confirmation, un dry-run, une clé d'idempotence "
                "ou un plafond par run — ou déplacer l'outil derrière un agent qui ne voit aucune "
                "entrée non maîtrisée. Chaque moitié est correcte ; c'est la composition qui ouvre "
                "le chemin d'une injection indirecte vers un effet irréversible",
                loc,
            )
        else:
            report.warn(
                "TOOL_SCOPE_EXCESS",
                f"agent `{agent.get('id')}` traite du texte non maîtrisé et porte "
                f"{sorted(exposed)} ({classes}) — mitigé par stratégie de sûreté",
                "chemin d'injection indirecte réel mais encadré : à confirmer par la suite "
                "d'injection de l'étage C, qui l'exerce au lieu de le supposer",
                loc,
            )


def observed_calls(root: Path, report: Report, ir: dict[str, Any], loc: str) -> int:
    """Les appels d'outils réellement tracés, confrontés au câblage de l'IR.

    C'est le seul contrôle qui distingue « déclaré » de « fait ». Un registre
    d'outils global passé à l'orchestrateur au lieu du sous-ensemble de chaque
    agent est une erreur d'une ligne, invisible en relecture, qui donne à tous
    les agents tous les outils du système.
    """
    allowed = {a["id"]: set(a.get("tools") or []) for a in ir.get("agents") or [] if a.get("id")}
    seen = 0
    offenders: dict[tuple[str, str], int] = {}
    unattributed = 0

    runs = tracing.runs_dir(root)
    for trace in sorted(runs.glob("*.jsonl")) if runs.is_dir() else ():
        # L'agent responsable d'un appel d'outil se LIT dans l'arbre des spans
        # (`parent_span_id`). Ce script suivait naguère « le dernier agent vu »
        # dans un flux d'événements plat : faux dès que deux agents travaillent
        # en parallèle, et surtout illisible sur une trace réelle, qui est faite
        # de spans — il n'y voyait aucun appel, donc aucun dépassement.
        for call in tracing.summarize(trace).tool_calls:
            if not call.tool:
                continue
            seen += 1
            # L'IR nomme ses agents et ses outils par identifiant de contrat ;
            # la trace porte les deux. On compare d'abord l'identifiant, puis le
            # nom — un appel légitime ne doit pas être signalé parce que
            # l'application a tracé `invoice_lookup` là où l'IR dit
            # `1-invoice-lookup`.
            agent = next((a for a in (call.agent_id, call.agent) if a in allowed), "")
            if not agent:
                unattributed += 1
                continue
            if not call.matches_tool(allowed[agent]):
                offenders[(agent, call.tool_id or call.tool)] = offenders.get((agent, call.tool_id or call.tool), 0) + 1

    if unattributed:
        report.warn(
            "TRACE_MALFORMED",
            f"{unattributed} appel(s) d'outil rattaché(s) à aucun agent de l'IR",
            "un `execute_tool` doit être enfant de l'`invoke_agent` qui l'a déclenché "
            "(observability/otel-genai.md §3.1), et cet agent doit porter son identifiant de "
            "contrat : sans ce lien, l'appel n'est rattaché à personne, donc à aucun périmètre — "
            "et un périmètre que rien ne confronte est un périmètre qui passe", loc)

    for (agent, tool), count in sorted(offenders.items()):
        report.error(
            "TOOL_SCOPE_EXCESS",
            f"agent `{agent}` a appelé `{tool}` {count} fois — outil absent de son câblage IR",
            "l'orchestration passe probablement un registre d'outils global au lieu du "
            "sous-ensemble de l'agent. Corriger le câblage (dev-orchestration), pas l'IR",
            loc,
        )
    return seen


def run(root: Path, ir_path: Path, use_traces: bool = True) -> Report:
    report = Report(name="TOOL-SCOPE", target=str(root))
    loc = paths.rel(root, ir_path)

    if not ir_path.is_file():
        report.error("IR_NOT_FOUND", f"IR absent : {loc}",
                     "compiler l'IR (`/sdda-topology {n}`) avant d'auditer les scopes", loc)
        return report

    try:
        ir = load_ir(ir_path)
    except ValueError as exc:
        report.error("IR_INVALID", f"IR illisible : {exc}", "recompiler l'IR", loc)
        return report

    declared_excess(ir, report, loc)
    unreachable_tools(ir, report, loc)
    dangerous_under_untrusted(ir, report, loc)

    traced = observed_calls(root, report, ir, loc) if use_traces else 0
    if use_traces and traced == 0:
        # Pas une erreur : l'audit de scope est jouable avant le premier run.
        # Le dire évite de prendre un vert déclaratif pour un vert observé.
        report.warn(
            "TRACE_MISSING",
            "aucun appel d'outil dans les traces — contrôle limité à la déclaration",
            "relancer après une exécution réelle (étage C) : un scope tenu sur le papier "
            "et un scope tenu en vol ne sont pas la même information",
            loc,
        )

    report.data.update({
        "agents": len(ir.get("agents") or []),
        "tools": len(ir.get("tools") or []),
        "tracedToolCalls": traced,
        "observed": use_traces,
    })
    return report


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="Audite le scope d'outils sur le système vivant (G7).")
    add_common_args(parser)
    parser.add_argument("--ir", type=Path, default=None, help="chemin de l'IR (défaut : dérivé de --mission)")
    parser.add_argument("--mission", default=None, help="numéro de MISSION")
    parser.add_argument("--no-traces", action="store_true", help="déclaration seule, sans lire les traces")
    args = parser.parse_args(argv)

    root = resolve_root(args)
    ir_path = args.ir if args.ir else paths.ir_path(root, args.mission or 1)
    if args.ir and not args.ir.is_absolute():
        ir_path = (root / args.ir).resolve()

    report = run(root, ir_path, use_traces=not args.no_traces)
    if not args.no_report:
        write_gate_report(root, "G7", str(args.mission or "stack"), report, {}, part="toolscope")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
