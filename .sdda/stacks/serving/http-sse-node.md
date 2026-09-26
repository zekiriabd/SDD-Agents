# Stack: http-sse-node (serving)

Stack ID: serving-http-sse-node
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: surface d'exposition **HTTP + SSE** de l'application agentic **Node/TypeScript** — endpoints, flux SSE, identité de l'appelant établie au transport, reprise, annulation à la déconnexion, sondes, OpenAPI dérivé de l'IR. C'est la surface du livrable `backend-api` en TypeScript, pendant de `serving/fastapi-sse.md` : **mêmes chemins, même `RunEvent`, même table `[CLASS] → HTTP`, même API GATE** ; seul le transport change. Montée sur la maison HTTP active (`backend/node-express.md` ou `backend/nestjs.md`), dont elle réutilise les dépendances : pas de `.libs.json` propre. Suppose `lang/typescript.md` et **réutilise sans le modifier** le `RunService`, le schéma `RunEvent` et la table `[CLASS] → code` de `serving/cli-node.md` / `serving/cli.md` §3.2-3.3.

---

## 1. Rôle et périmètre

La thèse de `serving/fastapi-sse.md` §1 vaut mot pour mot : **le HTTP est un
transport, pas une couche métier**. `serving/http/` traduit une requête en
`RunService.run()` et un flux d'événements en réponse ; une règle métier qui
apparaîtrait ici serait une règle que les evals ne mesurent jamais, parce
qu'elles passent par la CLI et le `RunService`, pas par le port.

Ce que la surface apporte et que la CLI ne peut pas donner, inchangé :
l'identité vient du transport, le streaming a un client réel qui **ferme la
connexion**, le contrat est publiable.

Ce que cette fiche règle en plus, propre à Node :

- **Deux maisons HTTP possibles.** La surface est écrite contre une interface
  minimale (requête → `RunService`, `RunEvent` → réponse) et branchée sur
  Express (`backend/node-express.md`) ou NestJS (`backend/nestjs.md`) selon
  `ApiFramework`. Les deux fiches backend transposaient déjà les routes de
  `fastapi-sse.md` §3 sans surface dédiée ; celle-ci est cette surface.
- **Le SSE est écrit à la main** (`res.write`), sans bibliothèque : le format
  tient en trois lignes, et une bibliothèque de plus serait un endroit de plus
  où le flux peut être bufferisé.

**La CLI reste générée** : elle porte le smoke et les evals (`serving/cli.md`
§3.5). Un projet `backend-api` expose les deux surfaces sur le même
`RunService`.

Hors périmètre : hébergement, TLS, passerelle, dimensionnement.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `serving-http-sse-node` |
| **Langage** | TypeScript 6.0.x, Node 22 (`lang/typescript.md`) |
| **Maison HTTP** | `backend/node-express.md` (`ApiFramework: express`) ou `backend/nestjs.md` (`ApiFramework: nestjs`) — leurs `.libs.json` portent les dépendances |
| **Point d'entrée** | `dist/serve.js` (alias racine `serve.ts`, comme `cli.ts`) → `node dist/serve.js` ; la CLI reste `dist/cli.js` |
| **Paramètres STACK.md** | `DeliverableType: backend-api`, `ApiFramework`, `ApiContractFirst`, `ApiAuthMode`, `ServingLocalPort`, `StreamingEnabled`, `HumanInTheLoopEnabled` |
| **Contrat machine** | OpenAPI 3.1 dérivé de l'IR + flux SSE dont chaque `data:` est un `RunEvent` |
| **Codes** | HTTP en surface, `[CLASS]` en profondeur — table de `serving/fastapi-sse.md` §3.3, **reprise à l'identique** |

**Réserve outillage, dite ici** : à la date de la fiche,
`validate_packaging.py` (part `packaging` de G2) n'admet pour `backend-api` que
des surfaces Python et .NET. Tant qu'il n'a pas appris `http-sse-node`, une
combinaison TypeScript `backend-api` est refusée en G2 : la fiche décrit la
cible, l'outillage doit suivre.

---

## 3. Mapping des concepts SDD_Agents → idiomes HTTP

### 3.1 Endpoints

Ceux de `serving/fastapi-sse.md` §3.1, **mêmes chemins et mêmes sémantiques** :
`POST /v1/runs` (SSE si `Accept: text/event-stream`, JSON sinon),
`POST /v1/runs/{thread_id}/resume` (seulement si checkpointer), `GET` et
`DELETE /v1/runs/{run_id}`, `GET /v1/inspect`, `GET /healthz` (vivacité, aucune
dépendance), `GET /readyz` (disponibilité, 0 token), `GET /openapi.json`. Même
chemin dans les deux langages : un client écrit contre le service Python
fonctionne contre le service Node, et un même test de conformité couvre les
deux.

