# Stack: pgvector-dotnet (vectorstore)

Stack ID: vectorstore-pgvector-dotnet
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: csharp
Scope: store vectoriel dans PostgreSQL via l'extension `pgvector`, côté **.NET** — même schéma SQL que `vectorstore/pgvector.md` (chunks, `tsvector`, HNSW, `index_meta`), accès par **Npgsql** + **Pgvector** en SQL paramétré, migrations SQL brutes (DbUp), filtrage par tenant posé par la session. Implémente l'entité **INDEX** du domain model. Suppose `lang/csharp.md`. Le pattern de requête (hybride, RRF) est dans `rag/hybrid-dotnet.md`. Catalogue : `pgvector-dotnet.libs.json`.

---

## 1. Rôle et périmètre

Le pendant .NET de `vectorstore/pgvector.md`. Les trois raisons qui font de
pgvector le store par défaut dès que PostgreSQL est là valent mot pour mot :
**un seul système à sécuriser** (le rôle en lecture seule, le
`statement_timeout` et le filtre tenant de l'enveloppe DATA ACCESS s'appliquent
tels quels à l'index), **hybride natif** (`tsvector` + GIN et `vector` + HNSW
dans la même table, une seule chaîne d'ingestion, un seul `indexHash`),
**migrations ordinaires** (un schéma SQL versionné, diffable, rejouable).

> **Le schéma n'est pas traduit, il est partagé.** `rag.documents`,
> `rag.chunks`, `rag.index_meta` sont ceux de `pgvector.md` §3.1, colonne pour
> colonne. Ce n'est pas une commodité : le rapport L3 d'une MISSION et la
> baseline qu'il compare dépendent de l'index, pas du langage qui l'interroge.
> Un index construit par une application Python et relu par une application C#
> doit rendre les mêmes `result_ids` — c'est ce qui rend deux implémentations de
> la même MISSION comparables.

Ce que la fiche **ne** reprend **pas** de Python : SQLAlchemy et Alembic. En
.NET, le SQL est écrit à la main dans des fichiers `.sql` et exécuté par Npgsql ;
les migrations sont ces mêmes fichiers, rejoués par DbUp. Pas d'ORM : la requête
hybride est un CTE qu'aucun ORM n'écrit mieux qu'un humain, et un modèle C# du
schéma serait une seconde source de vérité à côté du SQL.

Périmètre : schéma, index, dimensions, opérateurs, migrations, calcul de
`indexHash`, accès Npgsql. **Hors périmètre** : le chunking (décidé dans le
`retrieval-contract`), l'embedding (`embedding/voyage.md`, client dans
`rag/hybrid-dotnet.md` §3.3), la fusion et les métriques (`rag/hybrid-dotnet.md`).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `vectorstore-pgvector-dotnet` |
| **Serveur** | PostgreSQL **16+** (17 recommandé) · extension `vector` **0.8.x** · `unaccent` |
| **Client** | C# 14 / `net10.0` — `Npgsql` 10.0.x (`NpgsqlDataSource`, pool intégré) + `Pgvector` 0.3.x (`UseVector()`, type `Vector`) |
| **Migrations** | `dbup-postgresql` 7.0.x — scripts `.sql` embarqués, table de version |
| **Schéma SQL** | `rag` (jamais `public`) — identique à `vectorstore/pgvector.md` §3.1 |
| **Catalogue** | `pgvector-dotnet.libs.json` — seul fichier qui fasse foi sur les versions |

### 2.1 Pourquoi pas `CommunityToolkit.VectorData.PgVector`

Le connecteur Semantic Kernel pour PgVector n'existe plus qu'en preview ; sa
version stable est désormais `CommunityToolkit.VectorData.PgVector` (1.0.1,
.NET Foundation). Son `PostgresCollection` implémente même la recherche hybride
(`HybridSearchAsync` : `tsvector` + vecteur + RRF), vérifiée dans le code source
du paquet. Il est pourtant **onDemand**, et déconseillé par défaut, pour deux
raisons :

