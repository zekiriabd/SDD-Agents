"""Lot A — le workflow tel que les commandes l'enchaînent, et non tel qu'un script isolé le voit.

Chaque test ici rejoue une COUTURE entre deux étapes du pipeline : ce qu'une
phase écrit, et ce que la suivante en lit. Les scripts étaient verts pris un
par un ; c'est à la jointure que le pipeline retombait.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import make_project, run_main
from sdda_lib import hashing
from sdda_scripts import compute_status, sdda_state, validate_cap


def _state(root: Path, *argv: str) -> tuple[int, str]:
    return run_main(sdda_state.main, [argv[0], "--root", str(root), *argv[1:]])


# ---------------------------------------------------------------------------
# A1 — la PHASE 2 ne périme pas G1
# ---------------------------------------------------------------------------
def _g1_verdicts(root: Path) -> tuple[str, str]:
    code, out = run_main(compute_status.main, ["--root", str(root), "--mission", "1", "--json", "--no-write"])
    data = json.loads(out)["data"]["missions"][0]
    cap = next(c for c in data["caps"] if c["id"] == "1-1-ClassifyIntent")
    return data["gates"]["G1"], cap["gates"]["G1"]


def test_cap_spec_hash_ignores_allocated_to_but_not_the_acs() -> None:
    cap = ("# CAP\n\nID: 1-1-X\nStatus: Draft\n\n## Acceptance Criteria\n- AC-1:\n  - threshold: >= 0.9\n\n"
           "## Allocated To\n- agents: <à déterminer>\n\n## Dependencies\n- NONE\n")
    allocated = cap.replace("<à déterminer>", "`intent-classifier`")
    assert hashing.sha256_cap_spec_text(cap) == hashing.sha256_cap_spec_text(allocated)
    # Le hash COMPLET (celui de G2 et de l'IR) voit l'allocation : une
    # réallocation périme bien la topologie.
    assert hashing.sha256_spec_text(cap) != hashing.sha256_spec_text(allocated)
    assert hashing.sha256_cap_spec_text(cap) != hashing.sha256_cap_spec_text(cap.replace(">= 0.9", ">= 0.8"))
    # Les sections qui suivent l'allocation restent dans le hash.
    assert "## Dependencies" in hashing.cap_spec_text(cap)


def test_allocation_written_in_phase_2_keeps_g1_green(tmp_path: Path) -> None:
    """Le scénario réel : G1 verte, architect-topology remplit `## Allocated To`,
    la MISSION retombait à `Draft` et `--resume` repartait en PHASE 1."""
    root = make_project(tmp_path)
    code, out = run_main(validate_cap.main, ["--root", str(root), "--mission", "1"])
    assert code == 0, out
    assert _g1_verdicts(root) == ("green", "green")

    cap = root / "workspace/pipeline/caps/1-1-ClassifyIntent.md"
    text = cap.read_text(encoding="utf-8")
    cap.write_text(text.replace("- tools: aucun", "- tools: `lookup_invoice`"), encoding="utf-8", newline="\n")
    assert _g1_verdicts(root) == ("green", "green")

    cap.write_text(cap.read_text(encoding="utf-8").replace(">= 0.95", ">= 0.90"), encoding="utf-8", newline="\n")
    assert _g1_verdicts(root) == ("stale", "stale")


# ---------------------------------------------------------------------------
# A3 — `--resume` reprend le run interrompu, pas celui qu'il vient d'ouvrir
# ---------------------------------------------------------------------------
def _interrupted_run(root: Path) -> str:
    code, first = _state(root, "new-run", "--mission", "1", "--command", "/sdda-full")
    first = first.strip()
    for phase in ("mission", "caps", "topology", "eval_datasets"):
        _state(root, "set-phase", "--run-id", first, "--phase", phase, "--status", "pass")
    _state(root, "set-item", "--run-id", first, "--phase", "build_agents", "--item", "billing",
           "--status", "pass", "--inputs-hash", "sha256:aaa")
    for _ in range(2):
        _state(root, "set-item", "--run-id", first, "--phase", "build_agents", "--item", "routing",
               "--status", "fail", "--inputs-hash", "sha256:bbb")
    return first


def test_resume_reads_the_previous_run_before_opening_the_new_one(project: Path) -> None:
    """L'ancien ordre : `new-run`, puis `get-run --latest` — le run vide qu'on
    venait de créer. `resume-target` rendait `mission` et tout repartait."""
    first = _interrupted_run(project)
    code, rid = _state(project, "new-run", "--mission", "1", "--command", "/sdda-full", "--resume")
    rid = rid.strip()
    assert code == 0 and rid != first
    assert sdda_state.load_run(project, rid)["resumedFrom"] == first
    assert _state(project, "resume-target", "--run-id", rid)[1].strip() == "build_socle"


def test_items_and_the_loop_counter_survive_a_resume(project: Path) -> None:
    first = _interrupted_run(project)
    _, rid = _state(project, "new-run", "--mission", "1", "--command", "/sdda-full", "--resume")
    rid = rid.strip()
    # Le `pass` du run interrompu est vu : billing n'est pas repayé.
    assert _state(project, "should-skip-item", "--run-id", rid, "--phase", "build_agents",
                  "--item", "billing", "--inputs-hash", "sha256:aaa")[0] == 0
    assert _state(project, "done-items", "--run-id", rid, "--phase", "build_agents")[1].split() == ["billing"]
    # Deux échecs hérités + un dans le run courant = le plafond, pas 1/3.
    retry = ("should-retry-item", "--run-id", rid, "--phase", "build_agents", "--item", "routing",
             "--max-iter", "3", "--inputs-hash", "sha256:bbb")
    assert _state(project, *retry)[0] == 0
    _state(project, "set-item", "--run-id", rid, "--phase", "build_agents", "--item", "routing",
           "--status", "fail", "--inputs-hash", "sha256:bbb")
    code, out = _state(project, *retry)
    assert code == 1 and "BUILD_LOOP_EXHAUSTED" in out
    # Des entrées corrigées ouvrent une boucle neuve : c'est ce que promet le message.
    assert _state(project, "should-retry-item", "--run-id", rid, "--phase", "build_agents", "--item", "routing",
                  "--max-iter", "3", "--inputs-hash", "sha256:ccc")[0] == 0
    assert sdda_state.run_summary(project, sdda_state.load_run(project, rid))["lineage"] == [first, rid]


def test_resume_without_a_previous_run_is_an_error_not_a_fresh_start(project: Path) -> None:
    code, out = _state(project, "new-run", "--mission", "1", "--command", "/sdda-full", "--resume")
    assert code == 1 and "STATE_RUN_NOT_FOUND" in out
    assert not sdda_state.all_runs(project)


def test_the_loop_budget_follows_the_item_across_resumes(project: Path) -> None:
    first = _interrupted_run(project)
    sdda_state.add_cost(project, first, usd=10.0, phase="build_agents", item="routing")
    _, rid = _state(project, "new-run", "--mission", "1", "--command", "/sdda-full", "--resume")
    rid = rid.strip()
    sdda_state.add_cost(project, rid, usd=6.0, phase="build_agents", item="routing")
    code, out = _state(project, "should-retry-item", "--run-id", rid, "--phase", "build_agents",
                       "--item", "routing", "--max-iter", "99", "--max-cost-usd", "15")
    assert code == 1 and "BUILD_LOOP_BUDGET_EXHAUSTED" in out
    # Le plafond de RUN, lui, ne cumule pas la lignée : la facture de chaque run reste la sienne.
    assert sdda_state.load_run(project, rid)["costUsd"] == 6.0


# ---------------------------------------------------------------------------
# A5 — un RUN_ID propagé porte plusieurs rapports d'eval : --run les lit tous
# ---------------------------------------------------------------------------
def _eval(root: Path, ir: dict, score: float, run_id: str, suite: str) -> None:
    from sdda_lib.layered_config import read_layered_config
    from sdda_scripts.eval_runner import Filters, run_evals

    class _Scored:
        name = "scored"

        def run(self, item, *, suite, run_index, seed):
            return {"output": {"score": score}, "cost_usd": 0.0, "latency_ms": 0.0, "trace": {}}

    graders = {"exact": lambda item, output, trace, m: float(output["score"])}
    run_evals(root, ir, _Scored(), config=read_layered_config(root), filters=Filters(suites={suite}),
              graders=graders, run_id=run_id, write_gates=False)


def test_regression_and_promotion_by_run_read_every_report_of_that_run(project: Path) -> None:
    """`/sdda-full` propage UN RUN_ID ; eval-runner y écrit `{n}-{RID}.json`, puis
    `-2`, `-3`… `--run` ne lisait que le premier : le rapport de la PHASE 4."""
    from sdda_lib import paths
    from sdda_lib.eval_reports import merged_run_report, report_by_run_id
    from sdda_scripts import check_regression, ir_compiler, promote_baseline

    ir_compiler.compile_to_file(project, 1, compiled_at="2026-09-20T10:00:00Z")
    ir = ir_compiler.load_ir(paths.ir_path(project, 1))
    routing, citations = "1-1-routing_accuracy", "1-2-citation_resolve_rate"
    _eval(project, ir, 1.0, "RID", routing)          # PHASE 4 : 1-RID.json
    _eval(project, ir, 1.0, "RID", citations)        # PHASE 6 : 1-RID-2.json
    assert report_by_run_id(project, 1, "RID").name == "1-RID-2.json"
    assert {s["suiteId"] for s in merged_run_report(project, 1, "RID")["suites"]} == {routing, citations}

    code, out = run_main(promote_baseline.main, ["--root", str(project), "--mission", "1", "--run", "RID",
                                                 "--label", "premier run accepté", "--json"])
    assert code == 0, out
    assert sorted(json.loads(out)["data"]["promoted"]) == sorted([routing, citations])

    _eval(project, ir, 0.5, "RID2", routing)
    _eval(project, ir, 1.0, "RID2", citations)
    code, out = run_main(check_regression.main, ["--root", str(project), "--mission", "1", "--run", "RID2", "--json"])
    assert code == 1 and routing in json.loads(out)["data"]["regressions"]


# ---------------------------------------------------------------------------
# A6 — k se résout depuis la config quand la commande ne le surcharge pas
# ---------------------------------------------------------------------------
def test_k_comes_from_evalruns_or_evalrunscritical_without_override(project: Path) -> None:
    from sdda_lib.layered_config import read_layered_config
    from sdda_scripts.eval_runner import plan_suite

    cfg = read_layered_config(project)
    normal = plan_suite(project, {"id": "x-normal", "level": "L4"}, cfg)
    critical = plan_suite(project, {"id": "x-critical", "level": "L4", "capRef": "1-1-ClassifyIntent"}, cfg)
    assert normal.runs == cfg.get_int("EvalRuns", 3)
    assert critical.runs == cfg.get_int("EvalRunsCritical", 5) and critical.criticality == "critical"


def _command_invocations(script: str) -> list[tuple[str, str]]:
    """(commande, invocation) pour chaque appel de `script` dans les blocs bash des commandes."""
    import re

    out = []
    for md in sorted((Path(__file__).resolve().parents[2] / "commands").glob("*.md")):
        for block in re.findall(r"```bash\n(.*?)```", md.read_text(encoding="utf-8"), flags=re.S):
            joined = re.sub(r"\\\n\s*", " ", block)
            out.extend((md.name, line) for line in joined.splitlines() if f"sdda.py {script}" in line)
    return out


def test_every_command_propagates_the_run_id_and_never_sends_a_literal_k() -> None:
    """`--runs ${RUNS:-EvalRuns}` envoyait le TEXTE `EvalRuns` à argparse, et une
    valeur unique aurait écrasé `EvalRunsCritical`. Sans `--run-id`, chaque
    rapport prenait un horodatage et `check-regression --run $RUN_ID` ne trouvait rien."""
    calls = _command_invocations("eval-runner") + _command_invocations("run-adversarial-suite")
    assert calls
    for command, line in calls:
        assert "--run-id" in line, f"{command} : {line.strip()}"
        assert "EvalRuns" not in line and "${RUNS" not in line, f"{command} : {line.strip()}"


# ---------------------------------------------------------------------------
# A7 — la passe `--pre` informe ; seule la passe complète rend la part de G2
# ---------------------------------------------------------------------------
def test_pre_pass_writes_no_gate_part_and_the_full_pass_does(project: Path) -> None:
    """`/sdda-topology` ne lançait que `--pre`, et `--pre` écrivait une part
    `topology` verte : G2 franchissable avec un agent sans contrat."""
    from sdda_lib import paths
    from sdda_scripts import validate_topology

    part = paths.validation_dir(project) / "G2-1-SupportAssistant.topology.json"
    for contract in (project / "workspace/pipeline/contracts/agents").glob("*.agent.md"):
        contract.unlink()
    assert run_main(validate_topology.main, ["--root", str(project), "--mission", "1", "--pre"])[0] == 0
    assert not part.exists()
    code, out = run_main(validate_topology.main, ["--root", str(project), "--mission", "1"])
    assert code == 1 and "AGENT_CONTRACT_MISSING" in out
    assert json.loads(part.read_text(encoding="utf-8"))["ok"] is False


def test_the_topology_command_runs_the_full_pass_before_compiling_the_ir() -> None:
    text = (Path(__file__).resolve().parents[2] / "commands/sdda-topology.md").read_text(encoding="utf-8")
    step6 = text[text.index("## STEP 6"):text.index("## STEP 7")]
    full = step6.index("validate-topology --mission {n}\n")
    assert full < step6.index("ir-compiler --mission {n}")
