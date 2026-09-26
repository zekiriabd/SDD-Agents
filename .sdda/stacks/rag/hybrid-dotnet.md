# Stack: hybrid-dotnet (rag)

Stack ID: rag-hybrid-dotnet
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: csharp
Scope: pattern de retrieval **hybride** côté **.NET** — jambe lexicale (FTS PostgreSQL) + jambe vectorielle (pgvector), fusion par **Reciprocal Rank Fusion** en fonction pure C# testée en L1, variante SQL en une requête (même CTE que `rag/hybrid.md` §3.2), embeddings **Voyage** par un `IEmbeddingGenerator` REST maison dont l'`input_type` est obligatoire. Implémente l'entité **RETRIEVER** du domain model. Suppose `lang/csharp.md` et `vectorstore/pgvector-dotnet.md`. Pas de `.libs.json` : aucune dépendance propre — le store porte Npgsql/Pgvector, le framework porte `Microsoft.Extensions.AI` et `Microsoft.Extensions.Http.Resilience`.

---

## 1. Rôle et périmètre

Le pendant .NET de `rag/hybrid.md`, qui reste la référence du **pourquoi** :
les deux jambes échouent sur des cas complémentaires (le vectoriel rate
`INV-2024-0093`, le lexical rate la paraphrase), la fusion coûte une requête SQL
et aucun token, et c'est typiquement le plus grand gain de recall par euro après
le chunking. Rien de cela ne dépend du langage ; ce qui en dépend, et que cette
fiche fixe, c'est **où vit la fusion** et **comment l'embedding de la requête
est obtenu**.

> **La fusion est une fonction, pas une propriété du store.** Des voies .NET
> existent qui font l'hybride « toutes seules » — `CommunityToolkit.VectorData.PgVector`
> calcule RRF dans son SQL. On ne les prend pas par défaut
> (`vectorstore/pgvector-dotnet.md` §2.1) : une fusion qu'on ne peut pas appeler
> sans base n'est pas testable en L1, et le diagnostic « quelle jambe a remonté
> le document » — la donnée qui règle les poids — disparaît dans un score.

Périmètre : les deux jambes, la fusion (pondérée), le client d'embedding, la
structure générée, la mesure (RETRIEVAL GATE, sans agent). **Hors périmètre** :
le chunking (mesuré dans le `retrieval-contract`), le reranking (`RerankEnabled`,
après fusion), la génération et ses métriques.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `rag-hybrid-dotnet` |
| **Famille** | RAG · pattern de requête (RETRIEVER) |
| **Dépend de** | `vectorstore/pgvector-dotnet.md` · `embedding/voyage.md` · `lang/csharp.md` |
| **Paramètres STACK.md** | `HybridEnabled: true`, `HybridWeights: { vector: 0.6, lexical: 0.4 }`, `RetrievalTopK: 8`, `RerankEnabled`, `RerankTopN` — identiques à `rag/hybrid.md` §2 |
| **Paramètres du retrieval-contract** | `candidates_per_leg` (40), `rrf_k` (60), `LexicalLanguage` (`french`), `LexicalEngine` (`pg-fts` par défaut) |
| **Embedding** | `IEmbeddingGenerator<string, Embedding<float>>` (Microsoft.Extensions.AI) implémenté par `VoyageEmbeddingGenerator` sur `HttpClient` — §3.3 |

La franchise de `rag/hybrid.md` §2.1 vaut ici : `ts_rank_cd` **n'est pas
BM25**. Un vrai BM25 exige une extension serveur (`pg_search`) ; le choix
`LexicalEngine` est une mesure consignée dans le contrat, pas une préférence.

### 2.1 Pas de SDK Voyage officiel en .NET

Voyage ne publie de SDK officiel qu'en Python et en TypeScript. En .NET, les
paquets existants (`VoyageAI` de tryAGI, `VoyageAI.NET`) sont **communautaires**,
et Voyage les liste comme tels. Le code généré n'en dépend pas : il appelle
`POST https://api.voyageai.com/v1/embeddings` par un client d'une soixantaine de
lignes (§3.3). Et surtout **pas** par un client « compatible OpenAI » : il
omettrait `input_type` sans rien dire, c'est-à-dire l'erreur silencieuse que
`embedding/voyage.md` §3 interdit — même espace vectoriel, recall dégradé de
plusieurs points, aucun signal.

