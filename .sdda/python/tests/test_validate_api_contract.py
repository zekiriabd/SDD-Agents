"""API GATE (G6, part `api`) — ce qu'elle refuse, et ce qu'elle refuse d'affirmer.

Ces contrôles existent parce que `[API_CONTRACT_DRIFT]`, `[API_ROUTE_UNBACKED]`
et `[API_STATUS_UNMAPPED]` ont été annoncés bloquants dans `ARCHITECTURE.md §4`
pendant tout un lot sans qu'aucun script ne les émette. Ils ne doivent pas
repartir : un test par classe, plus le cas « rien à confronter », qui est celui
où un faux vert serait le plus facile à accorder.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from conftest import run_main
from sdda_lib import paths
from sdda_lib.errors import Report
from sdda_scripts import ir_compiler, validate_api_contract

FIXED_AT = "2026-09-20T10:00:00Z"

MINIMAL_PATHS = {
    "/v1/runs": {"post": {"responses": {"200": {"description": "ok"}}}},
    "/healthz": {"get": {"responses": {"200": {"description": "ok"}}}},
}


@pytest.fixture
def ir(project: Path) -> dict:
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    return ir_compiler.load_ir(paths.ir_path(project, 1))


def write_openapi(root: Path, doc: dict) -> Path:
    path = paths.workspace(root) / "src" / "serving" / "openapi.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def conforming_doc(ir: dict) -> dict:
    """Un OpenAPI DÉRIVÉ de l'IR — celui que `dev-api` est censé générer."""
    agent = validate_api_contract.entry_agent(ir)
    assert agent is not None, "la fixture doit avoir un agent au nœud d'entrée"
    return {
        "openapi": "3.1.0",
        # Copie PROFONDE : un test qui ajoute un statut ou une route ne doit pas
        # contaminer le suivant. La version superficielle a fait passer un
        # `429` d'un test à l'autre — exactement le genre de faux rouge qui
        # apprend à ignorer un test.
        "paths": copy.deepcopy(MINIMAL_PATHS),
        "components": {
            "schemas": {
                "RunRequest": {"properties": {"input": copy.deepcopy(agent["inputSchema"])}},
                "RunResponse": {"properties": {"output": copy.deepcopy(agent["outputSchema"])}},
            }
        },
    }


def _run(root: Path) -> Report:
    report = Report(name="G6.api", target=str(root))
    validate_api_contract.run(root, "1", report)
    return report


def _classes(report: Report) -> set[str]:
    return {f.cls for f in report.errors}


# -- le contrat dérivé de l'IR passe ------------------------------------------------
def test_openapi_derived_from_ir_passes(project: Path, ir: dict) -> None:
    write_openapi(project, conforming_doc(ir))
    report = _run(project)
    assert report.ok, report.render_text()
    assert report.data["applicable"] is True


# -- absence de contrat : ni vert, ni rouge -----------------------------------------
def test_no_openapi_is_not_applicable_and_writes_no_report(project: Path, ir: dict) -> None:
    """Le cas où un faux vert serait le plus facile : il n'y a rien à confronter."""
    code, out = run_main(validate_api_contract.main, ["--root", str(project), "--mission", "1"])
    assert code == 0
    assert "non applicable" in out
    assert not (paths.validation_dir(project) / "G6-1.api.json").exists()


# -- [API_CONTRACT_DRIFT] -----------------------------------------------------------
def test_published_field_absent_from_ir_input_schema(project: Path, ir: dict) -> None:
    doc = conforming_doc(ir)
    doc["components"]["schemas"]["RunRequest"]["properties"]["input"]["properties"]["debug_sql"] = {"type": "string"}
    write_openapi(project, doc)
    report = _run(project)
    assert "API_CONTRACT_DRIFT" in _classes(report)
    assert any("debug_sql" in f.message for f in report.errors)


def test_required_ir_field_missing_from_published_contract(project: Path, ir: dict) -> None:
    agent = validate_api_contract.entry_agent(ir)
    required = (agent or {}).get("inputSchema", {}).get("required") or []
    if not required:
        pytest.skip("la fixture ne déclare aucun champ requis en entrée")
    doc = conforming_doc(ir)
    doc["components"]["schemas"]["RunRequest"]["properties"]["input"]["properties"].pop(required[0])
    write_openapi(project, doc)
    assert "API_CONTRACT_DRIFT" in _classes(_run(project))


def test_run_request_absent_from_openapi(project: Path, ir: dict) -> None:
    doc = conforming_doc(ir)
    doc["components"]["schemas"].pop("RunRequest")
    write_openapi(project, doc)
    assert "API_CONTRACT_DRIFT" in _classes(_run(project))


