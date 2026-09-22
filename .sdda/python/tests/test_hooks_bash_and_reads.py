"""Les deux hooks qui ferment ce que `preflight_ownership` laissait ouvert.

- `preflight_bash_ownership` : la matrice d'écriture était contournable par le
  shell (`echo > datasets/…`, `rm prompts/…`). Le hook lit la commande.
- `preflight_forbidden_reads` : `forbidden_reads` de `loader.yml` n'était
  appliqué par rien. Le hook juge `Read`, `Glob` et `Grep`.

Même contrat que les autres hooks : le fil principal passe, un agent hors
matrice passe, un refus dit ce qui le débloque.
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from conftest import make_project
from sdda_hooks import _hook, preflight_bash_ownership, preflight_forbidden_reads
from sdda_scripts import audit_ownership as ao

ALLOW, DENY = _hook.ALLOW, _hook.DENY


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path)


def call(module, project: Path, **payload) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = module.check(project, payload)
    return code, buf.getvalue()


def bash(project: Path, agent: str | None, command: str) -> tuple[int, str]:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent:
        payload["subagent_type"] = agent
    return call(preflight_bash_ownership, project, **payload)


def read_tool(project: Path, agent: str | None, tool: str, **tool_input) -> tuple[int, str]:
    payload = {"tool_name": tool, "tool_input": tool_input}
    if agent:
        payload["subagent_type"] = agent
    return call(preflight_forbidden_reads, project, **payload)


# ---------------------------------------------------------------------------
# Le contrat commun
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("module", [preflight_bash_ownership, preflight_forbidden_reads])
def test_declares_name_wiring_and_entrypoint(module) -> None:
    assert isinstance(module.HOOK, str) and module.HOOK
    assert module.WIRING["event"] == "PreToolUse"
    assert module.WIRING["applies_to"] == ()
    assert callable(module.main)


def test_wiring_covers_bash_and_the_three_readers() -> None:
    assert preflight_bash_ownership.WIRING["matcher"] == "Bash"
    assert set(preflight_forbidden_reads.WIRING["matcher"].split("|")) == {"Read", "Glob", "Grep"}


# ---------------------------------------------------------------------------
# Bash : écritures
# ---------------------------------------------------------------------------
def test_a_redirection_into_the_dataset_is_a_dataset_violation(project: Path) -> None:
    code, err = bash(project, "dev-agent", "echo '{}' >> workspace/datasets/golden/billing-v1.jsonl")
    assert code == DENY and "DATASET_OWNERSHIP_VIOLATION" in err and "FIX:" in err


def test_rm_on_a_prompt_is_a_prompt_violation(project: Path) -> None:
    code, err = bash(project, "dev-agent", "rm -f workspace/prompts/billing-specialist.system.md")
    assert code == DENY and "PROMPT_OWNERSHIP_VIOLATION" in err


def test_tee_sed_in_place_and_mv_are_writes(project: Path) -> None:
    for command in (
        "cat x | tee workspace/prompts/a.system.md",
        "sed -i 's/a/b/' workspace/prompts/a.system.md",
        "mv workspace/src/agents/x.py workspace/prompts/a.system.md",
        "cp workspace/src/agents/x.py workspace/datasets/golden/y.jsonl",
    ):
        code, err = bash(project, "dev-agent", command)
        assert code == DENY, command
        assert "OWNERSHIP_VIOLATION" in err, command


def test_powershell_verbs_are_understood(project: Path) -> None:
    code, err = bash(project, "dev-agent", "Set-Content -Path workspace/datasets/golden/x.jsonl -Value '{}'")
    assert code == DENY and "DATASET_OWNERSHIP_VIOLATION" in err
    code, err = bash(project, "dev-agent", "Remove-Item workspace/prompts/a.system.md -Force")
    assert code == DENY and "PROMPT_OWNERSHIP_VIOLATION" in err


def test_a_write_in_the_agents_own_zone_passes(project: Path) -> None:
    code, err = bash(project, "dev-tools", "echo x > workspace/src/tools/invoice_lookup.py")
    assert code == ALLOW, err


def test_a_command_naming_no_governed_path_is_free(project: Path) -> None:
    for command in ("git status", "pip install -q pytest", "python -m pytest -q", "ls -la", "echo hello > /tmp/x"):
        code, err = bash(project, "dev-agent", command)
        assert code == ALLOW, (command, err)


def test_the_main_thread_and_unknown_agents_are_not_the_hooks_call(project: Path) -> None:
    assert bash(project, None, "rm -rf workspace/datasets")[0] == ALLOW
    assert bash(project, "un-agent-tiers", "rm -rf workspace/datasets")[0] == ALLOW


def test_chained_commands_are_each_judged(project: Path) -> None:
    code, err = bash(project, "dev-agent", "cd workspace && ls && echo x > workspace/datasets/golden/z.jsonl")
    assert code == DENY and "DATASET_OWNERSHIP_VIOLATION" in err


def test_a_sed_without_in_place_is_a_read_not_a_write(project: Path) -> None:
    # dev-tools n'a pas de forbidden_reads : lire un dataset n'est pas une faute pour lui.
    code, err = bash(project, "dev-tools", "sed 's/a/b/' workspace/datasets/golden/billing-v1.jsonl")
    assert code == ALLOW, err


# ---------------------------------------------------------------------------
# Bash : lectures interdites
# ---------------------------------------------------------------------------
def test_cat_on_stack_md_by_the_elicitor_is_refused(project: Path) -> None:
    code, err = bash(project, "po-elicitor", "cat workspace/stack/STACK.md")
    assert code == DENY and "OWNERSHIP_READ_FORBIDDEN" in err and "INJECTÉ" in err


def test_grep_over_the_whole_workspace_leaks_forbidden_content(project: Path) -> None:
    code, err = bash(project, "po-elicitor", "grep -rn Voyage workspace/")
    assert code == DENY and "OWNERSHIP_READ_FORBIDDEN" in err


def test_grep_inside_the_readable_zone_passes(project: Path) -> None:
    code, err = bash(project, "po-elicitor", "grep -rn Objective workspace/missions/")
    assert code == ALLOW, err


# ---------------------------------------------------------------------------
# Read / Glob / Grep
# ---------------------------------------------------------------------------
def test_read_of_a_forbidden_file_is_refused(project: Path) -> None:
    code, err = read_tool(project, "po-elicitor", "Read", file_path=str(project / "workspace/stack/STACK.md"))
    assert code == DENY and "OWNERSHIP_READ_FORBIDDEN" in err and "workspace/stack/STACK.md" in err


def test_read_of_a_declared_source_passes(project: Path) -> None:
    code, err = read_tool(project, "po-elicitor", "Read", file_path=str(project / "workspace/missions/1-SupportAssistant.md"))
    assert code == ALLOW, err


def test_forbidden_reads_with_placeholders_are_zones(project: Path) -> None:
    # `workspace/src/**` : tout fichier sous src/ est couvert, quelle que soit la profondeur.
    code, err = read_tool(project, "po-capabilities", "Read", file_path=str(project / "workspace/src/app/deep/agent.py"))
    assert code == DENY and "workspace/src/**" in err


def test_glob_from_inside_a_forbidden_zone_is_refused_but_from_an_ancestor_passes(project: Path) -> None:
    code, err = read_tool(project, "po-elicitor", "Glob", pattern="*.py", path=str(project / "workspace/src"))
    assert code == DENY and "OWNERSHIP_READ_FORBIDDEN" in err
    # Des NOMS depuis la racine du workspace ne sont pas du contenu interdit.
    code, err = read_tool(project, "po-elicitor", "Glob", pattern="**/*.md", path=str(project / "workspace"))
    assert code == ALLOW, err


def test_grep_from_an_ancestor_of_a_forbidden_zone_is_refused(project: Path) -> None:
    code, err = read_tool(project, "po-elicitor", "Grep", pattern="Voyage", path=str(project / "workspace"))
    assert code == DENY and "Restreindre" in err
    code, err = read_tool(project, "po-elicitor", "Grep", pattern="Voyage")          # sans path : tout le projet
    assert code == DENY


def test_grep_inside_the_readable_zone_passes_the_read_hook(project: Path) -> None:
    code, err = read_tool(project, "po-elicitor", "Grep", pattern="Objective", path=str(project / "workspace/missions"))
    assert code == ALLOW, err


def test_an_agent_without_forbidden_reads_is_never_refused(project: Path) -> None:
    code, err = read_tool(project, "dev-tools", "Grep", pattern="x", path=str(project / "workspace"))
    assert code == ALLOW, err


def test_main_thread_and_unknown_agent_pass_the_read_hook(project: Path) -> None:
    assert read_tool(project, None, "Read", file_path=str(project / "workspace/stack/STACK.md"))[0] == ALLOW
    assert read_tool(project, "inconnu", "Read", file_path=str(project / "workspace/stack/STACK.md"))[0] == ALLOW


# ---------------------------------------------------------------------------
# La brique partagée : audit_ownership.read_violation
# ---------------------------------------------------------------------------
def test_read_violation_distinguishes_the_three_scopes() -> None:
    loader = {"a": {"reads": ["workspace/missions/**"], "forbidden_reads": ["workspace/stack/STACK.md", "workspace/src/**"]}}
    assert ao.read_violation(loader, "a", "workspace/stack/STACK.md") == "workspace/stack/STACK.md"
    assert ao.read_violation(loader, "a", "workspace/stack/other.md") is None
    assert ao.read_violation(loader, "a", "workspace/src", scope="names") == "workspace/src/**"
    assert ao.read_violation(loader, "a", "workspace", scope="names") is None
    assert ao.read_violation(loader, "a", "workspace", scope="content") in {"workspace/stack/STACK.md", "workspace/src/**"}
    assert ao.read_violation(loader, "a", ".", scope="content") is not None
    assert ao.read_violation(loader, "a", "workspace/missions", scope="content") is None


def test_classify_extracts_writes_and_reads(tmp_path: Path) -> None:
    writes, reads = preflight_bash_ownership.classify(
        tmp_path,
        "cat workspace/a.md | tee workspace/b.md > workspace/c.md; grep x workspace/src; ls workspace/prompts; dd if=x of=workspace/d.bin",
    )
    assert writes == ["workspace/c.md", "workspace/b.md", "workspace/d.bin"]
    assert ("workspace/a.md", "file") in reads
    assert ("workspace/src", "content") in reads
    assert ("workspace/prompts", "names") in reads
