"""Un hook en panne ne passe plus pour un feu vert — mode strict, commande robuste, auto-contrôle.

Deux pannes autorisaient en silence : une exception dans le hook (`degrade`
autorise, par doctrine, en session interactive), et un interpréteur absent du
PATH — le hook ne démarre pas, le code n'est pas 2, le harnais laisse passer.
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from sdda_admin import harness_build, hooks_selfcheck
from sdda_hooks import _hook

ROOT = Path(__file__).resolve().parents[3]
OWNERSHIP = ".sdda/python/sdda_hooks/preflight_ownership.py"


def test_a_crashing_hook_allows_interactively_and_denies_in_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root, _data):
        raise RuntimeError("panne simulée")

    monkeypatch.delenv(_hook.STRICT_ENV, raising=False)
    buf = io.StringIO()
    with redirect_stderr(buf):
        assert _hook.run("h", boom) == _hook.ALLOW
    assert "AUTORISÉE" in buf.getvalue() and _hook.STRICT_ENV in buf.getvalue()

    monkeypatch.setenv(_hook.STRICT_ENV, "1")
    buf = io.StringIO()
    with redirect_stderr(buf):
        assert _hook.run("h", boom) == _hook.DENY
    assert "HOOK_FAILED" in buf.getvalue()


def test_the_generated_command_takes_a_configurable_interpreter_and_fails_closed_when_strict() -> None:
    command = harness_build.hook_command(OWNERSHIP)
    assert "${SDDA_PYTHON:-python}" in command and '"$CLAUDE_PROJECT_DIR/' + OWNERSHIP + '"' in command
    assert "SDDA_HOOKS_STRICT" in command and "exit 2" in command


@pytest.mark.skipif(shutil.which("bash") is None, reason="le harnais lance les hooks dans un shell POSIX")
@pytest.mark.parametrize("strict,expected", [("0", 127), ("1", 2)])
def test_a_missing_interpreter_is_loud_and_refused_when_strict(strict: str, expected: int) -> None:
    import os

    command = harness_build.hook_command(OWNERSHIP)
    shell = hooks_selfcheck._shell()
    assert shell
    proc = subprocess.run([*shell, command], input="{}", text=True, capture_output=True,
                          env=dict(os.environ, SDDA_PYTHON="python-introuvable-xyz",
                                   SDDA_HOOKS_STRICT=strict, CLAUDE_PROJECT_DIR=str(ROOT)), timeout=30)
    assert proc.returncode == expected
    assert "ne demarre pas" in proc.stderr


@pytest.mark.skipif(shutil.which("bash") is None and shutil.which("sh") is None, reason="shell POSIX requis")
def test_selfcheck_runs_every_wired_hook_of_the_facade() -> None:
    report = hooks_selfcheck.run(ROOT, ROOT / ".claude" / "settings.json")
    assert report.ok, [e.message for e in report.errors]
    checks = report.data["checks"]
    wired = {h["command"] for entries in json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
             ["hooks"].values() for e in entries for h in e["hooks"]}
    assert len({c["hook"] for c in checks}) == len(wired)
    # Les juges d'ownership sont vérifiés dans les DEUX sens : ils démarrent ET refusent.
    assert any(c["payload"] == "à refuser" and c["code"] == 2 for c in checks)


@pytest.mark.skipif(shutil.which("bash") is None and shutil.which("sh") is None, reason="shell POSIX requis")
def test_selfcheck_names_a_missing_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": harness_build.hook_command(OWNERSHIP)}]}]}}),
        encoding="utf-8")
    monkeypatch.setenv("SDDA_PYTHON", "python-introuvable-xyz")
    report = hooks_selfcheck.run(ROOT, settings)
    assert "HOOK_INTERPRETER_MISSING" in report.classes
