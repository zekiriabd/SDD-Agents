#!/usr/bin/env python3
"""Un run laisse une trace exploitable — invariant `trace-emitted-per-run`.

Sans trace — tours d'agent, appels d'outils, documents retournés, tokens, coût,
latence — un post-mortem est impossible et une dérive de qualité est invisible.
Ce hook ne juge pas le contenu du run : il vérifie qu'il en reste quelque chose.

Trois refus, par ordre de gravité décroissante :
  aucune trace                -> le run n'a pas eu lieu, pour la gate
  trace présente mais muette  -> ni `run_start` ni `run_end` : on ne peut ni
                                 mesurer la latence, ni affirmer que le run a fini
  événements mal formés       -> un champ absent rend la mesure impossible plus tard,
                                 quand plus personne ne saura le reconstituer
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, EVAL_BUILDERS, allow, deny, run  # noqa: E402

HOOK = "postflight_trace_present"

#: Câblage — lu par `harness_build.py`. Après les agents qui produisent ou
#: consomment des runs : ce sont les seuls dont la sortie doit avoir laissé une
#: trace. Exiger une trace d'un architecte qui n'a rien exécuté serait un refus
#: sans objet.
WIRING = {"event": "SubagentStop", "matcher": "*", "applies_to": EVAL_BUILDERS}


def check(root: Path, data: dict) -> int:
    from sdda_lib import tracing  # noqa: E402

    run_id = str(data.get("runId") or data.get("run_id") or "").strip()
    if run_id:
        summaries = [tracing.summarize(tracing.trace_path(root, run_id))]
        if summaries[0].events == 0:
            return deny(HOOK, "TRACE_MISSING", f"run `{run_id}` : aucune trace sur disque",
                        "câbler la stack d'observabilité (`observability/*.md`) : un run sans trace "
                        "n'est pas débogable, et son coût n'est pas mesurable (P6)")
    else:
        summaries = tracing.summarize_all(root)
        if not summaries:
            # Aucun run enregistré : normal avant la première exécution. Le
            # contrôle bloquant est joué en G6, sur un run qui a réellement eu lieu.
            return allow("aucune trace — normal avant la première exécution")

    broken = [s for s in summaries if s.problems]
    if not broken:
        return ALLOW

    worst = broken[0]
    return deny(HOOK, "TRACE_MALFORMED",
                f"{len(broken)} trace(s) inexploitable(s) — `{worst.run_id}` : {worst.problems[0]}",
                "un champ absent rend la mesure impossible plus tard, quand plus personne ne saura "
                "le reconstituer. Format : .sdda/python/sdda_lib/tracing.py")


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
