"""eval_pinning — un résultat sait ce qu'il a évalué (P10)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project
from sdda_lib import paths
from sdda_lib.eval_pinning import (
    Baseline,
    PinTuple,
    check_staleness,
    current_pins,
    dataset_digest,
    load_baselines,
    regression_delta,
)
from sdda_scripts import ir_compiler

FIXED_AT = "2026-09-20T10:00:00Z"


@pytest.fixture
def compiled(project: Path) -> tuple[Path, dict]:
    ir_compiler.compile_to_file(project, 1, compiled_at=FIXED_AT)
    return project, ir_compiler.load_ir(paths.ir_path(project, 1))


# -- le tuple -----------------------------------------------------------------------
def test_identical_tuples_are_not_stale() -> None:
    pins = PinTuple(promptHash="sha256:aaa", modelId="m", datasetHash="sha256:bbb")
    assert pins.diff(pins) == {}
    assert not pins.is_stale_against(pins)


def test_a_moved_dimension_is_named_not_just_flagged() -> None:
    """« périmé » ne suffit pas : il faut savoir QUOI a bougé pour agir."""
    pinned = PinTuple(promptHash="sha256:aaa", modelId="claude-sonnet-5")
    current = PinTuple(promptHash="sha256:zzz", modelId="claude-sonnet-5")
    moved = pinned.diff(current)
    assert list(moved) == ["promptHash"]
    assert moved["promptHash"] == ("sha256:aaa", "sha256:zzz")


def test_an_empty_dimension_means_not_applicable_not_changed() -> None:
    """Un agent sans retrieval n'a pas d'indexHash.

    Traiter l'absence comme un changement périmerait tout, tout le temps — et
    une mécanique qui crie toujours finit désactivée.
    """
    pinned = PinTuple(promptHash="sha256:aaa", indexHash="")
    current = PinTuple(promptHash="sha256:aaa", indexHash="sha256:idx")
    assert pinned.diff(current) == {}


def test_model_id_is_part_of_the_tuple() -> None:
    """Un fournisseur peut déplacer un modèle sous vos pieds.

    Mêmes prompt et dataset, scores différents, et rien dans le dépôt n'a
    changé. Sans `modelId` épinglé, cette dérive est invisible.
    """
    pinned = PinTuple(modelId="claude-sonnet-5")
    current = PinTuple(modelId="claude-sonnet-5-1")
    assert "modelId" in pinned.diff(current)


def test_digest_is_stable_and_discriminating() -> None:
    a = PinTuple(promptHash="sha256:aaa", modelId="m")
    b = PinTuple(promptHash="sha256:aaa", modelId="m")
    c = PinTuple(promptHash="sha256:bbb", modelId="m")
    assert a.digest() == b.digest()
    assert a.digest() != c.digest()


# -- lecture depuis le disque --------------------------------------------------------
def test_current_pins_reads_the_prompt_file_not_the_ir_memory(compiled) -> None:
    """C'est l'écart entre les deux qu'on cherche à détecter.

    Si l'IR faisait foi, éditer un prompt sans recompiler passerait inaperçu —
    exactement le scénario du vendredi soir.
    """
    root, ir = compiled
    suite = (ir["evaluation"]["suites"])[0]
    before = current_pins(root, ir, suite=suite)

    prompt = paths.resolve_rel(root, ir["agents"][0]["promptRef"])
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\n\nUne ligne ajoutée.\n", encoding="utf-8")

    after = current_pins(root, ir, suite=suite, agent_id=ir["agents"][0]["id"])
    pinned = current_pins(root, ir, suite=suite, agent_id=ir["agents"][0]["id"])
    assert after.promptHash == pinned.promptHash
    assert after.promptHash != PinTuple(promptHash=ir["agents"][0]["promptHash"]).promptHash


def test_model_id_resolves_the_tier_through_stack_md(compiled) -> None:
    """`balanced` seul n'identifie rien : il peut désigner deux modèles."""
    root, ir = compiled
    pins = current_pins(root, ir, agent_id=ir["agents"][0]["id"])
    assert pins.modelId.startswith("claude-")


def test_dataset_digest_ignores_line_order(tmp_path: Path) -> None:
    """Réordonner un jeu ne change pas ce qu'il mesure.

    Le re-hasher pour autant périmerait des résultats parfaitement valides.
    """
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_text('{"id":"1"}\n{"id":"2"}\n', encoding="utf-8")
    b.write_text('{"id":"2"}\n{"id":"1"}\n', encoding="utf-8")
    assert dataset_digest(tmp_path, "a.jsonl") == dataset_digest(tmp_path, "b.jsonl")


