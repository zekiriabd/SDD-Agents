#!/usr/bin/env python3
"""Anti-cumul de bypasses — hérité de SDD_Pro, joué AVANT tout coût LLM.

Un pipeline qu'on force deux fois n'est plus un pipeline : c'est une exécution
manuelle avec un rapport qui prétend le contraire. Chaque bypass pris isolément
est défendable — une variance élevée qu'on assume, un budget estimé qu'on
dépasse sciemment. Leur **cumul** ne l'est pas, parce que plus personne ne sait
ce qui a réellement été mesuré.

Trois contrôles, dans cet ordre de gravité :

1. **Raison obligatoire.** Toute env var `SDDA_BYPASS_*` posée sans
   `SDDA_BYPASS_REASON` est refusée. Un bypass anonyme est indistinguable d'un
   accident de shell six mois plus tard.
2. **Cumul refusé.** `--force` + au moins un bypass, ou deux bypasses,
   sur le même run → refus, sauf `SDDA_ALLOW_FORCE=1`, lui-même tracé.
3. **Journalisation.** Tout ce qui passe est écrit dans
   `workspace/.sys/.audit/bypasses.jsonl` — horodatage, opérateur, commande,
   classes court-circuitées, raison.

Le script écrit l'audit **même quand il autorise** : un bypass légitime qui ne
laisse pas de trace produit exactement le même dossier qu'un bypass dissimulé.

Usage :
    python .sdda/python/sdda_scripts/preflight_force_cumul.py \\
        --force --no-review --env-bypasses "SDDA_BYPASS_TOOL_GATE=1,"

Exit : 0 autorise · 1 refuse (ERROR sur stdout, format CAUSE/FIX).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths  # noqa: E402
from sdda_lib.runtime_io import ensure_utf8_stdout, now_iso  # noqa: E402

ensure_utf8_stdout()

#: Les bypasses reconnus, et la gate que chacun desserre. Liste **close** : une
#: env var `SDDA_BYPASS_*` inconnue est refusée plutôt qu'ignorée — sinon une
#: faute de frappe produit un pipeline qu'on croit forcé et qui ne l'est pas,
#: ou l'inverse.
KNOWN_BYPASSES = {
    "SDDA_BYPASS_BUDGET_ESTIMATE": "G2 TOPOLOGY — budget estimé",
    "SDDA_BYPASS_TOOL_GATE": "G3 TOOL — tests de contrat et connectivité read-only",
    "SDDA_BYPASS_RETRIEVAL_GATE": "G4 RETRIEVAL — seuils recall / nDCG / groundedness",
}

#: Les gates sans bypass possible, rappelées dans le message de refus : elles
#: sont la raison pour laquelle desserrer les autres reste tolérable.
NO_BYPASS_GATES = ("G0 MISSION", "G1 CAP", "G5 AGENT", "G6 ORCH", "G8 ACCEPTANCE")

BYPASS_ENV_RE = re.compile(r"^(SDDA_BYPASS_[A-Z0-9_]+)=1$")

#: Les classes émises. Déclarées en constantes `CLS_*` plutôt qu'en littéraux :
#: c'est la forme que `sync_error_registry.py` reconnaît, donc celle qui garantit
#: qu'elles entrent au registre canonique au lieu d'y manquer en silence.
CLS_UNKNOWN = "BYPASS_UNKNOWN"
CLS_REASON_MISSING = "BYPASS_REASON_MISSING"
CLS_CUMUL = "FORCE_CUMUL_REJECTED"


def parse_env_bypasses(raw: str | None) -> list[str]:
    """Les noms de bypasses depuis `--env-bypasses` **et** l'environnement réel.

    La commande passe ce qu'elle a vu ; on relit aussi `os.environ` parce qu'un
    bypass posé après la construction de la ligne de commande compterait sinon
    pour rien. L'union est la lecture sûre : elle ne peut que refuser plus.
    """
    found: set[str] = set()
    for token in (raw or "").replace(";", ",").split(","):
        match = BYPASS_ENV_RE.match(token.strip())
        if match:
            found.add(match.group(1))
    for name, value in os.environ.items():
        if name.startswith("SDDA_BYPASS_") and name != "SDDA_BYPASS_REASON":
            if str(value).strip().lower() in ("1", "true", "yes", "on"):
                found.add(name)
    return sorted(found)


def audit_line(root: Path, record: dict) -> None:
    """Une ligne JSONL dans `.sys/.audit/bypasses.jsonl`, en append atomique.

    Un échec d'écriture n'annule pas le verdict : refuser un run parce que le
    journal est en lecture seule transformerait un dispositif de traçabilité en
    point de panne. Il est dit sur stderr, et le run continue.
    """
    try:
        audit_dir = paths.audit_dir(root)
        audit_dir.mkdir(parents=True, exist_ok=True)
        with (audit_dir / "bypasses.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        sys.stderr.write(f"[audit] journal non écrit ({exc}) — le verdict reste valide\n")


def error(cls: str, cause: str, fix: str) -> int:
    print("ERROR: preflight_force_cumul — cumul de contournements refusé")
    print(f"CAUSE: [{cls}] {cause}")
    print(f"FIX: {fix}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="HARD-GATE anti-cumul de bypasses.")
    parser.add_argument("--force", action="store_true", help="les gates jaunes continuent")
    parser.add_argument("--no-review", action="store_true", help="PHASE 7 sautée (dev local)")
    parser.add_argument("--env-bypasses", default="", help="`SDDA_BYPASS_X=1,…` vus par la commande")
    parser.add_argument("--command", default="/sdda-full", help="commande appelante, pour l'audit")
    parser.add_argument("--mission", default="", help="numéro de MISSION, pour l'audit")
    args = parser.parse_args()

    root = paths.find_root()
    env_bypasses = parse_env_bypasses(args.env_bypasses)
    reason = (os.environ.get("SDDA_BYPASS_REASON") or "").strip()
    operator = (os.environ.get("SDDA_USER_EMAIL") or os.environ.get("USER")
                or os.environ.get("USERNAME") or "inconnu")
    allow_force = os.environ.get("SDDA_ALLOW_FORCE", "0").strip().lower() in ("1", "true", "yes", "on")

    levers = list(env_bypasses) + (["--force"] if args.force else []) + \
        (["--no-review"] if args.no_review else [])

    record = {
        "ts": now_iso(),   # suffixe `Z` comme les autres journaux : bypasses_of les trie par chaîne
        "operator": operator,
        "command": args.command,
        "mission": args.mission or None,
        "runId": os.environ.get("SDDA_RUN_ID"),
        "levers": levers,
        "reason": reason or None,
        "allowForce": allow_force,
    }

    # -- 0. Rien de posé : le cas nominal, et il ne s'audite pas -------------
    if not levers:
        print("ok — aucun contournement posé")
        return 0

    # -- 1. Un bypass inconnu est une faute de frappe, pas une intention -----
    unknown = [b for b in env_bypasses if b not in KNOWN_BYPASSES]
    if unknown:
        record["verdict"] = "refused"
        audit_line(root, record)
        return error(
            CLS_UNKNOWN,
            f"{', '.join(unknown)} — aucune gate ne lit cette variable",
            "vérifier l'orthographe. Bypasses reconnus : " + ", ".join(sorted(KNOWN_BYPASSES))
            + f". Sans bypass possible : {', '.join(NO_BYPASS_GATES)}",
        )

    # -- 2. Une raison est obligatoire pour toute env var -------------------
    # `--force` seul n'en exige pas : il est déjà nominatif (il figure dans la
    # ligne de commande, donc dans l'historique du shell et dans l'audit), là où
    # une env var survit d'un run à l'autre sans que personne ne la revoie.
    if env_bypasses and not reason:
        record["verdict"] = "refused"
        audit_line(root, record)
        return error(
            CLS_REASON_MISSING,
            f"{len(env_bypasses)} env var(s) posée(s) ({', '.join(env_bypasses)}) sans SDDA_BYPASS_REASON",
            "poser `SDDA_BYPASS_REASON=\"<pourquoi, en une phrase>\"`. "
            "Un contournement anonyme est indistinguable d'un accident de shell six mois plus tard",
        )

    # -- 3. Le cumul ---------------------------------------------------------
    # `--no-review` ne compte pas dans le cumul : il est déjà refusé hors dev
    # local par la commande, et le cumuler ici le rendrait impossible à utiliser
    # là où il est légitime.
    cumul = list(env_bypasses) + (["--force"] if args.force else [])
    if len(cumul) >= 2 and not allow_force:
        record["verdict"] = "refused"
        audit_line(root, record)
        return error(
            CLS_CUMUL,
            f"{len(cumul)} contournements sur le même run : {', '.join(cumul)}",
            "n'en garder qu'un, corriger ce que l'autre masque, ou assumer explicitement "
            "avec `SDDA_ALLOW_FORCE=1` — lui-même tracé. Un pipeline qu'on force deux fois "
            "n'est plus un pipeline",
        )

    # -- Autorisé : on trace, et on dit ce qui reste hors de portée ---------
    record["verdict"] = "allowed"
    audit_line(root, record)
    print(f"ok — {len(levers)} contournement(s) assumé(s) : {', '.join(levers)}")
    print(f"     raison : {reason or '(non requise — aucune env var posée)'}")
    print(f"     opérateur : {operator} · tracé dans {paths.rel(root, paths.audit_dir(root))}/bypasses.jsonl")
    if allow_force and len(cumul) >= 2:
        print(f"     ⚠  cumul autorisé par SDDA_ALLOW_FORCE — {', '.join(cumul)}")
    print(f"     hors de portée de tout contournement : {', '.join(NO_BYPASS_GATES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
