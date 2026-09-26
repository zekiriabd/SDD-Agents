# Stack: mcp-dotnet (tools)

Stack ID: tools-mcp-dotnet
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: csharp
Scope: intégration d'outils exposés par des serveurs **Model Context Protocol** côté **.NET**, avec le SDK C# officiel `ModelContextProtocol` — transports `stdio` / `http` (Streamable HTTP) / `sse` (legacy), allowlist d'outils, posture `trusted` / `untrusted` par serveur, mapping de chaque outil MCP vers un **tool-contract** puis vers une `AIFunction` (Microsoft.Extensions.AI), bornes (timeout, taille de réponse). Côté client uniquement. Suppose `lang/csharp.md` et `framework/ms-agent-framework.md`. Pas de `.libs.json` : `ModelContextProtocol` est déjà au catalogue du framework (`ms-agent-framework.libs.json`, capability `mcp-tools`).

---

## 1. Rôle et périmètre

Le pendant .NET de `tools/mcp.md`, dont la règle est inchangée : **un serveur
MCP propose, le contrat dispose.** Le serveur annonce N outils ; l'application
n'en câble que ceux de l'allowlist, avec la description **du contrat**, le
schéma d'entrée **épinglé par hash**, la classe d'effet de bord **déclarée par
`architect-tools`**, et la sortie enveloppée si le serveur est `untrusted`.

Ce que le SDK C# change, et qu'il faut savoir avant d'écrire une ligne :

- **`McpClientTool` EST une `AIFunction`** (doc XML du paquet : « Provides an
  AIFunction that calls a tool via an McpClient »). C'est commode — et c'est le
  piège principal : la liste rendue par `ListToolsAsync` se passe telle quelle à
  un `IChatClient`, avec la description **du serveur**, sans allowlist, sans
  enveloppe. Ce raccourci est interdit (§5.1).
- **Deux défauts du SDK vont contre la fiche** et doivent être renversés
  explicitement : `StdioClientTransportOptions.InheritEnvironmentVariables`
  vaut `true` (le sous-processus hérite de **toutes** les variables du parent,
  clés d'API comprises), et `HttpClientTransportOptions.TransportMode` vaut
  `AutoDetect` (Streamable HTTP, puis **repli silencieux sur SSE legacy**).

Périmètre : transports, cycle de vie, allowlist et fail-fast, mapping
MCP → contrat → `AIFunction`, enveloppe `untrusted`, erreurs, bornes, tests.
**Hors périmètre** : resources, prompts, sampling, elicitation — refusés
(§5.8) ; exposer l'application en serveur MCP (`serving/mcp-server.md`,
`ModelContextProtocol.AspNetCore`).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `tools-mcp-dotnet` |
| **SDK** | `ModelContextProtocol` **2.2.0** (dépend de `ModelContextProtocol.Core` 2.2.0, même version) — dépôt `modelcontextprotocol/csharp-sdk` ; pin dans `ms-agent-framework.libs.json` |
| **Protocole** | révision citée par la doc du SDK 2.2.0 : **2025-11-25** (Streamable HTTP) ; `sse` = transport HTTP+SSE de 2024-11-05, déprécié |
| **Types pivots** | `McpClient.CreateAsync(IClientTransport, McpClientOptions, ILoggerFactory, CancellationToken)`, `StdioClientTransport`, `HttpClientTransport`, `HttpTransportMode` (`StreamableHttp` \| `Sse` \| `AutoDetect`), `McpClientTool` (`ProtocolTool`, `JsonSchema`, `WithName`, `WithDescription`), `CallToolAsync` → `CallToolResult` (`IsError`, `Content`, `StructuredContent`) |
| **Déclaration** | `STACK.md ## Active Tools & Integrations → MCPServers[]` : `name`, `transport`, `command` \| `url`, `auth_env`, `trust`, `tools_allowlist` — identique à `tools/mcp.md` |
| **Contrat par outil** | `workspace/pipeline/contracts/tools/{n}-{server}-{tool}.tool.md` — un par outil allowlisté |

### 2.1 Transports

