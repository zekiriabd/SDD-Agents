"""Journal des runs : identité, phases, reprise, audit.

Ce qui est vérifié ici est surtout ce que le journal ne doit PAS faire : décider
de l'état d'une MISSION (c'est `compute_status.py`), sauter une phase qu'il n'a
pas su situer, ou rendre une valeur polluée par du diagnostic — `RUN_ID=$(…)`
casserait en silence.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import run_main  # type: ignore
from sdda_lib import paths
from sdda_scripts import sdda_state
from sdda_scripts.sdda_state import PIPELINE_PHASES


def _new(root: Path, mission: str = "1", command: str = "/sdda-full", tags: str = "") -> str:
    code, out = run_main(sdda_state.main, ["new-run", "--root", str(root), "--mission", mission, "--command", command, "--tags", tags])
    assert code == 0
    return out.strip()


def _set_window(root: Path, rid: str, started: str, ended: str | None) -> None:
    """Force les bornes temporelles d'un run (les bypasses s'y rattachent)."""
    path = sdda_state.run_path(root, rid)
    run = json.loads(path.read_text(encoding="utf-8"))
    run["startedAt"], run["endedAt"] = started, ended
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _set(root: Path, rid: str, phase: str, status: str, payload: str | None = None) -> int:
    argv = ["set-phase", "--root", str(root), "--run-id", rid, "--phase", phase, "--status", status]
    if payload:
        argv += ["--payload-json", payload]
    return run_main(sdda_state.main, argv)[0]


# ---------------------------------------------------------------------------
# Identité
# ---------------------------------------------------------------------------
def test_new_run_prints_only_the_id_so_shell_capture_is_safe(project: Path) -> None:
    out = _new(project, tags="force,resume")
    assert "\n" not in out and out.startswith("20") and "-m1-" in out
    run = sdda_state.load_run(project, out)
    assert run is not None and run["status"] == "running" and run["tags"] == ["force", "resume"]
    assert (paths.state_dir(project) / "runs.jsonl").is_file()


def test_two_runs_started_the_same_second_do_not_collide(project: Path) -> None:
    ids = {_new(project) for _ in range(5)}
    assert len(ids) == 5


def test_get_run_returns_the_latest_of_that_mission(project: Path) -> None:
    first = _new(project, mission="1")
    other = _new(project, mission="2")
    last = _new(project, mission="1")
    assert run_main(sdda_state.main, ["get-run", "--root", str(project), "--mission", "1", "--latest"])[1].strip() == last
    assert run_main(sdda_state.main, ["get-run", "--root", str(project), "--mission", "2"])[1].strip() == other
    assert first != last


def test_get_run_without_any_run_fails_with_a_class(project: Path) -> None:
    code, out = run_main(sdda_state.main, ["get-run", "--root", str(project), "--mission", "1"])
    assert code == 1 and "STATE_RUN_NOT_FOUND" in out


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------
def test_phases_are_journalled_with_their_payload(project: Path) -> None:
    rid = _new(project)
    assert _set(project, rid, "caps", "pass", '{"capCount":4,"critical":1}') == 0
    run = sdda_state.load_run(project, rid)
    assert run["phases"][0]["payload"] == {"capCount": 4, "critical": 1}
    journal = (paths.state_dir(project) / "runs.jsonl").read_text(encoding="utf-8")
    assert '"event": "set-phase"' in journal


def test_unknown_phase_is_refused_rather_than_recorded_nowhere(project: Path) -> None:
    rid = _new(project)
    code, out = run_main(sdda_state.main, ["set-phase", "--root", str(project), "--run-id", rid, "--phase", "batiment", "--status", "pass"])
    assert code == 1 and "STATE_PHASE_UNKNOWN" in out
    assert sdda_state.load_run(project, rid)["phases"] == []


def test_malformed_payload_is_refused(project: Path) -> None:
    rid = _new(project)
    code, out = run_main(sdda_state.main, ["set-phase", "--root", str(project), "--run-id", rid, "--phase", "caps", "--status", "pass", "--payload-json", "{oops"])
    assert code == 1 and "INVALID_ARG" in out


def test_set_phase_uses_the_env_run_id(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rid = _new(project)
    monkeypatch.setenv("SDDA_RUN_ID", rid)
    assert run_main(sdda_state.main, ["set-phase", "--root", str(project), "--phase", "mission", "--status", "pass"])[0] == 0
    assert sdda_state.effective_statuses(sdda_state.load_run(project, rid)) == {"mission": "pass"}


def test_a_failed_sub_stage_makes_the_whole_review_failed(project: Path) -> None:
    """Un étage C rouge ne peut pas être effacé par l'agrégat qui le suit."""
    rid = _new(project)
    _set(project, rid, "review_a", "pass")
    _set(project, rid, "review_c", "fail")
    _set(project, rid, "review", "pass")
    assert sdda_state.effective_statuses(sdda_state.load_run(project, rid))["review"] == "fail"


