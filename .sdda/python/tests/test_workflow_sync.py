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
