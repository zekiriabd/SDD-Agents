# Stack: otel-genai-node (observability)

> §2.3 (Librairies) suit `otel-genai-node.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: observability-otel-genai-node
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: observabilité de l'application TypeScript via **OpenTelemetry JS** et les **conventions sémantiques GenAI** — mêmes spans, mêmes attributs, même double export que `observability/otel-genai.md`, et surtout **le même fichier de trace** : `workspace/.sys/traces/runs/{run-id}.jsonl`, un span par ligne, champs `run_id` / `trace_id` / `span_id` / `parent_span_id`, que les scripts du framework relisent (invariant `trace-emitted-per-run`, coût mesuré de G6, trajectoire L5, scope d'outils G7, citations G4). LangChain.js n'émettant **pas** de spans `gen_ai.*` natifs, les spans sont écrits à la main (helpers imposés + un `BaseCallbackHandler` pour les appels de modèle). Suppose `lang/typescript.md`.

---

## 1. Rôle et périmètre

Tout le §1 de `observability/otel-genai.md` vaut ici : sans trace, un système
non déterministe n'est pas débogable, et la trace est l'artefact sur lequel
s'appuient la L5, la G6, la console et le post-mortem.

Ce qui rend cette fiche **plus contraignante** que son pendant Python, c'est
que la trace n'est pas lue par l'application : elle est lue par
`.sdda/python/sdda_lib/tracing.py` — donc par du Python, qui ne sait rien du
langage qui l'a écrite. Le fichier JSONL est un **contrat d'échange** entre
l'application et le framework, au même titre que le NDJSON de la CLI. Une
application Node qui écrirait des spans « à peu près » OTel (noms `sdda.agent.turn`,
tokens sous un autre nom, pas de `parent_span_id`) produirait des gates vertes à
tort : aucun appel d'outil vu, donc aucun excès de scope ; aucun token, donc un
coût mesuré à zéro, sous tous les plafonds. C'est exactement le faux vert que
`tracing.py` documente pour l'ancien format d'événements.

### 1.1 Honnêteté : LangChain.js et `gen_ai.*`

LangChain.js et LangGraph.js **n'émettent pas** de spans OpenTelemetry
`gen_ai.*`. Les options du marché ne conviennent pas :

| Voie | Pourquoi elle n'est pas retenue |
|---|---|
| `@arizeai/openinference-instrumentation-langchain` | convention **OpenInference**, pas `gen_ai.*` : ni `gen_ai.operation.name`, ni `gen_ai.usage.input_tokens` là où `tracing.py` les lit |
| `@traceloop/instrumentation-langchain` | noms d'attributs d'un tiers, et une seconde source de spans LLM qui doublerait le coût mesuré |
| LangSmith | tracing managé, hors du fichier JSONL ; toléré en complément (`framework/langgraph-js.libs.json`), jamais à la place |

Les spans sont donc **écrits à la main** : des helpers pour ce que le code de
l'application fait lui-même (tour d'agent, outil, retrieval, requête de vue,
guardrail), et **un** `BaseCallbackHandler` pour ce que LangChain.js fait à sa
place (l'appel au modèle, dont il connaît les tokens). C'est ce que
`observability/otel-genai.md` §3.3 prévoit déjà : les agents n'importent jamais
OpenTelemetry, seulement les helpers.

Les conventions GenAI restent en statut *Development* (`otel-genai.md` §1.1) :
mêmes conséquences — noms dans **un seul module** (`app/tracing/semconv.ts`),
paquet `@opentelemetry/semantic-conventions` épinglé, `semconv_version` dans
chaque ligne.

---

## 2. Identité

### 2.1 Identité

- **Stack ID** : `observability-otel-genai-node`
- **Langage** : TypeScript 6.0.x, Node 22 (`lang/typescript.md`)
- **SDK** : `@opentelemetry/api` 1.9.x · `@opentelemetry/sdk-trace-node` 2.11.x (`NodeTracerProvider`, contexte `AsyncLocalStorage`) · `@opentelemetry/sdk-trace-base` (processeurs, `SpanExporter`, `ReadableSpan`)
- **Export** : `JsonlSpanExporter` maison vers `workspace/.sys/traces/runs/{run-id}.jsonl` (**toujours**) **+** OTLP/HTTP vers `OTEL_EXPORTER_OTLP_ENDPOINT` (si déclaré)
- **Paramètres STACK.md** : `TraceLevel: full`, `TraceSampleRate: 1.0`, `TracePIIPolicy: redact`, `CostTrackingEnabled: true` — inchangés
- **Emplacement** : `workspace/src/{AppName}/app/tracing/` — zone de `dev-backend` (la coquille), lue par toutes les couches

### 2.2 Pourquoi `NodeTracerProvider` et pas `NodeSDK`

`@opentelemetry/sdk-node` (NodeSDK) configure processeurs, exporteurs et
instrumentations depuis des variables d'environnement. Pratique, mais l'ordre
qui fait l'invariant de cette fiche — **redaction, puis JSONL immédiat, puis
OTLP par lot** — n'est plus lisible dans le code, et un `OTEL_TRACES_EXPORTER`
posé par l'environnement peut le changer. `NodeTracerProvider({ resource,
spanProcessors: [...] })` le rend explicite. `sdk-node` est donc absent par
conception (`otel-genai-node.libs.json`).

### 2.3 Librairies

Source de vérité : `otel-genai-node.libs.json`.

**CORE** : `@opentelemetry/api`, `@opentelemetry/sdk-trace-node`,
`@opentelemetry/sdk-trace-base`, `@opentelemetry/resources`,
`@opentelemetry/core`, `@opentelemetry/exporter-trace-otlp-http`,
`@opentelemetry/semantic-conventions`, `ulid`, `zod`, `pino`.
**ON-DEMAND** : `@opentelemetry/instrumentation-pg` (`instrument-pg`).
**Absents par conception** : `@opentelemetry/sdk-node`,
`@arizeai/openinference-instrumentation-langchain`,
`@traceloop/instrumentation-langchain`, `langsmith` comme couche runtime.

---

## 3. Mapping des concepts SDD_Agents → spans et attributs

### 3.1 Les spans à émettre

La table de `observability/otel-genai.md` §3.1 est **la** table, sans
modification : noms de spans, `gen_ai.operation.name`, attributs standard,
extension `sdda.*`, hiérarchie `sdda.run` → `invoke_agent` → (`chat` \|
`execute_tool` \| `sdda.retrieve` → `embeddings` + `sdda.data.query`), statut
`ERROR` + `error.type`. Les métriques de §3.2 aussi.

Les noms que `tracing.py` reconnaît, et donc les seuls admis :

| Rôle lu par le framework | Nom du span | Clé qui le fait reconnaître |
|---|---|---|
| run (racine) | `sdda.run {mission_id}` | préfixe `sdda.run` ; **sans** `parent_span_id` |
| agent | `invoke_agent {agent_name}` | `gen_ai.operation.name = invoke_agent` + `gen_ai.agent.id`, `gen_ai.agent.name` |
| llm | `chat {model}` | `gen_ai.operation.name = chat` + `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `sdda.usage.cache_read_tokens`, `sdda.usage.cache_write_tokens` |
| tool | `execute_tool {tool}` | `gen_ai.operation.name = execute_tool` + `gen_ai.tool.name`, `sdda.tool.id`, `sdda.tool.side_effect_class` |
| embedding | `embeddings {model}` | `gen_ai.operation.name = embeddings` |
| retrieval | `sdda.retrieve {index_id}` | préfixe + `sdda.retrieval.result.ids` |
| data | `sdda.data.query {view}` | préfixe |
| guardrail | `sdda.guardrail {id}` | préfixe |

