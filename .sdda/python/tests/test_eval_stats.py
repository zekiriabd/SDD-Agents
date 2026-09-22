"""eval_stats — le verdict à trois couleurs, et ce qu'un pass/fail détruirait."""
from __future__ import annotations

import pytest

from sdda_lib.eval_stats import (
    ItemResult,
    RunResult,
    SuiteResult,
    Threshold,
    aggregate,
    parse_threshold,
    stddev,
    summarize,
)


def _run(index: int, *scores: float) -> RunResult:
    return RunResult(
        run_index=index,
        items=[ItemResult(f"i{n}", index, s, True) for n, s in enumerate(scores)],
    )


def _suite(*run_means: float, threshold: str = ">= 0.85", variance_warn: float = 15.0, **kw) -> SuiteResult:
    return SuiteResult(
        suite_id="s",
        metric="groundedness",
        threshold=parse_threshold(threshold),
        runs=[_run(i, m) for i, m in enumerate(run_means)],
        variance_warn_pct=variance_warn,
        **kw,
    )


# -- seuils -------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, value, expected",
    [
        (">= 0.85", 0.85, True),
        (">= 0.85", 0.849, False),
        ("<= 300", 299, True),
        ("<= 300", 301, False),
        (0.5, 0.5, True),          # nu => `>=`
        ("== 1.0", 1.0, True),
    ],
)
def test_threshold_parsing_and_comparison(raw, value, expected) -> None:
    assert parse_threshold(raw).holds(value) is expected


def test_lower_is_better_is_derived_from_the_operator() -> None:
    """La latence veut `<=`, le groundedness veut `>=`.

    Laisser l'auteur de la CAP l'écrire evite une table de métriques à
    maintenir — une table oubliée inverserait un verdict en silence.
    """
    assert parse_threshold("<= 300").lower_is_better
    assert not parse_threshold(">= 0.85").lower_is_better


# -- statistiques -------------------------------------------------------------------
def test_stddev_is_population_not_sample() -> None:
    """Les k runs SONT la population de ce qu'on mesure.

    Avec la correction de Bessel, l'écart de [0.9, 0.8] passerait de 0.05 à
    0.0707 — soit des jaunes déclenchés à tort sur k=2 ou k=3.
    """
    assert stddev([0.9, 0.8]) == pytest.approx(0.05)
    assert stddev([0.5]) == 0.0
    assert stddev([]) == 0.0


# -- LE test qui justifie tout le module --------------------------------------------
def test_mean_above_threshold_but_one_run_fails_is_yellow_not_green() -> None:
    """Le cas que le pass/fail binaire détruit.

    0.95 · 0.92 · 0.80 -> moyenne 0.89, au-dessus du seuil de 0.85. Un
    pipeline qui ne regarde que la moyenne affiche vert et livre un agent qui
    casse un jour sur trois.
    """
    suite = _suite(0.95, 0.92, 0.80, threshold=">= 0.85")
    assert suite.mean == pytest.approx(0.89, abs=1e-9)
    assert suite.pass_rate == pytest.approx(2 / 3)
    assert suite.verdict == "yellow"
    assert "run(s) sous le seuil" in suite.reason


def test_high_variance_is_yellow_even_when_every_run_passes() -> None:
    """0.87 ±0.01 et 0.87 ±0.12 ne racontent pas la même histoire."""
    stable = _suite(0.90, 0.90, 0.91, variance_warn=5.0)
    volatile = _suite(0.99, 0.86, 0.99, variance_warn=5.0)
    assert stable.verdict == "green"
    assert volatile.pass_rate == 1.0
    assert volatile.verdict == "yellow"
    assert "variance" in volatile.reason


def test_mean_below_threshold_is_red() -> None:
    suite = _suite(0.80, 0.82, 0.79)
    assert suite.verdict == "red"
    assert "hors seuil" in suite.reason


def test_no_runs_is_red_not_green() -> None:
    """L'absence de mesure n'est pas un succès."""
    assert _suite().verdict == "red"


def test_all_items_erroring_is_red_and_says_so() -> None:
    """Une erreur d'exécution n'est pas un score bas : la distinction compte."""
    suite = SuiteResult(
        suite_id="s",
        metric="groundedness",
        threshold=parse_threshold(">= 0.85"),
        runs=[RunResult(0, [ItemResult("i0", 0, 0.0, False, error="timeout")])],
    )
    assert suite.verdict == "red"
    assert "erreur d'exécution" in suite.reason


# -- classes critiques ---------------------------------------------------------------
def test_failing_class_makes_it_red_even_with_a_passing_global_mean() -> None:
    """Une accuracy globale de 0.95 peut cacher 0.40 sur la classe critique.

    C'est exactement la classe qui compte — d'où `accuracy_per_class`.
    """
    suite = _suite(0.95, 0.96, 0.95)
    suite.per_class = {"billing": 0.98, "refund": 0.40}
    assert suite.verdict == "red"
    assert suite.failing_classes == ["refund"]
    assert "refund" in suite.reason


def test_per_class_can_carry_its_own_threshold() -> None:
    suite = _suite(0.95, 0.95, 0.95)
    suite.per_class = {"refund": 0.90}
    suite.per_class_threshold = parse_threshold(">= 0.99")
    assert suite.failing_classes == ["refund"]


# -- advisory -------------------------------------------------------------------------
def test_advisory_suite_never_blocks_but_keeps_its_real_verdict() -> None:
    """Un juge non calibré informe, il ne bloque plus (P9).

    Son verdict réel reste lisible dans le rapport : seul son pouvoir de
    bloquer disparaît.
    """
    suite = _suite(0.50, 0.52, 0.49, advisory=True)
    assert suite.verdict == "red"
    assert suite.blocking_verdict == "yellow"


def test_advisory_green_stays_green() -> None:
    suite = _suite(0.95, 0.96, 0.95, advisory=True)
    assert suite.blocking_verdict == "green"


# -- agrégation ------------------------------------------------------------------------
def test_aggregate_takes_the_worst_not_the_average() -> None:
    """Pas de pourcentage d'avancement qui masque un trou (LIFECYCLE R3)."""
    green = _suite(0.95, 0.96, 0.95)
    red = _suite(0.10, 0.11, 0.10)
    assert aggregate([green, green, green]) == "green"
    assert aggregate([green, red, green]) == "red"


def test_aggregate_ignores_a_red_advisory() -> None:
    green = _suite(0.95, 0.96, 0.95)
    red_advisory = _suite(0.10, 0.11, 0.10, advisory=True)
    assert aggregate([green, red_advisory]) == "yellow"


def test_summarize_exposes_counts_and_advisory_list() -> None:
    green = _suite(0.95, 0.96, 0.95)
    yellow = _suite(0.95, 0.92, 0.80)
    yellow.suite_id = "y"
    data = summarize([green, yellow])
    assert data["verdict"] == "yellow"
    assert data["counts"] == {"green": 1, "yellow": 1, "red": 0}
    assert data["suites"] == 2


def test_render_line_shows_k_and_pass_ratio() -> None:
    line = _suite(0.95, 0.92, 0.80).render_line()
    assert "k=3" in line and "pass 2/3" in line