### 3.2 Le flux SSE

```ts
// workspace/src/{AppName}/serving/http/sse.ts — indépendant d'Express ou de Nest
export async function streamRun(req: IncomingMessage, res: ServerResponse, events: AsyncIterable<RunEvent>,
                                abort: AbortController): Promise<void> {
  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",                           // sans lui, nginx bufferise : le streaming n'existe pas
    Connection: "keep-alive",
  });
  res.flushHeaders();
  const keepalive = setInterval(() => res.write(": keepalive\n\n"), 15_000);
  const onClose = () => abort.abort(new RunCancelled());  // déconnexion → le run s'arrête, le budget aussi
  req.once("close", onClose);
  try {
    for await (const ev of events) {
      const e = RunEventSchema.parse(ev);                 // un événement hors schéma casse le client : refus à l'émission
      if (!res.write(`event: ${e.event}\ndata: ${JSON.stringify(e)}\n\n`)) await once(res, "drain");
    }
  } finally {
    clearInterval(keepalive);
    req.off("close", onClose);
    res.end();                                            // run_finished a été émis par RunService, dernier, toujours
  }
}
```

Les trois règles de `fastapi-sse.md` §3.2 : `run_finished` toujours en dernier
(émis par le `RunService`, y compris sur annulation), commentaire `: keepalive`
toutes les 15 s, `Cache-Control: no-cache` + `X-Accel-Buffering: no`. La
contre-pression (`drain`) en plus : un client lent ne doit pas faire grossir la
mémoire du service.

### 3.3 Statuts HTTP ↔ classes `[CLASS]`

La table de `serving/fastapi-sse.md` §3.3, **sans modification**. Elle vit dans
`serving/status.ts` — le chemin exact que cherche `validate-api-contract`
(`**/serving/status.ts`), donc **pas** sous `serving/http/` —,
**dérivée** de `serving/exit-codes.ts` de la CLI : une seule table de vérité,
deux projections. Une borne atteinte n'est jamais un `5xx`.

### 3.4 Identité

`ApiAuthMode` → un middleware (Express) ou un guard (Nest) qui produit
`Identity` **depuis les en-têtes seulement** et alimente `ToolContext`. Un
`tenant_id` dans le corps est un `400` (`[SERVING_IDENTITY_FROM_PAYLOAD]`),
jamais ignoré. Le corps est parsé par le schéma Zod `RunRequest` en
`z.strictObject` : un champ inconnu est refusé, pas avalé.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── serve.ts                    # racine (dev-backend) : `import "./serving/http/main.js";` → dist/serve.js
└── serving/                    # zone dev-api
    ├── run-service.ts · events.ts · exit-codes.ts · context.ts     # PARTAGÉS avec la CLI — inchangés
    ├── status.ts               # [CLASS] → HTTP, dérivé de exit-codes.ts — ICI, lu par validate-api-contract
    └── http/
        ├── main.ts             # démarrage : maison HTTP active, port ServingLocalPort, arrêt propre (SIGTERM → flush traces)
        ├── routes-runs.ts      # POST /v1/runs, resume, GET, DELETE — aucun métier
        ├── routes-health.ts    # /healthz, /readyz, /v1/inspect
        ├── sse.ts              # §3.2
        ├── identity.ts         # ApiAuthMode → Identity ; JAMAIS depuis le corps
        ├── errors.ts           # toute exception → {class, message, run_id}
        ├── schemas.ts          # RunRequest / RunResponse GÉNÉRÉS depuis l'IR (§6)
        ├── adapters/express.ts | adapters/nest.ts                   # l'une ou l'autre selon ApiFramework
        └── tests/
            ├── openapi-matches-ir.test.ts   # L1 : API GATE — isomorphie OpenAPI ↔ IR
            ├── identity.test.ts             # L1 : tenant du corps refusé ; sans jeton → 401
            ├── sse-contract.test.ts         # L1 : chaque data: parse en RunEvent ; run_finished en dernier
            ├── status-mapping.test.ts       # L1 : table exhaustive [CLASS] → HTTP ; bornes ≠ 5xx
            └── cancellation.test.ts         # L1 : fermeture de la requête → abort, budget arrêté
