"""Audit IR / validateurs du 2026-09-25 — parts de gate, fraîcheur, sources non compilées.

Suite de `test_audit_ir_gates.py` : M1 à M11 et les mineurs. Chaque test
échouait sur le code d'avant le correctif qu'il nomme.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conftest import make_project, run_main  # type: ignore
from sdda_lib import gate_reports, paths
from sdda_lib.errors import Report
from sdda_lib.layered_config import read_layered_config
from sdda_scripts import (check_ir_freshness, compute_status, ir_compiler, validate_adr,
                          validate_api_contract, validate_architecture, validate_data_access,
                          validate_datasets, validate_framework, validate_ir, validate_packaging,
                          validate_safety_gate, validate_topology)
from test_roster import COMPLETE, write_manifest  # type: ignore

FIXED_AT = "2026-09-25T00:00:00Z"
STACK = "workspace/stack/STACK.md"


def _patch(root: Path, rel: str, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} absent de {rel}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def _compile(root: Path) -> dict:
    ir_compiler.compile_to_file(root, 1, compiled_at=FIXED_AT)
    return ir_compiler.load_ir(paths.ir_path(root, 1))


def _gate(root: Path, name: str) -> dict:
    return json.loads((paths.validation_dir(root) / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# M1 — les parts contributives de G2/G6 s'épinglent et se rattachent
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("module, part", [(validate_adr, "adr"), (validate_packaging, "packaging"),
                                          (validate_architecture, "architecture")])
def test_a_g2_contributive_part_goes_stale_when_stack_md_changes(project: Path, module, part) -> None:
    write_manifest(project)
    run_main(module.main, ["--root", str(project), "--mission", "1"])
    report = _gate(project, f"G2-1-SupportAssistant.{part}.json")
    assert report["pinnedHashes"], "une part épinglée à vide ne se périme jamais"
    assert compute_status.stale_keys(project, report) == []
    _patch(project, STACK, "StackComboCheck: warn", "StackComboCheck: warn\nDeliverableType: cli-exe")
    assert "stack" in compute_status.stale_keys(project, report)


@pytest.mark.parametrize("module, gate, part", [(validate_adr, "G2", "adr"), (validate_packaging, "G2", "packaging")])
def test_without_mission_a_part_is_written_under_the_global_artifact(project: Path, module, gate, part) -> None:
    run_main(module.main, ["--root", str(project)])
    assert (paths.validation_dir(project) / f"{gate}-stack.{part}.json").is_file()
    assert not (paths.validation_dir(project) / f"{gate}-0.{part}.json").exists()


def test_editing_the_roster_stales_the_architecture_part(project: Path) -> None:
    write_manifest(project)
    run_main(validate_architecture.main, ["--root", str(project), "--mission", "1"])
    report = _gate(project, "G2-1-SupportAssistant.architecture.json")
    _patch(project, "workspace/feats/1-roster.md", "tier: balanced", "tier: deep")
    assert "workspace/feats/1-roster.md" in compute_status.stale_keys(project, report)


def test_architecture_without_mission_finds_the_roster_in_feats(project: Path) -> None:
    write_manifest(project)
    report = Report(name="ARCH", target=str(project))
    roster = validate_architecture._roster_manifest(project, None, report)
    assert roster is not None and roster.source == "workspace/feats/1-roster.md"


def test_a_bare_number_artifact_resolves_its_mission() -> None:
    assert compute_status._mission_number("1") == 1
    assert compute_status._mission_number("12-Foo") == 12


# ---------------------------------------------------------------------------
# M2 — roster <-> IR
# ---------------------------------------------------------------------------
def test_an_ir_matching_the_roster_is_green(project: Path) -> None:
    write_manifest(project)
    ir = _compile(project)
    report = validate_ir.validate_ir_data(ir, root=project, config=read_layered_config(project))
    assert "ARCH_ROSTER_INCOHERENT" not in {f.cls for f in report.errors}, report.render_text()


def test_a_tier_raised_away_from_the_roster_is_refused(project: Path) -> None:
    write_manifest(project, COMPLETE.replace("tier: balanced", "tier: deep"))
    ir = _compile(project)
    report = validate_ir.validate_ir_data(ir, root=project, config=read_layered_config(project))
    assert "ARCH_ROSTER_INCOHERENT" in {f.cls for f in report.errors}


def test_an_agent_the_roster_does_not_declare_is_refused(project: Path) -> None:
    write_manifest(project, COMPLETE.replace("  - id: billing-specialist", "  - id: refund-specialist"))
    ir = _compile(project)
    report = validate_ir.validate_ir_data(ir, root=project, config=read_layered_config(project))
    messages = [f.message for f in report.errors if f.cls == "ARCH_ROSTER_INCOHERENT"]
    assert any("1-billing-specialist" in m for m in messages) and any("1-refund-specialist" in m for m in messages)


def test_a_tool_the_roster_does_not_grant_is_refused(project: Path) -> None:
    write_manifest(project, COMPLETE.replace("tools: [invoice_lookup, zendesk_create_ticket]", "tools: [invoice_lookup]"))
    ir = _compile(project)
    report = validate_ir.validate_ir_data(ir, root=project, config=read_layered_config(project))
    assert any("zendesk_create_ticket" in f.message for f in report.errors if f.cls == "ARCH_ROSTER_INCOHERENT")


# ---------------------------------------------------------------------------
# M3 — sources lues et désormais suivies
# ---------------------------------------------------------------------------
def test_adding_a_system_suite_stales_the_ir(project: Path) -> None:
    _compile(project)
    (paths.suites_dir(project) / "1-e2e.yaml").write_text(
        "id: 1-e2e\nlevel: L7\ngrader: exact\nthreshold: 0.8\nruns: 3\n"
        "dataset: workspace/pipeline/datasets/golden/billing-v1.jsonl\n", encoding="utf-8")
    state = check_ir_freshness.freshness(project, 1)
    assert not state["fresh"] and "suiteHashes" in state["moved"]


def test_changing_eval_runs_critical_stales_the_ir(project: Path) -> None:
    _compile(project)
    _patch(project, STACK, "StackComboCheck: warn", "StackComboCheck: warn\nEvalRunsCritical: 7")
    state = check_ir_freshness.freshness(project, 1)
    assert "configHash" in state["moved"]


def test_writing_the_roster_stales_the_ir(project: Path) -> None:
    _compile(project)
    write_manifest(project)
    assert "rosterHash" in check_ir_freshness.freshness(project, 1)["moved"]


# ---------------------------------------------------------------------------
# M4 — le contrat mémoire est compilé
# ---------------------------------------------------------------------------
MEMORY_CONTRACT = """# MEMORY CONTRACT: 1-memory

