"""ir_compiler — déterminisme, conformité au schéma, refus d'inventer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_lib import paths
from sdda_lib.jsonschema_mini import SchemaValidator
from sdda_scripts import ir_compiler

FIXED_AT = "2026-09-20T10:00:00Z"


def _read(root: Path) -> dict:
    return ir_compiler.load_ir(paths.ir_path(root, 1))


def test_compiles_project_ok_to_schema_valid_ir(project: Path) -> None:
    code, out = run_main(ir_compiler.main, ["--root", str(project), "--mission", "1", "--compiled-at", FIXED_AT])
    assert code == 0, out
    ir = _read(project)
    schema = json.loads(paths.ir_schema_path(None).read_text(encoding="utf-8-sig"))
    assert SchemaValidator(schema).validate(ir) == []
    assert ir["missionId"] == "1-SupportAssistant"
    assert ir["compiledFrom"]["compiledAt"] == FIXED_AT
    assert set(ir["compiledFrom"]["capHashes"]) == {"1-1-ClassifyIntent", "1-2-ExplainInvoiceLine"}
    assert all(h.startswith("sha256:") for h in (ir["compiledFrom"]["missionHash"], ir["compiledFrom"]["topologyHash"], ir["compiledFrom"]["stackHash"]))


# ---------------------------------------------------------------------------
# Scission intent / binding — le critère d'acceptation de l'abstraction
# ---------------------------------------------------------------------------
# La ROADMAP dit : « si un second générateur demande de modifier les contrats,
# l'IR a échoué ». Attendre le Lot 7 pour l'apprendre, c'est l'apprendre après
# avoir écrit six générateurs contre le mauvais contrat. Ces tests simulent le
# second générateur MAINTENANT, à coût nul.
def retrieval_plan(ir: dict) -> list[dict]:
    """Ce qu'un générateur doit pouvoir écrire SANS jamais lire `binding`.

    C'est la définition opérationnelle de la frontière : si un plan d'appel
    complet se dérive de l'intention seule, alors changer de store ne touche
    pas le générateur. Sinon, `binding` n'est pas une branche, c'est une
    étagère.
    """
    plan = []
    for r in ir.get("retrievers") or []:
        intent = {k: v for k, v in r.items() if k != "binding"}
        plan.append({
            "id": intent["id"],
            "pattern": intent["pattern"],
            "topK": intent["topK"],
            "citationMode": intent["citationMode"],
            "identityFilter": intent.get("identityFilter"),
            "maxRetrievalCalls": intent.get("maxRetrievalCalls"),
            "thresholds": intent["gateThresholds"],
        })
    return plan


def test_a_generator_derives_its_plan_without_ever_reading_binding(project: Path) -> None:
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    plan = retrieval_plan(_read(project))
    assert plan, "la fixture doit porter au moins un retriever"
    assert plan[0]["pattern"] == "hybrid" and plan[0]["topK"] == 8


def test_the_plan_is_invariant_under_a_change_of_binding(project: Path) -> None:
    """Le test de vérité : changer de store ne doit rien changer au plan."""
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    before = retrieval_plan(_read(project))

    ir = _read(project)
    ir["retrievers"][0]["binding"] = {
        "store": "qdrant",
        "embeddingModel": "bge-m3",
        "chunk": {"strategy": "semantic", "size": 400, "overlap": 40},
        "rerank": {"model": "bge-reranker-v2"},
    }
    assert retrieval_plan(ir) == before


def test_no_infrastructure_identifier_survives_in_the_plan(project: Path) -> None:
    """Si un nom de composant remonte dans le plan, la frontière a fui."""
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    rendered = json.dumps(retrieval_plan(_read(project)), ensure_ascii=False).lower()
    for name in ("pgvector", "voyage", "qdrant", "postgres", "bge"):
        assert name not in rendered, f"`{name}` a fui de `binding` vers l'intention"


def test_a_binding_outside_the_active_stack_is_refused(project: Path) -> None:
    """`Store:` et `## Active Retrieval Stack` disaient la même chose sans se confronter."""
    contract = paths.contracts_dir(project, "retrieval") / "1-contracts-index.retrieval.md"
    contract.write_text(
        contract.read_text(encoding="utf-8").replace("Store: pgvector", "Store: qdrant"),
        encoding="utf-8",
    )
    with pytest.raises(ir_compiler.CompileError) as excinfo:
        ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    assert "RETRIEVAL_BINDING_MISMATCH" in excinfo.value.report.render_text()


def test_a_model_of_the_active_family_is_accepted(project: Path) -> None:
    """Une fiche couvre une FAMILLE (`voyage`), un contrat nomme un modèle (`voyage-3-lite`)."""
    contract = paths.contracts_dir(project, "retrieval") / "1-contracts-index.retrieval.md"
    contract.write_text(
        contract.read_text(encoding="utf-8").replace("voyage-3-large", "voyage-3-lite"),
        encoding="utf-8",
    )
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    assert _read(project)["retrievers"][0]["binding"]["embeddingModel"] == "voyage-3-lite"


def test_ir_is_byte_for_byte_reproducible(tmp_path: Path) -> None:
    a = make_project(tmp_path / "a")
    b = make_project(tmp_path / "b")
    ir_compiler.compile_to_file(a, 1, compiled_at=FIXED_AT)
    ir_compiler.compile_to_file(b, 1, compiled_at=FIXED_AT)
    assert paths.ir_path(a, 1).read_bytes() == paths.ir_path(b, 1).read_bytes()
    # Une recompilation SANS horodatage imposé conserve le `compiledAt` précédent :
    # le fichier est identique octet pour octet, donc son hash épinglé ne bouge pas.
    ir_compiler.compile_to_file(a, 1)
    assert paths.ir_path(a, 1).read_bytes() == paths.ir_path(b, 1).read_bytes()


def test_output_uses_lf_and_sorted_keys(project: Path) -> None:
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    raw = paths.ir_path(project, 1).read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    ir = json.loads(raw)
    assert list(ir) == sorted(ir)


def test_graph_is_compiled_from_mermaid(project: Path) -> None:
    ir, _ = ir_compiler.compile_mission(project, 1, compiled_at=FIXED_AT)
    orch = ir["orchestration"]
    assert orch["entryNode"] == "classify" and orch["terminalNodes"] == ["finalize"] and orch["maxHops"] == 6
    kinds = {n["id"]: (n["kind"], n["ref"]) for n in orch["nodes"]}
    assert kinds["classify"] == ("agent", "1-intent-classifier")
    assert kinds["billing"] == ("agent", "1-billing-specialist")
    assert kinds["finalize"] == ("function", "compose_answer")
    fallback = [e for e in orch["edges"] if e.get("isFallback")]
    assert [(e["from"], e["to"]) for e in fallback] == [("classify", "clarify")]
    assert all(e.get("countsAsHop", True) for e in orch["edges"])


def test_dotted_edge_is_a_free_edge(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_unbounded")
    ir, _ = ir_compiler.compile_mission(root, 1, compiled_at=FIXED_AT)
    free = {(e["from"], e["to"]) for e in ir["orchestration"]["edges"] if e.get("countsAsHop") is False}
    assert free == {("classify", "billing"), ("billing", "classify")}


def test_suites_are_derived_from_cap_acs_and_injection_suites(project: Path) -> None:
    ir, _ = ir_compiler.compile_mission(project, 1, compiled_at=FIXED_AT)
    suites = {s["id"]: s for s in ir["evaluation"]["suites"]}
    assert suites["1-2-groundedness"]["judgeCalibrationRef"] == "workspace/evals/calibration/groundedness.json"
    assert suites["1-1-routing_accuracy"]["runs"] == 5 and suites["1-1-routing_accuracy"]["threshold"] == 0.95
    assert suites["1-billing-specialist-injection"]["level"] == "L8"
    assert ir["evaluation"]["holdout"] == "workspace/datasets/holdout/mission-1-v1.jsonl"
    assert ir["traceability"]["1-2-ExplainInvoiceLine"]["implementedBy"]["tools"] == ["1-invoice-lookup", "1-zendesk-create-ticket"]


def test_missing_bound_is_a_compile_error_not_a_default(project: Path) -> None:
    contract = project / "workspace/contracts/agents/1-billing-specialist.agent.md"
    text = contract.read_text(encoding="utf-8").replace("| `max_tool_calls` | 10 | fail-explicit |\n", "")
    contract.write_text(text, encoding="utf-8")
    with pytest.raises(ir_compiler.CompileError) as exc:
        ir_compiler.compile_mission(project, 1)
    assert exc.value.report.has("IR_COMPILE_FAILED")
    assert any("max_tool_calls" in f.message for f in exc.value.report.errors)
    assert not paths.ir_path(project, 1).exists()


def test_missing_prompt_and_no_pinned_hash_is_a_compile_error(project: Path) -> None:
    (project / "workspace/prompts/billing-specialist.system.md").unlink()
    with pytest.raises(ir_compiler.CompileError) as exc:
        ir_compiler.compile_mission(project, 1)
    assert any("prompt" in f.message.lower() for f in exc.value.report.errors)


def test_unknown_tool_in_agent_contract_is_a_compile_error(project: Path) -> None:
    contract = project / "workspace/contracts/agents/1-billing-specialist.agent.md"
    text = contract.read_text(encoding="utf-8").replace("| `1-invoice-lookup` |", "| `1-ghost-tool` |")
    contract.write_text(text, encoding="utf-8")
    code, out = run_main(ir_compiler.main, ["--root", str(project), "--mission", "1"])
    assert code == 1
    assert "[IR_COMPILE_FAILED]" in out and "1-ghost-tool" in out


def test_cli_exit_codes_and_json(project: Path) -> None:
    code, out = run_main(ir_compiler.main, ["--root", str(project), "--json", "--compiled-at", FIXED_AT])
    assert code == 0
    data = json.loads(out)
    assert data["ok"] is True and data["data"]["1"]["nodes"] == 4
