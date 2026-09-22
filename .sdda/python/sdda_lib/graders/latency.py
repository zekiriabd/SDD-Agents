"""Grader `latency` — la latence mesurée du run, en millisecondes, valeur brute.

Même contrat que `cost` : `output` est un nombre ou l'objet de mesure du run
(`latency_ms`, `latencyMs`, `sdda.latency.ms`, `duration_ms`, `latency`, ou
`config["field"]`). Le score est la valeur brute en ms ; `Threshold("<=", 300)`
décide. `lower_is_better`.

Rappel pour l'auteur de la suite : un seuil de latence porte en général sur un
**percentile** (`LatencyP95Target`), pas sur la moyenne. `SuiteResult` agrège
en moyenne des runs ; le p95 se calcule sur la distribution des `ItemResult`
au niveau du rapport, et c'est là qu'il faut le lire (eval-protocol.md §9 :
« la queue à 3 % »).

Une mesure absente est une **erreur** `[MEASUREMENT_MISSING]`, pas un 0 ms.
Déterministe. `bounded = False`.
"""
from __future__ import annotations

from typing import Any

from sdda_lib.graders._base import CLS_MEASUREMENT_MISSING, CLS_OUTPUT_UNGRADABLE, BaseGrader, GradeResult
from sdda_lib.graders.cost import read_measure

LATENCY_KEYS = ("latency_ms", "latencyMs", "sdda.latency.ms", "duration_ms", "latency")


class LatencyGrader(BaseGrader):
    name = "latency"
    deterministic = True
    bounded = False
    reads = "measures"
    lower_is_better = True
    unit = "ms"
    metrics = ("latency_ms", "latency_p95_ms", "latency_p50_ms")

    def score(self, item: dict, output: Any, *, config: dict) -> GradeResult:
        value, source = read_measure(output, LATENCY_KEYS, config.get("field"))
        if value is None:
            if source is None and isinstance(output, dict):
                return GradeResult.failure(CLS_MEASUREMENT_MISSING, f"aucune mesure de latence dans le run (clés cherchées : {list(LATENCY_KEYS)})", keys_present=sorted(output))
            return GradeResult.failure(CLS_OUTPUT_UNGRADABLE, f"mesure de latence non numérique : {output!r}", source_key=source)
        if value < 0:
            return GradeResult.failure(CLS_OUTPUT_UNGRADABLE, f"latence négative {value} : mesure incohérente", source_key=source)
        return GradeResult(score=value, detail={"unit": self.unit, "lower_is_better": True, "source_key": source, "latency_ms": value})


GRADER = LatencyGrader()