Un nom hors de cette table (`sdda.agent.turn`, `sdda.llm.call`,
`sdda.tool.call`) n'a **aucun rôle** pour `tracing.py` : le span est lu, puis
ignoré. Le coût vient des tokens du span `chat` et de la table de tarifs du
framework ; `sdda.cost.usd` est enregistré (il sert aux bornes budgétaires de
l'application), jamais cru.

### 3.2 Le format de ligne — un contrat, pas une préférence

```json
{"semconv_version":"1.43.0","run_id":"01J…","trace_id":"4bf9…","span_id":"00f0…","parent_span_id":"a3ce…",
 "name":"execute_tool billing_specialist_unpaid_invoices","start":"2026-09-20T14:12:03.120Z","end":"2026-09-20T14:12:03.432Z",
 "duration_ms":312,"status":"OK",
 "attributes":{"gen_ai.operation.name":"execute_tool","gen_ai.tool.name":"billing_specialist_unpaid_invoices","sdda.tool.id":"1-billing-specialist-unpaid-invoices","sdda.tool.side_effect_class":"read-only","sdda.tool.args":"{\"customer_id\":\"[REDACTED:uuid]\"}"},
 "events":[]}
```

Champs obligatoires sur **chaque** ligne : `run_id`, `trace_id`, `span_id`,
`name` (`SPAN_FIELDS` de `tracing.py`). `parent_span_id` est présent sur tout
span sauf la racine — c'est à son **absence** que la racine se reconnaît, donc
il n'est jamais écrit `""` ni `null` sur un enfant, et **omis** sur la racine.
`start`/`end` en ISO 8601 UTC avec `Z` ; `duration_ms` entier ; `status`
`"OK"` ou `"ERROR"`. Identifiants en hexadécimal minuscule (32 caractères pour
`trace_id`, 16 pour `span_id`), tels que les rend `spanContext()`.
`semconv_version` = la version épinglée de `@opentelemetry/semantic-conventions`.

### 3.3 L'exporteur JSONL

```ts
// workspace/src/{AppName}/app/tracing/jsonl-exporter.ts
import { appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { ExportResultCode, hrTimeToMilliseconds, type ExportResult } from "@opentelemetry/core";
import type { ReadableSpan, SpanExporter } from "@opentelemetry/sdk-trace-base";
import { SpanStatusCode } from "@opentelemetry/api";
import { SEMCONV_VERSION, SDDA_RUN_ID } from "./semconv.js";
import { TraceEventSchema } from "./trace-event.js";

const iso = (t: [number, number]) => new Date(hrTimeToMilliseconds(t)).toISOString();

export class JsonlSpanExporter implements SpanExporter {
  constructor(private readonly dir: string) { mkdirSync(dir, { recursive: true }); }

  export(spans: ReadableSpan[], done: (r: ExportResult) => void): void {
    try {
      for (const s of spans) {
        const runId = String(s.attributes[SDDA_RUN_ID] ?? "");          // posé sur CHAQUE span par RunIdSpanProcessor (§3.4)
        if (!runId) continue;                                            // un span hors run n'appartient à aucun fichier
        const parent = s.parentSpanContext?.spanId;                      // SDK 2.x : parentSpanContext (plus de parentSpanId)
        const line = TraceEventSchema.parse({
          semconv_version: SEMCONV_VERSION, run_id: runId,
          trace_id: s.spanContext().traceId, span_id: s.spanContext().spanId,
          ...(parent ? { parent_span_id: parent } : {}),
          name: s.name, start: iso(s.startTime), end: iso(s.endTime),
          duration_ms: Math.round(hrTimeToMilliseconds(s.duration)),
          status: s.status.code === SpanStatusCode.ERROR ? "ERROR" : "OK",
          attributes: s.attributes,
          events: s.events.map((e) => ({ name: e.name, time: iso(e.time), attributes: e.attributes ?? {} })),
        });
        // Une ligne ENTIÈRE par appel (O_APPEND). Un seul processus écrit un fichier de run : l'atomicité
        // entre processus n'est pas garantie sous Windows, et elle n'est pas demandée.
        appendFileSync(join(this.dir, `${runId}.jsonl`), JSON.stringify(line) + "\n", { encoding: "utf8" });
      }
      done({ code: ExportResultCode.SUCCESS });
    } catch (error) {
      done({ code: ExportResultCode.FAILED, error: error as Error });
    }
  }
  async shutdown(): Promise<void> {}
  async forceFlush(): Promise<void> {}
}
```

L'exporteur est branché sur un `SimpleSpanProcessor` : chaque span est écrit à
sa **fin**, synchrone — un run qui plante laisse sa trace jusqu'au point de
coupure. `appendFileSync` et non un flux : un `WriteStream` bufferise, et un
`process.exitCode` suivi d'une sortie rapide perd les dernières lignes — souvent
l'erreur finale, la plus utile. Le fichier est **par run** (le nom vient de
`run_id`), ce qui sert aussi la surface HTTP, où un processus porte plusieurs
runs concurrents.