def test_ref_indirection_is_resolved(project: Path, ir: dict) -> None:
    """Un `$ref` ne doit pas faire passer une dérive pour une conformité."""
    doc = conforming_doc(ir)
    doc["components"]["schemas"]["Input"] = doc["components"]["schemas"]["RunRequest"]["properties"]["input"]
    doc["components"]["schemas"]["RunRequest"]["properties"]["input"] = {"$ref": "#/components/schemas/Input"}
    write_openapi(project, doc)
    assert _run(project).ok

    doc["components"]["schemas"]["Input"]["properties"]["smuggled"] = {"type": "string"}
    write_openapi(project, doc)
    assert "API_CONTRACT_DRIFT" in _classes(_run(project))


# -- [API_ROUTE_UNBACKED] -----------------------------------------------------------
def test_route_outside_surface_contract_is_rejected(project: Path, ir: dict) -> None:
    doc = conforming_doc(ir)
    doc["paths"]["/v1/admin/reindex"] = {"post": {"responses": {"200": {"description": "ok"}}}}
    write_openapi(project, doc)
    report = _run(project)
    assert "API_ROUTE_UNBACKED" in _classes(report)
    assert any("/v1/admin/reindex" in f.message for f in report.errors)


def test_resume_route_without_human_in_the_loop_is_rejected(project: Path, ir: dict) -> None:
    doc = conforming_doc(ir)
    doc["paths"]["/v1/runs/{thread_id}/resume"] = {"post": {"responses": {"200": {"description": "ok"}}}}
    write_openapi(project, doc)
    report = _run(project)
    assert "API_ROUTE_UNBACKED" in _classes(report)
    assert any("humanInTheLoop" in f.message for f in report.errors)


def test_resume_route_is_accepted_when_ir_declares_human_in_the_loop(project: Path, ir: dict) -> None:
    ir["orchestration"]["humanInTheLoop"] = True
    paths.ir_path(project, 1).write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    doc = conforming_doc(ir)
    doc["paths"]["/v1/runs/{thread_id}/resume"] = {"post": {"responses": {"200": {"description": "ok"}}}}
    write_openapi(project, doc)
    assert "API_ROUTE_UNBACKED" not in _classes(_run(project))


def test_path_parameter_name_does_not_matter(project: Path, ir: dict) -> None:
    doc = conforming_doc(ir)
    doc["paths"]["/v1/runs/{whatever_id}"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    write_openapi(project, doc)
    assert "API_ROUTE_UNBACKED" not in _classes(_run(project))


# -- [API_STATUS_UNMAPPED] ----------------------------------------------------------
def test_status_without_mapping_module_is_rejected(project: Path, ir: dict) -> None:
    doc = conforming_doc(ir)
    doc["paths"]["/v1/runs"]["post"]["responses"]["429"] = {"description": "rate limited"}
    write_openapi(project, doc)
    report = _run(project)
    assert "API_STATUS_UNMAPPED" in _classes(report)
    assert any("429" in f.message for f in report.errors)


def test_status_present_in_mapping_module_is_accepted(project: Path, ir: dict) -> None:
    doc = conforming_doc(ir)
    doc["paths"]["/v1/runs"]["post"]["responses"]["429"] = {"description": "rate limited"}
    write_openapi(project, doc)
    status = paths.workspace(project) / "src" / "serving" / "status.py"
    status.write_text('CLASS_TO_STATUS = {"BUDGET_EXCEEDED_MEASURED": 429}\n', encoding="utf-8")
    assert "API_STATUS_UNMAPPED" not in _classes(_run(project))


# -- ApiContractFirst: false --------------------------------------------------------
def test_contract_first_false_relaxes_schemas_but_not_routes(project: Path, ir: dict) -> None:
    """La divergence assumée porte sur les schémas ; une route non soutenue reste une faute."""
    stack = paths.stack_md_path(project)
    stack.write_text(
        stack.read_text(encoding="utf-8").replace(
            "## Project Config\n", "## Project Config\nApiContractFirst: false\n", 1
        ),
        encoding="utf-8",
    )
    doc = conforming_doc(ir)
    doc["components"]["schemas"]["RunRequest"]["properties"]["input"]["properties"]["debug_sql"] = {"type": "string"}
    doc["paths"]["/v1/admin/reindex"] = {"post": {"responses": {"200": {"description": "ok"}}}}
    write_openapi(project, doc)
    report = _run(project)
    assert "API_CONTRACT_DRIFT" not in _classes(report)
    assert "API_ROUTE_UNBACKED" in _classes(report)


# -- rapport de gate ----------------------------------------------------------------
def test_cli_writes_g6_api_report(project: Path, ir: dict) -> None:
    write_openapi(project, conforming_doc(ir))
    code, out = run_main(validate_api_contract.main, ["--root", str(project), "--mission", "1", "--json"])
    assert code == 0, out
    data = json.loads((paths.validation_dir(project) / "G6-1.api.json").read_text(encoding="utf-8"))
    assert data["ok"] is True and data["part"] == "api" and data["gate"] == "G6"
