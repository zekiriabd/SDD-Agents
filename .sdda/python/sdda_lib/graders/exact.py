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

Projection (`config["fields"]`, AC `fields: a, b`) : quand la sortie est une
structure dont une partie est du texte libre (un `message` rédigé), comparer
la sortie ENTIÈRE rend 0.0 à une réponse parfaite. `fields` restreint la
comparaison aux champs nommés : `expected` est alors un objet, la sortie est un
objet (ou du JSON), et chaque champ nommé doit être égal après la même
normalisation — un champ absent de la sortie est une différence, pas une
tolérance. Les autres champs ne sont pas regardés.

Score : 1.0 ou 0.0. Déterministe.
"""
from __future__ import annotations

import json
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
        fields = config.get("fields")
        if fields:
            return self._score_fields(item, expected, output, fields, case_sensitive=case_sensitive,
                                      strip_punctuation=strip_punctuation)
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

    def _score_fields(self, item: dict, expected: Any, output: Any, fields: Any, *,
                      case_sensitive: bool, strip_punctuation: bool) -> GradeResult:
        names = [f.strip() for f in fields.split(",")] if isinstance(fields, str) else [str(f).strip() for f in fields]
        names = [n for n in names if n]
        if not isinstance(expected, dict):
            return GradeResult.failure(CLS_EXPECTED_INVALID, f"item `{item.get('id', '?')}` : avec `fields`, `expected` doit être un objet (champ -> valeur)")
        missing_expected = [n for n in names if n not in expected]
        if missing_expected:
            return GradeResult.failure(CLS_EXPECTED_INVALID, f"item `{item.get('id', '?')}` : `expected` ne porte pas le(s) champ(s) {missing_expected} que `fields` compare")
        instance = output
        if isinstance(output, str):
            try:
                instance = json.loads(output)
            except (json.JSONDecodeError, ValueError):
                instance = None
        if not isinstance(instance, dict):
            # La sortie devait être une structure : ne pas en être une est une mauvaise réponse.
            return GradeResult(score=0.0, detail={"matched": False, "fields": names, "mismatches": {"$": "la sortie n'est pas un objet"}})

        def norm(value: Any) -> str:
            return normalize_text(as_text(value), case_sensitive=case_sensitive, strip_punctuation=strip_punctuation)

        mismatches = {}
        for name in names:
            if name not in instance:
                mismatches[name] = {"expected": expected[name], "output": "<absent>"}
            elif norm(expected[name]) != norm(instance[name]):
                mismatches[name] = {"expected": expected[name], "output": instance[name]}
        return GradeResult(
            score=0.0 if mismatches else 1.0,
            detail={"matched": not mismatches, "fields": names, "mismatches": mismatches,
                    "case_sensitive": case_sensitive, "strip_punctuation": strip_punctuation},
        )


GRADER = ExactGrader()
