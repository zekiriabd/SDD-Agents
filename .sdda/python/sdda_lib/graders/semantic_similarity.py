"""Grader `semantic-similarity` — ressemblance **lexicale**, sans dépendance.

Ce que c'est : un Jaccard pondéré sur les tokens (multi-ensemble, donc la
répétition compte) combiné à un Jaccard sur les bigrammes de tokens adjacents.
Insensible à l'ordre des mots pour la partie unigramme ; les bigrammes
réintroduisent une faible sensibilité à l'ordre local.

Ce que ce n'est **pas** : un embedding. Il ne mesure pas le sens, il mesure
la ressemblance de surface. Conséquences à connaître avant de s'en servir :

- une **négation** n'est pas détectée : « le paiement a été accepté » et
  « le paiement n'a pas été accepté » obtiennent un score élevé ;
- une **paraphrase** sans mots communs obtient un score bas ;
- un **synonyme** est un mot différent.

Le rapport le déclare : `detail["method"] = "lexical-weighted-jaccard+bigrams"`,
avec `detail["caveat"]`. Un lecteur du rapport doit pouvoir voir qu'il regarde
une ressemblance lexicale et non un jugement sémantique. Un vrai embedding
viendra d'un provider (stack) et sortira du périmètre déterministe de ce
paquet ; il portera un autre `method`.

Déterministe (contrairement au ◐ de la table §4, qui vise l'implémentation par
embeddings). Score dans [0,1]. `stopwords` (liste) et `unigram_weight`
(défaut 0.7) sont configurables.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any, Iterable

from sdda_lib.graders._base import (
    CLS_EXPECTED_INVALID,
    BaseGrader,
    GradeResult,
    as_text,
    clamp01,
    config_error,
    expected_of,
    is_missing,
)

METHOD = "lexical-weighted-jaccard+bigrams"
CAVEAT = "ressemblance lexicale, pas sémantique : négation, paraphrase et synonymes ne sont pas détectés"

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str, stopwords: Iterable[str] = ()) -> list[str]:
    """Tokens minuscules, Unicode normalisé, sans les stopwords fournis."""
    norm = unicodedata.normalize("NFKC", text).casefold()
    stop = {s.casefold() for s in stopwords}
    return [t for t in _TOKEN_RE.findall(norm) if t not in stop]


def bigrams(tokens: list[str]) -> list[str]:
    return [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]


def weighted_jaccard(left: Counter, right: Counter) -> float:
    """Jaccard sur multi-ensembles : Σ min / Σ max. 1.0 si les deux sont vides."""
    keys = set(left) | set(right)
    if not keys:
        return 1.0
    numerator = sum(min(left[k], right[k]) for k in keys)
    denominator = sum(max(left[k], right[k]) for k in keys)
    return numerator / denominator if denominator else 1.0


class SemanticSimilarityGrader(BaseGrader):
    name = "semantic-similarity"
    deterministic = True
    bounded = True
    metrics = ("semantic_similarity", "lexical_similarity", "answer_similarity")

    def score(self, item: dict, output: Any, *, config: dict) -> GradeResult:
        reference = expected_of(item)
        if is_missing(reference):
            return GradeResult.failure(CLS_EXPECTED_INVALID, f"item `{item.get('id', '?')}` sans `expected` : aucune référence à laquelle comparer")
        weight = float(config.get("unigram_weight", 0.7))
        if not 0.0 <= weight <= 1.0:
            raise config_error(self.name, f"unigram_weight={weight} hors de [0,1]", "déclarer `unigram_weight` entre 0 et 1")
        stopwords = tuple(config.get("stopwords", ()))

        ref_tokens = tokenize(as_text(reference), stopwords)
        if not ref_tokens:
            return GradeResult.failure(CLS_EXPECTED_INVALID, f"item `{item.get('id', '?')}` : référence vide après tokenisation", expected=as_text(reference))
        out_tokens = tokenize(as_text(output), stopwords)

        uni = weighted_jaccard(Counter(ref_tokens), Counter(out_tokens))
        # Sans bigramme possible (référence d'un seul mot), la composante d'ordre n'a pas de sens : tout sur les unigrammes.
        if len(ref_tokens) < 2 and len(out_tokens) < 2:
            bi, effective_weight = uni, 1.0
        else:
            bi, effective_weight = weighted_jaccard(Counter(bigrams(ref_tokens)), Counter(bigrams(out_tokens))), weight
        score = clamp01(effective_weight * uni + (1.0 - effective_weight) * bi)
        shared = sorted((Counter(ref_tokens) & Counter(out_tokens)).elements())
        return GradeResult(
            score=score,
            detail={
                "method": METHOD,
                "caveat": CAVEAT,
                "unigram_score": round(uni, 6),
                "bigram_score": round(bi, 6),
                "unigram_weight": effective_weight,
                "shared_tokens": shared,
                "reference_token_count": len(ref_tokens),
                "output_token_count": len(out_tokens),
            },
        )


GRADER = SemanticSimilarityGrader()
