# Stack: node-express (backend)

> §5 (Librairies) suit `node-express.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: backend-node-express
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: la **maison HTTP** en Express 4 + TypeScript quand `DeliverableType: backend-api` et `ApiFramework: express` — projet `pnpm`, composition, configuration validée par Zod, middlewares transverses, sécurité, journalisation `pino`, packaging. Hérité de SDD_Pro `backend/node-express.md`, transposé : aucun ORM (Prisma retiré), aucune entité, le contrat OpenAPI est **généré depuis l'IR** et non annoté à la main. Suppose `lang/typescript.md`. Il n'existe pas encore de fiche `serving/*-sse` TypeScript : les routes du §3 sont la transposition de `serving/fastapi-sse.md` §3, mêmes chemins, même `RunEvent`.

---

## 1. Rôle et périmètre

Même partage que `backend/python-fastapi.md` : la surface dit par où l'on
entre, cette fiche dit dans quelle maison. Express est retenu pour ce qu'il
est — un routeur minimal qui n'impose rien — et c'est précisément pour cela que
la fiche impose beaucoup : sans structure imposée par le framework, c'est la
fiche qui la tient.

Ce qui est gardé de SDD_Pro : TypeScript strict avec `noUncheckedIndexedAccess`,
ESM, Zod au bord, `pino` pour les logs, `helmet`/`cors`/`compression`/rate-limit
en middlewares systématiques, la couche d'erreurs unique. Ce qui est retiré :
Prisma et tout accès direct à une base (→ `dataaccess/`), `swagger-jsdoc` (le
contrat n'est pas écrit en commentaires, il est dérivé de l'IR), `dotenv` en
production (l'environnement fournit les valeurs).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `backend-node-express` |
| **Langage** | TypeScript 6.0.x — **plafonné sous 7** (`lang/typescript.md` §7) |
| **Runtime** | Node.js 22 LTS, ESM (`"type": "module"`) |
| **Framework HTTP** | Express 4.22.x — la ligne 4 : la 5 est publiée, la montée est une tâche dédiée (§7) |
| **Build** | `pnpm` ; `tsc` pour le build, `tsx` pour le dev |
| **Validation** | Zod 4 — schémas **générés** depuis l'IR (`app/models.ts`) |
| **Config** | `app/config.ts` : `process.env` lu **une fois**, parsé par un schéma Zod, gelé ; `.env` chargé par Node (`--env-file`) en dev seulement |
| **Logs** | `pino` + `pino-http`, JSON sur stderr |
| **Paramètres STACK.md** | `DeliverableType: backend-api`, `ApiFramework: express`, `ApiAuthMode`, `ApiContractFirst`, `## Active Architecture Pattern` |
| **Smoke** | §8 |

---

## 3. Mapping couche → répertoire

Racine `workspace/src/{AppName}/src/` (cf. `lang/typescript.md` §4) :

| Couche | Emplacement |
|---|---|
| Entrée HTTP | `serving/http/app.ts` (`createApp(runService)`), `serving/http/routes/runs.ts`, `routes/health.ts`, `serving/http/openapi.ts` |
| Service | `app/runService.ts` · `app/usecases/` |
| Domaine | `app/domain/{contexte}/` — `values.ts`, `rules.ts`, `ports.ts` (`interface`) |
| Composition | `app/composition.ts` — `buildSystem(config): RunService` |
| Config | `app/config.ts` · `app/app_config.json` |
| Modèle | `app/models.ts` (généré) · `data/schemas/` |
| Middlewares | `serving/http/middleware/{errors,identity,security,correlation}.ts` |
| Résilience | `app/resilience.ts` — `p-retry` + `AbortController`, une politique nommée par dépendance |
| Tests | `workspace/src/{AppName}/tests/` (vitest) |
| Projet | `package.json` · `tsconfig.json` · `README.md` · `Dockerfile` |

Routes (transposition de `serving/fastapi-sse.md` §3.1) : `POST /v1/runs`
(JSON ou SSE selon `Accept`), `GET /v1/runs/{id}`, `POST /v1/runs/{id}/resume`
(si `HumanInTheLoopEnabled`), `GET /healthz`, `GET /readyz`, `GET /openapi.json`.

---

## 4. Idiomes imposés

- `createApp(runService)` reçoit le système composé ; elle monte middlewares et
  routes, rien d'autre. Un `import` de `agents/` dans `serving/` est une faute.
- `tsconfig` : `"strict": true`, `"noUncheckedIndexedAccess": true`,
  `"exactOptionalPropertyTypes": true`, `"module": "NodeNext"`,
  `"verbatimModuleSyntax": true`. Ces flags sont load-bearing : les retirer fait
  compiler du code qui déréférence `undefined`.
- Validation : `RunRequestSchema.parse(req.body)` **avant** tout coût ; un
  échec rend `400` en ProblemDetails avec les chemins Zod, jamais le corps.
- Identité : `req.identity` posée par le middleware d'identité depuis le jeton
  ou l'en-tête ; une route qui lit `req.body.tenant_id` est
  `[SERVING_IDENTITY_FROM_PAYLOAD]`.
- Erreurs : un seul `errorHandler` final, `(err, req, res, next)`, qui mappe
  vers la table `[CLASS] → statut` ; aucun `try/catch` de formatage dans une
  route ; les rejets de promesses remontent via `express-async-errors` ou un
  wrapper `asyncHandler`.
- SSE : `res.flushHeaders()`, keepalive périodique, `req.on("close")` →
  `AbortController.abort()` propagé au run.
- Sécurité : `helmet()` en premier middleware ; `cors({ origin: config.corsOrigins })`
  explicite ; `express-rate-limit` par appelant ; `compression()` sauf sur
  `text/event-stream`.
- Config : `const config = ConfigSchema.parse(process.env)` **une fois** dans
  `app/config.ts`, `Object.freeze`, exporté ; nulle part ailleurs `process.env`.
- Logs : `pino` avec `redact` sur les champs sensibles ; `pino-http` avec
  `genReqId` = `run_id` ; jamais `console.*`.

---

## 5. Librairies

Source de vérité : `node-express.libs.json`.

**CORE** : `express`, `zod`, `pino`, `pino-http`, `helmet`, `cors`, `compression`,
`express-rate-limit`, `p-retry`, `typescript`, `tsx`, `@types/node`,
`@types/express`, `@types/cors`, `@types/compression`, `eslint`, `typescript-eslint`,
`vitest`.

**ON-DEMAND** : `jose` (`auth-jwt`), `@opentelemetry/sdk-node` +
`@opentelemetry/api` (`otel-http`).

**Absents par conception** : `prisma`, `@prisma/client`, `swagger-jsdoc`,
`swagger-ui-express`, `dotenv`, `axios`, `class-validator` — chacun avec sa
raison dans le catalogue.

---

## 6. Interdits

- `any`, `as unknown as T`, `// @ts-ignore` sans justification écrite ;
- `console.log` ; `process.env` hors `app/config.ts` ; secret littéral ;
- logique métier dans une route ou un middleware ; appel de modèle hors `agents/` ;
- `fetch` direct hors `app/resilience.ts` (timeout et backoff obligatoires) ;
- `require()` en ESM ; imports relatifs `../../../` (alias `#app/*` via `imports`
  de `package.json`) ;
- `process.exit()` hors du gestionnaire d'arrêt ;
- `cors({ origin: true })` ou `*` avec identifiants ;
- `TODO`, `FIXME`, code commenté ;
- `node_modules/`, `dist/`, `.env` commités ; `engines.node` absent ; lockfiles
  concurrents (`pnpm` seul).

---

## 7. Pièges connus

1. **TypeScript ≥ 7.** La chaîne (`typescript-eslint`, `tsx`) suit avec retard ;
   `pnpm install` ne doit pas remonter au-delà de `6.0.x` (`pnpm.overrides`).
2. **Express 5.** Le routage des chemins et la gestion des promesses changent ;
   la ligne 4 reste tant qu'aucun bench ne mesure la 5. Ce n'est pas un refus,
   c'est une tâche.
3. **`compression()` sur le flux SSE.** Le proxy tamponne, le client ne voit
   rien avant la fin : exclure `text/event-stream` du filtre.
4. **Un `Router` qui importe la composition.** Deux systèmes, deux vérités : la
   composition entre par `createApp(runService)`, jamais par un import.
5. **`pino-pretty` en production.** Les logs cessent d'être du JSON.

---

## 8. Smoke

```bash
cd workspace/src/{AppName}
pnpm install --frozen-lockfile
pnpm tsc --noEmit && pnpm eslint .
pnpm build && test -f dist/serving/http/server.js
node dist/serving/http/server.js & PID=$!; sleep 3
curl -sf http://localhost:8080/healthz -o /dev/null
curl -sf http://localhost:8080/openapi.json > /tmp/openapi.json
kill $PID
python .sdda/sdda.py validate-api-contract --mission {n} --json
```

Timeout : 120 s (installation comprise).
