"""TOOL GATE (G3), part `contracts` — ce qui doit refuser de câbler un outil.

La gate défend une règle simple : un outil se câble quand son contrat est
**tenu**, pas quand il existe. Les cas ci-dessous sont ceux où l'écart ne se
verrait qu'en production — un `required` fantôme, un retry sur un outil non
idempotent, un code qui écrit pendant que le contrat annonce `read-only`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main  # type: ignore
from sdda_lib import paths
from sdda_scripts import ir_compiler, validate_tool_contract

TOOLS = ("1-invoice-lookup", "1-zendesk-create-ticket")


@pytest.fixture
def compiled(tmp_path: Path) -> Path:
    root = make_project(tmp_path)
    ir_compiler.main(["--root", str(root), "--mission", "1", "--no-report"])
    return root


def _ir(root: Path) -> dict:
    return ir_compiler.load_ir(paths.ir_path(root, 1))


def _run(root: Path, ir: dict | None = None, **kwargs):
    from sdda_lib.errors import Report

    report = Report(name="G3", target=str(root))
    payload = validate_tool_contract.run(root, ir or _ir(root), report=report, **kwargs)
    return report, payload


def _patch_tool(ir: dict, tool_id: str, **fields) -> dict:
    for tool in ir["tools"]:
        if tool["id"] == tool_id:
            tool.update(fields)
    return ir


# ---------------------------------------------------------------------------
# Le cas nominal
# ---------------------------------------------------------------------------
def test_a_coherent_project_passes_and_writes_one_report_per_tool(compiled: Path) -> None:
    report, payload = _run(compiled)
    assert payload["verdict"] in ("green", "yellow"), report.render_text()
    assert report.ok
    for tool in TOOLS:
        gate = json.loads((paths.validation_dir(compiled) / f"G3-{tool}.contracts.json").read_text(encoding="utf-8"))
        assert gate["ok"] is True and gate["part"] == "contracts"
        # Épingles RÉSOLVABLES (audit 2026-09-25, M5) : l'outil dans l'IR et
        # son contrat sans `Status:` — plus l'identité de l'IR entier.
        assert gate["pinnedHashes"][f"irtool:{tool}"].startswith("sha256:")
        assert f"spec:workspace/pipeline/contracts/tools/{tool}.tool.md" in gate["pinnedHashes"]
        assert "ir" not in gate["pinnedHashes"]


def test_the_report_is_written_per_tool_because_that_is_what_the_state_machine_reads(compiled: Path) -> None:
    """`compute_status` cherche `G3-{outil}` : un rapport par MISSION serait ignoré."""
    _run(compiled)
    written = sorted(p.name for p in paths.validation_dir(compiled).glob("G3-*.json"))
    assert written == ["G3-1-invoice-lookup.contracts.json", "G3-1-zendesk-create-ticket.contracts.json"]


# ---------------------------------------------------------------------------
# Schémas
# ---------------------------------------------------------------------------
def test_a_required_field_absent_from_properties_is_refused(compiled: Path) -> None:
    ir = _patch_tool(_ir(compiled), "1-invoice-lookup",
                     inputSchema={"type": "object", "properties": {"invoice_id": {"type": "string"}}, "required": ["client_id"]})
    report, payload = _run(compiled, ir, write_report=False)
    assert payload["verdict"] == "red" and report.has("TOOL_SCHEMA_INVALID")
    assert "client_id" in " ".join(f.message for f in report.errors)


def test_a_scalar_input_schema_is_refused_by_the_meta_schema(compiled: Path) -> None:
    ir = _patch_tool(_ir(compiled), "1-invoice-lookup", inputSchema={"type": "string"})
    report, _ = _run(compiled, ir, write_report=False)
    assert report.has("TOOL_SCHEMA_INVALID")


def test_a_parameter_without_a_usable_description_warns(compiled: Path) -> None:
    """La description d'un paramètre est du prompt : sans elle, le modèle remplit au jugé."""
    report, _ = _run(compiled, write_report=False)
    assert report.has("TOOL_DESCRIPTION_VAGUE") and report.ok  # informe, ne bloque pas


