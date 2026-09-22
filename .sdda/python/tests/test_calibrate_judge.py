"""calibrate_judge — le script qui décide si un juge LLM a le droit de bloquer (P9).

Trois contrats : la **mesure** l'emporte sur le kappa déclaré quand les labels
sont là ; sous le seuil, le juge est rétrogradé en `advisory` et ne bloque plus
(sauf si le projet refuse ce repli) ; des labels synthétiques ne se rachètent
par aucun réglage.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import run_main
from sdda_lib import paths
from sdda_lib.gate_reports import report_path
from sdda_scripts import calibrate_judge as cj
from sdda_scripts import ir_compiler

FIXED_AT = "2026-09-20T10:00:00Z"
SUITE = "1-2-groundedness"
STACK = "workspace/stack/STACK.md"
CALIBRATION = "workspace/evals/calibration/groundedness.json"
LABELS = "workspace/datasets/calibration/groundedness-v1.jsonl"


@pytest.fixture
def compiled(project: Path) -> Path:
    """Le projet vert avec son IR compilé : la suite `1-2-groundedness` y est un llm-judge."""
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    return project


def write_labels(project: Path, *, n: int = 60, flip_every: int | None = 10,
                 constant_judge: bool = False, source: str | None = None) -> None:
    """60 items équilibrés, ~10 % de désaccord -> kappa 0,8 (accord brut 0,9)."""
    human = (["pass", "fail"] * (n // 2 + 1))[:n]
    judge = ["pass"] * n if constant_judge else list(human)
    if not constant_judge and flip_every:
        for i in range(0, n, flip_every):
            judge[i] = "fail" if judge[i] == "pass" else "pass"
    rows = []
    for k, (h, j) in enumerate(zip(human, judge)):
        item = {"id": f"i{k}", "human": h, "judge": j}
        if source:
            item["source"] = source
        rows.append(json.dumps(item))
    path = project / LABELS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def set_config(project: Path, **values) -> None:
    path = project / STACK
    text = path.read_text(encoding="utf-8")
    extra = "".join(f"{k}: {json.dumps(v) if isinstance(v, bool) else v}\n" for k, v in values.items())
    path.write_text(text.replace("AppName: SupportAssistant\n", "AppName: SupportAssistant\n" + extra, 1),
                    encoding="utf-8")


def patch_ir(project: Path, **fields) -> None:
    """Modifie la suite llm-judge de l'IR ; une valeur `None` retire la clé."""
    path = paths.ir_path(project, 1)
    ir = json.loads(path.read_text(encoding="utf-8"))
    for suite in ir["evaluation"]["suites"]:
        if suite["id"] == SUITE:
            for k, v in fields.items():
                if v is None:
                    suite.pop(k, None)
                else:
                    suite[k] = v
    path.write_text(json.dumps(ir, indent=2), encoding="utf-8")


def run_json(project: Path, *extra: str) -> tuple[int, dict]:
    code, out = run_main(cj.main, ["--root", str(project), "--json", *extra])
    return code, json.loads(out)


def classes(data: dict, severity: str) -> set[str]:
    return {f["class"] for f in data[severity]}


# ---------------------------------------------------------------------------
# Le cas nominal et ses deux lectures
# ---------------------------------------------------------------------------
def test_a_measured_kappa_above_threshold_is_a_green_verdict(compiled: Path) -> None:
    write_labels(compiled)
    code, data = run_json(compiled, "--no-report")
    assert code == 0, data
    assert data["ok"] is True and data["warnings"] == []
    (judge,) = data["judges"]
    assert judge["suiteId"] == SUITE and judge["grader"] == "groundedness"
    assert judge["verified"] is True and judge["calibrated"] is True and judge["advisory"] is False
    assert judge["items"] == 60
    assert judge["agreement"] == pytest.approx(0.8) and judge["rawAgreement"] == pytest.approx(0.9)
    assert judge["reason"] == "accord 0.800 sur 60 items"


def test_a_declared_kappa_without_labels_passes_but_is_said_unverified(compiled: Path) -> None:
    """La fixture ne porte qu'un résumé : on l'accepte, on ne le confond pas avec une mesure."""
    code, data = run_json(compiled, "--no-report")
    assert code == 0
    (judge,) = data["judges"]
    assert judge["verified"] is False and judge["calibrated"] is True
    assert judge["agreement"] == pytest.approx(0.71) and judge["items"] == 52
    assert classes(data, "warnings") == {"JUDGE_UNCALIBRATED"}
    assert any("déclaré sans labels vérifiables" in w["message"] for w in data["warnings"])


