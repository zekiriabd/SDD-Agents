"""Traces de CONSTRUCTION — ce que le framework coûte, agent par agent.

ARCHITECTURE §8 promet une trace pour « tout run, de construction comme
d'exécution ». Celle du produit existait, celle de la construction non : on
savait mesurer ce que l'application coûte à l'usage, et pas ce que le framework
coûte à la fabriquer — alors que c'est la facture qu'on voit en premier, et la
seule que `MaxCostPerRun` prétend plafonner.
"""
from __future__ import annotations

from pathlib import Path

from conftest import run_main
from sdda_lib import tracing
from sdda_scripts import build_trace, sdda_state


def new_run(project: Path) -> str:
    code, out = run_main(sdda_state.main, ["new-run", "--root", str(project), "--mission", "1",
                                           "--command", "/sdda-build"])
    assert code == 0
    return out.strip()


def trace_agent(project: Path, run_id: str, *args: str) -> tuple[int, str]:
    return run_main(build_trace.main, ["agent", "--root", str(project), "--run-id", run_id, *args])


def summary_of(project: Path, run_id: str) -> tracing.TraceSummary:
    return tracing.summarize(tracing.trace_path(project, run_id))


# ---------------------------------------------------------------------------
# Un span par invocation de Developer Agent
# ---------------------------------------------------------------------------
def test_an_agent_invocation_lands_as_a_span(project: Path) -> None:
    run_id = new_run(project)
    code, _ = trace_agent(project, run_id, "--agent", "dev-agent", "--item", "billing",
                          "--phase", "build_agents", "--tier", "deep",
                          "--cost-usd", "0.42", "--duration-ms", "61000", "--iterations", "2")
    assert code == 0
    (span,) = list(tracing.read_spans(tracing.trace_path(project, run_id)))
    assert span["name"] == "sdda.build.agent dev-agent"
    assert span["parent_span_id"] == sdda_state.TRACE_ROOT_SPAN_ID
    assert tracing.span_role(span) == "build_agent"
    attrs = span["attributes"]
    assert attrs[tracing.A_BUILD_ITEM] == "billing" and attrs[tracing.A_BUILD_TIER] == "deep"
    assert attrs[tracing.A_COST_DECLARED] == 0.42 and attrs[tracing.A_BUILD_ITERATIONS] == 2
    assert span["duration_ms"] == 61000 and span["start"] < span["end"]


def test_options_left_out_do_not_land_as_null_attributes(project: Path) -> None:
    """Un `null` par option non passée alourdit la trace sans rien dire."""
    run_id = new_run(project)
    trace_agent(project, run_id, "--agent", "dev-prompt")
    (span,) = list(tracing.read_spans(tracing.trace_path(project, run_id)))
    assert set(span["attributes"]) == {tracing.A_BUILD_AGENT}


def test_a_span_without_a_current_run_is_a_named_error(project: Path, monkeypatch) -> None:
    monkeypatch.delenv("SDDA_RUN_ID", raising=False)
    code, out = run_main(build_trace.main, ["agent", "--root", str(project), "--agent", "dev-agent"])
    assert code == 1 and "STATE_RUN_NOT_FOUND" in out


def test_the_run_id_comes_from_the_environment_by_default(project: Path, monkeypatch) -> None:
    run_id = new_run(project)
    monkeypatch.setenv("SDDA_RUN_ID", run_id)
    code, _ = run_main(build_trace.main, ["agent", "--root", str(project), "--agent", "dev-tools"])
    assert code == 0 and tracing.trace_path(project, run_id).is_file()


# ---------------------------------------------------------------------------
# Le budget de contexte
# ---------------------------------------------------------------------------
def test_a_context_budget_overrun_is_reported_at_write_time(project: Path) -> None:
    run_id = new_run(project)
    code, out = trace_agent(project, run_id, "--agent", "architect-topology",
                            "--budget-bytes", "180000", "--budget-bytes-used", "240000")
    assert code == 0                                   # un WARN ne bloque pas l'écriture
    assert "CONTEXT_BUDGET_EXCEEDED" in out


def test_a_context_budget_overrun_is_also_visible_in_the_summary(project: Path) -> None:
    run_id = new_run(project)
    trace_agent(project, run_id, "--agent", "architect-topology",
                "--budget-bytes", "180000", "--budget-bytes-used", "240000")
    summary = summary_of(project, run_id)
    assert any("tronquée et confiante" in p for p in summary.problems)
    assert summary.budget_bytes_used == 240000


