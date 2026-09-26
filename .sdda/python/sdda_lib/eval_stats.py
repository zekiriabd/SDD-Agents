"""Statistiques d'évaluation et verdict à trois couleurs.

Applique `rules/eval-protocol.md` §2-§3 et le principe P3 : **le
non-déterminisme se comptabilise, il ne se nie pas.**

Un run vert n'est pas une preuve. Toute évaluation médiée par un LLM déclare
`runs: k` et rapporte moyenne, écart-type et taux de réussite — jamais un
booléen. Aplatir cela en pass/fail est la façon la plus courante de livrer un
agent qui casse en production : un score de 0.87 ± 0.01 et un score de
0.87 ± 0.12 ne racontent pas la même histoire.

Aucun appel LLM, aucune I/O : ce module ne fait que du calcul.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

Verdict = Literal["green", "yellow", "red"]

# Ordre de gravité : sert à agréger (le verdict d'un parent est le pire de ses
# enfants — cf. LIFECYCLE.md R3, appliqué ici aux scores).
SEVERITY: dict[str, int] = {"green": 0, "yellow": 1, "red": 2}

GLYPH: dict[str, str] = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


# ---------------------------------------------------------------------------
# Comparaison au seuil
# ---------------------------------------------------------------------------
# Un seuil s'écrit `>= 0.85`, `<= 300`, `== 1.0`, ou nu (`0.85` => `>=`).
# Le sens compte : `latency_ms` veut `<=`, `groundedness` veut `>=`. Laisser
# l'auteur du CAP l'écrire évite une table de métriques à maintenir — et une
# table oubliée inverserait un verdict en silence.
_OPERATORS = {
    ">=": lambda value, bound: value >= bound,
    "<=": lambda value, bound: value <= bound,
    ">": lambda value, bound: value > bound,
    "<": lambda value, bound: value < bound,
    "==": lambda value, bound: math.isclose(value, bound, rel_tol=1e-9, abs_tol=1e-12),
}


@dataclass(frozen=True)
class Threshold:
    operator: str
    bound: float

    @property
    def lower_is_better(self) -> bool:
        return self.operator in ("<=", "<")

    def holds(self, value: float) -> bool:
        return _OPERATORS[self.operator](value, self.bound)

    def __str__(self) -> str:
        return f"{self.operator} {self.bound:g}"


def parse_threshold(raw: Any) -> Threshold:
    """`'>= 0.85'` · `'<=300'` · `0.85` -> Threshold. Défaut `>=`."""
    if isinstance(raw, (int, float)):
        return Threshold(">=", float(raw))
    text = str(raw).strip()
    for operator in (">=", "<=", "==", ">", "<"):
        if text.startswith(operator):
            return Threshold(operator, float(text[len(operator):].strip()))
    return Threshold(">=", float(text))


# ---------------------------------------------------------------------------
# Résultats
# ---------------------------------------------------------------------------
@dataclass
class ItemResult:
    """Le score d'UN item sur UN run."""

    item_id: str
    run_index: int
    score: float
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    cost_usd: float = 0.0
    latency_ms: float = 0.0


@dataclass
class RunResult:
    """Un passage complet du dataset."""

    run_index: int
    items: list[ItemResult] = field(default_factory=list)

    @property
    def score(self) -> float:
        """La moyenne des items, un item en erreur comptant ZÉRO.

        Il était exclu de la moyenne : neuf items sur dix qui plantent et un qui
        passe donnaient 1,0 — vert. Le runner écrivait pourtant « comptés comme
        échecs, jamais ignorés » dans son avertissement, et faisait l'inverse
        dans son calcul. Un exécuteur qui lève est une mesure ratée, pas une
        absence de mesure : l'exclure revient à ne noter que les copies rendues.
        """
        if not self.items:
            return 0.0
        return sum((0.0 if i.error is not None else i.score) for i in self.items) / len(self.items)

    @property
    def cost_usd(self) -> float:
        return sum(i.cost_usd for i in self.items)

    @property
    def latency_ms(self) -> float:
        return sum(i.latency_ms for i in self.items)

    @property
    def errors(self) -> int:
        return sum(1 for i in self.items if i.error is not None)