| `transport` (STACK.md) | Idiome .NET | Réglage **obligatoire** |
|---|---|---|
| `stdio` | `new StdioClientTransport(new StdioClientTransportOptions { Name, Command, Arguments, … })` | `InheritEnvironmentVariables = false` + `EnvironmentVariables` = exactement ce que le contrat §6 nomme |
| `http` | `new HttpClientTransport(new HttpClientTransportOptions { Endpoint, AdditionalHeaders, … }, httpClient)` | `TransportMode = HttpTransportMode.StreamableHttp` — jamais `AutoDetect` |
| `sse` | idem, `TransportMode = HttpTransportMode.Sse` | ADR de sortie exigé (déprécié) |

Pourquoi `AutoDetect` est refusé : il rend le transport effectif dépendant de
la réponse du serveur. Un serveur mal configuré (ou compromis) qui refuse
Streamable HTTP fait basculer le client en SSE legacy sans que `STACK.md` ait
changé — perte de reprise de session, comportement de timeout différent, et la
trace annonce un transport qui n'est pas celui qui tourne.

---

## 3. Mapping des concepts SDD_Agents → idiomes MCP .NET

La table de `tools/mcp.md` §3.1 (nom, description, `inputSchema` épinglé,
`outputSchema`, annotations = indices, `trust` hérité du serveur, `auth_env`,
`timeout_s` / `rate_limit_rpm` / `retry_policy`, `isError` mappé, erreurs
JSON-RPC standard, `content[]` limité au texte et au `structuredContent`)
s'applique **à l'identique**. Correspondances .NET :

| Côté MCP | Idiome .NET | Règle |
|---|---|---|
| `tools/list` | `await client.ListToolsAsync(cancellationToken: ct)` → `IList<McpClientTool>` | on itère l'**allowlist**, jamais la liste rendue |
| `inputSchema` | `tool.ProtocolTool.InputSchema` (JSON) | `sha256` de la forme canonique == contrat, sinon `[TOOL_SCHEMA_DRIFT]` |
| `description` du serveur | `tool.Description` | **jamais** transmise au modèle ; remplacée par `WithDescription(contract.Description)` ou, pour `untrusted`, par une `AIFunction` maison (§3.2) |
| nom interne | `{server}__{tool}` ; nom vu par le modèle = `contract.Name` | deux serveurs exposant `search` ne se marchent pas dessus |
| `tools/call` | `client.CallToolAsync(name, args, cancellationToken: ct)` → `CallToolResult` | sous `CancelAfter(contract.TimeoutSeconds)` |
| `isError: true` | `result.IsError == true` | mappé vers un code déclaré ; non mappable → `TOOL_ERROR_UNDECLARED` |
| `structuredContent` | `result.StructuredContent` (JSON) | validé contre `output_schema` du contrat |
| sampling / elicitation | `McpClientOptions.Handlers.SamplingHandler` / `ElicitationHandler` | **laissés nuls** : la capacité n'est pas annoncée au serveur |
| `notifications/tools/list_changed` | `Handlers.NotificationHandlers` | journalisée, **ignorée** : aucun outil n'apparaît à chaud |

### 3.1 Allowlist : découverte ≠ câblage

```csharp
// tools/mcp/McpToolFactory.cs — namespace {AppName}.Tools.Mcp
public async Task<IReadOnlyList<AIFunction>> BuildAsync(McpServerConfig server, McpClient client, ContractIndex contracts, CancellationToken ct)
{
    var listed = (await client.ListToolsAsync(cancellationToken: ct))
                 .ToDictionary(t => t.ProtocolTool.Name, StringComparer.Ordinal);

    var missing = server.ToolsAllowlist.Where(n => !listed.ContainsKey(n)).ToList();
    if (missing.Count > 0)
        throw new McpStartupException($"[TOOL_MCP_MISSING] {server.Name}: {string.Join(", ", missing)}");   // outil promis absent = config fausse

    var bound = new List<AIFunction>();
    foreach (var name in server.ToolsAllowlist)                       // l'ALLOWLIST, jamais `listed`
    {
        var remote = listed[name];
        var contract = contracts.Require($"{server.Name}-{name}");    // [TOOL_CONTRACT_MISSING] sinon
        if (SchemaHash.Of(remote.ProtocolTool.InputSchema) != contract.InputSchemaSha256)
            throw new McpStartupException($"[TOOL_SCHEMA_DRIFT] {server.Name}/{name}");

        bound.Add(server.Trust == Trust.Untrusted
            ? new UntrustedMcpFunction(remote, contract, server.Name)          // §3.2 : sortie enveloppée
            : remote.WithName(contract.Name).WithDescription(contract.Description));
    }
    return bound;
}
```

