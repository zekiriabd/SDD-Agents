"""L'ordre du pipeline : le holdout naît après l'IR, et il doit être mesuré.

Ces tests couvrent le défaut le plus coûteux trouvé à l'audit : `ir_compiler`
exigeait `workspace/pipeline/datasets/holdout/mission-{n}-*.jsonl` pour compiler, alors
que ce jeu est produit en PHASE 6a, après la compilation de PHASE 2 — et que
`qa-evals` lit l'IR pour savoir quoi produire. La boucle se fermait sur
elle-même : aucune MISSION neuve ne franchissait G2, donc aucune n'atteignait la
phase qui aurait écrit le fichier réclamé.

Le second défaut est jumeau et passait inaperçu derrière le premier : AUCUNE
suite `L9` n'était jamais compilée. `eval_runner` mappe pourtant `L9` sur la
part `acceptance` de G8. La dernière gate du pipeline, celle qui décide si le
produit est livrable, n'avait donc aucune exécution capable de la rendre verte.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_scripts import ir_compiler, validate_datasets, validate_ir, validate_mission


def _holdouts(root: Path) -> list[Path]:
    return sorted((root / "workspace/pipeline/datasets/holdout").glob("*.jsonl"))


def _drop_holdout(root: Path) -> None:
    for p in _holdouts(root):
        p.unlink()


# ---------------------------------------------------------------------------
# La compilation
# ---------------------------------------------------------------------------
def test_ir_compiles_without_holdout_because_phase_2_precedes_phase_6a(project: Path) -> None:
    _drop_holdout(project)
    ir, report = ir_compiler.compile_mission(project, 1)
    assert report.ok, [f.message for f in report.errors]
    assert "holdout" not in ir["evaluation"]
    assert not [s for s in ir["evaluation"]["suites"] if s["level"] == "L9"]


def test_the_holdout_makes_the_acceptance_suite_appear(project: Path) -> None:
    ir, report = ir_compiler.compile_mission(project, 1)
    assert report.ok
    assert ir["evaluation"]["holdout"] == "workspace/pipeline/datasets/holdout/mission-1-v1.jsonl"

    l9 = [s for s in ir["evaluation"]["suites"] if s["level"] == "L9"]
    assert len(l9) == 1, "une seule suite d'acceptation par mission"
    suite = l9[0]
    assert suite["id"] == "1-acceptance"
    assert suite["dataset"] == ir["evaluation"]["holdout"]
    assert suite["grader"] == "exact"          # `- Grader:` de ## Quantified Goal
    assert suite["threshold"] == 0.75          # `- Target: >= 0.75 sur le holdout`
    assert suite["runs"] >= 1


def test_the_holdout_is_a_compiled_source_so_its_arrival_stales_the_ir(project: Path) -> None:
    """Sans cette empreinte, l'IR compilé avant le holdout se déclarerait frais
    pour toujours — donc sans suite d'acceptation, donc avec une G8 inatteignable."""
    _drop_holdout(project)
    before = ir_compiler.source_hashes(project, 1)
    assert before["holdoutHash"] == ""

    ir_compiler.compile_to_file(project, 1)
    from sdda_scripts import check_ir_freshness

    assert check_ir_freshness.freshness(project, 1)["fresh"] is True

    # PHASE 6a : qa-evals écrit le jeu de verdict.
    (project / "workspace/pipeline/datasets/holdout/mission-1-v1.jsonl").write_text(
        '{"id": "h1", "input": "x", "expected": "y"}\n', encoding="utf-8")

    state = check_ir_freshness.freshness(project, 1)
    assert state["fresh"] is False
    assert "holdoutHash" in state["moved"]


def test_a_second_holdout_is_still_refused(project: Path) -> None:
    """Deux jeux de verdict, c'est choisir le verdict après coup."""
    (project / "workspace/pipeline/datasets/holdout/mission-1-v2.jsonl").write_text(
        '{"id": "h9", "input": "x", "expected": "y"}\n', encoding="utf-8")
    with pytest.raises(ir_compiler.CompileError) as exc:
        ir_compiler.compile_mission(project, 1)
    assert any("holdouts candidats" in f.message for f in exc.value.report.errors)


def test_an_unknown_goal_grader_is_refused_not_guessed(project: Path) -> None:
    mission = next((project / "workspace/pipeline/missions").glob("1-*.md"))
    mission.write_text(mission.read_text(encoding="utf-8").replace("- Grader: exact", "- Grader: vibes"),
                       encoding="utf-8")
    with pytest.raises(ir_compiler.CompileError) as exc:
        ir_compiler.compile_mission(project, 1)
    assert any("Grader" in f.message for f in exc.value.report.errors)


# ---------------------------------------------------------------------------
# La contrepartie : l'exigence déplacée, pas levée
# ---------------------------------------------------------------------------
def test_validate_ir_refuses_a_holdout_that_nothing_measures(project: Path) -> None:
    ir, _ = ir_compiler.compile_mission(project, 1)
    ir["evaluation"]["suites"] = [s for s in ir["evaluation"]["suites"] if s["level"] != "L9"]
    path = project / "workspace/.sys/.ir/1-system.ir.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")

    code, out = run_main(validate_ir.main, ["--root", str(project), "--ir", str(path), "--no-report"])
    assert code == 1 and "ACCEPTANCE_SUITE_MISSING" in out


def test_g8_datasets_requires_the_holdout_at_the_moment_it_must_exist(project: Path) -> None:
    _drop_holdout(project)
    code, out = run_main(validate_datasets.main, ["--root", str(project), "--mission", "1", "--no-report"])
    assert code == 1 and "HOLDOUT_SET_MISSING" in out


def test_a_phase_prerequisite_only_requires_what_it_names(project: Path) -> None:
    """`/sdda-build` vérifie le golden de retrieval AVANT que le holdout existe."""
    _drop_holdout(project)
    code, _ = run_main(validate_datasets.main,
                       ["--root", str(project), "--mission", "1", "--no-report", "--require", "golden"])
    assert code == 0


def test_min_items_raises_the_floor_only_for_the_required_kinds(project: Path) -> None:
    code, out = run_main(validate_datasets.main,
                         ["--root", str(project), "--mission", "1", "--no-report",
                          "--require", "golden", "--min-items", "9999"])
    assert code == 1 and "EVAL_DATASET_TOO_SMALL" in out
    # Le jeu de calibration n'est pas concerné : il répond à JudgeCalibrationMinItems.
    assert "calibration/" not in out.split("EVAL_DATASET_TOO_SMALL")[1].split("\n")[0]


def test_an_unknown_required_kind_is_named_not_ignored(project: Path) -> None:
    code, out = run_main(validate_datasets.main,
                         ["--root", str(project), "--mission", "1", "--no-report", "--require", "goldenn"])
    assert code == 1 and "goldenn" in out


# ---------------------------------------------------------------------------
# G0 : l'objectif chiffré dit COMBIEN, le grader dit COMMENT
# ---------------------------------------------------------------------------
def test_a_mission_without_a_grader_warns_but_stays_valid(project: Path) -> None:
    mission = next((project / "workspace/pipeline/missions").glob("1-*.md"))
    mission.write_text(mission.read_text(encoding="utf-8").replace("\n- Grader: exact", ""), encoding="utf-8")
    code, out = run_main(validate_mission.main, ["--root", str(project), "--mission", "1", "--no-report"])
    assert code == 0, "G0 ne bloque pas : la mesure de l'objectif se déclare, elle ne s'improvise pas ici"
    assert "Grader" in out and "WARN" in out
