"""Deux régressions qui désarmaient ou paralysaient toute la couche de hooks.

Elles ne se voyaient sur aucun test existant parce que les tests fabriquaient
le payload à la forme que le code attendait, et non à celle que le harnais
envoie réellement. C'est le défaut de test le plus coûteux : il confirme
l'implémentation au lieu de confronter le contrat.

**1. L'identité de l'agent vit sous `tool_input`, pas à la racine.** Les hooks
lisaient `data["subagent_type"]`, toujours absent. Conséquence double et
contradictoire : un hook de gate se croyait dans le périmètre faute
d'étiquette et refusait le PREMIER spawn du pipeline ; un hook d'ownership
laissait au contraire tout passer, puisqu'il autorise explicitement le fil
principal.

**2. `dev-agent` était refusé dans sa propre zone.** `forbidden_writes`
portait `src/**/agents/{other}/**`, dont le `{other}` se compile en `[^/]+` —
exactement comme le `{agent}` de `writes:`. Les interdits étant testés en
premier, la phase 4 devenait impossible dès que le hook recevait l'identité.
Les deux bugs se masquaient mutuellement : tant que l'identité n'arrivait pas,
le second restait invisible.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import make_project  # noqa: E402

PYTHON_DIR = Path(__file__).resolve().parents[1]
HOOKS = PYTHON_DIR / "sdda_hooks"


def _hook(script: str, payload: dict, root: Path) -> int:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SDDA_")}
    proc = subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=json.dumps({**payload, "cwd": str(root)}),
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    return proc.returncode


# ---------------------------------------------------------------------------
# 1. La forme réelle du payload
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("script", [
    "preflight_tool_gate.py",
    "preflight_retrieval_gate.py",
    "preflight_cap_gate.py",
    "preflight_db_envelope.py",
])
def test_a_gate_hook_lets_an_out_of_scope_agent_through(script: str, tmp_path: Path) -> None:
    """`po-elicitor` en phase 0 ne doit être refusé par AUCUNE gate aval.

    C'est le scénario qui aurait paralysé chaque pipeline dès son premier
    agent : aucune gate ne peut être verte à ce moment-là, et un hook qui
    refuse tout se fait désactiver — emportant les protections qui marchaient.
    """
    project = make_project(tmp_path)
    code = _hook(script, {"tool_name": "Task", "tool_input": {"subagent_type": "po-elicitor"}}, project)
    assert code == 0, f"{script} a refusé un agent hors de son périmètre"


def test_a_gate_hook_still_refuses_an_in_scope_agent(tmp_path: Path) -> None:
    """La contrepartie : `dev-agent` sans G3 verte reste refusé.

    Sans ce test, « ne refuse plus rien » passerait pour une correction.
    """
    project = make_project(tmp_path)
    code = _hook("preflight_tool_gate.py",
                 {"tool_name": "Task", "tool_input": {"subagent_type": "dev-agent"}}, project)
    assert code == 2, "G3 absente : le câblage d'un agent doit être refusé"


def test_the_identity_is_read_from_both_shapes(tmp_path: Path) -> None:
    """Racine et `tool_input` donnent le même verdict.

    Le format diffère entre harnais ; n'en lire qu'un seul désarme la couche
    entière sur les autres, silencieusement.
    """
    project = make_project(tmp_path)
    nested = _hook("preflight_tool_gate.py",
                   {"tool_name": "Task", "tool_input": {"subagent_type": "po-elicitor"}}, project)
    flat = _hook("preflight_tool_gate.py",
                 {"tool_name": "Task", "subagent_type": "po-elicitor"}, project)
    assert nested == flat == 0


# ---------------------------------------------------------------------------
# 2. L'ownership de `dev-agent`
# ---------------------------------------------------------------------------
def test_dev_agent_may_write_in_its_own_directory(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    code = _hook("preflight_ownership.py", {
        "tool_name": "Write",
        "tool_input": {"subagent_type": "dev-agent",
                       "file_path": "workspace/src/SupportAssistant/agents/billing/agent.py"},
    }, project)
    assert code == 0, "dev-agent doit pouvoir écrire dans src/agents/{son agent}/"


@pytest.mark.parametrize("forbidden", [
    "workspace/pipeline/datasets/golden/items.jsonl",
    "workspace/src/SupportAssistant/prompts/billing.system.md",
    "workspace/pipeline/suites/s.yml",
    "workspace/src/SupportAssistant/tools/crm.py",
])
def test_dev_agent_is_still_confined(forbidden: str, tmp_path: Path) -> None:
    """La règle critique de l'agentic tient : l'agent qui écrit le code ne
    modifie ni le jeu qui le juge, ni le prompt qu'il implémente.

    Débloquer sa propre zone ne devait rien relâcher d'autre — sans ces cas,
    « ça marche enfin » aurait aussi pu vouloir dire « plus rien ne protège ».
    """
    project = make_project(tmp_path)
    code = _hook("preflight_ownership.py", {
        "tool_name": "Write",
        "tool_input": {"subagent_type": "dev-agent", "file_path": forbidden},
    }, project)
    assert code == 2, f"dev-agent ne doit pas pouvoir écrire {forbidden}"
