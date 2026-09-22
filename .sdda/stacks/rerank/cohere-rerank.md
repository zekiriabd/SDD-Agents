# Stack: cohere-rerank (rerank)

Stack ID: rerank-cohere-rerank
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *
Scope: réordonnancement **par API** des résultats fusionnés — modèle cross-encoder hébergé, appelé en HTTP sur la paire (requête, document). Neutre vis-à-vis du langage : l'appel passe par le client HTTP du framework actif (`httpx` côté Python, `IHttpClientFactory` côté .NET), comme les embeddings. Pas de `.libs.json` propre.

---

## 1. Rôle et périmètre

Un reranker est un **cross-encoder** : il lit la requête et le document
*ensemble* et rend un score de pertinence. C'est structurellement plus précis
qu'une similarité entre deux vecteurs calculés séparément, et structurellement
plus cher — d'où le schéma en deux étages.

```
retrieve(topK=25)  ──►  rrf_fuse  ──►  rerank(query, docs) ──► topN=5 ──► modèle
   rapide, large            │              lent, précis
   (vecteur + BM25)         └─ cf. rag/hybrid.md §3.1
```

Le reranker s'applique **au résultat fusionné**, jamais à chaque jambe :
il juge une liste de candidats, et fusionner après reclassement détruirait
l'ordre qu'il vient d'établir (cf. `rag/hybrid.md` §6, piège 9).

Cette fiche est le choix par défaut dès qu'un reranker est justifié et que le
corpus peut sortir de l'infrastructure. Sinon → `bge-reranker-local.md`.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `rerank-cohere-rerank` |
| **Variable d'environnement** | `RERANK_API_KEY` |
| **Modèle par défaut** | `rerank-v3.5` |
| **Longueur par document** | ~4 096 tokens — au-delà, le document est tronqué **en silence** |
| **Candidats par appel** | ≤ 1 000 ; en pratique `RetrievalTopK` (≤ 50) |
| **Latence** | ~100-300 ms pour 25 candidats |
| **Paramètres STACK.md** | `RerankEnabled: true`, `RerankTopN`, `RetrievalTopK` |
| **Dépendances** | aucune au-delà du client HTTP du framework actif |

> **Alternative de la même famille** : `rerank-2.5` de Voyage AI, à retenir si
> `embedding/voyage.md` est déjà actif — une seule clé, un seul fournisseur, une
> seule politique de rétention à vérifier. Le contrat de cette fiche est
> identique ; seuls le nom du modèle et l'URL changent.

---

## 3. Contrat d'interface

Le reranker est une **fonction pure** du point de vue du reste du système : elle
ne touche ni l'index, ni l'état de l'agent.

| Entrée | Sortie |
|---|---|
| `query: str` | liste de `(index, relevance_score)` triée décroissante |
| `documents: list[str]` — le texte des `RetrievalTopK` candidats fusionnés | tronquée à `RerankTopN` |
| `top_n: int` = `RerankTopN` | |

Trois règles que le code généré doit rendre impossibles à enfreindre :

1. **L'ordre d'entrée est l'ordre de la fusion.** Le reranker rend des *indices*
   dans la liste qu'on lui a passée ; réordonner cette liste entre l'appel et
   l'exploitation du résultat mélange les documents. C'est un bug silencieux :
   les citations pointent vers le mauvais chunk et rien ne le signale.
2. **Le document envoyé au reranker est celui servi au modèle.** Si
   `ParentChildEnabled: true`, on reclasse le *chunk enfant* (celui qui a été
   retrouvé) et on sert le *parent* — envoyer le parent au reranker dilue le
   signal que la récupération vient d'isoler.
3. **Un échec de rerank n'est pas un échec de retrieval.** Timeout ou 5xx →
   servir l'ordre de fusion, tracer un span en erreur, continuer. Le contraire
   transforme une dégradation de qualité en panne totale.

---

## 4. Mapping vers l'IR

