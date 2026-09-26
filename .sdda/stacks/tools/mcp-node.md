# Stack: mcp-node (tools)

Stack ID: tools-mcp-node
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: intégration d'outils exposés par des serveurs **Model Context Protocol**, côté **Node/TypeScript** — transports `stdio` / `http` (streamable HTTP) / `sse` (legacy) via le SDK officiel `@modelcontextprotocol/sdk`, allowlist, posture `trusted` / `untrusted` par serveur, mapping de chaque outil MCP vers un **tool-contract**, bornes (timeout, taille, retry). Pendant Node de `tools/mcp.md` : **la règle « un serveur MCP propose, le contrat dispose » et toutes ses conséquences sont identiques**. Client uniquement. Pas de `.libs.json` : `@modelcontextprotocol/sdk` est tiré par `@langchain/mcp-adapters` (capability `mcp` de `framework/langgraph-js.libs.json`) et épinglé au même endroit.

---

## 1. Rôle et périmètre

Tout le §1 de `tools/mcp.md` vaut ici : MCP est un **transport d'outils**, et
chaque outil MCP passe par la même porte que tout autre outil — un
`tool-contract`, la TOOL GATE, le moindre privilège. La description vue par le
modèle est celle **du contrat**, le schéma d'entrée est **épinglé par hash**, la
classe d'effet de bord est **déclarée par `architect-tools`**, la sortie d'un
serveur `untrusted` est **enveloppée**.

Ce que Node change, et ce que la fiche règle :

- **Deux API pour le même protocole.** Le SDK officiel
  (`@modelcontextprotocol/sdk`, `Client` + transports) et l'adaptateur
  LangChain (`@langchain/mcp-adapters` : `MultiServerMCPClient`,
  `loadMcpTools`) qui convertit les outils en `DynamicStructuredTool`. La fiche
  impose le **SDK** pour la découverte, l'épinglage et l'appel ; l'adaptateur
  est **toléré** à une condition stricte (§5.6), parce qu'il retourne la liste
  complète des outils du serveur avec **leurs** descriptions.
- **L'environnement du sous-processus `stdio`.** Le SDK Node ne transmet pas
  tout `process.env` : il fusionne une liste de variables jugées sûres
  (`getDefaultEnvironment()` : `PATH`, `HOME`, `USER`… ; `APPDATA`,
  `USERPROFILE`, `SYSTEMROOT`… sous Windows — lu dans le source de la 1.30.1)
  avec l'`env` fourni. C'est mieux que l'héritage total, et ce n'est pas encore
  une allowlist : `HOME`/`USERPROFILE` ouvrent le répertoire personnel au
  serveur. L'`env` explicite du contrat reste obligatoire (§5.5).

Hors périmètre, comme en Python : resources, prompts, sampling, elicitation —
**refusés** (§5.9 de `tools/mcp.md`). Exposer l'application en serveur MCP :
hors de cette fiche.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `tools-mcp-node` |
| **Protocole** | MCP, révision **2025-06-18** (structured output, `outputSchema`) ; compatible **2025-03-26** (streamable HTTP, annotations) — comme `tools/mcp.md` |
| **SDK** | `@modelcontextprotocol/sdk` **1.30.1** — `Client` (`@modelcontextprotocol/sdk/client/index.js`), `StdioClientTransport` (`…/client/stdio.js`), `StreamableHTTPClientTransport` (`…/client/streamableHttp.js`), `SSEClientTransport` (`…/client/sse.js`) |
| **Adaptateur** | `@langchain/mcp-adapters` **1.1.4** — dépend de `@modelcontextprotocol/sdk ^1.30.0` ; `loadMcpTools(serverName, client, options)` sur un `Client` déjà vérifié |
| **Langage** | TypeScript 6.0.x, Node 22 (`lang/typescript.md`) |
| **Déclaration** | `STACK.md ## Active Tools & Integrations → MCPServers[]` : `name`, `transport`, `command` \| `url`, `auth_env`, `trust`, `tools_allowlist` — inchangé |
| **Contrat par outil** | `workspace/pipeline/contracts/tools/{n}-{server}-{tool}.tool.md` — un par outil allowlisté, avant câblage |

