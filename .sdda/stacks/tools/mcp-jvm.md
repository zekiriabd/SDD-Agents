# Stack: mcp-jvm (tools)

Stack ID: tools-mcp-jvm
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin, java
Scope: intégration d'outils exposés par des serveurs **Model Context Protocol**, côté **JVM** (Kotlin et Java) — SDK MCP Java (`io.modelcontextprotocol.sdk`) via `spring-ai-starter-mcp-client`, transports `stdio` / `http` (streamable HTTP) / `sse` (legacy), **allowlist** d'outils, **schéma épinglé par hash**, posture `trusted` / `untrusted` par serveur, **bornes** (timeout, taille de réponse, nombre d'appels), mapping de chaque outil MCP vers un tool-contract. Pendant JVM de `tools/mcp.md` : mêmes règles, même contrat. Côté client uniquement. Pas de `.libs.json` : le starter est ajouté au catalogue du framework actif (`framework/spring-ai.libs.json`, capability `mcp`).

---

## 1. Rôle et périmètre

MCP est un **transport d'outils** : il ne dit rien des effets de bord ni de la
confiance à accorder aux sorties (`tools/mcp.md` §1). La règle ne change pas
avec le langage : **un serveur MCP propose, le contrat dispose.** Seuls les
outils de l'allowlist sont câblés, avec la description **du contrat**, le
schéma d'entrée **épinglé**, la classe d'effet de bord **déclarée par
`architect-tools`**, et la sortie enveloppée si le serveur est `untrusted`.

Ce que la JVM ajoute comme risque, et que cette fiche ferme : Spring AI sait
**tout** câbler tout seul. Le starter `spring-ai-starter-mcp-client` crée par
défaut un client par connexion déclarée **et** publie un `ToolCallback` pour
**chaque** outil que le serveur annonce (`spring.ai.mcp.client.toolcallback.enabled`
vaut `true` par défaut — lu dans les métadonnées de configuration du module
2.0.1). C'est exactement « passer la liste brute au modèle », que
`tools/mcp.md` §5.6 interdit.

Périmètre : transports, cycle de vie, allowlist et fail-fast, épinglage,
enveloppe `untrusted`, bornes, erreurs, tests L2. **Hors périmètre** : resources,
prompts, sampling, elicitation MCP — refusés (§5.6).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `tools-mcp-jvm` |
| **Protocole** | MCP, révision de spécification visée **2025-06-18** (`structuredContent`, `outputSchema`) — celle de `tools/mcp.md` ; la révision négociée est journalisée à l'`initialize` |
| **SDK** | `io.modelcontextprotocol.sdk:mcp` **2.0.0**, tiré par `spring-ai-mcp` 2.0.1 (le BOM Spring AI ne gère pas le SDK ; Maven Central publie 2.0.1) |
| **Starter** | `org.springframework.ai:spring-ai-starter-mcp-client` (BOM 2.0.1) — capability `mcp` du catalogue du framework |
| **API utilisées** | `McpClient.sync(transport)` → `McpSyncClient` (`initialize`, `listTools`, `callTool`, `closeGracefully`) ; `StdioClientTransport` + `ServerParameters` ; `HttpClientStreamableHttpTransport` ; `HttpClientSseClientTransport` (legacy) ; `SyncMcpToolCallback` (Spring AI) |
| **Déclaration** | `STACK.md ## Active Tools & Integrations → MCPServers[]` : `name`, `transport`, `command` \| `url`, `auth_env`, `trust`, `tools_allowlist` |
| **Contrat par outil** | `workspace/pipeline/contracts/tools/{n}-{server}-{tool}.tool.md` — un par outil allowlisté, avant câblage |

### 2.1 Transports

| `transport` | Classe du SDK | Points d'attention JVM |
|---|---|---|
| `stdio` | `StdioClientTransport(ServerParameters)` | l'environnement du sous-processus : `ServerParameters` part d'une **liste d'héritage par défaut** (`PATH`, `HOME`, `USER`, `TEMP`, `APPDATA`, `USERPROFILE`…, relevée dans la classe 2.0.0) complétée par `env(…)` ; les secrets n'y entrent que nommés par le contrat §6. Sous Windows, `npx` est `npx.cmd` |
| `http` | `HttpClientStreamableHttpTransport` | `auth_env` → en-tête `Authorization` par un *request customizer* ; TLS obligatoire hors localhost ; timeouts explicites (§3.4) |
| `sse` | `HttpClientSseClientTransport` | déprécié par la spec 2025-03-26 ; toléré avec un ADR de sortie |

---

## 3. Mapping des concepts SDD_Agents → idiomes MCP JVM

Le tableau de `tools/mcp.md` §3.1 vaut tel quel (nom interne
`{server}__{tool}`, description du serveur = **proposition** jamais envoyée
telle quelle, `inputSchema` épinglé par hash, `outputSchema` validé,
annotations = indices, `isError` mappé vers une erreur déclarée, `content`
texte seul). Ce qui change est le câblage.

### 3.1 Construire les clients à la main, pas par le starter

```yaml
# app/resources/application.yml — le starter reste sur le classpath, son câblage automatique est coupé
spring:
  ai:
    mcp:
      client:
        enabled: false               # aucun client créé depuis des propriétés : la source est MCPServers[] de STACK.md
        toolcallback:
          enabled: false             # et surtout aucun ToolCallback publié pour « tous les outils du serveur »
```

Le starter est gardé pour ce qu'il épingle (SDK, `spring-ai-mcp`,
`SyncMcpToolCallback`) ; ses auto-configurations sont coupées parce qu'elles
câbleraient chaque outil annoncé, sans allowlist, sans hash, sans enveloppe.

