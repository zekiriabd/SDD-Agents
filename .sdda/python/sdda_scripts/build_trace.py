#!/usr/bin/env python3
"""Ce que la CONSTRUCTION coûte, agent par agent — une trace, pas un ressenti (0 token).

ARCHITECTURE §8 promet une trace pour « tout run, de construction comme
d'exécution du produit ». Celle du produit existait ; celle de la construction
n'existait pas. On savait donc mesurer ce que l'application coûte à l'usage, et
pas ce que le framework coûte à la fabriquer — alors que c'est la facture que
l'utilisateur voit en premier, et la seule que `MaxCostPerRun` prétend plafonner.

Ce script écrit des spans dans la trace du run courant
(`workspace/.sys/traces/runs/{run_id}.jsonl`), au MÊME format que l'application :

    sdda.build.agent {agent}    une invocation de Developer Agent
    sdda.gate {gate}            un franchissement de gate

Le span racine `sdda.run` est écrit par `sdda_state end-run`, qui seul connaît
le début, la fin et le cumul du run. Un span enfant peut donc être écrit avant
son parent : `summarize` lit le fichier entier, l'ordre d'écriture n'a pas de
sens dans un exportateur de spans.

Quatre grandeurs, et pourquoi celles-là :

    cost-usd          ce que le harnais a facturé pour cet agent. DÉCLARÉ, pas
                      recalculé : nous ne voyons pas les tokens d'un sous-agent.
                      Il reste donc séparé du coût du produit, qui vient des
                      tokens — les additionner ferait passer un chiffre
                      invérifiable pour une mesure. Ce même chiffre alimente le
                      cumul du run (`sdda_state add-cost`), seul nombre que
                      `preflight_cost_cap` sait lire : un span qui n'alimente
                      aucun cumul laisse `MaxCostPerRun` autoriser à l'infini.
    duration-ms       la latence réelle, celle qu'on attend devant son terminal.
    iterations        les tours de `build_loop` consommés vs `BuildLoopMaxIter` :
                      un agent qui boucle trois fois pour un résultat que le
                      premier tour donnait est une dérive qui ne se voit pas
                      autrement.
    budget-bytes-used le contexte réellement chargé vs le `budget_bytes` de
                      `loader.yml`. Au-delà, la sortie n'est pas plus courte :
                      elle est tronquée et confiante.

Usage :
    python .sdda/sdda.py build-trace agent --agent dev-agent --item billing \\
        --phase build_agents --tier deep --cost-usd 0.42 --duration-ms 61000 \\
        --iterations 2 --budget-bytes 180000 --budget-bytes-used 142000
    python .sdda/sdda.py build-trace gate --gate G5 --verdict green
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths, tracing  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.runtime_io import now_iso  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

#: L'identifiant du span racine, écrit par `sdda_state end-run`. Fixe parce
#: qu'un fichier de trace vaut pour UN run : pas besoin de le transmettre entre
#: deux processus qui ne se connaissent pas.
ROOT_SPAN_ID = "root"

VERDICTS = ("green", "yellow", "red")


def resolve_run_id(explicit: str | None) -> str:
    return (explicit or os.environ.get("SDDA_RUN_ID") or "").strip()


def _window(duration_ms: int | None) -> tuple[str, str]:
    """(début, fin) d'un span déjà terminé. La fin est maintenant ; le début s'en déduit."""
    end = now_iso()
    if not duration_ms:
        return end, end
    try:
        import datetime as dt

        stamp = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
        start = (stamp - dt.timedelta(milliseconds=duration_ms)).isoformat().replace("+00:00", "Z")
    except ValueError:
        start = end
    return start, end


def emit(root: Path, run_id: str, name: str, attributes: dict[str, Any], *,
         duration_ms: int | None = None, status: str = "OK") -> dict[str, Any]:
    start, end = _window(duration_ms)
    writer = tracing.TraceWriter(root, run_id)
    return writer.emit(name, span_id=secrets.token_hex(8), parent_span_id=ROOT_SPAN_ID,
                       attributes={k: v for k, v in attributes.items() if v is not None},
                       status=status, start=start, end=end, duration_ms=duration_ms)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Trace de construction : ce que chaque Developer Agent a coûté (0 token)")
    sub = p.add_subparsers(dest="cmd", required=True)

    ag = sub.add_parser("agent", help="une invocation de Developer Agent")
    ag.add_argument("--agent", required=True, help="fiche invoquée, ex. dev-agent")
    ag.add_argument("--item", default=None, help="cible, ex. l'agents[].id du produit")
    ag.add_argument("--phase", default=None, help="phase du pipeline, ex. build_agents")
    ag.add_argument("--tier", default=None, choices=["fast", "balanced", "deep"])
    ag.add_argument("--cost-usd", type=float, default=None, help="facturé par le harnais (déclaré, non recalculé)")
    ag.add_argument("--duration-ms", type=int, default=None)
    ag.add_argument("--iterations", type=int, default=None, help="tours de build_loop consommés")
    ag.add_argument("--budget-bytes", type=int, default=None, help="budget de contexte (loader.yml)")
    ag.add_argument("--budget-bytes-used", type=int, default=None, help="contexte réellement chargé")
    ag.add_argument("--status", default="OK", choices=["OK", "ERROR"])
    ag.add_argument("--run-id", default=None, help="défaut : $SDDA_RUN_ID")
    add_common_args(ag)

    gt = sub.add_parser("gate", help="un franchissement de gate")
    gt.add_argument("--gate", required=True, help="G0 … G8")
    gt.add_argument("--verdict", required=True, choices=list(VERDICTS))
    gt.add_argument("--error-class", default=None, help="classe portée si le verdict est rouge")
    gt.add_argument("--duration-ms", type=int, default=None)
    gt.add_argument("--run-id", default=None, help="défaut : $SDDA_RUN_ID")
    add_common_args(gt)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="BUILD-TRACE", target=str(root))

    run_id = resolve_run_id(args.run_id)
    if not run_id:
        report.error("STATE_RUN_NOT_FOUND", "aucun run courant : le span ne serait rattaché à rien",
                     "exporter SDDA_RUN_ID depuis `sdda_state new-run`, ou passer --run-id",
                     paths.rel(root, tracing.runs_dir(root)))
        return finish(report, args)

    if args.cmd == "agent":
        span = emit(root, run_id, f"sdda.build.agent {args.agent}", {
            tracing.A_BUILD_AGENT: args.agent,
            tracing.A_BUILD_ITEM: args.item,
            tracing.A_BUILD_PHASE: args.phase,
            tracing.A_BUILD_TIER: args.tier,
            tracing.A_COST_DECLARED: args.cost_usd,
            tracing.A_BUILD_ITERATIONS: args.iterations,
            tracing.A_BUDGET_BYTES: args.budget_bytes,
            tracing.A_BUDGET_BYTES_USED: args.budget_bytes_used,
        }, duration_ms=args.duration_ms, status=args.status)
        # Le coût déclaré alimente AUSSI le cumul du run, dans le même appel.
        #
        # `sdda_state.add_cost` existait, `preflight_cost_cap` lisait `costUsd`,
        # et aucune commande n'appelait la primitive : le hook trouvait toujours
        # 0,00 $ et autorisait toujours. `MaxCostPerRun` était donc un plafond
        # décoratif, ce que le docstring d'`add_cost` dit lui-même être pire
        # qu'un plafond absent.
        #
        # Le brancher ici plutôt que d'ajouter une seconde commande à chaque
        # STEP est délibéré : un appel qu'il faut penser à faire est un appel
        # qu'on oublie, et on ne s'en aperçoit qu'en relisant la facture. Le
        # span et le cumul viennent désormais du même chiffre, au même moment.
        if args.cost_usd:
            try:
                from sdda_scripts import sdda_state  # noqa: E402  (import tardif : coût de démarrage)

                # `phase`/`item` rattachent aussi la dépense à la boucle de
                # correction de l'item : c'est ce que BuildLoopMaxCostUsd plafonne.
                run = sdda_state.add_cost(root, run_id, usd=float(args.cost_usd),
                                          label=f"{args.agent}{f'/{args.item}' if args.item else ''}",
                                          phase=args.phase, item=args.item)
                report.data["cumulativeUsd"] = run.get("costUsd")
            except KeyError:
                report.warn("STATE_RUN_NOT_FOUND",
                            f"run `{run_id}` absent de l'état : le span est écrit, le cumul ne l'est pas",
                            "ouvrir le run avec `python .sdda/sdda.py state new-run` avant d'invoquer un agent — "
                            "sans cumul, MaxCostPerRun ne plafonne rien", run_id)

        if args.budget_bytes and args.budget_bytes_used and args.budget_bytes_used > args.budget_bytes:
            report.warn("CONTEXT_BUDGET_EXCEEDED",
                        f"`{args.agent}` : {args.budget_bytes_used} octets chargés pour un budget de {args.budget_bytes}",
                        "un agent qui déborde ne rend pas une sortie plus courte, il rend une sortie "
                        "tronquée et confiante — réduire son pack (context_pack) ou relever le budget "
                        "dans loader.yml, en connaissance de cause", args.agent)
    else:
        span = emit(root, run_id, f"sdda.gate {args.gate}", {
            "sdda.gate.id": args.gate,
            "sdda.gate.verdict": args.verdict,
            "sdda.gate.error_class": args.error_class,
        }, duration_ms=args.duration_ms, status="ERROR" if args.verdict == "red" else "OK")

    report.data.update({"runId": run_id, "span": span["name"], "spanId": span["span_id"],
                        "trace": paths.rel(root, tracing.trace_path(root, run_id))})
    if report.ok and not args.json:
        print(f"  span `{span['name']}` -> {report.data['trace']}")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
