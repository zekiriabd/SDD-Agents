# Stack: hybrid (rag)

Stack ID: rag-hybrid
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: pattern de retrieval **hybride** — recherche lexicale (BM25 ou FTS) + recherche vectorielle, fusion par Reciprocal Rank Fusion. Implémente l'entité **RETRIEVER** du domain model. Neutre vis-à-vis du store ; la fiche donne la variante `pgvector` en exemple (cf. `vectorstore/pgvector.md`). Pas de `.libs.json` : les dépendances sont portées par le store et le framework actifs.

---

## 1. Rôle et périmètre

`hybrid` est le **pattern RAG recommandé par défaut** de SDD_Agents dès qu'il y a
du RAG (RAG-PATTERNS §2). Pas parce qu'il est sophistiqué — il ne l'est pas —
mais parce que les deux jambes échouent sur des cas **complémentaires** :

| La jambe… | trouve | rate |
|---|---|---|
| **vectorielle** | paraphrases, synonymes, formulations éloignées | identifiants exacts (`INV-2024-0093`), codes d'erreur, noms propres rares, numéros d'article |
| **lexicale** | correspondances exactes, termes rares, acronymes | reformulations, questions sans mot commun avec la réponse |

Fusionner les deux coûte une requête SQL de plus (ou deux requêtes
concurrentes) et **aucun token**. Sur les golden sets contenant des références
métier, le gain de recall@k est typiquement le plus grand par euro dépensé
après le choix du chunking.

Cette fiche couvre : les deux jambes, la fusion RRF (pondérée ou non), les
paramètres et leurs défauts, **comment mesurer** (c'est un pattern de la
RETRIEVAL GATE, il se juge sans agent), la structure générée, les pièges.
**Hors périmètre** : le chunking (RAG-PATTERNS §4, décidé dans le
`retrieval-contract` par mesure comparative), le reranking (`RerankEnabled`,
optionnel, appliqué **après** la fusion), la génération et ses métriques
(`groundedness`, `answer_relevance` → `eval/*.md`).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `rag-hybrid` |
| **Famille** | RAG · pattern de requête (RETRIEVER) |
| **Dépend de** | `vectorstore/*.md` (la jambe vectorielle et, pour pgvector, la jambe lexicale) · `embedding/*.md` · `lang/python.md` |
| **Compatible** | tous les stores ; la jambe lexicale est native sur `pgvector` (FTS), `elasticsearch`, `azure-ai-search`, `qdrant` (sparse), `weaviate` ; **in-process** (`rank_bm25`) pour `chroma`, `faiss`, `pinecone` — corpus < ~100 k chunks |
| **Paramètres STACK.md** | `HybridEnabled: true`, `HybridWeights: { vector: 0.6, lexical: 0.4 }`, `RetrievalTopK: 8`, `RerankEnabled`, `RerankTopN` |
| **Paramètres du retrieval-contract** | `candidates_per_leg` (40), `rrf_k` (60), `LexicalLanguage` (`french`), `LexicalEngine` (`pg-fts` \| `pg-bm25` \| `rank-bm25` \| `external`) |

### 2.1 Une franchise nécessaire sur « BM25 »

La recherche plein texte native de PostgreSQL (`ts_rank`, `ts_rank_cd`)
**n'est pas BM25** : pas de saturation de fréquence de terme, pas de
normalisation par longueur de document paramétrable, et `ts_rank` ignore l'IDF.
Pour des corpus documentaires homogènes, la différence de recall est souvent
faible ; pour des corpus hétérogènes (chunks très courts + très longs), BM25
est nettement meilleur. Options, par ordre de préférence :

