# Stack: otel-genai-dotnet (observability)

Stack ID: observability-otel-genai-dotnet
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: csharp
Scope: observabilité d'une application **.NET** via **OpenTelemetry** et les **conventions sémantiques GenAI** — spans `chat` / `embeddings` / `execute_tool` émis par `Microsoft.Extensions.AI` (`UseOpenTelemetry()`), spans `sdda.run` / `invoke_agent` / `sdda.retrieve` / `sdda.data.query` émis par des helpers maison, attributs `sdda.*`, redaction avant export, export OTLP **et** écriture **obligatoire** du fichier JSONL par run (`workspace/.sys/traces/runs/{run-id}.jsonl`) au format exact de `observability/otel-genai.md` — c'est un contrat relu par les scripts du framework, pas une option. Suppose `lang/csharp.md`. Catalogue : `otel-genai-dotnet.libs.json`.

---

## 1. Rôle et périmètre

Le pendant .NET de `observability/otel-genai.md`, qui reste la référence du
**quoi** : la table des spans (§3.1), des attributs, des métriques et des règles
de redaction ne change pas avec le langage. Ce qui change, c'est **qui émet
quoi** — et une contrainte qui n'existe pas en Python, parce qu'en Python le
framework et l'application partagent la même bibliothèque de traces :

> **Le fichier JSONL est un contrat inter-langages.** `cost-report`,
> `trajectory-report`, `audit-tool-scope`, les graders `trajectory` / `cost` /
> `latency` de `eval-runner` et le scan G7 lisent
> `workspace/.sys/traces/runs/{run-id}.jsonl` avec `sdda_lib/tracing.py`. Ils ne
> savent pas, et ne doivent pas savoir, dans quel langage l'application est
> écrite. Une application .NET dont le JSONL diverge d'un champ n'a pas de L5,
> pas de coût mesuré, pas de G6 — et l'échec est **silencieux** : le lecteur
> ignore une ligne illisible, il ne plante pas.

C'est pourquoi l'exporteur JSONL est **écrit dans l'application** (§3.4), à
partir d'un format fixé ici champ par champ, et testé en L1 contre un fichier de
référence — et non délégué à un exporteur OTel générique, dont aucun n'écrit ce
format.

Périmètre : répartition des spans entre MEAI et les helpers, attributs `sdda.*`,
pipeline `IChatClient`, redaction, double export, format JSONL, tests.
**Hors périmètre** : le backend, les métriques d'infrastructure.

### 1.1 Honnêteté sur la stabilité