`WithName` / `WithDescription` changent ce que voit le modèle **sans** changer
l'appel sous-jacent (doc du SDK). C'est suffisant pour un serveur `trusted`,
dont la sortie n'a pas besoin d'enveloppe ; pour un serveur `untrusted`, la
sortie doit être transformée, ce qu'un simple renommage ne fait pas.

### 3.2 Enveloppe `untrusted`

```csharp
// tools/mcp/UntrustedMcpFunction.cs
public sealed class UntrustedMcpFunction(McpClientTool inner, ToolContract contract, string server) : DelegatingAIFunction(inner)
{
    public override string Name => contract.Name;
    public override string Description => contract.Description;     // celle du contrat, jamais celle du serveur

    protected override async ValueTask<object?> InvokeCoreAsync(AIFunctionArguments arguments, CancellationToken ct)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(contract.TimeoutSeconds));

        var result = await inner.CallAsync(arguments, cancellationToken: timeout.Token);   // CallToolResult
        if (result.IsError == true)
            return ToolResult.Error(ErrorMap.Map(result, contract));                        // code déclaré, sinon TOOL_ERROR_UNDECLARED

        var text = ContentReader.TextOnly(result, contract.MaxResponseBytes);              // tronque ; image/audio → [TOOL_CONTENT_UNSUPPORTED]
        if (contract.OutputSchema is not null)
            text = OutputValidator.ValidateOrFail(result.StructuredContent, text, contract.OutputSchema);  // [TOOL_CONTRACT_FAILED]

        return ToolResult.Ok(Trust.Wrap(text, source: $"mcp:{server}:{contract.Name}"));   // Untrusted, jamais dans le système
    }
}
```

`DelegatingAIFunction` (Microsoft.Extensions.AI) conserve le schéma JSON de
l'outil sous-jacent — celui qui a été épinglé par hash — et ne remplace que le
nom, la description et l'exécution. La forme `DelegatingAIFunction(inner)` +
`InvokeCoreAsync` + `inner.CallAsync(arguments, cancellationToken: …)` a été
compilée contre `ModelContextProtocol` 2.2.0 et `Microsoft.Extensions.AI` 10.10.x
(sonde du 2026-09-26) ; les types `ToolResult`, `ErrorMap`, `ContentReader` et
`OutputValidator` sont ceux de l'application générée.

### 3.3 Cycle de vie et bornes

| Moment | Action |
|---|---|
| Démarrage | pour chaque serveur : transport, `McpClient.CreateAsync`, `ListToolsAsync`, allowlist ∩ hashes (§3.1). Échec = l'application ne démarre pas (code `8`). |
| Par run | réutilisation du client (`http`) ou du sous-processus (`stdio`) ; un sous-processus mort → `[TOOL_MCP_DISCONNECTED]` tracé, redémarré |
| Par appel | `CancelAfter(timeout_s)` sur un jeton lié à celui de la borne d'agent ; span `execute_tool` avec `sdda.tool.transport`, `sdda.tool.trust`, `sdda.tool.server` |
| Taille | `MaxResponseBytes` borne la lecture **avant** parsing ; `MaxResponseChars` borne l'enveloppe ; `truncated` informe le modèle |
| Arrêt | `await client.DisposeAsync()` ; `StdioClientTransportOptions.ShutdownTimeout` borne l'arrêt du sous-processus |

