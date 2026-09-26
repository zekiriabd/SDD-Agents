# Stack: hybrid-jvm (rag)

Stack ID: rag-hybrid-jvm
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin, java
Scope: pattern de retrieval **hybride** sur la JVM (Kotlin et Java) — jambe lexicale PostgreSQL FTS (`tsvector`) + jambe vectorielle pgvector, fusion par Reciprocal Rank Fusion **en fonction pure testée L1**, intégration au RAG modulaire de Spring AI (`DocumentRetriever`, `DocumentJoiner`, `RetrievalAugmentationAdvisor` de `spring-ai-rag`), client d'embedding **Voyage REST maison** avec `input_type` obligatoire. Implémente l'entité **RETRIEVER**. Pendant JVM de `rag/hybrid.md` : mêmes paramètres, même CTE, même protocole de mesure. Suppose `vectorstore/pgvector-jvm.md` et `framework/spring-ai.md`. Pas de `.libs.json` : `spring-ai-rag` est porté par le catalogue du framework (capability `rag`), le store par `pgvector-jvm.libs.json`.

---

## 1. Rôle et périmètre

Les raisons du pattern sont celles de `rag/hybrid.md` §1 : la jambe
vectorielle trouve les paraphrases et rate les identifiants exacts
(`INV-2024-0093`), la jambe lexicale fait l'inverse ; les fusionner coûte une
requête SQL et aucun token. Elles ne dépendent pas du langage.

**Ce que Spring AI ne fournit pas, dit d'entrée** (vérifié sur les jars 2.0.1) :

| Besoin | Spring AI 2.0.1 | Ce que cette fiche fait |
|---|---|---|
| recherche hybride sur PostgreSQL | **absente** : `PgVectorStore` ne contient ni `tsvector`, ni `ts_rank`, ni mode hybride | deux jambes écrites en SQL sur `rag.chunks` (`vectorstore/pgvector-jvm.md`) |
| fusion par rang (RRF) | **absente** : le seul `DocumentJoiner` livré est `ConcatenationDocumentJoiner`, qui concatène et dédoublonne sans fusionner par rang | `RrfFusion` — fonction pure — et `RrfDocumentJoiner` |
| embeddings Voyage | **aucun module** (`spring-ai-voyage*` absent de la BOM 2.0.1) et **aucun SDK Java officiel** Voyage | `VoyageEmbeddingClient` REST (~60 lignes) + adaptateur `EmbeddingModel` |
| pipeline RAG modulaire | `RetrievalAugmentationAdvisor`, `DocumentRetriever`, `DocumentJoiner`, `QueryExpander`, `ContextualQueryAugmenter` (`spring-ai-rag`) | utilisé tel quel, avec nos retrievers et notre joiner |

Couvert : les deux jambes, la fusion, l'intégration Spring AI, le client
d'embedding, la mesure (G4, sans agent), la structure générée, les pièges.
**Hors périmètre** : chunking (décidé dans le `retrieval-contract` par
mesure), reranking (`rerank/*.md`, appliqué **après** la fusion), génération
et ses métriques.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `rag-hybrid-jvm` |
| **Famille** | RAG · pattern de requête (RETRIEVER) |
| **Dépend de** | `vectorstore/pgvector-jvm.md` · `embedding/voyage.md` · `framework/spring-ai.md` · `lang/kotlin.md` ou `lang/java.md` |
| **Bibliothèques** | `org.springframework.ai:spring-ai-rag` (BOM `spring-ai-bom` 2.0.1) — catalogue du framework, capability `rag` ; `spring-boot-starter-restclient` (client Voyage) |
| **Paramètres STACK.md** | `HybridEnabled: true`, `HybridWeights: { vector: 0.6, lexical: 0.4 }`, `RetrievalTopK: 8`, `RerankEnabled`, `RerankTopN` |
| **Paramètres du retrieval-contract** | `candidates_per_leg` (40), `rrf_k` (60), `LexicalLanguage` (`french`), `LexicalEngine` (`pg-fts` \| `pg-bm25`) |