---

## 3. Mapping des concepts SDD_Agents → idiomes .NET

| Concept | Idiome | Notes |
|---|---|---|
| **RETRIEVER** `RetrieveAsync(query, topK, ctx, ct)` | `embed(query) → (VectorLeg ∥ LexicalLeg) → Rrf.Fuse → [rerank] → topK` | jambes concurrentes (`Task.WhenAll`) ou une requête SQL (§3.2) |
| `hybridWeights` | `Score(d) = Σ w_leg / (rrf_k + rank_leg(d))` | `w_vector + w_lexical = 1`, vérifié |
| `candidates_per_leg` | `LIMIT` de chaque jambe, **le même** pour les deux | cf. `rag/hybrid.md` §7.2 |
| `citationMode: required` | chaque `RetrievedChunk` garde `ChunkId`, `DocId`, `Citation` ; un chunk vu par deux jambes garde une seule entrée | `citation_resolve_rate` mesuré sur le fusionné |
| **Diagnostic** | `Provenance : IReadOnlyDictionary<string, int?>` (`{"vector": 3, "lexical": null}`) | jusqu'au span et au rapport L3 |
| **Trust** | `Content : Untrusted` | P8 |
| **TRACE SPAN** `sdda.retrieve {index_id}` | attributs de `rag/hybrid.md` §3 (`sdda.retrieval.legs`, `rrf_k`, `weights`, `result.ids`, `result.scores`, `result.provenance`) | émis par le helper `Retrieval()` de `observability/otel-genai-dotnet.md` |
| **indexHash** | inclut `LexicalEngine`, `LexicalLanguage`, `rrf_k`, `weights` | changer un poids périme les baselines L3 — voulu |

### 3.1 La fusion — fonction pure, testée en L1

```csharp
// retrieval/{index_slug}/Rrf.cs
namespace {AppName}.Retrieval.{IndexSlug};

public sealed record FusedHit(string ChunkId, double Score, IReadOnlyDictionary<string, int?> Provenance);

public static class Rrf
{
    /// <summary>Reciprocal Rank Fusion pondérée. Déterministe : à égalité de score,
    /// l'ordre est celui de ChunkId en comparaison ORDINALE (stable entre runs et entre machines).</summary>
    public static IReadOnlyList<FusedHit> Fuse(
        IReadOnlyDictionary<string, IReadOnlyList<string>> legs,   // {"vector": [chunkId…] ordonnés, "lexical": […]}
        IReadOnlyDictionary<string, double> weights,               // {"vector": 0.6, "lexical": 0.4}
        int topK,
        int k = 60)
    {
        if (Math.Abs(weights.Values.Sum() - 1.0) > 1e-9)
            throw new ArgumentException("[RETRIEVAL_WEIGHTS_INVALID] weights must sum to 1.0");

        var scores = new Dictionary<string, double>(StringComparer.Ordinal);
        var provenance = new Dictionary<string, Dictionary<string, int?>>(StringComparer.Ordinal);
        foreach (var (leg, ranked) in legs)
        {
            var w = weights[leg];
            for (var i = 0; i < ranked.Count; i++)
            {
                var id = ranked[i];
                scores[id] = scores.GetValueOrDefault(id) + w / (k + i + 1);
                if (!provenance.TryGetValue(id, out var p))
                    provenance[id] = p = legs.Keys.ToDictionary(name => name, _ => (int?)null, StringComparer.Ordinal);
                p[leg] ??= i + 1;   // premier rang vu dans cette jambe
            }
        }
        return scores
            .OrderByDescending(kv => kv.Value)
            .ThenBy(kv => kv.Key, StringComparer.Ordinal)
            .Take(topK)
            .Select(kv => new FusedHit(kv.Key, kv.Value, provenance[kv.Key]))
            .ToList();
    }
}
```

C'est la traduction ligne à ligne de `rag/hybrid.md` §3.1, et le test L1
`RrfTests` rejoue **les mêmes cas** (rangs connus, égalités, poids invalides,
document présent dans les deux jambes qui remonte). Deux implémentations de la
référence qui divergent sur un cas d'égalité rendraient deux `result_ids`
différents pour la même requête — et le rapport L3 comparerait deux index qui
sont pourtant le même.

