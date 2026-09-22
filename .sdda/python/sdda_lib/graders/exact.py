"""Grader `exact` — égalité après normalisation de surface.

Usage : sortie canonique attendue (classe d'un routeur, identifiant, réponse
courte fermée). C'est le grader qui « ne flatte personne » : pas de crédit
partiel, la sortie est la bonne ou elle ne l'est pas.

Ce que la normalisation gomme : casse (sauf `case_sensitive: true`), espaces
multiples et espaces de bord, ponctuation terminale (sauf
`strip_punctuation: false`), variantes Unicode d'un même caractère. Ce qu'elle
ne gomme jamais : une différence de fond. « refund » et « refunds » restent
différents — c'est voulu ; si la CAP tolère les variantes, c'est `regex` qui
convient, pas un `exact` assoupli.

Score : 1.0 ou 0.0. Déterministe.
"""
from __future__ import annotations

from typing import Any

from sdda_lib.graders._base import (
    CLS_EXPECTED_INVALID,
    BaseGrader,
    GradeResult,
    as_text,
    expected_of,
    is_missing,
    normalize_text,
)


class ExactGrader(BaseGrader):
    name = "exact"
    deterministic = True
    bounded = True
    metrics = ("exact_match", "accuracy", "routing_accuracy", "accuracy_per_class", "classification_accuracy")

    def score(self, item: dict, output: Any, *, config: dict) -> GradeResult:
        expected = expected_of(item)
        if is_missing(expected):
            return GradeResult.failure(CLS_EXPECTED_INVALID, f"item `{item.get('id', '?')}` sans `expected` : le grader exact n'a rien à comparer")
        case_sensitive = bool(config.get("case_sensitive", False))
        strip_punctuation = bool(config.get("strip_punctuation", True))
        norm_expected = normalize_text(as_text(expected), case_sensitive=case_sensitive, strip_punctuation=strip_punctuation)
        norm_output = normalize_text(as_text(output), case_sensitive=case_sensitive, strip_punctuation=strip_punctuation)
        matched = norm_expected == norm_output
        return GradeResult(
            score=1.0 if matched else 0.0,
            detail={
                "matched": matched,
                "expected_normalized": norm_expected,
                "output_normalized": norm_output,
                "case_sensitive": case_sensitive,
                "strip_punctuation": strip_punctuation,
            },
        )


GRADER = ExactGrader()