| `LexicalEngine` | Quoi | Quand |
|---|---|---|
| `pg-fts` | `tsvector` + GIN + `ts_rank_cd` | **défaut** — zéro composant supplémentaire ; mesurer d'abord |
| `pg-bm25` | extension BM25 pour PostgreSQL (`pg_search` / ParadeDB) | si la mesure §5 montre `pg-fts` en retrait sur les requêtes lexicales |
| `rank-bm25` | `rank_bm25` in-process, index en mémoire reconstruit à l'ingestion | store sans FTS, corpus < 100 k chunks, un seul processus |
| `external` | Elasticsearch / OpenSearch | déjà en place dans l'organisation |

Le choix est une **mesure** consignée dans le `retrieval-contract`, pas une
préférence.

---

## 3. Mapping des concepts SDD_Agents → idiomes du pattern

| Concept | Idiome hybride | Notes |
|---|---|---|
| **RETRIEVER** `retrieve(query, top_k, tenant)` | `vector_leg ∥ lexical_leg → rrf_fuse → [rerank] → top_k` | les deux jambes s'exécutent **concurremment** (`asyncio.gather`) |
| `hybridWeights` | RRF pondéré : `score(d) = Σ_leg w_leg / (rrf_k + rank_leg(d))` | `w_vector + w_lexical = 1` ; poids non pondéré = `0.5/0.5` |
| `candidates_per_leg` | `LIMIT` de chaque jambe (défaut 40 = 5 × `top_k`) | trop bas → la fusion n'a rien à fusionner ; trop haut → latence, et `ef_search` doit suivre |
| `topK` | taille du résultat fusionné servi au modèle (8) | après reranking éventuel (`RerankTopN` = 4) |
| `citationMode: required` | la fusion **conserve** `chunk_id`, `doc_id`, `citation` de chaque candidat ; un chunk vu par les deux jambes garde une seule entrée | `citation_resolve_rate` se mesure sur le résultat fusionné |
| **Trust** | `RetrievedChunk.content: Untrusted` | P8 |
| **Diagnostic** (RAG-PATTERNS §5) | chaque `RetrievedChunk` porte `provenance: {"vector_rank": 3, "lexical_rank": None}` | permet de savoir **quelle jambe** a remonté le document pertinent — c'est la donnée qui règle les poids |
| **TRACE SPAN** `retrieve` | attributs `sdda.retrieval.legs = ["vector","lexical"]`, `sdda.retrieval.rrf_k`, `sdda.retrieval.weights`, `sdda.retrieval.candidates_per_leg`, `sdda.retrieval.result_ids`, `sdda.retrieval.result_scores` | cf. `observability/otel-genai.md` |
| **indexHash** | inclut `LexicalEngine`, `LexicalLanguage`, `rrf_k`, `weights` | changer les poids périme les baselines L3 — c'est voulu |

### 3.1 La fusion — fonction pure, testée en L1

```python
# src/{AppName}/retrieval/{index_slug}/fusion.py
from __future__ import annotations

from collections.abc import Mapping, Sequence


def rrf_fuse(
    legs: Mapping[str, Sequence[str]],        # {"vector": [chunk_id, ...] ordonnés, "lexical": [...]}
    *,
    weights: Mapping[str, float],             # {"vector": 0.6, "lexical": 0.4}
    k: int = 60,
    top_k: int,
) -> list[tuple[str, float, dict[str, int | None]]]:
    """Reciprocal Rank Fusion pondérée. Retourne [(chunk_id, score, {leg: rank|None})] trié décroissant.

    Déterministe : à égalité de score, l'ordre est celui de chunk_id (stable entre runs).
    """
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError("[RETRIEVAL_WEIGHTS_INVALID] weights must sum to 1.0")
    scores: dict[str, float] = {}
    provenance: dict[str, dict[str, int | None]] = {}
    for leg, ranked in legs.items():
        w = weights[leg]
        for rank, cid in enumerate(ranked, start=1):
            scores[cid] = scores.get(cid, 0.0) + w / (k + rank)
            provenance.setdefault(cid, {name: None for name in legs})[leg] = rank
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(cid, s, provenance[cid]) for cid, s in ordered[:top_k]]
```

