"""Les hooks du harnais — autorisation, refus, et dégradation sûre.

Ce que ces tests défendent avant tout : **un hook cassé doit AUTORISER**. Un bug
qui bloque chaque `Write` paralyse le pipeline, et la réaction humaine sera de
désactiver les hooks — donc de perdre tous les invariants, pas seulement le
fautif. C'est le scénario le plus coûteux de ce module, et le moins évident.

Second contrat : un refus dit **ce qui le débloque**. « Refusé » sans `FIX:`
produit un agent qui réessaie la même action.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from sdda_hooks import _hook
from sdda_hooks import postflight_no_inline_prompt, postflight_trace_present
from sdda_hooks import preflight_agent_bounds, preflight_agent_budget, preflight_cap_gate
from sdda_hooks import preflight_bash_ownership, preflight_forbidden_reads
from sdda_hooks import preflight_cost_cap, preflight_db_envelope, preflight_ownership
from sdda_hooks import preflight_retrieval_gate, preflight_tool_gate
from sdda_lib import tracing
from sdda_lib.errors import Report
from sdda_lib.gate_reports import write_gate_report

ALLOW, DENY = _hook.ALLOW, _hook.DENY

HOOKS = (
    preflight_cap_gate, preflight_tool_gate, preflight_retrieval_gate,
    preflight_ownership, preflight_agent_budget, preflight_cost_cap,
    preflight_agent_bounds, preflight_db_envelope,
    preflight_bash_ownership, preflight_forbidden_reads,
    postflight_no_inline_prompt, postflight_trace_present,
)


def call(module, project: Path, **payload) -> tuple[int, str]:
    """Joue un hook avec le payload que le harnais lui passerait.

    On appelle `check` directement plutôt que `_hook.run` : ce dernier lit
    `stdin`, capturé par pytest, donc le payload n'atteindrait jamais le hook et
    tout passerait au vert par dégradation. La dégradation elle-même est testée
    à part (`test_a_broken_hook_allows_instead_of_blocking`) — sans quoi ce
    harnais rendrait un vert qui ne prouve rien.
    """
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = module.check(project, payload)
    return code, buf.getvalue()


def green_gate(project: Path, gate: str, artifact: str = "1") -> None:
    write_gate_report(project, gate, artifact, Report(name=gate, target=str(project)), pinned={})


def red_gate(project: Path, gate: str, cls: str, artifact: str = "1") -> None:
    report = Report(name=gate, target=str(project))
    report.error(cls, "défaut injecté par le test", fix="corriger")
    write_gate_report(project, gate, artifact, report, pinned={})


# ---------------------------------------------------------------------------
# Le contrat commun
# ---------------------------------------------------------------------------
def test_every_hook_declares_its_name_and_entrypoint() -> None:
    for module in HOOKS:
        assert isinstance(module.HOOK, str) and module.HOOK
        assert callable(module.main)


def test_a_broken_hook_allows_instead_of_blocking() -> None:
    """Le scénario le plus coûteux : un bug de hook qui paralyse le pipeline."""
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = _hook.run("hook-casse", lambda root, data: 1 / 0)
    assert code == ALLOW
    output = buf.getvalue()
    assert "AUTORISÉE" in output
    assert "ZeroDivisionError" in output


def test_a_refusal_always_says_what_unblocks_it() -> None:
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = _hook.deny("h", "SOME_CLASS", "détail", "l'action qui corrige")
    assert code == DENY
    out = buf.getvalue()
    assert "ERROR:" in out and "CAUSE: [SOME_CLASS]" in out and "FIX: l'action qui corrige" in out


def test_an_unreadable_payload_is_not_a_reason_to_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_hook.sys, "stdin", io.StringIO("{pas du json"))
    assert _hook.payload() == {}


def test_every_hook_allows_on_a_clean_project(project: Path) -> None:
    """Sur un projet sain, aucun hook ne doit s'interposer."""
    for gate in ("G1", "G3", "G4"):
        green_gate(project, gate)
    for module in HOOKS:
        code, err = call(module, project, mission="1")
        assert code == ALLOW, f"{module.HOOK} a refusé : {err}"