Les conventions GenAI sont en statut *Development*. La doc de
`OpenTelemetryChatClient` (Microsoft.Extensions.AI 10.10.0) déclare implémenter
**semconv GenAI v1.41**, « expérimentale et sujette à changement ». Donc, comme
en Python : tous les noms d'attributs `sdda.*` et les noms `gen_ai.*` que le code
écrit lui-même vivent dans **un seul fichier** (`app/tracing/SemConv.cs`), et
chaque ligne JSONL porte `semconv_version`.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `observability-otel-genai-dotnet` |
| **SDK** | `OpenTelemetry` / `.Extensions.Hosting` / `.Exporter.OpenTelemetryProtocol` **1.19.1** |
| **Spans GenAI** | `Microsoft.Extensions.AI` 10.10.x — `ChatClientBuilder.UseOpenTelemetry(loggerFactory, sourceName, configure)`, `EmbeddingGeneratorBuilder.UseOpenTelemetry(…)` |
| **Source d'activités** | **une seule**, nommée `sdda` : `UseOpenTelemetry(sourceName: "sdda")` partout, `new ActivitySource("sdda")` pour les helpers, `AddSource("sdda")` au provider |
| **Export** | OTLP (`BatchActivityExportProcessor`) **+** `JsonlActivityExporter` maison (`SimpleActivityExportProcessor`) — les deux, toujours |
| **Paramètres STACK.md** | `TraceLevel: full`, `TraceSampleRate: 1.0`, `TracePIIPolicy: redact`, `CostTrackingEnabled: true` |
| **Contenu** | `OpenTelemetryChatClient.EnableSensitiveData = false` **explicite** (défaut `false`, sauf si `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` — la propriété explicite l'emporte sur la variable) ; `true` seulement si `TracePIIPolicy: raw` (ADR) |

Pourquoi une source unique et nommée : les sources par défaut de MEAI et de
Microsoft Agent Framework portent des noms préfixés `Experimental.` qui peuvent
changer d'une version à l'autre. Un `AddSource` qui ne correspond plus à rien ne
lève pas d'erreur : il ne collecte plus rien. Nommer la source explicitement
supprime cette dépendance.

---

## 3. Mapping des concepts SDD_Agents → spans .NET

### 3.1 Qui émet quel span

| Span (`otel-genai.md` §3.1) | Émis par | Complété par |
|---|---|---|
| `sdda.run {mission_id}` (racine) | helper `Tracing.Run(...)` — `serving/` au début de `run` / `resume` / `retrieve` | `sdda.run.id`, `sdda.mission.id`, `sdda.ir.hash`, `sdda.stack.hash`, `sdda.serving.surface` |
| `invoke_agent {agent_name}` | helper `Tracing.AgentTurn(...)` — **dans `BoundedAgentRunner`** | `gen_ai.operation.name = "invoke_agent"` **obligatoire**, `gen_ai.agent.id`, `gen_ai.agent.name`, `gen_ai.conversation.id`, `sdda.prompt.hash`, `sdda.bounds.*`, `sdda.agent.iteration`, `sdda.cap.ids` |
| `chat {model}` | **MEAI** `OpenTelemetryChatClient` | `SddaUsageEnricher` (§3.2) : `sdda.usage.cache_read_tokens`, `sdda.cost.usd`, `sdda.pricing.version`, `sdda.model.tier` |
| `execute_tool {tool}` | **MEAI** `FunctionInvokingChatClient`, sous le span `invoke_agent` courant | `ToolRegistry` au début de l'invocation : `sdda.tool.id`, `sdda.tool.side_effect_class`, `sdda.tool.trust`, `sdda.tool.args` (**redigés**), `sdda.tool.transport`, `sdda.tool.server` |
| `embeddings {model}` | **MEAI** `OpenTelemetryEmbeddingGenerator` | `sdda.cost.usd`, `sdda.embedding.dims` |
| `sdda.retrieve {index_id}` | helper `Tracing.Retrieval(...)` | attributs `sdda.retrieval.*` de `rag/hybrid-dotnet.md` §3 |
| `sdda.data.query {view}` | helper `Tracing.DataQuery(...)` | `db.query.text` **paramétré** |
| `sdda.guardrail {id}` | helper `Tracing.Guardrail(...)` | |
| événement `sdda.bound_exceeded` | `AgentSpan.BoundExceeded(...)` sur le span agent | `sdda.bound.name`, `.limit`, `.observed`, `.policy` |

Ce partage a été **mesuré** sur une sonde (MEAI 10.10.0, OpenTelemetry 1.19.1) :
avec le pipeline `FunctionInvokingChatClient` → `OpenTelemetryChatClient` →
client du modèle, un tour avec un appel d'outil produit, sous le span
`invoke_agent` courant, `chat`, `execute_tool lookup`, `chat` — chacun avec
`gen_ai.operation.name` renseigné, `gen_ai.usage.input_tokens` /
`output_tokens` et `gen_ai.response.model` sur `chat`, `gen_ai.tool.name` /
`gen_ai.tool.call.id` sur `execute_tool`.

**`invoke_agent` est émis par nous, pas par le framework.** Microsoft Agent
Framework sait l'émettre (`AIAgentBuilder.UseOpenTelemetry(sourceName, …)`,
vérifié par compilation), mais il n'y mettrait ni les bornes, ni le hash de
prompt, ni les CAP — et un second `invoke_agent` émis par le runner ferait
compter **deux** agents par tour à `trajectory-report`. Un seul émetteur : le
`BoundedAgentRunner`, qui connaît les bornes. Si un `ChatClientAgent` ajoute
lui-même une couche d'invocation de fonctions (point **non vérifié** pour
MAF 1.22.0), le test L1 `SpanShapeTests` le verrait : exactement un
`execute_tool` par appel d'outil, exactement un `chat` par appel de modèle.

