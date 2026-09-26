# Stack: pgvector-jvm (vectorstore)

> §2.3 (Librairies) suit `pgvector-jvm.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: vectorstore-pgvector-jvm
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin, java
Scope: store vectoriel dans PostgreSQL via l'extension `pgvector`, côté **JVM** (Kotlin et Java) — **le même schéma que `vectorstore/pgvector.md`** (tables `rag.*`, colonne `tsvector`, HNSW, tenant, `content_hash`, `index_meta`), migrations Flyway, accès par `JdbcClient` + `com.pgvector:pgvector`, filtrage par tenant, calcul de `indexHash`. Implémente l'entité **INDEX** du domain model. Suppose `lang/kotlin.md` ou `lang/java.md` et `framework/spring-ai.md`. Le pattern de requête (hybride, RRF) est dans `rag/hybrid-jvm.md`. Le `PgVectorStore` de Spring AI n'est **pas** le store de cette fiche (§1.1).

---

## 1. Rôle et périmètre

Les raisons de choisir pgvector sont celles de `vectorstore/pgvector.md` §1 —
un seul système à sécuriser, hybride natif dans la même table, transactions et
migrations ordinaires — et elles ne dépendent pas du langage. Ce qui dépend du
langage, c'est **comment** la JVM parle à cette table sans perdre aucune des
garanties : paramètres liés (jamais de littéral de vecteur concaténé),
`SET LOCAL` par transaction sur un pool, tenant posé par l'identité de
l'appelant, rôle en lecture seule.

**Le schéma est identique à celui de la fiche Python, colonne pour colonne.**
Un index construit par une application Python et relu par une application JVM
(ou l'inverse, ou par les scripts du framework) doit donner le même
`indexHash` et les mêmes rangs : les deux fiches décrivent un artefact, pas
deux.

Périmètre : schéma (renvoi), migrations Flyway, accès JDBC, dimensions,
opérateurs, `indexHash`, rôles, tests. **Hors périmètre** : chunking, modèle
d'embedding (`embedding/voyage.md`), fusion et métriques (`rag/hybrid-jvm.md`).

### 1.1 Pourquoi pas `PgVectorStore` de Spring AI

`spring-ai-pgvector-store` 2.0.1 fournit un `VectorStore` prêt à l'emploi.
Il n'est pas retenu, pour les mêmes raisons que `langchain-postgres` en
Python :

| Ce qu'exige le framework | `PgVectorStore` 2.0.1 |
|---|---|
| jambe lexicale `tsvector` + GIN dans la même table (`rag/hybrid-jvm.md`) | absente — aucune occurrence de `tsvector`, `ts_rank` ni d'hybride dans la classe (vérifié dans le jar) |
| `tenant_id` typé, filtré par `current_setting` posé depuis l'identité | filtre d'expression sur un `metadata` JSON, construit par l'appelant — donc par le code qui parle au modèle |
| `content_hash` (ingestion idempotente), `embedding_model` par ligne | non |
| `rag.index_meta` + `indexHash` (P10) | non |
| schéma `rag`, rôle `rag_ro`, migrations versionnées | table `vector_store` créée au démarrage (`CREATE TABLE IF NOT EXISTS`) |

Il reste activable pour un POC jetable (capability `spring-ai-vectorstore`,
on-demand) — pas pour un index que la RETRIEVAL GATE mesure.

---

## 2. Identité

### 2.1 Identité

- **Stack ID** : `vectorstore-pgvector-jvm`
- **Serveur** : PostgreSQL **16+** (17 recommandé) · extension `vector` **0.8.x** · `unaccent`
- **Client** : JDK 21 — `JdbcClient` (Spring Framework 7) sur HikariCP, `org.postgresql:postgresql`, `com.pgvector:pgvector` (type `PGvector`)
- **Migrations** : Flyway (`spring-boot-starter-flyway` + `flyway-database-postgresql`), SQL brut
- **Build** : Gradle Kotlin DSL (`lang/kotlin.md` / `lang/java.md`)
- **Schéma SQL dédié** : `rag` (jamais `public`)

### 2.3 Librairies

Source de vérité : `pgvector-jvm.libs.json` (versions du BOM Spring Boot
4.1.1 sauf mention).

| Artefact | Version | Rôle |
|---|---|---|
| `org.springframework.boot:spring-boot-starter-jdbc` | BOM 4.1.1 | `JdbcClient`, HikariCP, `TransactionTemplate` |
| `org.postgresql:postgresql` | 42.7.13 | pilote JDBC |
| `com.pgvector:pgvector` | 0.1.6 | `PGvector` — vecteur en paramètre lié |
| `org.springframework.boot:spring-boot-starter-flyway` | BOM 4.1.1 | migrations du schéma `rag` |
| `org.flywaydb:flyway-database-postgresql` | 12.4.0 | support PostgreSQL de Flyway (séparé du cœur) |
| `org.testcontainers:testcontainers-postgresql` (test) | 2.0.5 | PostgreSQL + pgvector jetable pour les tests `network` |
| `org.springframework.boot:spring-boot-testcontainers` (test) | BOM 4.1.1 | `@ServiceConnection` |

On-demand : `spring-ai-starter-vector-store-pgvector` (POC seulement, §1.1).
Absents par conception : `spring-ai-advisors-vector-store` (n'existe plus en
2.x, renommé `spring-ai-vector-store-advisor`), JPA, `langchain4j-pgvector`.

---

## 3. Mapping des concepts SDD_Agents → idiomes pgvector JVM

Le tableau de `vectorstore/pgvector.md` §3 vaut tel quel (CORPUS =
`rag.documents`, INDEX = `rag.chunks` + HNSW + GIN, `indexHash` stocké dans
`rag.index_meta`, citation par chunk, `content` typé `Untrusted`, filtre
tenant dans la requête, rôle `rag_ro`, `pii_scan_status`). Ce qui change :

| Concept | Idiome JVM | Notes |
|---|---|---|
| Schéma | **copie exacte** de `vectorstore/pgvector.md` §3.1, en migrations Flyway `retrieval/migrations/V1__rag_schema.sql`… | une divergence de colonne entre les deux fiches est un bug de fiche |
| Vecteur en paramètre | `new PGvector(float[])` passé à `JdbcClient.param(...)` | `PGvector` étend `PGobject` : le pilote l'envoie typé, sans littéral |
| `SET LOCAL` | `SELECT set_config('app.tenant_id', :tenant, true)`, `set_config('hnsw.ef_search', :ef, true)`, `set_config('statement_timeout', :ms, true)` **dans** un `TransactionTemplate` | `set_config(…, true)` = portée transaction, et accepte un paramètre lié — `SET LOCAL x = $1` ne l'accepte pas |
| Score | `1 - (embedding <=> :q)` en projection ; `ORDER BY embedding <=> :q` | `vectorstore/pgvector.md` §3.3 : l'index n'est pris qu'avec l'opérateur de sa classe |
| `RetrievedChunk` | `record` (Java) / `data class` (Kotlin) avec `content: Untrusted<String>` | P8 |
| Dimensions | `D` écrit une fois dans `V1__rag_schema.sql`, vérifié au démarrage contre `Settings.embeddingDims` | sinon `[RETRIEVAL_DIMS_MISMATCH]`, fail-fast |

### 3.1 Migrations

```
workspace/src/{AppName}/retrieval/migrations/
├── V1__rag_schema.sql      # extension vector + unaccent, schéma rag, rag.immutable_unaccent, tables, GIN, index_meta
├── V2__rag_hnsw.sql        # index HNSW — migration SÉPARÉE, après chargement initial (pgvector.md §5.3)
└── V3__rag_roles.sql       # rôles rag_ro / rag_ingest + GRANT ; mot de passe : jamais dans la migration
```

Le SQL est celui de `vectorstore/pgvector.md` §3.1, **y compris** le wrapper
`rag.immutable_unaccent` (piège §7.13 de cette fiche : `unaccent` n'est pas
`IMMUTABLE` et une colonne générée le refuse).

Les migrations ne tournent **pas** au démarrage de l'application :
`spring.flyway.enabled: false` dans `application.yml`. L'application se
connecte en `rag_ro`, qui ne peut rien créer ; lui faire migrer le schéma
exigerait de lui donner les droits que l'enveloppe lui retire. Elles tournent
par une tâche Gradle, avec le rôle propriétaire, lancée par l'opérateur :

```kotlin
// build.gradle.kts
tasks.register<JavaExec>("ragMigrate") {
    group = "sdda"
    classpath = sourceSets["main"].runtimeClasspath
    mainClass = "{package}.retrieval.RagMigrate"     // Flyway.configure().locations("classpath:retrieval/migrations")…migrate()
}
tasks.register<JavaExec>("ragIngest") {
    group = "sdda"
    classpath = sourceSets["main"].runtimeClasspath
    mainClass = "{package}.retrieval.RagIngest"      // documents → chunks → embeddings → upsert idempotent → indexHash
}
```

`RagMigrate` lit l'URL et le rôle propriétaire (`RAG_OWNER_DSN`, nom de
variable déclaré dans `STACK.md`) depuis la configuration, jamais depuis un
argument : un mot de passe en argument se lit dans `ps`.

### 3.2 Accès — la jambe vectorielle

**Java**

```java
// retrieval/{index}/VectorLeg.java — package {package}.retrieval.{index}
final class VectorLeg {
    private static final String SQL = """
        SELECT id, document_id, chunk_index, heading_path, char_start, char_end, content,
               1 - (embedding <=> :q) AS score
        FROM   rag.chunks
        WHERE  tenant_id = current_setting('app.tenant_id')::uuid
          AND  pii_scan_status <> 'flagged'
        ORDER  BY embedding <=> :q
        LIMIT  :k
        """;   // SQL, pas un prompt : P1 ne vise que les prompts

