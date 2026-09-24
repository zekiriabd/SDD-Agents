#!/usr/bin/env python3
"""Une instance de `dev-agent` part DÉCLARÉE — ou ne part pas.

`dev-agent` tourne en N instances parallèles, une par agent du produit, et sa
zone d'écriture `agents/{agent}/**` n'a de sens que si l'on sait QUELLE
instance écrit. Au spawn, le hook lit la ligne `SDDA-INSTANCE: {agent}` que
`/sdda-build` écrit dans le prompt, et l'enregistre comme instance déclarée ;
la liaison à l'`agent_id` du sous-agent se fait à sa première écriture (cf.
`_instances`, qui dit aussi la limite du mécanisme).

Seuls les agents qui déclarent `instance_placeholder:` dans `loader.yml` sont
concernés — un `architect-*` ou un reviewer n'a pas d'instances.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, agent_of, deny, run  # noqa: E402

HOOK = "preflight_instance_bind"

#: Câblage — lu par `harness_build.py`. Tout spawn : le hook décide lui-même,
#: depuis `loader.yml`, si l'agent lancé a des instances.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": ()}


def check(root: Path, data: dict) -> int:
    import _instances  # noqa: E402

    agent = agent_of(data)
    if not agent:
        return ALLOW
    from sdda_scripts import audit_ownership as ao  # noqa: E402

    loader = ao.load_loader(root)
    placeholder = _instances.placeholder_of(loader, agent)
    if not placeholder:
        return ALLOW
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    prompt = str(tool_input.get("prompt") or "")
    found = _instances.INSTANCE_LINE_RE.findall(prompt)
    if len(set(found)) != 1:
        return deny(HOOK, _instances.CLS_INSTANCE_UNDECLARED,
                    f"`{agent}` lancé avec {len(set(found))} déclaration(s) d'instance — il en faut exactement une",
                    f"écrire dans le prompt une ligne `SDDA-INSTANCE: {{{placeholder}}}` (cf. /sdda-build STEP 4.2) : "
                    "une instance anonyme n'a pas de répertoire, et deux instances dans un prompt n'en ont pas un seul")
    _instances.declare(root, agent, found[0])
    return ALLOW


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