`@modelcontextprotocol/sdk` doit être déclaré **en dépendance directe** du
projet, au même pin que celui que résout l'adaptateur : l'application importe
`Client` elle-même, et une dépendance transitive importée directement se casse
au premier dédoublonnage de `pnpm`.

### 2.1 Transports

| `transport` | Classe | Points d'attention Node |
|---|---|---|
| `stdio` | `StdioClientTransport({ command, args, env, cwd, stderr })` | lancé par `cross-spawn`, `shell: false` : `npx` fonctionne sous Windows sans `.cmd` ; `stderr` du serveur hérité par défaut → il sort sur **notre** stderr, jamais sur stdout (le NDJSON reste propre) |
| `http` | `StreamableHTTPClientTransport(new URL(url), { requestInit: { headers } })` | `auth_env` → en-tête `Authorization` ; TLS hors localhost |
| `sse` | `SSEClientTransport` | **déprécié** par la spec 2025-03-26, toléré avec un ADR de sortie |

---

## 3. Mapping des concepts SDD_Agents → idiomes MCP

La table de `tools/mcp.md` §3.1 (nom, description-proposition, `inputSchema`
épinglé, `outputSchema`, annotations = indices, `trust`, `auth`, timeouts,
`isError`, erreurs JSON-RPC, types de `content`) s'applique **entièrement**. Les
idiomes :

### 3.1 Allowlist : découverte ≠ câblage

```ts
// workspace/src/{AppName}/tools/mcp/registry.ts
import { Client } from "@modelcontextprotocol/sdk/client/index.js";

export async function buildMcpTools(server: McpServerConfig, client: Client, contracts: ContractIndex): Promise<BoundTool[]> {
  const listed = new Map((await client.listTools(undefined, { timeout: server.listTimeoutMs })).tools.map((t) => [t.name, t]));
  const missing = server.toolsAllowlist.filter((n) => !listed.has(n));
  if (missing.length) throw new McpStartupError("TOOL_MCP_MISSING", `${server.name}: ${missing.sort().join(", ")}`);

  return server.toolsAllowlist.map((name) => {                       // on itère l'ALLOWLIST, jamais `listed`
    const remote = listed.get(name)!;
    const contract = contracts.require(`${server.name}-${name}`);    // [TOOL_CONTRACT_MISSING] sinon
    if (sha256Canonical(remote.inputSchema) !== contract.inputSchemaSha256) {
      throw new McpStartupError("TOOL_SCHEMA_DRIFT", `${server.name}/${name}`);
    }
    return bindTool(contract.toSpec({ trust: server.trust }), makeCaller(client, remote.name, contract, server));
  });
}
```

`sha256Canonical` sérialise avec clés triées : le hash doit être le même que
celui que `gen_mcp_contracts` / le contrat ont calculé, quel que soit l'ordre
des clés que le serveur renvoie. La notification `tools/list_changed` est
journalisée et **ignorée** (pas de re-list en cours de run).

### 3.2 Appel borné et enveloppe `untrusted`

```ts
// workspace/src/{AppName}/tools/mcp/wrap.ts
export async function callMcp(client: Client, name: string, args: Record<string, unknown>,
                              c: ToolContract, server: McpServerConfig, signal: AbortSignal): Promise<ToolResult> {
  const res = await client.callTool({ name, arguments: args }, undefined, {
    timeout: c.timeoutS * 1000,          // sans lui : DEFAULT_REQUEST_TIMEOUT_MSEC = 60 000 ms du SDK
    maxTotalTimeout: c.timeoutS * 1000,  // un serveur qui envoie des progress ne prolonge pas l'appel indéfiniment
    signal,                               // annulation propagée depuis la surface
  });
  if (res.isError) return ToolResult.error(mapError(res, c));        // table error_map du contrat ; sinon TOOL_ERROR_UNDECLARED
  const text = extractText(res, c.maxResponseBytes);                 // text + structuredContent seulement ; image/audio refusés
  const payload = c.outputSchema ? validateOrFail(res.structuredContent ?? JSON.parse(text), c.outputSchema) : text;
  const body = typeof payload === "string" ? payload : JSON.stringify(payload);
  return server.trust === "untrusted"
    ? ToolResult.ok(wrapUntrusted(body, { source: `mcp:${server.name}:${name}`, maxChars: c.maxResponseChars }), "untrusted")
    : ToolResult.ok(body, "trusted");
}
```