def test_a_budget_within_bounds_says_nothing(project: Path) -> None:
    run_id = new_run(project)
    code, out = trace_agent(project, run_id, "--agent", "architect-topology",
                            "--budget-bytes", "180000", "--budget-bytes-used", "142000")
    assert code == 0 and "CONTEXT_BUDGET_EXCEEDED" not in out


# ---------------------------------------------------------------------------
# Les gates
# ---------------------------------------------------------------------------
def test_a_red_gate_span_carries_an_error_status(project: Path) -> None:
    run_id = new_run(project)
    code, _ = run_main(build_trace.main, ["gate", "--root", str(project), "--run-id", run_id,
                                          "--gate", "G5", "--verdict", "red",
                                          "--error-class", "AGENT_EVAL_FAILED"])
    assert code == 0
    (span,) = list(tracing.read_spans(tracing.trace_path(project, run_id)))
    assert span["status"] == "ERROR" and tracing.span_role(span) == "gate"
    assert span["attributes"]["sdda.gate.error_class"] == "AGENT_EVAL_FAILED"


def test_a_green_gate_span_is_ok(project: Path) -> None:
    run_id = new_run(project)
    run_main(build_trace.main, ["gate", "--root", str(project), "--run-id", run_id,
                               "--gate", "G2", "--verdict", "green"])
    (span,) = list(tracing.read_spans(tracing.trace_path(project, run_id)))
    assert span["status"] == "OK"


# ---------------------------------------------------------------------------
# La fermeture : `end-run` écrit la racine
# ---------------------------------------------------------------------------
def test_end_run_closes_the_trace_with_a_root_span(project: Path) -> None:
    run_id = new_run(project)
    trace_agent(project, run_id, "--agent", "dev-agent", "--cost-usd", "0.42")
    sdda_state.set_phase(project, run_id, phase="build_agents", status="pass")
    sdda_state.end_run(project, run_id)

    summary = summary_of(project, run_id)
    assert summary.complete and summary.problems == []
    assert summary.roles["run"] == 1 and summary.roles["build_agent"] == 1


def test_without_end_run_the_build_trace_has_no_root(project: Path) -> None:
    """C'est ce qui ferait refuser la trace par postflight_trace_present."""
    run_id = new_run(project)
    trace_agent(project, run_id, "--agent", "dev-agent")
    assert any("aucun span racine" in p for p in summary_of(project, run_id).problems)


def test_the_root_span_carries_the_cumulative_build_cost(project: Path) -> None:
    run_id = new_run(project)
    sdda_state.add_cost(project, run_id, usd=1.25, label="dev-agent billing")
    sdda_state.end_run(project, run_id, status="pass")
    (root,) = [s for s in tracing.read_spans(tracing.trace_path(project, run_id))
               if s["span_id"] == sdda_state.TRACE_ROOT_SPAN_ID]
    assert root["attributes"][tracing.A_COST_DECLARED] == 1.25
    assert root["attributes"]["sdda.command"] == "/sdda-build"


def test_a_failed_run_closes_with_an_error_status(project: Path) -> None:
    run_id = new_run(project)
    sdda_state.set_phase(project, run_id, phase="build_agents", status="fail")
    sdda_state.end_run(project, run_id)
    (root,) = [s for s in tracing.read_spans(tracing.trace_path(project, run_id))
               if s["span_id"] == sdda_state.TRACE_ROOT_SPAN_ID]
    assert root["status"] == "ERROR"


# ---------------------------------------------------------------------------
# Les deux coûts ne se mélangent pas
# ---------------------------------------------------------------------------
def test_build_cost_is_declared_and_kept_apart_from_the_product_cost(project: Path) -> None:
    """Le coût de construction vient du harnais, celui du produit vient des tokens.

    Les additionner ferait passer un chiffre invérifiable pour une mesure.
    """
    run_id = new_run(project)
    trace_agent(project, run_id, "--agent", "dev-agent", "--cost-usd", "0.42", "--iterations", "2")
    trace_agent(project, run_id, "--agent", "dev-tools", "--cost-usd", "0.18", "--iterations", "1")
    sdda_state.end_run(project, run_id, status="pass")

    summary = summary_of(project, run_id)
    assert summary.build_cost_usd == 0.6           # 0.42 + 0.18, déclarés
    assert summary.cost_usd == 0.0                 # aucun span `chat` : rien à recalculer
    assert summary.build_agents == {"dev-agent": 1, "dev-tools": 1}
    assert summary.build_iterations == 3
    assert summary.to_dict()["buildCostUsd"] == 0.6