`StringComparer.Ordinal` n'est pas un détail : l'ordre par défaut de .NET est
culturel, et deux machines de CI de cultures différentes classeraient autrement
deux chunks à égalité.

### 3.2 Variante pgvector en une requête

Le SQL est **celui de `rag/hybrid.md` §3.2**, texte identique à la syntaxe des
paramètres près (Npgsql : `@nom` au lieu de `%(nom)s`) :

```sql
-- retrieval/{index_slug}/sql/hybrid_rrf.sql
WITH q AS (
  SELECT @q_vec::vector AS v,
         websearch_to_tsquery(@lang::regconfig, rag.immutable_unaccent(@q_text)) AS tsq
),
vec AS (
  SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> q.v) AS rnk
  FROM   rag.chunks c, q
  WHERE  c.tenant_id = current_setting('app.tenant_id')::uuid
    AND  c.pii_scan_status <> 'flagged'
  ORDER  BY c.embedding <=> q.v
  LIMIT  @n
),
lex AS (
  SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.content_tsv, q.tsq) DESC) AS rnk
  FROM   rag.chunks c, q
  WHERE  c.tenant_id = current_setting('app.tenant_id')::uuid
    AND  c.pii_scan_status <> 'flagged'
    AND  c.content_tsv @@ q.tsq
  ORDER  BY ts_rank_cd(c.content_tsv, q.tsq) DESC
  LIMIT  @n
),
fused AS (
  SELECT COALESCE(vec.id, lex.id) AS id,
         COALESCE(@w_vec / (@rrf_k + vec.rnk), 0)
       + COALESCE(@w_lex / (@rrf_k + lex.rnk), 0) AS score,
         vec.rnk AS vector_rank, lex.rnk AS lexical_rank
  FROM   vec FULL OUTER JOIN lex ON vec.id = lex.id
)
SELECT c.id, c.document_id, c.chunk_index, c.heading_path, c.char_start, c.char_end, c.content,
       f.score, f.vector_rank, f.lexical_rank
FROM   fused f JOIN rag.chunks c ON c.id = f.id
ORDER  BY f.score DESC, c.id
LIMIT  @top_k;
```

`@w_vec`, `@w_lex` et `@rrf_k` sont passés en `double` (`NpgsqlDbType.Double`) :
en `int`, `@w_vec / (@rrf_k + rnk)` serait une division entière et vaudrait 0
partout. La fonction C# de §3.1 reste la référence ; la version SQL est
vérifiée **contre elle** en L6 sur un jeu fixe (`[RETRIEVAL_FUSION_MISMATCH]`
sinon).

Pourquoi garder la fusion C# alors que la requête SQL existe : la requête unique
gagne un aller-retour ; la fonction C# gagne la testabilité sans base et la
lisibilité de la provenance. Le défaut du générateur est la **voie deux
jambes + `Rrf.Fuse`** ; la requête unique est une optimisation de latence à
mesurer (L7), activée par le contrat.

### 3.3 Embeddings Voyage — `input_type` obligatoire

