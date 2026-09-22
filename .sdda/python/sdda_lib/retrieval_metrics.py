"""Métriques de récupération — la RETRIEVAL GATE (G4).

Ces métriques se calculent **sans aucun agent**, et c'est tout leur intérêt
(P5). Un retriever à recall 0.4 se présente comme « l'agent hallucine ». Les
mesurer séparément est la seule façon de distinguer les deux, et c'est
l'origine de la majorité des diagnostics erronés du domaine.

Règle de diagnostic (`rules/eval-protocol.md` §10) :

    recall@k bas                       -> le problème est le RETRIEVAL.
                                          Toucher au prompt ne servira à rien.
    recall@k haut + groundedness bas   -> le problème est la GÉNÉRATION.

`recall@k`, `nDCG@k`, `context_precision` et `citation_resolve_rate` sont
**déterministes et gratuits**. Seuls `groundedness` et `answer_relevance`
exigent un juge — donc une calibration (P9).

Aucun appel LLM, aucune I/O réseau : ce module ne fait que du calcul.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------------------
# Pertinence
# ---------------------------------------------------------------------------
# Le golden set note la pertinence de 1 à 3 (cf. golden-set.schema.json) :
#   3 = répond à la question    2 = utile    1 = lié mais insuffisant
# Un document absent du golden vaut 0. On ne suppose jamais qu'un document non
# annoté est non pertinent « pour de bon » : c'est une limite du jeu, pas un
# fait, et le rapport le dit.
DEFAULT_RELEVANCE = 0
RELEVANT_FROM = 1  # seuil à partir duquel un document compte pour le recall


def _relevance_map(expected: Iterable[Any]) -> dict[str, int]:
    """`expected_documents` -> {doc_id: relevance}.

    Accepte la forme riche (`{"doc_id": …, "relevance": 3}`) et la forme
    courte (une simple liste d'identifiants, relevance implicite 3).
    """
    out: dict[str, int] = {}
    for entry in expected or []:
        if isinstance(entry, str):
            out[entry] = 3
        elif isinstance(entry, dict):
            doc_id = entry.get("doc_id") or entry.get("id")
            if doc_id:
                out[str(doc_id)] = int(entry.get("relevance", 3))
    return out


# ---------------------------------------------------------------------------
# Métriques par requête
# ---------------------------------------------------------------------------
def recall_at_k(retrieved: Sequence[str], expected: Iterable[Any], k: int) -> float:
    """Proportion des documents pertinents présents dans le top-k.

    **La vérité est au niveau DOCUMENT, pas chunk** : un chunk différent du
    même document reste une trouvaille. Mesurer au chunk pénaliserait un
    découpage correct et pousserait à optimiser la mauvaise chose.
    """
    relevance = _relevance_map(expected)
    wanted = {d for d, r in relevance.items() if r >= RELEVANT_FROM}
    if not wanted:
        return 1.0  # rien à trouver : la requête ne teste pas le recall
    found = wanted & set(retrieved[:k])
    return len(found) / len(wanted)


def precision_at_k(retrieved: Sequence[str], expected: Iterable[Any], k: int) -> float:
    relevance = _relevance_map(expected)
    top = retrieved[:k]
    if not top:
        return 0.0
    return sum(1 for d in top if relevance.get(d, DEFAULT_RELEVANCE) >= RELEVANT_FROM) / len(top)


def mrr(retrieved: Sequence[str], expected: Iterable[Any]) -> float:
    """Rang réciproque du premier document pertinent."""
    relevance = _relevance_map(expected)
    for rank, doc in enumerate(retrieved, start=1):
        if relevance.get(doc, DEFAULT_RELEVANCE) >= RELEVANT_FROM:
            return 1.0 / rank
    return 0.0


def dcg(gains: Sequence[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(retrieved: Sequence[str], expected: Iterable[Any], k: int) -> float:
    """Gain cumulé actualisé normalisé : le bon document est-il bien CLASSÉ ?

    Complément indispensable du recall. `recall@25` bon mais `nDCG@5` médiocre
    signifie « trouvé mais mal classé » — c'est exactement le cas qu'un
    reranker corrige, et la mesure le dit sans ambiguïté.
    """
    relevance = _relevance_map(expected)
    gains = [float(relevance.get(d, DEFAULT_RELEVANCE)) for d in retrieved[:k]]
    ideal = sorted((float(v) for v in relevance.values()), reverse=True)[:k]
    denominator = dcg(ideal)
    return (dcg(gains) / denominator) if denominator else 0.0


def context_precision(retrieved: Sequence[str], expected: Iterable[Any], k: int) -> float:
    """Proportion de contexte SERVI qui est pertinent — la mesure du bruit.

    Distincte de `precision_at_k` par son usage : elle chiffre ce que l'agent
    doit lire pour rien, donc des tokens payés et de l'attention diluée.
    """
    return precision_at_k(retrieved, expected, k)


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------
# Une citation est un pointeur vers un passage servi. Une citation qui ne
# résout pas est une hallucination avec l'apparence d'une source — plus
# dangereuse qu'une absence de source, parce qu'elle rassure.
CITATION_RE = re.compile(r"\[(?:\^|cite:|source:)?([A-Za-z0-9_.:\-/#]+)\]")


def extract_citations(answer: str) -> list[str]:
    return CITATION_RE.findall(answer or "")


def citation_resolve_rate(answer: str, served_ids: Iterable[str]) -> tuple[float, list[str]]:
    """(taux de résolution, citations mortes).

    Une réponse sans citation rend 1.0 : l'absence de citation est un problème
    de `CitationMode: required`, vérifié ailleurs — pas un problème de
    résolution. Confondre les deux masquerait l'un des deux.
    """
    citations = extract_citations(answer)
    if not citations:
        return 1.0, []
    served = set(served_ids)
    dangling = sorted({c for c in citations if c not in served})
    resolved = len(citations) - sum(1 for c in citations if c in dangling)
    return resolved / len(citations), dangling


# ---------------------------------------------------------------------------
# Agrégation sur un golden set
# ---------------------------------------------------------------------------
@dataclass
class QueryOutcome:
    """Le résultat d'une requête du golden set."""

    query_id: str
    retrieved: list[str]
    expected: list[Any]
    answer: str = ""
    served_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


@dataclass
class RetrievalReport:
    """Les métriques de G4, agrégées et ventilées."""

    k: int
    queries: int
    recall_at_k: float
    ndcg_at_k: float
    context_precision: float
    mrr: float
    citation_resolve_rate: float
    dangling_citations: list[str]
    by_tag: dict[str, dict[str, float]]
    misses: list[str]
    latency_p95_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "queries": self.queries,
            "recallAtK": round(self.recall_at_k, 6),
            "ndcgAtK": round(self.ndcg_at_k, 6),
            "contextPrecision": round(self.context_precision, 6),
            "mrr": round(self.mrr, 6),
            "citationResolveRate": round(self.citation_resolve_rate, 6),
            "danglingCitations": self.dangling_citations[:20],
            "byTag": {t: {m: round(v, 6) for m, v in sorted(s.items())} for t, s in sorted(self.by_tag.items())},
            "misses": self.misses[:20],
            "latencyP95Ms": round(self.latency_p95_ms, 2),
        }


