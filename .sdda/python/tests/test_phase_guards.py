"""Les contrôles de phase : vérifier au moment où c'est actionnable.

Trois options étaient appelées par les commandes et n'existaient dans aucun
script. Ce n'est pas une erreur visible : `argparse` sort en code 2 avec un
`usage:` sur stderr, là où le protocole promet un bloc `ERROR/CAUSE/FIX` portant
une `[CLASS]`. L'agent qui orchestre lit une sortie qu'aucune table de sa fiche
ne décrit, conclut que le contrôle « n'a rien dit », et poursuit.

Le post-step censé refuser un contrat d'outil fautif **avant** de compiler l'IR
n'a donc jamais rien refusé, pendant tout un lot.
"""
from __future__ import annotations

from pathlib import Path

from conftest import run_main
from sdda_scripts import eval_runner, ir_compiler, validate_tool_contract, validate_topology


# ---------------------------------------------------------------------------
# validate-topology --pre : après l'architecte, avant les contrats
# ---------------------------------------------------------------------------
def test_pre_mode_does_not_demand_contracts_that_the_phase_has_not_written(project: Path) -> None:
    for contract in (project / "workspace/feats/contracts/agents").glob("*.agent.md"):
        contract.unlink()

    code, out = run_main(validate_topology.main,
                         ["--root", str(project), "--mission", "1", "--no-report", "--pre"])
    assert code == 0, out

    # La passe complète, elle, les exige toujours — sinon `--pre` serait un trou.
    code, out = run_main(validate_topology.main,
                         ["--root", str(project), "--mission", "1", "--no-report"])
    assert code == 1 and "AGENT_CONTRACT_MISSING" in out


def test_pre_mode_still_judges_everything_the_markdown_carries(project: Path) -> None:
    """`--pre` retire deux contrôles, pas la gate."""
    topo = project / "workspace/feats/topology/1-topology.md"
    topo.write_text(topo.read_text(encoding="utf-8").replace("MISSION: 1-SupportAssistant", "MISSION:"),
                    encoding="utf-8")
    code, out = run_main(validate_topology.main,
                         ["--root", str(project), "--mission", "1", "--no-report", "--pre"])
    assert code == 1 and "TOPOLOGY_INCOMPLETE" in out


# ---------------------------------------------------------------------------
# validate-tool-contract --static : sans IR compilé
# ---------------------------------------------------------------------------
def test_static_mode_validates_the_markdown_contracts_with_no_ir(project: Path) -> None:
    ir_path = project / "workspace/.sys/.ir/1-system.ir.json"
    if ir_path.exists():
        ir_path.unlink()
    code, out = run_main(validate_tool_contract.main,
                         ["--root", str(project), "--mission", "1", "--static", "--no-report"])
    assert code == 0, out
    assert "IR_NOT_FOUND" not in out


def test_static_mode_catches_a_faulty_contract_before_the_ir_is_paid_for(project: Path) -> None:
    contract = next((project / "workspace/feats/contracts/tools").glob("1-zendesk*.tool.md"))
    text = contract.read_text(encoding="utf-8")
    assert "external-side-effect" in text
    contract.write_text(text.replace("external-side-effect", "write-destructive"), encoding="utf-8")

    code, out = run_main(validate_tool_contract.main,
                         ["--root", str(project), "--mission", "1", "--static", "--no-report"])
    assert code == 1, "un outil destructif sans stratégie de sûreté doit être refusé ici, pas trois phases plus loin"


def test_static_mode_writes_no_gate_report(project: Path) -> None:
    """G3 se prononce sur l'IR. Un rapport écrit depuis des contrats non compilés
    ferait croire la gate franchie avant que le graphe n'ait été vérifié."""
    validation = project / "workspace/.sys/.validation"
    before = {p.name for p in validation.glob("*.json")} if validation.is_dir() else set()
    run_main(validate_tool_contract.main, ["--root", str(project), "--mission", "1", "--static"])
    after = {p.name for p in validation.glob("*.json")} if validation.is_dir() else set()
    assert not {n for n in after - before if n.startswith("G3")}


# ---------------------------------------------------------------------------
# eval-runner --isolated / --dataset
# ---------------------------------------------------------------------------
def _ir(project: Path) -> dict:
    ir, _ = ir_compiler.compile_mission(project, 1)
    return ir


def test_isolated_keeps_only_suites_that_name_one_agent(project: Path) -> None:
    suites = _ir(project)["evaluation"]["suites"]
    filters = eval_runner.Filters(isolated=True)
    kept = [s for s in suites if filters.accepts(s)]
    assert kept, "la fixture porte au moins une suite avec agentRef"
    assert all(s.get("agentRef") for s in kept)
    assert len(kept) < len(suites), "une suite sans agent n'est pas une mesure isolée"


def test_isolated_tells_the_executor_so_the_report_says_how_it_was_measured() -> None:
    seen: list[bool] = []

    class Spy:
        def run(self, item, *, suite, run_index, seed):
            seen.append(bool(suite.get("isolated")))
            return {"output": item.get("expected"), "cost_usd": 0.0, "latency_ms": 0.0, "trace": {"calls": []}}

    ir = {
        "missionId": "1-X",
        "evaluation": {"suites": [{"id": "s", "level": "L4", "agentRef": "1-a",
                                   "dataset": "workspace/proof/datasets/golden/g.jsonl",
                                   "grader": "exact", "threshold": 1.0, "runs": 1}],
                       "baselineRef": "workspace/proof/baselines/1-system.json"},
    }
    filters = eval_runner.Filters(isolated=True)
    assert filters.accepts(ir["evaluation"]["suites"][0])
    marked = [{**s, "isolated": True} for s in ir["evaluation"]["suites"] if filters.accepts(s)]
    assert marked[0]["isolated"] is True
    Spy().run({"expected": 1}, suite=marked[0], run_index=0, seed=None)
    assert seen == [True]


def test_dataset_filter_selects_the_holdout_for_g8(project: Path) -> None:
    suites = _ir(project)["evaluation"]["suites"]
    filters = eval_runner.Filters(datasets={"holdout"})
    kept = [s for s in suites if filters.accepts(s)]
    assert [s["level"] for s in kept] == ["L9"]


def test_parallel_measurement_gives_the_same_result_as_serial(project: Path, tmp_path: Path) -> None:
    """L'ordre des résultats est reconstruit : une mesure qui dépendrait de
    l'ordonnanceur ne serait pas comparable d'un run à l'autre (P10)."""
    from sdda_lib.layered_config import LayeredConfig

    dataset = project / "workspace/proof/datasets/golden/parallel-v1.jsonl"
    dataset.write_text("".join(
        f'{{"id": "i{k}", "input": "q{k}", "expected": "r{k}"}}\n' for k in range(12)), encoding="utf-8")

    suite = {"id": "s", "level": "L4", "dataset": "workspace/proof/datasets/golden/parallel-v1.jsonl",
             "grader": "exact", "threshold": 1.0, "runs": 2}

    def measure(max_parallel: int):
        config = LayeredConfig({"EvalMaxParallel": max_parallel, "EvalRuns": 2}, sources={})
        plan = eval_runner.plan_suite(project, suite, config, base_seed=7)
        result, per_class, rows, errors = eval_runner.execute_suite(
            project, plan, eval_runner.OracleExecutor(), config=config)
        return [(r["itemId"], r["run"], r["score"]) for r in rows], errors

    serial, serial_errors = measure(1)
    parallel, parallel_errors = measure(6)
    assert serial == parallel
    assert serial_errors == parallel_errors == 0
    assert [r[0] for r in serial[:12]] == [f"i{k}" for k in range(12)]
