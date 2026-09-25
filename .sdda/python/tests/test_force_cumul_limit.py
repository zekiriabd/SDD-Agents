"""`MaxBypassesPerRun` gouverne le cumul — il était déclaré et codé en dur à 2.

Une équipe qui l'abaissait à 0, ou un projet qui le montait à 2, ne changeait
rien : `preflight_force_cumul` comparait `len(cumul) >= 2`. La clé est lue
dans la config en couches (donc protégée security-down), et le refus la nomme.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import make_project
from sdda_scripts import preflight_force_cumul as pfc

SCRIPT = Path(pfc.__file__).resolve()
STACK = Path("workspace") / "stack" / "STACK.md"


def _set_limit(project: Path, limit: int) -> Path:
    stack = project / STACK
    text = stack.read_text(encoding="utf-8")
    assert "AppName: SupportAssistant\n" in text
    stack.write_text(text.replace("AppName: SupportAssistant\n", f"AppName: SupportAssistant\nMaxBypassesPerRun: {limit}\n", 1),
                     encoding="utf-8")
    return project


def _run(project: Path, *argv: str) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SDDA_BYPASS") and k != "SDDA_ALLOW_FORCE"}
    env["SDDA_BYPASS_REASON"] = "test du cumul"
    proc = subprocess.run([sys.executable, str(SCRIPT), *argv], cwd=project, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout + proc.stderr


def test_the_limit_comes_from_the_layered_config(project: Path) -> None:
    assert pfc.max_bypasses(project) == pfc.DEFAULT_MAX_BYPASSES == 1
    assert pfc.max_bypasses(_set_limit(project, 2)) == 2


def test_two_levers_exceed_the_default_and_the_refusal_names_the_key(project: Path) -> None:
    code, out = _run(project, "--force", "--env-bypasses", "SDDA_BYPASS_TOOL_GATE=1")
    assert code == 1 and pfc.CLS_CUMUL in out and "MaxBypassesPerRun = 1" in out, out


def test_a_project_that_raises_the_limit_is_obeyed(project: Path) -> None:
    code, out = _run(_set_limit(project, 2), "--force", "--env-bypasses", "SDDA_BYPASS_TOOL_GATE=1")
    assert code == 0 and "2 contournement(s) assumé(s)" in out, out


def test_a_zero_limit_refuses_even_a_single_force(project: Path) -> None:
    code, out = _run(_set_limit(project, 0), "--force")
    assert code == 1 and pfc.CLS_CUMUL in out and "MaxBypassesPerRun = 0" in out, out
