"""La console — surface par défaut, et surface des evals. GÉNÉRÉ, ne pas éditer.

`stdin -> run -> stdout`. Pas de serveur, pas de session, pas d'authentification :
c'est la surface qu'on peut faire tourner en PHASE 5 avant qu'une surface réseau
existe, et c'est **celle que le runner d'eval invoque en L5/L7**. Ce qu'on mesure
est donc exactement ce qu'on livre — c'est la raison d'être de `cli-exe` comme
livrable par défaut, dans les quatre langages.

Trois règles, et elles sont toutes vérifiables :

1. **`stdout` est le canal de résultat, `stderr` celui du diagnostic.** En
   `--json`, RIEN d'autre que du NDJSON valide ne sort sur `stdout` : pas de
   log, pas d'avertissement de librairie, pas de barre de progression. Un
   consommateur qui doit filtrer les lignes qui ne parsent pas n'a pas de
   contrat machine, il a une heuristique.
2. **`run_finished` est toujours le dernier événement**, y compris après une
   erreur ou un Ctrl-C : il porte le code effectif et le chemin de la trace,
   c'est-à-dire précisément ce qu'on cherche quand ça s'est mal passé.
3. **Le code de sortie est dérivé de la classe**, jamais choisi ici
   (`exit_codes.py`). Un code décidé dans une commande diverge de celui décidé
   dans la commande d'à côté, et la CI prend la mauvaise branche.

`argparse` et non `typer`
--------------------------
Le squelette doit rester importable et exécutable sans aucune dépendance : c'est
ce qui permet au smoke, au `health` et aux tests L0/L1 de tourner sur un clone
nu, avant `uv sync`. `typer`/`rich` apportent l'aide colorée et la complétion —
`dev-api` les branche par-dessus quand la surface est enrichie, sans toucher aux
codes de sortie ni au protocole d'événements, qui vivent ici.

Un secret n'est JAMAIS un argument : `ps` les voit, et l'aide les affiche.
Une option `--api-key` est `[SEC_SECRET_IN_ARGV]` au lint L0 ; les secrets
passent par `config.py`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from ..config import ConfigError, Settings
from ..run_service import RunRequest, RunService
from .exit_codes import ExitCode, resolve_exit_code


def _out(payload: dict[str, Any]) -> None:
    """Une ligne NDJSON sur `stdout`, vidée immédiatement.

    Le vidage par événement n'est pas du zèle : redirigé vers un fichier ou un
    tube, `stdout` est mis en tampon par blocs — le consommateur ne voit rien
    pendant trente secondes puis tout d'un coup, et un run interrompu ne laisse
    rien du tout.
    """
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def _log(message: str) -> None:
    """Le diagnostic va sur `stderr`, toujours. Voir la règle 1."""
    sys.stderr.write(message.rstrip("\n") + "\n")
    sys.stderr.flush()


def _read_input(args: argparse.Namespace) -> str:
    """L'entrée : option, fichier, ou `stdin`. Toujours en UTF-8.

    Sur un terminal sans `--input`, on refuse (code 2) plutôt que d'attendre
    indéfiniment une entrée que personne ne tape : un run qui pend en CI
    ressemble à un run lent, et on l'attend une heure.
    """
    if args.input is not None:
        return str(args.input)
    if args.input_file is not None:
        return Path(args.input_file).read_text(encoding="utf-8")
    if sys.stdin is None or sys.stdin.isatty():
        raise ConfigError("aucune entrée : passer `--input`, `--input-file`, ou canaliser stdin",
                          cls="CLI_USAGE", fix="`{AppName} run --input \"…\"`")
    return sys.stdin.read()


def _service(settings: Settings | None = None) -> RunService:
    return RunService(settings or Settings.load())


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace, service: RunService | None = None) -> int:
    """Une exécution de la MISSION. La seule commande qui coûte des tokens."""
    try:
        text = _read_input(args)
        service = service or _service()
    except ConfigError as exc:
        _emit_failure(args, exc)
        return int(resolve_exit_code(status="failed", error_class=exc.cls))
    except OSError as exc:
        _emit_failure(args, ConfigError(str(exc), cls="CLI_USAGE"))
        return int(ExitCode.USAGE)

    request = RunRequest(
        input=text, thread_id=args.thread_id or "", tenant_id=args.tenant or "",
        max_budget_usd=args.max_budget_usd, surface="cli")

    try:
        result = service.run_sync(request, on_event=_out if args.json else None)
    except KeyboardInterrupt:
        # Piège n°3 de la fiche : sans ce traitement, `KeyboardInterrupt`
        # interrompt l'export des spans et le run ne laisse rien derrière lui.
        _log("interrompu (SIGINT) — trace vidée")
        if args.json:
            _out({"event": "run_finished", "exit_code": int(ExitCode.SIGINT), "status": "interrupted"})
        return int(ExitCode.SIGINT)

    code = resolve_exit_code(status=result.status, error_class=result.error_class,
                             bound_exceeded=result.bound_exceeded)
    if not args.json:
        # Sans `--json`, seul le résultat va sur stdout ; le reste est du
        # diagnostic. Un humain lit une réponse, pas un flux d'événements.
        if result.output is not None:
            sys.stdout.write(str(result.output).rstrip("\n") + "\n")
        _log(f"run {result.run_id} · {result.status} · ${result.cost_usd:.6f} · "
             f"{result.latency_ms} ms · trace {result.trace_path or '<mémoire>'}")
    for problem in result.problems:
        _log(f"WARN {problem}")
    return int(code)


def cmd_health(args: argparse.Namespace) -> int:
    """Vérifications déterministes, 0 token, aucune connexion.

    C'est la commande du smoke. Elle n'appelle **jamais** un modèle « pour
    vérifier la clé » : ce serait payer à chaque sonde, et échouer sur un quota
    sans que la configuration soit fausse. La vérification de clé est un test
    `network` explicite, pas le défaut.
    """
    checks: list[dict[str, Any]] = []
    ok = True

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        checks.append({"check": name, "ok": passed, "detail": detail, "blocking": True})
        ok = ok and passed

    def note(name: str, observed: bool, detail: str = "") -> None:
        """Un constat qui ne bloque pas.

        Servirait mal de rougir sur l'absence de prompts : leur complétude se
        vérifie contre le ROSTER, que cette commande ne connaît pas — c'est le
        travail de `lint_prompts.py` et de l'AGENT GATE. Un `health` rouge pour
        une raison qu'il n'est pas en mesure de juger est un `health` qu'on
        finit par ignorer, y compris le jour où il a raison.
        """
        checks.append({"check": name, "ok": observed, "detail": detail, "blocking": False})

    try:
        settings = Settings.load()
    except ConfigError as exc:
        check("settings", False, str(exc))
        _report_health(args, checks, ok=False)
        return int(ExitCode.CONFIG)

    check("settings", True, f"app={settings.app_name} provider={settings.provider}")
    for tier in sorted(settings.tier_map):
        try:
            check(f"tier:{tier}", True, settings.model_for(tier))
        except ConfigError as exc:
            check(f"tier:{tier}", False, str(exc))
    check("pricing", bool(settings.pricing),
          f"{len(settings.pricing)} modèle(s) tarifés — sans tarif, un coût recalculé vaut zéro, "
          "et zéro passe sous tous les plafonds")
    # Le dossier des traces est créé à la volée ; ce qui doit exister, c'est la
    # racine du workspace — sans elle, le run écrirait sa trace ailleurs, et
    # l'invariant `trace-emitted-per-run` serait vert sur un fichier introuvable.
    check("workspace", settings.workspace_root.exists() or not settings.trace_enabled,
          f"traces -> {settings.traces_dir()}")
    note("prompts", settings.prompts_dir().exists(), str(settings.prompts_dir()))

    _report_health(args, checks, ok=ok)
    return int(ExitCode.OK if ok else ExitCode.CONFIG)


def cmd_version(args: argparse.Namespace) -> int:
    from .. import __version__  # noqa: PLC0415 - évite un import circulaire au chargement

    try:
        settings = Settings.load()
        payload = {"app": settings.app_name, "skeleton": __version__,
                   "mission": settings.mission_id, "provider": settings.provider,
                   "deliverable": settings.deliverable_type}
    except ConfigError as exc:
        payload = {"skeleton": __version__, "error": str(exc)}
    if args.json:
        _out(payload)
    else:
        sys.stdout.write(" ".join(f"{k}={v}" for k, v in payload.items()) + "\n")
    return int(ExitCode.OK)


def _report_health(args: argparse.Namespace, checks: list[dict[str, Any]], *, ok: bool) -> None:
    if args.json:
        _out({"event": "final", "ok": ok, "checks": checks})
        return
    for entry in checks:
        mark = "ok  " if entry["ok"] else ("FAIL" if entry.get("blocking", True) else "note")
        sys.stdout.write(f"{mark} {entry['check']}"
                         f"{' — ' + entry['detail'] if entry['detail'] else ''}\n")


def _emit_failure(args: argparse.Namespace, exc: ConfigError) -> None:
    if args.json:
        _out({"event": "error", "class": exc.cls, "message": str(exc), "fix": exc.fix})
        _out({"event": "run_finished",
              "exit_code": int(resolve_exit_code(status="failed", error_class=exc.cls))})
    else:
        _log(f"ERROR: {exc}\nCAUSE: [{exc.cls}]\nFIX: {exc.fix}")


# ---------------------------------------------------------------------------
# Entrée
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="{AppName}",
        description="{AppName} — exécution de la MISSION en ligne de commande.",
        epilog=("codes de sortie : 0 succès · 2 usage · 3 borne atteinte · 4 refus · "
                "5 budget · 6 outil · 7 dégradé · 8 configuration · 9 sortie non conforme · "
                "10 interrompu · 130 SIGINT"))
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="une exécution de la MISSION")
    source = run.add_mutually_exclusive_group()
    source.add_argument("--input", default=None, help="l'entrée, en clair")
    source.add_argument("--input-file", default=None, help="l'entrée, depuis un fichier UTF-8")
    run.add_argument("--thread-id", default="", help="conversation existante ; nouveau si absent")
    run.add_argument("--tenant", default="", help="identité de l'appelant (jamais vue du modèle)")
    run.add_argument("--max-budget-usd", type=float, default=None,
                     help="plafond du run ; ne peut que BAISSER celui du contrat")
    run.add_argument("--json", action="store_true", help="NDJSON sur stdout, un événement par ligne")
    run.set_defaults(handler=cmd_run)

    health = subparsers.add_parser("health", help="vérifications déterministes, 0 token")
    health.add_argument("--json", action="store_true")
    health.set_defaults(handler=cmd_health)

    version = subparsers.add_parser("version", help="versions et identité du build")
    version.add_argument("--json", action="store_true")
    version.set_defaults(handler=cmd_version)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Encodage : un jeton contenant « → » ou un emoji plante une console cp1252.
    # Reconfigurer AVANT la première écriture, et ne pas échouer si le flux ne
    # sait pas se reconfigurer (tests, tubes).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if getattr(args, "handler", None) is None:
        parser.print_help(sys.stderr)
        return int(ExitCode.USAGE)
    return int(args.handler(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
