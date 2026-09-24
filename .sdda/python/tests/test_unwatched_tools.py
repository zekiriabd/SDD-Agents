"""Les outils qu'aucun hook ne voyait : `PowerShell`, `NotebookEdit`, `MultiEdit`.

Les matchers câblés étaient `Bash`, `Read|Glob|Grep`, `Task|Agent`,
`Write|Edit`. Un sous-agent sous Windows avait donc un shell (`PowerShell`)
et deux outils d'écriture (`NotebookEdit`, `MultiEdit`) sans aucun contrôle —
et la lecture des `.env` ne tenait qu'à l'analyse lexicale des hooks.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from conftest import make_project
from sdda_admin import harness_build
from sdda_hooks import _hook, preflight_bash_ownership, preflight_ownership

ALLOW, DENY = _hook.ALLOW, _hook.DENY
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path)


def write_tool(project: Path, tool: str, **tool_input) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = preflight_ownership.check(project, {"tool_name": tool, "tool_input": tool_input,
                                                   "agent_type": "dev-agent"})
    return code, buf.getvalue()


def test_notebook_edit_is_judged_on_its_notebook_path(project: Path) -> None:
    code, err = write_tool(project, "NotebookEdit",
                           notebook_path=str(project / "workspace/pipeline/datasets/golden/explore.ipynb"),
                           new_source="x")
    assert code == DENY and "DATASET_OWNERSHIP_VIOLATION" in err


def test_multi_edit_is_judged_like_edit(project: Path) -> None:
    code, err = write_tool(project, "MultiEdit",
                           file_path=str(project / "workspace/src/SupportDesk/prompts/a.system.md"), edits=[])
    assert code == DENY and "PROMPT_OWNERSHIP_VIOLATION" in err


def test_the_write_hook_matcher_names_every_writing_tool() -> None:
    assert set(preflight_ownership.WIRING["matcher"].split("|")) >= {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def test_the_shell_hook_matcher_names_powershell() -> None:
    assert "PowerShell" in preflight_bash_ownership.WIRING["matcher"].split("|")


def test_generated_settings_wire_the_tools_and_deny_secret_reads() -> None:
    plan, _counts = harness_build.build_harness("claude-code", harness_build.load_matrix()["claude-code"])
    settings = json.loads(next(c for p, c in plan.files.items() if Path(p).name == "settings.json"))
    matchers = {m["matcher"] for m in settings["hooks"]["PreToolUse"]}
    assert "Bash|PowerShell" in matchers and "Write|Edit|MultiEdit|NotebookEdit" in matchers
    deny = settings["permissions"]["deny"]
    assert "Read(/workspace/assets/.env)" in deny and "Read(/workspace/src/**/.env)" in deny
    assert not any(".env.example" in rule for rule in deny), "un gabarit de noms reste lisible"


def test_the_committed_facade_carries_the_deny_rules() -> None:
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert set(harness_build.SECRET_READ_DENY) <= set(settings.get("permissions", {}).get("deny", []))
