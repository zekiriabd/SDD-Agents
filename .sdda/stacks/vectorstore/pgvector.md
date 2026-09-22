# Stack: pgvector (vectorstore)

> §2.3 (Librairies) régénérée depuis `pgvector.libs.json` — ne pas éditer manuellement.

Stack ID: vectorstore-pgvector
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: store vectoriel dans PostgreSQL via l'extension `pgvector` — schéma des chunks, index HNSW/IVFFlat, opérateurs de distance, recherche hybride avec `tsvector`, migrations, filtrage par tenant. Implémente l'entité **INDEX** du domain model. Suppose `lang/python.md`. Le pattern de requête (hybride, RRF) est dans `rag/hybrid.md`.

---

## 1. Rôle et périmètre

`pgvector` est le store vectoriel **recommandé dès que PostgreSQL est déjà
présent** (`DatabaseType: PostgreSql`), pour trois raisons qui comptent dans
SDD_Agents :

1. **Un seul système à sécuriser.** Le filtrage par tenant, le rôle en lecture
   seule, le `statement_timeout`, le journal des requêtes — tout ce que
   l'enveloppe de sûreté exige pour `DATA ACCESS` s'applique **tel quel** à
   l'index (RAG-PATTERNS §6 : le filtrage d'autorisation se fait à la source).
