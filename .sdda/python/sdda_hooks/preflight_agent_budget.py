#!/usr/bin/env python3
"""Le contexte d'un agent tient dans son budget — AVANT le spawn.

Un agent qui déborde son `budget_bytes` produit une sortie tronquée et
confiante, ce qui est pire qu'un échec net : personne ne voit qu'il a raisonné
sur la moitié de ses entrées. Le contrôle est volontairement brutal — il ne
part pas.

**Le budget lui-même a un plafond.** Le hook ne mesurait que les `reads:`
déclarés contre le `budget_bytes` déclaré — deux nombres que le même auteur
écrit. `qa-tests` portait 800 Ko, soit ~200 000 tokens à 4 octets par token :
la fenêtre ENTIÈRE du modèle, avant le premier tour, le premier résultat
d'outil, la première ligne écrite. Un budget au-dessus de ce qu'un tier peut
contenir n'est pas un budget, c'est une promesse de troncature. Le plafond se
calcule, il ne se déclare pas :

    plafond(tier) = fenêtre(tier) en tokens × OCTETS_PAR_TOKEN × (1 − RÉSERVE_TOURS)

La réserve (40 %) paie ce que `reads:` ne compte pas : les tours de la boucle,
les sorties d'outils, les fichiers lus en cours de route, la réponse. Un agent
qui a « vraiment besoin de plus » ne relève pas son budget : il découpe son
chargement (`qa-tests` est lancé par couche, `SDDA-LAYER: {couche}`).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, agent_of, deny, run  # noqa: E402

HOOK = "preflight_agent_budget"

#: Câblage — lu par `harness_build.py`. Tout spawn : chaque agent a un budget
#: de contexte dans loader.yml, et c'est avant le spawn qu'il faut le vérifier.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": ()}

#: Fenêtre de contexte par tier, en tokens. Les trois tiers résolvent aujourd'hui
#: vers des modèles à 200 k tokens (`capability-matrix.yml`, `tier_models`) ; un
#: tier servi par un modèle à fenêtre plus courte se déclare ici, plus bas.
WINDOW_TOKENS = {"fast": 200_000, "balanced": 200_000, "deep": 200_000}
#: Octets par token : l'ordre de grandeur des tokenizers sur du code et de la
#: prose FR/EN. Le plafond est un ordre de grandeur, pas une facture.
BYTES_PER_TOKEN = 4
#: Part de la fenêtre laissée aux tours, aux sorties d'outils et à la réponse.
TURN_RESERVE = 0.40

CLS_CEILING = "CONTEXT_BUDGET_CEILING_EXCEEDED"

_MISSION_RE = re.compile(r"\bMISSION\s*:?\s*(\d+)\b")
_INSTANCE_RE = re.compile(r"^\s*SDDA-INSTANCE\s*:\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$", re.M)
_LAYER_RE = re.compile(r"^\s*SDDA-(?:LAYER|OBJECT)\s*:\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$", re.M)


def ceiling_for(tier: str) -> int:
    window = WINDOW_TOKENS.get(str(tier or "balanced"), min(WINDOW_TOKENS.values()))
    return int(window * BYTES_PER_TOKEN * (1 - TURN_RESERVE))


def _mission_and_target(data: dict) -> tuple[str | None, str | None]:
    """La MISSION et la cible du spawn — du payload s'il les porte, sinon du prompt.

    Le harnais ne transmet que `tool_input.prompt` : il n'y a jamais de clé
    `mission`. Sans elle, chaque `{n}` de `loader.yml` s'élargissait à TOUTES
    les missions et le hook mesurait un contexte que l'agent ne lira jamais —
    il refusait tous les agents de construction au premier projet réel. Le
    brief assemblé par `spawn-brief` écrit toujours `MISSION : {n}` ; à défaut,
    `SDDA_MISSION` dans l'environnement. La cible (`{agent}`) est l'instance
    déclarée par `SDDA-INSTANCE:` quand le payload ne la porte pas.
    """
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    prompt = str(tool_input.get("prompt") or "")
    mission = data.get("mission") or tool_input.get("mission")
    if not mission:
        m = _MISSION_RE.search(prompt)
        mission = m.group(1) if m else os.environ.get("SDDA_MISSION") or None
    target = data.get("target") or tool_input.get("target")
    if not target:
        m = _INSTANCE_RE.search(prompt)
        target = m.group(1) if m else None
    return (str(mission) if mission else None), target


def _object_of(data: dict) -> str | None:
    """`{object}` des `reads:` — la couche d'un agent lancé par couche (`SDDA-LAYER:`)."""
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    m = _LAYER_RE.search(str(tool_input.get("prompt") or ""))
    return m.group(1) if m else (tool_input.get("object") or None)


def check(root: Path, data: dict) -> int:
    agent = agent_of(data)
    if not agent:
        return ALLOW

    from sdda_lib.errors import Report  # noqa: E402
    from sdda_scripts import context_pack  # noqa: E402

    loader = context_pack.load_loader(root)
    spec = loader.get(agent)
    if not isinstance(spec, dict):
        return ALLOW

    tier = str(spec.get("model_tier") or "balanced")
    declared = int(spec.get("budget_bytes") or 0)
    ceiling = ceiling_for(tier)
    if declared > ceiling:
        return deny(HOOK, CLS_CEILING,
                    f"`{agent}` déclare budget_bytes {declared} > plafond {ceiling} du tier `{tier}` "
                    f"({WINDOW_TOKENS.get(tier, '?')} tokens × {BYTES_PER_TOKEN} o × {1 - TURN_RESERVE:.0%})",
                    "découper le chargement (un spawn par couche ou par module, `SDDA-LAYER:`) plutôt que "
                    "relever le budget : au-delà du plafond, la fenêtre n'a plus la place des tours")

    report = Report(name="CONTEXT-HOOK", target=str(root))
    mission, target = _mission_and_target(data)
    resolution = context_pack.resolve_context(root, loader, agent, report=report,
                                              mission=mission, target=target, obj=_object_of(data))
    if resolution is None:
        return ALLOW
    budget = int(resolution.budget_bytes or 0)
    total = resolution.total_bytes
    if budget and total > budget:
        return deny(HOOK, "CONTEXT_BUDGET_EXCEEDED",
                    f"`{agent}` : {total} octets de contexte pour un budget de {budget}",
                    "réduire les `reads:` de cet agent dans loader.yml, reconstruire son pack "
                    "(`context_pack.py build`), découper le spawn (`SDDA-LAYER:`), ou relever son budget "
                    "— explicitement, et sous le plafond du tier")
    return ALLOW


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