### 3.2 Allowlist, hash, fail-fast

**Java**

```java
// tools/mcp/McpToolRegistry.java — package {package}.tools.mcp
public final class McpToolRegistry {
    public List<ToolCallback> build(McpServerConfig server, McpSyncClient client, ContractIndex contracts) {
        client.initialize();                                          // échec = l'application ne démarre pas
        Map<String, McpSchema.Tool> listed = client.listTools().tools().stream()
                .collect(Collectors.toMap(McpSchema.Tool::name, t -> t));
        Set<String> missing = new TreeSet<>(server.toolsAllowlist());
        missing.removeAll(listed.keySet());
        if (!missing.isEmpty()) {
            throw new McpStartupError("TOOL_MCP_MISSING", server.name() + ": " + missing);
        }
        List<ToolCallback> bound = new ArrayList<>();
        for (String name : server.toolsAllowlist()) {                 // on itère l'ALLOWLIST, jamais `listed`
            McpSchema.Tool remote = listed.get(name);
            ToolContract contract = contracts.require(server.name() + "-" + name);   // [TOOL_CONTRACT_MISSING] sinon
            String pinned = Hashing.sha256Struct(remote.inputSchema());               // JSON canonique, comme sdda_lib.hashing
            if (!pinned.equals(contract.inputSchemaSha256())) {
                throw new McpStartupError("TOOL_SCHEMA_DRIFT", server.name() + "/" + name);
            }
            bound.add(new GuardedMcpToolCallback(client, remote, contract, server));  // §3.3
        }
        return bound;
    }
}
```

**Kotlin**

```kotlin
// tools/mcp/McpToolRegistry.kt — package {package}.tools.mcp
class McpToolRegistry {
    fun build(server: McpServerConfig, client: McpSyncClient, contracts: ContractIndex): List<ToolCallback> {
        client.initialize()
        val listed = client.listTools().tools().associateBy { it.name() }
        val missing = server.toolsAllowlist - listed.keys
        if (missing.isNotEmpty()) throw McpStartupError("TOOL_MCP_MISSING", "${server.name}: ${missing.sorted()}")
        return server.toolsAllowlist.map { name ->                     // l'ALLOWLIST, jamais `listed`
            val remote = listed.getValue(name)
            val contract = contracts.require("${server.name}-$name")
            if (Hashing.sha256Struct(remote.inputSchema()) != contract.inputSchemaSha256) {
                throw McpStartupError("TOOL_SCHEMA_DRIFT", "${server.name}/$name")
            }
            GuardedMcpToolCallback(client, remote, contract, server)
        }
    }
}
```