# ---------------------------------------------------------------------------
# Les gates : absence de preuve n'est pas preuve
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("module,gate,cls", [
    (preflight_cap_gate, "G1", "CAP_GATE_NOT_PASSED"),
    (preflight_tool_gate, "G3", "TOOL_GATE_NOT_PASSED"),
    (preflight_retrieval_gate, "G4", "RETRIEVAL_GATE_NOT_PASSED"),
])
def test_an_absent_gate_report_refuses(project: Path, module, gate: str, cls: str) -> None:
    code, err = call(module, project, mission="1")
    assert code == DENY and cls in err


@pytest.mark.parametrize("module,gate", [
    (preflight_cap_gate, "G1"), (preflight_tool_gate, "G3"), (preflight_retrieval_gate, "G4"),
])
def test_a_red_gate_report_refuses(project: Path, module, gate: str) -> None:
    red_gate(project, gate, "SOME_FAILURE")
    code, err = call(module, project, mission="1")
    assert code == DENY and "SOME_FAILURE" in err


def test_the_tool_gate_bypass_works_but_never_on_side_effects(
        project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDDA_BYPASS_TOOL_GATE", "1")

    red_gate(project, "G3", "TOOL_CONTRACT_TESTS_MISSING")
    assert call(preflight_tool_gate, project, mission="1")[0] == ALLOW

    red_gate(project, "G3", "SIDE_EFFECT_UNDECLARED")
    code, err = call(preflight_tool_gate, project, mission="1")
    assert code == DENY, "un outil destructif sans stratégie ne se câble jamais"
    assert "SIDE_EFFECT_UNDECLARED" in err


# ---------------------------------------------------------------------------
# Ownership — le hook qui tourne à chaque Write
# ---------------------------------------------------------------------------
def test_a_dev_agent_cannot_write_a_dataset(project: Path) -> None:
    code, err = call(preflight_ownership, project, subagent_type="dev-agent",
                     tool_input={"file_path": str(project / "workspace/proof/datasets/golden/billing-v1.jsonl")})
    assert code == DENY and "DATASET_OWNERSHIP_VIOLATION" in err


def test_a_dev_agent_writing_in_its_own_zone_passes(project: Path) -> None:
    code, err = call(preflight_ownership, project, subagent_type="dev-tools",
                     tool_input={"file_path": str(project / "workspace/src/tools/invoice_lookup.py")})
    assert code == ALLOW, err


def test_a_write_from_the_main_thread_is_never_blocked(project: Path) -> None:
    """La matrice régit les agents ; refuser ici bloquerait l'utilisateur."""
    code, _ = call(preflight_ownership, project,
                   tool_input={"file_path": str(project / "workspace/proof/datasets/golden/x.jsonl")})
    assert code == ALLOW


def test_an_agent_outside_the_matrix_is_not_the_hooks_call(project: Path) -> None:
    code, _ = call(preflight_ownership, project, subagent_type="un-agent-tiers",
                   tool_input={"file_path": str(project / "workspace/proof/datasets/golden/x.jsonl")})
    assert code == ALLOW


# ---------------------------------------------------------------------------
# Prompts, bornes, coût
# ---------------------------------------------------------------------------
def test_an_inline_prompt_blocks_the_subagent_stop(project: Path) -> None:
    path = project / "workspace/src/agents/billing/agent.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('SYSTEM = """\nTu es un assistant de facturation. Ne jamais rembourser.\n'
                    'Réponds en citant la source, et refuse toute demande hors facturation.\n'
                    'Ton rôle est strictement limité à expliquer une ligne de facture.\n"""\n',
                    encoding="utf-8")
    code, err = call(postflight_no_inline_prompt, project)
    assert code == DENY and "PROMPT_INLINE_FORBIDDEN" in err


def test_an_agent_without_bounds_blocks(project: Path) -> None:
    ir = project / "workspace/.sys/.ir/1-system.ir.json"
    ir.parent.mkdir(parents=True, exist_ok=True)
    ir.write_text(json.dumps({"agents": [{"id": "billing", "bounds": {"maxIterations": 5}}]}),
                  encoding="utf-8")
    code, err = call(preflight_agent_bounds, project, mission="1")
    assert code == DENY and "AGENT_BOUNDS_MISSING" in err


