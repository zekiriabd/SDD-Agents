"""preflight_judge_calibration — aucun agent d'évaluation sur une calibration mesurée ROUGE (P9).

Un juge non calibré est rétrogradé en `advisory` par l'eval runner lui-même
(il ne reçoit que la calibration mesurée par `calibrate-judge`). Le hook garde
ce que le runner ne rattrape pas : une calibration mesurée rouge — labels
synthétiques, juge bloquant sans repli. Il lisait une clé `blocking` que rien
n'écrivait, dans les ENTRÉES de calibration : il autorisait tout, toujours.

Comme dans `test_hooks.py`, on appelle `check` directement pour le verdict ; le
périmètre (`applies_to`) et la dégradation sûre passent par `main()`, en
simulant le `stdin` du harnais.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from sdda_hooks import _hook
from sdda_hooks import preflight_judge_calibration as hook

ALLOW, DENY = _hook.ALLOW, _hook.DENY
VAL_DIR = "workspace/.sys/.validation"


def call(project: Path, **payload) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = hook.check(project, payload)
    return code, buf.getvalue()


def via_harness(monkeypatch: pytest.MonkeyPatch, project: Path, **payload) -> tuple[int, str]:
    """Joue `main()` comme le harnais : payload JSON sur stdin, `cwd` = racine du projet."""
    monkeypatch.setattr(_hook.sys, "stdin", io.StringIO(json.dumps({**payload, "cwd": str(project)})))
    monkeypatch.setattr(_hook.sys, "argv", ["preflight_judge_calibration"])
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = hook.main()
    return code, buf.getvalue()


def write_measured(project: Path, mission: str = "1", *, ok: bool, classes: tuple[str, ...] = ()) -> None:
    """Le rapport que `calibrate-judge` écrit : `G5-{n}.calibration.json`."""
    path = project / VAL_DIR / f"G5-{mission}.calibration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"gate": "G5", "artifact": mission, "part": "calibration", "ok": ok,
                                "errors": [{"class": c, "message": "x"} for c in classes], "warnings": []}),
                    encoding="utf-8")


def brief(mission: str) -> dict:
    return {"tool_name": "Task", "tool_input": {"subagent_type": "qa-evals", "prompt": f"MISSION : {mission}\n…"}}


def test_the_hook_is_wired_on_task_for_the_eval_builders() -> None:
    assert hook.HOOK == "preflight_judge_calibration" and callable(hook.main)
    assert hook.WIRING["event"] == "PreToolUse" and hook.WIRING["matcher"] == "Task|Agent"
    assert hook.WIRING["applies_to"] == _hook.EVAL_BUILDERS
    assert "qa-evals" in hook.WIRING["applies_to"]


def test_no_measured_calibration_allows_and_says_advisory(project: Path) -> None:
    code, err = call(project)
    assert code == ALLOW and "advisory" in err


def test_a_green_measured_calibration_allows(project: Path) -> None:
    write_measured(project, ok=True)
    assert call(project, **brief("1"))[0] == ALLOW


def test_a_red_measured_calibration_refuses_with_its_classes(project: Path) -> None:
    write_measured(project, ok=False, classes=("JUDGE_CALIBRATION_SYNTHETIC",))
    code, err = call(project, **brief("1"))
    assert code == DENY
    assert "CAUSE: [JUDGE_NOT_CALIBRATED]" in err and "JUDGE_CALIBRATION_SYNTHETIC" in err
    assert "FIX:" in err and "calibrate-judge" in err


def test_the_red_report_of_another_mission_does_not_block_this_one(project: Path) -> None:
    """La mission se lit dans le brief (`MISSION : n`) : un rouge de la mission 2 ne ferme pas la 1."""
    write_measured(project, "2", ok=False, classes=("JUDGE_CALIBRATION_SYNTHETIC",))
    assert call(project, **brief("1"))[0] == ALLOW
    assert call(project, **brief("2"))[0] == DENY


def test_the_old_inputs_with_a_blocking_key_no_longer_decide(project: Path) -> None:
    """Les ENTRÉES `pipeline/calibration/*.json` ne sont pas le verdict : seul le rapport mesuré l'est."""
    path = project / "workspace/pipeline/calibration/tone.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"blocking": True, "kappa": 0.1, "items": 5}), encoding="utf-8")
    assert call(project)[0] == ALLOW


def test_an_unreadable_report_is_skipped(project: Path) -> None:
    path = project / VAL_DIR / "G5-1.calibration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ pas du json", encoding="utf-8")
    assert call(project, **brief("1"))[0] == ALLOW


def test_an_out_of_scope_agent_passes_even_on_a_red_calibration(
        project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_measured(project, ok=False, classes=("JUDGE_CALIBRATION_SYNTHETIC",))
    code, err = via_harness(monkeypatch, project, tool_name="Task", tool_input={"subagent_type": "po-elicitor"})
    assert code == ALLOW, err


def test_an_in_scope_agent_is_refused(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_measured(project, ok=False, classes=("JUDGE_CALIBRATION_SYNTHETIC",))
    code, err = via_harness(monkeypatch, project, **brief("1"))
    assert code == DENY and "JUDGE_NOT_CALIBRATED" in err


def test_a_crash_inside_check_degrades_to_allow(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le scénario le plus coûteux : un hook cassé qui bloque, et qu'on désactive."""
    def broken(root: Path, data: dict) -> int:
        raise RuntimeError("cassé pour le test")

    monkeypatch.setattr(hook, "check", broken)
    code, err = via_harness(monkeypatch, project, tool_input={"subagent_type": "qa-evals"})
    assert code == ALLOW
    assert "AUTORISÉE" in err and "RuntimeError" in err