Les appels MCP passent par `FunctionInvokingChatClient` comme tout outil : les
compteurs `MaxToolCalls` du `BoundedAgentRunner` (`framework/ms-agent-framework.md`
§3.3) les comptent, et le span `execute_tool` est émis par l'instrumentation de
Microsoft.Extensions.AI (`observability/otel-genai-dotnet.md`).

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── tools/
│   └── mcp/
│       ├── McpServerConfig.cs          # record lu depuis IOptions ← STACK.md MCPServers[] ; auth = nom de variable, valeur via IConfiguration
│       ├── McpTransports.cs            # stdio (InheritEnvironmentVariables = false) | http (StreamableHttp) | sse (Sse)
│       ├── McpToolFactory.cs           # §3.1 — allowlist, hash, nommage {server}__{tool}
│       ├── UntrustedMcpFunction.cs     # §3.2
│       ├── ContentReader.cs  ErrorMap.cs  OutputValidator.cs  SchemaHash.cs
│       ├── FixtureMcpFunction.cs       # isolement L4 : sert SDDA_EVAL_FIXTURES/tools/{outil}.jsonl (§8)
│       └── McpSmoke.cs                 # cf. §6
└── tests/
    └── tools/
        └── mcp/
            ├── TestMcpServer.cs                 # serveur MCP de test EN PROCESSUS (echo, fail_declared, fail_unknown, slow, huge, poison)
            ├── McpRegistryTests.cs              # L2 : allowlist manquante → fail-fast ; drift → fail-fast ; outil non allowlisté invisible
            ├── UntrustedWrapTests.cs            # L2 : enveloppe ; troncature ; image refusée ; structuredContent validé
            ├── McpErrorsTests.cs                # L2 : [Theory] par cas déclaré — isError mappé, erreur non déclarée, timeout, auth KO
            ├── McpTransportTests.cs             # L1 : stdio n'hérite d'aucune variable ; http n'est jamais AutoDetect
            └── {Server}NetworkTests.cs          # [Trait("Category","network")] : initialize + list_tools + hashes sur le VRAI serveur