```jsonc
"retrievers": [{
  "id": "1-contracts-index",
  "topK": 25,                        // large en entrée
  "rerank": {
    "provider": "cohere",
    "model": "rerank-v3.5",
    "topN": 5,                       // étroit en sortie
    "onError": "fallback-to-fusion"  // jamais "fail"
  },
  "indexHash": "sha256:…"
}]
```

**Le modèle de rerank entre dans le tuple d'épinglage P10** au même titre que le
modèle d'embedding : changer `rerank-v3.5` pour une autre version périme les
baselines de retrieval. Vérifié par `check_baseline_freshness.py` →
`[EVAL_BASELINE_STALE]`.

---

## 5. Observabilité

Un span `rerank` distinct de `retrieve` — sans quoi la latence du reranker est
imputée au retrieval et le diagnostic part dans la mauvaise direction.

| Attribut | Pourquoi |
|---|---|
| `rerank.candidates` | `RetrievalTopK` effectif |
| `rerank.top_n` | `RerankTopN` |
| `rerank.duration_ms` | entre dans `LatencyP95TargetMs` |
| `rerank.fallback` | `true` si l'ordre de fusion a été servi — **à surveiller** |
| `rerank.score_range` | min/max des scores rendus |

`rerank.fallback` qui passe à `true` régulièrement est le symptôme le plus utile
de cette fiche : la qualité baisse sans qu'aucun test ne rougisse.

---

## 6. Confidentialité

Les documents candidats **sortent de votre infrastructure à chaque requête** —
et pas seulement à l'ingestion comme pour les embeddings. Sur un corpus à PII ou
réglementé, c'est une exposition continue, proportionnelle au trafic.

`TracePIIPolicy` ne protège pas ici : il gouverne les traces, pas les appels
sortants. Si le corpus ne peut pas sortir → `bge-reranker-local.md`.

Vérifier la politique de rétention du fournisseur dans `.sdda/providers/` avant
d'activer cette fiche.

---

## 7. Commande de smoke

```bash
python -m {AppName}.retrieval.{index_slug} smoke --query "facture INV-2024-0093 montant" --rerank
#   -> exit 0 ; exit 5 si l'ordre rendu n'est pas une permutation des candidats
#      envoyés ([RETRIEVAL_RERANK_MISMATCH])

python .sdda/python/sdda_scripts/run_retrieval_eval.py --index {index_slug} --ablation rerank --json
#   -> nDCG@5 avec et sans reranker sur le golden set. C'est CE chiffre qui
#      justifie la dépense, pas la fiche.
```

---

## 8. Pièges connus

1. **Reclasser avant de fusionner.** Le reranker s'applique une fois, sur la
   liste fusionnée. Appliqué à chaque jambe, il coûte deux appels et son ordre
   est ensuite écrasé par la RRF.
2. **`RerankTopN` ≥ `RetrievalTopK`.** Le reranker ne filtre alors plus rien :
   on paie l'appel pour réordonner une liste qu'on sert entièrement. Le rapport
   de gain est nul et la configuration a l'air active.
3. **Documents tronqués en silence.** Au-delà de ~4 096 tokens, la fin du
   document n'est pas jugée. Avec `ChunkSize: 800` le cas ne se pose pas ; avec
   `ParentChildEnabled: true` et des parents longs, si.
4. **Compter sur le score absolu.** Les scores ne sont pas calibrés entre
   modèles ni entre versions. Un seuil `score > 0.7` codé en dur se périme au
   premier changement de modèle. Filtrer par rang (`topN`), pas par score.
5. **Oublier le reranker dans l'estimation de budget.** `estimate_budget.py` le
   compte comme un appel réseau par requête : sur un agent à 5 hops dont 3 font
   du retrieval, c'est 3 appels de rerank par tour.
6. **Traiter le fallback comme un détail.** Sans le span `rerank.fallback`, une
   clé d'API expirée dégrade la qualité pendant des semaines sans une seule
   erreur visible.