La franchise de `rag/hybrid.md` §2.1 vaut ici : `ts_rank_cd` **n'est pas
BM25**. `LexicalEngine: pg-fts` est le défaut ; `pg-bm25` (extension
`pg_search`) seulement si la mesure le justifie. `rank-bm25` (en mémoire)
n'a pas d'équivalent retenu en JVM : un index lexical en mémoire reconstruit
à chaque démarrage d'un exécutable lancé une fois par item d'eval coûterait
plus que la requête.

---

## 3. Mapping des concepts SDD_Agents → idiomes JVM

| Concept | Idiome | Notes |
|---|---|---|
| **RETRIEVER** `retrieve(query, topK, tenant)` | `HybridRetriever` implémente `DocumentRetriever` : `embed(query, QUERY)` ∥ `LexicalLeg` → `RrfFusion.fuse` → [rerank] → `topK` | les deux jambes s'exécutent **concurremment** (threads virtuels en Java, `coroutineScope { async }` en Kotlin) |
| `hybridWeights` | RRF pondéré : `score(d) = Σ_leg w_leg / (rrf_k + rank_leg(d))` | `w_vector + w_lexical = 1`, sinon `[RETRIEVAL_WEIGHTS_INVALID]` |
| `candidates_per_leg` | `LIMIT` de chaque jambe (40 = 5 × `topK`) | même profondeur sur les deux jambes (`rag/hybrid.md` §7.2) |
| `citationMode: required` | chaque `Document` porte en métadonnées `chunk_id`, `document_id`, `citation` | `Document.getScore()` = score RRF |
| **Trust** | le texte du chunk est `Untrusted` ; mis dans le contexte par `UntrustedRenderer` (balisage) | P8 — y compris via `ContextualQueryAugmenter` (§3.4) |
| **Diagnostic** | métadonnée `provenance = {vector_rank, lexical_rank}` | quelle jambe a remonté le document — la donnée qui règle les poids |
| **TRACE SPAN** | `sdda.retrieve {index_id}` avec `sdda.retrieval.legs`, `.rrf_k`, `.weights`, `.candidates_per_leg`, `.result.ids`, `.result.scores`, `.result.doc_ids`, `.result.provenance` | noms dans `app/tracing/Semconv` (`observability/otel-genai-jvm.md`) |
| **indexHash** | inclut `LexicalEngine`, `LexicalLanguage`, `rrf_k`, `weights` | changer les poids périme les baselines L3 |

### 3.1 La fusion — fonction pure, testée en L1

**Java**

```java
// retrieval/{index}/RrfFusion.java — package {package}.retrieval.{index}
public final class RrfFusion {
    public record Fused(String chunkId, double score, Map<String, Integer> provenance) {}

    private RrfFusion() {}

    /** RRF pondérée. Déterministe : à score égal, ordre de chunkId (stable entre runs). */
    public static List<Fused> fuse(Map<String, List<String>> legs, Map<String, Double> weights, int k, int topK) {
        double sum = weights.values().stream().mapToDouble(Double::doubleValue).sum();
        if (Math.abs(sum - 1.0) > 1e-9) {
            throw new IllegalArgumentException("[RETRIEVAL_WEIGHTS_INVALID] weights must sum to 1.0");
        }
        Map<String, Double> scores = new HashMap<>();
        Map<String, Map<String, Integer>> provenance = new HashMap<>();
        legs.forEach((leg, ranked) -> {
            double w = weights.get(leg);
            for (int rank = 1; rank <= ranked.size(); rank++) {
                String id = ranked.get(rank - 1);
                scores.merge(id, w / (k + rank), Double::sum);
                provenance.computeIfAbsent(id, x -> new TreeMap<>()).putIfAbsent(leg, rank);
            }
        });
        return scores.entrySet().stream()
                .sorted(Comparator.<Map.Entry<String, Double>>comparingDouble(Map.Entry::getValue).reversed()
                        .thenComparing(Map.Entry::getKey))
                .limit(topK)
                .map(e -> new Fused(e.getKey(), e.getValue(), provenance.get(e.getKey())))
                .toList();
    }
}
```

**Kotlin**