# ---------------------------------------------------------------------------
# Suite de contrat
# ---------------------------------------------------------------------------
def test_a_declared_suite_absent_from_disk_is_refused(compiled: Path) -> None:
    (compiled / "workspace/pipeline/suites/tool-1-invoice-lookup.yaml").unlink()
    report, payload = _run(compiled, write_report=False)
    assert payload["verdict"] == "red" and report.has("TOOL_CONTRACT_FAILED")


def test_a_tool_without_any_contract_suite_is_refused(compiled: Path) -> None:
    ir = _patch_tool(_ir(compiled), "1-invoice-lookup", contractTestsRef="")
    report, _ = _run(compiled, ir, write_report=False)
    assert report.has("TOOL_CONTRACT_FAILED")


# ---------------------------------------------------------------------------
# Sûreté
# ---------------------------------------------------------------------------
def test_a_retry_on_a_non_idempotent_tool_is_refused(compiled: Path) -> None:
    """Un retry sur un outil non idempotent crée trois tickets pour une demande."""
    ir = _ir(compiled)
    _patch_tool(ir, "1-zendesk-create-ticket", retryPolicy="exponential:3",
                safetyStrategy={"idempotency": "none", "confirmation": "never", "dryRunSupported": True})
    report, payload = _run(compiled, ir, write_report=False)
    assert payload["verdict"] == "red" and report.has("TOOL_RETRY_UNSAFE")


def test_a_destructive_tool_without_confirmation_is_refused(compiled: Path) -> None:
    ir = _ir(compiled)
    _patch_tool(ir, "1-zendesk-create-ticket", sideEffectClass="write-destructive",
                safetyStrategy={"idempotency": "natural-key:conversation_id", "confirmation": "never", "dryRunSupported": True})
    report, _ = _run(compiled, ir, write_report=False)
    assert report.has("SAFETY_STRATEGY_MISSING")


def test_a_side_effect_tool_without_strategy_is_refused(compiled: Path) -> None:
    ir = _patch_tool(_ir(compiled), "1-zendesk-create-ticket", safetyStrategy={})
    report, _ = _run(compiled, ir, write_report=False)
    assert report.has("SAFETY_STRATEGY_MISSING")


def test_read_only_tools_are_not_asked_for_a_safety_strategy(compiled: Path) -> None:
    report, _ = _run(compiled, write_report=False)
    assert not any(f.cls == "SAFETY_STRATEGY_MISSING" and "invoice-lookup" in f.message for f in report.findings)


# ---------------------------------------------------------------------------
# Code
# ---------------------------------------------------------------------------
def test_code_declaring_another_side_effect_class_is_refused(compiled: Path) -> None:
    """Le contrat dit `read-only`, le code écrit : c'est cet écart qui rend une revue décorative."""
    # Layout plat : le code des outils vit dans le paquet de l'application, `workspace/src/{App}/tools/`.
    src = compiled / "workspace/src/SupportAssistant/tools"
    src.mkdir(parents=True, exist_ok=True)
    (src / "invoice_lookup.py").write_text(
        'def invoice_lookup(invoice_id: str):\n    side_effect_class = "write-destructive"\n    return {}\n', encoding="utf-8")
    report, payload = _run(compiled, write_report=False)
    assert payload["verdict"] == "red" and report.has("TOOL_CONTRACT_INCONSISTENT")


def test_code_agreeing_with_the_contract_passes(compiled: Path) -> None:
    src = compiled / "workspace/src/SupportAssistant/tools"   # layout plat : le code des outils vit dans le paquet
    src.mkdir(parents=True, exist_ok=True)
    (src / "invoice_lookup.py").write_text(
        'side_effect_class = "read-only"\n\n\ndef invoice_lookup(invoice_id: str):\n    return {}\n', encoding="utf-8")
    report, _ = _run(compiled, write_report=False, only={"1-invoice-lookup"})
    assert not report.has("TOOL_CONTRACT_INCONSISTENT")


