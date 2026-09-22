"""Socle commun des graders : résultat, protocole, aides de normalisation.

Un grader **mesure**, il ne **juge** pas : il rend un score et des détails,
et c'est `eval_stats.Threshold` — écrit par l'auteur de la CAP dans l'AC —
qui décide du pass. Cette séparation est ce qui permet au même grader de
servir `>= 0.85` sur une métrique et `<= 0.25` sur une autre sans qu'une table
de métriques cachée inverse un verdict en silence.

Deux natures d'échec, à ne jamais confondre (eval-protocol.md §1, §10) :

- **une mauvaise réponse** → `score` bas, `error is None` ;
- **une exécution impossible à noter** → `error = "[CLASS] …"`, et le score
  est ignoré par `RunResult.score` (`eval_stats` exclut les items en erreur).

Une sortie non numérique soumise à `numeric-tolerance`, une trace illisible
pour `trajectory`, une réponse de juge impossible à parser : ce sont des
erreurs, pas des zéros. Les compter comme des zéros ferait baisser la moyenne
pour une raison qui n'a rien à voir avec la qualité du système évalué, et le
diagnostic (§10) partirait dans la mauvaise direction.

Convention des exceptions : un problème de **câblage** qui rendrait le grader
inutilisable sur *tous* les items (client absent, configuration invalide) lève
`SddaError` ; un problème propre à *un* item rend un `GradeResult` en erreur.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from sdda_lib.errors import SddaError
from sdda_lib.eval_stats import Threshold

# Classes d'erreur émises par les graders. Regroupées ici pour que le registre
# (`sync_error_registry.py`) et un lecteur humain les trouvent d'un coup.
CLS_OUTPUT_UNGRADABLE = "EVAL_OUTPUT_UNGRADABLE"      # la sortie ne peut pas être notée par ce grader
CLS_EXPECTED_INVALID = "DATASET_ITEM_INVALID"          # l'attendu de l'item est mal formé pour ce grader
CLS_CONFIG_INVALID = "EVAL_GRADER_CONFIG_INVALID"      # configuration de grader incohérente (lève)
CLS_MEASUREMENT_MISSING = "MEASUREMENT_MISSING"        # cost/latency : aucune mesure dans le run
CLS_GRADER_UNKNOWN = "AC_GRADER_UNKNOWN"               # nom de grader hors registre


@dataclass
class GradeResult:
    """Le résultat d'UN grader sur UN item d'UN run.

    `score` : dans [0,1] pour les graders bornés ; valeur brute (USD, ms) pour
    `cost` et `latency`. `passed` reste `None` tant qu'un `Threshold` ne l'a
    pas fixé — un grader ne décide jamais du pass.
    """

    score: float
    passed: bool | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def failure(cls, error_class: str, message: str, **detail: Any) -> "GradeResult":
        """Un item impossible à noter. `score` vaut 0.0 par convention mais est ignoré."""
        return cls(score=0.0, passed=None, detail=dict(detail), error=f"[{error_class}] {message}")

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def error_class(self) -> str | None:
        if not self.error:
            return None
        m = re.match(r"^\[([A-Z][A-Z0-9_]+)\]", self.error)
        return m.group(1) if m else None

    def with_threshold(self, threshold: Threshold) -> "GradeResult":
        """Copie avec `passed` fixé par le seuil — le seul endroit où le pass se décide."""
        passed = None if self.error else threshold.holds(self.score)
        return GradeResult(score=self.score, passed=passed, detail=dict(self.detail), error=self.error)

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "passed": self.passed, "detail": self.detail, "error": self.error}


@runtime_checkable
class Grader(Protocol):
    """Ce qu'un grader expose au registre et au runner.

    `deterministic=False` exige k runs et, s'il s'agit d'un juge LLM, une
    calibration (P3, P9). `bounded=False` signale un score brut (cost,
    latency) auquel la contrainte [0,1] ne s'applique pas. `metrics` liste les
    métriques que le grader sait servir : c'est ce que `list_graders.py`
    confronte aux `metric:` déclarées dans les CAPs.
    """

    name: str
    deterministic: bool
    bounded: bool
    metrics: tuple[str, ...]

    def score(self, item: dict, output: Any, *, config: dict) -> GradeResult: ...


class GradingError(Exception):
    """Un item impossible à noter, remonté comme exception par `BaseGrader.grade`.

    `score()` rend l'erreur comme donnée (`GradeResult.error`) ; `grade()` la
    lève, pour qu'un runner qui ne lit que `score/passed/detail` compte l'item
    en **erreur d'exécution** et non comme un score de 0. Le `GradeResult`
    complet reste accessible via `.result`.
    """

    def __init__(self, result: GradeResult):
        super().__init__(result.error or "item impossible à noter")
        self.result = result


class BaseGrader:
    """Socle concret : `grade()` adapte `score()` au contrat callable du runner.

    `eval_runner.py` appelle `grader.grade(item, output, trace, measures)` sans
    connaître la nature du grader. `reads` dit sur quoi porte la mesure :
    `output` (la réponse), `trace` (la trajectoire), `measures` (coût,
    latence). Les sous-classes n'implémentent que `score()`.
    """

    name: str = ""
    deterministic: bool = True
    bounded: bool = True
    metrics: tuple[str, ...] = ()
    reads: str = "output"  # output | trace | measures

    @property
    def available(self) -> bool:
        """Faux quand le grader est enregistré mais ne peut noter aucun item (juge sans client).

        Un runner qui résout un grader indisponible doit le traiter comme
        absent, pas l'appeler — sinon chaque item part en erreur d'exécution.
        """
        return True

    def score(self, item: dict, output: Any, *, config: dict) -> GradeResult:  # pragma: no cover - abstrait
        raise NotImplementedError

    def grade(self, item: dict, output: Any, trace: Any = None, measures: dict | None = None, config: dict | None = None) -> GradeResult:
        subject = output
        cfg = dict(config or {})
        if self.reads == "trace" and trace is not None:
            subject = trace
            cfg.setdefault("answer", output)  # la réponse reste scannable (observables interdits, L8)
        elif self.reads == "measures" and measures:
            subject = measures
        result = self.score(item, subject, config=cfg)
        if result.error:
            raise GradingError(result)
        return result


# ---------------------------------------------------------------------------
# Aides partagées
# ---------------------------------------------------------------------------
_MISSING = object()


def expected_of(item: dict, key: str = "expected") -> Any:
    """L'attendu d'un item, ou `_MISSING` sentinelle si la clé est absente."""
    return item.get(key, _MISSING)


def is_missing(value: Any) -> bool:
    return value is _MISSING


def as_text(value: Any) -> str:
    """Une sortie quelconque vue comme texte.

    Une structure devient du JSON canonique (clés triées) pour que deux
    sorties égales en substance donnent le même texte — sinon `exact` échouerait
    sur l'ordre des clés d'un dict, ce qui n'est pas une différence de fond.
    """
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return str(value)


_WS_RE = re.compile(r"\s+")
_TERMINAL_PUNCT = ".!?;:,…"


def normalize_text(text: str, *, case_sensitive: bool = False, strip_punctuation: bool = True) -> str:
    """Normalisation « de surface » : casse, espaces multiples, ponctuation terminale.

    NFKC unifie les variantes Unicode d'un même caractère (espace insécable,
    ligatures). On ne touche à rien de plus : une différence de fond doit
    rester une différence.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _WS_RE.sub(" ", text).strip()
    if strip_punctuation:
        text = text.rstrip(_TERMINAL_PUNCT).rstrip()
    if not case_sensitive:
        text = text.casefold()
    return text


def config_error(grader: str, message: str, fix: str) -> SddaError:
    """Erreur de configuration : lève, parce qu'aucun item ne pourra être noté."""
    return SddaError(f"grader `{grader}` : configuration invalide", CLS_CONFIG_INVALID, fix, detail=message)


def clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else float(value)