```kotlin
// retrieval/{index}/RrfFusion.kt — package {package}.retrieval.{index}
object RrfFusion {
    data class Fused(val chunkId: String, val score: Double, val provenance: Map<String, Int>)

    /** RRF pondérée. Déterministe : à score égal, ordre de chunkId (stable entre runs). */
    fun fuse(legs: Map<String, List<String>>, weights: Map<String, Double>, k: Int = 60, topK: Int): List<Fused> {
        require(kotlin.math.abs(weights.values.sum() - 1.0) <= 1e-9) { "[RETRIEVAL_WEIGHTS_INVALID] weights must sum to 1.0" }
        val scores = HashMap<String, Double>()
        val provenance = HashMap<String, MutableMap<String, Int>>()
        for ((leg, ranked) in legs) {
            val w = weights.getValue(leg)
            ranked.forEachIndexed { i, id ->
                scores.merge(id, w / (k + i + 1), Double::plus)
                provenance.getOrPut(id) { sortedMapOf() }.putIfAbsent(leg, i + 1)
            }
        }
        return scores.entries
            .sortedWith(compareByDescending<Map.Entry<String, Double>> { it.value }.thenBy { it.key })
            .take(topK)
            .map { Fused(it.key, it.value, provenance.getValue(it.key)) }
    }
}
```

`object` sans état en Kotlin : c'est une fonction pure, pas un singleton
porteur d'état (`lang/kotlin.md` §5.1 l'autorise pour ce cas seulement).
Pourquoi RRF et pas une combinaison de scores : `rag/hybrid.md` §3.1 — les
scores bruts (cosinus, `ts_rank_cd`) ne sont pas comparables ; RRF n'utilise
que les rangs.

Test L1 (`tests/retrieval/RrfFusionTest`) : cas connus, égalités stables,
poids invalides, document présent dans les deux jambes qui remonte, et
**le même jeu de cas que `tests/retrieval/test_fusion.py` en Python** — la
fusion doit donner les mêmes rangs dans les deux langages, sinon deux
baselines ne sont pas comparables.

### 3.2 La jambe lexicale et la requête en une passe

```sql
-- retrieval/{index}/sql/lexical.sql — chargé comme ressource, paramètres nommés JdbcClient
SELECT c.id, c.document_id, c.chunk_index, c.heading_path, c.char_start, c.char_end, c.content,
       ts_rank_cd(c.content_tsv, q.tsq) AS score
FROM   rag.chunks c,
       (SELECT websearch_to_tsquery(CAST(:lang AS regconfig), rag.immutable_unaccent(:qtext)) AS tsq) q
WHERE  c.tenant_id = current_setting('app.tenant_id')::uuid
  AND  c.pii_scan_status <> 'flagged'
  AND  c.content_tsv @@ q.tsq
ORDER  BY ts_rank_cd(c.content_tsv, q.tsq) DESC, c.id
LIMIT  :n
```

La variante **une requête** est la CTE de `rag/hybrid.md` §3.2, à
l'identique, avec les paramètres nommés de `JdbcClient` (`:q_vec`, `:lang`,
`:q_text`, `:n`, `:w_vec`, `:w_lex`, `:rrf_k`, `:top_k`) à la place de
`%(…)s`. Comme en Python, **la fusion pure de §3.1 reste la référence** : la
variante SQL est vérifiée contre elle en L6 (`@Tag("network")`) sur un jeu
fixe, et un écart est `[RETRIEVAL_FUSION_MISMATCH]`. La garder hors SQL par
défaut préserve le test L1 sans base.

`CAST(:lang AS regconfig)` plutôt que `:lang::regconfig` : le `::` suivant un
paramètre nommé est mal découpé par certains analyseurs de paramètres ;
`CAST` lève l'ambiguïté.

### 3.3 Le retriever hybride et l'intégration Spring AI

**Java**