1. **La fusion vit dans son SQL.** RRF y est calculé par la base, donc il n'est
   plus une fonction pure qu'un test L1 exécute sur des cas connus
   (`rag/hybrid-dotnet.md` §3.1). Or le diagnostic « quelle jambe a remonté le
   document » (`provenance`) et la vérification SQL ↔ référence (`[RETRIEVAL_FUSION_MISMATCH]`)
   supposent une fusion qu'on peut appeler sans base.
2. **Le schéma est le sien.** Un store qui décide de ses tables ne porte ni
   `tenant_id` dénormalisé, ni `pii_scan_status`, ni `content_hash`, ni
   `index_meta` — c'est-à-dire ni le filtre d'identité, ni l'exclusion PII, ni
   l'idempotence, ni l'épinglage P10.

Il reste légitime quand un ADR impose l'abstraction `Microsoft.Extensions.VectorData`
(portabilité entre stores) ; la fusion est alors testée en L6 contre la fonction
pure, pas en L1.

---

## 3. Mapping des concepts SDD_Agents → idiomes pgvector / Npgsql

| Concept | Idiome | Notes |
|---|---|---|
| **CORPUS** / **INDEX** | `rag.documents` / `rag.chunks` + HNSW + GIN — `pgvector.md` §3.1 | un schéma par index logique (`rag_contracts`, `rag_kb`) |
| Dimensions `D` | `vector(D)` écrit dans la migration, vérifié au démarrage | `pgvector.md` §3.2 ; écart → `[RETRIEVAL_DIMS_MISMATCH]`, fail-fast |
| Opérateurs / classes d'index | `<=>` + `vector_cosine_ops` par défaut | `pgvector.md` §3.3 ; l'`ORDER BY` emploie exactement l'opérateur de la classe |
| **Pool** | un `NpgsqlDataSource` par rôle (`rag_ro`, `rag_ingest`), construit **une fois** par `NpgsqlDataSourceBuilder` + `UseVector()` | enregistré en singleton dans l'hôte ; un `NpgsqlConnection` par requête, rendu au pool par `await using` |
| **Vecteur de requête** | `new Vector(ReadOnlyMemory<float>)` passé en paramètre `@q_vec` | jamais sérialisé en texte dans le SQL |
| **Filtrage par identité** | `SELECT set_config('app.tenant_id', @tenant, true)` en tête de transaction | le tenant vient de `ToolContext` (l'appelant), jamais d'un argument que le modèle remplit |
| **Paramètres de session** | `SET LOCAL hnsw.ef_search`, `SET LOCAL statement_timeout`, `SET LOCAL hnsw.iterative_scan = relaxed_order` | `SET LOCAL` : portée transaction — un `SET` fuit sur la connexion rendue au pool |
| **Trust posture** | `RetrievedChunk.Content : Untrusted` (`lang/csharp.md` §5.3) | P8 — le compilateur refuse de le concaténer au prompt système |
| **indexHash** | `sha256(embedding_model ‖ D ‖ chunk_strategy ‖ chunk_size ‖ overlap ‖ index_params ‖ corpus_hash)`, stocké dans `rag.index_meta` | même formule que `pgvector.md` §3 ; publié dans `retrieval/{index}/index.manifest.json` |
| **Enveloppe** | rôle `rag_ro` : `SELECT` sur `rag.*`, `default_transaction_read_only = on` | vérifié au démarrage (§5.8) |
| **PII dans l'index** | `pii_scan_status <> 'flagged'` dans toute requête servie | scan déterministe à l'ingestion |

### 3.1 Construction du pool

```csharp
// retrieval/RagDataSources.cs — namespace {AppName}.Retrieval
using Npgsql;
using Pgvector;           // type Vector

namespace {AppName}.Retrieval;

public sealed class RagDataSources(IOptions<RagOptions> options) : IAsyncDisposable
{
    // Un NpgsqlDataSource EST le pool. En construire un par requête ouvre un
    // pool par requête : les connexions ne sont jamais réutilisées et le
    // serveur atteint max_connections sous charge d'eval.
    public NpgsqlDataSource ReadOnly { get; } = Build(options.Value.ReadOnlyConnectionString);

    private static NpgsqlDataSource Build(string connectionString)
    {
        var builder = new NpgsqlDataSourceBuilder(connectionString);
        builder.UseVector();  // extension de Pgvector sur le type mapper de Npgsql
        return builder.Build();
    }

    public ValueTask DisposeAsync() => ReadOnly.DisposeAsync();
}
```

La chaîne de connexion vient d'`IOptions` (liée à `IConfiguration`), jamais d'un
littéral ni d'`Environment.GetEnvironmentVariable` (`[SEC_ENV_VAR_FORBIDDEN]`).

### 3.2 Recherche vectorielle — une transaction, trois `SET LOCAL`

```csharp
// retrieval/{index_slug}/VectorLeg.cs
public async Task<IReadOnlyList<Candidate>> SearchAsync(
    ReadOnlyMemory<float> queryVector, string tenantId, int k, int efSearch, CancellationToken ct)
{
    await using var conn = await _sources.ReadOnly.OpenConnectionAsync(ct);
    await using var tx = await conn.BeginTransactionAsync(ct);

    // set_config(..., true) = portée transaction. Le tenant vient de l'appelant.
    await ExecAsync(conn, tx, "SELECT set_config('app.tenant_id', @t, true)", ("t", tenantId), ct);
    await ExecAsync(conn, tx, "SELECT set_config('hnsw.ef_search', @ef, true)", ("ef", efSearch.ToString(CultureInfo.InvariantCulture)), ct);
    await ExecAsync(conn, tx, "SELECT set_config('statement_timeout', @st, true)", ("st", _options.StatementTimeoutMs.ToString(CultureInfo.InvariantCulture)), ct);

    await using var cmd = new NpgsqlCommand(Sql.VectorSearch, conn, tx);   // texte SQL chargé depuis sql/vector_search.sql
    cmd.Parameters.AddWithValue("q_vec", new Vector(queryVector));
    cmd.Parameters.AddWithValue("k", k);
    await using var reader = await cmd.ExecuteReaderAsync(ct);
    // … lecture colonne par colonne ; Content -> Untrusted
    await tx.CommitAsync(ct);
}
```

`set_config(nom, valeur, true)` plutôt que `SET LOCAL x = @p` : `SET` n'accepte
pas de paramètre lié, et une valeur interpolée dans un `SET` est une injection
SQL dans la commande qui pose le tenant.

---

## 4. Structure de fichiers générée

Disposition **plate** de `lang/csharp.md` §4 : les couches sont des répertoires
en minuscules directement sous `workspace/src/{AppName}/`, parce que la matrice
d'ownership (`loader.yml`) les reconnaît par ce chemin, sensible à la casse sous
Linux.

```
workspace/src/{AppName}/
├── retrieval/
│   ├── migrations/                      # scripts DbUp — embarqués (EmbeddedResource), exécutés par `{AppName} migrate`
│   │   ├── 0001_rag_schema.sql          # extensions, schéma rag, tables, GIN, index_meta, rag.immutable_unaccent
│   │   ├── 0002_rag_hnsw.sql            # HNSW — script SÉPARÉ, après chargement initial
│   │   └── 0003_rag_roles.sql           # rag_ro / rag_ingest + GRANT (aucun mot de passe)
│   ├── RagDataSources.cs                # NpgsqlDataSource par rôle, UseVector()
│   ├── RetrievedChunk.cs                # record : ChunkId, DocId, Score, Content (Untrusted), Citation, Provenance
│   └── {index_slug}/
│       ├── VectorLeg.cs  LexicalLeg.cs  # cf. rag/hybrid-dotnet.md
│       ├── Ingest.cs                    # documents → chunks → embeddings (input_type=document) → upsert idempotent
│       ├── IndexHash.cs                 # calcule et compare avec rag.index_meta
│       ├── Smoke.cs                     # cf. §6
│       ├── index.manifest.json          # indexHash + config effective (lu par les scripts)
│       └── sql/vector_search.sql  sql/lexical_search.sql  sql/hybrid_rrf.sql
└── tests/
    └── retrieval/
        ├── StoreSqlTests.cs             # L1 : chaque .sql contient le filtre tenant, pii_scan_status et LIMIT
        └── IngestIdempotentNetworkTests.cs   # L6 [Trait("Category","network")] : double ingestion = même nombre de lignes
```

Les migrations sont sous `retrieval/migrations/` et non à la racine du projet :
un répertoire racine qui n'est pas une couche n'appartient à aucune zone de la
matrice d'ownership, donc aucun `dev-*` n'a le droit d'y écrire. Le schéma de
l'index est la propriété de `dev-retrieval`, comme ses requêtes.

Les `.sql` sont copiés dans la sortie (`<None Include="retrieval/**/sql/*.sql" CopyToOutputDirectory="PreserveNewest" />`)
ou embarqués : un SQL qui n'est pas un fichier n'est pas relisible par le test
L1 d'analyse de texte, ni par la revue.

---

## 5. Conventions imposées

Les règles 1 à 13 de `vectorstore/pgvector.md` §5 s'appliquent **sans
exception** : filtre tenant dans toute requête, `LIMIT` obligatoire et
≤ `DbMaxRowsReturned`, `ORDER BY` avec l'opérateur de la classe d'index,
`SET LOCAL` (jamais `SET`), `pii_scan_status <> 'flagged'`, idempotence par
`content_hash`, `embedding_model` sur chaque ligne, `indexHash` recalculé après
ingestion, scan PII à l'ingestion, schéma `rag`, HNSW en migration séparée, rôles
distincts `rag_ro` / `rag_ingest`, `ANALYZE` après chargement massif. Ce qui
s'ajoute en .NET :

1. **Un `NpgsqlDataSource` par rôle, singleton.** Il porte le pool ; le
   recréer par requête détruit le pooling.
2. **Paramètres nommés liés (`@nom`), toujours.** Aucune concaténation de
   chaîne dans un `CommandText` — y compris pour `ef_search` et le tenant, qui
   passent par `set_config`.
3. **`await using` sur connexion, transaction, commande et lecteur.** Un lecteur
   non libéré garde la connexion hors du pool ; sous k runs d'eval en parallèle,
   le pool s'épuise et l'outil « tombe en timeout ».
4. **`CancellationToken` propagé jusqu'à `ExecuteReaderAsync`.** Le timeout de
   borne de l'agent doit annuler la requête en cours, pas seulement cesser de
   l'attendre (`lang/csharp.md` §7.4).
5. **`Content` est lu en `Untrusted`**, jamais en `string` exposée.
6. **Les requêtes vivent dans `sql/*.sql`**, chargées au démarrage ; le test L1
   les analyse comme du texte.
7. **Migrations = SQL brut rejoué par DbUp**, scripts nommés `NNNN_*.sql`,
   exécutés par une commande explicite (`{AppName} migrate`), jamais au démarrage
   d'un `run` : une application qui migre sa base en démarrant modifie le schéma
   pendant qu'une eval le lit.
8. **Rôle vérifié au démarrage** : `SHOW transaction_read_only` = `on` et
   `current_user` = `rag_ro` pour le pool de lecture, sinon
   `[DATA_ROLE_NOT_READONLY]`, fail-fast.

---

## 6. Commande de smoke

Marqué `network` (base de test requise), 0 token — le vecteur de requête est
fixé (`[0.01] × D`), pas un appel au provider :

```bash
cd workspace/src/{AppName}
dotnet build -warnaserror
dotnet run --project . -- migrate --connection-name RagIngest
dotnet run --project . -- smoke retrieval --index {index_slug}
#   1. SELECT extversion FROM pg_extension WHERE extname='vector'      → >= 0.8
#   2. atttypmod de rag.chunks.embedding == EmbeddingDims               → sinon [RETRIEVAL_DIMS_MISMATCH]
#   3. index chunks_embedding_hnsw et chunks_tsv_gin présents
#   4. EXPLAIN de sql/vector_search.sql contient "Index Scan using chunks_embedding_hnsw"
#   5. current_user == rag_ro et transaction_read_only == on
#   6. rag.index_meta.index_hash == IndexHash.Compute()               → sinon [RETRIEVAL_INDEX_STALE]
dotnet test --filter "Category!=network"
```

Smoke Timeout : 120 s (la première compilation domine).

---

## 7. Pièges connus

Les pièges 1 à 13 de `vectorstore/pgvector.md` §7 valent ici (distance ≠
similarité, opérateur hors classe d'index = scan séquentiel, filtre + HNSW
avant 0.8, 3072 dims non indexables, modèles mélangés, IVFFlat sur table vide,
`SET` sur un pool, `ef_search < top_k`, tenant fourni par le modèle, TOAST,
corpus hostile, version d'extension au restore, `unaccent` non `IMMUTABLE`).
Propres à .NET :

1. **`UseVector()` oublié.** Le paramètre `Vector` lève à l'exécution (type non
   mappé), pas à la compilation : le test L6 du smoke est le seul à le voir. Le
   builder est écrit une fois, dans `RagDataSources`.
2. **`CREATE EXTENSION vector` dans la même connexion que la première requête.**
   Les types sont chargés à l'ouverture : après avoir créé l'extension, la
   connexion ne connaît pas `vector` avant `ReloadTypes()`. Les migrations
   tournent dans leur propre processus (`migrate`), pas dans le `run`.
3. **`SET LOCAL x = @p`.** PostgreSQL refuse un paramètre lié dans `SET` ; la
   tentation est alors l'interpolation. `set_config(nom, valeur, true)` est la
   seule forme paramétrable, et elle a la même portée transaction.
4. **`float` et `double` confondus.** `Vector` porte des `float` (32 bits),
   comme la colonne. Un embedding reçu en `double[]` puis converti arrondit
   silencieusement ; le client d'embedding rend directement `float`.
5. **Transaction non commitée en lecture.** Sans `CommitAsync`, la transaction
   est annulée au `Dispose` — sans effet en lecture, mais un `set_config` mal
   placé hors transaction (`is_local = false`) fuit sur la connexion suivante du
   pool : le tenant d'un appel devient celui du suivant. Test L6 dédié.
6. **`CultureInfo` courant dans une valeur SQL.** `efSearch.ToString()` sous une
   culture qui groupe les milliers produit `1 000` ; toujours
   `CultureInfo.InvariantCulture`.
7. **Le connecteur Semantic Kernel en preview.** Un agent générateur qui cherche
   « PgVector .NET » tombe sur `Microsoft.SemanticKernel.Connectors.PgVector`
   (preview seulement). Il est `absentByDesign` avec sa raison écrite.

---

## 8. Contrat d'exécution

L'application .NET implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** — mêmes commandes (`run`, `resume`, `health`, `inspect`, `trace`,
`version`), même NDJSON `RunEvent` (`event_schema: "1"`, champs snake_case),
mêmes codes de sortie ; le détail .NET est dans `serving/cli-dotnet.md`. Elle
honore en plus les trois points de `serving/cli.md` §3.5, cités tels quels :

