"""G3 part `suites` : un écrivain qui existe vraiment, et G3 sur les seuls outils câblés.

Deux régressions du premier run réel (MISSION 1 SupportDesk) :

1. La part `suites` de G3 n'était écrivable que par `eval-runner --level L2`,
   qui charge des items JSONL depuis `dataset:`. Une suite d'outil déclare des
   CAS inline et pointe un export brut : rien à jouer, G3 jamais verte, et le
   hook `preflight_tool_gate` bloquait tout `dev-agent`.
2. `compute_status` exigeait un G3 pour chaque CONTRAT d'outil (21), quand
   l'IR n'en câble que 7 : la MISSION restait `Architected` pour toujours.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import run_main
from sdda_lib import paths
from sdda_lib.errors import Report
from sdda_lib.gate_reports import write_gate_report
from sdda_scripts import compute_status, run_tool_suites
from test_compute_status import _mission, _pass_g0_g1, _pass_g2, _status

TOOL = "1-invoice-lookup"
SUITE_ID = f"tool-{TOOL}"

SUITE = f"""id: "{SUITE_ID}"
level: "L2"
toolRef: "{TOOL}"
cases:
  - id: "happy-1"
  - id: "not-found"
  - id: "timeout"
"""

GREEN_TESTS = f'''import pytest

SUITE = "{SUITE_ID}"

@pytest.mark.parametrize("case", ["happy-1", "not-found"])
def test_generic(case):
    assert case

def test_timeout():
    case = "timeout"
    assert case
'''

RED_TESTS = f'''import pytest

SUITE = "{SUITE_ID}"

@pytest.mark.parametrize("case", ["happy-1"])
def test_generic(case):
    assert case

@pytest.mark.xfail(reason="défaut connu", strict=True)
def test_known_defect():
    assert False
'''


@pytest.fixture
def g2_project(project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _pass_g0_g1(project)
    _pass_g2(project)
    # L'interpréteur courant a pytest : pas besoin de l'environnement de l'app.
    monkeypatch.setenv("SDDA_TOOL_SUITES_PYTHON", sys.executable)
    suites = project / "workspace/pipeline/suites"
    suites.mkdir(parents=True, exist_ok=True)
    (suites / f"{SUITE_ID}.yaml").write_text(SUITE, encoding="utf-8")
    return project


def _tests_dir(root: Path) -> Path:
    from sdda_lib.layered_config import app_name

    d = paths.app_dir(root, app_name(root)) / "tools" / "tests"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _report(root: Path, tool: str) -> dict:
    return json.loads((paths.validation_dir(root) / f"G3-{tool}.suites.json").read_text(encoding="utf-8"))


def test_every_declared_case_played_and_green_writes_a_green_suites_part(g2_project: Path) -> None:
    (_tests_dir(g2_project) / "test_invoice_lookup.py").write_text(GREEN_TESTS, encoding="utf-8")
    code, out = run_main(run_tool_suites.main, ["--root", str(g2_project), "--mission", "1", "--tool", TOOL])
    assert code == 0, out
    report = _report(g2_project, TOOL)
    assert report["ok"] is True and report["part"] == "suites"
    # Épinglé sur la suite ET les tests : qu'un seul bouge, et la part est périmée.
    assert any(k.startswith("suite:") for k in report["pinnedHashes"])
    assert any(k.startswith("tests:") for k in report["pinnedHashes"])


def test_an_xfail_and_an_unplayed_case_make_the_part_red(g2_project: Path) -> None:
    (_tests_dir(g2_project) / "test_invoice_lookup.py").write_text(RED_TESTS, encoding="utf-8")
    code, out = run_main(run_tool_suites.main, ["--root", str(g2_project), "--mission", "1", "--tool", TOOL])
    assert code != 0
    messages = " ".join(e["message"] for e in _report(g2_project, TOOL)["errors"])
    assert "xfail" in messages                       # un défaut connu n'est pas un contrat tenu
    assert "`not-found`" in messages and "`timeout`" in messages   # déclarés, joués par personne


def test_a_suite_no_test_plays_is_red(g2_project: Path) -> None:
    code, _ = run_main(run_tool_suites.main, ["--root", str(g2_project), "--mission", "1", "--tool", TOOL])
    assert code != 0
    assert "aucun test ne joue" in _report(g2_project, TOOL)["errors"][0]["message"]


def test_g3_is_required_only_for_the_tools_the_ir_wires(g2_project: Path) -> None:
    """Un contrat d'outil qu'aucun agent n'appelle n'est ni construit ni exigé."""
    wired = compute_status.wired_tool_ids(g2_project, 1)
    contracts = sorted(p.name[: -len(".tool.md")]
                       for p in paths.contracts_dir(g2_project, "tools").glob("1-*.tool.md"))
    extra = paths.contracts_dir(g2_project, "tools") / "1-unwired-export.tool.md"
    extra.write_text((paths.contracts_dir(g2_project, "tools") / f"{contracts[0]}.tool.md").read_text(encoding="utf-8"),
                     encoding="utf-8")
    assert "1-unwired-export" not in compute_status.wired_tool_ids(g2_project, 1)
    for tool in wired:
        for part in ("contracts", "suites"):
            write_gate_report(g2_project, "G3", tool, Report(name="G3", target=tool), {}, part=part)
    write_gate_report(g2_project, "G4", "1-contracts-index", Report(name="G4", target="1-contracts-index"), {})
    _, data = _status(g2_project)
    assert _mission(data)["state"] == "Implemented", _mission(data)
