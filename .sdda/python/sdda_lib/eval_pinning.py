"""Épinglage des résultats d'évaluation (P10).

Un résultat d'eval sait ce qu'il a évalué. Il est indexé par le tuple :

    (prompt_hash, model_id, retrieval_index_hash, tool_schema_hash, dataset_hash)

Si l'un bouge, le résultat est **périmé** — pas « probablement encore valable ».

C'est le seul garde-fou contre la dérive silencieuse la plus banale du domaine :
un ajustement de prompt « anodin » un vendredi, et des scores qu'on croit
toujours valables trois semaines plus tard. Aucun compilateur n'attrape
l'édition d'une chaîne de caractères.

`model_id` fait partie du tuple parce qu'un fournisseur peut déplacer un modèle
sous vos pieds : mêmes prompt et dataset, scores différents, et rien dans le
dépôt n'a changé.

Invariant : `eval-baseline-hash-pinned`. Aucun appel LLM, aucun réseau.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from sdda_lib import hashing, paths

# Les cinq dimensions. L'ordre est fixe : il fait partie du format sérialisé.
PIN_KEYS = ("promptHash", "modelId", "indexHash", "toolSchemaHash", "datasetHash")


def _same(pinned: str, current: str) -> bool:
    """Deux valeurs de pin désignent-elles la même chose ?

    **Toutes les dimensions ne sont pas des hashes.** `modelId` vaut
    `claude-sonnet-5` : le comparer avec `hashes_match` renverrait toujours
    faux (la valeur n'a pas la forme `sha256:…`), et **toute baseline serait
    périmée en permanence**. Le mécanisme d'épinglage crierait à chaque run,
    donc finirait désactivé — et P10 ne protégerait plus rien.

    D'où l'ordre : égalité d'abord, correspondance de préfixe ensuite, et
    seulement quand les deux valeurs sont réellement des hashes (un hash
    épinglé court doit matcher le hash complet courant).
    """
    if pinned == current:
        return True
    if hashing.is_hash_ref(pinned) and hashing.is_hash_ref(current):
        return hashing.hashes_match(pinned, current)
    return False


@dataclass(frozen=True)
class PinTuple:
    """Ce qu'un résultat a réellement mesuré."""

    promptHash: str = ""
    modelId: str = ""
    indexHash: str = ""
    toolSchemaHash: str = ""
    datasetHash: str = ""

    def to_dict(self) -> dict[str, str]:
        return {k: getattr(self, k) for k in PIN_KEYS}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PinTuple":
        data = data or {}
        return cls(**{k: str(data.get(k, "") or "") for k in PIN_KEYS})

    def digest(self) -> str:
        """Empreinte du tuple — la clé sous laquelle un résultat est rangé."""
        return hashing.sha256_struct(self.to_dict())

    def diff(self, other: "PinTuple") -> dict[str, tuple[str, str]]:
        """Dimensions qui ont bougé : {clé: (épinglé, courant)}.

        Une dimension VIDE d'un côté n'est pas une divergence : elle signifie
        « non applicable » (un agent sans retrieval n'a pas d'`indexHash`).
        Traiter l'absence comme un changement périmerait tout, tout le temps,
        et la mécanique finirait désactivée.
        """
        moved: dict[str, tuple[str, str]] = {}
        for key in PIN_KEYS:
            pinned, current = getattr(self, key), getattr(other, key)
            if not pinned or not current:
                continue
            if _same(pinned, current):
                continue
            moved[key] = (pinned, current)
        return moved

    def is_stale_against(self, current: "PinTuple") -> bool:
        return bool(self.diff(current))


# ---------------------------------------------------------------------------
# Construction du tuple courant depuis le disque
# ---------------------------------------------------------------------------
def current_pins(
    root: Path,
    ir: dict[str, Any],
    *,
    suite: dict[str, Any] | None = None,
    agent_id: str | None = None,
) -> PinTuple:
    """Calcule le tuple courant pour une suite donnée.

    Lit ce qui existe sur disque plutôt que ce que l'IR déclare : c'est
    précisément l'écart entre les deux qu'on cherche à détecter.
    """
    agents = {a["id"]: a for a in ir.get("agents") or []}
    target = agent_id or (suite or {}).get("agentRef")

    prompt_hash = ""
    model_id = ""
    if target and target in agents:
        agent = agents[target]
        prompt_hash = _prompt_hash_on_disk(root, agent)
        model_id = _resolve_model(root, ir, agent.get("modelTier", ""))

    index_hash = ""
    retrievers = ir.get("retrievers") or []
    if target and target in agents:
        used = agents[target].get("retrievers") or []
        retrievers = [r for r in retrievers if r.get("id") in used] or retrievers
    if retrievers:
        index_hash = hashing.sha256_struct(
            sorted((r.get("id", ""), r.get("indexHash", "")) for r in retrievers)
        )

    tools = ir.get("tools") or []
    if target and target in agents:
        used = set(agents[target].get("tools") or [])
        tools = [t for t in tools if t.get("id") in used]
    tool_schema_hash = (
        hashing.sha256_struct(
            sorted(
                (t.get("id", ""), t.get("inputSchema"), t.get("outputSchema"))
                for t in tools
            )
        )
        if tools
        else ""
    )

    dataset_hash = ""
    dataset = (suite or {}).get("dataset")
    if dataset:
        dataset_hash = dataset_digest(root, str(dataset))

    return PinTuple(
        promptHash=prompt_hash,
        modelId=model_id,
        indexHash=index_hash,
        toolSchemaHash=tool_schema_hash,
        datasetHash=dataset_hash,
    )


def _prompt_hash_on_disk(root: Path, agent: dict[str, Any]) -> str:
    """Le hash du FICHIER, pas celui que l'IR a mémorisé.

    Si les deux diffèrent, le prompt a été édité depuis la compilation — et
    c'est exactement le cas que cette mécanique existe pour attraper.
    """
    ref = agent.get("promptRef")
    if not ref:
        return str(agent.get("promptHash", "") or "")
    path = paths.resolve_rel(root, str(ref))
    if path.is_file():
        return hashing.sha256_file(path)
    return str(agent.get("promptHash", "") or "")


def _resolve_model(root: Path, ir: dict[str, Any], tier: str) -> str:
    """Tier -> identifiant de modèle, via `## Runtime Models` de STACK.md.

    Le tier seul ne suffit pas à épingler : `balanced` peut désigner deux
    modèles différents à deux semaines d'intervalle.
    """
    if not tier:
        return ""
    try:
        from sdda_lib.layered_config import read_runtime_tier_map  # type: ignore

        return str(read_runtime_tier_map(root).get(tier, "") or tier)
    except Exception:
        # Pas de table résolvable : on épingle le tier, qui reste plus
        # informatif que rien — et la dimension sera simplement moins fine.
        return tier


def dataset_digest(root: Path, rel: str) -> str:
    """Empreinte d'un dataset JSONL, insensible à l'ordre des lignes.

    Réordonner un jeu ne change pas ce qu'il mesure ; le re-hasher pour autant
    périmerait des résultats valides.
    """
    path = paths.resolve_rel(root, rel)
    if not path.is_file():
        return ""
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(hashing.canonical_json(json.loads(raw)))
        except json.JSONDecodeError:
            lines.append(raw)
    return hashing.sha256_struct(sorted(lines))


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------
@dataclass
class Baseline:
    """Un résultat de référence, et ce qu'il a mesuré."""

    suite_id: str
    metric: str
    mean: float
    stddev: float
    pass_rate: float
    verdict: str
    pins: PinTuple
    recorded_at: str = ""
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = {k: v for k, v in asdict(self).items() if k != "pins"}
        data["pins"] = self.pins.to_dict()
        data["pinDigest"] = self.pins.digest()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Baseline":
        return cls(
            suite_id=str(data.get("suite_id") or data.get("suiteId") or ""),
            metric=str(data.get("metric", "")),
            mean=float(data.get("mean", 0.0)),
            stddev=float(data.get("stddev", 0.0)),
            pass_rate=float(data.get("pass_rate", data.get("passRate", 0.0))),
            verdict=str(data.get("verdict", "")),
            pins=PinTuple.from_dict(data.get("pins")),
            recorded_at=str(data.get("recorded_at", data.get("recordedAt", ""))),
            label=str(data.get("label", "")),
        )


def load_baselines(path: Path) -> dict[str, Baseline]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    entries = data.get("baselines", data) if isinstance(data, dict) else {}
    return {
        suite_id: Baseline.from_dict({**payload, "suite_id": suite_id})
        for suite_id, payload in entries.items()
        if isinstance(payload, dict)
    }


@dataclass
class Staleness:
    """Le verdict de fraîcheur d'une baseline."""

    suite_id: str
    stale: bool
    moved: dict[str, tuple[str, str]]

    @property
    def summary(self) -> str:
        if not self.stale:
            return "à jour"
        dims = ", ".join(sorted(self.moved))
        return f"périmée — {dims} a bougé"


def check_staleness(
    baselines: dict[str, Baseline], current: dict[str, PinTuple]
) -> list[Staleness]:
    out: list[Staleness] = []
    for suite_id, baseline in sorted(baselines.items()):
        pins_now = current.get(suite_id)
        if pins_now is None:
            continue
        moved = baseline.pins.diff(pins_now)
        out.append(Staleness(suite_id, bool(moved), moved))
    return out


def regression_delta(baseline: Baseline, mean: float, lower_is_better: bool) -> float:
    """Variation en % par rapport à la baseline. Négatif = dégradation.

    Le signe tient compte du sens de la métrique : une latence qui baisse est
    une amélioration, un groundedness qui baisse ne l'est pas.
    """
    if not baseline.mean:
        return 0.0
    raw = (mean - baseline.mean) / abs(baseline.mean) * 100.0
    return -raw if lower_is_better else raw