1. `run --json --input-file -` lit l'entrée sur stdin ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'app sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil,
   `tools/{outil}.jsonl`) et le retrieval figé depuis le même dossier
   (`retrieval/{index}.jsonl`), sans aucun appel réseau d'outil ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished` —
   c'est ce que la RETRIEVAL GATE mesure, sans agent.

Lancement : en développement, `dotnet run --project workspace/src/{AppName} --`
suivi de la commande (`… -- run --json --input-file -`) ; en livrable,
l'exécutable publié
(`dotnet publish workspace/src/{AppName} -c Release -r linux-x64 -p:PublishSingleFile=true -p:SelfContained=true`,
puis `…/publish/{AppName}`). Les runners du framework l'invoquent par
`--executor cli` (commande dérivée du langage actif : `dotnet run --project workspace/src/{AppName} --`)
ou par `--executor cmd:<commande>` (typiquement le chemin de l'exécutable publié).

**Ce que cette fiche doit au contrat.** La commande `retrieve` lit le store par
le **même** pool `rag_ro`, le même tenant (`--tenant`) et les mêmes `sql/*.sql`
que l'agent : une RETRIEVAL GATE qui passerait par un autre chemin mesurerait un
autre index. En isolement `mocked`, `VectorLeg` et `LexicalLeg` ne sont **pas**
appelés : le retriever sert `SDDA_EVAL_FIXTURES/retrieval/{index}.jsonl` et
n'ouvre aucune connexion — un `NpgsqlDataSource` ouvert en isolement est un
défaut, testé en L1 avec une chaîne de connexion invalide.
