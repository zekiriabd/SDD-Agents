"""Traces — un seul format, celui des spans, et un coût recalculé depuis les tokens.

Ce que ces tests défendent, et le bug qu'ils ferment : `tracing.py` lisait une
grammaire d'« événements » que RIEN ne produisait. L'application générée écrit
des spans OTel-GenAI. Les conséquences étaient silencieuses plutôt que
bruyantes : `audit_tool_scope` ne voyait aucun appel d'outil dans une trace
réelle, donc aucun dépassement de périmètre, et le coût mesuré de G6 restait à
zéro, donc sous n'importe quel plafond.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sdda_lib import tracing
from sdda_lib.errors import Report
from sdda_scripts import audit_tool_scope, ir_compiler

RUN = "run-spans"


# ---------------------------------------------------------------------------
# Construction de traces
# ---------------------------------------------------------------------------
def writer(root: Path, run_id: str = RUN) -> tracing.TraceWriter:
    return tracing.TraceWriter(root, run_id)


def root_span(w: tracing.TraceWriter, *, ended: bool = True) -> None:
    w.emit("sdda.run 1", span_id="root", start="2026-09-21T10:00:00Z",
           end="2026-09-21T10:00:05Z" if ended else None,
           duration_ms=5000 if ended else None,
           attributes={"sdda.run.id": w.run_id, "sdda.mission.id": "1"})


def agent_span(w: tracing.TraceWriter, span_id: str, name: str, *, parent: str = "root",
               agent_id: str = "", start: str = "2026-09-21T10:00:01Z", **attrs) -> None:
    w.emit(f"invoke_agent {name}", span_id=span_id, parent_span_id=parent, start=start,
           end="2026-09-21T10:00:04Z",
           attributes={"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": name,
                       **({"gen_ai.agent.id": agent_id} if agent_id else {}), **attrs})


def tool_span(w: tracing.TraceWriter, span_id: str, tool: str, *, parent: str,
              tool_id: str = "", status: str = "OK", start: str = "2026-09-21T10:00:02Z") -> None:
    w.emit(f"execute_tool {tool}", span_id=span_id, parent_span_id=parent, start=start,
           end="2026-09-21T10:00:03Z", status=status,
           attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool,
                       **({"sdda.tool.id": tool_id} if tool_id else {}),
                       "sdda.tool.side_effect_class": "read-only"})


def chat_span(w: tracing.TraceWriter, span_id: str, *, parent: str, model: str = "claude-sonnet-5",
              tokens_in: int = 1000, tokens_out: int = 200, declared: float | None = None,
              **attrs) -> None:
    payload = {"gen_ai.operation.name": "chat", "gen_ai.request.model": model,
               "gen_ai.usage.input_tokens": tokens_in, "gen_ai.usage.output_tokens": tokens_out, **attrs}
    if declared is not None:
        payload["sdda.cost.usd"] = declared
    w.emit(f"chat {model}", span_id=span_id, parent_span_id=parent,
           start="2026-09-21T10:00:01Z", end="2026-09-21T10:00:02Z", attributes=payload)


# ---------------------------------------------------------------------------
# Le format, et un seul
# ---------------------------------------------------------------------------
def test_the_writer_produces_the_shape_the_application_produces(tmp_path: Path) -> None:
    w = writer(tmp_path)
    root_span(w)
    (span,) = list(tracing.read_spans(w.path))
    assert set(tracing.SPAN_FIELDS) <= set(span)
    assert span["run_id"] == RUN and span["trace_id"] == RUN and span["span_id"] == "root"
    assert "parent_span_id" not in span            # la racine se reconnaît à son absence


@pytest.mark.parametrize("name,attrs,role", [
    ("sdda.run 1", {}, "run"),
    ("invoke_agent billing", {"gen_ai.operation.name": "invoke_agent"}, "agent"),
    ("chat m", {"gen_ai.operation.name": "chat"}, "llm"),
    ("execute_tool t", {"gen_ai.operation.name": "execute_tool"}, "tool"),
    ("sdda.retrieve idx", {}, "retrieval"),
    ("sdda.guardrail g", {}, "guardrail"),
    ("quelque chose", {}, None),
])
def test_span_roles_come_from_semconv_then_from_the_sdda_prefix(name, attrs, role) -> None:
    assert tracing.span_role({"name": name, "attributes": attrs}) == role


def test_an_unknown_role_is_named_rather_than_ignored(tmp_path: Path) -> None:
    w = writer(tmp_path)
    root_span(w)
    w.emit("mystere", span_id="x", parent_span_id="root", attributes={})
    assert any("rôle inconnu" in p for p in tracing.summarize(w.path).problems)


def test_the_legacy_event_format_is_refused_not_half_read(tmp_path: Path) -> None:
    path = tracing.trace_path(tmp_path, RUN)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in [
        {"ts": "2026-09-21T10:00:00Z", "runId": RUN, "kind": "run_start", "missionId": "1"},
        {"ts": "2026-09-21T10:00:01Z", "runId": RUN, "kind": "agent_turn", "agentId": "billing",
         "tokensIn": 900, "costUsd": 0.012},
    ]) + "\n", encoding="utf-8")
    summary = tracing.summarize(path)
    assert any("ancien format" in p for p in summary.problems)
    assert summary.tokens_in == 0 and summary.cost_usd == 0.0 and summary.agents == []


def test_a_parent_comes_before_its_child_when_both_start_at_the_same_instant(tmp_path: Path) -> None:
    """L'horloge de Windows donne souvent le même horodatage à un agent et à son outil.

    L'exportateur écrit dans l'ordre des FINS — l'outil avant l'agent. À début
    égal, le tri stable gardait cet ordre et la trajectoire mettait l'outil
    avant l'agent qui l'avait appelé : un faux `[TRAJECTORY_VIOLATION]` en G6.
    """
    same = "2026-09-21T10:00:01Z"
    w = writer(tmp_path)
    tool_span(w, "t1", "lookup", parent="a1", start=same)     # écrit en premier : il finit en premier
    agent_span(w, "a1", "billing", start=same)
    root_span(w)
    assert tracing.summarize(w.path).trajectory == ["billing", "tool:lookup"]


# ---------------------------------------------------------------------------
# Le coût est recalculé, jamais relu
# ---------------------------------------------------------------------------
def test_cost_is_recomputed_from_tokens(tmp_path: Path) -> None:
    w = writer(tmp_path)
    root_span(w)
    agent_span(w, "a1", "billing")
    chat_span(w, "c1", parent="a1", tokens_in=1000, tokens_out=200)
    summary = tracing.summarize(w.path)
    # claude-sonnet-5 : $2.00 / MTok en entrée, $10.00 en sortie.
    assert summary.cost_usd == pytest.approx(1000 * 2.0 / 1e6 + 200 * 10.0 / 1e6)
    assert summary.tokens_in == 1000 and summary.tokens_out == 200


def test_a_declared_cost_that_does_not_follow_its_tokens_is_flagged(tmp_path: Path) -> None:
    """Un chiffre qu'on relit sans le recalculer n'est pas une mesure, c'est une déclaration."""
    w = writer(tmp_path)
    root_span(w)
    agent_span(w, "a1", "billing")
    chat_span(w, "c1", parent="a1", declared=0.0001)     # très en dessous du vrai coût
    summary = tracing.summarize(w.path)
    assert any("ne suit pas ses propres tokens" in p for p in summary.problems)
    assert summary.cost_declared_usd == pytest.approx(0.0001)
    assert summary.cost_usd > summary.cost_declared_usd   # le recalcul fait foi


