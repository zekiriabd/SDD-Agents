# Stack: pgvector-node (vectorstore)

> §2.3 (Librairies) suit `pgvector-node.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: vectorstore-pgvector-node
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: store vectoriel dans PostgreSQL via l'extension `pgvector`, côté **Node/TypeScript** — schéma des chunks, index HNSW, opérateurs de distance, colonne `tsvector` pour la jambe lexicale, migrations `node-pg-migrate`, filtrage par tenant, accès `pg` + `pgvector`. Implémente l'entité **INDEX** du domain model. Pendant Node de `vectorstore/pgvector.md` : **le schéma SQL, les index, les rôles et les règles sont identiques** ; seuls le client et l'outil de migration changent. Suppose `lang/typescript.md`. Le pattern de requête (hybride, RRF) est dans `rag/hybrid-node.md`.

---

## 1. Rôle et périmètre

Les trois raisons qui font de pgvector le store par défaut dès que PostgreSQL
est présent (`vectorstore/pgvector.md` §1) ne dépendent pas du langage : **un
seul système à sécuriser**, **hybride natif** (`tsvector` + GIN à côté de
`vector` + HNSW, fusion en une requête), **migrations ordinaires**. Elles valent
mot pour mot en Node.

Ce que cette fiche ajoute, c'est **comment ne pas les perdre en changeant de
client**. L'écosystème Node pousse vers deux raccourcis qui cassent chacun une
des trois raisons :

| Raccourci | Ce qu'il casse |
|---|---|
| `PGVectorStore` de `@langchain/community` comme store de production | son schéma (`langchain_pg_embedding`, métadonnées JSONB) ne porte ni `tenant_id`, ni `content_tsv`, ni `content_hash`, ni `index_meta` : le filtre d'identité devient un filtre de métadonnées fourni à l'appel, la jambe lexicale n'existe pas, l'`indexHash` n'a rien à épingler |
| un ORM (Prisma, TypeORM) | `SET LOCAL` par transaction et `ORDER BY embedding <=> $1` exigent du SQL brut ; l'ORM devient une seconde source de vérité du schéma à côté des migrations |

D'où le choix : **`pg` (node-postgres) + `pgvector` (adaptateur de types) + SQL
écrit à la main**, schéma porté par `node-pg-migrate`. `PGVectorStore` reste
disponible en on-demand pour un POC jetable, et c'est dit (§2.3).

Périmètre : schéma, index, dimensions, opérateurs, colonne lexicale,
migrations, `indexHash`, accès TypeScript. **Hors périmètre** : le chunking (le
`retrieval-contract`), le modèle d'embedding (`embedding/voyage.md`), la fusion
et les métriques (`rag/hybrid-node.md`).

---

## 2. Identité

### 2.1 Identité

- **Stack ID** : `vectorstore-pgvector-node`
- **Serveur** : PostgreSQL **16+** (17 recommandé) · extension `vector` **0.8.x** · extension `unaccent`
- **Client** : Node 22, TypeScript 6.0.x — `pg` 8.x (pool) + `pgvector` 0.3.x (`pgvector/pg`)
- **Migrations** : `node-pg-migrate` 9.x — fichiers TypeScript sous `retrieval/migrations/`
- **Build** : `pnpm`
- **Schéma SQL dédié** : `rag` (jamais `public`)

### 2.2 Pourquoi `pg` et pas un autre pilote

`pg` est le pilote que tirent déjà `@langchain/langgraph-checkpoint-postgres`
(checkpointer partagé, `framework/langgraph-js.md`) et `PGVectorStore`. Un seul
pilote = un seul pool par rôle, une seule façon de poser le tenant, une seule
instrumentation (`@opentelemetry/instrumentation-pg`, `observability/otel-genai-node.md`).
`postgres` (Postgres.js) est déclaré absent par conception pour cette raison, pas
parce qu'il serait moins bon.

### 2.3 Librairies

Source de vérité : `pgvector-node.libs.json`.

| Lib | Rôle |
|---|---|
| `pg` | `Pool` avec `onConnect: (c) => pgvector.registerTypes(c)` ; transaction explicite par requête |
| `pgvector` | `registerTypes`, `toSql(number[])` — types seulement |
| `node-pg-migrate` | migrations `up`/`down` : extension, schéma, GIN, HNSW **séparé**, rôles |
| `zod` / `pino` | parsing des lignes au bord (`Untrusted`), journal des requêtes |
| `@types/pg` | dev |

On-demand : `voyageai` (capability `embedding-voyage`, le SDK officiel appelé
par `rag/hybrid-node.md` §2.1 à l'ingestion comme à la requête) ;
`@langchain/community` (capability `langchain-vectorstore`) — **POC
seulement**, pour les raisons du §1. Absents par conception :
`@langchain/classic` (la fusion ne passe pas par `EnsembleRetriever`, cf.
`rag/hybrid-node.md` §1.1), `prisma`, `postgres`.

---

## 3. Mapping des concepts SDD_Agents → idiomes pgvector-node

Le mapping de `vectorstore/pgvector.md` §3 s'applique **entièrement** :
`rag.documents`, `rag.chunks`, `rag.index_meta`, un schéma par index logique,
`embedding vector(D)` fixé par migration, `citation`, `Untrusted`, filtre
`tenant_id` posé par `set_config`, rôle `rag_ro`, `pii_scan_status`. Le SQL de
`vectorstore/pgvector.md` §3.1 (tables, index, `rag.immutable_unaccent`) est
**repris tel quel** dans la migration : une base produite par le squelette
Python et une base produite par le squelette Node doivent être indiscernables,
sinon le rapport L3 d'un langage ne dit rien de l'autre.

Les tables de dimensions (§3.2), d'opérateurs (§3.3) et HNSW vs IVFFlat (§3.4)
de la fiche Python valent sans changement.

### 3.1 Migration

`node-pg-migrate` exécute des migrations **SQL** (fichier `.sql` coupé par les
marqueurs `-- Up Migration` / `-- Down Migration`, vérifié dans le gabarit et le
chargeur `sqlMigration` de la 9.0.0). C'est la forme retenue : un diff de schéma
se lit en SQL, pas dans l'API d'un outil de migration, et le fichier n'a pas à
être compilé par `tsc` (qui ne copie pas les `.sql` dans `dist/`).

```sql
-- workspace/src/{AppName}/retrieval/migrations/1727000000000_rag-schema.sql
-- Up Migration
-- corps IDENTIQUE à vectorstore/pgvector.md §3.1 (extensions, schéma rag, tables, GIN, index_meta,
-- fonction rag.immutable_unaccent) — HNSW exclu, cf. migration suivante
-- Down Migration
DROP SCHEMA rag CASCADE;
```

Trois migrations, dans cet ordre, comme en Python : schéma + GIN +
`index_meta`, puis index HNSW (**séparé**, après chargement initial), puis
rôles `rag_ro` / `rag_ingest` (mot de passe **jamais** dans la migration).

### 3.2 Pool et transaction

```ts
// workspace/src/{AppName}/retrieval/{index_slug}/store.ts
import pg from "pg";
import pgvector from "pgvector/pg";
import { settings } from "#app/app/config.js";

