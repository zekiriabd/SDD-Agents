"""Le second audit : ce que la première passe n'avait pas vu.

Une fois le pipeline capable d'aboutir, un audit source par source a trouvé
une seconde couche de défauts, tous de la même famille que la première — des
contrôles qui se croient actifs. Chaque test ici fixe un mécanisme, et échoue
si le contrôle redevient décoratif.
"""
from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from conftest import run_main

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdda_hooks"))

import _hook  # noqa: E402
from sdda_hooks import preflight_agent_bounds  # noqa: E402
from sdda_lib.eval_stats import ItemResult, RunResult  # noqa: E402
from sdda_scripts import compute_status, ir_compiler, validate_safety_gate, validate_topology  # noqa: E402


def _phase_2_workspace(project: Path) -> Path:
    """L'état réel d'un workspace neuf après les architectes : aucun prompt,
    contrat au gabarit. La fixture livre ses prompts, donc aucun test ne le voyait."""
    for p in (project / "workspace/src/prompts").glob("*.md"):
        p.unlink()
    for c in (project / "workspace/feats/contracts/agents").glob("*.agent.md"):
        c.write_text(re.sub(r"(Hash\s*:\s*)sha256:[0-9a-f]+", r"\1sha256:…",
                            c.read_text(encoding="utf-8")), encoding="utf-8")
    return project


# ---------------------------------------------------------------------------
# 1. Le second interblocage : le hash de prompt exigé avant que le prompt existe
# ---------------------------------------------------------------------------
def test_the_ir_compiles_in_phase_2_before_any_prompt_exists(project: Path) -> None:
    _phase_2_workspace(project)
    ir, report = ir_compiler.compile_mission(project, 1)
    assert report.ok, [f.message for f in report.errors]
    assert all("promptHash" not in a for a in ir["agents"])
    assert all(a["promptRef"].startswith("workspace/src/prompts/") for a in ir["agents"])
    assert ir["compiledFrom"]["promptHashes"] == {}


def test_writing_the_prompts_stales_the_ir_so_the_hash_gets_pinned(project: Path) -> None:
    _phase_2_workspace(project)
    ir_compiler.compile_to_file(project, 1)
    from sdda_scripts import check_ir_freshness

    assert check_ir_freshness.freshness(project, 1)["fresh"] is True
    (project / "workspace/src/prompts/billing-specialist.system.md").write_text("# prompt\n", encoding="utf-8")
    state = check_ir_freshness.freshness(project, 1)
    assert state["fresh"] is False and "promptHashes" in state["moved"]

    ir, _ = ir_compiler.compile_mission(project, 1)
    pinned = {a["id"]: a.get("promptHash") for a in ir["agents"]}
    assert pinned["1-billing-specialist"], "le prompt écrit est épinglé à la recompilation"
    assert pinned["1-intent-classifier"] is None, "celui qui manque encore ne l'est pas"


def test_dev_agent_is_refused_on_an_agent_whose_prompt_is_not_pinned(project: Path) -> None:
    """L'exigence n'est pas levée, elle est déplacée là où elle coûte quelque chose."""
    _phase_2_workspace(project)
    ir_compiler.compile_to_file(project, 1)
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = preflight_agent_bounds.check(project, {"tool_input": {"subagent_type": "dev-agent"}, "mission": 1})
    assert code == _hook.DENY and "PROMPT_NOT_PINNED" in buf.getvalue()


def test_dev_agent_is_allowed_once_every_prompt_is_pinned(project: Path) -> None:
    ir_compiler.compile_to_file(project, 1)   # la fixture complète : tous les prompts existent
    with redirect_stderr(io.StringIO()):
        code = preflight_agent_bounds.check(project, {"tool_input": {"subagent_type": "dev-agent"}, "mission": 1})
    assert code == _hook.ALLOW


# ---------------------------------------------------------------------------
# 2. Un item qui plante compte zéro, il ne disparaît pas de la moyenne
# ---------------------------------------------------------------------------
def test_an_errored_item_counts_as_zero_not_as_absent() -> None:
    run = RunResult(run_index=0)
    run.items.append(ItemResult("ok", 0, 1.0, True, {}, None))
    for k in range(9):
        run.items.append(ItemResult(f"crash-{k}", 0, 0.0, False, {}, "RuntimeError: boom"))
    assert run.score == pytest.approx(0.1), "9 plantages sur 10 ne font pas un 1,0"
    assert run.errors == 9


# ---------------------------------------------------------------------------
# 3. Les parts de gate écrites sous le numéro sont lues par la machine à états
# ---------------------------------------------------------------------------
def test_a_gate_part_written_under_the_bare_mission_number_is_seen(project: Path) -> None:
    from sdda_lib.errors import Report
    from sdda_lib.gate_reports import write_gate_report

    # Une part contributive, écrite comme le font les validateurs qui ne
    # reçoivent que `--mission {n}` : artifact = "1", pas "1-SupportAssistant".
    red = Report(name="G7.toolscope", target="1")
    red.error("TOOL_SCOPE_EXCESS", "outil hors périmètre", "retirer l'outil", "x")
    write_gate_report(project, "G7", "1", red, {}, part="toolscope")

    index = compute_status.GateIndex(project)
    ev = index.evaluate("G7", "1-SupportAssistant")
    assert "TOOL_SCOPE_EXCESS" in ev.classes, "un rouge écrit sous le numéro doit bloquer la mission"


# ---------------------------------------------------------------------------
# 4. Les seuils des reviewers lisent le rapport que la fiche écrit vraiment
# ---------------------------------------------------------------------------
def test_reviewer_findings_are_read_from_the_file_the_sheet_declares(project: Path) -> None:
    reports = project / "workspace/.sys/.validation/reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "agent-safety-1.md").write_text(
        "# Findings\n\n| critical | exfiltration par l'outil `mail` |\n", encoding="utf-8")
    found = validate_safety_gate.reviewer_findings(project, "1-SupportAssistant")
    assert found.get("review-safety"), "le rapport `agent-safety-{n}.md` est celui que la fiche écrit"
    assert found["review-safety"][0][0] == "critical"


# ---------------------------------------------------------------------------
# 5. Le journal des bypasses : un seul champ d'horodatage lu par tous
# ---------------------------------------------------------------------------
def test_every_bypass_writer_uses_the_field_the_readers_expect(project: Path) -> None:
    from sdda_scripts import preflight_force_cumul

    preflight_force_cumul.audit_line(project, {"gate": "G2", "bypass": "SDDA_BYPASS_BUDGET_ESTIMATE",
                                               "reason": "test"})
    line = (project / "workspace/.sys/.audit/bypasses.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
    entry = json.loads(line)
    assert entry.get("at"), "`at` est la clé que lisent sdda_state.bypasses_of et compute_status"


# ---------------------------------------------------------------------------
# 6. La section de justification est lue sous le titre que le gabarit donne
# ---------------------------------------------------------------------------
def test_the_simplicity_section_is_parsed_under_the_template_heading(project: Path) -> None:
    topo = project / "workspace/feats/topology/1-topology.md"
    text = topo.read_text(encoding="utf-8")
    assert "## 3. Alternative plus simple considérée" in text
    spec = validate_topology.parse_topology(text, topo)
    assert spec.alternative_body is not None, "lue comme absente quoi que l'architecte écrive"
