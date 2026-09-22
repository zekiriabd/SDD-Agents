"""Registre des graders du moteur d'évaluation (eval-protocol.md §4).

Un grader par fichier, une entrée par nom dans `GRADERS`. Le nom est celui
que l'AC d'une CAP écrit dans son champ `grader:` (liste close de
`validate_cap.GRADERS`) ; `get()` accepte aussi la graphie Python
(`numeric_tolerance` → `numeric-tolerance`).

Préférer toujours le grader le plus déterministe qui répond à la question :
`schema` + `exact` sur le champ décisif valent mieux qu'un `llm-judge` sur la
réponse entière — moins cher, reproductible, et il ne flatte personne.

| Grader | Déterministe | Score |
|---|:---:|---|
| `exact`, `regex`, `schema`, `numeric-tolerance` | oui | 1 / 0 |
| `trajectory` | oui | [0,1] |
| `semantic-similarity` | oui (lexical) | [0,1] |
| `cost`, `latency` | oui | valeur brute (USD, ms) |
| `llm-judge` | **non** — k runs, calibration | [0,1] |

Le grader rend un score ; `eval_stats.Threshold` décide du pass ; jamais
l'inverse. Aucun module de ce paquet n'appelle un LLM ni le réseau (P4).
"""
from __future__ import annotations

from typing import Any

from sdda_lib.errors import SddaError
from sdda_lib.graders import cost, exact, latency, llm_judge, numeric_tolerance, regex, schema, semantic_similarity, trajectory
from sdda_lib.graders._base import CLS_GRADER_UNKNOWN, BaseGrader, GradeResult, Grader, GradingError

__all__ = ["GRADERS", "BaseGrader", "GradeResult", "Grader", "GradingError", "get", "grade", "normalize_name", "describe", "graders_for_metric"]

GRADERS: dict[str, Grader] = {
    g.name: g
    for g in (
        exact.GRADER,
        regex.GRADER,
        schema.GRADER,
        numeric_tolerance.GRADER,
        semantic_similarity.GRADER,
        trajectory.GRADER,
        cost.GRADER,
        latency.GRADER,
        llm_judge.GRADER,
    )
}


def normalize_name(name: str) -> str:
    """`Numeric_Tolerance` → `numeric-tolerance`."""
    return str(name).strip().casefold().replace("_", "-")


def get(name: str) -> Grader:
    """Le grader nommé, ou `[AC_GRADER_UNKNOWN]` — la liste est close."""
    key = normalize_name(name)
    if key not in GRADERS:
        raise SddaError(
            f"grader `{name}` inconnu",
            CLS_GRADER_UNKNOWN,
            f"choisir dans la liste close : {', '.join(sorted(GRADERS))}",
        )
    return GRADERS[key]


def grade(name: str, item: dict, output: Any, config: dict | None = None) -> GradeResult:
    """Raccourci : `get(name).score(item, output, config=config or {})`."""
    return get(name).score(item, output, config=config or {})


def graders_for_metric(metric: str) -> list[str]:
    """Les graders qui déclarent servir cette métrique (vide si aucun)."""
    key = str(metric).strip().casefold()
    return sorted(name for name, g in GRADERS.items() if key in g.metrics)


def describe() -> list[dict[str, Any]]:
    """Inventaire sérialisable : ce que `list_graders.py` imprime."""
    return [
        {
            "name": name,
            "deterministic": g.deterministic,
            "bounded": g.bounded,
            "lower_is_better": bool(getattr(g, "lower_is_better", False)),
            "metrics": list(g.metrics),
            "requires_client": name == "llm-judge",
        }
        for name, g in sorted(GRADERS.items())
    ]
