"""Reprise à la granularité de l'agent : les items de phase du journal de run.

Ce que ces tests défendent : un `dev-agent` vert n'est pas repayé parce que
son voisin a échoué — MAIS un `pass` obtenu sur d'autres entrées (prompt
réécrit, entrée IR modifiée) n'est pas un `pass` : il se rejoue.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import run_main  # type: ignore
from sdda_scripts import sdda_state


def _new(root: Path) -> str:
    code, out = run_main(sdda_state.main, ["new-run", "--root", str(root), "--mission", "1", "--command", "/sdda-build"])
    assert code == 0
    return out.strip()


def _set_item(root: Path, rid: str, item: str, status: str, h: str | None = None, phase: str = "build_agents") -> tuple[int, str]:
    argv = ["set-item", "--root", str(root), "--run-id", rid, "--phase", phase, "--item", item, "--status", status]
    if h:
        argv += ["--inputs-hash", h]
    return run_main(sdda_state.main, argv)


def _skip(root: Path, rid: str, item: str, h: str | None = None, phase: str = "build_agents") -> int:
    argv = ["should-skip-item", "--root", str(root), "--run-id", rid, "--phase", phase, "--item", item]
    if h:
        argv += ["--inputs-hash", h]
    return run_main(sdda_state.main, argv)[0]


H1, H2 = "sha256:" + "a" * 64, "sha256:" + "b" * 64


def test_a_passed_item_on_the_same_inputs_is_skipped(project: Path) -> None:
    rid = _new(project)
    assert _set_item(project, rid, "billing", "pass", H1)[0] == 0
    assert _skip(project, rid, "billing", H1) == 0          # SKIP
    assert _skip(project, rid, "billing", H2) == 1          # entrées modifiées -> RUN
    assert _skip(project, rid, "routing", H1) == 1          # jamais vu -> RUN


def test_warn_and_fail_items_are_always_replayed(project: Path) -> None:
    rid = _new(project)
    _set_item(project, rid, "billing", "fail", H1)
    assert _skip(project, rid, "billing", H1) == 1
    _set_item(project, rid, "billing", "warn", H1)
    assert _skip(project, rid, "billing", H1) == 1
    _set_item(project, rid, "billing", "pass", H1)          # le dernier verdict gagne
    assert _skip(project, rid, "billing", H1) == 0


def test_a_pass_without_recorded_hash_cannot_be_proven_against_a_presented_hash(project: Path) -> None:
    rid = _new(project)
    _set_item(project, rid, "billing", "pass")
    assert _skip(project, rid, "billing", H1) == 1          # non prouvable -> RUN
    assert _skip(project, rid, "billing") == 0              # sans hash présenté : le pass suffit


def test_done_items_lists_only_passes_one_per_line(project: Path) -> None:
    rid = _new(project)
    _set_item(project, rid, "billing", "pass", H1)
    _set_item(project, rid, "routing", "fail", H1)
    _set_item(project, rid, "answer", "pass", H1)
    code, out = run_main(sdda_state.main, ["done-items", "--root", str(project), "--run-id", rid, "--phase", "build_agents"])
    assert code == 0 and out.split() == ["answer", "billing"]
    code, out = run_main(sdda_state.main, ["done-items", "--root", str(project), "--run-id", rid, "--phase", "build_agents", "--json"])
    data = json.loads(out)
    assert data["done"] == ["answer", "billing"] and data["items"]["routing"] == "fail"


def test_items_are_visible_in_the_run_summary_and_journal(project: Path) -> None:
    rid = _new(project)
    _set_item(project, rid, "billing", "pass", H1)
    _set_item(project, rid, "routing", "fail", H1)
    run = sdda_state.load_run(project, rid)
    summary = sdda_state.run_summary(project, run)
    assert summary["items"] == {"build_agents": {"billing": "pass", "routing": "fail"}}
    assert "build_agents items 1/2 pass" in sdda_state.render_run_line(summary)
    journal = (sdda_state.journal_path(project)).read_text(encoding="utf-8")
    assert journal.count('"event": "set-item"') == 2


def test_items_never_decide_the_phase_status(project: Path) -> None:
    """Le verdict de phase reste explicite (`set-phase`) : deux vérités sur le même fait, c'est zéro."""
    rid = _new(project)
    _set_item(project, rid, "billing", "pass", H1)
    run = sdda_state.load_run(project, rid)
    assert sdda_state.effective_statuses(run) == {}
    assert sdda_state.resume_target(run) == "mission"


def test_a_phase_without_items_refuses_set_item(project: Path) -> None:
    rid = _new(project)
    code, out = _set_item(project, rid, "x", "pass", H1, phase="caps")
    assert code == 1 and "STATE_PHASE_NOT_ITEMIZED" in out
    assert _skip(project, rid, "x", H1, phase="caps") == 1