```java
// retrieval/{index}/HybridRetriever.java
public final class HybridRetriever implements DocumentRetriever {
    private final VoyageEmbeddingModel queryEmbeddings;   // lié à input_type = query (§3.5)
    private final VectorLeg vector;
    private final LexicalLeg lexical;
    private final HybridSettings s;                       // poids, rrf_k, candidates_per_leg, topK — du retrieval-contract
    private final RetrievalSpans spans;                   // app/tracing

    // constructeur : injection

    @Override
    public List<Document> retrieve(Query query) {
        TenantId tenant = RunContext.current().tenant();  // identité de l'appelant — jamais une métadonnée de Query
        try (var span = spans.retrieve(s.indexId(), s);
             var exec = Context.taskWrapping(Executors.newVirtualThreadPerTaskExecutor())) {   // contexte OTel propagé (§8.5)
            var vec = exec.submit(() -> vector.search(queryEmbeddings.embed(query.text()), tenant, s.candidatesPerLeg(), s.efSearch()));
            var lex = exec.submit(() -> lexical.search(query.text(), tenant, s.candidatesPerLeg()));
            Map<String, ChunkHit> byId = new HashMap<>();
            List<String> vIds = ids(vec.get(), byId), lIds = ids(lex.get(), byId);
            if (vIds.isEmpty() && lIds.isEmpty()) { span.empty(); return List.of(); }
            List<RrfFusion.Fused> fused = RrfFusion.fuse(
                    Map.of("vector", vIds, "lexical", lIds), s.weights(), s.rrfK(), s.topK());
            span.results(fused, byId);
            return fused.stream().map(f -> toDocument(f, byId.get(f.chunkId()))).toList();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ClassifiedException("RETRIEVAL_INTERRUPTED", e);
        } catch (ExecutionException e) {
            throw ClassifiedException.wrap("RETRIEVAL_LEG_FAILED", e.getCause());
        }
    }
}
```

**Kotlin**

```kotlin
// retrieval/{index}/HybridRetriever.kt
class HybridRetriever(
    private val queryEmbeddings: VoyageEmbeddingModel,   // lié à input_type = query (§3.5)
    private val vector: VectorLeg,
    private val lexical: LexicalLeg,
    private val s: HybridSettings,
    private val spans: RetrievalSpans,
) : DocumentRetriever {

    override fun retrieve(query: Query): List<Document> {
        val tenant = RunContext.current().tenant           // identité de l'appelant — jamais une métadonnée de Query
        return spans.retrieve(s.indexId, s).use { span ->
            val (vHits, lHits) = runBlocking(Dispatchers.IO + span.otel.asContextElement()) {   // pont bloquant imposé par l'interface Spring AI
                val v = async { vector.search(queryEmbeddings.embed(query.text()), tenant, s.candidatesPerLeg, s.efSearch) }
                val l = async { lexical.search(query.text(), tenant, s.candidatesPerLeg) }
                v.await() to l.await()
            }
            val byId = (vHits + lHits).associateBy { it.chunkId }
            val fused = RrfFusion.fuse(
                mapOf("vector" to vHits.map { it.chunkId }, "lexical" to lHits.map { it.chunkId }),
                s.weights, s.rrfK, s.topK,
            )
            span.results(fused, byId)
            fused.map { toDocument(it, byId.getValue(it.chunkId)) }
        }
    }
}
```