### 3.2 Le pipeline `IChatClient` — l'ordre compte

```csharp
// app/Models.cs — Resolve(tier) : seul contact avec le provider
IChatClient chat = new AnthropicClient { ApiKey = options.LlmApiKey }      // SDK officiel `Anthropic`, IChatClient natif
    .AsIChatClient(options.RuntimeTierMap[tier], options.MaxOutputTokens)   // (modèle par défaut, max tokens par défaut)
    .AsBuilder()
    .UseFunctionInvocation()                                                 // 1. le plus EXTERNE : exécute les outils → spans execute_tool
    .UseOpenTelemetry(sourceName: "sdda", configure: c => c.EnableSensitiveData = false)   // 2. span chat par appel au modèle
    .Use(inner => new SddaUsageEnricher(inner, pricing, tier))              // 3. le plus INTERNE : Activity.Current == span chat
    .Build();
```

Pourquoi cet ordre : `ChatClientBuilder` enveloppe dans l'ordre d'appel, le
premier ajouté étant le plus externe. `UseOpenTelemetry` **sous**
`UseFunctionInvocation` fait un span `chat` par aller-retour au modèle, fermé
avant l'exécution des outils — c'est ce qui place `execute_tool` sous
`invoke_agent` et non sous `chat` (la doc de Microsoft Agent Framework dit la
même chose de son propre pipeline). L'enrichisseur est **sous** OTel : au moment
où la réponse revient, `Activity.Current` est le span `chat`, et il y écrit
`sdda.cost.usd` et les tokens de cache depuis `ChatResponse.Usage`
(`CachedInputTokenCount`). Les tokens d'écriture de cache ne sont pas un champ
de `UsageDetails` : leur disponibilité via `AdditionalCounts` pour le SDK
`Anthropic` n'a **pas** été vérifiée.

Ce pipeline — `AnthropicClient { ApiKey }`, `AsIChatClient`, les trois étages,
un `DelegatingChatClient` enrichisseur — a été compilé contre les pins du
catalogue (sonde du 2026-09-26). La clé passe par la propriété `ApiKey`, lue dans
`IOptions`, jamais par un défaut du SDK : la source de clé du constructeur sans
argument n'a pas été vérifiée, et une clé lue hors de `IConfiguration` échappe à
la redaction des secrets connus (§3.3).

`sdda.cost.usd` est **déclaré**, le framework le **recalcule** depuis
`gen_ai.usage.*` et `gen_ai.response.model` (ARCHITECTURE §8) ; un écart est
signalé. Un span `chat` sans `gen_ai.request.model` ni `gen_ai.response.model`
rend le coût « non recalculable » : `AsIChatClient(modelId, …)` fixe le modèle
par défaut, et `SpanShapeTests` vérifie qu'il apparaît.

### 3.3 Redaction — un processeur avant tout exporteur

`RedactionProcessor : BaseProcessor<Activity>`, enregistré **avant** les deux
exporteurs, réécrit les tags dans `OnEnd` :

| Tag | Traitement | Pourquoi |
|---|---|---|
| `gen_ai.tool.description` | remplacé par `sdda.tool.description.hash` (sha256), supprimé | **MEAI l'émet en clair** (mesuré) ; `otel-genai.md` §3.1 exige le hash — la description est du prompt, et le texte d'un serveur MCP `untrusted` est hostile |
| `gen_ai.tool.definitions` | remplacé par un hash | émis en clair par MEAI sur chaque `chat` (mesuré) : volume à chaque appel, et descriptions d'outils recopiées |
| `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions` | absents tant que `EnableSensitiveData = false` ; supprimés par filet sinon, sauf `TracePIIPolicy: raw` | P8, et le prompt système est déjà connu par `sdda.prompt.hash` |
| `sdda.tool.args` | redigé **avant** d'entrer dans le span (champs `@pii`, regex) | un argument ne doit jamais atteindre un exporteur en clair |
| toute valeur texte | regex e-mail / téléphone / IBAN / carte (Luhn) / NIR, puis valeurs des secrets connus (`IOptions`) → `[REDACTED:{type}]` | mêmes règles que `otel-genai.md` §5.4 |
| `db.query.text` / `db.statement` | vérifié paramétré (aucun littéral > 32 caractères) | `[TRACE_SQL_LITERAL]` |

