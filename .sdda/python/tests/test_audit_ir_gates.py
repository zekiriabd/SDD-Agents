"""Audit IR / validateurs du 2026-09-25 — chaque constat, le test qui échouait avant.

Un test par correctif, nommé d'après ce qu'il REFUSE ou ce qu'il n'accorde
plus. Les preuves de l'audit ont été reproduites sur une copie de la fixture
`project_ok` ; elles sont ici rejouées sur `make_project(tmp_path)`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import run_main  # type: ignore
from sdda_lib import hashing, paths
from sdda_lib.errors import Report
from sdda_lib.eval_stats import parse_threshold
from sdda_lib.layered_config import read_layered_config
from sdda_scripts import (compute_status, ir_compiler, validate_cap, validate_ir,
                          validate_tool_contract)

FIXED_AT = "2026-09-25T00:00:00Z"
CAP = "1-1-ClassifyIntent"


def _cap_path(root: Path) -> Path:
    return paths.caps_dir(root) / f"{CAP}.md"


def _compile(root: Path) -> dict:
    ir_compiler.compile_to_file(root, 1, compiled_at=FIXED_AT)
    return ir_compiler.load_ir(paths.ir_path(root, 1))


def _suite(ir: dict, sid: str) -> dict:
    return next(s for s in ir["evaluation"]["suites"] if s["id"] == sid)


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} absent de {path.name}"
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# C1 — le comparateur du seuil voyage jusqu'au runner
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    (">= 0.95", 0.95), ("0.95", 0.95), ("<= 5%", "<= 0.05"), ("< 0.1", "< 0.1"),
    ("= 1", "== 1"), (">= 85% sur le golden", 0.85), ("<= 2000 ms", "<= 2000"),
])
def test_normalize_threshold_keeps_the_comparator_and_converts_percent(raw, expected) -> None:
    assert validate_cap.normalize_threshold(raw) == expected


def test_an_upper_bound_ac_is_not_compiled_as_a_lower_bound(project: Path) -> None:
    _replace(_cap_path(project), "threshold: >= 0.95", "threshold: <= 5%")
    ir = _compile(project)
    suite = _suite(ir, "1-1-routing_accuracy")
    assert suite["threshold"] == "<= 0.05"
    t = parse_threshold(suite["threshold"])
    # Avant : `5.0`, lu `>= 5` — aucun score ne passait ; et `<= 0.05` sans `%`
    # devenait `>= 0.05`, où un taux d'hallucination de 0,9 passait.
    assert t.holds(0.01) and not t.holds(0.9)
    report = validate_ir.validate_ir_data(ir, root=project, config=read_layered_config(project))
    assert not [f for f in report.errors if f.cls == "IR_INVALID"], report.render_text()


def test_a_numeric_lower_bound_stays_a_number(project: Path) -> None:
    ir = _compile(project)
    assert _suite(ir, "1-1-routing_accuracy")["threshold"] == 0.95


def test_a_system_suite_with_a_string_threshold_is_projected(project: Path) -> None:
    (paths.suites_dir(project) / "1-latency.yaml").write_text(
        "id: 1-latency\nlevel: L7\ngrader: latency\nthreshold: \"<= 3000\"\nruns: 3\n"
        "dataset: workspace/pipeline/datasets/golden/billing-v1.jsonl\n", encoding="utf-8")
    ir = _compile(project)
    assert _suite(ir, "1-latency")["threshold"] == "<= 3000"


# ---------------------------------------------------------------------------
# C2 / C3 — G2 n'exige pas une calibration qui naît en PHASE 6a
# ---------------------------------------------------------------------------
def test_g2_is_not_red_when_the_calibration_set_does_not_exist_yet(project: Path) -> None:
    (paths.calibration_dir(project) / "groundedness.json").unlink()
    ir = _compile(project)
    report = validate_ir.validate_ir_data(ir, root=project, config=read_layered_config(project))
    assert report.ok, report.render_text()
    assert any(f.cls == "JUDGE_UNCALIBRATED" for f in report.warnings)


def test_g2_reads_the_inline_calibration_form_like_g5(project: Path) -> None:
    items = [{"id": f"i{i}", "human": "pass" if i % 2 else "fail", "judge": "pass" if i % 2 else "fail"} for i in range(60)]
    (paths.calibration_dir(project) / "groundedness.json").write_text(
        json.dumps({"grader": "groundedness", "scale": "ordinal", "items": items}), encoding="utf-8")
    ir = _compile(project)
    report = validate_ir.validate_ir_data(ir, root=project, config=read_layered_config(project))
    assert report.ok, report.render_text()
    assert not [f for f in report.findings if f.cls == "JUDGE_UNCALIBRATED"], report.render_text()


def test_g2_still_requires_a_declared_calibration_reference(project: Path) -> None:
    ir = _compile(project)
    del _suite(ir, "1-2-groundedness")["judgeCalibrationRef"]
    report = validate_ir.validate_ir_data(ir, root=project, config=read_layered_config(project))
    assert "JUDGE_UNCALIBRATED" in {f.cls for f in report.errors}


def test_validate_ir_accepts_a_positional_ir_path(project: Path) -> None:
    _compile(project)
    code, out = run_main(validate_ir.main, ["workspace/.sys/.ir/1-system.ir.json", "--root", str(project), "--no-report"])
    assert code == 0, out


# ---------------------------------------------------------------------------
# C4 / M5 / M12 — G3 : findings attribués à l'émission, épingles restreintes
# ---------------------------------------------------------------------------
def _write_tool_code(root: Path, marker: str) -> None:
    code = paths.app_src_root(root, "SupportAssistant") / "tools" / "invoice.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text(f'side_effect_class = "{marker}"\n\n\ndef invoice_lookup(invoice_id: str) -> dict:\n    return {{}}\n',
                    encoding="utf-8")


def _g3(root: Path, tool: str) -> dict:
    return json.loads((paths.validation_dir(root) / f"G3-{tool}.contracts.json").read_text(encoding="utf-8"))


def test_a_code_contract_mismatch_is_red_in_the_tool_report(project: Path) -> None:
    ir = _compile(project)
    _write_tool_code(project, "write-destructive")
    validate_tool_contract.run(project, ir, report=Report(name="G3", target=str(project)))
    gate = _g3(project, "1-invoice-lookup")
    assert gate["ok"] is False
    assert "TOOL_CONTRACT_INCONSISTENT" in {e["class"] for e in gate["errors"]}


def test_write_scoped_is_a_side_effect_class_the_code_check_recognises() -> None:
    assert "write-scoped" in validate_tool_contract.SIDE_EFFECTS
    assert "idempotent-write" not in validate_tool_contract.SIDE_EFFECTS


def test_an_incomplete_envelope_lands_in_a_tool_report(project: Path) -> None:
    ir = _compile(project)
    ir["dataAccess"] = [{"id": "1-data-orders", "binding": {"strategy": "view-per-agent"},
                         "exposedTo": ["1-billing-specialist"], "envelope": {"role": "readonly"}}]
    validate_tool_contract.run(project, ir, report=Report(name="G3", target=str(project)))
    # Aucun outil `1-data-orders` câblé : l'erreur tombe dans TOUS les rapports.
    assert _g3(project, "1-invoice-lookup")["ok"] is False
    assert "DB_ENVELOPE_MISSING" in {e["class"] for e in _g3(project, "1-invoice-lookup")["errors"]}


def test_recompiling_after_a_prompt_edit_does_not_stale_g3(project: Path) -> None:
    ir = _compile(project)
    validate_tool_contract.run(project, ir, report=Report(name="G3", target=str(project)))
    report = _g3(project, "1-invoice-lookup")
    prompt = paths.prompts_dir(project, "SupportAssistant") / "billing-specialist.system.md"
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\nUne ligne de plus.\n", encoding="utf-8")
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    assert compute_status.stale_keys(project, report) == []


def test_editing_the_tool_contract_stales_g3(project: Path) -> None:
    ir = _compile(project)
    validate_tool_contract.run(project, ir, report=Report(name="G3", target=str(project)))
    report = _g3(project, "1-invoice-lookup")
    contract = paths.contracts_dir(project, "tools") / "1-invoice-lookup.tool.md"
    contract.write_text(contract.read_text(encoding="utf-8") + "\n<!-- édition -->\n", encoding="utf-8")
    assert compute_status.stale_keys(project, report) == ["spec:workspace/pipeline/contracts/tools/1-invoice-lookup.tool.md"]


def test_rewriting_status_does_not_stale_g3(project: Path) -> None:
    ir = _compile(project)
    validate_tool_contract.run(project, ir, report=Report(name="G3", target=str(project)))
    report = _g3(project, "1-invoice-lookup")
    contract = paths.contracts_dir(project, "tools") / "1-invoice-lookup.tool.md"
    text = contract.read_text(encoding="utf-8")
    if "Status:" in text:
        import re
        contract.write_text(re.sub(r"^Status:.*$", "Status: Implemented", text, count=1, flags=re.M), encoding="utf-8")
    assert compute_status.stale_keys(project, report) == []
    assert hashing.is_hash_ref(report["pinnedHashes"]["irtool:1-invoice-lookup"])
