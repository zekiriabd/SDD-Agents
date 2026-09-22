# Embedding: Voyage AI

Stack ID: embedding-voyage
Status: Stable
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *

---

## 1. Rôle et périmètre

Modèle d'embedding du MVP (combo C1), et modèle de *rerank* optionnel.

Retenu pour trois raisons opérationnelles, pas pour un classement :

1. **Modèles de domaine** (`voyage-law-2`, `voyage-code-3`, `voyage-finance-2`) —
   sur un corpus juridique ou technique, un modèle de domaine bat régulièrement
   un généraliste plus gros ;
2. **Dimensions et quantification configurables** — 256 à 2048, `int8` et
   `binary` : le coût de stockage et de recherche se pilote sans changer de
   modèle ;
3. **Reranker de la même famille** — une seule intégration pour les deux étages.

> **Le choix du modèle d'embedding est rarement le levier principal.** Le
> découpage l'est. Passer d'un modèle à un autre gagne quelques points de
> recall ; passer d'un découpage arbitraire à un découpage structurel en gagne
> beaucoup plus. Ne pas inverser l'ordre des travaux.

---

## 2. Identité

| | |
|---|---|
| Variable d'environnement | `EMBEDDING_API_KEY` |
| Modèle par défaut | `voyage-3-large` |
| Dimensions | 1024 (défaut) · 256 / 512 / 2048 disponibles |
| Quantification | `float32` (défaut) · `int8` · `binary` |
| Fenêtre d'entrée | ~32 000 tokens |
| Reranker | `rerank-2.5` |

Alternatives à déclarer dans `RuntimeModels.EmbeddingModel` :
`voyage-3-lite` (coût réduit), `voyage-code-3`, `voyage-law-2`,
`voyage-finance-2`.

---

## 3. `input_type` — la subtilité qui change les résultats

L'API distingue `document` (à l'indexation) et `query` (à la recherche). Les deux
produisent des vecteurs dans le même espace, mais optimisés pour leur rôle.

**Utiliser le même `input_type` des deux côtés dégrade le recall de plusieurs
points.** C'est une erreur silencieuse : rien ne la signale, les résultats sont
simplement un peu moins bons. Le code généré doit la rendre impossible — un
paramètre obligatoire, pas un défaut.

---

## 4. Dimensions et quantification — ce qu'on achète

| Configuration | Stockage relatif | Recall relatif | Employer quand |
|---|---:|---:|---|
| 1024 `float32` | 100 % | référence | défaut |
| 1024 `int8` | 25 % | ~-1 pt | corpus > ~1 M chunks |
| 512 `float32` | 50 % | ~-2 pts | contrainte de coût |
| 256 `binary` | ~3 % | ~-8 pts | filtrage grossier avant rerank uniquement |

**Règle** : ne descendre en dimensions ou en quantification qu'après avoir
**mesuré** le recall sur le golden set. Le gain de stockage est certain ; la
perte de qualité doit l'être aussi.

`binary` n'est jamais employé seul : il sert de premier étage devant un
reranker.

---

## 5. Le reranking

Un reranker (`rerank-2.5`) réordonne les `topK` résultats en jugeant chaque paire
(requête, document) — beaucoup plus précis qu'une similarité de vecteurs.

```yaml
RetrievalTopK: 25        # on récupère large
RerankEnabled: true
RerankTopN: 5            # on ne sert que le meilleur
```

**Quand il vaut son coût** : `recall@25` est bon mais `nDCG@5` est médiocre —
autrement dit, le bon document est trouvé mais mal classé. C'est exactement le
cas que le reranking corrige, et la mesure le dit sans ambiguïté.

**Quand il ne sert à rien** : `recall@25` est déjà mauvais. Reclasser des
mauvais résultats produit de meilleurs mauvais résultats. Réparer le retrieval
d'abord.

Coût : un appel réseau supplémentaire, ~100-300 ms. Il entre dans
`LatencyP95TargetMs`.

---

## 6. Opérations

- **Débit** — la limite est en requêtes ET en tokens par minute. Une ingestion
  de masse doit gérer le backoff ; c'est le seul endroit où un retry agressif est
  légitime (opération idempotente, sans effet de bord).
- **Lots** — embedder par paquets de ~100 textes. Diviser par dix le nombre
  d'appels change l'ordre de grandeur du temps d'ingestion.
- **Déterminisme** — l'API n'en garantit aucun entre versions de modèle.
  D'où `indexHash` dans le tuple d'épinglage : **changer de modèle d'embedding
  périme tous les résultats de retrieval** (P10). Ce n'est pas une précaution
  théorique — un réindexage silencieux est une des causes les plus difficiles à
  diagnostiquer d'une baisse de qualité.
- **Réindexage** — changer de modèle ou de dimensions impose de tout réindexer.
  Prévoir le double stockage pendant la bascule, ou une fenêtre de coupure.

---

## 7. Confidentialité

Les textes embeddés **sortent de votre infrastructure**. Sur un corpus contenant
des PII ou des données réglementées, vérifier la politique de rétention du
fournisseur (déclarée dans `.sdda/providers/`) avant d'ingérer quoi que ce soit.

Alternative locale : `.sdda/stacks/embedding/bge-local.md` — qualité inférieure
sur le multilingue, mais rien ne sort.

Ce point n'est pas une formalité : une ingestion est difficile à défaire, et on
ne peut pas retirer d'un service tiers ce qu'on lui a déjà envoyé.

---

## 8. Mapping vers l'IR

```jsonc
"retrievers": [{
  "id": "1-contracts-index",
  "indexHash": "sha256:…",
  "binding": {
    "embeddingModel": "voyage-3-large",
    "rerank": { "model": "rerank-2.5", "topN": 5 }
  }
}]
```