`runBlocking` ici est la **seconde** exception à la règle de `lang/kotlin.md`
§5.1 (après le point d'entrée de la CLI) : `DocumentRetriever.retrieve` est
une interface Java synchrone, et Spring AI l'appelle depuis un thread qui
n'est pas une coroutine. La règle reste : un seul pont, dans le retriever,
jamais dans un agent.

**Brancher dans Spring AI** — deux usages, selon la topologie :

```java
// app/Composition.java (dev-backend) — retrieval avant génération, pattern RAG de la TOPOLOGY
RetrievalAugmentationAdvisor rag = RetrievalAugmentationAdvisor.builder()
        .documentRetriever(hybridRetriever)                 // les deux jambes + RRF, déjà fusionnées
        .documentJoiner(new RrfDocumentJoiner(settings))    // ne sert que si un QueryExpander produit plusieurs requêtes
        .queryAugmenter(untrustedContextAugmenter)          // §3.4 — pas le ContextualQueryAugmenter par défaut
        .build();
```

```kotlin
// ou : retrieval en OUTIL de l'agent, si le contrat d'agent le déclare
@Tool(description = SEARCH_KB_DESCRIPTION)   // description = celle du tool-contract, constante générée
fun searchKb(query: String, ctx: ToolContext): List<CitedChunk> =
    hybridRetriever.retrieve(Query(query)).map(CitedChunk::of)
```

`RetrievalAugmentationAdvisor` appelle son `DocumentRetriever` **une fois par
requête** et passe au `DocumentJoiner` une `Map<Query, List<List<Document>>>`.
Avec une seule requête, la fusion a déjà eu lieu dans `HybridRetriever` et le
joiner est un passe-plat. Avec un `MultiQueryExpander`, `RrfDocumentJoiner`
fusionne **par rang** les listes des différentes reformulations (même
`RrfFusion.fuse`, poids égaux) ; `ConcatenationDocumentJoiner` les
concaténerait, et le document classé premier par une reformulation pourrait
finir dixième.

### 3.4 Le contexte : texte non maîtrisé

`ContextualQueryAugmenter` (défaut de l'advisor) insère le texte des
documents dans le message utilisateur **tel quel**. Un chunk contenant
« ignore les consignes précédentes » arrive donc nu dans le contexte. La
fiche impose un `QueryAugmenter` maison qui rend chaque document par
`UntrustedRenderer` (balisage, source, troncature) — même posture que
`wrap_untrusted` en Python. Le gabarit de ce message est un **fragment de
prompt** : il vit dans `prompts/` (ou `rules/`), hashé, jamais en littéral.

### 3.5 Embeddings Voyage — client REST, `input_type` obligatoire

Ni SDK Java officiel (`com.voyageai` absent de Maven Central), ni module Spring
AI : le client est écrit, et c'est ce qui permet de rendre `input_type`
**obligatoire** (`embedding/voyage.md` §3). Un client « compatible OpenAI »
l'omettrait sans rien dire.

API (documentation Voyage, relue le 2026-09-26) : `POST
https://api.voyageai.com/v1/embeddings`, en-tête `Authorization: Bearer
<clé>`, corps `{input: [..], model, input_type: "query"|"document",
output_dimension?, truncation?}`, réponse `{data: [{embedding, index}],
model, usage: {total_tokens}}`, au plus 1 000 textes par appel.

**Java**

```java
// retrieval/embedding/VoyageEmbeddingClient.java
public final class VoyageEmbeddingClient {
    public enum InputType { QUERY, DOCUMENT }

    private final RestClient http;   // baseUrl + Authorization posés par la composition depuis Settings (secret par NOM)
    private final String model;
    private final @Nullable Integer outputDimension;

    public List<float[]> embed(List<String> texts, InputType inputType) {   // pas de surcharge sans inputType
        if (texts.isEmpty() || texts.size() > 1000) {
            throw new IllegalArgumentException("[RETRIEVAL_EMBED_BATCH_INVALID] 1..1000 textes");
        }
        var body = new VoyageRequest(texts, model, inputType.name().toLowerCase(Locale.ROOT), outputDimension);
        VoyageResponse r = http.post().uri("/v1/embeddings").body(body).retrieve().body(VoyageResponse.class);
        return Objects.requireNonNull(r).data().stream()
                .sorted(Comparator.comparingInt(VoyageResponse.Item::index))   // l'ordre de réponse n'est pas l'ordre d'entrée garanti
                .map(VoyageResponse.Item::embedding).toList();
    }
}
```

**Kotlin**

```kotlin
// retrieval/embedding/VoyageEmbeddingModel.kt — adaptateur Spring AI, lié à UN input_type
class VoyageEmbeddingModel(
    private val client: VoyageEmbeddingClient,
    private val inputType: VoyageEmbeddingClient.InputType,   // fixé à la construction : forQueries() / forDocuments()
    private val dims: Int,
) : EmbeddingModel {
    override fun call(request: EmbeddingRequest): EmbeddingResponse =
        EmbeddingResponse(client.embed(request.instructions, inputType).mapIndexed { i, v -> Embedding(v, i) })

    override fun embed(document: Document): FloatArray = client.embed(listOf(document.text ?: ""), inputType).single()

    override fun dimensions(): Int = dims
}
```

La composition construit **deux** instances — `forQueries()` pour
`HybridRetriever`, `forDocuments()` pour l'ingestion — et aucune sans
`input_type`. Test L1 (`VoyageEmbeddingClientTest`, `MockRestServiceServer`) :
l'ingestion envoie `"input_type":"document"`, la recherche `"query"` ; une
réponse désordonnée est remise dans l'ordre ; 1 001 textes sont refusés avant
tout appel. Chaque appel émet un span `embeddings` (tokens depuis
`usage.total_tokens`) : c'est un coût, et G6 le compte.

Alternative non retenue par défaut : `dev.langchain4j:langchain4j-voyage-ai`
1.20.1-beta30 — publiée en `-beta`, et elle ferait entrer une seconde
abstraction de fournisseur à côté de Spring AI (`framework/spring-ai.md`).

---

## 4. Structure de fichiers générée

Zone de `dev-retrieval` (`workspace/src/**/retrieval/**`), disposition à plat
(`lang/*.md` §4) ; extension `.kt` ou `.java` selon le langage actif.

```
workspace/src/{AppName}/retrieval/
├── embedding/
│   ├── VoyageEmbeddingClient      # §3.5 — REST, input_type obligatoire
│   └── VoyageEmbeddingModel       # adaptateur EmbeddingModel lié à un input_type
└── {index}/
    ├── HybridRetriever            # DocumentRetriever : jambes ∥ → RrfFusion → [rerank] → topK ; span sdda.retrieve
    ├── RrfFusion                  # pure, L1
    ├── RrfDocumentJoiner          # DocumentJoiner par rang (multi-requêtes)
    ├── VectorLeg · LexicalLeg     # vectorstore/pgvector-jvm.md §3.2, §3.2 ci-dessus
    ├── QueryPrep                  # unaccent cohérent avec l'ingestion ; identifiants préservés (hybrid.md §7.3)
    ├── sql/lexical.sql · sql/hybrid_rrf.sql
    └── HybridSettings             # poids, rrf_k, candidates_per_leg, topK, LexicalLanguage — du retrieval-contract

workspace/src/{AppName}/tests/retrieval/
├── RrfFusionTest                  # L1 : mêmes cas que test_fusion.py (Python)
├── QueryPrepTest                  # L1 : 'INV-2024-0093' survit à la préparation lexicale
├── VoyageEmbeddingClientTest      # L1 : input_type par usage, ordre, lot > 1000 refusé
└── HybridSqlNetworkTest           # @Tag("network") : CTE SQL == RrfFusion sur un jeu fixe ; les deux jambes > 0
```

---

## 5. Conventions imposées — et comment mesurer

Les sept règles de `rag/hybrid.md` §5.1 s'appliquent (jambes en parallèle,
`candidates_per_leg ≥ 3 × topK` et `ef_search ≥ candidates_per_leg`, RRF sur les
rangs, déduplication par `chunk_id`, `provenance` conservée, paramètres dans le
contrat et dans `indexHash`, requête lexicale préparée). En plus :

1. **Le tenant ne voyage pas dans `Query`.** `Query.context()` est une
   `Map<String, Object>` que l'advisor remplit depuis la conversation ; y lire
   le tenant, c'est le lire depuis ce que le modèle a pu influencer. Le tenant
   vient de `RunContext`, posé par la surface depuis l'identité de l'appelant.
2. **Un seul `DocumentRetriever` hybride par index.** Pas de
   `VectorStoreDocumentRetriever` à côté : il interrogerait `PgVectorStore`,
   donc un autre schéma.
3. **Aucun `QuestionAnswerAdvisor`** (`spring-ai-vector-store-advisor`) : il
   suppose un `VectorStore` et un gabarit de contexte non balisé.
4. **Le gabarit d'augmentation est un fragment de prompt hashé** (§3.4).

### 5.1 Mesurer — G4, 0 token de chat

Le protocole est celui de `rag/hybrid.md` §5.2 (ablation par jambe, par tag,
grille de poids sur **golden**, profondeur, vérité au niveau document,
citations) ; il est joué par le runner du framework, qui **lance**
l'application :

```bash
python .sdda/sdda.py run-retrieval-eval --mission {n} --executor cli
#   → pour chaque item du golden : java -jar …/{AppName}.jar retrieve --json --index {id} --query-file - --k {k}
```

Dans l'événement `retrieval` de la commande `retrieve`, `result_ids[]` porte
les **identifiants de document** dans l'ordre du classement (premier chunk de
chaque document, dédoublonné), parce que c'est ce que le runner compare aux
`expected_documents` du golden ; les identifiants de chunk et la provenance
restent dans le span `sdda.retrieve` (`sdda.retrieval.result.ids`,
`sdda.retrieval.result.provenance`). La correspondance entre identifiant de
document et `expected_documents` est fixée dans le contrat de retrieval.

