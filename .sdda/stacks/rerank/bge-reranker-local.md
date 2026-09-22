# Stack: bge-reranker-local (rerank)

Stack ID: rerank-bge-reranker-local
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: réordonnancement **local** par cross-encoder BGE — le modèle tourne dans le processus de l'application, rien ne sort de l'infrastructure. Couplé à Python : `sentence-transformers` et `torch` portent le runtime d'inférence. Catalogue de versions : `bge-reranker-local.libs.json`.

---

## 1. Rôle et périmètre

Même contrat que `cohere-rerank.md` — un cross-encoder qui réordonne les
candidats fusionnés — mais **exécuté chez vous**. C'est la réponse à une
contrainte de confidentialité, pas à une contrainte de qualité : sur un corpus
ordinaire, un reranker hébergé récent reste devant.

Ce qu'on échange :

| | API (`cohere-rerank`) | Local (cette fiche) |
|---|---|---|
| Corpus sortant | oui, **à chaque requête** | non |
| Latence (25 candidats) | ~100-300 ms réseau | ~50-400 ms CPU · ~15-40 ms GPU |
| Coût marginal | par appel | nul, mais le serveur est dimensionné pour |
| Démarrage | immédiat | chargement du modèle (~1-4 s) |
| Qualité | référence | proche avec `bge-reranker-v2-m3` |
| Dépendances | aucune | `torch` — ~800 Mo à ~2,5 Go d'image |

Le dernier point décide plus souvent que les autres : `torch` change l'ordre de
grandeur de l'image de conteneur et du temps de démarrage à froid. Sur une
surface `serving/fastapi-sse.md` autoscalée, c'est structurant ; sur
`serving/batch.md`, c'est sans importance.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `rerank-bge-reranker-local` |
| **Modèle par défaut** | `BAAI/bge-reranker-v2-m3` — multilingue, 568 M paramètres |
| **Alternative légère** | `BAAI/bge-reranker-base` — 278 M, anglais/chinois, ~2× plus rapide |
| **Longueur par paire** | 8 192 tokens (`v2-m3`) · 512 (`base`) |
| **Runtime** | CPU par défaut ; CUDA si disponible |
| **Variable d'environnement** | aucune — pas de clé, pas d'appel sortant |
| **Cache du modèle** | `HF_HOME` (défaut `~/.cache/huggingface`) |
| **Paramètres STACK.md** | `RerankEnabled: true`, `RerankTopN`, `RetrievalTopK` |

---

## 3. Contrat d'interface

Identique à `cohere-rerank.md` §3 — mêmes trois règles (ordre d'entrée préservé,
document reclassé = document servi, échec non bloquant). Trois contraintes
s'ajoutent, propres à l'inférence locale :

1. **Le modèle est chargé une fois, au démarrage.** Un chargement par requête
   ajoute des secondes à chaque appel. Il vit dans le conteneur d'injection de
   dépendances, aux côtés du pool de connexions.
2. **L'inférence est synchrone et tient le GIL.** Sur une surface async
   (`serving/fastapi-sse.md`), l'appel passe par `asyncio.to_thread` ou un
   `ProcessPoolExecutor` — sinon un rerank de 300 ms gèle *toutes* les requêtes
   concurrentes, y compris les flux SSE en cours.
3. **La concurrence est bornée explicitement.** Deux rerankers simultanés sur un
   CPU à 4 cœurs sont plus lents que deux séquentiels. Un sémaphore dimensionné
   sur les cœurs disponibles, pas sur le trafic.

```python
# src/{AppName}/retrieval/rerank.py
from __future__ import annotations

import asyncio

from sentence_transformers import CrossEncoder

_SEMAPHORE = asyncio.Semaphore(2)  # cf. §3.3 — dimensionné sur les cœurs, pas le trafic


class LocalReranker:
    """Cross-encoder BGE chargé une fois (§3.1), appelé hors boucle d'événements (§3.2)."""

    def __init__(self, model_name: str, max_length: int) -> None:
        self._model = CrossEncoder(model_name, max_length=max_length)

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        # `documents` est l'ordre de la fusion : on rend des INDICES dedans,
        # jamais des documents réordonnés (cf. cohere-rerank.md §3.1).
        pairs = [(query, doc) for doc in documents]
        async with _SEMAPHORE:
            scores = await asyncio.to_thread(self._model.predict, pairs)
        ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
        return [(index, float(score)) for index, score in ranked[:top_n]]
```