def test_the_measure_overrides_the_declared_kappa(compiled: Path) -> None:
    """Le résumé annonce 0,71 ; les labels montrent un juge constant. La mesure gagne."""
    write_labels(compiled, constant_judge=True)
    code, data = run_json(compiled, "--no-report")
    assert code == 0                                   # advisory par défaut : informe, ne bloque plus
    (judge,) = data["judges"]
    assert judge["verified"] is True and judge["calibrated"] is False and judge["advisory"] is True
    assert judge["agreement"] == pytest.approx(0.0)
    assert any("toujours la même valeur" in n for n in judge["notes"])
    assert any("-> advisory" in w["message"] for w in data["warnings"])


# ---------------------------------------------------------------------------
# Seuils : kappa, items, repli advisory
# ---------------------------------------------------------------------------
def test_a_stricter_kappa_threshold_demotes_the_judge_to_advisory(compiled: Path) -> None:
    write_labels(compiled)
    set_config(compiled, JudgeCalibrationMinKappa=0.95)
    code, data = run_json(compiled, "--no-report")
    assert code == 0
    (judge,) = data["judges"]
    assert judge["minAgreement"] == 0.95 and judge["calibrated"] is False
    assert judge["reason"].startswith("accord 0.800 < 0.95")
    assert data["errors"] == [] and classes(data, "warnings") == {"JUDGE_UNCALIBRATED"}


def test_without_the_advisory_fallback_an_uncalibrated_judge_blocks(compiled: Path) -> None:
    write_labels(compiled)
    set_config(compiled, JudgeCalibrationMinKappa=0.95, JudgeAdvisoryFallback=False)
    code, data = run_json(compiled, "--no-report")
    assert code == 1
    assert classes(data, "errors") == {"JUDGE_UNCALIBRATED"}
    assert any("non calibrée" in e["message"] for e in data["errors"])


def test_too_few_items_is_not_calibrated_however_good_the_agreement(compiled: Path) -> None:
    write_labels(compiled)
    set_config(compiled, JudgeCalibrationMinItems=100)
    _, data = run_json(compiled, "--no-report")
    (judge,) = data["judges"]
    assert judge["calibrated"] is False and judge["minItems"] == 100
    assert judge["reason"] == "60 items < 100 exigés"


def test_a_suite_declared_advisory_in_the_ir_never_fails_the_run(compiled: Path) -> None:
    write_labels(compiled)
    set_config(compiled, JudgeCalibrationMinKappa=0.95, JudgeAdvisoryFallback=False)
    patch_ir(compiled, advisory=True)
    code, data = run_json(compiled, "--no-report")
    assert code == 0
    (judge,) = data["judges"]
    assert judge["declaredAdvisory"] is True and judge["calibrated"] is False
    assert data["errors"] == []


# ---------------------------------------------------------------------------
# Ce qu'aucun réglage ne rachète
# ---------------------------------------------------------------------------
def test_synthetic_labels_block_even_with_the_advisory_fallback(compiled: Path) -> None:
    write_labels(compiled, source="llm")
    code, data = run_json(compiled, "--no-report")
    assert code == 1
    assert classes(data, "errors") == {"JUDGE_CALIBRATION_SYNTHETIC"}
    (judge,) = data["judges"]
    assert judge["labelsAreSynthetic"] is True and judge["calibrated"] is False


def test_synthetic_labels_block_even_on_an_advisory_suite(compiled: Path) -> None:
    write_labels(compiled, source="model")
    patch_ir(compiled, advisory=True)
    code, data = run_json(compiled, "--no-report")
    assert code == 1 and classes(data, "errors") == {"JUDGE_CALIBRATION_SYNTHETIC"}


# ---------------------------------------------------------------------------
# Jeu absent, référence absente, IR absent ou illisible
# ---------------------------------------------------------------------------
def test_a_missing_calibration_set_blocks_a_blocking_judge(compiled: Path) -> None:
    (compiled / CALIBRATION).unlink()
    code, data = run_json(compiled, "--no-report")
    assert code == 1
    assert classes(data, "errors") == {"JUDGE_UNCALIBRATED"}
    assert any("introuvable ou illisible" in e["message"] for e in data["errors"])
    (judge,) = data["judges"]
    assert judge["calibrated"] is False and judge["reason"] == "jeu absent"


def test_a_missing_calibration_set_only_warns_on_an_advisory_judge(compiled: Path) -> None:
    (compiled / CALIBRATION).unlink()
    patch_ir(compiled, advisory=True)
    code, data = run_json(compiled, "--no-report")
    assert code == 0
    assert data["errors"] == [] and classes(data, "warnings") == {"JUDGE_UNCALIBRATED"}