MISSION: 1-SupportAssistant
Status: Draft

## 2. Mémoire court terme (conversation)

| Clé | Valeur | Commentaire |
|---|---|---|
| `ShortTermPolicy` | sliding-window | |
| `ShortTermMaxTurns` | {turns} | |
| `SummarizeTriggerTokens` | 8000 | |

## 4. PII

| Clé | Valeur | Commentaire |
|---|---|---|
| `MemoryPIIPolicy` | redact-before-write | |
"""


def _memory_contract(root: Path, turns: int) -> None:
    d = paths.contracts_dir(root, "memory")
    d.mkdir(parents=True, exist_ok=True)
    (d / "1-memory.md").write_text(MEMORY_CONTRACT.format(turns=turns), encoding="utf-8")


def test_the_memory_contract_reaches_the_ir(project: Path) -> None:
    _memory_contract(project, 12)
    ir = _compile(project)
    # `SummarizeTriggerTokens` n'est QUE dans le contrat : avant, il n'atteignait pas l'IR.
    assert ir["memory"]["summarizeTriggerTokens"] == 8000
    assert ir["memory"]["shortTermMaxTurns"] == 12 and ir["memory"]["piiPolicy"] == "redact-before-write"


def test_a_memory_contract_contradicting_stack_md_does_not_compile(project: Path) -> None:
    _memory_contract(project, 30)
    with pytest.raises(ir_compiler.CompileError) as exc:
        ir_compiler.compile_mission(project, 1, compiled_at=FIXED_AT)
    assert "MEMORY_CONTRACT_MISMATCH" in {f.cls for f in exc.value.report.errors}


# ---------------------------------------------------------------------------
# M6 — la part `api` de G6
# ---------------------------------------------------------------------------
def test_the_entry_agent_is_found_behind_a_non_agent_entry(project: Path) -> None:
    ir = _compile(project)
    orch = ir["orchestration"]
    orch["nodes"].append({"id": "entry", "kind": "function", "ref": "entrée"})
    orch["edges"].append({"from": "entry", "to": orch["entryNode"], "condition": "always"})
    orch["entryNode"] = "entry"
    agent = validate_api_contract.entry_agent(ir)
    assert agent is not None and agent["id"] == "1-intent-classifier"


def test_a_backend_api_without_published_contract_is_red(project: Path) -> None:
    _compile(project)
    _patch(project, STACK, "StackComboCheck: warn", "StackComboCheck: warn\nDeliverableType: backend-api")
    report = Report(name="G6.api", target=str(project))
    assert validate_api_contract.run(project, "1", report) is True
    assert "API_CONTRACT_DRIFT" in {f.cls for f in report.errors}


def test_a_cli_without_published_contract_stays_not_applicable(project: Path) -> None:
    _compile(project)
    report = Report(name="G6.api", target=str(project))
    assert validate_api_contract.run(project, "1", report) is False and report.ok


def test_an_openapi_inside_a_virtualenv_is_not_ours(project: Path) -> None:
    lib = project / "workspace/src/SupportAssistant/.venv/lib/site-packages/pkg/openapi.json"
    lib.parent.mkdir(parents=True)
    lib.write_text("{}", encoding="utf-8")
    assert validate_api_contract.find_openapi(project) is None


# ---------------------------------------------------------------------------
# M7 — `--fail-on` et les rapports de reviewers
# ---------------------------------------------------------------------------
def test_a_severity_legend_is_not_a_finding(project: Path) -> None:
    reports = paths.validation_dir(project) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "agent-safety-1.md").write_text("| Sévérité | info · minor · moderate · serious · critical |\n", encoding="utf-8")
    assert validate_safety_gate.reviewer_findings(project, "1-SupportAssistant")["review-safety"] == []


# ---------------------------------------------------------------------------
# M8 — une CAP sans agent n'attend pas de G5
# ---------------------------------------------------------------------------
def test_a_cap_carried_by_a_tool_only_needs_no_g5(project: Path) -> None:
    ir = _compile(project)
    ir["traceability"]["1-1-ClassifyIntent"]["implementedBy"] = {"tools": ["1-invoice-lookup"]}
    paths.ir_path(project, 1).write_bytes(ir_compiler.dump_ir(ir))
    assert compute_status.agent_cap_ids(project, 1, ["1-1-ClassifyIntent", "1-2-ExplainInvoiceLine"]) == ["1-2-ExplainInvoiceLine"]


# ---------------------------------------------------------------------------
# M9 / M10 — accès aux données
# ---------------------------------------------------------------------------
@pytest.fixture
def sources_project(tmp_path: Path) -> Path:
    return make_project(tmp_path, "project_declared_sources")


def test_phase_2_reads_the_env_the_human_deposited(sources_project: Path) -> None:
    app_env = sources_project / "workspace/src/SupportAssistant/.env"
    assets_env = sources_project / "workspace/assets/.env"
    assets_env.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(app_env), str(assets_env))
    report = validate_data_access.run(sources_project)
    assert "DATA_SECRET_FILE_MISSING" not in {f.cls for f in report.errors}, report.render_text()


def test_a_contract_widening_the_stack_envelope_is_refused(sources_project: Path) -> None:
    report = Report(name="DA", target=str(sources_project))
    envelope = {"role": "readonly", "statementTimeoutMs": 5000, "maxRows": 100000, "schemas": ["order_tracking"],
                "forbidden": ["WRITE"], "egressAllowlist": ["evil.example.com"]}
    validate_data_access._check_envelope_within_stack(sources_project, "1-data-x", "declared-sources", envelope, report, "ir")
    messages = " ".join(f.message for f in report.errors if f.cls == "DATA_ACCESS_INCONSISTENT")
    assert "maxRows" in messages and "evil.example.com" in messages


# ---------------------------------------------------------------------------
# M11 — un pré-requis de phase n'écrit pas G8.datasets
# ---------------------------------------------------------------------------
def test_a_phase_precheck_does_not_write_the_g8_datasets_part(project: Path) -> None:
    validate_datasets.validate_datasets(project, mission=1, config=read_layered_config(project), require=("golden",))
    assert not (paths.validation_dir(project) / "G8-1-SupportAssistant.datasets.json").exists()


def test_the_full_check_still_writes_it(project: Path) -> None:
    validate_datasets.validate_datasets(project, mission=1, config=read_layered_config(project))
    assert (paths.validation_dir(project) / "G8-1-SupportAssistant.datasets.json").is_file()


# ---------------------------------------------------------------------------
# Couplage Python — hors Python, la part `framework` est absente, pas verte
# ---------------------------------------------------------------------------
def test_framework_part_is_not_written_green_for_another_language(project: Path) -> None:
    """C# est désormais vérifié (`validate_framework.NATIVE_IMPORTS`) : un framework
    Python déclaré sur un projet C# rend la part ROUGE, jamais verte."""
    import json

    _patch(project, STACK, " - .sdda/stacks/lang/python.md", " - .sdda/stacks/lang/csharp.md")
    run_main(validate_framework.main, ["--root", str(project), "--mission", "1"])
    written = list(paths.validation_dir(project).glob("G6-*.framework.json"))
    assert all(json.loads(p.read_text(encoding="utf-8"))["ok"] is False for p in written)