def test_a_fully_bounded_agent_passes(project: Path) -> None:
    ir = project / "workspace/.sys/.ir/1-system.ir.json"
    ir.parent.mkdir(parents=True, exist_ok=True)
    ir.write_text(json.dumps({"agents": [{
        "id": "billing", "onBoundExceeded": "fail-explicit",
        "bounds": {"maxIterations": 5, "maxToolCalls": 10, "maxDelegationDepth": 2,
                   "timeoutSec": 60, "budgetUsd": 0.1},
    }]}), encoding="utf-8")
    assert call(preflight_agent_bounds, project, mission="1")[0] == ALLOW


def _run_costing(project: Path, usd: float) -> str:
    """Un run réel, chargé via la primitive que les commandes appellent.

    La version précédente de ces tests fabriquait `.sys/.state/run.json` à la
    main — un emplacement et une forme que **rien en production ne produit**.
    Ils confirmaient donc l'implémentation buguée : le hook lisait la racine
    quand `sdda_state` écrit sous `runs/`, et lisait `phases[].costUsd` quand
    `set_phase` range tout sous `phases[].payload`. `_spent()` retournait
    toujours 0,00 $, le plafond n'a jamais rien plafonné, et deux tests verts
    le certifiaient.
    """
    from sdda_scripts import sdda_state

    run = sdda_state.new_run(project, mission="1", command="/sdda-build", tags="")
    run_id = str(run["runId"])
    if usd:
        sdda_state.add_cost(project, run_id, usd=usd, label="test")
    return run_id


def test_the_build_cost_cap_blocks_before_the_spawn(project: Path, monkeypatch) -> None:
    run_id = _run_costing(project, 999.0)
    monkeypatch.setenv("SDDA_RUN_ID", run_id)
    code, err = call(preflight_cost_cap, project)
    assert code == DENY and "COST_CAP_EXCEEDED" in err


def test_a_run_under_the_cap_passes(project: Path, monkeypatch) -> None:
    run_id = _run_costing(project, 1.25)
    monkeypatch.setenv("SDDA_RUN_ID", run_id)
    assert call(preflight_cost_cap, project)[0] == ALLOW