### 3.4 Le provider et la chaîne de processeurs

```ts
// workspace/src/{AppName}/app/tracing/setup.ts
const provider = new NodeTracerProvider({
  resource: resourceFromAttributes({ "service.name": settings.appName, "service.version": settings.appVersion }),
  spanProcessors: [
    new RunIdSpanProcessor(),                                             // onStart : copie run_id du contexte en `sdda.run.id`
    new SimpleSpanProcessor(new RedactingSpanExporter(new JsonlSpanExporter(settings.traceDir), redactor)),
    ...(settings.otlpEndpoint
      ? [new BatchSpanProcessor(new RedactingSpanExporter(new OTLPTraceExporter({ url: settings.otlpEndpoint }), redactor))]
      : []),                                                              // absent → avertissement sur stderr, jamais d'erreur
  ],
});
provider.register();                                                      // contexte AsyncLocalStorage : le parent survit aux await
```

La redaction est un **exporteur enveloppant** (`RedactingSpanExporter`) et non un
processeur qui modifierait le span en `onEnd` : en JS, `onEnd` reçoit un
`ReadableSpan` dont les attributs ne sont pas faits pour être réécrits, et un
processeur placé avant un autre ne garantit pas que le second voie la version
redigée. L'enveloppe produit une **copie** redigée et la passe à l'exporteur
réel — la même copie pour le JSONL et pour l'OTLP. Les règles de redaction
sont celles de `otel-genai.md` §5.4 (champs `@pii`, regex e-mail / téléphone /
IBAN / carte, valeurs des secrets connus, `hash`), et les helpers redigent déjà
les arguments d'outil **avant** `setAttribute` : la redaction de l'exporteur est
le filet, pas la première ligne.

