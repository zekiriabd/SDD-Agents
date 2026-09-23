# Stack: spring-ai (framework)

> §2.3 (Librairies) suit `spring-ai.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: framework-spring-ai
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin
Scope: Spring AI — `ChatClient`, outils typés (`@Tool`), sorties structurées, client MCP, mémoire de conversation, sur la JVM en Kotlin. Suppose `lang/kotlin.md`. **Sans graphe borné natif** : Spring AI n'a pas d'équivalent de LangGraph. Les patterns à cycle (`supervisor`, `reflection`, `plan-execute`, `graph`) exigent une boucle d'orchestration **écrite** dans `orchestration/`, bornée par `Bounds` et persistée par un checkpointer maison — la fiche le dit, et `architecture-requirements.yml` l'exige déjà. Retenu à la place de Semantic Kernel Java (portage non poussé par Microsoft) et de LangChain4j (même absence de graphe, écosystème plus étroit). Aucune combo de bootstrap ne l'active encore (cf. `lang/kotlin.md`) ; pins `versionsVerified: false`.

---

## 1. Rôle et périmètre

Spring AI apporte à la JVM une abstraction de fournisseur propre
(`ChatModel`, `ChatClient`), une définition d'outils par annotation ou par
fonction, la conversion de sorties en types Kotlin, et un client MCP intégré au
conteneur Spring. Il est **suffisant** pour :

| Pattern | Condition |
|---|---|
| `single-agent` | ≤ 2 tours d'outils, boucle écrite et bornée (§3.2) |
| `sequential` | pipeline d'appels typés, sans retour arrière |
| `parallel` | coroutines `async {}` + fusion déterministe |
| `router` | classification puis dispatch en un passage |

Il est **insuffisant seul** — et `orchestration/` doit alors porter une boucle
explicite avec état persisté — dès que la TOPOLOGY comporte un cycle, une
reprise (`HumanInTheLoopEnabled`), `escalate-human`, ou `max_delegation_depth > 0`.
Ce n'est pas un défaut de la fiche : c'est la raison pour laquelle
`registry/architecture-requirements.yml` exige `loop_bound` déclaré pour ces
patterns, quel que soit le framework.

Hors périmètre : la surface (`serving/cli-kotlin.md`), la maison HTTP
(`backend/kotlin-spring-boot.md`), le retrieval (aucune fiche `rag/` JVM —
Spring AI a des `VectorStore`, mais sans fiche mesurée c'est `rag/none.md`).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `framework-spring-ai` |
| **Langage** | Kotlin 2.x, JDK 21 (`lang/kotlin.md`) |
| **Framework** | Spring AI 1.x via BOM `spring-ai-bom` ; Spring Boot 3.5 pour le conteneur |
| **Fournisseurs** | `spring-ai-starter-model-anthropic`, `-openai`, `-azure-openai`, `-vertex-ai-gemini` — ON-DEMAND selon `RuntimeProvider` |
| **Outils MCP** | `spring-ai-starter-mcp-client` — ON-DEMAND si `tools/mcp.md` actif |
| **Mémoire** | `ChatMemory` en mémoire (dev) ; JDBC (`spring-ai-starter-model-chat-memory-repository-jdbc`) partagé sinon |
| **Build** | Gradle Kotlin DSL, BOM importé |
| **Combinaisons** (SSoT : `compatibility.matrix.json`) | `kotlin × spring-ai × {single-agent, router, sequential, parallel}` ; les patterns à cycle exigent une boucle écrite — toutes `untested` |

### 2.1 Init

Depuis `lang/kotlin.md` §2.1, puis `platform(libs.spring.ai.bom)` et les
starters du catalogue.

### 2.2 Patterns d'erreurs

- `NonTransientAiException` / `TransientAiException` : la seconde est
  rejouable avec backoff (Resilience4j), la première jamais ;
- échec de conversion de sortie structurée : erreur nommée, jamais une chaîne
  brute passée au tour suivant ;
- `ToolExecutionException` : l'outil a refusé l'argument — remonté au modèle
  comme erreur d'outil, pas comme exception applicative.

### 2.3 Librairies

Source de vérité : `spring-ai.libs.json`.

**CORE** : `spring-ai-bom` (plateforme), `spring-ai-client-chat`,
`spring-boot-starter`, `kotlinx-coroutines-core`, `kotlinx-coroutines-reactor`,
`jackson-module-kotlin`, `kotlin-logging-jvm`, `resilience4j-kotlin`,
`kotest-runner-junit5`, `kotest-assertions-core`.

**ON-DEMAND** : starters fournisseur (`provider-*`), `spring-ai-starter-mcp-client`
(`mcp`), `spring-ai-starter-model-chat-memory-repository-jdbc` (`memory-shared`),
`opentelemetry-spring-boot-starter` (`otel`).

**Absents par conception** : `spring-ai-advisors-vector-store` et tout
`VectorStore` (aucune fiche `rag/` JVM mesurée), `langchain4j` (deux abstractions
de fournisseur dans un même projet).

---

## 3. Mapping des concepts SDD_Agents → idiomes Spring AI

### 3.1 Agent

```kotlin
class BillingAgent(
    private val chat: ChatClient,          // construit par la composition depuis RuntimeTierMap
    private val prompts: PromptLoader,     // hash vérifié
    private val bounds: Bounds,
) {
    suspend fun run(input: BillingInput, ctx: RunContext): BillingOutput =
        chat.prompt()
            .system(prompts.load("billing-agent").text)
            .user(input.asUserMessage())
            .tools(ctx.tools)                                  // outils du contrat, identité dans ctx
            .call()
            .entity(BillingOutput::class.java)                 // sortie structurée dérivée du contrat
}
```

### 3.2 Bornes et boucle

Spring AI exécute les appels d'outils **à l'intérieur** de `.call()` sans
exposer un compteur : la borne `maxToolCalls` est posée par un
`ToolCallbackDecorator` qui compte et refuse au-delà, et `maxIterations` par
la boucle de `orchestration/` — jamais par une option du client. Un
dépassement rend `RunOutcome.BoundExceeded(bound)` selon `onBoundExceeded`.

### 3.3 Outils

`@Tool(description = ORDERS_LOOKUP_DESCRIPTION)` sur une fonction dont le
schéma d'entrée est une data class **générée** depuis le contrat ; l'identité
de l'appelant vient de `ToolContext`, jamais d'un paramètre exposé au modèle ;
la sortie `untrusted` est enveloppée au parsing.

### 3.4 Traces

Spring AI est instrumenté Micrometer : les observations `gen_ai.*` sont
exportées en OTel et **re-nommées** vers le format `observability/otel-genai.md`
(`sdda.llm.call`, `sdda.tool.call`) par un `ObservationFilter` ; le coût est
recalculé depuis les tokens d'`Usage`.

---

## 4. Conventions imposées

1. **Un `ChatClient` par tier**, construit par la composition depuis
   `RuntimeTierMap` ; aucun nom de modèle dans un agent.
2. **Toute boucle porte `Bounds`** ; `.call()` ne fait jamais office de boucle
   d'agent au-delà de `maxToolCalls`.
3. **Prompts chargés par hash**, jamais inline.
4. **Sortie structurée dérivée du contrat** (`.entity(...)`) ; une conversion
   qui échoue est une erreur nommée.
5. **Mémoire partagée** dès qu'il y a plus d'un processus.
6. **Pas de `VectorStore`** tant qu'aucune fiche `rag/` JVM n'existe.

---

## 5. Smoke

```bash
cd workspace/src/{AppName}
./gradlew ktlintCheck detekt build test
python .sdda/sdda.py diff-code-vs-ir --mission {n} --json
```

---

## 6. Pièges connus

1. **Les pins ne sont pas vérifiés** (`versionsVerified: false`) : résoudre
   contre Maven Central avant le premier bootstrap réel.
2. **`.call()` comme boucle.** Les appels d'outils internes ne sont pas comptés
   par défaut : sans décorateur, `maxToolCalls` est une intention.
3. **Deux abstractions de fournisseur.** Spring AI **ou** LangChain4j, jamais les
   deux.
4. **`ChatMemory` en mémoire en production.** Même piège que le checkpointer :
   une reprise échoue sans erreur claire.
5. **Cycle sans boucle écrite.** `supervisor`/`reflection` avec Spring AI seul
   est une topologie que rien ne borne : G2 l'exige (`loop_bound`), la fiche le
   répète.
