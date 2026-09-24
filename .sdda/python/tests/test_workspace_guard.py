"""La garde anti-pollution du `workspace/` réel — elle-même testée.

Une garde qu'on n'exerce jamais est une garde dont on découvre qu'elle ne
tenait pas le jour où le journal d'audit du projet se remplit de faux bypass.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import _workspace_guard as guard


def test_an_in_process_write_under_the_real_workspace_is_refused_before_it_happens() -> None:
    probe = guard.REAL_WORKSPACE / ".sys" / ".audit" / "guard-probe.jsonl"
    try:
        with guard.expect_violation(), pytest.raises(guard.WorkspaceWriteForbidden):
            probe.open("a", encoding="utf-8").close()
        with guard.expect_violation(), pytest.raises(guard.WorkspaceWriteForbidden):
            os.close(os.open(str(probe), os.O_WRONLY | os.O_CREAT | os.O_APPEND))
        with guard.expect_violation(), pytest.raises(guard.WorkspaceWriteForbidden):
            (guard.REAL_WORKSPACE / "guard-probe-dir").mkdir()
        assert not probe.exists() and not (guard.REAL_WORKSPACE / "guard-probe-dir").exists()
    finally:  # si la garde a failli, ne pas laisser la sonde chez l'utilisateur
        with guard.disarmed():
            probe.unlink(missing_ok=True)
            if (guard.REAL_WORKSPACE / "guard-probe-dir").is_dir():
                (guard.REAL_WORKSPACE / "guard-probe-dir").rmdir()


def test_the_guard_is_not_fooled_by_a_relative_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(guard.REPO_ROOT)
    with guard.expect_violation(), pytest.raises(guard.WorkspaceWriteForbidden):
        open("workspace/guard-probe.txt", "w", encoding="utf-8").close()
    assert not (guard.REAL_WORKSPACE / "guard-probe.txt").exists()


def test_reading_the_real_workspace_and_writing_elsewhere_stay_allowed(tmp_path: Path) -> None:
    for path in guard.REAL_WORKSPACE.rglob("*"):
        if path.is_file():
            path.read_bytes()
            break
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "ok.txt").write_text("ok", encoding="utf-8")
    assert (tmp_path / "workspace" / "ok.txt").read_text(encoding="utf-8") == "ok"


def test_the_session_snapshot_sees_a_write_the_audit_hook_cannot(tmp_path: Path) -> None:
    """Un sous-processus échappe à l'audit hook ; la comparaison de fin de session, non."""
    (tmp_path / ".sys" / ".audit").mkdir(parents=True)
    (tmp_path / ".sys" / ".audit" / ".gitkeep").write_text("", encoding="utf-8")
    before = guard.snapshot(tmp_path)
    (tmp_path / ".sys" / ".audit" / "bypasses.jsonl").write_text('{"x": 1}\n', encoding="utf-8")
    (tmp_path / ".sys" / ".audit" / ".gitkeep").write_text("changé", encoding="utf-8")
    changes = guard.diff(before, guard.snapshot(tmp_path))
    assert "+ .sys/.audit/bypasses.jsonl" in changes and "~ .sys/.audit/.gitkeep" in changes