// Un pool PAR RÔLE : rag_ro sert les agents, rag_ingest ne sert que l'ingestion.
export const roPool = new pg.Pool({
  connectionString: settings.ragRoDsn.reveal(),          // nom de variable dans STACK.md, valeur dans .env
  max: settings.dbPoolMax,
  onConnect: async (client) => { await pgvector.registerTypes(client); },
});

const VECTOR_SQL = `
SELECT id, document_id, chunk_index, heading_path, char_start, char_end, content,
       1 - (embedding <=> $1) AS score
FROM   rag.chunks
WHERE  tenant_id = current_setting('app.tenant_id', true)::uuid
  AND  pii_scan_status <> 'flagged'
ORDER  BY embedding <=> $1
LIMIT  $2`;

export async function withTenantTx<T>(pool: pg.Pool, tenantId: string, efSearch: number,
                                      fn: (c: pg.PoolClient) => Promise<T>): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN READ ONLY");
    // set_config(..., true) = portée TRANSACTION : le tenant ne fuit jamais à la connexion suivante du pool.
    await client.query("SELECT set_config('app.tenant_id', $1, true)", [tenantId]);
    await client.query("SELECT set_config('hnsw.ef_search', $1, true)", [String(efSearch)]);
    await client.query("SELECT set_config('statement_timeout', $1, true)", [String(settings.dbStatementTimeoutMs)]);
    const out = await fn(client);
    await client.query("COMMIT");
    return out;
  } catch (err) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw err;
  } finally {
    client.release();
  }
}