`listTools()` rend une page ; si le serveur pagine (`nextCursor`), la
registry suit les curseurs jusqu'au bout avant de comparer — un outil
allowlisté en page 2 n'est pas « manquant ». La notification
`tools/list_changed` est journalisée et **ignorée** : aucun outil n'apparaît à
chaud ; `SyncMcpToolCallbackProvider` (qui invalide son cache sur
`McpToolsChangedEvent`) n'est pas utilisé.

### 3.3 L'outil gardé : description du contrat, bornes, enveloppe

`GuardedMcpToolCallback` implémente `ToolCallback` :

- `getToolDefinition()` rend **le nom et la description du contrat** et le
  schéma épinglé — jamais ceux du serveur (tool poisoning, `tools/mcp.md` §7.2) ;
- `call(String json, ToolContext ctx)` : valide les arguments contre le schéma
  épinglé, compte l'appel dans le `ToolBudget` du run (`maxToolCalls`,
  `framework/spring-ai.md` §3.2), appelle `client.callTool(...)` sous le
  `timeout_s` du contrat, borne la réponse à `max_response_bytes` **avant**
  parsing, refuse tout `content` autre que texte (`[TOOL_CONTENT_UNSUPPORTED]`),
  valide `structuredContent` contre `output_schema`, mappe `isError` vers une
  erreur déclarée (`error_map` du contrat, sinon `TOOL_ERROR_UNDECLARED`), et —
  si le serveur est `untrusted` — rend le texte **enveloppé**
  (`UntrustedRenderer`, source `mcp:{server}:{tool}`) ;
- émet un span `execute_tool` (`gen_ai.tool.name` = nom du contrat,
  `sdda.tool.server`, `sdda.tool.transport`, `sdda.tool.trust`, arguments
  redigés) — `observability/otel-genai-jvm.md`.

`SyncMcpToolCallback` de Spring AI n'est pas exposé directement au modèle : il
reprend la description et le schéma **du serveur**.

### 3.4 Bornes

| Borne | Où | Défaut |
|---|---|---|
| délai d'initialisation | `McpClient.sync(t).initializationTimeout(…)` | 10 s — échec = pas de démarrage |
| délai par requête | `McpClient.sync(t).requestTimeout(…)` = `timeout_s` le plus long des contrats du serveur | le `timeout_s` **de chaque contrat** est en plus appliqué autour de `callTool` (un `Future` borné) : le délai du client est un filet, pas la borne |
| taille de réponse | `max_response_bytes` / `max_response_chars` du contrat | lecture bornée avant parsing ; `truncated: true` signalé au modèle |
| nombre d'appels | `ToolBudget` du run (`maxToolCalls` de l'IR) | au-delà : `bound_exceeded`, politique `onBoundExceeded` |
| retry | `retry_policy` du contrat ; `none` si `untrusted` ou `idempotentHint` absent | Resilience4j dans `tools/`, jamais dans le client MCP |
| capacités client | `capabilities(ClientCapabilities)` **sans** `sampling` ni `elicitation`, et aucun gestionnaire `sampling(…)` / `elicitation(…)` enregistré | un serveur qui les exige est incompatible (§5.6) |

### 3.5 Cycle de vie

Démarrage : pour chaque serveur, construction du transport, `initialize`,
`listTools`, allowlist ∩ hashes (§3.2) — un échec arrête l'application
(code `8` en CLI). Par run : réutilisation du client (un par serveur ; **un par
identité d'appelant** si le serveur `http` est multi-tenant, `tools/mcp.md`
§7.8). Arrêt : `closeGracefully()` dans un `@PreDestroy`, puis `close()` après
5 s ; le sous-processus `stdio` est tué s'il survit.

---

## 4. Structure de fichiers générée