def _p95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    # Rang du 95e centile, borné — sur 3 valeurs, p95 vaut le maximum, ce qui
    # est le comportement voulu : on veut la queue, pas une interpolation.
    index = min(len(ordered) - 1, max(0, math.ceil(0.95 * len(ordered)) - 1))
    return ordered[index]


def evaluate(outcomes: Sequence[QueryOutcome], k: int) -> RetrievalReport:
    if not outcomes:
        return RetrievalReport(k, 0, 0.0, 0.0, 0.0, 0.0, 1.0, [], {}, [], 0.0)

    recalls, ndcgs, precisions, mrrs, citations = [], [], [], [], []
    dangling: list[str] = []
    misses: list[str] = []
    per_tag: dict[str, list[tuple[float, float]]] = {}

    for outcome in outcomes:
        recall = recall_at_k(outcome.retrieved, outcome.expected, k)
        ndcg = ndcg_at_k(outcome.retrieved, outcome.expected, k)
        precision = context_precision(outcome.retrieved, outcome.expected, k)
        rank = mrr(outcome.retrieved, outcome.expected)
        rate, dead = citation_resolve_rate(outcome.answer, outcome.served_ids)

        recalls.append(recall)
        ndcgs.append(ndcg)
        precisions.append(precision)
        mrrs.append(rank)
        citations.append(rate)
        dangling.extend(dead)

        # Les ratés sont nommés, pas seulement comptés : c'est la seule sortie
        # sur laquelle on peut agir. Un taux global ne dit pas quoi corriger.
        if recall < 1.0:
            wanted = {d for d, r in _relevance_map(outcome.expected).items() if r >= RELEVANT_FROM}
            absent = sorted(wanted - set(outcome.retrieved[:k]))
            if absent:
                misses.append(f"{outcome.query_id} -> manque {absent}")

        for tag in outcome.tags or ["(sans tag)"]:
            per_tag.setdefault(tag, []).append((recall, ndcg))

    def mean(values: Sequence[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    # La ventilation par tag est ce qui rend le rapport actionnable : un recall
    # global de 0.82 peut cacher 0.35 sur les questions multi-sauts, et c'est
    # cette famille-là qu'il faut traiter — pas le retriever entier.
    by_tag = {
        tag: {
            "recallAtK": mean([r for r, _ in pairs]),
            "ndcgAtK": mean([n for _, n in pairs]),
            "queries": float(len(pairs)),
        }
        for tag, pairs in per_tag.items()
    }

    return RetrievalReport(
        k=k,
        queries=len(outcomes),
        recall_at_k=mean(recalls),
        ndcg_at_k=mean(ndcgs),
        context_precision=mean(precisions),
        mrr=mean(mrrs),
        citation_resolve_rate=mean(citations),
        dangling_citations=sorted(set(dangling)),
        by_tag=by_tag,
        misses=misses,
        latency_p95_ms=_p95([o.latency_ms for o in outcomes]),
    )


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------
def diagnose(recall: float, groundedness: float | None, thresholds: dict[str, float]) -> str:
    """Traduit deux mesures en une conclusion actionnable.

    Existe pour éviter la perte de temps la plus coûteuse du domaine : passer
    des jours sur un prompt alors que le document n'était pas dans le contexte.
    """
    recall_min = thresholds.get("recallAtK", 0.80)
    ground_min = thresholds.get("groundedness", 0.85)

    if recall < recall_min:
        return (
            f"RETRIEVAL — recall {recall:.2f} < {recall_min:g}. "
            "Le document n'arrive pas dans le contexte : découpage, index ou "
            "stratégie de requête. **Ne pas toucher au prompt**, aucune quantité "
            "de prompt engineering ne compensera."
        )
    if groundedness is None:
        return f"RETRIEVAL OK (recall {recall:.2f}). Groundedness non mesurée — juge absent ou non calibré."
    if groundedness < ground_min:
        return (
            f"GÉNÉRATION — recall {recall:.2f} correct mais groundedness "
            f"{groundedness:.2f} < {ground_min:g}. Le contexte est bon, le modèle "
            "s'en écarte : prompt, format de sortie, ou politique de citation."
        )
    return f"OK — recall {recall:.2f}, groundedness {groundedness:.2f}, les deux étages tiennent."
