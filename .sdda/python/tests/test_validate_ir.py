"""validate_ir — les 11 contrôles, surtout ce qu'ils REFUSENT."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_lib import paths
from sdda_lib.layered_config import read_layered_config
from sdda_scripts import ir_compiler, validate_ir

FIXED_AT = "2026-09-20T10:00:00Z"


@pytest.fixture
def ok_ir(project: Path) -> tuple[Path, dict]:
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    return project, ir_compiler.load_ir(paths.ir_path(project, 1))


def _validate(root: Path, ir: dict):
    return validate_ir.validate_ir_data(ir, root=root, config=read_layered_config(root))


def _classes(report) -> set[str]:
    return {f.cls for f in report.errors}


# -- le projet valide passe ---------------------------------------------------------
def test_project_ok_passes_all_controls(ok_ir) -> None:
    root, ir = ok_ir
    report = _validate(root, ir)
    assert report.ok, report.render_text()
    assert report.data["cyclesBoundedBy"] == {"billing -> classify -> billing": "maxHops"}


def test_cli_writes_g2_ir_report(project: Path) -> None:
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    code, out = run_main(validate_ir.main, ["--root", str(project), "--mission", "1", "--json"])
    assert code == 0, out
    rep = paths.validation_dir(project) / "G2-1-SupportAssistant.ir.json"
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert data["ok"] is True and data["part"] == "ir"
    assert {"mission", "topology", "stack", "ir", "cap:1-1-ClassifyIntent"} <= set(data["pinnedHashes"])
    assert "topology-mmd" not in data["pinnedHashes"]      # le graphe est dans `topology`


# -- (4) cycles non bornés : bloquant ----------------------------------------------
def test_unbounded_cycle_from_fixture_is_rejected(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_unbounded")
    ir_compiler.compile_to_file(root, 1, compiled_at=FIXED_AT)
    code, out = run_main(validate_ir.main, ["--root", str(root), "--mission", "1", "--no-report"])
    assert code == 1
    assert "[UNBOUNDED_LOOP]" in out and "billing -> classify" in out


def test_cycle_with_all_free_edges_is_unbounded(ok_ir) -> None:
    root, ir = ok_ir
    for e in ir["orchestration"]["edges"]:
        e["countsAsHop"] = False
    assert "UNBOUNDED_LOOP" in _classes(_validate(root, ir))


def test_free_cycle_with_decrementing_condition_is_bounded(ok_ir) -> None:
    root, ir = ok_ir
    for e in ir["orchestration"]["edges"]:
        e["countsAsHop"] = False
        if (e["from"], e["to"]) == ("billing", "classify"):
            e["condition"] = "needs_reclassification && attempts < 2"
    assert "UNBOUNDED_LOOP" not in _classes(_validate(root, ir))


def test_agent_self_loop_is_bounded_by_max_iterations(ok_ir) -> None:
    root, ir = ok_ir
    ir["orchestration"]["edges"].append({"from": "billing", "to": "billing", "condition": "retry", "countsAsHop": False})
    report = _validate(root, ir)
    assert "UNBOUNDED_LOOP" not in _classes(report)
    assert report.data["cyclesBoundedBy"]["billing -> billing"] == "maxIterations"


def test_function_self_loop_without_hop_is_unbounded(ok_ir) -> None:
    root, ir = ok_ir
    ir["orchestration"]["edges"].append({"from": "clarify", "to": "clarify", "condition": "again", "countsAsHop": False})
    assert "UNBOUNDED_LOOP" in _classes(_validate(root, ir))


# -- (10) neutralité framework -------------------------------------------------------
def test_framework_leak_in_contract_is_rejected(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_framework_leak")
    ir_compiler.compile_to_file(root, 1, compiled_at=FIXED_AT)
    code, out = run_main(validate_ir.main, ["--root", str(root), "--mission", "1", "--no-report"])
    assert code == 1
    assert "[FRAMEWORK_LEAK_IN_CONTRACT]" in out and "StateGraph" in out and "LangGraph" in out


@pytest.mark.parametrize("token", [
    "StateGraph", "Kernel", "AgentExecutor", "CrewAI", "Runnable", "LLMChain", "AgentType",
    # .NET : Semantic Kernel puis Microsoft Agent Framework
    "ChatCompletionAgent", "AIAgent", "ChatClientAgent", "AgentThread",
    # TypeScript, Java, Python hors LangChain
    "generateText", "streamText", "AiServices", "LlmAgent",
])
def test_every_listed_framework_token_is_caught_anywhere(ok_ir, token: str) -> None:
    root, ir = ok_ir
    ir["tools"][0]["description"] = f"Retourne les lignes d'une facture via un {token} dédié, pour le client courant."
    assert "FRAMEWORK_LEAK_IN_CONTRACT" in _classes(_validate(root, ir))


def test_framework_token_in_a_key_is_caught(ok_ir) -> None:
    root, ir = ok_ir
    ir["schemas"] = {"StateGraph": {"type": "object"}}
    assert "FRAMEWORK_LEAK_IN_CONTRACT" in _classes(_validate(root, ir))


def test_infrastructure_names_are_not_leaks(ok_ir) -> None:
    root, ir = ok_ir
    assert ir["retrievers"][0]["binding"]["store"] == "pgvector"
    assert "FRAMEWORK_LEAK_IN_CONTRACT" not in _classes(_validate(root, ir))


@pytest.mark.parametrize("name", [
    "langchain-js", "langgraph-js", "mastra", "vercel-ai-sdk",
    "ms-agent-framework", "semantic-kernel", "spring-ai", "langchain4j", "agno", "google-adk",
])
def test_non_python_framework_names_are_caught(ok_ir, name: str) -> None:
    """Une fuite de framework est la même faute en C# ou en TypeScript qu'en Python.

    Le catalogue déclare 18 frameworks sur 4 langages ; une détection qui n'en
    connaîtrait qu'un donnerait un faux vert aux trois autres.
    """
    root, ir = ok_ir
    ir["tools"][0]["description"] = f"Retourne les lignes d'une facture en passant par {name}, pour le client courant."
    assert "FRAMEWORK_LEAK_IN_CONTRACT" in _classes(_validate(root, ir))


@pytest.mark.parametrize("phrase", [
    "le routeur choisit un agent séquentiel selon l'intention détectée",
    "l'outil retourne un client de chat prêt à l'emploi pour le support",
    "génère un texte de réponse à partir des lignes de facture trouvées",
])
def test_ordinary_prose_is_not_a_leak(ok_ir, phrase: str) -> None:
    """Un faux positif bloque un contrat juste : les jetons doivent être distinctifs."""
    root, ir = ok_ir
    ir["tools"][0]["description"] = phrase
    assert "FRAMEWORK_LEAK_IN_CONTRACT" not in _classes(_validate(root, ir))


# -- (7) moindre privilège ---------------------------------------------------------------
def test_tool_outside_cap_scope_is_rejected(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_tool_scope_excess")
    ir_compiler.compile_to_file(root, 1, compiled_at=FIXED_AT)
    code, out = run_main(validate_ir.main, ["--root", str(root), "--mission", "1", "--no-report"])
    assert code == 1
    assert "[TOOL_SCOPE_EXCESS]" in out and "1-intent-classifier" in out and "1-invoice-lookup" in out


# -- (8) effets de bord -----------------------------------------------------------------------
def test_destructive_tool_without_safety_strategy_is_rejected(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_side_effect_undeclared")
    ir_compiler.compile_to_file(root, 1, compiled_at=FIXED_AT)
    report = _validate(root, ir_compiler.load_ir(paths.ir_path(root, 1)))
    assert {"SIDE_EFFECT_UNDECLARED", "TOOL_RETRY_UNSAFE"} <= _classes(report)


def test_read_only_tool_needs_no_strategy(ok_ir) -> None:
    root, ir = ok_ir
    assert "safetyStrategy" not in ir["tools"][0]
    assert "SIDE_EFFECT_UNDECLARED" not in _classes(_validate(root, ir))


# -- (9) posture de confiance --------------------------------------------------------------------
def test_untrusted_inputs_without_injection_suite_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    ir["evaluation"]["suites"] = [s for s in ir["evaluation"]["suites"] if s["id"] != "1-billing-specialist-injection"]
    report = _validate(root, ir)
    assert "INJECTION_SUITE_MISSING" in _classes(report)
    assert any("1-billing-specialist" in f.message for f in report.errors if f.cls == "INJECTION_SUITE_MISSING")


# -- (11) juge calibré ----------------------------------------------------------------------------
def test_llm_judge_without_calibration_ref_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    for s in ir["evaluation"]["suites"]:
        s.pop("judgeCalibrationRef", None)
    report = _validate(root, ir)
    assert "JUDGE_UNCALIBRATED" in _classes(report)
    assert "IR_INVALID" in _classes(report)     # le schéma l'exige aussi (if/then)


def test_llm_judge_with_unresolvable_calibration_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    (root / "workspace/proof/calibration/groundedness.json").unlink()
    assert "JUDGE_UNCALIBRATED" in _classes(_validate(root, ir))


def test_llm_judge_below_kappa_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    cal = root / "workspace/proof/calibration/groundedness.json"
    cal.write_text(json.dumps({"grader": "groundedness", "items": 52, "kappa": 0.41}), encoding="utf-8")
    assert "JUDGE_UNCALIBRATED" in _classes(_validate(root, ir))


def test_advisory_llm_judge_needs_no_calibration(ok_ir) -> None:
    root, ir = ok_ir
    for s in ir["evaluation"]["suites"]:
        if s["grader"] == "llm-judge":
            s.pop("judgeCalibrationRef", None)
            s["advisory"] = True
    assert "JUDGE_UNCALIBRATED" not in _classes(_validate(root, ir))


def test_suite_iterating_on_holdout_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    ir["evaluation"]["suites"][0]["dataset"] = "workspace/proof/datasets/holdout/mission-1-v1.jsonl"
    assert "AC_DATASET_IS_HOLDOUT" in _classes(_validate(root, ir))


# -- (1) (2) (3) (5) (6) -----------------------------------------------------------------------------
def test_schema_violation_is_ir_invalid(ok_ir) -> None:
    root, ir = ok_ir
    ir["agents"][0]["modelTier"] = "claude-sonnet-5"       # un nom de modèle, pas un tier (P11)
    assert "IR_INVALID" in _classes(_validate(root, ir))


def test_dangling_node_ref_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    ir["orchestration"]["nodes"][0]["ref"] = "1-nobody"
    assert "IR_DANGLING_REF" in _classes(_validate(root, ir))


def test_unreachable_node_and_dead_end_are_rejected(ok_ir) -> None:
    root, ir = ok_ir
    ir["orchestration"]["nodes"].append({"id": "orphan", "kind": "function", "ref": "noop"})
    ir["orchestration"]["edges"] = [e for e in ir["orchestration"]["edges"] if e["from"] != "clarify"]
    report = _validate(root, ir)
    msgs = [f.message for f in report.errors if f.cls == "GRAPH_UNREACHABLE"]
    assert any("orphan" in m and "inatteignable" in m for m in msgs)
    assert any("clarify" in m and "terminal" in m for m in msgs)


def test_agent_budgets_over_hard_cap_on_longest_path(ok_ir) -> None:
    root, ir = ok_ir
    ir["budget"]["costPerRunHardCapUsd"] = 0.05     # 0.12 + 0.01 sur le chemin classify -> billing
    assert "BUDGET_EXCEEDED_ESTIMATE" in _classes(_validate(root, ir))


def test_cap_served_by_nobody_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    ir["agents"][1]["servesCaps"] = ["1-2-ExplainInvoiceLine"]
    ir["traceability"]["1-1-ClassifyIntent"]["implementedBy"] = {}
    assert "CAP_NOT_IMPLEMENTED" in _classes(_validate(root, ir))


def test_router_without_fallback_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    for e in ir["orchestration"]["edges"]:
        e.pop("isFallback", None)
    assert "ROUTER_NO_FALLBACK" in _classes(_validate(root, ir))


# -- 10.bis neutralite d'INFRASTRUCTURE : rien hors de `binding` ---------------------
# La neutralite framework ne cherchait que des noms d'API (`StateGraph`) :
# `store: pgvector` passait, alors qu'il couple exactement pareil. Trouve en
# auditant l'IR, pas par un test qui echouait.
def test_an_infra_identifier_outside_binding_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    ir["retrievers"][0]["identityFilter"] = "pgvector_tenant"
    report = _validate(root, ir)
    assert "INFRA_LEAK_IN_INTENT" in _classes(report)
    assert any("pgvector" in f.message for f in report.errors)


def test_the_same_identifier_inside_binding_is_accepted(ok_ir) -> None:
    """`binding` est la SEULE branche ou un composant se nomme — sinon le controle ne sert a rien."""
    root, ir = ok_ir
    assert ir["retrievers"][0]["binding"]["store"] == "pgvector"
    assert "INFRA_LEAK_IN_INTENT" not in _classes(_validate(root, ir))


def test_a_database_name_in_a_data_access_envelope_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    ir["dataAccess"] = [{
        "id": "1-billing-view", "binding": {"strategy": "view-per-agent"},
        "exposedTo": ["1-billing-specialist"],
        "envelope": {"role": "readonly", "statementTimeoutMs": 2000, "maxRows": 500,
                     "schemas": ["postgres_billing"], "forbidden": ["DROP"]},
    }]
    assert "INFRA_LEAK_IN_INTENT" in _classes(_validate(root, ir))


# -- reflection : le critique n'est pas le redacteur --------------------------------
# `[REFLECTION_SELF_GRADING]` etait range parmi les anti-patterns REFUSES par la
# TOPOLOGY GATE (ORCHESTRATION-PATTERNS.md §4) et n'etait emis par rien : la classe
# vivait dans errors.py et dans la prose, jamais dans un controle. Trouve par le
# controle `errors.documented` du smoke.
def test_reflection_with_single_agent_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    ir["orchestration"]["rootPattern"] = "reflection"
    ir["orchestration"]["nodes"] = [
        n for n in ir["orchestration"]["nodes"] if n.get("kind") != "agent"
    ] + [{"id": "writer", "kind": "agent", "ref": ir["agents"][0]["id"]}]
    assert "REFLECTION_SELF_GRADING" in _classes(_validate(root, ir))


def test_reflection_self_loop_on_agent_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    ir["orchestration"]["rootPattern"] = "reflection"
    agent_node = next(n for n in ir["orchestration"]["nodes"] if n.get("kind") == "agent")
    ir["orchestration"]["edges"].append(
        {"from": agent_node["id"], "to": agent_node["id"], "condition": "needs_revision"}
    )
    assert "REFLECTION_SELF_GRADING" in _classes(_validate(root, ir))


def test_reflection_nested_in_a_single_agent_node_is_rejected(ok_ir) -> None:
    root, ir = ok_ir
    agent_node = next(n for n in ir["orchestration"]["nodes"] if n.get("kind") == "agent")
    agent_node["nestedPattern"] = "reflection"
    assert "REFLECTION_SELF_GRADING" in _classes(_validate(root, ir))


def test_reflection_with_two_distinct_agents_is_accepted(ok_ir) -> None:
    """Le pattern legitime ne doit pas devenir impraticable : deux agents distincts passent."""
    root, ir = ok_ir
    ir["orchestration"]["rootPattern"] = "reflection"
    assert "REFLECTION_SELF_GRADING" not in _classes(_validate(root, ir))


def test_stale_ir_is_rejected_after_source_edit(ok_ir) -> None:
    root, ir = ok_ir
    topo = root / "workspace/feats/topology/1-topology.md"
    text = topo.read_text(encoding="utf-8")
    topo.write_text(text.replace("  clarify --> finalize\n", "  clarify --> finalize\n  clarify --> classify\n", 1), encoding="utf-8")
    report = _validate(root, ir)
    assert "IR_STALE" in _classes(report)


def test_missing_ir_file(project: Path) -> None:
    code, out = run_main(validate_ir.main, ["--root", str(project), "--mission", "1", "--no-report"])
    assert code == 1 and "[IR_NOT_FOUND]" in out


# -- 8.bis Cohabitation outil dangereux <-> entree non maitrisee --------------------
# Controle ajoute apres coup : `[UNSAFE_TOOL_COHABITATION]` etait declare bloquant
# sans bypass dans la taxonomie et n'etait applique par rien. Trouve en sabotant
# l'IR a la main ; ces tests existent pour qu'il ne disparaisse pas au prochain
# refactoring.

def _billing_tool(ir: dict) -> dict:
    """L'outil `external-side-effect` porte par un agent expose (fixture ok)."""
    return next(t for t in ir["tools"] if t["id"] == "1-zendesk-create-ticket")