export const vectorSearch = (tenantId: string, queryVec: number[], k: number, efSearch: number) =>
  withTenantTx(roPool, tenantId, efSearch, async (c) =>
    (await c.query(VECTOR_SQL, [pgvector.toSql(queryVec), k])).rows);
```

Pourquoi `set_config(name, value, true)` plutôt que `SET LOCAL x = $1` : `SET`
n'accepte pas de paramètre lié en PostgreSQL ; l'interpoler serait une injection
SQL par la valeur du tenant. `set_config` prend des paramètres et a la même
portée transactionnelle avec `is_local = true`.

### 3.3 `indexHash`

Calculé par `retrieval/{index_slug}/index-hash.ts` avec **exactement** la même
concaténation que la fiche Python (`embedding_model ‖ D ‖ chunk_strategy ‖
chunk_size ‖ overlap ‖ index_params ‖ corpus_hash`, SHA-256 hex) et stocké dans
`rag.index_meta`. Il est publié dans `retrieval/{index_slug}/index.manifest.json`,
que lisent les scripts du framework : le hash est un **fait** comparé par des
scripts Python à un index construit en Node, il ne peut pas dépendre du langage.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/retrieval/                 # zone dev-retrieval
├── migrations/                                    # node-pg-migrate, migrations SQL (-- Up / -- Down)
│   ├── {ts}_rag-schema.sql                        # IDENTIQUE à vectorstore/pgvector.md §3.1 (hors HNSW)
│   ├── {ts}_rag-hnsw.sql                          # après chargement initial
│   └── {ts}_rag-roles.sql                         # CREATE ROLE … LOGIN ; jamais de PASSWORD
└── {index_slug}/
    ├── store.ts                                   # pools par rôle, withTenantTx, vectorSearch, lexicalSearch
    ├── ingest.ts                                  # documents → chunks → embeddings → upsert idempotent (content_hash)
    ├── index-hash.ts                              # calcule / compare indexHash avec rag.index_meta
    ├── index.manifest.json                        # indexHash et config effective — lu par les scripts
    ├── models.ts                                  # RetrievedChunk (Zod), Citation
    ├── smoke.ts                                   # cf. §6
    └── tests/
        ├── store-sql.test.ts                      # L1 : chaque requête contient le filtre tenant + LIMIT (analyse du texte SQL)
        └── ingest-idempotent.test.ts              # describe("network …") : double ingestion = même nombre de lignes
```

Les migrations vivent sous `retrieval/` et non à la racine du projet : la racine
(`workspace/src/{AppName}/*`) est la zone de `dev-backend`, et le schéma de
l'index appartient à celui qui écrit l'index. En Python, `alembic/` à la racine
est un héritage que cette fiche ne reproduit pas.

---

## 5. Conventions imposées

Les règles 1 à 13 de `vectorstore/pgvector.md` §5 s'appliquent **telles
quelles**. Transposées en Node :

1. **Jamais `SET` hors transaction sur un `Pool`** : `client.query("SET ...")`
   hors `BEGIN` pose la valeur pour la **connexion**, qui retourne au pool et
   sert la requête suivante — d'un autre tenant. `withTenantTx` est le seul
   chemin vers la base ; un `pool.query(...)` direct dans `retrieval/` est
   signalé en L0 par grep.
2. **Paramètres liés partout** (`$1`, `$2`) ; aucune interpolation de gabarit
   (`` `…${x}…` ``) dans une chaîne SQL — lint L0 sur les fichiers qui
   importent `pg`.
3. **`pgvector.toSql(vec)`** pour tout vecteur passé en paramètre : un
   `number[]` passé nu est sérialisé par `pg` en tableau PostgreSQL
   (`{0.1,0.2}`), pas en `vector` — la requête échoue ou, pire, un cast implicite
   passe sur une colonne `real[]`.
