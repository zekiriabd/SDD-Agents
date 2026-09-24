"""`--mission {n}` : la forme qu'emploient les commandes.

Les neuf commandes du pipeline ne connaissent qu'un numéro de MISSION, jamais un
chemin. Les validateurs n'acceptaient que des chemins positionnels : la ligne
écrite dans `/sdda-mission` échouait en `unrecognized arguments`. Ce décalage
entre la commande et le script est le genre de dette qu'on ne découvre qu'en
exécutant réellement le pipeline — d'où ces tests.
"""
from __future__ import annotations

from pathlib import Path

from conftest import make_project, run_main  # type: ignore
from sdda_scripts import validate_cap, validate_mission, validate_topology

BROKEN_MISSION = """# MISSION: Autre

MISSION ID: 2-Autre
Status: Draft
"""


def _with_broken_second_mission(tmp_path: Path) -> Path:
    root = make_project(tmp_path)
    (root / "workspace" / "pipeline" / "missions" / "2-Autre.md").write_text(BROKEN_MISSION, encoding="utf-8")
    return root


def test_mission_flag_isolates_one_mission_from_a_broken_neighbour(tmp_path: Path) -> None:
    root = _with_broken_second_mission(tmp_path)
    assert run_main(validate_mission.main, ["--root", str(root), "--mission", "1", "--no-report"])[0] == 0

    code, out = run_main(validate_mission.main, ["--root", str(root), "--mission", "2", "--no-report"])
    assert code == 1 and "2-Autre" in out


def test_without_the_flag_every_mission_is_validated(tmp_path: Path) -> None:
    root = _with_broken_second_mission(tmp_path)
    code, out = run_main(validate_mission.main, ["--root", str(root), "--no-report"])
    assert code == 1 and "2-Autre" in out  # la voisine cassée est vue


def test_an_unknown_mission_number_is_an_error_not_a_silent_pass(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    code, out = run_main(validate_mission.main, ["--root", str(root), "--mission", "9", "--no-report"])
    assert code == 1 and "aucune MISSION" in out


def test_cap_and_topology_accept_the_same_flag(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    assert run_main(validate_cap.main, ["--root", str(root), "--mission", "1", "--no-report"])[0] == 0
    assert run_main(validate_topology.main, ["--root", str(root), "--mission", "1", "--no-report"])[0] == 0

    code, out = run_main(validate_cap.main, ["--root", str(root), "--mission", "9", "--no-report"])
    assert code == 1 and "aucune CAP" in out
    code, out = run_main(validate_topology.main, ["--root", str(root), "--mission", "9", "--no-report"])
    assert code == 1 and "aucune" in out


def test_positional_paths_still_work_for_manual_use(tmp_path: Path) -> None:
    root = _with_broken_second_mission(tmp_path)
    ok = run_main(validate_mission.main, ["--root", str(root), "--no-report",
                                          str(root / "workspace/pipeline/missions/1-SupportAssistant.md")])
    broken = run_main(validate_mission.main, ["--root", str(root), "--no-report",
                                              str(root / "workspace/pipeline/missions/2-Autre.md")])
    assert ok[0] == 0 and broken[0] == 1