Zone de `dev-tools` (`workspace/src/**/tools/**`), disposition à plat
(`lang/*.md` §4) ; `.kt` ou `.java` selon le langage actif.

```
workspace/src/{AppName}/tools/mcp/
├── McpServerConfig          # record / data class lu depuis la configuration ← STACK.md MCPServers[] ; auth = SecretValue
├── McpClients               # transport par serveur (stdio | http | sse), env allowlist, timeouts, capacités sans sampling
├── McpToolRegistry          # §3.2 — allowlist, hash, fail-fast
├── GuardedMcpToolCallback   # §3.3 — description du contrat, bornes, enveloppe, erreurs, span
├── McpErrors                # McpStartupError ; codes INVALID_ARGS / SERVER_ERROR / TIMEOUT / TOOL_ERROR_UNDECLARED
└── McpSmoke                 # §6

workspace/src/{AppName}/tests/tools/mcp/
├── McpToolRegistryTest       # L2 : allowlist manquante -> fail-fast ; drift de schéma -> fail-fast ; outil non allowlisté invisible
├── GuardedMcpToolCallbackTest # L2 : @ParameterizedTest(name = "{0}", quoteTextArguments = false) — happy, isError mappé, erreur non déclarée,
│                             #      timeout, réponse trop grosse, image refusée, structuredContent invalide, enveloppe untrusted
├── McpClientsTest            # L2 : env du sous-processus stdio = liste par défaut + variables du contrat, rien d'autre ; sampling refusé
└── McpLive{Server}NetworkTest # @Tag("network") : initialize + listTools + hashes sur le VRAI serveur — seconde moitié de la TOOL GATE
```

Le serveur MCP de test (outils `echo`, `fail_declared`, `fail_unknown`,
`slow`, `huge`, `poison`) est un petit serveur `stdio` embarqué dans
`tests/resources/` et lancé par `StdioClientTransport` — le même chemin que la
production, sans réseau. Le SDK Java fournit aussi un côté serveur
(`McpServer`, `StdioServerTransportProvider`), qui peut servir à l'écrire.

---

## 5. Conventions imposées

Les onze règles de `tools/mcp.md` §5 s'appliquent. Elles se traduisent ainsi :

1. **Un tool-contract par outil allowlisté, avant câblage** ; sinon
   `[TOOL_CONTRACT_MISSING]` au démarrage.
2. **La description vue par le modèle est celle du contrat** —
   `GuardedMcpToolCallback.getToolDefinition()`, jamais celle du serveur.
3. **`inputSchema` épinglé par hash**, avec la **même** canonicalisation que
   `sdda_lib.hashing.canonical_json` (clés triées, séparateurs `,` `:`,
   non-ASCII brut) : le contrat est écrit par un script Python, la vérification
   par la JVM, et un octet d'écart rend le drift permanent.
4. **`side_effect_class` déclarée par `architect-tools`**, jamais déduite des
   annotations.
5. **`env` explicite pour `stdio`** : `ServerParameters.builder(cmd).env(Map.of(...))`
   avec uniquement les variables que le contrat §6 nomme ; aucune clé de
   l'application n'est ajoutée « par commodité ».
6. **Capacités refusées** : ni `sampling`, ni `elicitation`, ni `resources/*`,
   ni `prompts/*` ; `spring.ai.mcp.client.enabled: false` et
   `toolcallback.enabled: false` (§3.1).
7. **`retry_policy: none`** pour tout outil `untrusted` ou sans `idempotentHint`.
8. **Seul `content` texte (et `structuredContent`)** ; lecture bornée avant
   parsing.
9. **Un span `execute_tool` par appel**, y compris les refus.
10. **Nom interne `{server}__{tool}`** unique ; le modèle voit `contract.name`.
11. **Les clients MCP sont des beans `@PreDestroy`-fermés** ; jamais un client
    créé par appel (un sous-processus `stdio` par appel d'outil est un
    démarrage de serveur par appel).

---

## 6. Commande de smoke