def test_require_code_refuses_a_tool_with_no_implementation(compiled: Path) -> None:
    report, payload = _run(compiled, write_report=False, require_code=True)
    assert payload["verdict"] == "red" and report.has("TOOL_CONTRACT_INCONSISTENT")


def test_without_require_code_the_absence_of_code_is_not_an_error(compiled: Path) -> None:
    """La gate doit pouvoir tourner AVANT la génération : sinon on la joue trop tard."""
    report, _ = _run(compiled, write_report=False)
    assert not report.has("TOOL_CONTRACT_INCONSISTENT")


# ---------------------------------------------------------------------------
# Enveloppe DB et bypass
# ---------------------------------------------------------------------------
def test_a_full_role_envelope_demands_an_adr(compiled: Path) -> None:
    ir = _ir(compiled)
    ir["dataAccess"] = [{"id": "1-billing-view", "binding": {"strategy": "view-per-agent"}, "exposedTo": ["1-billing-specialist"],
                         "envelope": {"role": "full", "statementTimeoutMs": 2000, "maxRows": 500, "schemas": ["billing"], "forbidden": []}}]
    report, _ = _run(compiled, ir, write_report=False)
    assert report.has("DATA_ACCESS_ADR_REQUIRED")


def test_an_incomplete_envelope_is_refused(compiled: Path) -> None:
    ir = _ir(compiled)
    ir["dataAccess"] = [{"id": "1-billing-view", "binding": {"strategy": "view-per-agent"}, "exposedTo": ["1-billing-specialist"],
                         "envelope": {"role": "readonly", "schemas": ["billing"]}}]
    report, _ = _run(compiled, ir, write_report=False)
    assert report.has("DB_ENVELOPE_MISSING")


def test_the_bypass_never_covers_an_unsafe_destructive_tool(compiled: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDDA_BYPASS_TOOL_GATE", "1")
    monkeypatch.setenv("SDDA_BYPASS_REASON", "livraison urgente")
    ir = _patch_tool(_ir(compiled), "1-zendesk-create-ticket", safetyStrategy={})
    report, payload = _run(compiled, ir, write_report=False)
    assert payload["bypassed"] is True and payload["verdict"] == "red"
    assert report.has("SAFETY_STRATEGY_MISSING")  # jamais dégradée en avertissement
    assert (paths.audit_dir(compiled) / "bypasses.jsonl").is_file()


def test_the_bypass_downgrades_what_it_is_allowed_to(compiled: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDDA_BYPASS_TOOL_GATE", "1")
    monkeypatch.setenv("SDDA_BYPASS_REASON", "suite en cours d'écriture")
    (compiled / "workspace/pipeline/suites/tool-1-invoice-lookup.yaml").unlink()
    report, payload = _run(compiled, write_report=False)
    assert payload["verdict"] == "red" and report.ok  # dégradé en avertissement, verdict conservé
    assert any(f.cls == "TOOL_CONTRACT_FAILED" for f in report.warnings)


def test_static_mode_requires_the_suite_declared_not_present(compiled: Path) -> None:
    """PHASE 2 : la suite L2 appartient à qa-evals/qa-tests (PHASE 6) — déclarée, pas encore sur disque."""
    (compiled / "workspace/pipeline/suites/tool-1-invoice-lookup.yaml").unlink()
    code, out = run_main(validate_tool_contract.main, ["--root", str(compiled), "--mission", "1", "--static"])
    assert code == 0, out
    assert "absente du disque" not in out
    # Sans --static (G3 sur l'IR), l'absence reste bloquante.
    code, out = run_main(validate_tool_contract.main, ["--root", str(compiled), "--mission", "1", "--no-report"])
    assert code == 1 and "TOOL_CONTRACT_FAILED" in out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_cli_filters_on_one_tool(compiled: Path) -> None:
    code, out = run_main(validate_tool_contract.main,
                         ["--root", str(compiled), "--mission", "1", "--tool", "1-invoice-lookup", "--no-report"])
    assert code == 0 and "1-zendesk-create-ticket" not in out