# ---------------------------------------------------------------------------
# Mineurs
# ---------------------------------------------------------------------------
def test_a_typographic_apostrophe_in_the_entry_node_is_read(project: Path) -> None:
    _patch(project, "workspace/pipeline/topology/1-topology.md", "- **Nœud d'entrée** :", "- **Nœud d’entrée** :")
    ir = _compile(project)
    assert ir["orchestration"]["entryNode"] == "classify"


def test_the_most_recent_report_wins_regardless_of_its_file_name(project: Path) -> None:
    ok, red = Report(name="x", target="1"), Report(name="x", target="1")
    red.error("ADR_MISSING", "rouge", "", "")
    gate_reports.write_gate_report(project, "G2", "1-SupportAssistant", ok, {}, part="adr")
    old = paths.validation_dir(project) / "G2-1.adr.json"
    gate_reports.write_gate_report(project, "G2", "1", red, {}, part="adr")
    data = json.loads(old.read_text(encoding="utf-8"))
    data["checkedAt"] = "2000-01-01T00:00:00Z"                 # l'ancien, rouge
    old.write_text(json.dumps(data), encoding="utf-8")
    index = compute_status.GateIndex(project)
    reports = [r for r in index.reports if r.get("part") == "adr"]
    assert max(reports, key=lambda r: str(r.get("checkedAt")))["ok"] is True
    # `evaluate` doit retenir le vert récent, pas `G2-1.adr.json` (dernier par nom).
    assert "ADR_MISSING" not in index.evaluate("G2", "1-SupportAssistant").classes


def test_unwired_tools_survive_in_the_compile_report(project: Path) -> None:
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    _, report = ir_compiler.compile_mission(project, 1, compiled_at=FIXED_AT)
    assert "unwiredTools" in report.data


def test_topology_meta_keys_are_normalised() -> None:
    spec = validate_topology.parse_topology("## 4. Le graphe\n\n- **Nœud d’entrée** : `a`\n")
    assert spec.graph_meta.get("noeud d'entree") == "a"