---

## 4. Mapping vers l'IR

```jsonc
"retrievers": [{
  "id": "1-contracts-index",
  "topK": 25,
  "rerank": {
    "provider": "local",
    "model": "BAAI/bge-reranker-v2-m3",
    "topN": 5,
    "onError": "fallback-to-fusion"
  },
  "indexHash": "sha256:…"
}]
```

Le nom du modèle entre dans le tuple d'épinglage P10 (cf. `cohere-rerank.md` §4).
Un modèle local n'y échappe pas : `bge-reranker-base` et `bge-reranker-v2-m3` ne
rendent pas le même ordre, donc pas les mêmes baselines.

---

## 5. Opérations

- **Téléchargement au premier démarrage** — ~1,1 Go pour `v2-m3`. En conteneur,
  le modèle est intégré à l'image au `build`, jamais tiré au `run` : sinon le
  premier démarrage en production dépend d'un accès réseau sortant que
  `SourceEgressAllowlist` interdit probablement.
- **Déterminisme** — l'inférence est déterministe à modèle et précision
  constants. Passer en `fp16` sur GPU change les scores à la marge et peut
  changer l'ordre sur des candidats proches : c'est un changement de tuple P10.
- **Dimensionnement** — mesurer avec `RetrievalTopK` réel. Le coût est linéaire
  en nombre de candidats **et** en longueur : 25 chunks de 800 tokens n'est pas
  le même problème que 25 parents de 4 000.
- **`torch` n'est payé que par les projets qui l'activent** — ce catalogue n'est
  résolu que si cette fiche est la fiche active de `## Active Reranker`. C'est
  la raison d'être de la catégorie `rerank/` : un projet en `rerank/none.md` ou
  `rerank/cohere-rerank.md` ne voit jamais ces ~2 Go. Le pin est explicite et non
  laissé en transitif, parce qu'il décide la taille de l'image et le temps de
  démarrage à froid.

---

## 6. Commande de smoke

```bash
uv run python -m {AppName}.retrieval.{index_slug} smoke --query "facture INV-2024-0093 montant" --rerank
#   -> exit 0 ; exit 5 si l'ordre rendu n'est pas une permutation des candidats
#      envoyés ([RETRIEVAL_RERANK_MISMATCH])

uv run python -m sdda_scripts.run_retrieval_eval --index {index_slug} --ablation rerank --json
#   -> nDCG@5 avec et sans reranker, et latence p95 des deux. Sur cette fiche,
#      la latence compte autant que le gain : elle est payée sur votre CPU.
```

---

## 7. Pièges connus

1. **Charger le modèle dans le handler.** Le cas le plus courant et le plus
   coûteux : quelques secondes ajoutées à chaque requête, invisibles en test
   unitaire où le modèle est mocké.
2. **Bloquer la boucle d'événements.** `model.predict()` appelé directement dans
   une coroutine gèle tout le processus pendant l'inférence. Le symptôme — des
   flux SSE qui saccadent — ne ressemble pas à sa cause.
3. **`torch` en dépendance `core`.** L'image passe de ~200 Mo à ~2,5 Go pour
   tous les projets, y compris ceux sans reranker.
4. **Tirer le modèle au démarrage en production.** Fonctionne en développement,
   échoue derrière une allowlist d'egress — et échoue au *redémarrage*, donc au
   pire moment.
5. **Comparer les scores à ceux de l'API.** Les échelles n'ont rien de commun.
   Toute logique de seuil absolu se casse à la bascule ; filtrer par rang.
6. **Oublier le sémaphore.** Sous charge, la latence p95 s'effondre de façon non
   linéaire et le profil accuse le retrieval.