    private final JdbcClient jdbc;
    private final TransactionTemplate tx;   // TransactionTemplate en lecture seule
    private final DbSettings db;

    VectorLeg(JdbcClient jdbc, TransactionTemplate tx, DbSettings db) {
        this.jdbc = jdbc; this.tx = tx; this.db = db;
    }

    List<ChunkHit> search(float[] queryVec, TenantId tenant, int k, int efSearch) {
        return tx.execute(status -> {
            // portée TRANSACTION : un pool réutilise la connexion, une valeur de session fuirait au run suivant
            jdbc.sql("SELECT set_config('app.tenant_id', :t, true)").param("t", tenant.value()).query().singleValue();
            jdbc.sql("SELECT set_config('hnsw.ef_search', :ef, true)").param("ef", Integer.toString(efSearch)).query().singleValue();
            jdbc.sql("SELECT set_config('statement_timeout', :ms, true)").param("ms", Long.toString(db.statementTimeoutMs())).query().singleValue();
            return jdbc.sql(SQL).param("q", new PGvector(queryVec)).param("k", k).query(ChunkHit::fromRow).list();
        });
    }
}
```

**Kotlin**

```kotlin
// retrieval/{index}/VectorLeg.kt — package {package}.retrieval.{index}
internal class VectorLeg(
    private val jdbc: JdbcClient,
    private val tx: TransactionTemplate,
    private val db: DbSettings,
) {
    fun search(queryVec: FloatArray, tenant: TenantId, k: Int, efSearch: Int): List<ChunkHit> =
        tx.execute {
            jdbc.sql("SELECT set_config('app.tenant_id', :t, true)").param("t", tenant.value).query().singleValue()
            jdbc.sql("SELECT set_config('hnsw.ef_search', :ef, true)").param("ef", efSearch.toString()).query().singleValue()
            jdbc.sql("SELECT set_config('statement_timeout', :ms, true)").param("ms", db.statementTimeoutMs.toString()).query().singleValue()
            jdbc.sql(SQL).param("q", PGvector(queryVec)).param("k", k).query(ChunkHit::fromRow).list()
        } ?: emptyList()

    private companion object {
        const val SQL = """
            SELECT id, document_id, chunk_index, heading_path, char_start, char_end, content,
                   1 - (embedding <=> :q) AS score
            FROM   rag.chunks
            WHERE  tenant_id = current_setting('app.tenant_id')::uuid
              AND  pii_scan_status <> 'flagged'
            ORDER  BY embedding <=> :q
            LIMIT  :k
        """
    }
}
```

Le `DataSource` du retrieval est ouvert avec le rôle `rag_ro` ; l'URL, le rôle
et le mot de passe viennent de la configuration (noms de variables de
`STACK.md`), jamais d'un littéral. `TransactionTemplate` configuré
`setReadOnly(true)` : le pilote PostgreSQL pose alors la transaction en lecture
seule, **en plus** de `default_transaction_read_only` sur le rôle — ceinture et
bretelles, comme en Python.

### 3.3 Dimensions, opérateurs, HNSW

`vectorstore/pgvector.md` §3.2 à §3.4 s'appliquent sans changement :
dimensions par modèle (`voyage-3-large` → `vector(1024)`), `<=>` et
`vector_cosine_ops` par défaut, HNSW `m = 16`, `ef_construction = 64`,
`hnsw.iterative_scan = relaxed_order` avec filtre tenant (0.8.x). Seule la
façon de poser `ef_search` change : `set_config('hnsw.ef_search', …, true)`
plutôt que `SET LOCAL`, parce qu'il accepte un paramètre lié.

---

## 4. Structure de fichiers générée

Disposition à plat de `lang/kotlin.md` / `lang/java.md` §4 : tout ce qui suit
est dans la zone de `dev-retrieval` (`workspace/src/**/retrieval/**`).

```
workspace/src/{AppName}/retrieval/
├── migrations/V1__rag_schema.sql · V2__rag_hnsw.sql · V3__rag_roles.sql   # ressources (classpath:retrieval/migrations)
├── RagMigrate.{kt,java}           # main : Flyway, rôle propriétaire — tâche Gradle ragMigrate
├── RagIngest.{kt,java}            # main : ingestion idempotente — tâche Gradle ragIngest
└── {index}/
    ├── VectorLeg.{kt,java}        # §3.2
    ├── LexicalLeg.{kt,java}       # rag/hybrid-jvm.md
    ├── Ingest.{kt,java}           # documents → chunks → embeddings → INSERT … ON CONFLICT … WHERE content_hash <> …
    ├── IndexHash.{kt,java}        # calcule et compare indexHash avec rag.index_meta
    ├── ChunkHit.{kt,java}         # record / data class ; content: Untrusted<String>
    └── Smoke.{kt,java}            # §6

workspace/src/{AppName}/tests/retrieval/
├── StoreSqlTest.{kt,java}                  # L1 : chaque requête porte tenant_id + LIMIT + ORDER BY <=> (analyse du texte SQL)
├── IndexHashTest.{kt,java}                 # L1 : même entrée -> même hash ; changer le modèle ou D change le hash
└── IngestIdempotentNetworkTest.{kt,java}   # @Tag("network") : double ingestion = même nombre de lignes (Testcontainers)
```

---

## 5. Conventions imposées

Les treize règles de `vectorstore/pgvector.md` §5 s'appliquent (filtre tenant
partout, `LIMIT` ≤ `DbMaxRowsReturned`, `ORDER BY` avec l'opérateur de
l'index, portée transaction, `pii_scan_status`, idempotence par
`content_hash`, `embedding_model` par ligne, `indexHash` recalculé, scan PII à
l'ingestion, schéma `rag`, HNSW séparé, rôle `rag_ro`, `ANALYZE`). En plus :

1. **Aucun vecteur en littéral.** `"'[" + … + "]'::vector"` est refusé en
   revue : `PGvector` en paramètre lié, toujours. Un littéral construit par
   concaténation est le même chemin qu'une injection SQL.
2. **`set_config(…, true)` et jamais `SET`** hors d'une transaction : HikariCP
   rend la connexion au pool avec la valeur de session, et le run suivant — d'un
   autre tenant — hérite de son `app.tenant_id`.
3. **Deux `DataSource`** : `rag_ro` pour la recherche (application), rôle
   propriétaire pour `ragMigrate` (tâche d'opérateur), `rag_ingest` pour
   `ragIngest`. L'application ne reçoit jamais les deux derniers.
4. **`spring.flyway.enabled: false`** dans l'application ; les migrations sont
   une action d'opérateur (§3.1).
5. **Pas de `spring.sql.init`**, pas de `schema.sql` : un schéma créé au
   démarrage n'est pas versionné.

---

## 6. Commande de smoke

Marqué `network` (base de test requise), 0 token — l'embedding de la requête
de smoke est un **vecteur fixé** (`new float[D]` rempli de `0.01f`), pas un
appel au fournisseur :

```bash
cd workspace/src/{AppName}
./gradlew ragMigrate                       # rôle propriétaire, depuis la configuration
./gradlew run --args='health --live --json'
#   1. SELECT extversion FROM pg_extension WHERE extname='vector'       → >= 0.8
#   2. atttypmod de rag.chunks.embedding == Settings.embeddingDims       → sinon [RETRIEVAL_DIMS_MISMATCH]
#   3. index chunks_embedding_hnsw et chunks_tsv_gin présents
#   4. EXPLAIN de VectorLeg.SQL contient "Index Scan using chunks_embedding_hnsw"
#   5. current_user == rag_ro et transaction_read_only == on
#   6. rag.index_meta.index_hash == IndexHash.compute()                  → sinon [RETRIEVAL_INDEX_STALE]
./gradlew test -PincludeTags=network --tests '*.retrieval.*'
```

Smoke Timeout : 120 s (conteneur Testcontainers compris).

---

## 7. Contrat d'exécution

L'application JVM implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** (commandes, NDJSON `RunEvent`, codes de sortie). Pour ce store,
les trois points de `serving/cli.md` §3.5 signifient :

1. `run --json --input-file -` lit l'entrée sur `stdin` ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'application sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil) et le retrieval
   figé depuis le même dossier (`retrieval/{index}.jsonl`), **sans ouvrir de
   connexion à cette base** : le `DataSource` `rag_ro` n'est pas même
   construit en mode isolé, ce qui rend la fuite impossible plutôt
   qu'improbable ;
3. `retrieve --json --index ID --query-file - [--k N]` interroge **cet** index
   (hors isolement) et émet un événement `retrieval` (`index_id`,
   `result_ids[]`, `scores[]`) puis `run_finished` — sans appel au modèle de
   chat ; seul l'embedding de la requête est calculé.

Commande de lancement : `java -jar workspace/src/{AppName}/build/libs/{AppName}.jar`
(livrable, depuis la racine du dépôt), `./gradlew run --args='…'` en
développement ; les runners la dérivent par `--executor cli` ou l'imposent par
`--executor cmd:<commande>`. Le build produit exactement
`build/libs/{AppName}.jar` (`lang/java.md` §8.2, `lang/kotlin.md` §8).

---

## 8. Pièges connus

Les treize pièges de `vectorstore/pgvector.md` §7 s'appliquent. Propres à la
JVM :

1. **`SET LOCAL hnsw.ef_search = ?` avec un paramètre lié** échoue
   (`SET` n'accepte pas de paramètre) ; la tentation est alors de concaténer.
   `set_config('hnsw.ef_search', ?, true)` fait la même chose, paramétré.
2. **`PGvector.registerTypes(conn)` oublié à la lecture.** Pour **écrire**,
   `PGvector` suffit (c'est un `PGobject`) ; pour **relire** une colonne
   `vector` en `PGvector`, les types doivent être enregistrés sur la
   connexion. Le retrieval ne relit jamais le vecteur (seulement le score) :
   ne pas le sélectionner.
3. **Transaction absente.** `JdbcClient` hors `TransactionTemplate` exécute
   chaque requête en auto-commit : le `set_config(…, true)` s'éteint à la fin
   de sa propre instruction et la recherche part **sans tenant** — zéro ligne
   (fail-closed) dans le meilleur cas.
4. **Flyway au démarrage par défaut.** Avec `spring-boot-starter-flyway` sur
   le classpath, Spring Boot migre au démarrage si une `DataSource` existe :
   avec `rag_ro`, le démarrage échoue ; avec un rôle propriétaire, l'enveloppe
   est perdue. `spring.flyway.enabled: false`.
5. **`PgVectorStore` auto-configuré par un starter tiré par erreur** : il crée
   `public.vector_store` au démarrage (`initialize-schema`) — une seconde
   table d'index que rien ne mesure. Le starter est on-demand, jamais core.
6. **Pool partagé entre recherche et ingestion.** Un seul `DataSource` pour les
   deux donne à l'application le rôle d'ingestion. Deux pools, deux rôles.