def test_a_broken_calibration_file_counts_as_absent(compiled: Path) -> None:
    (compiled / CALIBRATION).write_text("{ pas du json", encoding="utf-8")
    code, data = run_json(compiled, "--no-report")
    assert code == 1 and classes(data, "errors") == {"JUDGE_UNCALIBRATED"}


def test_a_judge_without_calibration_ref_is_refused(compiled: Path) -> None:
    patch_ir(compiled, judgeCalibrationRef=None)
    code, data = run_json(compiled, "--no-report")
    assert code == 1
    assert classes(data, "errors") == {"JUDGE_UNCALIBRATED"}
    assert any("sans `judgeCalibrationRef`" in e["message"] for e in data["errors"])
    assert data["judges"] == []


def test_no_ir_is_a_distinct_error(project: Path) -> None:
    code, data = run_json(project, "--no-report")
    assert code == 1
    assert classes(data, "errors") == {"IR_NOT_FOUND"}
    assert data["judges"] == []


def test_an_unreadable_ir_is_reported_not_swallowed(compiled: Path) -> None:
    paths.ir_path(compiled, 1).write_text("{ pas du json", encoding="utf-8")
    code, data = run_json(compiled, "--no-report")
    assert code == 1 and classes(data, "errors") == {"IR_INVALID"}


def test_a_suite_with_another_grader_is_not_a_judge(compiled: Path) -> None:
    ir = json.loads(paths.ir_path(compiled, 1).read_text(encoding="utf-8"))
    suites = cj.judge_suites(ir)
    assert [s["id"] for s in suites] == [SUITE]
    assert all(s["grader"] == "llm-judge" for s in suites)


# ---------------------------------------------------------------------------
# Ligne de commande : filtre, sorties, rapport
# ---------------------------------------------------------------------------
def test_grader_filter_selects_by_substring(compiled: Path) -> None:
    write_labels(compiled)
    _, data = run_json(compiled, "--no-report", "--grader", "groundedness")
    assert [j["suiteId"] for j in data["judges"]] == [SUITE]

    code, data = run_json(compiled, "--no-report", "--grader", "inexistant")
    assert code == 0 and data["judges"] == [] and data["ok"] is True


def test_json_output_has_the_shape_the_gate_reads(compiled: Path) -> None:
    write_labels(compiled)
    _, data = run_json(compiled, "--no-report")
    assert data["name"] == "G5.calibration"
    assert {"name", "target", "ok", "errors", "warnings", "judges"} <= set(data)
    (judge,) = data["judges"]
    assert set(judge) == {
        "grader", "items", "scale", "agreement", "rawAgreement", "minItems", "minAgreement",
        "calibrated", "advisory", "reason", "confusion", "labelsAreSynthetic", "notes",
        "suiteId", "declaredAdvisory", "verified",
    }
    assert judge["confusion"] == {"pass": {"pass": 24, "fail": 6}, "fail": {"fail": 30}}


def test_text_output_names_the_state_of_each_judge(compiled: Path) -> None:
    write_labels(compiled)
    code, out = run_main(cj.main, ["--root", str(compiled), "--no-report"])
    assert code == 0
    assert "calibré" in out and SUITE in out and "0.800" in out

    write_labels(compiled, constant_judge=True)
    _, out = run_main(cj.main, ["--root", str(compiled), "--no-report"])
    assert "ADVISORY" in out


def test_the_gate_report_is_written_as_the_calibration_part_of_g5(compiled: Path) -> None:
    write_labels(compiled)
    code, _ = run_json(compiled)
    assert code == 0
    path = report_path(compiled, "G5", "calibration", part="calibration")
    assert path.is_file()
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["gate"] == "G5" and written["part"] == "calibration" and written["ok"] is True


def test_a_red_verdict_is_written_red(compiled: Path) -> None:
    write_labels(compiled, source="llm")
    assert run_json(compiled)[0] == 1
    written = json.loads(report_path(compiled, "G5", "calibration", part="calibration").read_text(encoding="utf-8"))
    assert written["ok"] is False
    assert {e["class"] for e in written["errors"]} == {"JUDGE_CALIBRATION_SYNTHETIC"}


def test_no_report_writes_nothing(compiled: Path) -> None:
    write_labels(compiled)
    run_json(compiled, "--no-report")
    assert not report_path(compiled, "G5", "calibration", part="calibration").exists()


def test_the_python_api_returns_the_report_and_the_outcomes(compiled: Path) -> None:
    write_labels(compiled)
    report, outcomes = cj.run(compiled, write=False)
    assert report.ok and report.name == "G5.calibration"
    assert [o["suiteId"] for o in outcomes] == [SUITE]
    assert not report_path(compiled, "G5", "calibration", part="calibration").exists()
