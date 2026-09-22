"""Grader `cost` — le coût mesuré du run, en USD, valeur brute.

Il lit la **mesure** du run, pas la réponse : `output` est soit un nombre,
soit l'objet de mesure/trace du run portant l'une des clés `cost_usd`,
`costUsd`, `sdda.cost.usd`, `cost` (ou la clé donnée par `config["field"]`).

Le score n'est **pas normalisé** : c'est la valeur en USD. `lower_is_better`
est porté par le seuil de l'AC — `Threshold("<=", 0.25)` s'en charge, et
`SuiteResult` compare la moyenne des runs à cette borne. Normaliser ici
(1 − coût/budget, par exemple) cacherait la valeur qui intéresse le lecteur
du rapport et introduirait une seconde borne à maintenir.

Un run sans mesure de coût est une **erreur** `[MEASUREMENT_MISSING]`, pas un
coût de 0 : un 0 ferait passer la suite pour économe alors qu'on ne sait
simplement pas ce qu'elle a coûté (P6 : le budget est une exigence
fonctionnelle, il se mesure).

Déterministe. `bounded = False`.
"""
from __future__ import annotations

from typing import Any

from sdda_lib.graders._base import CLS_MEASUREMENT_MISSING, CLS_OUTPUT_UNGRADABLE, BaseGrader, GradeResult
from sdda_lib.graders.numeric_tolerance import to_number

COST_KEYS = ("cost_usd", "costUsd", "sdda.cost.usd", "cost")


def read_measure(output: Any, keys: tuple[str, ...], field: str | None) -> tuple[float | None, str | None]:
    """(valeur, clé source) — (None, None) si aucune mesure ; (None, clé) si présente mais non numérique."""
    if isinstance(output, dict):
        for key in ((field,) if field else keys):
            if key in output:
                return to_number(output[key]), key
        return None, None
    return to_number(output), None


class CostGrader(BaseGrader):
    name = "cost"
    deterministic = True
    bounded = False
    reads = "measures"
    lower_is_better = True
    unit = "usd"
    metrics = ("cost_usd", "cost_per_run_usd", "cost_per_cap_usd")

    def score(self, item: dict, output: Any, *, config: dict) -> GradeResult:
        value, source = read_measure(output, COST_KEYS, config.get("field"))
        if value is None:
            if source is None and isinstance(output, dict):
                return GradeResult.failure(CLS_MEASUREMENT_MISSING, f"aucune mesure de coût dans le run (clés cherchées : {list(COST_KEYS)})", keys_present=sorted(output))
            return GradeResult.failure(CLS_OUTPUT_UNGRADABLE, f"mesure de coût non numérique : {output!r}", source_key=source)
        if value < 0:
            return GradeResult.failure(CLS_OUTPUT_UNGRADABLE, f"coût négatif {value} : mesure incohérente", source_key=source)
        return GradeResult(score=value, detail={"unit": self.unit, "lower_is_better": True, "source_key": source, "cost_usd": value})


GRADER = CostGrader()