```csharp
// retrieval/embedding/VoyageEmbeddingGenerator.cs
using System.Net.Http.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.AI;

namespace {AppName}.Retrieval.Embedding;

public enum VoyageInputType { Query, Document }

/// <summary>Client REST Voyage (POST /v1/embeddings). Aucun SDK officiel .NET n'existe.
/// L'input_type est fixé À LA CONSTRUCTION, sans valeur par défaut : une instance
/// "query" et une instance "document" sont deux services distincts (clés DI).</summary>
public sealed class VoyageEmbeddingGenerator(HttpClient http, string model, int dimensions, VoyageInputType inputType)
    : IEmbeddingGenerator<string, Embedding<float>>
{
    public async Task<GeneratedEmbeddings<Embedding<float>>> GenerateAsync(
        IEnumerable<string> values, EmbeddingGenerationOptions? options = null, CancellationToken cancellationToken = default)
    {
        if (options?.AdditionalProperties?.ContainsKey("input_type") == true)
            throw new ArgumentException("[RETRIEVAL_INPUT_TYPE_OVERRIDE] input_type is fixed at construction");

        var request = new VoyageRequest(values.ToList(), model,
            inputType == VoyageInputType.Query ? "query" : "document", dimensions);
        using var response = await http.PostAsJsonAsync("v1/embeddings", request, VoyageJson.Default.VoyageRequest, cancellationToken);
        response.EnsureSuccessStatusCode();   // 401/429/5xx : typés par le handler de résilience, jamais avalés
        var body = await response.Content.ReadFromJsonAsync(VoyageJson.Default.VoyageResponse, cancellationToken)
                   ?? throw new InvalidOperationException("[RETRIEVAL_EMBEDDING_EMPTY]");

        var ordered = body.Data.OrderBy(d => d.Index).ToList();   // l'API rend `index` : ne pas supposer l'ordre
        if (ordered.Count != request.Input.Count || ordered.Any(d => d.Embedding.Length != dimensions))
            throw new InvalidOperationException("[RETRIEVAL_DIMS_MISMATCH]");

        return new GeneratedEmbeddings<Embedding<float>>(ordered.Select(d => new Embedding<float>(d.Embedding)))
        {
            Usage = new UsageDetails { InputTokenCount = body.Usage.TotalTokens },
        };
    }

    public object? GetService(Type serviceType, object? serviceKey = null) =>
        serviceType.IsInstanceOfType(this) ? this : null;

    public void Dispose() { }   // le HttpClient appartient à IHttpClientFactory
}

public sealed record VoyageRequest(
    [property: JsonPropertyName("input")] IReadOnlyList<string> Input,
    [property: JsonPropertyName("model")] string Model,
    [property: JsonPropertyName("input_type")] string InputType,
    [property: JsonPropertyName("output_dimension")] int OutputDimension);

public sealed record VoyageResponse(
    [property: JsonPropertyName("data")] IReadOnlyList<VoyageItem> Data,
    [property: JsonPropertyName("usage")] VoyageUsage Usage);
public sealed record VoyageItem([property: JsonPropertyName("embedding")] float[] Embedding, [property: JsonPropertyName("index")] int Index);
public sealed record VoyageUsage([property: JsonPropertyName("total_tokens")] long TotalTokens);

[JsonSerializable(typeof(VoyageRequest))]
[JsonSerializable(typeof(VoyageResponse))]
internal sealed partial class VoyageJson : JsonSerializerContext;
```

Pourquoi l'`input_type` est dans le **constructeur** et non dans
`GenerateAsync` : la signature de `GenerateAsync` est celle de l'interface
`IEmbeddingGenerator`, qui n'a pas de paramètre `input_type`. Le passer par
`EmbeddingGenerationOptions.AdditionalProperties` le rendrait **facultatif** —
exactement ce que `embedding/voyage.md` §3 interdit. En le fixant à la
construction, sans valeur par défaut, on garde l'interface standard (donc
`UseOpenTelemetry()` et le span `embeddings` gratuits, cf.
`observability/otel-genai-dotnet.md`) **et** l'impossibilité de l'oublier :
l'ingestion reçoit l'instance clé `"document"`, le retriever l'instance clé
`"query"`, et une tentative de surcharge par options lève.

Enregistrement (dans `app/`, par `dev-backend`) :

```csharp
services.AddHttpClient("voyage", c => c.BaseAddress = new Uri("https://api.voyageai.com/"))
        .AddStandardResilienceHandler();   // ingestion idempotente : le seul endroit où un retry agressif est légitime
services.AddKeyedSingleton<IEmbeddingGenerator<string, Embedding<float>>>("query", (sp, _) =>
    new VoyageEmbeddingGenerator(sp.GetRequiredService<IHttpClientFactory>().CreateClient("voyage"),
                                 opts.EmbeddingModel, opts.EmbeddingDims, VoyageInputType.Query)
        .AsBuilder().UseOpenTelemetry(sourceName: "sdda").Build());
// idem clé "document" avec VoyageInputType.Document
```