Pourquoi RRF et pas une combinaison linéaire de scores : les scores bruts
(cosinus ∈ [0,1], `ts_rank_cd` non borné, BM25 non borné) **ne sont pas
comparables** ; les normaliser par min-max dépend de la requête et rend le
résultat instable. RRF n'utilise que les rangs — robuste, sans réglage, et un
document présent dans les deux jambes remonte naturellement.

### 3.2 Variante pgvector en une requête

```sql
-- src/{AppName}/retrieval/{index_slug}/sql/hybrid_rrf.sql
WITH q AS (
  SELECT %(q_vec)s::vector AS v,
         websearch_to_tsquery(%(lang)s::regconfig, rag.immutable_unaccent(%(q_text)s)) AS tsq
),
vec AS (
  SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> q.v) AS rnk
  FROM   rag.chunks c, q
  WHERE  c.tenant_id = current_setting('app.tenant_id')::uuid
    AND  c.pii_scan_status <> 'flagged'
  ORDER  BY c.embedding <=> q.v
  LIMIT  %(n)s
),
lex AS (
  SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.content_tsv, q.tsq) DESC) AS rnk
  FROM   rag.chunks c, q
  WHERE  c.tenant_id = current_setting('app.tenant_id')::uuid
    AND  c.pii_scan_status <> 'flagged'
    AND  c.content_tsv @@ q.tsq
  ORDER  BY ts_rank_cd(c.content_tsv, q.tsq) DESC
  LIMIT  %(n)s
),
fused AS (
  SELECT COALESCE(vec.id, lex.id) AS id,
         COALESCE(%(w_vec)s / (%(rrf_k)s + vec.rnk), 0)
       + COALESCE(%(w_lex)s / (%(rrf_k)s + lex.rnk), 0) AS score,
         vec.rnk AS vector_rank, lex.rnk AS lexical_rank
  FROM   vec FULL OUTER JOIN lex ON vec.id = lex.id
)
SELECT c.id, c.document_id, c.chunk_index, c.heading_path, c.char_start, c.char_end, c.content,
       f.score, f.vector_rank, f.lexical_rank
FROM   fused f JOIN rag.chunks c ON c.id = f.id
ORDER  BY f.score DESC, c.id
LIMIT  %(top_k)s;
```

Une requête, une transaction, un `SET LOCAL app.tenant_id`, un `SET LOCAL
hnsw.ef_search >= n`. La fusion Python de §3.1 reste la référence testée ; la
version SQL est vérifiée **contre elle** en L6 sur un jeu de données fixe.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/src/{AppName}/retrieval/{index_slug}/
├── __init__.py                 # retrieve(query, *, top_k, tenant) -> list[RetrievedChunk]
├── retriever.py                # orchestre : embed(query) ∥ lexical → fuse → [rerank] → top_k ; span retrieve
├── fusion.py                   # rrf_fuse — pure, L1
├── legs.py                     # vector_leg(...), lexical_leg(...) — délèguent au store
├── sql/hybrid_rrf.sql          # variante une-requête (pgvector)
├── query_prep.py               # normalisation de la requête lexicale : unaccent, préservation des identifiants (§7.3)
└── models.py                   # RetrievedChunk(content: Untrusted, score, citation, provenance)

workspace/src/{AppName}/tests/retrieval/
├── test_fusion.py              # L1 : RRF sur cas connus, égalités stables, poids invalides, doc dans les 2 jambes remonte
└── test_query_prep.py          # L1 : 'INV-2024-0093' survit à la préparation lexicale