### 3.4 Le fichier JSONL — le contrat, champ par champ

Une ligne = un span, écrite **à la fin du span**, en UTF-8 sans BOM, `\n` :

```json
{"semconv_version":"1.41","run_id":"…","trace_id":"<32 hex>","span_id":"<16 hex>","parent_span_id":"<16 hex>",
 "name":"execute_tool billing_specialist_unpaid_invoices","start":"2026-09-20T14:12:03.120Z","end":"2026-09-20T14:12:03.432Z",
 "duration_ms":312,"status":"OK",
 "attributes":{"gen_ai.operation.name":"execute_tool","gen_ai.tool.name":"…","sdda.tool.side_effect_class":"read-only","sdda.tool.args":"{\"customer_id\":\"[REDACTED:uuid]\"}"},
 "events":[{"name":"sdda.bound_exceeded","time":"…","attributes":{"sdda.bound.name":"MaxToolCalls"}}]}
```

| Champ | Règle | Lu par |
|---|---|---|
| `run_id` | l'ULID du run = nom du fichier ; **identique sur toutes les lignes** | tous (`SPAN_FIELDS`) |
| `trace_id` | `Activity.TraceId.ToHexString()` | tous |
| `span_id` | `Activity.SpanId.ToHexString()` | tous |
| `parent_span_id` | `Activity.ParentSpanId.ToHexString()` — **absent** sur la racine | arbre : agent responsable d'un appel d'outil, profondeur de délégation |
| `name` | `Activity.DisplayName` ; la racine commence par `sdda.run` | reconnaissance du span racine |
| `start` / `end` | ISO 8601 UTC avec `Z`, `CultureInfo.InvariantCulture` | latence |
| `duration_ms` | entier | latence, p95 |
| `status` | `"ERROR"` si `ActivityStatusCode.Error`, sinon `"OK"` | statut du run |
| `attributes` | objet **plat** ; tableaux en tableaux JSON, nombres en nombres | rôles (`gen_ai.operation.name`), coût, trajectoire |
| `events` | `[{name, time, attributes}]` | bornes atteintes |
| `semconv_version` | la version de semconv implémentée par MEAI (`1.41` pour 10.10.x) | console (table de renommage) |

```csharp
// app/tracing/JsonlActivityExporter.cs — extrait (le fichier complet est compilé et testé en L1)
public sealed class JsonlActivityExporter(string tracesDir, string runId, string semconvVersion) : BaseExporter<Activity>
{
    private static readonly Lock Gate = new();
    private readonly string _path = Path.Combine(tracesDir, $"{runId}.jsonl");

    public override ExportResult Export(in Batch<Activity> batch)
    {
        var sb = new StringBuilder();
        foreach (var activity in batch) sb.Append(ToLine(activity)).Append('\n');   // ToLine : Utf8JsonWriter, champs du tableau ci-dessus
        lock (Gate)                                                                  // une ligne ENTIÈRE sous verrou (ARCHITECTURE §8)
        {
            using var stream = new FileStream(_path, FileMode.Append, FileAccess.Write, FileShare.Read);
            stream.Write(new UTF8Encoding(false).GetBytes(sb.ToString()));
            stream.Flush(flushToDisk: true);
        }
        return ExportResult.Success;
    }
}
```