La clé d'API (`EMBEDDING_API_KEY`) est posée en en-tête `Authorization: Bearer`
par un `DelegatingHandler` qui la lit dans `IOptions` — jamais dans le code,
jamais en argument de ligne de commande.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── retrieval/
│   ├── embedding/
│   │   ├── VoyageEmbeddingGenerator.cs     # §3.3 — REST, input_type fixé à la construction
│   │   └── VoyageAuthHandler.cs            # en-tête Bearer depuis IOptions
│   └── {index_slug}/
│       ├── Retriever.cs                    # RetrieveAsync : embed(query) → jambes → Rrf.Fuse → [rerank] → topK ; span sdda.retrieve
│       ├── Rrf.cs                          # fusion pure — L1
│       ├── VectorLeg.cs  LexicalLeg.cs     # délèguent au store (vectorstore/pgvector-dotnet.md)
│       ├── QueryPrep.cs                    # unaccent cohérent avec l'ingestion, préservation des identifiants
│       ├── FrozenRetriever.cs              # isolement L4 : sert SDDA_EVAL_FIXTURES/retrieval/{index}.jsonl (§8)
│       └── sql/hybrid_rrf.sql              # variante une requête (§3.2)
└── tests/
    └── retrieval/
        ├── RrfTests.cs                     # L1 : mêmes cas que rag/hybrid.md (rangs, égalités ordinales, poids invalides)
        ├── QueryPrepTests.cs               # L1 : 'INV-2024-0093' survit à la préparation lexicale
        ├── VoyageEmbeddingGeneratorTests.cs# L1 : HttpMessageHandler de test — la requête "query" porte input_type=query, "document" porte document ; override refusé
        └── HybridSqlNetworkTests.cs        # L6 [Trait("Category","network")] : SQL == Rrf.Fuse sur jeu fixe

workspace/pipeline/suites/retrieval-{index_slug}.yaml   # L3 — qa-evals, jamais dev-*
```

---

## 5. Conventions imposées

Les règles de `rag/hybrid.md` §5.1 s'appliquent telles quelles (jambes
parallèles ou une requête, `candidates_per_leg ≥ 3 × top_k`, RRF sur les rangs,
déduplication par `chunk_id`, provenance conservée, paramètres dans le contrat
et dans `indexHash`, requête lexicale préparée). Propres à .NET :

1. **`Task.WhenAll` pour les deux jambes, chacune sur sa propre connexion.** Une
   `NpgsqlConnection` n'exécute qu'une commande à la fois : deux jambes sur la
   même connexion sont séquentielles, et la latence P95 double sans erreur.
2. **Comparaisons ordinales partout** (`StringComparer.Ordinal`) dans la fusion
   et le tri — jamais la culture courante.
3. **Paramètres de poids en `double`**, jamais en `decimal` ni en `int` (§3.2).
4. **Deux instances d'embedding, deux clés** (`"query"`, `"document"`), aucune
   instance sans clé : un `GetRequiredService<IEmbeddingGenerator<…>>()` non clé
   doit échouer au démarrage.
5. **Le span `embeddings` vient de `UseOpenTelemetry()`**, le span
   `sdda.retrieve` du helper maison ; le code de retrieval n'appelle pas
   `ActivitySource` directement.

La mesure est celle de `rag/hybrid.md` §5.2, sans changement : ablation par
jambe, par tag, grille de poids sur **golden** (jamais holdout), profondeur,
vérité document, citations — 0 token hors embedding des requêtes, et jugée par
`python .sdda/sdda.py run-retrieval-eval`, qui appelle la commande `retrieve`
de §8.

---

## 6. Commande de smoke

```bash
cd workspace/src/{AppName}
dotnet build -warnaserror
dotnet test --filter "FullyQualifiedName~Retrieval&Category!=network"      # L1, 0 token, < 10 s
# base de test requise (network), vecteur de requête fixé — aucun appel Voyage :
dotnet run --project . -- smoke retrieval --index {index_slug} --query "facture INV-2024-0093 montant" --fixed-vector
#   → affiche vector / lexical / fused avec rangs et provenance
#   → vérifie : deux jambes > 0 candidat ; fused == Rrf.Fuse(vector, lexical) ; latence < LatencyP95Target/4
#   → exit 0 ; exit 3 si une jambe est vide ([RETRIEVAL_LEG_EMPTY]) ; exit 4 si SQL ≠ C# ([RETRIEVAL_FUSION_MISMATCH])
```

Puis, en L3 (coûte l'embedding des requêtes du golden, pas de LLM) — le runner
du framework, qui lance l'application par sa CLI :

```bash
python .sdda/sdda.py run-retrieval-eval --executor cli …
```

---

## 7. Pièges connus

Les pièges 1 à 12 de `rag/hybrid.md` §7 valent ici (`rrf_k` trop petit,
profondeurs de jambe inégales, identifiants découpés par le parser FTS, langue
de stemming incohérente, `unaccent` d'un seul côté, deux fraîcheurs d'index,
chunk vs document, doublons parent-child, reranker avant fusion, poids réglés
sur le holdout, empoisonnement lexical, GIN absent). Propres à .NET :

1. **Division entière dans le SQL.** `@w_vec` passé en `int` (ou poids
   arrondis) → tous les scores à 0, ordre = `c.id` : le résultat « marche » et
   ne veut rien dire. Test L6 sur des scores attendus non nuls.
2. **Tri culturel.** `OrderBy(x => x.Key)` sans comparateur classe `é` et `e`
   selon la culture de la machine ; deux runners rendent deux ordres à égalité.
3. **Un seul `IEmbeddingGenerator` enregistré.** Le plus probable pour un
   générateur de code : une seule instance, `input_type` en dur à `document`,
   et la recherche embarque ses requêtes comme des documents. Le recall baisse
   de quelques points, sans erreur. D'où les deux clés et le test L1 qui lit le
   corps de la requête HTTP.
4. **Un client « compatible OpenAI » pour Voyage.** Même symptôme : `input_type`
   omis. Interdit par `embedding/voyage.md` §3.1.
5. **L'ordre de `data[]` supposé.** L'API rend un `index` par vecteur ; trier
   dessus coûte une ligne et évite d'associer un vecteur au mauvais chunk.
6. **Un retry sur l'embedding de requête dans un run.** Il est idempotent, mais
   il consomme la borne de latence de l'agent ; la politique de résilience de
   l'ingestion (agressive) n'est pas celle du retriever (courte).
7. **Jambes concurrentes sur une connexion partagée** — convention 1.

---

## 8. Contrat d'exécution

L'application .NET implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** — mêmes commandes, même NDJSON `RunEvent` (`event_schema: "1"`,
snake_case), mêmes codes de sortie ; détail .NET dans `serving/cli-dotnet.md`.
Elle honore en plus les trois points de `serving/cli.md` §3.5, cités tels quels :

1. `run --json --input-file -` lit l'entrée sur stdin ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'app sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil,
   `tools/{outil}.jsonl`) et le retrieval figé depuis le même dossier
   (`retrieval/{index}.jsonl`), sans aucun appel réseau d'outil ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished` —
   c'est ce que la RETRIEVAL GATE mesure, sans agent.