def test_sub_phase_items_attach_to_the_canonical_phase(project: Path) -> None:
    """`build_socle` peut recevoir ses couches sous leur nom : tools, retrieval, data."""
    rid = _new(project)
    for layer in ("tools", "retrieval", "data"):
        _set_item(project, rid, layer, "pass", H1, phase="build_socle")
    assert _skip(project, rid, "retrieval", H1, phase="build_socle") == 0
    assert sdda_state.items_summary(sdda_state.load_run(project, rid))["build_socle"] == {"data": "pass", "retrieval": "pass", "tools": "pass"}


def test_an_unknown_run_is_a_named_error(project: Path) -> None:
    code, out = run_main(sdda_state.main, ["should-skip-item", "--root", str(project), "--run-id", "nope", "--phase", "build_agents", "--item", "x"])
    assert code == 1 and "STATE_RUN_NOT_FOUND" in out


# ---------------------------------------------------------------------------
# inputs-hash : le hash des ENTRÉES d'un item, lu dans l'IR — jamais composé à la main
# ---------------------------------------------------------------------------
def _hash(root: Path, phase: str, item: str) -> tuple[int, str]:
    code, out = run_main(sdda_state.main, ["inputs-hash", "--root", str(root), "--mission", "1", "--phase", phase, "--item", item])
    return code, out.strip()


def _compile(root: Path) -> None:
    from sdda_scripts import ir_compiler

    _, report = ir_compiler.compile_to_file(root, 1)
    assert report.ok, [f.cls for f in report.errors]


def test_inputs_hash_needs_the_compiled_ir(project: Path) -> None:
    code, out = _hash(project, "build_agents", "billing-specialist")
    assert code == 1 and "IR_NOT_FOUND" in out


def test_an_agent_hash_follows_its_prompt_and_its_ir_entry(project: Path) -> None:
    _compile(project)
    code, before = _hash(project, "build_agents", "billing-specialist")
    assert code == 0 and before.startswith("sha256:") and len(before) == len("sha256:") + 64
    # Le préfixe `{n}-` de agents[].id est optionnel : `--agent billing-specialist` est ce qu'un humain tape.
    assert _hash(project, "build_agents", "1-billing-specialist")[1] == before
    # Un autre agent, un autre hash.
    assert _hash(project, "build_agents", "intent-classifier")[1] != before

    prompt = project / "workspace/prompts/billing-specialist.system.md"
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\nNouvelle consigne.\n", encoding="utf-8")
    assert _hash(project, "build_agents", "billing-specialist")[1] != before   # le prompt est une entrée

    # Un pass enregistré sur l'ancien hash ne se saute plus : les entrées ont bougé.
    rid = _new(project)
    _set_item(project, rid, "billing-specialist", "pass", before)
    assert _skip(project, rid, "billing-specialist", _hash(project, "build_agents", "billing-specialist")[1]) == 1


def test_an_agent_absent_from_the_ir_is_a_named_error(project: Path) -> None:
    _compile(project)
    code, out = _hash(project, "build_agents", "nobody")
    assert code == 1 and "AGENT_NOT_IN_IR" in out and "1-billing-specialist" in out


def test_socle_layers_hash_their_ir_slice(project: Path) -> None:
    _compile(project)
    hashes = {layer: _hash(project, "build_socle", layer) for layer in ("tools", "retrieval", "data")}
    assert all(code == 0 for code, _ in hashes.values())
    assert len({h for _, h in hashes.values()}) == 3
    code, out = _hash(project, "build_socle", "prompts")
    assert code == 1 and "STATE_ITEM_UNKNOWN" in out


def test_eval_items_are_suites(project: Path) -> None:
    _compile(project)
    assert _hash(project, "eval", "1-2-groundedness")[0] == 0
    code, out = _hash(project, "eval", "no-such-suite")
    assert code == 1 and "STATE_ITEM_UNKNOWN" in out


def test_inputs_hash_refuses_a_phase_without_items(project: Path) -> None:
    _compile(project)
    code, out = _hash(project, "caps", "x")
    assert code == 1 and "STATE_PHASE_NOT_ITEMIZED" in out


def test_inputs_hash_json_carries_the_same_value(project: Path) -> None:
    _compile(project)
    _, plain = _hash(project, "build_socle", "tools")
    code, out = run_main(sdda_state.main, ["inputs-hash", "--root", str(project), "--mission", "1", "--phase", "build_socle", "--item", "tools", "--json"])
    assert code == 0 and json.loads(out)["inputsHash"] == plain