---

## 6. Commande de smoke

```bash
cd workspace/src/{AppName}
./gradlew test --tests '*.retrieval.RrfFusionTest' --tests '*.retrieval.QueryPrepTest' --tests '*.retrieval.VoyageEmbeddingClientTest'   # L1, 0 token
./gradlew test -PincludeTags=network --tests '*.retrieval.HybridSqlNetworkTest'                                                    # base de test
cd ../../..
printf 'facture INV-2024-0093 montant' | java -jar workspace/src/{AppName}/build/libs/{AppName}.jar retrieve --json --index {index} --query-file - --k 8
#   → une ligne {"event":"retrieval",…} puis {"event":"run_finished",…} ; exit 0
#   → exit 3 si une jambe est vide ([RETRIEVAL_LEG_EMPTY]) en mode smoke
```

---

## 7. Contrat d'exécution

L'application JVM implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique**. Les trois points de `serving/cli.md` §3.5, pour ce pattern :

1. `run --json --input-file -` lit l'entrée sur `stdin` ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'application sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil) et le retrieval
   figé depuis le même dossier : `HybridRetriever` est **remplacé** par un
   `FrozenRetriever` qui lit `SDDA_EVAL_FIXTURES/retrieval/{index}.jsonl`
   (clé `query` ou `query_hash` = `sha256:` du texte UTF-8), sans appel
   d'embedding ni connexion à la base — une requête sans ligne rend une liste
   vide, pas une erreur ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished` ;
   `--k` remplace `topK` pour cet appel, sans toucher à `candidates_per_leg`.

Commande de lancement : `java -jar workspace/src/{AppName}/build/libs/{AppName}.jar`
depuis la racine du dépôt (livrable), `./gradlew run --args='…'` en
développement ; `--executor cli` la dérive du langage actif,
`--executor cmd:<commande>` l'impose. Le build produit exactement
`build/libs/{AppName}.jar`.

### 7.1 L'alternative LangChain4j

`dev.langchain4j:langchain4j-pgvector` 1.20.1-beta30 propose
`PgVectorEmbeddingStore` avec `SearchMode.HYBRID` (tsvector + vecteur + RRF,
`k` réglable) — vérifié dans le jar : c'est la **seule** pile JVM avec
hybride natif. Elle n'est pas le défaut parce que (1) le module est publié en
`-betaNN`, (2) son schéma n'est pas celui de `rag.chunks` (même objection que
`PgVectorStore`), (3) la fusion vit dans le SQL et n'est plus testable en L1
sans base, (4) elle introduirait une seconde abstraction de fournisseur. Elle
se choisit par ADR, avec `framework: [langchain4j]`, en combo
`experimental`.

---

## 8. Pièges connus

Les douze pièges de `rag/hybrid.md` §7 s'appliquent. Propres à la JVM et à
Spring AI :

1. **`ConcatenationDocumentJoiner` pris pour une fusion.** Il concatène ; avec
   une expansion de requête, l'ordre final dépend de l'ordre des
   reformulations, pas du rang. `RrfDocumentJoiner` dès qu'il y a plus d'une
   requête.
2. **`VectorStoreDocumentRetriever` par réflexe.** C'est le retriever des
   exemples Spring AI ; il interroge `PgVectorStore`, donc une table qui n'a
   ni tenant typé ni `tsvector`.
3. **`input_type` perdu par un `EmbeddingModel` générique.** Une seule instance
   partagée entre ingestion et recherche envoie le même `input_type` des deux
   côtés : recall dégradé sans erreur. Deux instances, liées à la construction.
4. **Jambes séquentielles.** `vector.search(...)` puis `lexical.search(...)`
   double la latence ; les deux jambes partent ensemble.
5. **Contexte de trace perdu dans les threads virtuels.** Un
   `Executors.newVirtualThreadPerTaskExecutor()` ne propage pas le contexte
   OpenTelemetry : les spans SQL des jambes deviennent orphelins. Envelopper
   l'exécuteur (`Context.taskWrapping(executor)` de `opentelemetry-context`) ;
   en Kotlin, `Dispatchers.IO + span.asContextElement()`
   (`opentelemetry-extension-kotlin`, `observability/otel-genai-jvm.md` §7).
6. **Texte des chunks dans le contexte sans balisage** (§3.4) : c'est la
   surface d'injection indirecte que la suite L8 empoisonne volontairement.