```bash
cd workspace/src/{AppName}
./gradlew test --tests '*.tools.mcp.*'                      # hors ligne : serveur de test stdio embarqué
cd ../../..
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar health --live --json
#   1. connect (transport déclaré) + initialize → protocolVersion, serverInfo journalisés
#   2. listTools → allowlist ⊆ listés                        sinon [TOOL_MCP_MISSING]
#   3. hash(inputSchema) == contrat pour chaque outil          sinon [TOOL_SCHEMA_DRIFT]
#   4. chaque outil a un contrat avec side_effect_class + trust sinon [TOOL_CONTRACT_MISSING]
#   5. aucun callTool n'est émis (un outil write-* ne s'appelle pas « pour voir »)
(cd workspace/src/{AppName} && ./gradlew test -PincludeTags=network --tests '*.tools.mcp.*')
```

---

## 7. Contrat d'exécution

L'application JVM implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique**. Les trois points de `serving/cli.md` §3.5, pour MCP :

1. `run --json --input-file -` lit l'entrée sur `stdin` ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'application sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil,
   `tools/{nom_du_contrat}.jsonl`) et le retrieval figé depuis le même
   dossier, **sans aucun appel réseau d'outil** : aucun client MCP n'est
   construit, aucun sous-processus `stdio` n'est lancé ; chaque
   `GuardedMcpToolCallback` est remplacé par un `FixtureToolCallback` de même
   nom et même définition (description et schéma du contrat), qui rejoue les
   lignes de fixture ; un outil allowlisté sans fixture → refus de démarrer,
   code `8` `[CONFIG_INVALID]` ;
3. `retrieve --json --index ID --query-file - [--k N]` n'appelle aucun outil
   MCP : il interroge l'index (`rag/hybrid-jvm.md`).

Commande de lancement : `java -jar workspace/src/{AppName}/build/libs/{AppName}.jar`
depuis la racine du dépôt (livrable), `./gradlew run --args='…'` en
développement ; `--executor cli` la dérive du langage actif,
`--executor cmd:<commande>` l'impose. Le build produit exactement
`build/libs/{AppName}.jar`.

---

## 8. Pièges connus

Les douze pièges de `tools/mcp.md` §7 s'appliquent (environnement hérité,
tool poisoning, rug pull de schéma, `isError` traité comme réponse,
`structuredContent` hostile, timeouts par défaut, réponses volumineuses,
session partagée entre tenants, `sse` sans reprise, Windows et `stdio`,
serveur « interne » jugé `trusted`, sampling accepté). Propres à la JVM :

1. **Le câblage automatique du starter.** Sans
   `spring.ai.mcp.client.toolcallback.enabled: false`, chaque outil de chaque
   serveur devient un `ToolCallback` disponible pour `ChatClient` — allowlist
   contournée sans une ligne de code.
2. **`SyncMcpToolCallbackProvider` et le rechargement à chaud.** Il invalide son
   cache sur `McpToolsChangedEvent` : un outil ajouté côté serveur apparaît au
   run suivant. La registry figée au démarrage (§3.2) ne le fait pas.
3. **`requestTimeout` pris pour la borne.** C'est une valeur par client
   (défaut du starter : 20 s) ; la borne est le `timeout_s` de chaque contrat,
   appliqué autour de chaque appel.
4. **Hash calculé sur l'objet Java plutôt que sur le JSON canonique.** Deux
   sérialisations d'un même schéma (ordre des clés, `1` vs `1.0`) donnent deux
   hash : drift permanent au démarrage. `Hashing.sha256Struct` sérialise en
   JSON canonique, testé contre des vecteurs Python.
5. **Sous-processus `stdio` orphelin.** Un `System.exit` sans fermeture du
   contexte laisse le serveur MCP vivant ; d'où la sortie par
   `SpringApplication.exit` (`lang/java.md` §8.3) et `@PreDestroy`.
6. **Version du SDK tirée par Spring AI.** `spring-ai-mcp` 2.0.1 tire `mcp`
   2.0.0 alors que 2.0.1 est publié ; le surclasser à la main est une
   résolution que Spring AI n'a pas testée. Suivre la version tirée.