Enregistré par `AddProcessor(new SimpleActivityExportProcessor(new JsonlActivityExporter(…)))`
— **Simple**, pas Batch : un run qui plante laisse sa trace jusqu'au dernier span
fermé. Ce format a été vérifié de bout en bout sur une sonde : un arbre
`sdda.run` > `chat` écrit par cet exporteur est relu par `sdda_lib/tracing.py`
(`read_spans`, `span_role`, `summarize`) avec les rôles `run` et `llm`, et un
`chat` sans modèle y est bien signalé « coût non recalculable ».

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── app/
│   └── tracing/
│       ├── SemConv.cs                 # TOUTES les constantes gen_ai.* écrites par le code + sdda.* ; SemconvVersion
│       ├── TracingSetup.cs            # AddOpenTelemetry().WithTracing : AddSource("sdda"), [AddNpgsql()], RedactionProcessor, Simple(Jsonl) + Batch(OTLP)
│       ├── Tracing.cs                 # helpers imposés : Run, AgentTurn, Retrieval, DataQuery, Guardrail — SEULS points d'entrée vers ActivitySource
│       ├── SddaUsageEnricher.cs       # DelegatingChatClient : sdda.cost.usd, cache tokens, tier sur le span chat
│       ├── RedactionProcessor.cs      # §3.3
│       ├── JsonlActivityExporter.cs   # §3.4
│       └── Pricing.cs                 # coût depuis la table versionnée (.sdda/providers/*.yaml recopiée au build) — sdda.pricing.version
└── tests/
    └── tracing/
        ├── JsonlTraceContractTests.cs # L1 : chaque ligne a run_id/trace_id/span_id/name ; parent absent sur la racine ; types JSON ; UTF-8 sans BOM
        ├── SpanShapeTests.cs          # L1 (InMemory exporter, IChatClient doublé) : invoke_agent > chat + execute_tool > chat ; un seul de chaque par appel
        ├── RedactionTests.cs          # L1 : e-mail/IBAN/secret redigés ; gen_ai.tool.description absent, hash présent
        └── CostTests.cs               # L1 : usage → sdda.cost.usd via table épinglée ; cache compté à part