### 3.5 Les helpers imposés

```ts
// workspace/src/{AppName}/app/tracing/spans.ts — les SEULS points d'entrée du code applicatif vers OTel
const tracer = trace.getTracer("sdda");

export function agentTurn<T>(a: AgentTurnAttrs, fn: (span: AgentSpan) => Promise<T>): Promise<T> {
  return tracer.startActiveSpan(`invoke_agent ${a.agentName}`, { attributes: {
      [sc.GEN_AI_OPERATION_NAME]: "invoke_agent", [sc.GEN_AI_AGENT_ID]: a.agentId, [sc.GEN_AI_AGENT_NAME]: a.agentName,
      [sc.GEN_AI_CONVERSATION_ID]: a.threadId, [sc.SDDA_PROMPT_HASH]: a.promptHash, [sc.SDDA_AGENT_ITERATION]: a.iteration,
      [sc.SDDA_BOUNDS_MAX_ITERATIONS]: a.bounds.maxIterations, [sc.SDDA_BOUNDS_MAX_TOOL_CALLS]: a.bounds.maxToolCalls,
      [sc.SDDA_BOUNDS_BUDGET_USD]: a.bounds.budgetUsd } },
    async (span) => { try { return await fn(new AgentSpan(span)); }
                      catch (e) { span.setStatus({ code: SpanStatusCode.ERROR }); span.setAttribute(sc.ERROR_TYPE, errorClass(e)); throw e; }
                      finally { span.end(); } });
}

export function toolCall<T>(spec: ToolSpec, callId: string, args: Record<string, unknown>, fn: (s: ToolSpan) => Promise<T>): Promise<T>;
export function retrieval<T>(a: RetrievalAttrs, fn: (s: RetrievalSpan) => Promise<T>): Promise<T>;   // sdda.retrieval.result.ids / .scores / .provenance
export function dataQuery<T>(a: DataQueryAttrs, fn: (s: DataSpan) => Promise<T>): Promise<T>;
export function guardrail<T>(a: GuardrailAttrs, fn: (s: GuardrailSpan) => Promise<T>): Promise<T>;
export function runRoot<T>(a: RunAttrs, fn: (s: RunSpan) => Promise<T>): Promise<T>;                 // sdda.run {mission}, pose run_id dans le contexte
```