# ---------------------------------------------------------------------------
# Reprise
# ---------------------------------------------------------------------------
def test_resume_target_is_the_first_phase_that_did_not_pass(project: Path) -> None:
    rid = _new(project)
    for phase in ("mission", "caps", "topology", "eval_datasets"):
        _set(project, rid, phase, "pass")
    _set(project, rid, "build_socle", "fail")
    code, out = run_main(sdda_state.main, ["resume-target", "--root", str(project), "--run-id", rid])
    assert code == 0 and out.strip() == "build_socle"


def test_a_yellow_phase_is_replayed_not_skipped(project: Path) -> None:
    rid = _new(project)
    _set(project, rid, "mission", "pass")
    _set(project, rid, "caps", "warn")
    assert run_main(sdda_state.main, ["resume-target", "--root", str(project), "--run-id", rid])[1].strip() == "caps"


def test_a_fully_passed_run_resumes_at_done(project: Path) -> None:
    rid = _new(project)
    for phase in PIPELINE_PHASES:
        _set(project, rid, phase, "pass")
    assert run_main(sdda_state.main, ["resume-target", "--root", str(project), "--run-id", rid])[1].strip() == "done"


def test_resume_target_without_a_run_fails_rather_than_guessing(project: Path) -> None:
    code, out = run_main(sdda_state.main, ["resume-target", "--root", str(project), "--run-id", "inexistant"])
    assert code == 1 and "STATE_RUN_NOT_FOUND" in out


@pytest.mark.parametrize(
    ("target", "current", "expected"),
    [
        ("build_agents", "caps", 0),        # avant la cible -> SKIP
        ("build_agents", "build_agents", 1),  # la cible elle-même -> RUN
        ("build_agents", "eval", 1),        # après la cible -> RUN
        ("topology", "contracts", 1),       # sous-phase rattachée à topology -> RUN
        ("done", "acceptance", 0),          # tout est passé -> SKIP partout
    ],
)
def test_should_skip_step_orders_the_pipeline(project: Path, target: str, current: str, expected: int) -> None:
    code, _ = run_main(sdda_state.main, ["should-skip-step", "--root", str(project), "--target", target, "--current", current])
    assert code == expected


def test_an_unknown_phase_makes_the_step_run(project: Path) -> None:
    """Sauter une phase qu'on n'a pas su situer serait le pire des deux erreurs."""
    code, out = run_main(sdda_state.main, ["should-skip-step", "--root", str(project), "--target", "build_agents", "--current", "inconnue"])
    assert code == 1 and "STATE_PHASE_UNKNOWN" in out


# ---------------------------------------------------------------------------
# Clôture et audit
# ---------------------------------------------------------------------------
def test_end_run_derives_partial_from_a_yellow_phase(project: Path) -> None:
    rid = _new(project)
    _set(project, rid, "mission", "pass")
    _set(project, rid, "eval", "warn")
    run_main(sdda_state.main, ["end-run", "--root", str(project), "--run-id", rid])
    run = sdda_state.load_run(project, rid)
    assert run["status"] == "partial" and run["endedAt"]


def test_end_run_derives_fail_and_an_explicit_status_wins(project: Path) -> None:
    rid = _new(project)
    _set(project, rid, "eval", "fail")
    run_main(sdda_state.main, ["end-run", "--root", str(project), "--run-id", rid])
    assert sdda_state.load_run(project, rid)["status"] == "fail"
    run_main(sdda_state.main, ["end-run", "--root", str(project), "--run-id", rid, "--status", "aborted"])
    assert sdda_state.load_run(project, rid)["status"] == "aborted"


def test_status_json_attaches_bypasses_to_the_run_window(project: Path) -> None:
    from sdda_lib.gate_reports import append_bypass_audit

    before = _new(project)
    run_main(sdda_state.main, ["end-run", "--root", str(project), "--run-id", before, "--status", "pass"])
    current = _new(project, mission="2")
    append_bypass_audit(project, "G4", "index en réindexation")

    # Fenêtres posées à la main : le test porte sur le rattachement d'un bypass
    # à un run, pas sur la vitesse de l'horloge pendant l'exécution du test.
    _set_window(project, before, "2020-01-01T00:00:00Z", "2020-01-01T00:10:00Z")
    _set_window(project, current, "2020-06-01T00:00:00Z", None)

    code, out = run_main(sdda_state.main, ["status", "--root", str(project), "--all", "--json"])
    payload = json.loads(out)
    assert code == 0 and [r["runId"] for r in payload["runs"]] == [before, current]
    live = next(r for r in payload["runs"] if r["runId"] == current)
    assert live["bypassCount"] == 1 and live["bypasses"][0]["gate"] == "G4"
    assert payload["phasesOrder"][0] == "mission"


def test_status_text_shows_one_line_per_mission(project: Path) -> None:
    rid = _new(project, mission="1")
    _set(project, rid, "mission", "pass")
    _set(project, rid, "caps", "pass")
    _new(project, mission="2")
    code, out = run_main(sdda_state.main, ["status", "--root", str(project)])
    assert code == 0 and len(out.strip().splitlines()) == 2
    assert "reprise : topology" in out and "caps:pass" in out


def test_status_without_any_run_is_not_an_error(project: Path) -> None:
    code, out = run_main(sdda_state.main, ["status", "--root", str(project), "--json"])
    assert code == 0 and json.loads(out)["runs"] == []
