"""Fichiers de secrets : toutes les graphies qui ouvrent le même fichier.

`is_secret_file` comparait le nom à la casse près : `.ENV` passait, alors que
sous Windows et macOS c'est le même fichier. Les points et espaces finaux et le
flux NTFS (`.env::$DATA`) ouvrent eux aussi `.env` sous Windows.
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


@pytest.mark.parametrize("name", [
    ".env", ".ENV", ".Env", ".env.local", ".ENV.LOCAL", ".env.", ".env ", ".env::$DATA", ".env:stream",
    "workspace\\assets\\.ENV", "workspace/src/App/.env.Production",
])
def test_every_spelling_of_a_secret_file_is_one(name: str) -> None:
    assert ao.is_secret_file(name)


@pytest.mark.parametrize("name", [".env.example", ".ENV.EXAMPLE", ".env.sample", "env", "x.env", "README.md", ""])
def test_templates_and_lookalikes_are_not(name: str) -> None:
    assert not ao.is_secret_file(name)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = make_project(tmp_path)
    (root / "workspace" / "assets").mkdir(parents=True, exist_ok=True)
    (root / "workspace" / "assets" / ".env").write_text("LLM_API_KEY=sk-test\n", encoding="utf-8")
    return root


def read_tool(project: Path, agent: str, tool: str, **tool_input) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = preflight_forbidden_reads.check(project, {"tool_name": tool, "tool_input": tool_input,
                                                         "agent_type": agent})
    return code, buf.getvalue()


def test_read_of_an_uppercase_env_is_refused(project: Path) -> None:
    code, err = read_tool(project, "architect-data", "Read", file_path=str(project / "workspace/assets/.ENV"))
    assert code == DENY and "SECRET_READ_FORBIDDEN" in err


def test_read_through_git_bash_drive_path_is_refused(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ao, "CASE_INSENSITIVE", True)
    posix = project.as_posix()
    if len(posix) > 1 and posix[1] == ":":
        git_bash = "/" + posix[0].lower() + posix[2:] + "/workspace/assets/.env"
        code, err = read_tool(project, "dev-data", "Read", file_path=git_bash)
        assert code == DENY and "SECRET_READ_FORBIDDEN" in err


def test_grep_aimed_at_a_secret_by_its_glob_is_refused(project: Path) -> None:
    code, err = read_tool(project, "dev-tools", "Grep", pattern="KEY", path=str(project / "workspace"), glob=".env*")
    assert code == DENY and "SECRET_READ_FORBIDDEN" in err
    code, err = read_tool(project, "dev-tools", "Grep", pattern="KEY", path=str(project / "workspace"), glob="*.py")
    assert code == ALLOW, err


def test_a_template_stays_readable(project: Path) -> None:
    code, err = read_tool(project, "dev-backend", "Read", file_path=str(project / "workspace/src/App/.env.example"))
    assert code == ALLOW, err


def test_bash_cat_of_an_uppercase_env_is_refused(project: Path) -> None:
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = preflight_bash_ownership.check(project, {"tool_name": "Bash", "agent_type": "dev-data",
                                                        "tool_input": {"command": "cat workspace/assets/.ENV"}})
    assert code == DENY and "SECRET_READ_FORBIDDEN" in buf.getvalue()


def test_secrets_under_names_what_a_recursive_read_would_return(project: Path) -> None:
    assert ao.secrets_under(project, "workspace") == ["workspace/assets/.env"]
    assert ao.secrets_under(project, "workspace/pipeline") == []