Le code des agents, des outils et du retrieval n'importe **jamais**
`@opentelemetry/*` : seulement `#app/app/tracing/spans.js`. Lint L0 (grep
`from "@opentelemetry` hors `app/tracing/`).

### 3.6 Le callback des appels de modèle

```ts
// workspace/src/{AppName}/app/tracing/llm-callback.ts
import { BaseCallbackHandler } from "@langchain/core/callbacks/base";

export class SddaLlmSpans extends BaseCallbackHandler {
  name = "sdda-llm-spans";
  awaitHandlers = true;                    // sinon exécuté en arrière-plan : le span `chat` se ferme après son parent, ou jamais
  private readonly open = new Map<string, Span>();

  constructor(private readonly parent: Context, private readonly tier: string) { super(); }  // contexte du tour d'agent, EXPLICITE

  override handleChatModelStart(llm: Serialized, _m: BaseMessage[][], runId: string, _p?: string, extra?: Record<string, unknown>) {
    const model = requestModelOf(llm, extra);                            // nom de modèle demandé, depuis les paramètres d'invocation
    this.open.set(runId, tracer.startSpan(`chat ${model}`, { attributes: {
      [sc.GEN_AI_OPERATION_NAME]: "chat", [sc.GEN_AI_PROVIDER_NAME]: providerOf(llm), [sc.GEN_AI_REQUEST_MODEL]: model,
      [sc.SDDA_MODEL_TIER]: this.tier } }, this.parent));
  }

  override handleLLMEnd(output: LLMResult, runId: string) {
    const span = this.open.get(runId); if (!span) return;
    const msg = (output.generations[0]?.[0] as ChatGeneration | undefined)?.message as AIMessage | undefined;
    const u = msg?.usage_metadata;                                       // input_tokens, output_tokens, input_token_details.{cache_read,cache_creation}
    if (!u && settings.costTrackingEnabled) { span.setStatus({ code: SpanStatusCode.ERROR }); span.setAttribute(sc.ERROR_TYPE, "TRACE_USAGE_MISSING"); }
    else if (u) span.setAttributes({
      [sc.GEN_AI_USAGE_INPUT_TOKENS]: u.input_tokens, [sc.GEN_AI_USAGE_OUTPUT_TOKENS]: u.output_tokens,
      [sc.SDDA_USAGE_CACHE_READ_TOKENS]: u.input_token_details?.cache_read ?? 0,
      [sc.SDDA_USAGE_CACHE_WRITE_TOKENS]: u.input_token_details?.cache_creation ?? 0,
      [sc.GEN_AI_RESPONSE_MODEL]: responseModelOf(msg), [sc.SDDA_COST_USD]: costUsd(msg, u) });
    span.end(); this.open.delete(runId);
  }

  override handleLLMError(err: Error, runId: string) { /* statut ERROR + error.type, puis end() */ }
}
```

Trois décisions, vérifiées contre les types publiés de `@langchain/core`
1.2.12 :

1. **`awaitHandlers = true`** : la propriété existe sur `BaseCallbackHandler`,
   et sa valeur par défaut dépend de `LANGCHAIN_CALLBACKS_BACKGROUND` (vrai
   seulement si la variable vaut `"false"`). En arrière-plan, le span `chat`
   peut être exporté **après** `run_finished`, donc hors de la trace que le
   runner lit à la sortie du processus.