Le `Client` est construit avec des capacités **vides**
(`new Client({ name, version }, { capabilities: {} })`) : sans capacité
`sampling` ni `elicitation` déclarée, le serveur n'a pas le droit de les
demander, et toute requête `sampling/createMessage` reçue est rejetée.

### 3.3 Cycle de vie

Identique à `tools/mcp.md` §3.4 : connexion + `listTools` + allowlist ∩ hashes
au **démarrage** (échec = l'application ne démarre pas), session réutilisée par
run, un span `execute_tool` par appel, `client.close()` à l'arrêt (le transport
`stdio` termine le sous-processus). Session `http` : **une par identité
d'appelant** si le serveur est multi-tenant.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/tools/mcp/                 # zone dev-tools
├── config.ts            # McpServerConfig (Zod) lu depuis la config ← STACK.md MCPServers[] ; auth = nom de variable
├── client.ts            # connect(server) : Client + Stdio | StreamableHTTP | SSE ; env explicite ; en-têtes
├── registry.ts          # buildMcpTools (§3.1) ; fail-fast ; nom interne {server}__{tool}
├── wrap.ts              # callMcp (§3.2) ; extractText ; mapError ; validation outputSchema
├── errors.ts            # McpStartupError ; codes INVALID_ARGS / SERVER_ERROR / TIMEOUT / TOOL_ERROR_UNDECLARED
├── smoke.ts             # cf. §6
└── tests/
    ├── fake-server.ts   # serveur MCP de test EN PROCESSUS (McpServer de …/server/mcp.js + InMemoryTransport de …/inMemory.js) : echo, fail_declared, fail_unknown, slow, huge, poison
    ├── registry.test.ts # L2 : allowlist manquante → fail-fast ; drift → fail-fast ; outil non allowlisté invisible
    ├── wrap-untrusted.test.ts   # L2 : enveloppe présente ; troncature ; image refusée ; structuredContent validé
    ├── errors.test.ts   # L2 : it.each(cases)("%s", …) — happy-1, error-NOT_FOUND, timeout-1, auth-401 ; isError mappé
    └── live-{server}.test.ts    # describe("network …") : initialize + listTools + hashes sur le VRAI serveur
