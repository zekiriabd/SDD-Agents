"""Grader `numeric-tolerance` — une valeur numérique à ±ε.

Usage : un montant, un pourcentage, un comptage extrait par l'agent. La
tolérance se déclare dans la config : `abs` (écart absolu) et/ou `rel` (écart
relatif à l'attendu). Quand les deux sont donnés, la plus permissive gagne :
`|sortie − attendu| <= max(abs, rel · |attendu|)`.

Une sortie **non numérique** (« environ trente », `None`, un booléen, NaN) est
une **erreur** `[EVAL_OUTPUT_UNGRADABLE]`, pas un score de 0. La distinction
compte : un 0 dirait « l'agent s'est trompé de valeur », alors que le fait
constaté est « l'agent n'a pas produit de valeur » — deux diagnostics, deux
corrections (§10). `eval_stats` exclut les items en erreur de la moyenne et
passe la suite au ROUGE si tous sont en erreur.

`extract: true` autorise à repêcher le premier nombre d'un texte
(« Total : 42,50 € » → 42.5). Désactivé par défaut : l'extraction est une
tolérance de plus et elle doit être choisie, pas subie.

Score : 1.0 ou 0.0. Déterministe.
"""
from __future__ import annotations

import math
import re
from typing import Any

from sdda_lib.graders._base import (
    CLS_EXPECTED_INVALID,
    CLS_OUTPUT_UNGRADABLE,
    BaseGrader,
    GradeResult,
    config_error,
    expected_of,
    is_missing,
)

#: Un nombre, séparateurs de milliers compris (`1,234.50`, `1 234,50`, `1.234,5`).
_NUMBER_RE = re.compile(
    r"[-+]?(?:\d{1,3}(?:[ ,.  ]\d{3})+(?!\d)(?:[.,]\d+)?|\d+(?:[.,]\d+)?|[.,]\d+)(?:[eE][-+]?\d+)?")


def _normalize_decimal(text: str) -> str | None:
    """`1,234.50` -> `1234.50` ; `1.234,5` -> `1234.5` ; `3,5` -> `3.5`.

    `"1,234"` devenait `1.234` — un total de mille deux cent trente-quatre
    noté comme un peu plus d'un. Quand les deux séparateurs sont présents, le
    DERNIER est la décimale. Seul, `,` suivi d'exactement trois chiffres est
    ambigu (milliers anglais ou décimale française) : on ne devine pas, l'item
    n'est pas notable.
    """
    t = text.replace(" ", "").replace(" ", "").replace(" ", "")
    if "," in t and "." in t:
        decimal = "," if t.rfind(",") > t.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        return t.replace(thousands, "").replace(decimal, ".")
    if "," in t:
        head, _, tail = t.rpartition(",")
        if t.count(",") > 1 or (len(tail) == 3 and tail.isdigit() and head.lstrip("+-").isdigit()):
            return None if t.count(",") == 1 else t.replace(",", "")
        return t.replace(",", ".")
    if t.count(".") > 1:
        return t.replace(".", "")
    return t


def to_number(value: Any, *, extract: bool = False) -> float | None:
    """Convertit en float ; None si ce n'est pas un nombre fini exploitable."""
    if isinstance(value, bool):
        return None  # True/False ne sont pas des mesures
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, str):
        text = value.strip()
        if extract:
            m = _NUMBER_RE.search(text)
            if not m:
                return None
            text = m.group(0)
        normalized = _normalize_decimal(text)
        if normalized is None:
            return None
        text = normalized
        try:
            number = float(text)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


class NumericToleranceGrader(BaseGrader):
    name = "numeric-tolerance"
    deterministic = True
    bounded = True
    metrics = ("numeric_accuracy", "value_within_tolerance", "amount_accuracy")

    def score(self, item: dict, output: Any, *, config: dict) -> GradeResult:
        abs_tol = float(config.get("abs", 0.0))
        rel_tol = float(config.get("rel", 0.0))
        if abs_tol < 0 or rel_tol < 0:
            raise config_error(self.name, f"tolérance négative (abs={abs_tol}, rel={rel_tol})", "déclarer `abs` et `rel` >= 0")
        extract = bool(config.get("extract", False))

        raw_expected = expected_of(item)
        if is_missing(raw_expected):
            return GradeResult.failure(CLS_EXPECTED_INVALID, f"item `{item.get('id', '?')}` sans `expected` numérique")
        expected = to_number(raw_expected)
        if expected is None:
            return GradeResult.failure(CLS_EXPECTED_INVALID, f"item `{item.get('id', '?')}` : `expected` {raw_expected!r} n'est pas un nombre", expected=raw_expected)

        actual = to_number(output, extract=extract)
        if actual is None:
            return GradeResult.failure(
                CLS_OUTPUT_UNGRADABLE,
                f"sortie non numérique {output!r} : aucune valeur à comparer (ce n'est pas une mauvaise valeur, c'est une absence de valeur)",
                output=repr(output), extract=extract,
            )

        tolerance = max(abs_tol, rel_tol * abs(expected))
        difference = abs(actual - expected)
        within = difference <= tolerance or math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
        return GradeResult(
            score=1.0 if within else 0.0,
            detail={
                "expected": expected,
                "actual": actual,
                "difference": difference,
                "tolerance": tolerance,
                "abs": abs_tol,
                "rel": rel_tol,
                "within_tolerance": within,
            },
        )


GRADER = NumericToleranceGrader()