workspace/evals/suites/
└── retrieval-{index_slug}.yaml # L3 : golden set, recall@k / nDCG par jambe et fusionné (cf. §5)
workspace/evals/reports/
└── retrieval-{index_slug}-{run-id}.json
```

---

## 5. Conventions imposées — et comment mesurer

### 5.1 Règles

1. **Les deux jambes tournent en parallèle** (`asyncio.gather`) ou en une seule
   requête SQL. Jamais séquentiellement : la latence P95 est un budget (P6).
2. **`candidates_per_leg ≥ 3 × top_k`**, défaut 5×. Et `hnsw.ef_search ≥
   candidates_per_leg` sur pgvector.
3. **RRF sur les rangs, jamais sur les scores bruts.** Toute « normalisation
   de score » est refusée sans mesure comparative dans le contrat.
4. **Déduplication par `chunk_id`** dans la fusion ; en `parent-child`, la
   déduplication se fait sur le **parent** servi, après fusion.
5. **`provenance` conservée** jusqu'au span et au rapport L3 : c'est la donnée
   de diagnostic.
6. **Poids, `rrf_k`, `candidates_per_leg`, `LexicalEngine`, `LexicalLanguage`**
   vivent dans le `retrieval-contract` et entrent dans `indexHash`.
7. **La requête lexicale est préparée** (`query_prep.py`) : suppression des
   accents cohérente avec l'ingestion, **préservation des identifiants**
   (tokens contenant chiffres/tirets passés en `phrase` ou en jambe `pg_trgm`
   dédiée), `websearch_to_tsquery` plutôt que `plainto_tsquery` (gère guillemets
   et `OR`).

### 5.2 Mesurer — protocole de la RETRIEVAL GATE (G4), 0 token

Sur `workspace/datasets/golden/retrieval-{index}.jsonl`
(`{query, relevant_doc_ids[], relevant_chunk_ids[]?, tags[]}`, ≥ `GoldenSetMinItems`) :

| Étape | Mesure | Pourquoi |
|---|---|---|
| 1. **Ablation** | `recall@k` et `nDCG@k` pour `vector` seul, `lexical` seul, `hybrid` | si `hybrid` ≤ max(jambes), la fusion est mal réglée ou une jambe est cassée |
| 2. **Par tag de requête** | mêmes métriques sur `tags ∈ {identifier, paraphrase, factual, multi-hop}` | une accuracy globale de 0.85 peut cacher 0.40 sur `identifier` — c'est là que le lexical sert |
| 3. **Grille de poids** | `w_vector ∈ {0.3, 0.4, 0.5, 0.6, 0.7}`, `rrf_k ∈ {20, 60, 100}` | choisir par `recall@k` sur **golden** ; le verdict final se rend sur **holdout** (G8), jamais l'inverse |
| 4. **Profondeur** | `candidates_per_leg ∈ {20, 40, 80}` vs latence P95 | le point où le recall plafonne fixe la valeur |
| 5. **Vérité au niveau document** | `recall@k` avec `relevant_doc_ids` | isole le chunking : le document est là mais coupé au mauvais endroit (RAG-PATTERNS §1) |
| 6. **Citations** | `citation_resolve_rate` : chaque `citation` relit un passage réel | ≥ 0.98 |

Le rapport L3 (`workspace/evals/reports/retrieval-{index}-{run-id}.json`)
porte les six tableaux et les paramètres retenus. **Le `retrieval-contract`
cite ce rapport** : un poids sans rapport est une opinion.

Règle de diagnostic héritée : `recall@k` haut + `groundedness` bas ⇒ problème
de génération, ne pas toucher au retriever. `recall@k` bas sur `identifier`
seulement ⇒ jambe lexicale ou `query_prep`. `recall@k` bas sur `paraphrase`
seulement ⇒ jambe vectorielle, embedding ou chunking.

Tout est **déterministe** : `recall@k`, `nDCG`, `citation_resolve_rate` ne
coûtent aucun token et tournent à chaque commit qui touche `retrieval/` ou le
corpus.

---

## 6. Commande de smoke

Sans provider (vecteur de requête fixé), base de test requise (`network`) :

```bash
cd workspace/src/{AppName}
uv run pytest tests/retrieval/test_fusion.py tests/retrieval/test_query_prep.py -q     # L1, 0 token, < 5 s
uv run python -m {AppName}.retrieval.{index_slug} smoke --query "facture INV-2024-0093 montant"
#   → affiche les 3 listes (vector / lexical / fused) avec rangs et provenance
#   → vérifie : les deux jambes ont renvoyé > 0 candidat ; fused == rrf_fuse(vector, lexical) ; latence < LatencyP95Target/4
#   → exit 0 ; exit 3 si une jambe est vide ([RETRIEVAL_LEG_EMPTY]) ; exit 4 si SQL ≠ Python ([RETRIEVAL_FUSION_MISMATCH])
```

Puis, en L3 (coûte l'embedding des requêtes du golden, pas de LLM) :

```bash
uv run python -m sdda_scripts.eval_retrieval --suite workspace/evals/suites/retrieval-{index_slug}.yaml --ablation
```

---

## 7. Pièges connus

1. **`rrf_k` trop petit sur-pondère le rang 1.** Avec `k=1`, le premier de
   chaque jambe écrase tout ; avec `k=60` (valeur de la littérature), les rangs
   1–10 restent différenciés sans tyrannie du premier. Ne pas « optimiser » `k`
   sans la grille §5.2.
2. **Profondeurs de jambe différentes** faussent RRF : une jambe à 40 candidats
   et l'autre à 10 donnent à la seconde un avantage artificiel (ses rangs sont
   petits). Même `candidates_per_leg` partout.
3. **Le parser FTS découpe les identifiants.** `INV-2024-0093` devient
   `inv`, `2024`, `0093` ; `websearch_to_tsquery` en fait un `AND`, ce qui
   marche souvent — mais `A/B-12` ou `ERR_42` peuvent disparaître. Test L1
   `test_query_prep.py` sur les identifiants du golden ; jambe `pg_trgm` si
   nécessaire.
4. **Langue de stemming incohérente** entre ingestion (`french`) et requête
   (`simple`) = zéro correspondance sur les mots fléchis. La configuration est
   **un** paramètre lu par les deux chemins.
5. **`unaccent` à l'ingestion mais pas à la requête** (ou l'inverse) — même
   piège, même remède : `query_prep` et la colonne générée appellent la même
   fonction.
6. **Deux index, deux fraîcheurs.** Sur un store externe pour le lexical,
   l'index BM25 peut avoir 1 h de retard sur l'index vectoriel : un document
   n'est trouvé que par une jambe et la fusion le rétrograde. `indexHash`
   inclut les deux, et l'ingestion est **une** transaction logique.
7. **Mesurer au niveau chunk quand la vérité est au niveau document** (ou
   l'inverse) : `recall@k` chunk sévère + document laxiste, les deux sont
   utiles et le rapport porte les deux — mais ne pas comparer l'un à un seuil
   défini pour l'autre.
8. **Parent-child et doublons.** Trois chunks enfants du même parent occupent
   trois places du `top_k` fusionné ; dédupliquer sur le parent **après**
   fusion, puis compléter jusqu'à `top_k`.
9. **Reranker avant fusion.** Le reranker (si `RerankEnabled`) s'applique au
   résultat fusionné (`top_k` → `RerankTopN`), jamais à chaque jambe : il
   coûte par candidat.
10. **Optimiser les poids sur le holdout.** La grille §5.2 tourne sur `golden/`
    ; le holdout rend le verdict G8 une fois. Un `HoldoutDisjointCheck: strict`
    ne protège pas contre un humain qui regarde les deux.
11. **Le lexical est une jambe d'injection aussi efficace.** Un document
    empoisonné avec les mots exacts de la requête attendue remontera en
    lexical à coup sûr. La suite L8 doit tester l'empoisonnement **sur les deux
    jambes**.
12. **`ts_rank_cd` avec `LIMIT` sans index sur `@@`** : sans GIN, la jambe
    lexicale scanne la table. Le smoke de `pgvector.md` vérifie l'index GIN ;
    celui-ci vérifie que la jambe répond sous le quart du budget de latence.
