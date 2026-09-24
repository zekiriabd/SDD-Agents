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
    assert suites["1-2-groundedness"]["judgeCalibrationRef"] == "workspace/pipeline/calibration/groundedness.json"
    assert suites["1-1-routing_accuracy"]["runs"] == 5 and suites["1-1-routing_accuracy"]["threshold"] == 0.95
    assert suites["1-billing-specialist-injection"]["level"] == "L8"
    assert ir["evaluation"]["holdout"] == "workspace/pipeline/datasets/holdout/mission-1-v1.jsonl"
    assert ir["traceability"]["1-2-ExplainInvoiceLine"]["implementedBy"]["tools"] == ["1-invoice-lookup", "1-zendesk-create-ticket"]


def test_missing_bound_is_a_compile_error_not_a_default(project: Path) -> None:
    contract = project / "workspace/pipeline/contracts/agents/1-billing-specialist.agent.md"
    text = contract.read_text(encoding="utf-8").replace("| `max_tool_calls` | 10 | fail-explicit |\n", "")
    contract.write_text(text, encoding="utf-8")
    with pytest.raises(ir_compiler.CompileError) as exc:
        ir_compiler.compile_mission(project, 1)
    assert exc.value.report.has("IR_COMPILE_FAILED")
    assert any("max_tool_calls" in f.message for f in exc.value.report.errors)
    assert not paths.ir_path(project, 1).exists()


def test_missing_prompt_and_no_pinned_hash_leaves_the_agent_unpinned_not_uncompilable(project: Path) -> None:
    """Le contrat inverse de l'ancien test, et pour une raison de séquence.

    L'IR se compile en PHASE 2 ; les prompts naissent en PHASE 4 chez `dev-prompt`,
    qui lit l'IR. Exiger ici le hash d'un fichier qui n'existe pas encore fermait
    la boucle : aucune MISSION neuve ne compilait. L'agent reste sans `promptHash`,
    et c'est `preflight_agent_bounds` qui refuse de lancer `dev-agent` tant qu'il
    en est ainsi — l'exigence vit là où elle est actionnable (P10 intact).
    """
    (project / "workspace/src/SupportAssistant/prompts/billing-specialist.system.md").unlink()
    ir, report = ir_compiler.compile_mission(project, 1)
    assert report.ok
    by_id = {a["id"]: a for a in ir["agents"]}
    assert "promptHash" not in by_id["1-billing-specialist"]
    assert by_id["1-intent-classifier"].get("promptHash"), "l'autre prompt, présent, reste épinglé"


def test_unknown_tool_in_agent_contract_is_a_compile_error(project: Path) -> None:
    contract = project / "workspace/pipeline/contracts/agents/1-billing-specialist.agent.md"
    text = contract.read_text(encoding="utf-8").replace("| `1-invoice-lookup` |", "| `1-ghost-tool` |")
    contract.write_text(text, encoding="utf-8")
    code, out = run_main(ir_compiler.main, ["--root", str(project), "--mission", "1"])
    assert code == 1
    assert "[IR_COMPILE_FAILED]" in out and "1-ghost-tool" in out


def test_tools_resolve_by_the_name_the_model_calls(project: Path) -> None:
    """`invoice_lookup` (nom, §1 du contrat) et `invoice-lookup` (id kebab) désignent `1-invoice-lookup`.

    Roster, topologie et CAPs parlent la langue du modèle (`refunds_search`) ;
    le contrat porte un id kebab dérivé. Ne résoudre que par id rendait
    `## Allocated To` incompilable sur 21 outils au premier run réel.
    """
    contract = project / "workspace/pipeline/contracts/agents/1-billing-specialist.agent.md"
    contract.write_text(contract.read_text(encoding="utf-8").replace("| `1-invoice-lookup` |", "| `invoice_lookup` |"),
                        encoding="utf-8")
    cap = project / "workspace/pipeline/caps/1-2-ExplainInvoiceLine.md"
    cap.write_text(cap.read_text(encoding="utf-8").replace("- tools: `invoice-lookup`,", "- tools: `invoice_lookup`,"),
                   encoding="utf-8")
    code, out = run_main(ir_compiler.main, ["--root", str(project), "--mission", "1"])
    assert code == 0, out
    ir = ir_compiler.load_ir(paths.ir_path(project, 1))
    billing = next(a for a in ir["agents"] if a["id"] == "1-billing-specialist")
    assert "1-invoice-lookup" in billing["tools"] and "1-invoice_lookup" not in billing["tools"]


def test_system_level_suites_written_by_qa_evals_enter_the_ir(project: Path) -> None:
    """L5 / L7 ne naissent d'aucune CAP : sans cette projection, G6 n'avait aucune exécution possible."""
    suites = project / "workspace/pipeline/suites"
    suites.mkdir(parents=True, exist_ok=True)
    (suites / "1-trajectory.yaml").write_text(
        'id: "1-trajectory"\nlevel: "L5"\ndataset: "workspace/pipeline/datasets/golden/x.jsonl"\n'
        'grader: "trajectory"\nthreshold: 1.0\nruns: 3\n', encoding="utf-8")
    (suites / "1-mission.yaml").write_text(
        'id: "1-mission"\nlevel: "L7"\ndataset: "workspace/pipeline/datasets/golden/x.jsonl"\n'
        'grader: "cost"\nthreshold: 0.03\nruns: 3\n', encoding="utf-8")
    (suites / "1-broken.yaml").write_text('id: "1-broken"\nlevel: "L7"\ngrader: "cost"\n', encoding="utf-8")
    (suites / "1-1-routing.yaml").write_text('id: "1-1-routing"\nlevel: "L4"\ngrader: "exact"\n', encoding="utf-8")
    ir, report = ir_compiler.compile_mission(project, 1, compiled_at=FIXED_AT)
    by_id = {s["id"]: s for s in ir["evaluation"]["suites"]}
    assert by_id["1-trajectory"]["level"] == "L5" and by_id["1-mission"]["grader"] == "cost"
    assert "1-broken" not in by_id and "1-1-routing" not in by_id       # incomplète / pas une suite système
    assert "EVAL_SUITE_INCOMPLETE" in {w.cls for w in report.warnings}
    schema = json.loads(paths.ir_schema_path(None).read_text(encoding="utf-8-sig"))
    assert SchemaValidator(schema).validate(ir) == []


def test_cli_exit_codes_and_json(project: Path) -> None:
    code, out = run_main(ir_compiler.main, ["--root", str(project), "--json", "--compiled-at", FIXED_AT])
    assert code == 0
    data = json.loads(out)
    assert data["ok"] is True and data["data"]["1"]["nodes"] == 4