2. **Parent explicite** : le handler est construit **par tour d'agent** avec le
   contexte de `agentTurn`, et passé dans `callbacks` de l'invocation. On ne
   compte pas sur la propagation de contexte à travers le gestionnaire de
   callbacks de LangChain.js — **non vérifiée** ; le parent explicite rend la
   question sans objet.
3. **Tokens depuis `usage_metadata`** (`input_tokens`, `output_tokens`,
   `input_token_details.cache_read` / `cache_creation` dans le type
   `UsageMetadata`), jamais depuis un coût déclaré par le fournisseur. En
   streaming, `handleLLMEnd` n'est appelé qu'à la fin du flux : le span n'est
   pas fermé trop tôt (`otel-genai.md` §7.2). L'extraction du nom de modèle
   demandé (`requestModelOf`) dépend du fournisseur actif ; elle est testée en
   L1 sur les `Serialized` réels de `@langchain/anthropic` — la forme exacte de
   ces paramètres est **non vérifiée** ici.

Les outils ne passent **pas** par ce callback (`handleToolStart`) : ils sont
tracés par `toolCall` dans leur wrapper, qui connaît le contrat (classe d'effet
de bord, `trust`, champs `@pii`) que le callback ignore.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/app/tracing/               # zone dev-backend (la coquille)
├── semconv.ts           # TOUTES les clés gen_ai.* et sdda.* (littéraux épinglés) + SEMCONV_VERSION
├── setup.ts             # configureTracing(settings) : provider, processeurs (§3.4), shutdown()
├── run-context.ts       # clé de contexte run_id ; RunIdSpanProcessor
├── spans.ts             # runRoot, agentTurn, toolCall, retrieval, dataQuery, guardrail (§3.5)
├── llm-callback.ts      # SddaLlmSpans (§3.6)
├── redaction.ts         # RedactingSpanExporter + règles (otel-genai.md §5.4)
├── jsonl-exporter.ts    # JsonlSpanExporter (§3.3)
├── trace-event.ts       # TraceEventSchema (Zod) — forme de ligne du §3.2
├── pricing.ts           # coût déclaré (bornes budgétaires) depuis la table de tarifs du contexte projet
└── tests/
    ├── redaction.test.ts        # L1 : e-mail/IBAN/téléphone ; @pii ; valeur d'un secret connu ; hash stable
    ├── jsonl-schema.test.ts     # L1 : chaque ligne valide TraceEvent ; racine SANS parent_span_id ; enfants AVEC
    ├── span-shape.test.ts       # L1 : InMemorySpanExporter — tour mocké → invoke_agent > chat + execute_tool, attributs obligatoires
    └── framework-reader.test.ts # L1 : un arbre écrit par l'exporteur est relu par `python .sdda/sdda.py trajectory-report` sans problème (network : exige python)