def test_dataset_digest_changes_when_content_changes(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    path.write_text('{"id":"1"}\n', encoding="utf-8")
    before = dataset_digest(tmp_path, "a.jsonl")
    path.write_text('{"id":"1"}\n{"id":"2"}\n', encoding="utf-8")
    assert dataset_digest(tmp_path, "a.jsonl") != before


def test_missing_dataset_digests_to_empty_not_to_a_crash(tmp_path: Path) -> None:
    assert dataset_digest(tmp_path, "absent.jsonl") == ""


# -- baselines -------------------------------------------------------------------------
def test_staleness_reports_which_baselines_moved_and_why() -> None:
    baselines = {
        "s1": Baseline("s1", "groundedness", 0.9, 0.01, 1.0, "green",
                       PinTuple(promptHash="sha256:aaa")),
        "s2": Baseline("s2", "accuracy", 0.95, 0.01, 1.0, "green",
                       PinTuple(promptHash="sha256:bbb")),
    }
    current = {
        "s1": PinTuple(promptHash="sha256:zzz"),   # a bougé
        "s2": PinTuple(promptHash="sha256:bbb"),   # inchangé
    }
    outcomes = {s.suite_id: s for s in check_staleness(baselines, current)}
    assert outcomes["s1"].stale and "promptHash" in outcomes["s1"].moved
    assert not outcomes["s2"].stale
    assert "périmée" in outcomes["s1"].summary


def test_load_baselines_tolerates_both_key_spellings(tmp_path: Path) -> None:
    path = tmp_path / "1-system.json"
    path.write_text(json.dumps({"baselines": {
        "s1": {"metric": "groundedness", "mean": 0.9, "passRate": 1.0,
               "verdict": "green", "pins": {"promptHash": "sha256:aaa"}},
    }}), encoding="utf-8")
    baselines = load_baselines(path)
    assert baselines["s1"].pass_rate == 1.0
    assert baselines["s1"].pins.promptHash == "sha256:aaa"


def test_load_baselines_on_missing_or_broken_file_is_empty(tmp_path: Path) -> None:
    assert load_baselines(tmp_path / "absent.json") == {}
    broken = tmp_path / "b.json"
    broken.write_text("{ pas du json", encoding="utf-8")
    assert load_baselines(broken) == {}


# -- régression ---------------------------------------------------------------------------
def test_regression_delta_respects_the_direction_of_the_metric() -> None:
    """Une latence qui baisse est une amélioration ; un groundedness non."""
    baseline = Baseline("s", "latency_ms", 300.0, 5.0, 1.0, "green", PinTuple())
    assert regression_delta(baseline, 240.0, lower_is_better=True) == pytest.approx(20.0)
    assert regression_delta(baseline, 360.0, lower_is_better=True) == pytest.approx(-20.0)

    quality = Baseline("s", "groundedness", 0.90, 0.01, 1.0, "green", PinTuple())
    assert regression_delta(quality, 0.81, lower_is_better=False) == pytest.approx(-10.0)
    assert regression_delta(quality, 0.99, lower_is_better=False) == pytest.approx(10.0)


def test_regression_against_a_zero_baseline_is_zero_not_a_division_error() -> None:
    baseline = Baseline("s", "m", 0.0, 0.0, 0.0, "red", PinTuple())
    assert regression_delta(baseline, 0.5, lower_is_better=False) == 0.0


def test_non_hash_dimensions_compare_by_equality_not_by_hash_form() -> None:
    """`modelId` n'est pas un hash — et c'est le piège.

    Comparer `claude-sonnet-5` avec une fonction qui exige la forme
    `sha256:…` renvoie toujours faux : TOUTE baseline serait périmée en
    permanence, le mécanisme crierait à chaque run, et finirait désactivé.
    P10 ne protégerait alors plus rien.
    """
    same = PinTuple(modelId="claude-sonnet-5")
    assert same.diff(PinTuple(modelId="claude-sonnet-5")) == {}
    assert "modelId" in same.diff(PinTuple(modelId="gpt-5.4"))


def test_a_short_pinned_hash_matches_the_full_current_one() -> None:
    """Les specs épinglent `sha256:` + 8 caractères, le disque en rend 64."""
    short = PinTuple(promptHash="sha256:deadbeef")
    full = PinTuple(promptHash="sha256:deadbeef" + "0" * 56)
    assert short.diff(full) == {}