```

---

## 5. Conventions imposées

Les règles 1 à 10 de `serving/fastapi-sse.md` §5 s'appliquent telles quelles
(identité jamais du corps, `ApiAuthMode: none` justifié par la MISSION, aucun
secret dans une réponse, déconnexion = annulation, `/readyz` sans token, corps
plafonné avant parsing, un run par `thread_id`, `resume` absent sans
checkpointer, traces flushées à l'arrêt, CORS fermé). En Node :

1. **`AbortController` par run**, propagé jusqu'au modèle, aux outils, à
   `embed` et aux requêtes SQL ; `req.on("close")` l'abandonne.
2. **Corps plafonné par la maison HTTP** (`express.json({ limit })` ou
   équivalent Nest) **avant** Zod : `413` sans parser 10 Mo.
3. **Pas de `res.json()` après `res.writeHead` SSE** : une erreur en cours de
   flux sort en événement `error` puis `run_finished`, jamais en statut HTTP
   (déjà envoyé).
4. **Arrêt propre** : `SIGTERM` → `server.close()` (plus de nouvelle
   connexion), abandon des runs en cours, `await` du flush des traces
   (`observability/otel-genai-node.md` §5.1), puis sortie.

---

## 6. L'API GATE — l'OpenAPI est dérivé, jamais écrit

`serving/fastapi-sse.md` §6, **à l'identique** : `schemas.ts` est généré depuis
l'IR (`agents[root].inputSchema` → `RunRequest.input`, `outputSchema` →
`RunResponse.output` et `RunEvent.final.output`, `budget` → bornes de
`max_budget_usd`, `humanInTheLoop` → présence de `/resume`), et
`openapi-matches-ir.test.ts` échoue sur `[API_CONTRACT_DRIFT]`,
`[API_ROUTE_UNBACKED]`, `[API_STATUS_UNMAPPED]`. Avec NestJS, l'OpenAPI que
Nest produit depuis ses décorateurs est **confronté** à celui dérivé de l'IR
(`backend/nestjs.md`), jamais publié à sa place. `ApiContractFirst: false`
exige un ADR.

---

## 7. Commande de smoke

Déterministe, 0 token :

```bash
cd workspace/src/{AppName}
pnpm build
node dist/serve.js --check                               # construit l'application, n'écoute pas ; exit 0
npx --no-install vitest run serving/http --tags-filter="!network"
# service lancé en arrière-plan :
curl -s localhost:{ServingLocalPort}/healthz              # 200
curl -s localhost:{ServingLocalPort}/readyz               # 200 ; 503 + [CLASS] sinon
curl -s -X POST localhost:{ServingLocalPort}/v1/runs      # 401 attendu si ApiAuthMode != none : l'identité se refuse AVANT la validation
```

---

## 8. Contrat d'exécution

La surface HTTP ne remplace pas la CLI : **l'application Node implémente la CLI
de `serving/cli.md` §3.1-3.3 à l'identique** — mêmes commandes, même flux
NDJSON `RunEvent`, mêmes codes de sortie — et les trois points de
`serving/cli.md` §3.5 :

1. `run --json --input-file -` lit l'entrée sur stdin ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'app sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil) et le retrieval
   figé depuis le même dossier, sans aucun appel réseau d'outil ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished`.

Les evals L4–L7 et G4 passent par elle : `node workspace/src/{AppName}/dist/cli.js`
après `pnpm build`, ou le `bin` du package — `--executor cli` côté runner, ou
`--executor cmd:<commande>`. La surface HTTP est jouée **en plus** en G7
(`adversarial-target-check` accepte une cible `--endpoint` HTTP, adresse de
bouclage seulement), sur le même `RunService` : ce qui est mesuré par la CLI est
ce que sert le port.

---

## 9. Pièges connus

Les pièges de `serving/fastapi-sse.md` §8 valent. Propres à Node :

1. **`compression()` devant le SSE** (middleware courant en Express) :
   gzip bufferise le flux jusqu'à remplir un bloc. Exclure `text/event-stream`
   de la compression.
2. **`res.write` sans gestion de `drain`** : mémoire qui croît avec un client
   lent.
3. **Timeout de socket par défaut** : `server.requestTimeout` /
   `headersTimeout` de Node coupent un run long ; les régler au-dessus de
   `AgentTimeoutSec`, pas à l'infini.
4. **`req.on("close")` vs `res.on("close")`** : selon la version de Node et la
   maison HTTP, la fermeture se lit sur l'un ou l'autre ; le test
   `cancellation.test.ts` fixe le comportement réel — **non vérifié** ici sur
   Express 4.22 × Node 22.
5. **Une exception dans un `for await`** qui n'émet pas `run_finished` : le
   client attend. Le `RunService` émet `run_finished` dans son `finally`, pas la
   surface.
