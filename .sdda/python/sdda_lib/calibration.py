"""Calibration des juges LLM (P9).

**Un grader LLM non calibré ne rend pas de verdict bloquant.**

Sans validation contre des labels humains, on mesure la complaisance d'un
modèle envers un autre modèle — souvent le même — et on appelle ça de la
qualité. Le mécanisme est simple et sa valeur est entière : il transforme une
opinion automatisée en instrument dont on connaît l'erreur.

Sous le seuil, le juge ne disparaît pas : il bascule en **`advisory`**. Il
produit un score informatif et perd son pouvoir de bloquer. C'est plus honnête
que de le désactiver (on perd l'information) et que de le garder bloquant (on
bloque sur du bruit).

Invariant : `llm-judge-calibrated`. Aucun appel LLM, aucune I/O réseau.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


# ---------------------------------------------------------------------------
# Accord entre deux annotateurs
# ---------------------------------------------------------------------------
def cohen_kappa(human: Sequence[Any], judge: Sequence[Any]) -> float:
    """Kappa de Cohen — l'accord CORRIGÉ du hasard.

    Pourquoi pas un simple taux d'accord : sur un jeu où 90 % des items sont
    « bon », un juge qui répond toujours « bon » obtient 90 % d'accord et zéro
    compétence. Kappa le ramène à 0. C'est précisément le cas qu'on veut
    attraper, et c'est le comportement par défaut d'un juge mal conçu.

    Renvoie une valeur dans [-1, 1]. 1 = accord parfait, 0 = niveau du hasard,
    négatif = pire que le hasard.
    """
    if len(human) != len(judge):
        raise ValueError("les deux séries doivent avoir la même longueur")
    n = len(human)
    if n == 0:
        return 0.0

    observed = sum(1 for h, j in zip(human, judge) if h == j) / n

    human_counts, judge_counts = Counter(human), Counter(judge)
    expected = sum(
        (human_counts[label] / n) * (judge_counts[label] / n)
        for label in set(human_counts) | set(judge_counts)
    )
    if math.isclose(expected, 1.0):
        # Les deux annotateurs sont constants et identiques : l'accord est
        # total mais NON INFORMATIF — un jeu à une seule classe ne dit pas si
        # le juge distingue quoi que ce soit. Rendre 1.0 calibrait un juge
        # « toujours pass » sur un jeu « tout pass » ; 0.0 le laisse advisory.
        return 0.0
    return (observed - expected) / (1 - expected)


def pearson(human: Sequence[float], judge: Sequence[float]) -> float:
    """Corrélation de Pearson, pour les grilles continues."""
    n = len(human)
    if n < 2 or len(judge) != n:
        return 0.0
    mean_h, mean_j = sum(human) / n, sum(judge) / n
    cov = sum((h - mean_h) * (j - mean_j) for h, j in zip(human, judge))
    var_h = math.sqrt(sum((h - mean_h) ** 2 for h in human))
    var_j = math.sqrt(sum((j - mean_j) ** 2 for j in judge))
    return cov / (var_h * var_j) if var_h and var_j else 0.0


# ---------------------------------------------------------------------------
# Rapport de calibration
# ---------------------------------------------------------------------------
@dataclass
class CalibrationReport:
    grader: str
    items: int
    scale: str                      # "ordinal" | "continuous"
    agreement: float                # kappa ou corrélation, selon l'échelle
    raw_agreement: float            # taux d'accord brut, pour comparaison
    min_items: int
    min_agreement: float
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    labels_are_synthetic: bool = False
    notes: list[str] = field(default_factory=list)
    #: Accord recopié d'un résumé, sans labels pour le recalculer. Un kappa
    #: écrit à la main est une affirmation : il n'a jamais calibré un juge.
    declared_only: bool = False

    @property
    def calibrated(self) -> bool:
        if self.labels_are_synthetic or self.declared_only:
            return False
        return self.items >= self.min_items and self.agreement >= self.min_agreement

    @property
    def advisory(self) -> bool:
        """Non calibré => advisory : il informe, il ne bloque plus."""
        return not self.calibrated

    @property
    def reason(self) -> str:
        if self.labels_are_synthetic:
            return (
                "labels produits par un LLM — la calibration mesurerait l'accord "
                "de deux modèles entre eux [JUDGE_CALIBRATION_SYNTHETIC]"
            )
        if self.declared_only:
            return "accord déclaré sans labels résolvables — rien n'a été recalculé"
        if self.items < self.min_items:
            return f"{self.items} items < {self.min_items} exigés"
        if self.agreement < self.min_agreement:
            return (
                f"accord {self.agreement:.3f} < {self.min_agreement:g} "
                f"(accord brut {self.raw_agreement:.3f} — l'écart entre les deux "
                "mesure ce que le hasard expliquait)"
            )
        return f"accord {self.agreement:.3f} sur {self.items} items"

    def to_dict(self) -> dict[str, Any]:
        return {
            "grader": self.grader,
            "items": self.items,
            "scale": self.scale,
            "agreement": round(self.agreement, 6),
            "rawAgreement": round(self.raw_agreement, 6),
            "minItems": self.min_items,
            "minAgreement": self.min_agreement,
            "calibrated": self.calibrated,
            "advisory": self.advisory,
            "reason": self.reason,
            "confusion": self.confusion,
            "labelsAreSynthetic": self.labels_are_synthetic,
            "declaredOnly": self.declared_only,
            "notes": list(self.notes),
        }


def _confusion(human: Sequence[Any], judge: Sequence[Any]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {}
    for h, j in zip(human, judge):
        matrix.setdefault(str(h), {}).setdefault(str(j), 0)
        matrix[str(h)][str(j)] += 1
    return matrix


def calibrate(
    grader: str,
    human: Sequence[Any],
    judge: Sequence[Any],
    *,
    min_items: int = 50,
    min_agreement: float = 0.6,
    scale: str = "ordinal",
    labels_are_synthetic: bool = False,
) -> CalibrationReport:
    notes: list[str] = []
    if len(human) != len(judge):
        notes.append(
            f"séries de longueurs différentes ({len(human)} labels humains, {len(judge)} verdicts du "
            "juge) — seules les paires complètes sont comptées"
        )
    n = min(len(human), len(judge))
    human, judge = list(human[:n]), list(judge[:n])

    raw = sum(1 for h, j in zip(human, judge) if h == j) / n if n else 0.0
    if scale == "continuous":
        agreement = pearson([float(h) for h in human], [float(j) for j in judge])
    else:
        agreement = cohen_kappa(human, judge)
    if n and len(set(human)) == 1 and len(set(judge)) == 1:
        notes.append(
            "jeu à une seule classe : l'accord est total mais non informatif — il faut des items "
            "que les humains jugent différemment pour calibrer"
        )

    # Un juge constant obtient un kappa nul quel que soit son taux d'accord.
    # Le dire explicitement évite de chercher la cause ailleurs.
    if n and len(set(judge)) == 1:
        notes.append(
            "le juge rend toujours la même valeur — il n'apprend rien du contenu, "
            "la grille est probablement inapplicable en l'état"
        )
    if n and raw - max(agreement, 0.0) > 0.4:
        notes.append(
            "écart important entre accord brut et accord corrigé : le jeu est "
            "déséquilibré, l'accord apparent vient surtout du hasard"
        )

    return CalibrationReport(
        grader=grader,
        items=n,
        scale=scale,
        agreement=agreement,
        raw_agreement=raw,
        min_items=min_items,
        min_agreement=min_agreement,
        confusion=_confusion(human, judge),
        labels_are_synthetic=labels_are_synthetic,
        notes=notes,
    )


def measured_for_suite(root: Path, mission: Any, suite_id: str) -> dict[str, Any] | None:
    """La calibration MESURÉE d'une suite, telle que `calibrate-judge` l'a écrite.

    C'est la seule que le juge doit croire. Un `calibration: {kappa: 0.9}`
    recopié dans la suite par qui l'écrit (qa-evals) rendait un juge bloquant
    sans qu'aucun label n'ait été relu — P9 contourné par une ligne de YAML.

    Rend `{"kappa", "n", "verified"}` ou None (aucun rapport, suite absente,
    accord non recalculé) : None laisse le juge advisory.
    """
    head = str(mission or "").split("-", 1)[0]
    if not head.isdigit():
        return None
    from sdda_lib import gate_reports  # noqa: PLC0415 — évite un cycle d'import

    path = gate_reports.report_path(root, "G5", head, "calibration")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    judges = (data.get("data") or {}).get("judges") if isinstance(data, dict) else None
    for entry in judges or []:
        if isinstance(entry, dict) and entry.get("suiteId") == suite_id:
            if not entry.get("verified") or entry.get("labelsAreSynthetic"):
                return None
            agreement = entry.get("agreement")
            if not isinstance(agreement, (int, float)):
                return None
            return {"kappa": float(agreement), "n": int(entry.get("items") or 0), "verified": True}
    return None


# ---------------------------------------------------------------------------
# Chargement d'un jeu de calibration
# ---------------------------------------------------------------------------
@dataclass
class CalibrationSet:
    """Items labellisés à la main, et les verdicts du juge sur les mêmes items.

    `declared_only` distingue deux situations que rien d'autre ne sépare :
      - les labels sont là, l'accord est **recalculé** ;
      - seul un résumé existe, l'accord est **déclaré**.

    Un kappa déclaré sans ses labels est une affirmation, pas une mesure. On
    l'accepte — exiger les labels bruts serait impraticable quand ils portent
    des PII — mais le rapport dit lequel des deux on lit. Sans cette
    distinction, une ligne `"kappa": 0.9` écrite à la main vaudrait une
    calibration réelle.
    """

    grader: str
    human: list[Any]
    judge: list[Any]
    scale: str = "ordinal"
    labels_are_synthetic: bool = False
    declared_only: bool = False
    declared_agreement: float | None = None
    declared_items: int = 0
    labels_path: Path | None = None     # le JSONL relu via `labelsRef`, s'il l'a été


def _load_labels_jsonl(path: Path) -> tuple[list[Any], list[Any], bool]:
    """Lit un fichier de labels JSONL -> (humains, juge, synthétique)."""
    human: list[Any] = []
    judge: list[Any] = []
    synthetic = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and "human" in item and "judge" in item:
            human.append(item["human"])
            judge.append(item["judge"])
            if str(item.get("source", "")).lower() in ("llm", "model", "generated"):
                synthetic = True
    return human, judge, synthetic


def load_calibration_set(path: Path) -> CalibrationSet | None:
    """Lit un rapport de calibration JSON. Deux formes acceptées.

    **Inline** — les paires sont dans le fichier :
        {"grader": "groundedness", "scale": "ordinal",
         "items": [{"id": "…", "human": "pass", "judge": "pass"}, …]}

    **Résumé + labels séparés** — le fichier porte le verdict, les paires
    vivent ailleurs (utile quand elles contiennent des PII) :
        {"grader": "groundedness", "items": 52, "kappa": 0.71,
         "labelsRef": "workspace/pipeline/datasets/calibration/groundedness-v1.jsonl"}

    Si `labelsRef` est résolvable, l'accord est **recalculé** et c'est cette
    valeur qui fait foi. Sinon le résumé est accepté, marqué `declared_only`.

    Un label marqué `source: "llm"` rend tout le jeu synthétique : la
    calibration mesurerait alors l'accord de deux modèles entre eux.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    grader = str(data.get("grader") or path.stem)
    scale = str(data.get("scale") or "ordinal")
    flag = data.get("labelsAreSynthetic")
    # `bool("false")` vaut True : une chaîne se lit comme une chaîne.
    synthetic = flag.strip().lower() in ("true", "yes", "1") if isinstance(flag, str) else bool(flag)
    raw_items = data.get("items")

    human: list[Any] = []
    judge: list[Any] = []

    # Forme inline : `items` est une liste de paires.
    if isinstance(raw_items, list):
        pairs = [i for i in raw_items if isinstance(i, dict) and "human" in i and "judge" in i]
        human = [i["human"] for i in pairs]
        judge = [i["judge"] for i in pairs]
        synthetic = synthetic or any(
            str(i.get("source", "")).lower() in ("llm", "model", "generated") for i in pairs
        )

    # Forme résumé : les labels sont ailleurs. On les récupère si on peut.
    labels_ref = data.get("labelsRef")
    labels_path: Path | None = None
    if not human and labels_ref:
        candidate = Path(labels_ref)
        if not candidate.is_absolute():
            # Le chemin est relatif à la racine du projet, pas au rapport.
            for base in (path.parents[3] if len(path.parents) > 3 else path.parent, path.parent):
                resolved = base / labels_ref
                if resolved.is_file():
                    candidate = resolved
                    break
        if candidate.is_file():
            human, judge, from_labels = _load_labels_jsonl(candidate)
            synthetic = synthetic or from_labels
            labels_path = candidate

    declared_agreement = data.get("kappa", data.get("agreement"))
    declared_items = raw_items if isinstance(raw_items, int) else len(human)

    return CalibrationSet(
        grader=grader,
        human=human,
        judge=judge,
        scale=scale,
        labels_are_synthetic=synthetic,
        declared_only=not human,
        declared_agreement=float(declared_agreement) if isinstance(declared_agreement, (int, float)) else None,
        declared_items=int(declared_items or 0),
        labels_path=labels_path,
    )