def test_a_declared_cost_that_matches_is_not_flagged(tmp_path: Path) -> None:
    w = writer(tmp_path)
    root_span(w)
    agent_span(w, "a1", "billing")
    chat_span(w, "c1", parent="a1", tokens_in=1000, tokens_out=200, declared=0.004)
    assert tracing.summarize(w.path).problems == []


def test_an_unpriced_model_is_a_problem_not_a_zero(tmp_path: Path) -> None:
    """Un zéro passerait sous n'importe quel plafond sans rien dire."""
    w = writer(tmp_path)
    root_span(w)
    agent_span(w, "a1", "billing")
    chat_span(w, "c1", parent="a1", model="modele-maison-v3")
    problems = tracing.summarize(w.path).problems
    assert any("BUDGET_PRICING_UNKNOWN" in p for p in problems)


def test_missing_usage_is_a_problem(tmp_path: Path) -> None:
    w = writer(tmp_path)
    root_span(w)
    agent_span(w, "a1", "billing")
    w.emit("chat claude-sonnet-5", span_id="c1", parent_span_id="a1",
           attributes={"gen_ai.operation.name": "chat", "gen_ai.request.model": "claude-sonnet-5"})
    assert any("coût non recalculable" in p for p in tracing.summarize(w.path).problems)


# ---------------------------------------------------------------------------
# L'arbre : profondeur de délégation et agent responsable
# ---------------------------------------------------------------------------
def test_delegation_depth_is_read_from_the_tree(tmp_path: Path) -> None:
    w = writer(tmp_path)
    root_span(w)
    agent_span(w, "a1", "superviseur")
    agent_span(w, "a2", "billing", parent="a1", start="2026-09-21T10:00:02Z")
    agent_span(w, "a3", "refund", parent="a2", start="2026-09-21T10:00:03Z")
    summary = tracing.summarize(w.path)
    assert summary.hops == 3 and summary.max_depth == 3
    assert summary.agents == ["superviseur", "billing", "refund"]


def test_a_tool_call_is_attributed_to_its_parent_agent_even_in_parallel(tmp_path: Path) -> None:
    """Le cas que « le dernier agent vu » ratait : deux agents actifs en même temps."""
    w = writer(tmp_path)
    root_span(w)
    agent_span(w, "a1", "billing")
    agent_span(w, "a2", "refund")
    tool_span(w, "t1", "invoice_lookup", parent="a1", start="2026-09-21T10:00:02Z")
    tool_span(w, "t2", "issue_refund", parent="a2", start="2026-09-21T10:00:03Z")
    calls = {c.tool: c.agent for c in tracing.summarize(w.path).tool_calls}
    assert calls == {"invoice_lookup": "billing", "issue_refund": "refund"}


