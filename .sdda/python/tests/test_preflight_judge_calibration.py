"""preflight_judge_calibration — aucun juge BLOQUANT ne rend de verdict sans mesure (P9).

Le hook ne refuse que le juge déclaré `blocking` et non calibré. Un juge
`advisory` n'est pas concerné : c'est la nuance qui rend la règle tenable —
sinon elle se contourne le jour où elle gêne.

Comme dans `test_hooks.py`, on appelle `check` directement pour le verdict ; le
périmètre (`applies_to`) et la dégradation sûre passent par `main()`, en
simulant le `stdin` du harnais.
"""
from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from sdda_hooks import _hook
from sdda_hooks import preflight_judge_calibration as hook

ALLOW, DENY = _hook.ALLOW, _hook.DENY
CAL_DIR = "workspace/evals/calibration"
STACK = "workspace/stack/STACK.md"


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


def write_judge(project: Path, name: str = "groundedness", **fields) -> None:
    path = project / CAL_DIR / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"grader": name, **fields}), encoding="utf-8")


def set_config(project: Path, **values) -> None:
    path = project / STACK
    text = path.read_text(encoding="utf-8")
    extra = "".join(f"{k}: {v}\n" for k, v in values.items())
    path.write_text(text.replace("AppName: SupportAssistant\n", "AppName: SupportAssistant\n" + extra, 1),
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# Le contrat de câblage
# ---------------------------------------------------------------------------
def test_the_hook_is_wired_on_task_for_the_eval_builders() -> None:
    assert hook.HOOK == "preflight_judge_calibration" and callable(hook.main)
    assert hook.WIRING["event"] == "PreToolUse" and hook.WIRING["matcher"] == "Task"
    assert hook.WIRING["applies_to"] == _hook.EVAL_BUILDERS
    assert "qa-evals" in hook.WIRING["applies_to"]


# ---------------------------------------------------------------------------
# Autorisé : rien à exiger
# ---------------------------------------------------------------------------
def test_the_fixture_judge_is_not_blocking_so_nothing_is_required(project: Path) -> None:
    """`groundedness.json` ne porte pas `blocking` : un juge advisory n'a pas à être calibré."""
    code, err = call(project)
    assert code == ALLOW, err


def test_no_calibration_dir_allows_and_says_why(project: Path) -> None:
    shutil.rmtree(project / CAL_DIR)
    code, err = call(project)
    assert code == ALLOW and "advisory" in err


def test_an_empty_calibration_dir_allows(project: Path) -> None:
    (project / CAL_DIR / "groundedness.json").unlink()
    assert call(project)[0] == ALLOW


def test_a_blocking_judge_above_both_thresholds_passes(project: Path) -> None:
    write_judge(project, blocking=True, kappa=0.71, items=52)
    code, err = call(project)
    assert code == ALLOW, err


def test_n_is_accepted_as_an_alias_of_items(project: Path) -> None:
    write_judge(project, blocking=True, kappa=0.8, n=60)
    assert call(project)[0] == ALLOW


def test_an_unreadable_report_is_skipped_not_a_reason_to_refuse(project: Path) -> None:
    write_judge(project, blocking=True, kappa=0.8, items=60)
    (project / CAL_DIR / "casse.json").write_text("{ pas du json", encoding="utf-8")
    assert call(project)[0] == ALLOW


def test_a_bom_prefixed_report_is_still_read(project: Path) -> None:
    path = project / CAL_DIR / "groundedness.json"
    path.write_text(json.dumps({"blocking": True, "kappa": 0.2, "items": 60}), encoding="utf-8-sig")
    code, err = call(project)
    assert code == DENY and "JUDGE_NOT_CALIBRATED" in err


# ---------------------------------------------------------------------------
# Refusé : un juge bloquant sans mesure suffisante
# ---------------------------------------------------------------------------
def test_a_blocking_judge_under_kappa_is_refused_with_a_fix(project: Path) -> None:
    write_judge(project, blocking=True, kappa=0.3, items=60)
    code, err = call(project)
    assert code == DENY
    assert "CAUSE: [JUDGE_NOT_CALIBRATED]" in err and "groundedness (kappa=0.3, n=60)" in err
    assert "FIX:" in err and "calibrate_judge" in err and "advisory" in err


def test_a_blocking_judge_with_too_few_items_is_refused(project: Path) -> None:
    write_judge(project, blocking=True, kappa=0.9, items=10)
    code, err = call(project)
    assert code == DENY and "n=10" in err


def test_a_blocking_judge_without_kappa_is_refused(project: Path) -> None:
    """Un kappa absent n'est pas un kappa parfait."""
    write_judge(project, blocking=True, items=60)
    code, err = call(project)
    assert code == DENY and "kappa=None" in err


def test_a_non_numeric_kappa_is_refused(project: Path) -> None:
    write_judge(project, blocking=True, kappa="0.9", items=60)
    assert call(project)[0] == DENY


def test_one_bad_judge_among_good_ones_is_enough_to_refuse(project: Path) -> None:
    write_judge(project, "groundedness", blocking=True, kappa=0.9, items=60)
    write_judge(project, "tone", blocking=True, kappa=0.1, items=60)
    code, err = call(project)
    assert code == DENY and "1 juge(s)" in err and "tone" in err


def test_the_default_thresholds_are_those_of_the_base_config(project: Path) -> None:
    write_judge(project, blocking=True, kappa=0.59, items=50)
    assert call(project)[0] == DENY
    write_judge(project, blocking=True, kappa=0.6, items=49)
    assert call(project)[0] == DENY
    write_judge(project, blocking=True, kappa=0.6, items=50)
    assert call(project)[0] == ALLOW


# ---------------------------------------------------------------------------
# Configuration : seuils du projet, config illisible
# ---------------------------------------------------------------------------
def test_the_project_config_threshold_is_honoured(project: Path) -> None:
    set_config(project, JudgeCalibrationMinKappa=0.8)
    write_judge(project, blocking=True, kappa=0.71, items=52)
    code, err = call(project)
    assert code == DENY and "kappa >= 0.8" in err


def test_the_project_config_item_floor_is_honoured(project: Path) -> None:
    set_config(project, JudgeCalibrationMinItems=100)
    write_judge(project, blocking=True, kappa=0.9, items=60)
    code, err = call(project)
    assert code == DENY and ">= 100 labels" in err


def test_an_unreadable_config_falls_back_to_the_framework_defaults(
        project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le projet relâche le seuil à 0,3 — mais sa config est refusée : on juge à 0,6.

    Dégrader vers les défauts du framework, jamais vers « tout passe » : une
    config illisible ne doit pas devenir le moyen d'éteindre le contrôle.
    """
    set_config(project, JudgeCalibrationMinKappa=0.3, ZzzCleInconnue=1)
    write_judge(project, blocking=True, kappa=0.5, items=60)

    # Sanity : lisible (mode non strict), le seuil du projet s'applique.
    assert call(project)[0] == ALLOW

    monkeypatch.setenv("SDDA_CONFIG_STRICT", "1")   # la clé inconnue rend la config fatale
    code, err = call(project)
    assert code == DENY and "kappa >= 0.6" in err


# ---------------------------------------------------------------------------
# Périmètre et dégradation, par le chemin réel du harnais
# ---------------------------------------------------------------------------
def test_an_out_of_scope_agent_passes_even_with_an_uncalibrated_judge(
        project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`po-elicitor` en phase 0 n'a rien à faire d'un juge : le refuser paralyserait le pipeline."""
    write_judge(project, blocking=True, kappa=0.1, items=5)
    code, err = via_harness(monkeypatch, project, tool_name="Task",
                            tool_input={"subagent_type": "po-elicitor"})
    assert code == ALLOW, err
    assert "JUDGE_NOT_CALIBRATED" not in err


def test_an_in_scope_agent_is_refused(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_judge(project, blocking=True, kappa=0.1, items=5)
    code, err = via_harness(monkeypatch, project, tool_name="Task",
                            tool_input={"subagent_type": "qa-evals"})
    assert code == DENY and "JUDGE_NOT_CALIBRATED" in err


def test_no_agent_named_means_the_hook_rules(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Invocation manuelle ou CI : refuser de juger faute d'étiquette rendrait le filet inutile."""
    write_judge(project, blocking=True, kappa=0.1, items=5)
    code, err = via_harness(monkeypatch, project, tool_name="Task")
    assert code == DENY and "JUDGE_NOT_CALIBRATED" in err


def test_the_identity_is_read_at_the_root_as_well_as_under_tool_input(
        project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_judge(project, blocking=True, kappa=0.1, items=5)
    assert via_harness(monkeypatch, project, subagent_type="dev-agent")[0] == ALLOW
    assert via_harness(monkeypatch, project, subagent_type="dev-orchestration")[0] == DENY


def test_an_in_scope_agent_on_a_calibrated_project_passes(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_judge(project, blocking=True, kappa=0.8, items=60)
    code, err = via_harness(monkeypatch, project, tool_input={"subagent_type": "qa-tests"})
    assert code == ALLOW, err


def test_a_crash_inside_check_degrades_to_allow(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le scénario le plus coûteux : un hook cassé qui bloque, et qu'on désactive."""
    def broken(root: Path, data: dict) -> int:
        raise RuntimeError("cassé pour le test")

    monkeypatch.setattr(hook, "check", broken)
    code, err = via_harness(monkeypatch, project, tool_input={"subagent_type": "qa-evals"})
    assert code == ALLOW
    assert "AUTORISÉE" in err and "RuntimeError" in err