Lancement : `dotnet run --project workspace/src/{AppName} --` en développement,
l'exécutable publié (`dotnet publish … -p:PublishSingleFile=true -p:SelfContained=true`)
en livrable. Les runners l'invoquent par `--executor cli` (commande dérivée du
langage actif : `dotnet run --project workspace/src/{AppName} --`) ou par
`--executor cmd:<commande>`.

**Ce que cette fiche doit au contrat — c'est elle qui porte `retrieve`.**

- `retrieve` appelle **le même** `Retriever.RetrieveAsync` que l'agent, avec le
  tenant de `--tenant`, et n'appelle **aucun** `IChatClient` : un `chat` dans la
  trace d'un `retrieve` est un défaut (test L1 avec un `IChatClient` qui lève).
- `--k N` fixe le `topK` servi (défaut : le `topK` du contrat de retrieval) ;
  `candidates_per_leg` **ne** suit **pas** `--k` — il reste celui du contrat,
  sinon le recall@k mesuré ne serait pas celui du système livré.
- L'événement émis est exactement
  `{"event":"retrieval","index_id":…,"result_ids":[…],"chunk_ids":[…],"scores":[…]}`
  (`result_ids` = le `DocumentId` de chaque chunk servi — la gate mesure au niveau
  document, `serving/cli.md` §3.5 ; `chunk_ids` = les `ChunkId` ; scores = score
  **fusionné**, même ordre), suivi de `run_finished`.
  La provenance par jambe va dans la trace (`sdda.retrieval.result.provenance`),
  pas dans l'événement : le contrat d'événement est commun aux cinq langages.
- En `SDDA_EVAL_ISOLATION=mocked`, `FrozenRetriever` remplace `Retriever` et
  sert `SDDA_EVAL_FIXTURES/retrieval/{index}.jsonl` ; ni Voyage ni PostgreSQL ne
  sont contactés. Un index sans fixture en isolement → code `8`
  (`[CONFIG_INVALID]`) au démarrage.
