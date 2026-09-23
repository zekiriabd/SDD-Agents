# Stack: langgraph-js (framework)

> §2.3 (Librairies) suit `langgraph-js.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: framework-langgraph-js
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: LangGraph.js — graphe d'état, cycles **bornés**, checkpointing, reprise après interruption (HITL), avec `@langchain/core` pour les outils, les messages et les sorties structurées. Suppose `lang/typescript.md`. C'est la cible TypeScript retenue par la ROADMAP **à la place** de LangChain.js seul : LCEL ne borne pas les boucles et ne persiste pas l'état, or P12 et la reprise sont structurants. Sans générateur de squelette TypeScript ni fiche `eval/` / `observability/` dans ce langage, aucune combo de bootstrap ne l'active encore (cf. `lang/typescript.md`).

---

## 1. Rôle et périmètre

LangGraph.js est le pendant TypeScript de `framework/langgraph.md` : la même
idée — un graphe d'état explicite dont chaque nœud est une fonction, chaque
arête une décision nommée, chaque cycle une borne — dans l'écosystème Node.
Ce qui compte pour SDD_Agents, et que la fiche impose :

| Besoin du framework | Ce que LangGraph.js apporte | Ce que la fiche exige en plus |
|---|---|---|
| graphe = topologie de l'IR | `StateGraph` avec nœuds et arêtes conditionnelles nommés | le graphe est **compilé depuis l'IR**, jamais dessiné à la main ; `diff-code-vs-ir` le compare |
| cycles bornés (P12) | `recursionLimit` | insuffisant seul : `maxIterations`, `maxToolCalls`, `maxDelegationDepth` vivent **dans l'état** et sont vérifiés à chaque nœud |
| reprise, HITL | checkpointer (`MemorySaver`, SQLite, Postgres) + `interrupt` | checkpointer **partagé** dès qu'il y a plus d'un processus |
| trajectoire observable (L5) | événements de nœud (`streamEvents`) | chaque nœud émet un span OTel-GenAI ; le coût est recalculé depuis les tokens |
| outils typés | `tool()` de `@langchain/core` + Zod | schéma Zod **généré** depuis le contrat d'outil ; sortie `untrusted` enveloppée au parsing |

Hors périmètre : la surface (`serving/cli-node.md`), la maison HTTP
(`backend/*`), le retrieval (aucune fiche `rag/` TypeScript : un projet
LangGraph.js est aujourd'hui `rag/none.md`, et c'est dit).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `framework-langgraph-js` |
| **Langage** | TypeScript 6.0.x, Node 22 (`lang/typescript.md`) |
| **Framework** | `@langchain/langgraph` 1.x · `@langchain/core` 1.x |
| **Fournisseurs** | `@langchain/anthropic`, `@langchain/openai`, `@langchain/google-genai` — ON-DEMAND selon `RuntimeProvider` |
| **Checkpointer** | `MemorySaver` en dev **seulement** ; `@langchain/langgraph-checkpoint-sqlite` (mono-processus) ou `-postgres` (partagé) sinon |
| **Outils MCP** | `@langchain/mcp-adapters` — ON-DEMAND si `tools/mcp.md` actif |
| **Build** | `pnpm` |
| **Combinaisons** (SSoT : `compatibility.matrix.json`) | `typescript × langgraph-js × {single-agent, router, sequential, parallel, supervisor, graph, reflection, plan-execute}` — toutes `untested` |

### 2.1 Init

Depuis `lang/typescript.md` §2.1, puis les paquets CORE du catalogue, versions
épinglées.

### 2.2 Patterns d'erreurs

- `GraphRecursionError` : la borne de récursion du graphe — **doit** être
  précédée par la borne applicative (`maxIterations` dans l'état), sinon c'est
  le framework qui décide de la limite, pas la MISSION ;
- `ToolInputParsingException` / échec Zod : l'outil a reçu un argument que son
  contrat refuse — remonté au modèle comme erreur nommée, jamais avalé ;
- `AbortError` : annulation propagée depuis la surface (déconnexion, deadline).

### 2.3 Librairies

Source de vérité : `langgraph-js.libs.json`.

**CORE** : `@langchain/langgraph`, `@langchain/core`, `zod`, `pino`,
`typescript`, `tsx`, `@types/node`, `eslint`, `typescript-eslint`, `vitest`.

**ON-DEMAND** : `@langchain/anthropic` (`provider-anthropic`),
`@langchain/openai` (`provider-openai`), `@langchain/google-genai`
(`provider-google`), `@langchain/langgraph-checkpoint-sqlite` (`checkpoint-sqlite`),
`@langchain/langgraph-checkpoint-postgres` (`checkpoint-postgres`),
`@langchain/mcp-adapters` (`mcp`), `@opentelemetry/sdk-node` + `@opentelemetry/api`
+ `@opentelemetry/exporter-trace-otlp-http` (`otel`), `langsmith` (`tracing-langsmith`).

**Absents par conception** : `langchain` (le méta-paquet : `createReactAgent`
et les agents pré-câblés cachent un graphe non borné), `@langchain/community`
(surface trop large, pins instables).

---

## 3. Mapping des concepts SDD_Agents → idiomes LangGraph.js

### 3.1 État et bornes

```ts
const RunState = Annotation.Root({
  messages: Annotation<BaseMessage[]>({ reducer: (a, b) => a.concat(b) }),
  iterations: Annotation<number>({ reducer: (_, b) => b, default: () => 0 }),
  toolCalls: Annotation<number>({ reducer: (a, b) => a + b, default: () => 0 }),
  bounds: Annotation<Bounds>(),
  identity: Annotation<Identity>(),
});
```

Chaque nœud d'agent incrémente `iterations` et vérifie `bounds` **avant**
d'appeler le modèle ; le dépassement rend un état terminal `bound_exceeded`
avec la borne nommée, selon `onBoundExceeded`.

### 3.2 Le graphe est compilé depuis l'IR

`orchestration/graph.ts` est **généré** : nœuds = `agents[]`, arêtes =
`orchestration.edges` (avec `countsAsHop`), conditions = fonctions de routage
nommées d'après la condition de l'IR, `entryNode`, nœuds terminaux.
`graph.getGraph().drawMermaid()` est comparé au bloc ```mermaid de la topologie
par `diff-code-vs-ir` : un nœud en plus ou en moins est `[ORCH_DIVERGES_FROM_IR]`.

### 3.3 Outils

```ts
const orders_lookup = tool(async (input, config) => envelope.lookup("orders", input, config.configurable.identity),
  { name: "orders_lookup", description: ORDERS_LOOKUP_DESCRIPTION, schema: OrdersLookupInput });
```

`description` et `schema` sont **générés** depuis le contrat ; l'identité de
l'appelant vient de `config.configurable`, jamais des arguments du modèle.

### 3.4 Reprise et HITL

`interrupt()` dans un nœud + `Command({ resume })` ; le `thread_id` est le
`run_id`. Avec `HumanInTheLoopEnabled: true`, le checkpointer est partagé et
`/readyz` le vérifie.

### 3.5 Traces

`streamEvents(..., { version: "v2" })` alimente un exporteur qui émet un span
`sdda.agent.turn`, `sdda.tool.call`, `sdda.llm.call` par événement, avec
`run_id`, `trace_id`, `parent_span_id` — le format de
`observability/otel-genai.md`. Le coût est recalculé depuis
`usage_metadata`, jamais relu depuis un attribut déclaré.

---

## 4. Conventions imposées

1. **Aucun agent pré-câblé** (`createReactAgent`, `langchain/agents`) : le
   graphe est celui de l'IR, écrit nœud par nœud.
2. **Toute boucle porte `Bounds` dans l'état** ; `recursionLimit` est une
   ceinture, pas la borne.
3. **Prompts chargés par hash** (`lang/typescript.md` §3.1), jamais inline.
4. **Sorties structurées** : `withStructuredOutput(schema)` avec un schéma Zod
   dérivé du `outputSchema` du contrat ; une sortie non conforme est une erreur
   nommée.
5. **Checkpointer partagé** dès qu'il y a plus d'un processus.
6. **Un seul fournisseur actif par tier** : résolu depuis `RuntimeTierMap`, jamais
   depuis un nom de modèle en dur dans un agent.

---

## 5. Smoke

```bash
cd workspace/src/{AppName}
pnpm install --frozen-lockfile && pnpm tsc --noEmit && pnpm eslint . && pnpm vitest run
python .sdda/sdda.py diff-code-vs-ir --mission {n} --json     # le graphe compilé est celui de l'IR
```

---

## 6. Pièges connus

1. **`recursionLimit` pris pour la borne.** Il compte des super-steps du
   graphe, pas des tours d'agent ni des appels d'outils ; la MISSION borne ces
   derniers.
2. **`MemorySaver` en production.** Une reprise après redémarrage échoue sans
   erreur claire.
3. **`@langchain/community` « pour un seul connecteur ».** Il tire des dizaines
   de paquets non épinglés.
4. **L'identité dans les arguments d'outil.** Le modèle peut la fournir ; elle
   vient de `configurable`.
5. **Pas de RAG.** Aucune fiche `rag/` TypeScript : activer `rag/hybrid.md`
   ([python]) sur cette stack est refusé par `preflight_stack_combo`.