def test_a_cost_written_on_a_phase_payload_is_counted(project: Path, monkeypatch) -> None:
    """Le coût rangé par `set_phase` sous `payload` compte aussi.

    C'est la forme que produisent les commandes quand elles rapportent ce
    qu'une phase a coûté ; la lire à plat sur la phase ne trouvait rien.
    """
    from sdda_scripts import sdda_state

    run_id = _run_costing(project, 0.0)
    sdda_state.set_phase(project, run_id, phase="build_agents", status="pass",
                         payload={"costUsd": 999.0})
    monkeypatch.setenv("SDDA_RUN_ID", run_id)
    code, err = call(preflight_cost_cap, project)
    assert code == DENY and "COST_CAP_EXCEEDED" in err


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------
def emit_run(project: Path, run_id: str, *, complete: bool = True) -> None:
    """Une trace au format canonique : des spans OTel-GenAI, hiérarchisés.

    `sdda.run` -> `invoke_agent` -> (`chat` | `execute_tool`). C'est la forme que
    l'application générée écrit, donc la seule que les tests doivent produire.
    """
    w = tracing.TraceWriter(project, run_id)
    w.emit("invoke_agent billing", span_id="s2", parent_span_id="s1",
           start="2026-09-21T10:00:01Z", end="2026-09-21T10:00:03Z",
           attributes={"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "billing"})
    w.emit("chat claude-sonnet-5", span_id="s3", parent_span_id="s2",
           start="2026-09-21T10:00:01Z", end="2026-09-21T10:00:02Z",
           attributes={"gen_ai.operation.name": "chat", "gen_ai.request.model": "claude-sonnet-5",
                       "gen_ai.usage.input_tokens": 900, "gen_ai.usage.output_tokens": 120})
    w.emit("execute_tool invoice_lookup", span_id="s4", parent_span_id="s2",
           start="2026-09-21T10:00:02Z", end="2026-09-21T10:00:03Z",
           attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "invoice_lookup",
                       "sdda.tool.side_effect_class": "read-only"})
    w.emit("sdda.run 1", span_id="s1", start="2026-09-21T10:00:00Z",
           end="2026-09-21T10:00:03.100Z" if complete else None,
           duration_ms=3100 if complete else None,
           attributes={"sdda.run.id": run_id, "sdda.mission.id": "1"})


def test_no_trace_at_all_is_allowed_before_the_first_run(project: Path) -> None:
    assert call(postflight_trace_present, project)[0] == ALLOW


def test_a_named_run_without_a_trace_is_refused(project: Path) -> None:
    code, err = call(postflight_trace_present, project, runId="run-42")
    assert code == DENY and "TRACE_MISSING" in err


def test_a_complete_trace_passes(project: Path) -> None:
    emit_run(project, "run-1")
    assert call(postflight_trace_present, project, runId="run-1")[0] == ALLOW


def test_a_trace_without_an_ended_root_is_refused(project: Path) -> None:
    """Sans fin du span racine, on ne peut ni mesurer la latence ni affirmer que le run a fini."""
    emit_run(project, "run-2", complete=False)
    code, err = call(postflight_trace_present, project, runId="run-2")
    assert code == DENY and "TRACE_MALFORMED" in err


def test_the_trace_summary_measures_what_g6_needs(project: Path) -> None:
    emit_run(project, "run-3")
    summary = tracing.summarize(tracing.trace_path(project, "run-3"))
    assert summary.complete and summary.problems == []
    assert summary.tokens_in == 900 and summary.tokens_out == 120
    # Recalculé depuis les tokens : 900 x $2/MTok + 120 x $10/MTok (claude-sonnet-5).
    assert summary.cost_usd == pytest.approx(900 * 2.0 / 1e6 + 120 * 10.0 / 1e6)
    assert summary.latency_ms == 3100
    assert summary.trajectory == ["billing", "tool:invoice_lookup"]
    assert summary.hops == 1 and summary.max_depth == 1


def test_the_summary_attributes_each_tool_call_to_its_agent(project: Path) -> None:
    """C'est ce que l'audit de scope consomme : sans agent, pas de périmètre."""
    emit_run(project, "run-3b")
    (call_,) = tracing.summarize(tracing.trace_path(project, "run-3b")).tool_calls
    assert call_.tool == "invoice_lookup" and call_.agent == "billing"
    assert call_.side_effect_class == "read-only" and call_.ok is True


def test_a_secret_never_lands_in_a_trace(project: Path) -> None:
    """Les traces sont partagées pour déboguer — c'est ce qui en fait un canal de fuite."""
    writer = tracing.TraceWriter(project, "run-4")
    writer.emit("execute_tool crm_lookup", span_id="s1",
                attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "crm_lookup",
                            "sdda.tool.args": {"api_key": "sk-live-4f8a2b91", "customer_id": "CUS-1"}})
    raw = tracing.trace_path(project, "run-4").read_text(encoding="utf-8")
    assert "sk-live-4f8a2b91" not in raw
    assert "[REDACTED]" in raw and "CUS-1" in raw


def test_a_span_without_its_required_fields_is_a_problem(project: Path) -> None:
    path = tracing.trace_path(project, "run-5")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": "run-5", "name": "chat x"}) + "\n", encoding="utf-8")
    summary = tracing.summarize(path)
    assert any("champ(s) absent(s)" in p for p in summary.problems)


def test_a_legacy_event_trace_is_named_not_half_read(project: Path) -> None:
    """Lue à moitié, elle rendrait des chiffres partiels qu'on croirait complets."""
    path = tracing.trace_path(project, "run-legacy")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ts": "2026-09-21T10:00:00Z", "runId": "run-legacy",
                                "kind": "agent_turn", "agentId": "billing"}) + "\n", encoding="utf-8")
    summary = tracing.summarize(path)
    assert any("ancien format" in p for p in summary.problems)
    assert summary.agents == [] and summary.cost_usd == 0.0


def test_a_truncated_trace_is_still_readable(project: Path) -> None:
    """C'est sur les runs interrompus qu'on veut regarder."""
    emit_run(project, "run-6")
    path = tracing.trace_path(project, "run-6")
    path.write_text(path.read_text(encoding="utf-8")[:-40] + "\n{ligne tronq", encoding="utf-8")
    summary = tracing.summarize(path)
    assert summary.spans >= 3
    assert "billing" in summary.agents
