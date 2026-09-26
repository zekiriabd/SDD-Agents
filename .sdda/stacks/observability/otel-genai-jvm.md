# Stack: otel-genai-jvm (observability)

> §2.3 (Librairies) suit `otel-genai-jvm.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: observability-otel-genai-jvm
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin, java
Scope: observabilité de l'application générée sur la JVM (Kotlin et Java) via **OpenTelemetry** et les conventions **GenAI** — observations Micrometer `gen_ai.*` de Spring AI ponctées vers OTel par Spring Boot 4, helpers de spans imposés pour ce que Spring AI n'émet pas (run, tour d'agent, outil, retrieval, requête de données, garde-fou), **normalisation** des noms Spring AI vers ceux du framework, export OTLP **et écriture OBLIGATOIRE du fichier de trace JSONL par run** (`workspace/.sys/traces/runs/{run_id}.jsonl`, un span par ligne, champs `run_id` / `trace_id` / `span_id` / `parent_span_id`) — **le même fichier, au même format, que `observability/otel-genai.md`**, parce que les scripts du framework le relisent. Redaction avant écriture.

---

## 1. Rôle et périmètre

Les raisons sont celles de `observability/otel-genai.md` §1 : sans trace, un
système non déterministe n'est pas débogable, et la trace est la mesure sur
laquelle s'appuient la L5 (trajectoires), la G6 (coût et latence mesurés), la
console et le post-mortem.

**Ce qui rend cette fiche non négociable** : les scripts du framework ne lisent
pas l'OTLP, ils lisent **un fichier**. `cost-report`, `trajectory-report`, les
graders `trajectory` / `cost` / `latency`, le scan G7 (`[SECRET_LEAK]`,
`[PII_IN_TRACE]`) et `CommandExecutor` (`trace_path` de `run_finished`)
ouvrent `workspace/.sys/traces/runs/{run_id}.jsonl` et le lisent avec
`.sdda/python/sdda_lib/tracing.py`. Une application JVM qui n'émettrait que de
l'OTLP serait, pour eux, une application sans trace : G6 sans coût, L5 sans
trajectoire, G7 sans rien à scanner.

Périmètre : ce que Spring AI émet et ce qu'il faut compléter, normalisation,
format JSONL, redaction, échantillonnage, flush, structure, pièges. **Hors
périmètre** : le backend de visualisation, les métriques d'infrastructure.

### 1.1 Honnêteté sur les noms

Les conventions GenAI d'OpenTelemetry sont en statut *Development*, et Spring
AI 2.0.1 n'émet **pas** tous les noms que le framework lit (relevé dans les
classes `spring-ai-model` 2.0.1) :

| Le framework lit (`sdda_lib/tracing.py`) | Spring AI 2.0.1 émet |
|---|---|
| `gen_ai.provider.name` | `gen_ai.system` (l'ancien nom) |
| `gen_ai.operation.name` = `embeddings` | `gen_ai.operation.name` = `embedding` |
| `sdda.usage.cache_read_tokens` / `sdda.usage.cache_write_tokens` | `gen_ai.usage.cache_read.input_tokens` / `gen_ai.usage.cache_creation.input_tokens` |
| `execute_tool {gen_ai.tool.name}`, `gen_ai.tool.*`, `sdda.tool.*` | observation `spring.ai.tool`, attributs `spring.ai.tool.definition.name`, `spring.ai.tool.call.arguments`… |
| `invoke_agent`, `sdda.run`, `sdda.retrieve`, `sdda.data.query`, `sdda.guardrail` | rien (Spring AI ne connaît ni agent, ni run, ni retrieval SQL) |

Conséquence imposée : **une table de normalisation, en un seul endroit**
(`app/tracing/Semconv`, §3.2), appliquée à l'écriture du JSONL ; et des
**helpers** pour tout ce que Spring AI n'émet pas (§3.3). Les noms du
framework sont recopiés de `sdda_lib/tracing.py`, qui fait foi.

---

## 2. Identité

### 2.1 Identité

- **Stack ID** : `observability-otel-genai-jvm`
- **Langage** : Kotlin ou Java sur JDK 21
- **SDK** : `spring-boot-starter-opentelemetry` (Spring Boot 4.1.1) → `micrometer-tracing-bridge-otel` 1.7.1 + OpenTelemetry 1.62.0
- **Export** : OTLP/HTTP si un endpoint est configuré **+** `JsonlSpanExporter` maison, via un `SimpleSpanProcessor` — les deux ; le second jamais désactivable
- **Paramètres STACK.md** : `TraceLevel: full`, `TraceSampleRate: 1.0`, `TracePIIPolicy: redact`, `CostTrackingEnabled: true`
- **Capture de contenu** : `spring.ai.chat.observations.log-prompt` et `log-completion` restent à `false` (défaut vérifié) ; `true` seulement si `TracePIIPolicy: raw` (ADR)

### 2.3 Librairies

Source de vérité : `otel-genai-jvm.libs.json`.

| Artefact | Version | Rôle |
|---|---|---|
| `org.springframework.boot:spring-boot-starter-opentelemetry` | BOM 4.1.1 | pont Micrometer → OTel, SDK, OTLP ; ajoute les beans `SpanProcessor` au `SdkTracerProvider` |
| `io.opentelemetry:opentelemetry-api` / `-sdk` | 1.62.0 (BOM) | helpers de spans ; `SpanExporter` du JSONL |
| `net.logstash.logback:logstash-logback-encoder` | 9.0 | journaux JSON sur stderr, `trace_id` / `span_id` en MDC (ligne 9 = Jackson 3) |
| `com.github.f4b6a3:ulid-creator` | 5.2.4 | `run_id` en ULID, nom du fichier de trace |
| `io.opentelemetry:opentelemetry-sdk-testing` (dev) | 1.62.0 | `InMemorySpanExporter` pour `TraceShapeTest` |
| `io.opentelemetry:opentelemetry-extension-kotlin` (on-demand, Kotlin) | 1.62.0 | contexte de trace dans les coroutines |

Absents par conception : `opentelemetry-spring-boot-starter` (seconde
instrumentation, compatibilité Boot 4 non vérifiée), l'agent Java
(`-javaagent` hors du contrat de lancement), `opentelemetry-semconv-incubating`
(alpha ; les noms sont des constantes locales).

---

## 3. Mapping des concepts SDD_Agents → spans

### 3.1 Qui émet quoi

La table des spans de `observability/otel-genai.md` §3.1 est **la** table :
mêmes noms, mêmes attributs standard, même extension `sdda.*`, même
hiérarchie `sdda.run` → `invoke_agent` → (`chat` | `execute_tool` |
`sdda.retrieve` → `embeddings` + `sdda.data.query`). En JVM, les spans ont deux
origines :

| Span | Émis par | Pourquoi |
|---|---|---|
| `sdda.run {mission_id}` (racine) | helper `RunSpans`, dans `RunService` | Spring AI ne connaît pas le run ; `sdda.run.id` = `run_id` = nom du fichier |
| `invoke_agent {agent}` | helper `AgentSpans`, dans chaque agent | idem ; porte `sdda.agent.iteration`, `sdda.bounds.*`, `sdda.prompt.hash` |
| `chat {model}` | **observation Spring AI** `gen_ai.client.operation` (ChatModel), **normalisée** (§3.2) | c'est elle qui porte les tokens réels de la réponse (`Usage`) ; la doubler par un helper compterait chaque appel deux fois dans `cost-report` |
| `embeddings {model}` | observation Spring AI si l'`EmbeddingModel` est instrumenté, sinon helper dans `VoyageEmbeddingClient` (`rag/hybrid-jvm.md` §3.5) | le client Voyage est maison : c'est lui qui connaît `usage.total_tokens` |
| `execute_tool {tool}` | helper `ToolSpans`, dans le décorateur d'outil (`GuardedMcpToolCallback`, outils de vue, outils maison) | l'observation `spring.ai.tool` ne connaît ni la classe d'effet de bord, ni la confiance, ni l'erreur déclarée |
| `sdda.retrieve`, `sdda.data.query`, `sdda.guardrail` | helpers | aucune convention Spring AI |

Les observations `spring.ai.tool`, `spring.ai.chat.client`, `spring.ai.advisor`
et `db.vector.client.operation` sont **écartées** par un `ObservationPredicate`
(bean pris en compte par l'auto-configuration Micrometer Observation de Spring
Boot 4.1.1) : la première ferait un second span par appel d'outil, les deux
suivantes intercaleraient des nœuds sans rôle entre `invoke_agent` et `chat`,
la dernière n'a pas d'objet (aucun `VectorStore` Spring AI n'est utilisé).

**Java**

```java
// app/tracing/ObservationConfig.java — package {package}.app.tracing
@Configuration(proxyBeanMethods = false)
class ObservationConfig {
    private static final Set<String> DROPPED =
            Set.of("spring.ai.tool", "spring.ai.chat.client", "spring.ai.advisor", "db.vector.client.operation");