4. **`registerTypes` dans `onConnect`** : sans lui, une colonne `vector` lue
   revient en chaîne `"[0.1,0.2,…]"`.
5. **Dimensions vérifiées au démarrage** (`atttypmod` = `settings.embeddingDims`)
   → sinon `[RETRIEVAL_DIMS_MISMATCH]`, code 8.
6. **Pas de `PGVectorStore` hors capability `langchain-vectorstore`** : le
   déclencheur est déclaratif, la revue voit qui l'a activé.

---

## 6. Commande de smoke

Base de test requise (`network`), 0 token — vecteur de requête **fixé**
(`Array(D).fill(0.01)`), aucun appel au provider :

```bash
cd workspace/src/{AppName}
pnpm exec node-pg-migrate up -m retrieval/migrations      # DSN du rôle d'ingestion, depuis l'environnement
pnpm build
node dist/retrieval/{index_slug}/smoke.js
#   1. extversion de 'vector' >= 0.8
#   2. atttypmod(rag.chunks.embedding) == settings.embeddingDims      sinon [RETRIEVAL_DIMS_MISMATCH]
#   3. index chunks_embedding_hnsw et chunks_tsv_gin présents
#   4. EXPLAIN de VECTOR_SQL contient "Index Scan using chunks_embedding_hnsw"
#   5. current_user == rag_ro et transaction_read_only == on
#   6. rag.index_meta.index_hash == indexHash calculé                   sinon [RETRIEVAL_INDEX_STALE]
npx --no-install vitest run retrieval
```

Smoke Timeout : 60 s.

---

## 7. Contrat d'exécution

L'application Node implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** — mêmes commandes, même flux NDJSON `RunEvent`, mêmes codes de
sortie — et les trois points de `serving/cli.md` §3.5 :

1. `run --json --input-file -` lit l'entrée sur stdin ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'app sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil) et le retrieval
   figé depuis le même dossier, sans aucun appel réseau d'outil ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished`.

Pour cette fiche, cela veut dire : en isolement, **aucune connexion** n'est
ouverte vers `rag.*` (le pool n'est pas construit) ; `retrieve` sans isolement
ouvre `roPool` et n'appelle aucun modèle de chat — seul l'embedding de la
requête est payé.

Lancement : `node workspace/src/{AppName}/dist/cli.js` après `pnpm build` — le
build **doit** produire exactement ce point d'entrée (`lang/typescript.md` §4) —
ou par le `bin` du package (`pnpm exec {AppName}`). Les runners du framework
choisissent cette commande avec `--executor cli` (dérivée du langage actif) ou
l'imposent avec `--executor cmd:<commande>`.

---

## 8. Pièges connus

Les pièges 1 à 13 de `vectorstore/pgvector.md` §7 valent tous. Propres à Node :

1. **`SET` sur une connexion de pool** (§5.1) — le piège le plus probable en
   Node, parce que `pool.query()` est l'API la plus courte et qu'elle prend une
   connexion au hasard.
2. **`toSql` oublié** (§5.3) : l'erreur `invalid input syntax for type vector`
   n'apparaît qu'au premier run réel si les tests L1 mockent `pg`.
3. **Ingestion concurrente par `Promise.all` sans borne** : 5 000 chunks =
   5 000 connexions demandées ; le pool les sérialise mais le provider
   d'embedding reçoit 5 000 requêtes. Batch de `settings.embeddingBatchSize`,
   concurrence bornée.
4. **Nombres flottants et JSON.** `JSON.stringify(1.0)` rend `"1"` là où Python
   rend `"1.0"` : un `index_params` sérialisé différemment change l'`indexHash`.
   La sérialisation canonique (`clés triées`, entiers/flottants tels que
   l'IR les porte) est une fonction unique, testée en L1 contre un vecteur de
   référence partagé avec la fiche Python.
5. **`PGVectorStore` « juste pour démarrer »** : il apporte son propre schéma
   de table, et le projet vit ensuite avec deux schémas d'index. S'il a servi à
   un POC, le jeter avant la PHASE 3.
6. **Migration lancée depuis `dist/`.** Les `.sql` ne sont pas copiés par
   `tsc` : `node-pg-migrate -m retrieval/migrations` pointe les sources, jamais
   `dist/`.