workspace/.sys/traces/runs/{run-id}.jsonl   # écrit à chaque run ; relu par les scripts du framework
```

`tracing/` est sous `app/` : ce n'est pas une couche de la matrice d'ownership,
c'est de la composition (`dev-backend`). Les agents, outils et retrievers
n'appellent que `Tracing.*`.

---

## 5. Conventions imposées

Les quinze conventions de `otel-genai.md` §5 valent (un span racine par run,
`chat` avec tokens et coût, `execute_tool` y compris en échec,
`sdda.retrieve` avec ids et scores, événement de borne, `TraceSampleRate: 1.0`
en eval, double export, Simple pour le JSONL, arrêt explicite, aucun littéral
d'attribut hors du module de constantes, redaction avant export, contenu opt-in,
SQL paramétré, scan G7 sur les JSONL). Propres à .NET :

1. **Une seule `ActivitySource`, `sdda`**, passée à tous les `UseOpenTelemetry`.
2. **`EnableSensitiveData = false` écrit explicitement**, pour qu'une variable
   d'environnement posée sur une machine ne change pas le contenu des traces.
3. **Ordre du pipeline** : `UseFunctionInvocation` → `UseOpenTelemetry` →
   enrichisseur → client du modèle (§3.2). Tout autre ordre est un écart testé.
4. **`gen_ai.operation.name` sur tout span maison qui a un rôle**
   (`invoke_agent`) : `sdda_lib/tracing.py` classe les spans par cet attribut,
   et un `invoke_agent` sans lui n'est pas un agent pour le framework.
5. **Aucun `Console` exporter** (stdout est le canal NDJSON) ; aucun log sur
   stdout en `--json`.
6. **Arrêt** : le `TracerProvider` est disposé avant la sortie du processus
   (fin de l'hôte), **puis** `Environment.ExitCode` est posé — jamais
   `Environment.Exit()` au milieu (`serving/cli-dotnet.md` §7.2).
7. **`Activity.Current` n'est jamais écrit à la main** hors de `Tracing` ; le
   contexte suit `async`/`await` par `AsyncLocal`, mais un `Task.Run` sans
   capture explicite du parent produit un span orphelin.

---

## 6. Commande de smoke

Déterministe, 0 token, sans Collector :

```bash
cd workspace/src/{AppName}
dotnet test --filter "FullyQualifiedName~Tracing"
dotnet run --project . -- smoke tracing
#   1. TracingSetup sans OTEL_EXPORTER_OTLP_ENDPOINT → avertissement, pas d'erreur
#   2. émet un arbre doublé : sdda.run > invoke_agent > chat + execute_tool(args avec e-mail + secret factice) + sdda.retrieve
#   3. dispose du TracerProvider → flush
#   4. relit workspace/.sys/traces/runs/smoke-….jsonl : 5 lignes ; champs du contrat §3.4 ; aucun e-mail, aucun secret ([SECRET_LEAK] sinon)
#   5. gen_ai.usage.* et sdda.cost.usd > 0 sur chat ; gen_ai.tool.description absent
#   6. supprime le fichier de smoke ; exit 0
python .sdda/sdda.py cost-report --help    # le lecteur du framework est disponible pour relire une vraie trace
```

---

## 7. Pièges connus

Les pièges de `otel-genai.md` §7 valent (conventions qui bougent, usage en fin
de stream, coût hors semconv, tokens de cache, cardinalité des métriques,
documents dans les spans, flush à la sortie, contexte async, double
instrumentation, faux négatifs de regex, fichier JSONL sous Windows, sampling en
eval, SQL interpolé). Propres à .NET :

1. **`ParentSpanId` de la racine.** Il vaut `0000000000000000`, pas `null` :
   écrit tel quel, la racine a un parent fantôme et `load_run` ne la reconnaît
   plus comme racine. Le champ est **omis** quand `ParentSpanId == default`.
2. **`TagObjects` sérialisés par `ToString()`.** Un `string[]` devient
   `"System.String[]"` (vu sur la sonde en affichage brut) : `result_ids` perdu.
   L'exporteur écrit les tableaux en tableaux JSON.
3. **Culture courante dans les dates et les nombres.** `ToString()` d'un
   `DateTime` sous `fr-FR` donne `20/09/2026` ; `InvariantCulture` et format
   ISO explicites.
4. **`AddSource` avec le nom par défaut d'une bibliothèque.** Il change sans
   bruit d'une version à l'autre ; source explicite `sdda`.
5. **`gen_ai.tool.description` en clair** — émis par MEAI (mesuré), retiré par
   le processeur de redaction (§3.3).
6. **Deux `invoke_agent` par tour** si `AIAgentBuilder.UseOpenTelemetry` est
   ajouté en plus du helper : le nombre de hops double dans le rapport de
   trajectoire.
7. **`BatchActivityExportProcessor` pour le JSONL.** Plus rapide, et il perd les
   derniers spans d'un processus qui plante — ceux qui expliquent le plantage.
8. **Écriture concurrente.** Des evals parallèles écrivent des runs différents
   (un fichier par run), mais un même run peut fermer deux spans en même temps
   (outils parallèles) : une ligne entière sous verrou, jamais deux `Write`
   partiels entrelacés.

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
l'exécutable publié en livrable. Les runners l'invoquent par `--executor cli`
(commande dérivée du langage actif : `dotnet run --project workspace/src/{AppName} --`)
ou par `--executor cmd:<commande>`.

**Ce que cette fiche doit au contrat.**

- **Toute invocation écrit sa trace**, `run`, `resume` **et** `retrieve`, y
  compris en échec au démarrage (span racine avec `error.type`, `status:
  "ERROR"`), y compris en isolement `mocked` — une L4 sans trace n'a pas de L5.
- **Le fichier s'appelle `{run_id}.jsonl`**, sous `workspace/.sys/traces/runs/`
  (ou `--trace-out`), et `run_finished.trace_path` le cite : c'est par ce champ
  que le runner retrouve la trace d'un item.
- **`run_id` vient du runner quand il le fournit** (forme `{item}-{run_index}`,
  que `_run_traces.item_of` sait rattacher à un item du jeu), sinon un ULID.
- **En isolement**, les spans `execute_tool` et `sdda.retrieve` sont émis
  **comme en live** (avec `sdda.tool.transport = "fixture"`) : la trajectoire
  mesurée en L4/L5 doit être celle du système livré, seule la source des
  réponses change.
- **La trace se relit avec l'outillage du framework** — `python .sdda/sdda.py cost-report`
  et `trajectory-report` — sans aucun adaptateur .NET. C'est le critère
  d'acceptation de cette fiche.