    @Bean
    ObservationPredicate sddaObservationScope() {
        return (name, context) -> !DROPPED.contains(name);
    }
}
```

**Kotlin**

```kotlin
// app/tracing/ObservationConfig.kt — package {package}.app.tracing
@Configuration(proxyBeanMethods = false)
class ObservationConfig {
    @Bean
    fun sddaObservationScope() = ObservationPredicate { name, _ -> name !in DROPPED }

    private companion object {
        val DROPPED = setOf("spring.ai.tool", "spring.ai.chat.client", "spring.ai.advisor", "db.vector.client.operation")
    }
}
```

Une observation écartée est un *no-op* : ses enfants se rattachent au span
courant. `TraceShapeTest` (L1) vérifie que `chat` a bien `invoke_agent` pour
parent — c'est un comportement du pont, et il se vérifie au lieu de se supposer.

### 3.2 Normalisation — une table, un endroit

`app/tracing/Semconv` porte **toutes** les constantes d'attributs (recopiées de
`sdda_lib/tracing.py`) et la table de renommage appliquée par
`JsonlSpanExporter` avant d'écrire une ligne :

| Condition | Transformation |
|---|---|
| attribut `gen_ai.system` | → `gen_ai.provider.name` (valeur inchangée : `anthropic`, `openai`…) |
| `gen_ai.operation.name = "embedding"` | → `"embeddings"` |
| `gen_ai.usage.cache_read.input_tokens` | → `sdda.usage.cache_read_tokens` |
| `gen_ai.usage.cache_creation.input_tokens` | → `sdda.usage.cache_write_tokens` |
| span d'opération `chat` / `embeddings` | nom → `chat {gen_ai.request.model}` / `embeddings {gen_ai.request.model}` |
| span `chat` sans `gen_ai.usage.input_tokens` ou `output_tokens` | ligne écrite **et** `sdda.trace.problem = "TRACE_USAGE_MISSING"` — jamais un zéro inventé |
| span `chat` | ajoute `sdda.cost.usd` recalculé depuis les tokens (tarifs de `.sdda/providers/*.yaml`, projetés dans `app_config.json`) et `sdda.pricing.version` |

`sdda.cost.usd` est **indicatif** : `sdda_lib/tracing.py` recalcule le coût
depuis les tokens et signale l'écart (ARCHITECTURE §8). Le JVM le calcule pour
`run_finished.cost_usd` et `--max-budget-usd`, avec la même table de tarifs ; il
ne fait pas foi.

Les attributs `gen_ai.request.model`, `gen_ai.response.model`,
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`,
`gen_ai.response.finish_reasons` que Spring AI émet déjà sont conservés tels
quels : ce sont ceux que `span_cost_usd` lit.

### 3.3 Helpers imposés

Le code des agents, outils et retrievers n'importe **jamais**
`io.opentelemetry` directement : seulement `app/tracing`.

**Kotlin**

```kotlin
// app/tracing/AgentSpans.kt
class AgentSpans(private val tracer: Tracer) {
    fun <T> agentTurn(agent: AgentRef, threadId: String, tier: String, promptHash: String,
                      bounds: Bounds, iteration: Int, body: (AgentSpan) -> T): T {
        val span = tracer.spanBuilder("invoke_agent ${agent.name}").startSpan()
        span.setAttribute(Semconv.GEN_AI_OPERATION_NAME, "invoke_agent")
        span.setAttribute(Semconv.GEN_AI_AGENT_ID, agent.id)
        span.setAttribute(Semconv.GEN_AI_AGENT_NAME, agent.name)
        span.setAttribute(Semconv.GEN_AI_CONVERSATION_ID, threadId)
        span.setAttribute(Semconv.SDDA_AGENT_MODEL_TIER, tier)
        span.setAttribute(Semconv.SDDA_PROMPT_HASH, promptHash)
        span.setAttribute(Semconv.SDDA_AGENT_ITERATION, iteration.toLong())
        span.setAttribute(Semconv.SDDA_BOUNDS_MAX_ITERATIONS, bounds.maxIterations.toLong())
        span.setAttribute(Semconv.SDDA_BOUNDS_MAX_TOOL_CALLS, bounds.maxToolCalls.toLong())
        return span.makeCurrent().use {
            try {
                body(AgentSpan(span))                    // .boundExceeded(name, limit, observed, policy) ; .interrupted(reason)
            } catch (e: Throwable) {
                span.setStatus(StatusCode.ERROR); span.setAttribute(Semconv.ERROR_TYPE, ErrorClass.of(e)); throw e
            } finally {
                span.end()
            }
        }
    }
}
```

**Java**

```java
// app/tracing/ToolSpans.java
public final class ToolSpans {
    private final Tracer tracer;
    private final Redactor redactor;

    public <T> T toolCall(ToolSpec spec, String callId, Map<String, Object> args, Function<ToolSpan, T> body) {
        Span span = tracer.spanBuilder("execute_tool " + spec.name()).startSpan();
        span.setAttribute(Semconv.GEN_AI_OPERATION_NAME, "execute_tool");
        span.setAttribute(Semconv.GEN_AI_TOOL_NAME, spec.name());
        span.setAttribute(Semconv.GEN_AI_TOOL_CALL_ID, callId);
        span.setAttribute(Semconv.SDDA_TOOL_ID, spec.id());
        span.setAttribute(Semconv.SDDA_TOOL_SIDE_EFFECT_CLASS, spec.sideEffectClass());
        span.setAttribute(Semconv.SDDA_TOOL_TRUST, spec.trust());
        span.setAttribute(Semconv.SDDA_TOOL_ARGS, redactor.redactArgs(args, spec.piiFields()));   // redigé AVANT d'entrer dans le span
        try (Scope ignored = span.makeCurrent()) {
            return body.apply(new ToolSpan(span));    // .result(bytes, truncated) ; .declaredError(code) ; .retry(attempt)
        } catch (RuntimeException e) {
            span.setStatus(StatusCode.ERROR);
            span.setAttribute(Semconv.ERROR_TYPE, ErrorClass.of(e));
            throw e;
        } finally {
            span.end();
        }
    }
}
```

`RunSpans`, `RetrievalSpans`, `DataSpans`, `GuardrailSpans` suivent le même
modèle. `run_id` est posé en *baggage* au démarrage du span racine et recopié
sur chaque span par un `SpanProcessor.onStart` (`sdda.run.id`) : c'est ce qui
permet au même processus `backend-api` de servir deux runs concurrents sans
mélanger leurs fichiers.

---

## 4. Le fichier de trace — format obligatoire

**Emplacement** : `{workspace}/.sys/traces/runs/{run_id}.jsonl`, où
`{workspace}` est résolu comme en `lang/java.md` §8.4 / `lang/kotlin.md` §8
(`SDDA_WORKSPACE_ROOT`, sinon déduit de l'emplacement du jar). Le chemin est
rendu dans `run_finished.trace_path`.

**Une ligne = un span**, JSON UTF-8, `\n`, écrite **entière** — les champs sont
ceux que `sdda_lib/tracing.py` exige (`SPAN_FIELDS = run_id, trace_id,
span_id, name`) et ceux qu'il exploite :

```json
{"semconv_version":"0.65b0","run_id":"01J8…","trace_id":"4bf92f3577b34da6a3ce929d0e0e4736","span_id":"00f067aa0ba902b7",
 "parent_span_id":"a3ce929d0e0e4736","name":"execute_tool billing_specialist_unpaid_invoices",
 "start":"2026-09-26T14:12:03.120Z","end":"2026-09-26T14:12:03.432Z","duration_ms":312,"status":"OK",
 "attributes":{"gen_ai.operation.name":"execute_tool","gen_ai.tool.name":"billing_specialist_unpaid_invoices",
               "sdda.tool.side_effect_class":"read-only","sdda.tool.args":"{\"customer_id\":\"[REDACTED:uuid]\"}"},
 "events":[]}
```

| Champ | Règle |
|---|---|
| `run_id` | ULID du run, **identique sur toutes les lignes** du fichier |
| `trace_id`, `span_id` | identifiants OTel en hexadécimal (`SpanContext.getTraceId()`, `getSpanId()`) |
| `parent_span_id` | **absent** sur la racine `sdda.run` — c'est ainsi que `tracing.py` la reconnaît ; présent partout ailleurs |
| `name` | `sdda.run {mission}`, `invoke_agent {agent}`, `chat {model}`, `execute_tool {tool}`, `embeddings {model}`, `sdda.retrieve {index}`, `sdda.data.query {view}`, `sdda.guardrail {id}` |
| `start`, `end` | ISO 8601 UTC avec `Z` ; `duration_ms` entier |
| `status` | `"OK"` ou `"ERROR"` (jamais `UNSET` : `UNSET` → `"OK"`) |
| `attributes` | normalisés (§3.2), redigés (§5.4) ; tableaux JSON pour `sdda.retrieval.result.ids` / `.scores` |
| `events` | `[{name, time, attributes}]` — `sdda.bound_exceeded`, `sdda.interrupted` |
| `semconv_version` | la version de vocabulaire du framework — celle de `opentelemetry-semantic-conventions` épinglée dans `observability/otel-genai.libs.json` (`0.65b0` à ce jour) : les constantes JVM **recopient** ce vocabulaire, elles changent avec lui |

**Écriture** : `JsonlSpanExporter` implémente `SpanExporter` et est enregistré
comme bean **`SpanProcessor`** (`SimpleSpanProcessor.create(jsonl)`), jamais
comme bean `SpanExporter` : Spring Boot 4.1.1 regroupe les beans
`SpanExporter` dans un `BatchSpanProcessor` (lu dans
`OpenTelemetryTracingAutoConfiguration`), et un lot en mémoire au moment d'un
crash est une trace perdue. Chaque ligne est ajoutée sous **verrou exclusif**
(`FileChannel.lock()` sur le fichier ouvert en `APPEND`), en un seul `write` :
des évaluations parallèles perdaient des lignes (ARCHITECTURE §8).

**Java**

```java
// app/tracing/JsonlSpanExporter.java
public final class JsonlSpanExporter implements SpanExporter {
    private final TracePaths paths;        // {workspace}/.sys/traces/runs/{run_id}.jsonl
    private final SpanNormalizer normalizer;   // §3.2 + redaction §5.4
    private final JsonMapper json;         // Jackson 3, le mapper du projet

    @Override
    public CompletableResultCode export(Collection<SpanData> spans) {
        for (SpanData span : spans) {
            Map<String, Object> line = normalizer.toLine(span);      // champs du tableau ci-dessus
            byte[] bytes = (json.writeValueAsString(line) + "\n").getBytes(StandardCharsets.UTF_8);
            Path file = paths.forRun((String) line.get("run_id"));
            try (FileChannel ch = FileChannel.open(file, CREATE, WRITE, APPEND); FileLock lock = ch.lock()) {
                ch.write(ByteBuffer.wrap(bytes));
            } catch (IOException e) {
                return CompletableResultCode.ofFailure();             // journalisé sur stderr ; le run continue
            }
        }
        return CompletableResultCode.ofSuccess();
    }

    @Override public CompletableResultCode flush() { return CompletableResultCode.ofSuccess(); }
    @Override public CompletableResultCode shutdown() { return CompletableResultCode.ofSuccess(); }
}

@Bean
SpanProcessor sddaJsonlSpanProcessor(JsonlSpanExporter exporter) {
    return SimpleSpanProcessor.create(exporter);   // synchrone, à chaque span.end()
}
```

**Kotlin** — même classe ; l'enregistrement :

```kotlin
@Bean
fun sddaJsonlSpanProcessor(exporter: JsonlSpanExporter): SpanProcessor = SimpleSpanProcessor.create(exporter)
```

---

## 5. Conventions imposées

### 5.1 Émission

Les six règles de `observability/otel-genai.md` §5.1 s'appliquent (une racine
`sdda.run` par exécution, un `chat` par appel LLM avec tokens —
`[TRACE_USAGE_MISSING]` sinon —, un `execute_tool` par appel y compris les
refus, un `sdda.retrieve` par retrieval avec ids et scores, l'événement
`sdda.bound_exceeded`, pas d'échantillonnage en eval).

### 5.2 Échantillonnage — le défaut de Spring Boot est 10 %

`management.tracing.sampling.probability` vaut **`0.1` par défaut** (lu dans
les métadonnées de configuration de Spring Boot 4.1.1). Sans réglage, neuf runs
sur dix n'ont pas de trace : la L5 conclut « trajectoire absente », G6 mesure
un coût sur un dixième des runs.

```yaml
# app/resources/application.yml
management:
  tracing:
    sampling:
      probability: 1.0          # TraceSampleRate — 1.0 obligatoire en eval ; l'application refuse de démarrer sinon sous SDDA_EVAL_ISOLATION
  opentelemetry:
    tracing:
      export:
        otlp:
          endpoint: ${OTEL_EXPORTER_OTLP_ENDPOINT:}   # vide = pas d'export OTLP ; le JSONL, lui, est toujours écrit
spring:
  ai:
    chat:
      observations:
        log-prompt: false        # contenu des messages : seulement si TracePIIPolicy: raw (ADR)
        log-completion: false
```

Au démarrage, la configuration vérifie que la probabilité effective vaut
`TraceSampleRate` et que la valeur est `1.0` dès qu'un runner d'eval lance
l'application (`[TRACE_SAMPLED_IN_EVAL]`, code `8`).

### 5.3 Export et flush

1. **Double export** : OTLP si `OTEL_EXPORTER_OTLP_ENDPOINT` est posé,
   JSONL **toujours** (`trace-emitted-per-run`).
2. **`SimpleSpanProcessor` pour le JSONL**, `BatchSpanProcessor` (celui de
   Spring Boot) pour OTLP.
3. **Fermeture** : la CLI sort par `SpringApplication.exit` (`lang/*.md` §8.3),
   qui ferme le contexte, donc le `SdkTracerProvider` et son `forceFlush` ;
   jamais `System.exit` / `exitProcess` avant.

### 5.4 Redaction — `TracePIIPolicy`

Les règles de `observability/otel-genai.md` §5.4 s'appliquent. En JVM, la
redaction a **deux** points, parce qu'un `SpanProcessor` Java ne peut pas
modifier un span terminé (`ReadableSpan` est immuable) :

1. **À la source** : les helpers redigent les arguments d'outil (`@pii` du
   contrat) et hashent tout texte libre avant `setAttribute` ;
2. **À l'écriture** : `SpanNormalizer` applique la seconde passe —
   motifs déterministes (e-mail, téléphone, IBAN, carte Luhn), **valeurs des
   secrets connus** (celles des `SecretValue` de la configuration →
   `[REDACTED:secret]`), clés sensibles par nom (`FORBIDDEN_KEYS` et suffixes
   de `sdda_lib/tracing.py`, recopiés dans `Semconv`).

Pour l'OTLP, l'exporteur est **enveloppé** de la même passe
(`RedactingSpanExporter`, bean construit par l'application à partir de
`OtlpHttpSpanExporter.builder()`), au lieu de l'exporteur auto-configuré — sinon
le Collector recevrait ce que le fichier ne contient pas. Le scan G7 lit le
JSONL : la redaction est une défense, le scan la vérification.

### 5.5 Noms

Aucun littéral `"gen_ai.…"` ou `"sdda.…"` hors `app/tracing/Semconv`. Test L0
(`SemconvLiteralTest`) : recherche dans les sources hors `app/tracing/` →
`[TRACE_ATTR_LITERAL]`.

---

## 6. Structure de fichiers générée

Zone de `dev-backend` (`workspace/src/**/app/**`) — c'est le pendant de
`tracing.py`, qui appartient à la coquille en Python ; les autres couches
**utilisent** les helpers, ne les écrivent pas.

```
workspace/src/{AppName}/app/tracing/
├── Semconv                  # TOUTES les constantes (gen_ai.*, sdda.*, error.type) + table de renommage §3.2 + SEMCONV_VERSION
├── ObservationConfig        # ObservationPredicate (§3.1) ; beans SpanProcessor (JSONL, run_id) ; RedactingSpanExporter OTLP
├── JsonlSpanExporter        # §4 — une ligne par span, verrou exclusif, UTF-8
├── SpanNormalizer           # SpanData -> ligne : champs, renommage, coût indicatif, redaction
├── RunIdSpanProcessor       # onStart : baggage sdda.run.id -> attribut
├── RunSpans · AgentSpans · ToolSpans · RetrievalSpans · DataSpans · GuardrailSpans   # helpers imposés
├── Redactor                 # motifs, secrets connus, clés sensibles
├── Pricing                  # coût indicatif depuis app_config.json (tarifs des fiches providers)
└── TracePaths               # {workspace}/.sys/traces/runs/{run_id}.jsonl

workspace/src/{AppName}/tests/app/
├── TraceShapeTest           # L1 : InMemorySpanExporter — sdda.run > invoke_agent > chat + execute_tool ; chat a invoke_agent pour parent ;
│                            #      aucune observation spring.ai.tool / spring.ai.chat.client exportée
├── JsonlFormatTest          # L1 : chaque ligne a run_id/trace_id/span_id/name ; racine sans parent_span_id ; status OK|ERROR ; UTC Z
├── NormalizationTest        # L1 : gen_ai.system -> provider.name ; embedding -> embeddings ; cache tokens renommés
├── RedactionTest            # L1 : e-mail, IBAN, secret connu, champ @pii redigés — mêmes cas que test_redaction.py
└── TraceReadableByFrameworkTest  # L1 : le fichier produit est lu par `python .sdda/sdda.py trajectory-report` sans problème de format
```

---

## 7. Commande de smoke

Déterministe, 0 token, sans Collector :

```bash
cd workspace/src/{AppName}
./gradlew test --tests '*.app.TraceShapeTest' --tests '*.app.JsonlFormatTest' --tests '*.app.NormalizationTest' --tests '*.app.RedactionTest'
cd ../../..
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar health --json > /tmp/health.ndjson
python - <<'EOF'
import json, pathlib
ev = [json.loads(l) for l in open("/tmp/health.ndjson", encoding="utf-8")]
path = pathlib.Path(ev[-1]["trace_path"])
spans = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
assert spans and all({"run_id", "trace_id", "span_id", "name"} <= s.keys() for s in spans)
assert len({s["run_id"] for s in spans}) == 1
assert sum(1 for s in spans if "parent_span_id" not in s) == 1   # une seule racine
EOF
```

`health` écrit sa trace (`serving/cli.md` §5.10) : c'est le plus petit run qui
prouve que le fichier existe, au bon endroit, au bon format.

---

## 8. Contrat d'exécution

L'application JVM implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique**. Les trois points de `serving/cli.md` §3.5, vus depuis la trace :

1. `run --json --input-file -` lit l'entrée sur `stdin` ; `run_finished.trace_path`
   désigne le fichier JSONL de ce run ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'application sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil) et le retrieval
   figé depuis le même dossier, sans aucun appel réseau d'outil — et la trace
   le dit : les spans `execute_tool` portent `sdda.tool.transport = "fixture"`,
   `sdda.retrieve` porte `sdda.retrieval.pattern = "frozen"` ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished`, et
   sa trace contient `sdda.run` > `sdda.retrieve` (> `embeddings`), sans `chat`.

Commande de lancement : `java -jar workspace/src/{AppName}/build/libs/{AppName}.jar`
depuis la racine du dépôt (livrable) ; `./gradlew run --args='…'` en
développement ; `--executor cli` la dérive du langage actif (Kotlin et Java),
`--executor cmd:<commande>` l'impose. Le build produit exactement
`build/libs/{AppName}.jar`.

---

## 9. Pièges connus

Les treize pièges de `observability/otel-genai.md` §7 s'appliquent. Propres à
la JVM et à Spring :

1. **Échantillonnage à 10 % par défaut** (§5.2). Le plus coûteux, parce qu'il
   ne produit aucune erreur.
2. **Exporteur JSONL déclaré en bean `SpanExporter`.** Spring Boot le met dans
   son `BatchSpanProcessor` : écriture différée, perte au crash, et l'ordre
   des lignes n'est plus celui des fins de span.
3. **Double comptage des appels LLM.** Un helper `chat` autour de
   `ChatClient.call()` **et** l'observation Spring AI donnent deux spans
   `chat` avec les mêmes tokens : `cost-report` double la facture. Un seul
   émetteur par span (§3.1).
4. **Noms Spring AI laissés tels quels.** `gen_ai.system` au lieu de
   `gen_ai.provider.name`, `embedding` au lieu de `embeddings` : les lecteurs
   du framework ignorent les spans d'embedding et perdent leur coût.
5. **Contexte perdu hors du thread courant.** Threads virtuels et coroutines
   ne propagent pas le contexte OTel sans `Context.taskWrapping(...)` ou
   `asContextElement()` : spans orphelins, profondeur de délégation fausse
   (`rag/hybrid-jvm.md` §8.5).
6. **Contenu des prompts dans les traces.** `log-prompt: true` pour « déboguer »
   met le texte des messages dans les journaux et les spans : PII et texte
   hostile dans le backend. `TracePIIPolicy: raw` + ADR, ou rien.
7. **`System.exit` avant la fermeture du contexte.** Le `BatchSpanProcessor`
   OTLP perd ses derniers spans ; le JSONL est intact (synchrone) — c'est
   précisément pour cela qu'il l'est.
8. **Verrou de fichier sous Windows.** Un lecteur (console, `tail`) qui garde
   le fichier ouvert en exclusif bloque l'écriture ; le lecteur ouvre en
   partagé, et `JsonlSpanExporter` n'échoue pas le run sur une ligne perdue —
   il le journalise sur stderr.