```

Convention de nommage G3 (`lang/csharp.md` §5.4) : chaque cas de contrat est une
ligne `[InlineData("…")]` dont l'identifiant apparaît dans le nom affiché du
test ; les tests live sont dans une classe `…NetworkTests`.

---

## 5. Conventions imposées

Les onze conventions de `tools/mcp.md` §5 valent telles quelles. Leur forme
.NET, et ce qui s'ajoute :

1. **Jamais la liste brute de `ListToolsAsync` dans un `ChatOptions.Tools`**
   ni dans les outils d'un `AIAgent`. Seul `McpToolFactory.BuildAsync` produit
   des `AIFunction` MCP. Recherche L0 : `ListToolsAsync(` hors
   `tools/mcp/McpToolFactory.cs` → `[TOOL_SCOPE_EXCESS]`.
2. **La description vue par le modèle est celle du contrat** (`WithDescription`
   ou `UntrustedMcpFunction.Description`).
3. **`inputSchema` épinglé** : hash de la forme JSON canonique (clés triées,
   sans espaces), calculé par la **même** fonction que celle du contrat.
4. **`side_effect_class` déclarée par `architect-tools`** ; les
   `ProtocolTool.Annotations` ne sont que des indices.
5. **`stdio` : `InheritEnvironmentVariables = false`**, et `EnvironmentVariables`
   ne contient que `PATH` et les variables que le contrat §6 nomme, valeurs lues
   par `IConfiguration`. Test L1. Hériter l'environnement est `[SEC_ENV_INHERITED]`.
6. **`http` : `TransportMode = StreamableHttp` explicite** ; `AutoDetect` est
   refusé en L0. `sse` exige `TransportMode = Sse` et un ADR.
7. **`retry_policy: none`** pour tout outil d'un serveur `untrusted` ou sans
   `idempotentHint` ; le handler de résilience du `HttpClient` MCP ne retente
   **pas** les `POST` — un `AddStandardResilienceHandler()` par défaut sur ce
   client serait un retry non déclaré.
8. **Capacités refusées** : `SamplingHandler`, `ElicitationHandler`,
   `RootsHandler` restent nuls ; `resources/*` et `prompts/*` ne sont jamais
   appelés. Un serveur qui en dépend est incompatible avec cette stack.
9. **Une session par identité d'appelant** si le serveur est multi-tenant
   (`tools/mcp.md` §7.8) : le client est choisi par `ToolContext`, pas partagé.
10. **Seuls le texte et `StructuredContent` sont acceptés** ; `MaxResponseBytes`
    borne la lecture avant tout parsing.

---

## 6. Commande de smoke

Deux niveaux — hors ligne (0 token, sans réseau) puis live (`network`) :

```bash
cd workspace/src/{AppName}
dotnet test --filter "FullyQualifiedName~Tools.Mcp&Category!=network"
#   → serveur MCP de test en processus : allowlist, drift, enveloppe, erreurs, troncature, refus image/sampling

dotnet run --project . -- smoke mcp --server internal-crm
#   1. transport déclaré (stdio sans héritage d'env | Streamable HTTP) + initialize → protocolVersion, serverInfo journalisés
#   2. ListToolsAsync → allowlist ⊆ listés                    sinon exit 3 [TOOL_MCP_MISSING]
#   3. sha256(inputSchema) == contrat pour chaque outil        sinon exit 4 [TOOL_SCHEMA_DRIFT]
#   4. chaque outil a un contrat avec side_effect_class + trust sinon exit 5 [TOOL_CONTRACT_MISSING]
#   5. si trust=untrusted : le premier agent consommateur a une suite adversariale sinon exit 6 [INJECTION_SUITE_MISSING]
#   6. exit 0 — aucun CallToolAsync n'est émis par le smoke
```

---

## 7. Pièges connus

Les douze pièges de `tools/mcp.md` §7 valent ici (environnement hérité, tool
poisoning, rug pull de schéma, `isError` traité comme une réponse,
`structuredContent` hostile, timeouts par défaut, réponses volumineuses, session
partagée entre tenants, SSE sans reprise, Windows et `stdio`, serveur « interne »
jugé `trusted`, sampling accepté). Propres au SDK C# :

1. **`McpClientTool` passé directement au modèle.** Il est une `AIFunction` :
   le code compile, l'agent marche, et le modèle voit **tous** les outils du
   serveur avec **leurs** descriptions. C'est le raccourci que la doc du SDK
   montre en premier ; convention 1.
2. **`InheritEnvironmentVariables` à `true` par défaut.** `EnvironmentVariables`
   s'applique **par-dessus** l'environnement hérité : ajouter les variables du
   contrat ne retire pas les autres. Il faut mettre l'héritage à `false`.
3. **`AutoDetect` par défaut.** Le repli sur SSE est silencieux (§2.1).
4. **`WithDescription` sur un serveur `untrusted`.** Le renommage change ce que
   voit le modèle, pas ce que l'outil **renvoie** : la sortie arrive nue dans un
   message d'outil. D'où `UntrustedMcpFunction`.
5. **Windows et `stdio`.** `Command = "npx"` échoue (`npx.cmd`) ; le serveur
   doit écrire de l'UTF-8 sur stdout ; `StandardErrorLines` sert au diagnostic,
   jamais à la réponse.
6. **Un `HttpClient` MCP sans timeout propre.** Le `HttpClient.Timeout` par
   défaut (100 s) dépasse la borne de l'agent ; le timeout du contrat est posé
   par `CancelAfter` **et** le client HTTP a un timeout inférieur à
   `TimeoutSeconds` de l'agent.
7. **Version du SDK et révision du protocole.** Le SDK 2.x suit la révision
   2025-11-25 ; `tools/mcp.md` cite 2025-06-18. Un serveur resté en 2025-03-26
   négocie une version plus ancienne : la version négociée est journalisée au
   démarrage et figure dans le span racine.

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

**Ce que cette fiche doit au contrat.** En `SDDA_EVAL_ISOLATION=mocked`,
**aucun** client MCP n'est créé : ni sous-processus `stdio`, ni connexion
`http`. Chaque outil MCP allowlisté est remplacé par une `FixtureMcpFunction`
qui porte **le même nom, la même description (celle du contrat) et le même
schéma** que l'outil live, et qui sert `SDDA_EVAL_FIXTURES/tools/{outil}.jsonl`
(réponse choisie par les arguments, erreurs déclarées rejouées). Le schéma vient
du contrat épinglé, pas d'un `ListToolsAsync` — il n'y a pas de serveur à
interroger. Un outil sans fixture → code `8` (`[CONFIG_INVALID]`) au démarrage.
Si le serveur est `untrusted`, la sortie de fixture passe **aussi** par
`Trust.Wrap` : un agent évalué isolé doit voir la même enveloppe que l'agent
livré, sinon la L4 mesure un autre prompt effectif.