workspace/.sys/traces/runs/{run-id}.jsonl          # écrit à chaque run ; lu par les scripts du framework
```

Le chemin du répertoire de traces vient de la configuration (`settings.traceDir`,
défaut `../../.sys/traces/runs` relatif à la racine de l'application) : les
runners du framework le fixent explicitement quand ils lancent la CLI.

---

## 5. Conventions imposées

Les règles 1 à 15 de `observability/otel-genai.md` §5 s'appliquent telles
quelles (span racine par run, `chat` avec tokens, `execute_tool` y compris en
échec, `sdda.retrieve` avec identifiants et scores, événement de borne,
`TraceSampleRate: 1.0` en eval, double export, JSONL immédiat, `shutdown()`
explicite, aucun littéral d'attribut hors `semconv.ts`, redaction avant
export, contenu des messages seulement si `TracePIIPolicy: raw`, SQL
paramétré, scan G7 sur les JSONL). En Node :

1. **`await shutdown()` avant de rendre la main** : la CLI pose
   `process.exitCode`, appelle `provider.shutdown()` (qui vide le lot OTLP),
   puis retourne — jamais `process.exit()` avant.
2. **Aucun `setAttribute` d'un objet** : OTel JS n'accepte que des primitives et
   des tableaux homogènes ; `sdda.tool.args`, `sdda.retrieval.weights`,
   `…provenance` sont sérialisés en JSON (chaîne) par le helper.
3. **`sdda.run.id` sur chaque span** (RunIdSpanProcessor) : c'est lui qui route
   le span vers son fichier, et il est lu par la console.
4. **Contexte perdu = span orphelin** : tout `Promise.all` qui lance des
   sous-tâches le fait **dans** le callback d'un helper ; pas de travail
   détaché (`setImmediate`, `queueMicrotask` hors contexte, `EventEmitter`
   sans `bind`).
5. **Un seul `NodeTracerProvider` par processus**, créé par `configureTracing`
   dans la composition ; aucune couche n'en crée un autre.

---

## 6. Commande de smoke

Déterministe, 0 token, sans Collector :

```bash
cd workspace/src/{AppName}
npx --no-install vitest run app/tracing --tags-filter="!network"
pnpm build
node dist/app/tracing/selftest.js
#   1. configureTracing sans OTEL_EXPORTER_OTLP_ENDPOINT → avertissement stderr, pas d'erreur
#   2. émet un arbre mocké : sdda.run > invoke_agent > chat + execute_tool(args avec e-mail + secret factice) + sdda.retrieve
#   3. await shutdown()
#   4. relit {traceDir}/smoke-….jsonl : 5 lignes, toutes valides TraceEvent, racine sans parent_span_id,
#      aucun e-mail ni secret en clair ([SECRET_LEAK] sinon), tokens et sdda.cost.usd > 0 sur chat
#   5. supprime le fichier de smoke ; exit 0
python .sdda/sdda.py trajectory-report --help          # le lecteur du framework est disponible (lecture réelle : framework-reader.test.ts)
```

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

Pour la trace : **toute** exécution de `run`, `resume` et `retrieve` écrit
`{run_id}.jsonl` avant de rendre la main, et `run_finished.trace_path` le
désigne. En isolement, les outils servis par fixture émettent quand même leur
span `execute_tool` (avec `sdda.tool.id` du contrat) : la L5 d'un agent isolé
mesure sa trajectoire, et un outil mocké qui ne laisserait pas de span ferait
croire que l'agent ne l'a pas appelé. `retrieve` émet `sdda.run` >
`sdda.retrieve` (> `embeddings`), sans span `chat`.

Lancement : `node workspace/src/{AppName}/dist/cli.js` après `pnpm build`
(`lang/typescript.md` §4), ou le `bin` du package ; `--executor cli` côté
runner, ou `--executor cmd:<commande>`.

---

## 8. Pièges connus

Les pièges 1 à 13 de `observability/otel-genai.md` §7 valent tous. Propres à
Node :

1. **Noms de spans « maison »** (`sdda.agent.turn`, `sdda.llm.call`) : lus, puis
   ignorés par `tracing.py` — coût zéro, aucun outil, gates vertes. Seule la
   table du §3.1 fait foi.
2. **`parentSpanId` du SDK 1.x** : en 2.x, c'est `parentSpanContext?.spanId`.
   Un exporteur recopié d'un exemple ancien écrit `undefined` partout, et tous
   les spans deviennent des racines.
3. **Callbacks en arrière-plan** (§3.6) : le span `chat` arrive après la sortie
   du processus, donc jamais.
4. **`WriteStream` pour le JSONL** : bufferisé ; les dernières lignes sont
   perdues à la sortie.
5. **`process.exit()`** avant `await provider.shutdown()` : le lot OTLP et,
   selon l'implémentation, les dernières lignes JSONL sont perdus.
6. **Deux runs dans un processus** (surface HTTP) avec un exporteur qui
   écrirait dans « le » fichier du run courant : les spans se mélangent. Le
   routage par `sdda.run.id` (§3.3) règle ce cas.
7. **Horodatages locaux** : `new Date(...).toString()` n'est pas ISO ;
   `toISOString()` toujours, UTC avec `Z` — `tracing.py` refuse de soustraire
   un horodatage sans fuseau à un horodatage UTC.
