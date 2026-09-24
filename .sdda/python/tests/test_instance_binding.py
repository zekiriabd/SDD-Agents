"""Liaison d'instance de `dev-agent` : déclarée au spawn, liée à la première écriture.

`{agent}` se compilait en « un segment quelconque » : l'instance lancée pour
`billing` pouvait écrire `agents/triage/`, en pleine phase 4 parallèle.
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from conftest import make_project
from sdda_hooks import _hook, preflight_bash_ownership, preflight_instance_bind, preflight_ownership

ALLOW, DENY = _hook.ALLOW, _hook.DENY
AGENTS = "workspace/src/SupportDesk/agents"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path)


def spawn(project: Path, prompt: str, agent: str = "dev-agent") -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = preflight_instance_bind.check(project, {
            "tool_name": "Agent", "tool_input": {"subagent_type": agent, "prompt": prompt, "description": "x"}})
    return code, buf.getvalue()


def write(project: Path, agent_id: str | None, rel: str) -> tuple[int, str]:
    payload = {"tool_name": "Write", "agent_type": "dev-agent",
               "tool_input": {"file_path": str(project / rel), "content": "x"}}
    if agent_id:
        payload["agent_id"] = agent_id
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = preflight_ownership.check(project, payload)
    return code, buf.getvalue()


def test_a_dev_agent_spawn_without_instance_declaration_is_refused(project: Path) -> None:
    code, err = spawn(project, "Implémenter l'agent billing de la MISSION 1.")
    assert code == DENY and "OWNERSHIP_INSTANCE_UNDECLARED" in err
    code, err = spawn(project, "SDDA-INSTANCE: billing\nSDDA-INSTANCE: triage\n")
    assert code == DENY, "deux instances dans un prompt n'en font pas une"


def test_agents_without_instances_are_not_concerned(project: Path) -> None:
    assert spawn(project, "MISSION 1", agent="dev-tools")[0] == ALLOW
    assert spawn(project, "MISSION 1", agent="architect-topology")[0] == ALLOW


def test_first_write_binds_and_the_instance_stays_home(project: Path) -> None:
    assert spawn(project, "MISSION 1\nSDDA-INSTANCE: billing\n")[0] == ALLOW
    assert spawn(project, "MISSION 1\nSDDA-INSTANCE: triage\n")[0] == ALLOW

    assert write(project, "a1", f"{AGENTS}/billing/agent.py")[0] == ALLOW          # liaison a1 -> billing
    code, err = write(project, "a1", f"{AGENTS}/triage/agent.py")
    assert code == DENY and "OWNERSHIP_INSTANCE_ESCAPE" in err                       # a1 sort de chez lui

    assert write(project, "a2", f"{AGENTS}/triage/agent.py")[0] == ALLOW            # liaison a2 -> triage
    code, err = write(project, "a2", f"{AGENTS}/billing/tests/test_x.py")
    assert code == DENY and "OWNERSHIP_INSTANCE_ESCAPE" in err


def test_an_undeclared_instance_has_no_directory(project: Path) -> None:
    assert spawn(project, "SDDA-INSTANCE: billing")[0] == ALLOW
    code, err = write(project, "a3", f"{AGENTS}/refund/agent.py")
    assert code == DENY and "OWNERSHIP_INSTANCE_UNDECLARED" in err


def test_a_claimed_instance_refuses_a_second_writer(project: Path) -> None:
    assert spawn(project, "SDDA-INSTANCE: billing")[0] == ALLOW
    assert write(project, "a1", f"{AGENTS}/billing/agent.py")[0] == ALLOW
    code, err = write(project, "a9", f"{AGENTS}/billing/other.py")
    assert code == DENY and "OWNERSHIP_INSTANCE_ESCAPE" in err


def test_a_respawn_releases_the_previous_claim(project: Path) -> None:
    """Reprise après échec : la nouvelle instance reçoit un nouvel `agent_id`."""
    assert spawn(project, "SDDA-INSTANCE: billing")[0] == ALLOW
    assert write(project, "old", f"{AGENTS}/billing/agent.py")[0] == ALLOW
    assert spawn(project, "SDDA-INSTANCE: billing")[0] == ALLOW
    assert write(project, "new", f"{AGENTS}/billing/agent.py")[0] == ALLOW


def test_without_agent_id_the_static_pattern_applies(project: Path) -> None:
    """Harnais qui ne transmet pas `agent_id` : rien à lier — le contrôle après coup reste."""
    assert write(project, None, f"{AGENTS}/anything/agent.py")[0] == ALLOW


def test_the_shell_hook_binds_the_same_way(project: Path) -> None:
    assert spawn(project, "SDDA-INSTANCE: billing")[0] == ALLOW
    assert spawn(project, "SDDA-INSTANCE: triage")[0] == ALLOW

    def sh(agent_id: str, command: str) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = preflight_bash_ownership.check(project, {
                "tool_name": "Bash", "agent_type": "dev-agent", "agent_id": agent_id, "cwd": str(project),
                "tool_input": {"command": command}})
        return code, buf.getvalue()

    assert sh("b1", f"mkdir -p {AGENTS}/billing && echo x > {AGENTS}/billing/a.py")[0] == ALLOW
    code, err = sh("b1", f"echo x > {AGENTS}/triage/a.py")
    assert code == DENY and "OWNERSHIP_INSTANCE_ESCAPE" in err