2. **Hybride natif.** `tsvector` + GIN dans la même table que `vector` + HNSW :
   la fusion RRF se fait en une requête SQL, sans second moteur à maintenir ni à
   rafraîchir (une seule chaîne d'ingestion → un seul `indexHash`).
3. **Transactions et migrations ordinaires.** Le schéma de l'index est un
   artefact Alembic versionné, diffable, rejouable — exactement ce que P1 exige.

Périmètre de la fiche : schéma des tables, choix et paramètres d'index,
dimensions par modèle d'embedding, opérateurs, colonne lexicale, migrations,
calcul de `indexHash`, accès Python. **Hors périmètre** : la stratégie de
chunking (RAG-PATTERNS §4, décidée dans le `retrieval-contract`), le modèle
d'embedding (`embedding/*.md`), la fusion et les métriques (`rag/hybrid.md`).

Quand **ne pas** choisir pgvector : > ~50 M de vecteurs, ou besoin de filtrage
par métadonnées complexe à très haut débit, ou pas de PostgreSQL dans le
paysage — voir `qdrant.md`, `azure-ai-search.md`.

---

## 2. Identité

### 2.1 Identité

- **Stack ID** : `vectorstore-pgvector`
- **Serveur** : PostgreSQL **16+** (17 recommandé) · extension `vector` **0.8.x**
- **Langage client** : Python 3.12 — `psycopg` 3.x (async) + `pgvector` (adaptateur types) ; SQLAlchemy 2.x + Alembic pour le schéma
- **Build tool** : `uv`
- **Schéma SQL dédié** : `rag` (jamais `public`)

<!-- CORE_PACKAGES_START -->
```bash
# Auto-généré depuis pgvector.libs.json — ne pas éditer.
uv add --project workspace/src/{AppName} \
  pgvector==0.4.2 \
  psycopg[binary,pool]==3.3.2 \
  sqlalchemy[asyncio]==2.0.52 \
  alembic==1.19.1 \
  pydantic==2.13.5 \
  pydantic-settings==2.15.0 \
  structlog==26.1.0 \
  ruff==0.16.5 \
  mypy==2.3.1
```
<!-- CORE_PACKAGES_END -->

<!-- ONDEMAND_PACKAGES_START -->
```bash
# Auto-généré depuis pgvector.libs.json (on-demand).
# capability: langchain-vectorstore
uv add --project workspace/src/{AppName} langchain-postgres==0.0.18
```
<!-- ONDEMAND_PACKAGES_END -->

<!-- LIBS_CATALOG_START -->
### 2.3 Librairies

> Source de vérité : `.sdda/stacks/vectorstore/pgvector.libs.json`. Pins à
> re-résoudre contre PyPI au premier bootstrap (design-phase).

| Lib | Version | Rôle |
|---|---|---|
| pgvector | 0.4.2 | types `Vector`, `HalfVector`, `SparseVector` pour psycopg 3 et SQLAlchemy ; `register_vector_async` |
| psycopg[binary,pool] | 3.3.2 | driver async + pool ; `SET LOCAL` par transaction (tenant, `hnsw.ef_search`) |
| sqlalchemy[asyncio] | 2.0.52 | modèle déclaratif de `rag.documents` / `rag.chunks` |
| alembic | 1.19.1 | migrations : extension, tables, index, colonne `tsvector` |
| pydantic / pydantic-settings | 2.13.5 / 2.15.0 | `RetrievedChunk`, config |
| structlog | 26.1.0 | journal des requêtes (`DbQueryLogging: full`) |
| ruff / mypy | 0.16.5 / 2.3.1 | L0 |

On-demand : `langchain-postgres` (`PGVector` LangChain) — **déconseillé** : son
schéma (`langchain_pg_embedding`, métadonnées JSONB non typées) ne porte ni la
colonne `tsvector`, ni le tenant, ni `content_hash`. À n'activer que pour un
POC jetable.
<!-- LIBS_CATALOG_END -->

---

## 3. Mapping des concepts SDD_Agents → idiomes pgvector

| Concept | Idiome pgvector | Notes |
|---|---|---|
| **CORPUS** | `rag.documents` (`id`, `source_uri`, `source_hash`, `tenant_id`, `acl`, `ingested_at`) | une ligne par source ; `source_hash` détecte la ré-ingestion |
| **INDEX** | `rag.chunks` + index HNSW sur `embedding` + GIN sur `content_tsv` | un **schéma par index logique** si plusieurs retrievers (`rag_contracts`, `rag_kb`) — jamais une colonne `index_name` |
| `retrievers[].binding.embeddingModel` + dims | `embedding vector(D)` — `D` fixé par migration (§3.2) | changer de modèle = nouvelle colonne/table + ré-embedding + nouvel `indexHash` |
| `retrievers[].binding.chunk` | colonnes `chunk_index`, `parent_id` (parent-child), `heading_path` (structural) | la stratégie n'est pas dans la base ; ses **traces** le sont |
| `retrievers[].binding.hybridWeights` | requête RRF (`rag/hybrid.md`) — poids en paramètres SQL | |
| `retrievers[].indexHash` | `sha256(embedding_model ‖ D ‖ chunk_strategy ‖ chunk_size ‖ overlap ‖ index_params ‖ corpus_hash)` calculé par `sdda_scripts/index_hash.py` et **stocké** dans `rag.index_meta` | tuple d'épinglage P10 |
| `citationMode: required` | chaque chunk porte `citation` = `{source_uri, heading_path, chunk_index, char_start, char_end}` | `citation_resolve_rate` se vérifie en relisant `rag.chunks` par `chunk_id` |
| **Trust posture** | `content` est retourné typé `Untrusted` | P8 — le corpus est une surface d'attaque |
| **Filtrage par identité** (RAG-PATTERNS §6) | `WHERE tenant_id = current_setting('app.tenant_id')::uuid` **dans la requête**, tenant posé par `SET LOCAL` depuis l'identité de l'appelant | jamais un paramètre fourni par le modèle |
| **Enveloppe de sûreté** | rôle `rag_ro` : `SELECT` sur `rag.*` uniquement, `default_transaction_read_only = on`, `statement_timeout` | même mécanique que `dataaccess/view-per-agent.md` |
| **PII dans l'index** (G7) | colonne `pii_scan_status` (`clean`/`redacted`/`flagged`) posée à l'ingestion ; `flagged` exclu par la requête | scan déterministe, 0 token |

### 3.1 Schéma

```sql
-- alembic/versions/0001_rag_schema.py → op.execute(...)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE rag.documents (
  id            uuid PRIMARY KEY,
  tenant_id     uuid NOT NULL,
  source_uri    text NOT NULL,
  source_hash   text NOT NULL,                 -- sha256 du contenu source
  acl           text[] NOT NULL DEFAULT '{}',  -- groupes autorisés, résolus à la source
  ingested_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, source_uri)
);

CREATE TABLE rag.chunks (
  id              uuid PRIMARY KEY,
  document_id     uuid NOT NULL REFERENCES rag.documents(id) ON DELETE CASCADE,
  tenant_id       uuid NOT NULL,               -- dénormalisé : le filtre ne doit pas joindre
  chunk_index     int  NOT NULL,
  parent_id       uuid REFERENCES rag.chunks(id),   -- parent-child, sinon NULL
  heading_path    text[] NOT NULL DEFAULT '{}',
  char_start      int NOT NULL,
  char_end        int NOT NULL,
  content         text NOT NULL,
  content_hash    text NOT NULL,               -- idempotence de l'ingestion
  content_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('french', unaccent(content))) STORED,
  embedding       vector(1024) NOT NULL,       -- D dépend du modèle, cf. §3.2
  embedding_model text NOT NULL,
  pii_scan_status text NOT NULL DEFAULT 'clean' CHECK (pii_scan_status IN ('clean','redacted','flagged')),
  metadata        jsonb NOT NULL DEFAULT '{}',
  UNIQUE (document_id, chunk_index)
);

CREATE INDEX chunks_tenant_idx   ON rag.chunks (tenant_id);
CREATE INDEX chunks_tsv_gin      ON rag.chunks USING gin (content_tsv);
-- HNSW : créé APRÈS le chargement initial (cf. §5.3), dans une migration séparée
CREATE INDEX chunks_embedding_hnsw ON rag.chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE TABLE rag.index_meta (
  index_id        text PRIMARY KEY,            -- ex. '1-contracts-index'
  index_hash      text NOT NULL,
  embedding_model text NOT NULL,
  dims            int  NOT NULL,
  chunk_strategy  text NOT NULL,
  chunk_size      int  NOT NULL,
  chunk_overlap   int  NOT NULL,
  index_params    jsonb NOT NULL,
  corpus_hash     text NOT NULL,
  built_at        timestamptz NOT NULL DEFAULT now()
);
```

> `to_tsvector('french', ...)` : la configuration linguistique est un paramètre
> du `retrieval-contract` (`LexicalLanguage`), pas une constante. Corpus
> multilingue → colonne `lang` + `to_tsvector(lang::regconfig, ...)` ou
> configuration `simple`.

### 3.2 Dimensions par modèle d'embedding

| `EmbeddingModel` (STACK.md) | Dims par défaut | Type conseillé | Remarque |
|---|:---:|---|---|
| `voyage-3-large` | 1024 | `vector(1024)` | supporte 256/512/2048 (Matryoshka) ; 1024 est le compromis |
| `voyage-3.5` / `voyage-3.5-lite` | 1024 | `vector(1024)` | |
| `text-embedding-3-small` | 1536 | `vector(1536)` | |
| `text-embedding-3-large` | 3072 | `halfvec(3072)` **ou** `vector(1024)` via `dimensions=1024` | 3072 > 2000 : **pas d'index HNSW/IVFFlat possible sur `vector`** ; `halfvec` monte à 4000 |
| `embed-v4` (Cohere) | 1536 | `vector(1536)` | dims réglables 256–1536 |
| `bge-m3` (local) | 1024 | `vector(1024)` | dense uniquement ici ; sparse → `sparsevec`, hors MVP |

Règle : **`D` est écrit une fois dans la migration** et vérifié au démarrage
(`SELECT atttypmod ... = D`) contre `Settings.embedding_dims` → sinon
`[RETRIEVAL_DIMS_MISMATCH]`, fail-fast.

### 3.3 Opérateurs de distance et classes d'index

| Opérateur | Distance | Classe HNSW/IVFFlat | Quand |
|---|---|---|---|
| `<=>` | cosinus (1 − cos) | `vector_cosine_ops` | **défaut** — tous les modèles listés en §3.2 |
| `<#>` | produit scalaire **négatif** | `vector_ip_ops` | vecteurs normalisés (équivalent cosinus, un peu plus rapide) ; **le score est négatif**, ne pas l'afficher tel quel |
| `<->` | L2 | `vector_l2_ops` | rarement pertinent pour du texte |
| `<+>` | L1 | `vector_l1_ops` | rarement |

**L'index n'est utilisé que si l'`ORDER BY` emploie exactement l'opérateur de sa
classe** et qu'un `LIMIT` est présent. `score = 1 - (embedding <=> $q)` se
calcule en projection, pas dans l'`ORDER BY`.

### 3.4 HNSW vs IVFFlat

| | HNSW | IVFFlat |
|---|---|---|
| Recall à paramètres par défaut | élevé | moyen, dépend de `probes` |
| Construction | lente, mémoire (`maintenance_work_mem`) | rapide, mais **exige des données** (entraînement des centroïdes) |
| Mises à jour incrémentales | bonnes | dégradent le recall (centroïdes figés) → rebuild périodique |
| Paramètres build | `m` (16), `ef_construction` (64) | `lists` = `rows/1000` (≤ 1 M lignes), `sqrt(rows)` au-delà |
| Paramètre requête | `SET LOCAL hnsw.ef_search = 40` (défaut ; ≥ `top_k` candidats) | `SET LOCAL ivfflat.probes = sqrt(lists)` |
| Choix SDD_Agents | **défaut** (`IngestionMode: incremental` ou `on-source-change`) | `IngestionMode: batch` avec corpus figé et > 5 M vecteurs |

Filtrage + index (0.8.x) : `SET LOCAL hnsw.iterative_scan = relaxed_order` pour
que `WHERE tenant_id = …` ne réduise pas le nombre de résultats sous `top_k`
(comportement pré-0.8 : l'index renvoie `ef_search` candidats **puis** filtre —
avec un tenant à 2 % du corpus, on obtenait 0 ligne). Alternative : index HNSW
**partiel par tenant** si les tenants sont peu nombreux et stables.

### 3.5 Accès Python

```python
# src/{AppName}/retrieval/{index_slug}/store.py
from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector_async
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

VECTOR_SQL = """
SELECT id, document_id, chunk_index, heading_path, char_start, char_end, content,
       1 - (embedding <=> %(q)s::vector) AS score
FROM   rag.chunks
WHERE  tenant_id = current_setting('app.tenant_id')::uuid
  AND  pii_scan_status <> 'flagged'
ORDER  BY embedding <=> %(q)s::vector
LIMIT  %(k)s
"""

async def vector_search(pool: AsyncConnectionPool, *, query_vec: list[float], tenant_id: str, k: int, ef_search: int) -> list[dict]:
    async with pool.connection() as conn:
        await register_vector_async(conn)
        conn.row_factory = dict_row
        async with conn.transaction():
            # SET LOCAL : portée transaction. Le tenant vient de l'identité de l'appelant, jamais du modèle.
            await conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            await conn.execute("SET LOCAL hnsw.ef_search = %s", (ef_search,))
            await conn.execute("SET LOCAL statement_timeout = %s", (settings.db_statement_timeout_ms,))
            cur = await conn.execute(VECTOR_SQL, {"q": query_vec, "k": k})
            return await cur.fetchall()
```

Le pool est ouvert avec le rôle `rag_ro` (DSN via `Settings`, `SecretStr`).

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── alembic/
│   ├── env.py                          # URL depuis Settings (jamais en dur)
│   └── versions/
│       ├── 0001_rag_schema.py          # extension, schéma rag, tables, GIN, index_meta
│       ├── 0002_rag_hnsw.py            # index HNSW — migration SÉPARÉE, après chargement initial
│       └── 0003_rag_roles.py           # rôle rag_ro + GRANT (mot de passe : jamais dans la migration)
└── src/{AppName}/retrieval/{index_slug}/
    ├── __init__.py
    ├── store.py                        # vector_search, lexical_search (cf. rag/hybrid.md), upsert_chunks
    ├── ingest.py                       # documents → chunks → embeddings → upsert idempotent (content_hash)
    ├── index_hash.py                   # calcule et compare indexHash avec rag.index_meta
    ├── models.py                       # RetrievedChunk (pydantic frozen), Citation
    └── smoke.py                        # cf. §6
workspace/src/{AppName}/tests/retrieval/
    ├── test_store_sql.py               # L1 : les requêtes contiennent le filtre tenant + LIMIT (analyse du texte SQL)
    └── test_ingest_idempotent.py       # L6 (marqué network) : double ingestion = même nombre de lignes
```

---

## 5. Conventions imposées

### 5.1 Requêtes

1. **Toute requête de recherche porte le filtre `tenant_id`** posé par
   `SET LOCAL`, même en mono-tenant (valeur unique). Test L1 par analyse du SQL :
   `WHERE tenant_id` présent → sinon `[RETRIEVAL_TENANT_FILTER_MISSING]`.
2. **`LIMIT` obligatoire** et ≤ `DbMaxRowsReturned`.
3. **`ORDER BY` avec l'opérateur de la classe d'index** ; jamais `ORDER BY score DESC`.
4. **`SET LOCAL`** (portée transaction) et jamais `SET` (portée session) sur un
   pool : `ef_search`, `statement_timeout`, `app.tenant_id`.
5. **`pii_scan_status <> 'flagged'`** dans toute requête servie à un agent.

### 5.2 Ingestion

6. **Idempotence par `content_hash`** : `INSERT … ON CONFLICT (document_id, chunk_index) DO UPDATE WHERE excluded.content_hash <> chunks.content_hash` — ré-embedder seulement ce qui a changé.
7. **`embedding_model` écrit sur chaque ligne** ; l'ingestion refuse d'écrire
   dans une table dont `index_meta.embedding_model` diffère.
8. **`indexHash` recalculé après chaque ingestion** et comparé à
   `rag.index_meta` ; s'il change, les baselines L3+ sont **périmées** (P10) et
   le pipeline l'annonce.
9. **Scan PII à l'ingestion** (déterministe : regex e-mail/téléphone/IBAN/carte
   + dictionnaires) → `pii_scan_status`. `MemoryPIIPolicy: redact-before-write`
   s'applique au `content` stocké.

### 5.3 Schéma et migrations

10. **Schéma `rag`, jamais `public`.** Un index logique = un schéma
    (`rag_contracts`, `rag_kb`) si plusieurs retrievers.
11. **HNSW dans une migration séparée**, exécutée après le chargement initial
    avec `SET maintenance_work_mem = '2GB'` et `max_parallel_maintenance_workers`
    adaptés. Construire l'index sur table vide puis insérer 1 M lignes est
    5–10× plus lent.
12. **Rôle `rag_ro`** : `GRANT USAGE ON SCHEMA rag`, `GRANT SELECT ON ALL TABLES
    IN SCHEMA rag`, `ALTER ROLE rag_ro SET default_transaction_read_only = on`.
    L'ingestion utilise un rôle distinct (`rag_ingest`). Le mot de passe n'est
    **jamais** dans la migration : `ALTER ROLE … PASSWORD` est exécuté par
    l'opérateur depuis `STACK.md`.
13. **`ANALYZE rag.chunks`** après chargement massif — le planificateur doit
    connaître la distribution de `tenant_id`.

---

## 6. Commande de smoke

Marqué `network` (base de test requise), 0 token — l'embedding de la requête de
smoke est un **vecteur fixé** (`[0.01] * D`), pas un appel au provider :

```bash
cd workspace/src/{AppName}
uv run alembic upgrade head
uv run python -m {AppName}.retrieval.{index_slug}.smoke
#   1. SELECT extversion FROM pg_extension WHERE extname='vector'      → >= 0.8
#   2. atttypmod de rag.chunks.embedding == Settings.embedding_dims     → sinon [RETRIEVAL_DIMS_MISMATCH]
#   3. index chunks_embedding_hnsw et chunks_tsv_gin présents
#   4. EXPLAIN de VECTOR_SQL contient "Index Scan using chunks_embedding_hnsw"
#   5. rôle courant == rag_ro et transaction_read_only == on
#   6. rag.index_meta.index_hash == index_hash.compute()               → sinon [RETRIEVAL_INDEX_STALE]
uv run pytest tests/retrieval -q -m "not llm"
```

Smoke Timeout : 60 s.

---

## 7. Pièges connus

1. **`<=>` est une distance, pas une similarité.** `score = 1 - distance`.
   Afficher la distance brute comme score inverse le classement dans les
   rapports. Le `RetrievedChunk.score` est toujours une similarité ∈ [0, 1].
2. **`ORDER BY` avec un autre opérateur que celui de l'index = scan
   séquentiel.** Index `vector_cosine_ops` + `ORDER BY embedding <-> q` → pas
   d'index, latence ×100 sur 1 M lignes. Le smoke §6.4 vérifie le plan.
3. **Filtre `WHERE` + HNSW pré-0.8 renvoie moins de lignes que `LIMIT`.**
   Cause principale des « recall@k catastrophique sur un tenant ». Exiger
   0.8.x et `hnsw.iterative_scan`, ou index partiel.
4. **3072 dimensions = pas d'index sur `vector`.** Limite 2000 pour
   HNSW/IVFFlat sur `vector`. `text-embedding-3-large` doit être réduit
   (`dimensions=1024`) ou stocké en `halfvec` (limite 4000, légère perte de
   précision). Décision dans le `retrieval-contract`, mesurée en L3.
5. **Changer de modèle d'embedding sans tout ré-embedder.** Deux modèles dans
   la même colonne = similarités sans signification. La colonne
   `embedding_model` + le refus d'écriture §5.7 rendent l'erreur impossible.
6. **IVFFlat construit sur table vide.** Les centroïdes sont aléatoires,
   le recall est nul. Toujours après chargement, et rebuild après ingestion
   massive.
7. **`SET hnsw.ef_search` sur un pool** fuit la valeur aux connexions
   suivantes. `SET LOCAL` dans la transaction, systématiquement.
8. **`ef_search < top_k`** : HNSW ne peut pas renvoyer plus de candidats que
   `ef_search`. Avec `RetrievalTopK: 8` et candidats hybrides à 40 par jambe,
   `ef_search` ≥ 40.
9. **Le tenant fourni par le modèle.** Un outil `search(query, tenant_id)` où
   `tenant_id` est un argument que le LLM remplit est une fuite d'autorisation
   par construction. Le tenant vient de l'identité de l'appelant, posé par le
   wrapper (`set_config`), invisible du modèle.
10. **`content` volumineux et TOAST.** Des chunks de 800 tokens ≈ 3–4 Ko
    passent ; des `parent` de 10 Ko sont TOASTés — coût de lecture à chaque
    hit. Parent-child : stocker le parent une fois, référencé par `parent_id`.
11. **Le corpus contient des instructions.** « Ignore les consignes
    précédentes » dans un PDF finira dans un contexte. Le `content` est
    `Untrusted` ; la suite adversariale (L8) empoisonne volontairement un index
    de test.
12. **`pg_dump` et la version d'extension.** Un dump restauré sur une instance
    en 0.7 perd `iterative_scan` et les `halfvec` d'index > 2000 dims. Épingler
    la version d'extension dans le smoke.
13. **`unaccent` n'est pas `IMMUTABLE`** par défaut → refusé dans une colonne
    générée. Créer un wrapper `CREATE FUNCTION rag.immutable_unaccent(text)
    RETURNS text IMMUTABLE …` et l'utiliser dans `content_tsv`.