def test_destructive_tool_with_untrusted_input_is_refused(ok_ir) -> None:
    root, ir = ok_ir
    ir = copy.deepcopy(ir)
    _billing_tool(ir)["sideEffectClass"] = "write-destructive"
    assert "UNSAFE_TOOL_COHABITATION" in _classes(_validate(root, ir))


def test_destructive_cohabitation_has_no_bypass_via_safety_strategy(ok_ir) -> None:
    """Aucune strategie ne rachete l'irreversible : c'est tout l'interet de la classe."""
    root, ir = ok_ir
    ir = copy.deepcopy(ir)
    tool = _billing_tool(ir)
    tool["sideEffectClass"] = "write-destructive"
    tool["safetyStrategy"] = {
        "idempotency": "natural-key:conversation_id",
        "cap": {"perRun": 1},
        "confirmation": "always",
        "dryRunSupported": True,
    }
    assert "UNSAFE_TOOL_COHABITATION" in _classes(_validate(root, ir))


def test_external_side_effect_is_allowed_when_damage_is_bounded(ok_ir) -> None:
    """Le cas nominal de la fixture : ticket idempotent et plafonne -> autorise.

    Sans ce gradient la classe serait contournee partout, et une regle que tout
    le monde contourne ne protege plus personne.
    """
    root, ir = ok_ir
    report = _validate(root, copy.deepcopy(ir))
    assert "UNSAFE_TOOL_COHABITATION" not in _classes(report)


def test_external_side_effect_unbounded_is_refused(ok_ir) -> None:
    root, ir = ok_ir
    ir = copy.deepcopy(ir)
    _billing_tool(ir)["safetyStrategy"] = {"dryRunSupported": True}  # ni idempotence ni plafond
    assert "UNSAFE_TOOL_COHABITATION" in _classes(_validate(root, ir))


def test_no_cohabitation_finding_when_agent_reads_nothing_untrusted(ok_ir) -> None:
    root, ir = ok_ir
    ir = copy.deepcopy(ir)
    tool = _billing_tool(ir)
    tool["sideEffectClass"] = "write-destructive"
    for agent in ir["agents"]:
        agent["trustPosture"]["untrustedInputs"] = []
    assert "UNSAFE_TOOL_COHABITATION" not in _classes(_validate(root, ir))
