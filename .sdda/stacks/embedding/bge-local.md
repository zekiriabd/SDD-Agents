# Embedding: BGE (local)

Stack ID: embedding-bge-local
Status: Stable
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *

---

## 1. Rôle et périmètre

Modèles d'embedding de la famille BGE exécutés **sur votre infrastructure**.
Rien ne sort.

C'est le choix à faire quand la confidentialité prime sur la qualité — et c'est
un arbitrage réel, pas une équivalence.

---

## 2. Quand le choisir

- corpus contenant des PII, des données de santé, des secrets industriels ;
- contrainte réglementaire ou contractuelle interdisant la sortie des données ;
- volume d'ingestion où le coût par million de tokens d'une API devient
  significatif ;
- besoin d'un déterminisme total : un modèle figé sur votre disque ne change pas
  sous vos pieds.

Ce dernier point est sous-estimé. Un fournisseur qui met à jour un modèle
d'embedding modifie silencieusement la qualité de votre retrieval. En local,
vous décidez du moment.

## 3. Ce que ça coûte

| Face à une API managée | |
|---|---|
| Qualité multilingue | **inférieure**, surtout hors anglais et chinois |
| Longueur de contexte | 512 tokens (`bge-m3` : 8192) — contrainte réelle sur le découpage |
| Exploitation | un GPU à dimensionner, à surveiller, à mettre à jour |
| Débit d'ingestion | dépend de votre matériel, pas d'un quota |
| Confidentialité | **rien ne sort** |
| Coût marginal | proche de zéro après l'investissement matériel |

> **L'arbitrage honnête** : sur un corpus français, `bge-m3` reste correct mais
> un modèle managé récent fait mieux. Si la confidentialité l'impose, c'est le
> bon choix ; si elle ne l'impose pas, c'est un choix qu'il faut justifier par
> le volume, pas par principe.

---

## 4. Identité

| | |
|---|---|
| Modèle recommandé | `BAAI/bge-m3` (multilingue, 8192 tokens, dense + sparse + colbert) |
| Alternatives | `bge-large-en-v1.5` (anglais), `bge-large-zh-v1.5` (chinois) |
| Dimensions | 1024 |
| Exécution | `sentence-transformers`, `FlagEmbedding`, ou serveur `TEI` |
| Matériel | GPU 8 Go suffisant pour `bge-m3` en inférence |
| Variable d'environnement | aucune — pas de clé |

---

## 5. Le piège du préfixe d'instruction

Les modèles BGE **anglais et chinois** attendent un préfixe sur la **requête**,
jamais sur les documents :

```
requête   : "Represent this sentence for searching relevant passages: {query}"
document  : "{text}"   (aucun préfixe)
```

Oublier ce préfixe coûte plusieurs points de recall. L'appliquer aux documents
aussi en coûte également. C'est une erreur silencieuse — rien ne la signale, les
résultats sont simplement moins bons — donc le code généré doit la rendre
impossible plutôt que la documenter.

`bge-m3` n'a **pas** besoin de préfixe. Confondre les deux familles est l'erreur
la plus fréquente sur cette stack.

---

## 6. `bge-m3` : trois représentations pour le prix d'une

Le modèle produit simultanément :

- **dense** — le vecteur classique ;
- **sparse** (lexical weights) — l'équivalent d'un BM25 appris ;
- **ColBERT** (multi-vecteurs) — appariement fin, coûteux en stockage.

**Conséquence utile** : `bge-m3` fournit le côté lexical d'un retrieval hybride
sans BM25 séparé. Sur une stack où l'on ne veut pas maintenir un index
Elasticsearch à côté de pgvector, c'est un argument solide.

Le mode ColBERT n'est employé qu'en reranking d'un petit `topN` : son coût de
stockage est d'un ordre de grandeur supérieur.

---

## 7. Opérations

- **Chargement** — quelques secondes et plusieurs Go de VRAM. Le modèle se
  charge **une fois** au démarrage du service d'ingestion, jamais par requête.
- **Lots** — la taille de lot optimale dépend de la VRAM ; commencer à 32 et
  mesurer. C'est le levier principal du débit d'ingestion.
- **Version épinglée** — épingler le hash de révision du modèle, pas seulement
  son nom. Un modèle mis à jour change les embeddings, donc périme
  `indexHash` et tous les résultats de retrieval (P10).
- **CPU** — possible, ~10 à 30× plus lent. Acceptable pour les requêtes, pas
  pour l'ingestion d'un corpus.

---

## 8. Mapping vers l'IR

```jsonc
"retrievers": [{
  "id": "1-contracts-index",
  "pattern": "hybrid",
  "indexHash": "sha256:…",
  "binding": {
    "embeddingModel": "BAAI/bge-m3@<revision>",
    "hybridWeights": { "vector": 0.6, "lexical": 0.4 }
  }
}]
```

En mode `bge-m3` dense+sparse, la partie `lexical` de l'hybride est fournie par
le modèle lui-même — le `retrieval-contract` doit le dire explicitement, sinon
`dev-retrieval` câblera un BM25 inutile.
