"""compute_status — l'état est un fait dérivé des gates (R1..R5)."""
from __future__ import annotations

import json
from pathlib import Path

from conftest import make_project, run_main
from sdda_lib import markdown_io, paths
from sdda_lib.errors import Report
from sdda_lib.gate_reports import write_gate_report
from sdda_scripts import compute_status, estimate_budget, ir_compiler, validate_cap, validate_ir, validate_mission, validate_topology

FIXED_AT = "2026-09-20T10:00:00Z"


def _status(root: Path, *extra: str) -> tuple[int, dict]:
    code, out = run_main(compute_status.main, ["--root", str(root), "--json", *extra])
    return code, json.loads(out)


def _mission(data: dict) -> dict:
    return data["data"]["missions"][0]


def _pass_g0_g1(root: Path) -> None:
    assert run_main(validate_mission.main, ["--root", str(root)])[0] == 0
    assert run_main(validate_cap.main, ["--root", str(root)])[0] == 0


def _pass_g2(root: Path) -> None:
    assert run_main(validate_topology.main, ["--root", str(root)])[0] == 0
    ir_compiler.compile_to_file(root, 1, compiled_at=FIXED_AT)
    assert run_main(validate_ir.main, ["--root", str(root), "--mission", "1"])[0] == 0
    assert run_main(estimate_budget.main, ["--root", str(root), "--mission", "1"])[0] == 0


def test_no_reports_means_draft(project: Path) -> None:
    code, data = _status(project)
    assert code == 0
    m = _mission(data)
    assert m["state"] == "Draft" and all(c["state"] == "Draft" for c in m["caps"])
    assert m["gates"]["G0"] == "absent"


def test_g0_and_g1_green_means_specified(project: Path) -> None:
    _pass_g0_g1(project)
    _, data = _status(project)
    m = _mission(data)
    assert m["state"] == "Specified" and [c["state"] for c in m["caps"]] == ["Specified", "Specified"]


def test_full_g2_chain_means_architected(project: Path) -> None:
    _pass_g0_g1(project)
    _pass_g2(project)
    _, data = _status(project)
    m = _mission(data)
    assert m["state"] == "Architected", m
    assert m["gates"]["G2"] == "green" and m["gates"]["G3"] == "absent"
    # La revue de plan est conditionnelle : absente, Planned n'est pas accordé (aucun saut d'état)…
    assert m["gates"]["PLAN"] == "absent"
    # … mais G3/G4 verts suffisent ensuite pour Implemented.
    # G3 est composite : `contracts` (le contrat tient) ET `suites` (les tests
    # L2 passent). Un outil dont le contrat est propre mais dont les tests ne
    # tournent pas n'est pas un outil câblable.
    for tool in ("1-invoice-lookup", "1-zendesk-create-ticket"):
        for part in ("contracts", "suites"):
            write_gate_report(project, "G3", tool, Report(name="G3", target=tool), {}, part=part)
    write_gate_report(project, "G4", "1-contracts-index", Report(name="G4", target="1-contracts-index"), {})
    _, data = _status(project)
    assert _mission(data)["state"] == "Implemented"


def test_g2_needs_all_three_parts(project: Path) -> None:
    _pass_g0_g1(project)
    assert run_main(validate_topology.main, ["--root", str(project)])[0] == 0    # part topology seule
    _, data = _status(project)
    m = _mission(data)
    assert m["state"] == "Specified" and m["gates"]["G2"] == "absent"


