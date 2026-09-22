"""retrieval_metrics — la RETRIEVAL GATE (G4), mesurée SANS agent."""
from __future__ import annotations

import pytest

from sdda_lib.retrieval_metrics import (
    QueryOutcome,
    citation_resolve_rate,
    context_precision,
    diagnose,
    evaluate,
    extract_citations,
    mrr,
    ndcg_at_k,
    recall_at_k,
)

EXPECTED = [{"doc_id": "d1", "relevance": 3}, {"doc_id": "d2", "relevance": 2}]


# -- recall ---------------------------------------------------------------------------
def test_recall_counts_documents_not_chunks() -> None:
    """La vérité est au niveau DOCUMENT.

    Un chunk différent du même document reste une trouvaille. Mesurer au chunk
    pénaliserait un découpage correct et pousserait à optimiser la mauvaise
    chose.
    """
    assert recall_at_k(["d1", "d2", "d9"], EXPECTED, k=3) == pytest.approx(1.0)
    assert recall_at_k(["d1", "d9", "d8"], EXPECTED, k=3) == pytest.approx(0.5)
    assert recall_at_k(["d9", "d8"], EXPECTED, k=3) == pytest.approx(0.0)


def test_recall_respects_k() -> None:
    """Un document trouvé en position 5 n'est pas trouvé si on sert k=3."""
    assert recall_at_k(["x", "y", "z", "w", "d1"], [{"doc_id": "d1"}], k=3) == 0.0
    assert recall_at_k(["x", "y", "z", "w", "d1"], [{"doc_id": "d1"}], k=5) == 1.0


def test_query_with_nothing_to_find_scores_one() -> None:
    """Une requête sans document attendu ne teste pas le recall.

    La faire compter comme un échec ferait baisser la métrique globale sans
    qu'aucun retriever n'y soit pour quelque chose.
    """
    assert recall_at_k(["d1"], [], k=3) == 1.0


def test_short_form_expected_is_accepted() -> None:
    assert recall_at_k(["d1"], ["d1"], k=1) == 1.0


# -- classement ------------------------------------------------------------------------
def test_ndcg_separates_found_from_well_ranked() -> None:
    """Le complément indispensable du recall.

    Les deux listes contiennent les mêmes documents : recall identique, nDCG
    différent. C'est exactement l'écart qu'un reranker corrige.
    """
    well = ["d1", "d2", "x"]
    badly = ["x", "d2", "d1"]
    assert recall_at_k(well, EXPECTED, 3) == recall_at_k(badly, EXPECTED, 3)
    assert ndcg_at_k(well, EXPECTED, 3) > ndcg_at_k(badly, EXPECTED, 3)


def test_ndcg_is_one_for_the_ideal_ordering() -> None:
    assert ndcg_at_k(["d1", "d2"], EXPECTED, 2) == pytest.approx(1.0)


def test_mrr_rewards_an_early_hit() -> None:
    assert mrr(["d1", "x"], EXPECTED) == pytest.approx(1.0)
    assert mrr(["x", "d1"], EXPECTED) == pytest.approx(0.5)
    assert mrr(["x", "y"], EXPECTED) == 0.0


def test_context_precision_measures_the_noise_served() -> None:
    assert context_precision(["d1", "d2"], EXPECTED, 2) == pytest.approx(1.0)
    assert context_precision(["d1", "x", "y", "z"], EXPECTED, 4) == pytest.approx(0.25)


# -- citations ---------------------------------------------------------------------------
def test_extract_citations_handles_the_usual_forms() -> None:
    text = "Selon [d1] et [^d2], voir aussi [source:d3]."
    assert extract_citations(text) == ["d1", "d2", "d3"]


def test_dangling_citation_is_a_hallucination_with_the_look_of_a_source() -> None:
    """Plus dangereux qu'une absence de source, parce que ça rassure."""
    rate, dead = citation_resolve_rate("D'après [d1] et [d404].", ["d1", "d2"])
    assert rate == pytest.approx(0.5)
    assert dead == ["d404"]


def test_answer_without_citations_resolves_at_one() -> None:
    """L'absence de citation relève de `CitationMode`, pas de la résolution.

    Confondre les deux masquerait l'un des deux problèmes.
    """
    rate, dead = citation_resolve_rate("Une réponse sans source.", ["d1"])
    assert rate == 1.0 and dead == []


# -- agrégation ----------------------------------------------------------------------------
def test_report_names_the_misses_instead_of_only_counting_them() -> None:
    """Un taux global ne dit pas quoi corriger. La liste des ratés, si."""
    outcomes = [
        QueryOutcome("q1", ["d1", "d2"], EXPECTED),
        QueryOutcome("q2", ["x", "y"], [{"doc_id": "d7", "relevance": 3}]),
    ]
    report = evaluate(outcomes, k=3)
    assert report.recall_at_k == pytest.approx(0.5)
    assert any("q2" in m and "d7" in m for m in report.misses)


def test_by_tag_reveals_what_a_global_score_hides() -> None:
    """Un recall global de 0.67 peut cacher 0.0 sur les questions multi-sauts.

    C'est cette famille qu'il faut traiter, pas le retriever entier.
    """
    outcomes = [
        QueryOutcome("q1", ["d1"], [{"doc_id": "d1"}], tags=["factoid"]),
        QueryOutcome("q2", ["d2"], [{"doc_id": "d2"}], tags=["factoid"]),
        QueryOutcome("q3", ["x"], [{"doc_id": "d3"}], tags=["multihop"]),
    ]
    report = evaluate(outcomes, k=3)
    assert report.recall_at_k == pytest.approx(2 / 3)
    assert report.by_tag["factoid"]["recallAtK"] == pytest.approx(1.0)
    assert report.by_tag["multihop"]["recallAtK"] == pytest.approx(0.0)


def test_empty_outcomes_do_not_crash() -> None:
    report = evaluate([], k=5)
    assert report.queries == 0 and report.recall_at_k == 0.0


def test_p95_on_few_values_takes_the_tail_not_an_interpolation() -> None:
    outcomes = [QueryOutcome(f"q{n}", ["d1"], [{"doc_id": "d1"}], latency_ms=lat)
                for n, lat in enumerate([100.0, 200.0, 900.0])]
    assert evaluate(outcomes, k=3).latency_p95_ms == 900.0


# -- diagnostic ------------------------------------------------------------------------------
def test_low_recall_says_do_not_touch_the_prompt() -> None:
    """La perte de temps la plus coûteuse du domaine, évitée par une phrase."""
    message = diagnose(0.40, 0.95, {"recallAtK": 0.80, "groundedness": 0.85})
    assert "RETRIEVAL" in message
    assert "Ne pas toucher au prompt" in message


def test_good_recall_and_low_groundedness_points_at_generation() -> None:
    message = diagnose(0.92, 0.60, {"recallAtK": 0.80, "groundedness": 0.85})
    assert "GÉNÉRATION" in message


def test_unmeasured_groundedness_is_said_not_assumed() -> None:
    message = diagnose(0.92, None, {"recallAtK": 0.80})
    assert "non mesurée" in message


def test_both_layers_holding_is_stated_plainly() -> None:
    assert "OK" in diagnose(0.92, 0.92, {"recallAtK": 0.80, "groundedness": 0.85})
