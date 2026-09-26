"""calibration — un juge non calibré ne bloque pas (P9)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdda_lib.calibration import (
    calibrate,
    cohen_kappa,
    load_calibration_set,
    pearson,
)


# -- LE cas pour lequel kappa existe --------------------------------------------------
def test_flattering_judge_scores_90pct_raw_and_zero_kappa() -> None:
    """Sur un jeu déséquilibré, un juge constant paraît excellent.

    90 items « bon », 10 « mauvais ». Un juge qui répond toujours « bon »
    obtient 90 % d'accord brut — et zéro compétence. Kappa le ramène à 0.
    C'est le comportement par défaut d'un juge mal conçu, et le taux d'accord
    brut le déclarerait bon.
    """
    human = ["bon"] * 90 + ["mauvais"] * 10
    judge = ["bon"] * 100

    result = calibrate("flatteur", human, judge, min_items=50, min_agreement=0.6)

    assert result.raw_agreement == pytest.approx(0.90)
    assert result.agreement == pytest.approx(0.0)
    assert not result.calibrated
    assert result.advisory
    assert any("toujours la même valeur" in n for n in result.notes)
    assert any("déséquilibré" in n for n in result.notes)


def test_competent_judge_is_calibrated() -> None:
    human = (["bon", "mauvais"] * 30)[:60]
    judge = list(human)
    for i in range(0, 60, 10):           # ~10 % de désaccord
        judge[i] = "bon" if judge[i] == "mauvais" else "mauvais"

    result = calibrate("sérieux", human, judge, min_items=50, min_agreement=0.6)
    assert result.agreement > 0.6
    assert result.calibrated
    assert not result.advisory


def test_perfect_agreement_is_one() -> None:
    labels = ["a", "b", "a", "c", "b"]
    assert cohen_kappa(labels, labels) == pytest.approx(1.0)


def test_worse_than_chance_is_negative() -> None:
    assert cohen_kappa(["a", "b", "a", "b"], ["b", "a", "b", "a"]) < 0


def test_two_constant_and_identical_annotators_do_not_get_a_flattering_one() -> None:
    """Accord total mais non informatif : un jeu à une seule classe ne calibre pas.

    Il rendait 1.0, donc un juge « toujours pass » sur un jeu « tout pass »
    était déclaré calibré sans avoir rien distingué.
    """
    assert cohen_kappa(["a"] * 10, ["a"] * 10) == pytest.approx(0.0)
    assert cohen_kappa(["a"] * 10, ["b"] * 10) == pytest.approx(0.0)
    report = calibrate("g", ["pass"] * 60, ["pass"] * 60)
    assert report.calibrated is False and any("une seule classe" in n for n in report.notes)


def test_mismatched_lengths_are_refused() -> None:
    with pytest.raises(ValueError):
        cohen_kappa(["a", "b"], ["a"])


# -- seuils -----------------------------------------------------------------------------
def test_too_few_items_is_not_calibrated_however_good_the_agreement() -> None:
    """20 items en accord parfait ne valent pas une calibration.

    Un échantillon trop petit mesure surtout le hasard du choix des items.
    """
    labels = ["bon", "mauvais"] * 10
    result = calibrate("court", labels, labels, min_items=50, min_agreement=0.6)
    assert result.agreement == pytest.approx(1.0)
    assert not result.calibrated
    assert "< 50 exigés" in result.reason


def test_synthetic_labels_can_never_be_calibrated() -> None:
    """Des labels produits par un LLM mesurent l'accord de deux modèles.

    Aucun seuil ne rachète cela : `[JUDGE_CALIBRATION_SYNTHETIC]` est l'une
    des classes bloquantes sans bypass.
    """
    labels = ["bon", "mauvais"] * 40
    result = calibrate("synthetique", labels, labels, min_items=10, labels_are_synthetic=True)
    assert result.agreement == pytest.approx(1.0)
    assert not result.calibrated
    assert "LLM" in result.reason


def test_continuous_scale_uses_correlation() -> None:
    human = [0.1, 0.4, 0.6, 0.9, 0.95]
    judge = [0.15, 0.35, 0.65, 0.85, 0.99]
    result = calibrate("continu", human, judge, min_items=3, min_agreement=0.6, scale="continuous")
    assert result.agreement > 0.9
    assert result.calibrated


def test_pearson_on_degenerate_input_is_zero_not_a_crash() -> None:
    assert pearson([1.0, 1.0, 1.0], [2.0, 3.0, 4.0]) == 0.0
    assert pearson([1.0], [2.0]) == 0.0


# -- chargement : les deux formes ---------------------------------------------------------
def test_inline_form_recomputes_the_agreement(tmp_path: Path) -> None:
    path = tmp_path / "groundedness.json"
    path.write_text(json.dumps({
        "grader": "groundedness",
        "scale": "ordinal",
        "items": [{"id": f"i{n}", "human": "pass", "judge": "pass"} for n in range(5)],
    }), encoding="utf-8")

    dataset = load_calibration_set(path)
    assert dataset is not None
    assert not dataset.declared_only
    assert len(dataset.human) == 5


def test_summary_form_without_labels_is_flagged_declared_only(tmp_path: Path) -> None:
    """Un kappa écrit à la main est une affirmation, pas une mesure.

    On l'accepte — les labels portent souvent des PII — mais le rapport doit
    dire lequel des deux on lit.
    """
    path = tmp_path / "groundedness.json"
    path.write_text(json.dumps({
        "grader": "groundedness", "items": 52, "kappa": 0.71,
        "labelsRef": "workspace/pipeline/datasets/calibration/absent.jsonl",
    }), encoding="utf-8")

    dataset = load_calibration_set(path)
    assert dataset is not None
    assert dataset.declared_only
    assert dataset.declared_agreement == pytest.approx(0.71)
    assert dataset.declared_items == 52


def test_summary_form_with_resolvable_labels_recomputes(tmp_path: Path) -> None:
    """Quand les labels sont là, c'est la mesure qui fait foi — pas le résumé.

    Le résumé annonce 0.99 ; les labels disent le contraire. Le recalcul gagne.
    """
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        "\n".join(json.dumps({"human": "bon", "judge": "bon" if n % 2 else "mauvais"})
                  for n in range(20)),
        encoding="utf-8",
    )
    path = tmp_path / "groundedness.json"
    path.write_text(json.dumps({
        "grader": "groundedness", "items": 20, "kappa": 0.99, "labelsRef": "labels.jsonl",
    }), encoding="utf-8")

    dataset = load_calibration_set(path)
    assert dataset is not None
    assert not dataset.declared_only
    assert len(dataset.human) == 20

    result = calibrate("g", dataset.human, dataset.judge, min_items=10, min_agreement=0.6)
    assert result.agreement < 0.99          # le résumé mentait
    assert not result.calibrated


def test_llm_sourced_labels_in_jsonl_mark_the_set_synthetic(tmp_path: Path) -> None:
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        json.dumps({"human": "bon", "judge": "bon", "source": "llm"}) + "\n"
        + json.dumps({"human": "bon", "judge": "bon"}),
        encoding="utf-8",
    )
    path = tmp_path / "g.json"
    path.write_text(json.dumps({"grader": "g", "items": 2, "labelsRef": "labels.jsonl"}), encoding="utf-8")

    dataset = load_calibration_set(path)
    assert dataset is not None and dataset.labels_are_synthetic


def test_missing_or_broken_file_returns_none(tmp_path: Path) -> None:
    assert load_calibration_set(tmp_path / "absent.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("{ pas du json", encoding="utf-8")
    assert load_calibration_set(broken) is None