```

Les squelettes de contrats (`gen_mcp_contracts`, script Python du framework)
ne dépendent pas du langage de l'application.

---

## 5. Conventions imposées

Les règles 1 à 11 de `tools/mcp.md` §5 s'appliquent telles quelles. Leur forme
Node :

1. **Un tool-contract par outil allowlisté, avant câblage.**
2. **La description vue par le modèle est celle du contrat.**
3. **`inputSchema` épinglé par hash canonique**, vérifié au démarrage.
4. **`side_effect_class` déclarée**, jamais déduite de `annotations`.
5. **`env` explicite pour `stdio`** : `env: { CRM_MCP_TOKEN: settings.crmToken.reveal() }` —
   uniquement ce que le contrat nomme. Le SDK y ajoute sa liste par défaut ;
   si le contrat exige de retirer `HOME`/`USERPROFILE`, lancer le serveur par un
   `command` qui les efface (non fourni par le SDK). `env` absent est signalé en
   L0 (`[SEC_ENV_INHERITED]`), même si l'héritage du SDK est partiel.
6. **`@langchain/mcp-adapters` toléré sous condition** : on lui passe un
   `Client` **déjà vérifié** (`loadMcpTools(serverName, client)`), puis on
   **filtre** la liste retournée par l'allowlist, on **remplace** `description`
   par celle du contrat et on **enveloppe** l'appel. `MultiServerMCPClient`
   (qui ouvre lui-même les connexions et renvoie tous les outils) n'est pas
   utilisé : la découverte doit passer par §3.1. Jamais la liste brute au
   `bindTools` du modèle.
7. **`retry_policy: none`** pour un serveur `untrusted` ou sans `idempotentHint`.
8. **Seul `text` (et `structuredContent`)** ; `maxResponseBytes` borne la
   lecture avant parsing.
9. **Capacités client vides** (§3.2) : sampling, elicitation, roots refusés.
10. **Un span par appel** (`gen_ai.tool.name` = nom du contrat, `sdda.tool.server`,
    `sdda.tool.transport`, `sdda.tool.trust`, arguments redigés) — helpers de
    `observability/otel-genai-node.md`.
11. **Nom interne `{server}__{tool}`**, unique ; le modèle voit `contract.name`.

---

## 6. Commande de smoke

Hors ligne (0 token, sans réseau), puis live (`network`) :

```bash
cd workspace/src/{AppName}
npx --no-install vitest run tools/mcp --tags-filter="!network"   # si le tag `network` est déclaré (eval/vitest-eval.md §5.1)
pnpm build
node dist/tools/mcp/smoke.js --server internal-crm
#   1. connect + initialize → protocolVersion, serverInfo journalisés (stderr)
#   2. listTools → allowlist ⊆ listés                          sinon exit 3 [TOOL_MCP_MISSING]
#   3. sha256Canonical(inputSchema) == contrat                 sinon exit 4 [TOOL_SCHEMA_DRIFT]
#   4. chaque outil a un contrat (side_effect_class + trust)   sinon exit 5 [TOOL_CONTRACT_MISSING]
#   5. trust=untrusted : l'agent consommateur a une suite adversariale sinon exit 6 [INJECTION_SUITE_MISSING]
#   6. exit 0 — AUCUN callTool émis par le smoke
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

Pour MCP, l'isolement a une conséquence qui se vérifie : **aucune connexion
MCP n'est ouverte** — ni sous-processus `stdio`, ni session HTTP. Les outils
allowlistés sont servis par `SDDA_EVAL_FIXTURES/tools/{nom-du-contrat}.jsonl`
(format du squelette Python, repris à l'identique) ; un outil allowlisté sans
fixture fait refuser le démarrage (code 8). Une vérification de hash qui
exigerait le serveur est donc **sautée en isolement**, et `health --live` reste
la commande qui la joue. Hors isolement, un serveur injoignable au démarrage
sort en code 6 (`[TOOL_MCP_DISCONNECTED]`).

Lancement : `node workspace/src/{AppName}/dist/cli.js` après `pnpm build`
(`lang/typescript.md` §4), ou le `bin` du package ; `--executor cli` côté
runner, ou `--executor cmd:<commande>`.

---

## 8. Pièges connus

Les pièges 1 à 12 de `tools/mcp.md` §7 valent tous (env, tool poisoning, rug
pull, `isError`, `structuredContent` hostile, timeouts, réponses volumineuses,
session partagée, `sse`, Windows, « interne donc trusted », sampling). Propres
à Node :

1. **Timeout implicite de 60 s.** Sans `timeout`, `callTool` attend
   `DEFAULT_REQUEST_TIMEOUT_MSEC` (60 000 ms, lu dans le source) — souvent
   au-delà du `timeout_s` du contrat et de la borne de l'agent.
2. **`resetTimeoutOnProgress`** (défaut `false` dans le SDK) : activé, un serveur qui émet des
   notifications de progrès repousse le timeout sans fin. Laisser à `false` et
   poser `maxTotalTimeout`.
3. **`MultiServerMCPClient.getTools()` passé tel quel au modèle** : tous les
   outils du serveur, avec les descriptions du serveur — exactement ce que §5.2
   et §5.6 interdisent.
4. **Import d'une dépendance transitive** (§2) : `import … from
   "@modelcontextprotocol/sdk/…"` sans dépendance directe casse sous `pnpm`
   (pas de hoisting par défaut).
5. **Promesse de `client.close()` non attendue** à `SIGINT` : le sous-processus
   `stdio` survit à l'application. `await` dans le handler d'arrêt, avant le
   flush des traces.
