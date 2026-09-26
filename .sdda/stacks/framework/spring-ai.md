# Stack: spring-ai (framework)

> §2.3 (Librairies) suit `spring-ai.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: framework-spring-ai
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin, java
Scope: Spring AI **2.0.1** sur Spring Boot **4.1.1** — `ChatClient`, outils typés (`@Tool`, `ToolCallback`, `ToolContext`), sorties structurées, client MCP, mémoire de conversation, RAG modulaire (`spring-ai-rag`), sur la JVM en **Kotlin ou en Java** (mêmes bibliothèques, mêmes pins). Suppose `lang/kotlin.md` ou `lang/java.md`. **Sans graphe borné natif** : Spring AI n'a pas d'équivalent de LangGraph. Les patterns à cycle (`supervisor`, `reflection`, `plan-execute`, `graph`) exigent une boucle d'orchestration **écrite** dans `orchestration/`, bornée par `Bounds` et persistée par un checkpointer maison — la fiche le dit, et `architecture-requirements.yml` l'exige déjà. Retenu à la place de Semantic Kernel Java (portage non poussé par Microsoft) et de LangChain4j (même absence de graphe, modules hors cœur en `-beta`). Le RAG hybride est dans `rag/hybrid-jvm.md`, l'observabilité dans `observability/otel-genai-jvm.md`.

---

## 1. Rôle et périmètre

Spring AI apporte à la JVM une abstraction de fournisseur propre
(`ChatModel`, `ChatClient`), une définition d'outils par annotation ou par
fonction, la conversion de sorties en types, un client MCP, un pipeline RAG
modulaire et des observations Micrometer `gen_ai.*`. Il est **suffisant** pour :

| Pattern | Condition |
|---|---|
| `single-agent` | ≤ 2 tours d'outils, boucle écrite et bornée (§3.2) |
| `sequential` | pipeline d'appels typés, sans retour arrière |
| `parallel` | coroutines `async {}` (Kotlin) ou threads virtuels (Java) + fusion déterministe |
| `router` | classification puis dispatch en un passage |

Il est **insuffisant seul** — et `orchestration/` doit alors porter une boucle
explicite avec état persisté — dès que la TOPOLOGY comporte un cycle, une
reprise (`HumanInTheLoopEnabled`), `escalate-human`, ou
`max_delegation_depth > 0`. Ce n'est pas un défaut de la fiche : c'est la
raison pour laquelle `registry/architecture-requirements.yml` exige
`loop_bound` déclaré pour ces patterns, quel que soit le framework.

Ce que Spring AI 2.0.1 **n'a pas**, vérifié sur les jars et la BOM :
recherche hybride PgVector, fusion RRF, module d'embedding Voyage (les trois
sont écrits par `rag/hybrid-jvm.md`) ; starter Azure OpenAI 2.x (le dernier est
1.1.8) ; `spring-ai-advisors-vector-store` (renommé
`spring-ai-vector-store-advisor` en 2.x).

Hors périmètre : la surface (`serving/cli-kotlin.md`, `serving/cli-java.md`),
la maison HTTP (`backend/kotlin-spring-boot.md`), le retrieval
(`rag/hybrid-jvm.md`, `vectorstore/pgvector-jvm.md`), les traces
(`observability/otel-genai-jvm.md`).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `framework-spring-ai` |
| **Langage** | Kotlin 2.3 (`lang/kotlin.md`) ou Java 21 (`lang/java.md`), JDK 21 |
| **Framework** | Spring AI **2.0.1** via BOM `spring-ai-bom` ; Spring Boot **4.1.1** / Spring Framework **7.0.9** pour le conteneur — la combinaison qu'exige Spring AI 2.0.1 (POM `spring-ai-autoconfigure-model-anthropic` 2.0.1 → `spring-boot-autoconfigure` 4.1.1) |
| **Fournisseurs** | `spring-ai-starter-model-anthropic`, `-openai`, `-google-genai` — ON-DEMAND selon `RuntimeProvider` ; **pas** de starter Azure OpenAI ni Vertex AI Gemini en 2.x |
| **Outils MCP** | `spring-ai-starter-mcp-client` — ON-DEMAND, câblé par `tools/mcp-jvm.md` (auto-configuration coupée) |
| **RAG** | `spring-ai-rag` — ON-DEMAND si `rag/hybrid-jvm.md` actif |
| **Mémoire** | `ChatMemory` en mémoire (dev) ; JDBC (`spring-ai-starter-model-chat-memory-repository-jdbc`) partagé sinon |
| **Build** | Gradle Kotlin DSL, plateformes `spring-boot-dependencies` + `spring-ai-bom`, disposition à plat (`lang/*.md` §4) |
| **Combinaisons** (SSoT : `compatibility.matrix.json`) | `{kotlin, java} × spring-ai × {single-agent, router, sequential, parallel}` ; les patterns à cycle exigent une boucle écrite — toutes `untested` |

### 2.1 Init

Depuis `lang/kotlin.md` §2.1 ou `lang/java.md` §2.2, puis :

```kotlin
// build.gradle.kts
dependencies {
    implementation(platform(org.springframework.boot.gradle.plugin.SpringBootPlugin.BOM_COORDINATES))
    implementation(platform(libs.spring.ai.bom))
    implementation(libs.spring.boot.starter)
    implementation(libs.spring.ai.client.chat)
    implementation(libs.spring.ai.starter.model.anthropic)   // selon RuntimeProvider
}
```

Vérifié le 2026-09-26 (Gradle 9.5.0, JDK 21) : cette déclaration résout et
compile en Kotlin comme en Java, disposition à plat, jar
`build/libs/{AppName}.jar` produit et démarré.

### 2.2 Patterns d'erreurs

- `NonTransientAiException` / `TransientAiException` : la seconde est
  rejouable avec backoff (Resilience4j), la première jamais ;
- échec de conversion de sortie structurée : erreur nommée
  (`[AGENT_OUTPUT_INVALID]`, code `9`), jamais une chaîne brute passée au tour
  suivant ;
- `ToolExecutionException` : l'outil a refusé l'argument — remonté au modèle
  comme erreur d'outil déclarée, pas comme exception applicative.

### 2.3 Librairies

Source de vérité : `spring-ai.libs.json`.

**CORE** (Kotlin et Java) : `spring-ai-bom` et `spring-boot-dependencies`
(plateformes), `spring-ai-client-chat`, `spring-boot-starter`,
`resilience4j-retry`, plugin Gradle Spring Boot (`bootJar`).

**ON-DEMAND, par capability** :
- fournisseurs (`provider-anthropic`, `provider-openai`, `provider-google`),
  `mcp`, `rag`, `rest-client` (client Voyage), `memory-shared` ;
- `lang-kotlin` : plugin Kotlin 2.3.21 + `plugin.spring`, `kotlin-reflect`,
  `kotlinx-coroutines-core` / `-reactor`, `jackson-module-kotlin` (Jackson 3,
  `tools.jackson.module`), `kotlin-logging-jvm`, `resilience4j-kotlin`, ktlint,
  detekt ;
- `lang-java` : JSpecify, Error Prone + NullAway (plugin `net.ltgt.errorprone`),
  Spotless + `google-java-format` ;
- `serving-cli` : `clikt` (Kotlin) ; `serving-cli-java` : `picocli` (Java).

**Absents par conception** : `spring-ai-advisors-vector-store` (n'existe plus
en 2.x) et `spring-ai-vector-store-advisor` (suppose un `VectorStore`),
`spring-ai-starter-model-azure-openai` et `-vertex-ai-gemini` (aucune 2.x),
`langchain4j` (deux abstractions de fournisseur), `opentelemetry-spring-boot-starter`
(seconde instrumentation), `com.fasterxml.jackson.module:jackson-module-kotlin`
(Jackson 2 sous Boot 4).

---

## 3. Mapping des concepts SDD_Agents → idiomes Spring AI

### 3.1 Agent

**Kotlin**

```kotlin
// agents/billing/BillingAgent.kt — package {package}.agents.billing
class BillingAgent(
    private val chat: ChatClient,          // construit par la composition depuis RuntimeTierMap
    private val prompts: PromptLoader,     // hash vérifié
    private val bounds: Bounds,
    private val spans: AgentSpans,
) {
    fun run(input: BillingInput, ctx: RunContext): BillingOutput =
        spans.agentTurn(AGENT, ctx.threadId, TIER, prompts.hash("billing-agent"), bounds, ctx.iteration) {
            chat.prompt()
                .system(prompts.load("billing-agent").text)
                .user(input.asUserMessage())
                .toolCallbacks(ctx.tools.budgeted(bounds))          // outils du contrat, comptés (§3.2)
                .toolContext(ctx.toolContext())                    // identité de l'appelant — jamais visible du modèle
                .call()
                .entity(BillingOutput::class.java)                 // sortie structurée dérivée du contrat
                ?: throw ClassifiedException("AGENT_OUTPUT_INVALID")
        }
}
```

**Java**

```java
// agents/billing/BillingAgent.java — package {package}.agents.billing
public final class BillingAgent {
    private final ChatClient chat;          // construit par la composition depuis RuntimeTierMap
    private final PromptLoader prompts;     // hash vérifié
    private final Bounds bounds;
    private final AgentSpans spans;

    public BillingOutput run(BillingInput input, RunContext ctx) {
        return spans.agentTurn(AGENT, ctx.threadId(), TIER, prompts.hash("billing-agent"), bounds, ctx.iteration(), span -> {
            BillingOutput out = chat.prompt()
                    .system(prompts.load("billing-agent").text())
                    .user(input.asUserMessage())
                    .toolCallbacks(ctx.tools().budgeted(bounds))    // outils du contrat, comptés (§3.2)
                    .toolContext(ctx.toolContext())                 // identité de l'appelant — jamais visible du modèle
                    .call()
                    .entity(BillingOutput.class);                   // sortie structurée dérivée du contrat
            if (out == null) throw new ClassifiedException("AGENT_OUTPUT_INVALID");
            return out;
        });
    }
}
```

`ChatClient.ChatClientRequestSpec` expose `system`, `user`, `tools`,
`toolCallbacks`, `toolContext`, `advisors` (vérifié sur 2.0.1). Kotlin appelle
l'API Java telle quelle : l'agent est synchrone parce que l'API l'est ; le
streaming passe par `.stream()` (Reactor `Flux`, ponté par
`kotlinx-coroutines-reactor` en Kotlin).

### 3.2 Bornes et boucle

Spring AI exécute les appels d'outils **à l'intérieur** de `.call()` sans
exposer de compteur que l'agent contrôle. La borne `maxToolCalls` est donc
posée **sur les outils eux-mêmes** : `ctx.tools().budgeted(bounds)` enveloppe
chaque `ToolCallback` dans un `BudgetedToolCallback` (classe maison,
`ToolCallback` délégant) qui incrémente un compteur **par run** et, au-delà de
la borne, refuse l'appel et lève `BoundExceededException("maxToolCalls")` —
que l'agent convertit en `RunOutcome.BoundExceeded` selon `onBoundExceeded`.
`maxIterations` est posé par la boucle de `orchestration/`, jamais par une
option du client. Aucune classe Spring AI ne fait office de borne : ni une
option de modèle, ni un advisor (Spring AI 2.0.1 livre un `ToolCallingAdvisor`,
dont aucun réglage de borne n'est vérifié ici).

### 3.3 Outils

`@Tool(name = …, description = …)` sur une méthode dont l'entrée est un
`record` / une `data class` **générée** depuis le contrat, ou un
`FunctionToolCallback` ; la description est une **constante générée** depuis
le contrat (c'est du prompt : P1). L'identité de l'appelant vient de
`ToolContext` (`ChatClient…toolContext(Map)`, lue par un paramètre
`ToolContext` de la méthode), jamais d'un paramètre exposé au modèle ; la
sortie `untrusted` est enveloppée au parsing. Les outils MCP passent par
`tools/mcp-jvm.md`, les vues SQL par `dataaccess/view-per-agent-jvm.md`.

### 3.4 Traces

Spring AI émet des observations Micrometer (`gen_ai.client.operation` pour
chat et embedding, `spring.ai.tool`, `spring.ai.chat.client`,
`spring.ai.advisor`) que Spring Boot 4 ponte vers OpenTelemetry. Leurs noms ne
sont pas tous ceux que le framework lit (`gen_ai.system` au lieu de
`gen_ai.provider.name`, `embedding` au lieu de `embeddings`…) :
`observability/otel-genai-jvm.md` normalise à l'écriture du fichier JSONL et
écarte les observations qui doubleraient un span. Le coût est recalculé depuis
les tokens d'`Usage`.

### 3.5 Mémoire

`MessageChatMemoryAdvisor` + `ChatMemory` : en mémoire pour un processus
unique (`cli-exe` sans reprise), `spring-ai-starter-model-chat-memory-repository-jdbc`
dès que deux processus doivent se voir (`backend-api` à plusieurs répliques,
reprise après `escalate-human`). La politique (rétention, PII) vient du contrat
de mémoire, pas du starter.

---

## 4. Conventions imposées

1. **Un `ChatClient` par tier**, construit par la composition depuis
   `RuntimeTierMap` ; aucun nom de modèle dans un agent.
2. **Toute boucle porte `Bounds`** ; tout `ToolCallback` passé au modèle est
   `budgeted` (§3.2) ; `.call()` ne fait jamais office de boucle d'agent.
3. **Prompts chargés par hash**, jamais inline — ni chaîne, ni *text block*.
4. **Sortie structurée dérivée du contrat** (`.entity(...)`) ; une conversion
   qui échoue est une erreur nommée.
5. **Mémoire partagée** dès qu'il y a plus d'un processus.
6. **Pas de `VectorStore` Spring AI** pour un index mesuré : le retrieval passe
   par `rag/hybrid-jvm.md` (`DocumentRetriever` sur `rag.chunks`).
7. **Pas d'auto-câblage d'outils** : ni `ToolCallbackProvider` publié par un
   starter (MCP : `spring.ai.mcp.client.toolcallback.enabled: false`), ni scan
   global de `@Tool` ; chaque agent reçoit la liste de son contrat.
8. **Une seule plateforme par famille** : `spring-boot-dependencies` 4.1.1 et
   `spring-ai-bom` 2.0.1, rien surclassé à la main.

---

## 5. Smoke

```bash
cd workspace/src/{AppName}
./gradlew ktlintCheck detekt build          # Kotlin
./gradlew spotlessCheck build               # Java (Error Prone + NullAway dans compileJava)
test -f build/libs/{AppName}.jar
cd ../../..
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar health --json
python .sdda/sdda.py diff-code-vs-ir --mission {n} --json
```

---

## 6. Contrat d'exécution

L'application Spring AI implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** (commandes, NDJSON `RunEvent`, codes de sortie), et les trois
points de `serving/cli.md` §3.5 :

1. `run --json --input-file -` lit l'entrée sur `stdin` ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'application sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil) et le retrieval
   figé depuis le même dossier, sans aucun appel réseau d'outil — pour Spring
   AI : la composition remplace chaque `ToolCallback` du contrat par un
   `FixtureToolCallback` de **même** `ToolDefinition` (le modèle voit les mêmes
   outils, seule l'exécution change) et le `DocumentRetriever` par un
   `FrozenRetriever` ; le `ChatModel`, lui, reste réel — c'est l'agent qu'on
   évalue ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished`,
   sans aucun appel au `ChatModel`.

Commande de lancement : `java -jar workspace/src/{AppName}/build/libs/{AppName}.jar`
depuis la racine du dépôt (livrable) ; `./gradlew run --args='…'` en
développement. Les runners la dérivent par `--executor cli` (Kotlin et Java :
la même) ou l'imposent par `--executor cmd:<commande>`. Le build produit
exactement ce jar (`tasks.bootJar { archiveFileName = "{AppName}.jar" }`,
`tasks.jar { enabled = false }`).

---

## 7. Pièges connus

1. **Mélanger Spring AI 2.x et Spring Boot 3.x.** Spring AI 2.0.1 est compilé
   contre Boot 4.1.1 / Framework 7.0.9 : sous Boot 3.5, des auto-configurations
   manquent et le démarrage échoue loin de la cause.
2. **Starters 1.x dans une BOM 2.x.** `spring-ai-starter-model-vertex-ai-gemini`
   et `-azure-openai` n'existent qu'en 1.1.x ; les déclarer à côté de la BOM
   2.0.1 tire des modules 1.x incompatibles.
3. **`.call()` comme boucle.** Les appels d'outils internes ne sont pas comptés
   par défaut : sans `BudgetedToolCallback`, `maxToolCalls` est une intention.
4. **Deux abstractions de fournisseur.** Spring AI **ou** LangChain4j, jamais
   les deux.
5. **`ChatMemory` en mémoire en production.** Même piège que le checkpointer :
   une reprise échoue sans erreur claire.
6. **Cycle sans boucle écrite.** `supervisor` / `reflection` avec Spring AI seul
   est une topologie que rien ne borne : G2 l'exige (`loop_bound`), la fiche le
   répète.
7. **Jackson 2 et Jackson 3 ensemble.** `com.fasterxml.jackson.module:jackson-module-kotlin`
   (Jackson 2) ajouté « parce que les exemples le montrent » à côté de Boot 4
   (Jackson 3) : deux `ObjectMapper`, deux formes du même `RunEvent`.
8. **Outils câblés par le starter MCP.** Voir `tools/mcp-jvm.md` §8.1 : chaque
   outil de chaque serveur devient disponible au modèle.