def test_a_tool_call_without_an_agent_parent_has_no_owner(tmp_path: Path) -> None:
    w = writer(tmp_path)
    root_span(w)
    tool_span(w, "t1", "invoice_lookup", parent="root")
    (call,) = tracing.summarize(w.path).tool_calls
    assert call.agent == "" and call.tool == "invoice_lookup"


def test_a_failed_tool_call_is_recorded_as_such(tmp_path: Path) -> None:
    w = writer(tmp_path)
    root_span(w)
    agent_span(w, "a1", "billing")
    tool_span(w, "t1", "invoice_lookup", parent="a1", status="ERROR")
    (call,) = tracing.summarize(w.path).tool_calls
    assert call.ok is False


def test_a_cycle_in_the_parent_chain_does_not_hang(tmp_path: Path) -> None:
    w = writer(tmp_path)
    w.emit("invoke_agent a", span_id="x", parent_span_id="y",
           attributes={"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "a"})
    w.emit("invoke_agent b", span_id="y", parent_span_id="x",
           attributes={"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "b"})
    assert tracing.summarize(w.path).hops == 2


# ---------------------------------------------------------------------------
# La racine
# ---------------------------------------------------------------------------
def test_a_trace_without_a_root_span_is_incomplete(tmp_path: Path) -> None:
    w = writer(tmp_path)
    agent_span(w, "a1", "billing", parent="")
    summary = tracing.summarize(w.path)
    assert not summary.complete
    assert any("aucun span racine" in p for p in summary.problems)


def test_two_root_spans_mean_two_runs_in_one_file(tmp_path: Path) -> None:
    w = writer(tmp_path)
    root_span(w)
    w.emit("sdda.run 2", span_id="root2", start="2026-09-21T11:00:00Z", end="2026-09-21T11:00:01Z",
           attributes={"sdda.mission.id": "2"})
    assert any("un fichier de trace vaut pour UN run" in p for p in tracing.summarize(w.path).problems)


def test_latency_comes_from_the_root_span(tmp_path: Path) -> None:
    w = writer(tmp_path)
    root_span(w)
    assert tracing.summarize(w.path).latency_ms == 5000


def test_latency_falls_back_to_start_and_end(tmp_path: Path) -> None:
    w = writer(tmp_path)
    w.emit("sdda.run 1", span_id="root", start="2026-09-21T10:00:00Z", end="2026-09-21T10:00:02Z",
           attributes={"sdda.mission.id": "1"})
    assert tracing.summarize(w.path).latency_ms == 2000


# ---------------------------------------------------------------------------
# L'audit de scope : le faux vert que ce format ferme
# ---------------------------------------------------------------------------
def _ir(project: Path) -> tuple[Path, dict]:
    path, report = ir_compiler.compile_to_file(project, 1)
    assert report.ok, [f.cls for f in report.errors]
    return path, json.loads(path.read_text(encoding="utf-8-sig"))


def _audit(project: Path, ir_path: Path) -> Report:
    return audit_tool_scope.run(project, ir_path, True)


def test_a_tool_call_outside_the_ir_wiring_is_caught_in_a_span_trace(project: Path) -> None:
    """Avant, l'audit lisait des `kind` : sur une trace de spans il ne voyait rien,
    donc aucun dépassement — un faux vert sur un contrôle de sûreté."""
    ir_path, ir = _ir(project)
    agent, forbidden = next(
        (a, t["id"]) for a in ir["agents"] for t in ir["tools"]
        if t["id"] not in (a.get("tools") or [])
    )

    w = writer(project, "run-scope")
    root_span(w)
    agent_span(w, "a1", agent["id"], agent_id=agent["id"])
    tool_span(w, "t1", forbidden, parent="a1", tool_id=forbidden)

    report = _audit(project, ir_path)
    assert "TOOL_SCOPE_EXCESS" in {f.cls for f in report.errors}
    assert forbidden in " ".join(f.message for f in report.errors)


def test_a_tool_call_inside_the_wiring_is_clean(project: Path) -> None:
    ir_path, ir = _ir(project)
    agent = next(a for a in ir["agents"] if a.get("tools"))
    allowed = agent["tools"][0]

    w = writer(project, "run-scope-ok")
    root_span(w)
    agent_span(w, "a1", agent["id"], agent_id=agent["id"])
    tool_span(w, "t1", allowed, parent="a1", tool_id=allowed)

    assert "TOOL_SCOPE_EXCESS" not in {f.cls for f in _audit(project, ir_path).errors}


def test_a_tool_call_attached_to_nobody_is_a_warning(project: Path) -> None:
    ir_path, _ = _ir(project)
    w = writer(project, "run-scope-orphan")
    root_span(w)
    tool_span(w, "t1", "invoice_lookup", parent="root")
    assert "TRACE_MALFORMED" in {f.cls for f in _audit(project, ir_path).warnings}