# ---------------------------------------------------------------------------
# Agrégation
# ---------------------------------------------------------------------------
def stddev(values: Iterable[float]) -> float:
    """Écart-type de population.

    Population et non échantillon : les k runs SONT la population de ce qu'on
    mesure, pas un tirage dans un ensemble plus vaste. Avec k=3, la correction
    de Bessel gonflerait l'écart d'un tiers et déclencherait des jaunes à tort.
    """
    data = list(values)
    if len(data) < 2:
        return 0.0
    mean = sum(data) / len(data)
    return math.sqrt(sum((v - mean) ** 2 for v in data) / len(data))


@dataclass
class SuiteResult:
    """Le résultat agrégé d'une suite sur k runs."""

    suite_id: str
    metric: str
    threshold: Threshold
    runs: list[RunResult] = field(default_factory=list)
    variance_warn_pct: float = 15.0
    advisory: bool = False
    per_class: dict[str, float] = field(default_factory=dict)
    per_class_threshold: Threshold | None = None
    notes: list[str] = field(default_factory=list)

    # -- scores -------------------------------------------------------------
    @property
    def scores(self) -> list[float]:
        return [r.score for r in self.runs]

    @property
    def mean(self) -> float:
        return sum(self.scores) / len(self.scores) if self.scores else 0.0

    @property
    def stddev(self) -> float:
        return stddev(self.scores)

    @property
    def pass_rate(self) -> float:
        """Proportion des k runs dont la MOYENNE franchit le seuil.

        Souvent l'information la plus utile, et celle qu'un booléen détruit :
        « 2 runs sur 3 » dit qu'un déploiement est un pari.
        """
        if not self.runs:
            return 0.0
        return sum(1 for s in self.scores if self.threshold.holds(s)) / len(self.scores)

    @property
    def variance_pct(self) -> float:
        """Écart-type rapporté à la moyenne. 0 si la moyenne est nulle."""
        return (self.stddev / abs(self.mean) * 100.0) if self.mean else 0.0

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.runs) / len(self.runs) if self.runs else 0.0

    @property
    def latency_ms(self) -> float:
        return sum(r.latency_ms for r in self.runs) / len(self.runs) if self.runs else 0.0

    @property
    def errors(self) -> int:
        return sum(r.errors for r in self.runs)

    # -- verdict ------------------------------------------------------------
    @property
    def failing_classes(self) -> list[str]:
        """Classes sous leur seuil propre.

        Une accuracy globale de 0.95 peut cacher 0.40 sur la classe critique —
        et c'est exactement la classe qui compte (cf. po-capabilities,
        règle 4 sur `accuracy_per_class`).
        """
        if not self.per_class:
            return []
        bound = self.per_class_threshold or self.threshold
        return sorted(name for name, value in self.per_class.items() if not bound.holds(value))

    @property
    def verdict(self) -> Verdict:
        if not self.runs:
            return "red"
        if self.errors and self.errors == sum(len(r.items) for r in self.runs):
            return "red"  # tout a échoué à l'exécution : ce n'est pas un score bas
        if self.errors and self.threshold.lower_is_better:
            # Sous un plafond (latence, coût), le zéro d'un item en erreur est
            # le MEILLEUR score possible : il tirerait la moyenne sous le seuil.
            return "red"
        if not self.threshold.holds(self.mean):
            return "red"
        if self.failing_classes:
            return "red"
        if self.pass_rate < 1.0 or self.variance_pct > self.variance_warn_pct:
            return "yellow"
        return "green"

    @property
    def blocking_verdict(self) -> Verdict:
        """Le verdict tel qu'une gate doit le lire.

        Une suite `advisory` — typiquement un juge LLM non calibré — informe
        mais ne bloque pas (P9). Son verdict réel reste visible dans le rapport ;
        seul son pouvoir de bloquer disparaît.
        """
        if self.advisory:
            return "green" if self.verdict != "red" else "yellow"
        return self.verdict

    @property
    def reason(self) -> str:
        """Pourquoi ce verdict, en une clause."""
        if not self.runs:
            return "aucun run exécuté"
        if self.errors and self.errors == sum(len(r.items) for r in self.runs):
            return f"{self.errors} item(s) en erreur d'exécution — aucun score mesuré"
        if self.errors and self.threshold.lower_is_better:
            return (f"{self.errors} item(s) en erreur sous un plafond ({self.threshold}) — "
                    "un item non mesuré ne peut pas attester qu'il tient")
        if not self.threshold.holds(self.mean):
            return f"moyenne {self.mean:.3f} hors seuil {self.threshold}"
        if self.failing_classes:
            return f"classe(s) sous seuil : {', '.join(self.failing_classes)}"
        if self.pass_rate < 1.0:
            failed = round((1 - self.pass_rate) * len(self.runs))
            return f"{failed}/{len(self.runs)} run(s) sous le seuil malgré une moyenne au-dessus"
        if self.variance_pct > self.variance_warn_pct:
            return f"variance {self.variance_pct:.1f}% > {self.variance_warn_pct:g}%"
        return "seuil franchi, variance contenue"

    # -- sérialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "suiteId": self.suite_id,
            "metric": self.metric,
            "threshold": str(self.threshold),
            "runs": len(self.runs),
            "scores": [round(s, 6) for s in self.scores],
            "mean": round(self.mean, 6),
            "stddev": round(self.stddev, 6),
            "variancePct": round(self.variance_pct, 2),
            "passRate": round(self.pass_rate, 4),
            "min": round(min(self.scores), 6) if self.scores else None,
            "max": round(max(self.scores), 6) if self.scores else None,
            "perClass": {k: round(v, 6) for k, v in sorted(self.per_class.items())},
            "failingClasses": self.failing_classes,
            "errors": self.errors,
            "costUsd": round(self.cost_usd, 6),
            "latencyMs": round(self.latency_ms, 2),
            "advisory": self.advisory,
            "verdict": self.verdict,
            "blockingVerdict": self.blocking_verdict,
            "reason": self.reason,
            "notes": list(self.notes),
        }

    def render_line(self) -> str:
        return (
            f"{GLYPH[self.verdict]} {self.suite_id} — {self.metric} "
            f"{self.mean:.3f} ±{self.stddev:.3f} "
            f"(seuil {self.threshold}, k={len(self.runs)}, "
            f"pass {int(self.pass_rate * len(self.runs))}/{len(self.runs)})"
            + ("  [advisory]" if self.advisory else "")
        )


# ---------------------------------------------------------------------------
# Agrégation d'un ensemble de suites
# ---------------------------------------------------------------------------
def aggregate(results: Iterable[SuiteResult]) -> Verdict:
    """Le verdict d'un ensemble est le PIRE de ses membres.

    Pas une moyenne : un pourcentage d'avancement masquerait le trou. Même
    principe que LIFECYCLE.md R3 pour les états.
    """
    worst: Verdict = "green"
    for result in results:
        if SEVERITY[result.blocking_verdict] > SEVERITY[worst]:
            worst = result.blocking_verdict
    return worst


def summarize(results: list[SuiteResult]) -> dict[str, Any]:
    counts = {"green": 0, "yellow": 0, "red": 0}
    for result in results:
        counts[result.verdict] += 1
    return {
        "verdict": aggregate(results),
        "suites": len(results),
        "counts": counts,
        "totalCostUsd": round(sum(r.cost_usd for r in results), 6),
        "advisorySuites": sorted(r.suite_id for r in results if r.advisory),
        "results": [r.to_dict() for r in results],
    }