# -- R1 : un Status non étayé est écrasé --------------------------------------------------
def test_unbacked_status_is_reported_and_overwritten(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_status_unbacked")
    cap = root / "workspace/feats/caps/1-1-ClassifyIntent.md"
    assert "Status: Tested" in cap.read_text(encoding="utf-8")
    code, out = run_main(compute_status.main, ["--root", str(root)])
    assert code == 1
    assert "[STATUS_UNBACKED]" in out and "Tested" in out
    text = cap.read_text(encoding="utf-8")
    assert "Status: Draft" in text and "Status: Tested" not in text
    # Une fois écrasé, le second passage est propre.
    assert run_main(compute_status.main, ["--root", str(root)])[0] == 0


def test_a_stale_blocked_header_is_cleared_when_gates_turn_green(project: Path) -> None:
    """`Blocked` écrit sur une gate rouge s'efface quand plus aucun rapport ne l'étaye.

    `Blocked` a le rang le plus bas : la règle « écraser si le fichier prétend
    plus haut » ne le touchait jamais, et un artefact débloqué restait `Blocked`
    à vie dans son en-tête.
    """
    mission = next((project / "workspace/feats/missions").glob("1-*.md"))
    text = mission.read_text(encoding="utf-8")
    mission.write_text(markdown_io.replace_header_field(text, "Status", "Blocked"), encoding="utf-8", newline="\n")
    code, out = run_main(compute_status.main, ["--root", str(project), "--mission", "1"])
    assert code == 0, out
    assert "Status: Blocked" not in mission.read_text(encoding="utf-8")
    assert "[STATUS_UNBACKED]" in out


def test_no_write_keeps_the_file_but_still_reports(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_status_unbacked")
    cap = root / "workspace/feats/caps/1-1-ClassifyIntent.md"
    code, out = run_main(compute_status.main, ["--root", str(root), "--no-write"])
    assert code == 1 and "[STATUS_UNBACKED]" in out
    assert "Status: Tested" in cap.read_text(encoding="utf-8")


def test_human_decision_states_are_honored(project: Path) -> None:
    cap = project / "workspace/feats/caps/1-1-ClassifyIntent.md"
    cap.write_text(cap.read_text(encoding="utf-8").replace("Status: Draft", "Status: Deferred"), encoding="utf-8")
    code, data = _status(project)
    assert code == 0
    assert _mission(data)["caps"][0]["state"] == "Deferred"


# -- R2 : un hash épinglé qui bouge périme le rapport -----------------------------------------
def test_pinned_hash_move_regresses_state(project: Path) -> None:
    _pass_g0_g1(project)
    cap = project / "workspace/feats/caps/1-2-ExplainInvoiceLine.md"
    cap.write_text(cap.read_text(encoding="utf-8").replace("ne couvre pas les factures multi-devises", "ne couvre pas les avoirs"), encoding="utf-8")
    code, data = _status(project)
    m = _mission(data)
    assert m["caps"][1]["state"] == "Draft" and m["caps"][1]["gates"]["G1"] == "stale"
    assert m["state"] == "Draft"                    # R3 : minimum des CAPs
    assert any(w["class"] == "STATUS_PINNED_HASH_MOVED" for w in data["warnings"])


# -- R3 : le parent est le minimum des enfants --------------------------------------------------
def test_mission_is_minimum_of_caps(project: Path) -> None:
    _pass_g0_g1(project)
    (paths.validation_dir(project) / "G1-1-1-ClassifyIntent.json").unlink()
    _, data = _status(project)
    m = _mission(data)
    assert [c["state"] for c in m["caps"]] == ["Draft", "Specified"]
    assert m["state"] == "Draft"


def test_red_gate_blocks_with_its_classes(project: Path) -> None:
    _pass_g0_g1(project)
    red = Report(name="G1", target="1-1-ClassifyIntent")
    red.error("AC_NOT_EVALUABLE", "AC-1 en prose", "réécrire l'AC")
    write_gate_report(project, "G1", "1-1-ClassifyIntent", red, {})
    _, data = _status(project)
    m = _mission(data)
    assert m["caps"][0]["state"] == "Blocked" and m["caps"][0]["blockedBy"] == ["AC_NOT_EVALUABLE"]
    assert m["state"] == "Blocked" and "AC_NOT_EVALUABLE" in m["blockedBy"]


def test_a_green_g8_datasets_part_alone_does_not_approve(project: Path) -> None:
    _pass_g0_g1(project)
    ok = Report(name="datasets", target="1-SupportAssistant")
    write_gate_report(project, "G8", "1-SupportAssistant", ok, {}, part="datasets")
    _, data = _status(project)
    assert _mission(data)["state"] == "Specified"


# -- R4 / --require-gate ----------------------------------------------------------------------------
def test_confidence_escalation_is_flagged(project: Path) -> None:
    mission = project / "workspace/feats/missions/1-SupportAssistant.md"
    mission.write_text(mission.read_text(encoding="utf-8").replace("Confidence: high", "Confidence: medium"), encoding="utf-8")
    _, data = _status(project)
    assert any(w["class"] == "CONFIDENCE_ESCALATION" for w in data["warnings"])


def test_require_gate_fails_when_not_passed(project: Path) -> None:
    code, out = run_main(compute_status.main, ["--root", str(project), "--require-gate", "G1"])
    assert code == 1 and "[CAP_GATE_NOT_PASSED]" in out
    _pass_g0_g1(project)
    code, out = run_main(compute_status.main, ["--root", str(project), "--require-gate", "G1"])
    assert code == 0, out


def test_text_output_is_a_tree(project: Path) -> None:
    _pass_g0_g1(project)
    code, out = run_main(compute_status.main, ["--root", str(project)])
    assert code == 0
    assert out.startswith("MISSION 1-SupportAssistant")
    assert "CAP 1-1-ClassifyIntent" in out and "Specified" in out
