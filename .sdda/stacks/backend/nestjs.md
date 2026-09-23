# Stack: nestjs (backend)

> §5 (Librairies) suit `nestjs.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: backend-nestjs
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: la **maison HTTP** en NestJS 12 quand `DeliverableType: backend-api` et `ApiFramework: nestjs` — modules, conteneur d'injection, pipes, guards, filtres, `@nestjs/config`, `nestjs-pino`, packaging. Hérité de SDD_Pro `backend/nestjs.md`, transposé : TypeORM et Passport retirés (pas de base possédée, l'identité vient d'un guard qui lit le transport), le contrat OpenAPI est **dérivé de l'IR** et confronté à celui que Nest génère depuis les décorateurs. Suppose `lang/typescript.md`. Alternative à `backend/node-express.md` : choisir Nest quand la structure imposée vaut son coût de démarrage.

---

## 1. Rôle et périmètre

NestJS **impose** ce qu'Express laisse ouvert : modules, injection par
constructeur, cycle de vie, pipes de validation, guards, filtres d'exception.
C'est un avantage pour la coquille d'un système agentic — la composition a un
endroit naturel (le module racine), l'identité a un endroit naturel (un guard),
les erreurs ont un endroit naturel (un filtre). C'est aussi un coût : un temps
de démarrage plus long, des décorateurs et de la réflexion, une abstraction de
plus entre la requête et le `RunService`.

Ce que la fiche garde de SDD_Pro : `ValidationPipe({ whitelist: true,
forbidNonWhitelisted: true })`, `helmet` dans `main.ts`, `ConfigService` seul
lecteur de l'environnement, `nestjs-pino`, `Test.createTestingModule` pour les
tests. Ce qui est retiré : TypeORM, Passport-local, Argon2 (aucun compte
utilisateur local : l'identité est établie par un fournisseur externe ou une
passerelle), `@nestjs/swagger` comme **source** du contrat — il reste utile pour
**publier** le document, mais le contrat de référence est celui de l'IR.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `backend-nestjs` |
| **Langage** | TypeScript 6.0.x — plafonné sous 7 (`lang/typescript.md` §7) |
| **Runtime** | Node.js 22 LTS |
| **Framework** | NestJS 12.x sur adaptateur Express (Fastify via capability `fastify-adapter`) |
| **Build** | `pnpm` · `nest build` · `node dist/main.js` |
| **Validation** | Zod via `nestjs-zod` sur les schémas **générés** depuis l'IR — `class-validator` reste possible mais double la source du schéma |
| **Config** | `@nestjs/config` avec `validate` Zod au démarrage ; `.env` en dev seulement |
| **Logs** | `nestjs-pino`, JSON |
| **Paramètres STACK.md** | `DeliverableType: backend-api`, `ApiFramework: nestjs`, `ApiAuthMode`, `ApiContractFirst`, `## Active Architecture Pattern` |
| **Smoke** | §8 |

---

## 3. Mapping couche → répertoire

Racine `workspace/src/{AppName}/src/` :

| Couche | Emplacement |
|---|---|
| Bootstrap | `main.ts` — `NestFactory.create`, `helmet`, `ValidationPipe`, filtres globaux, écoute |
| Module racine | `app.module.ts` — importe `ConfigModule`, `LoggerModule`, `RunsModule`, `HealthModule` |
| Entrée HTTP | `serving/http/runs/runs.controller.ts` (HTTP seulement), `runs.module.ts`, `serving/http/health/`, `serving/http/openapi.ts` |
| Service | `app/run.service.ts` (`@Injectable() RunService`) · `app/usecases/` |
| Domaine | `app/domain/{contexte}/` — sans décorateur : le Domaine ne connaît pas Nest |
| Composition | `app/composition.module.ts` — **le** module qui fournit agents, outils, graphe, `RunService` |
| Config | `app/config/configuration.ts`, `app/config/validation.ts` (Zod) · `app/app_config.json` |
| Modèle | `app/models.ts` (généré) · `data/schemas/` |
| Transverse | `common/filters/problem-details.filter.ts`, `common/guards/identity.guard.ts`, `common/interceptors/correlation.interceptor.ts` |
| Tests | `test/` (e2e, `supertest`) · `*.spec.ts` à côté des sources |
| Projet | `package.json` · `nest-cli.json` · `tsconfig.json` · `README.md` · `Dockerfile` |

Routes : identiques à `backend/node-express.md` §3 — `POST /v1/runs`,
`GET /v1/runs/{id}`, `POST /v1/runs/{id}/resume`, `/healthz`, `/readyz`,
`/openapi.json`.

---

## 4. Idiomes imposés

- **Un module de composition**, importé par `AppModule`, qui `provide` tout ce
  que le moteur expose : le contrôleur ne connaît que `RunService`.
- Injection par constructeur uniquement ; `@Injectable()` sur chaque
  fournisseur ; aucun `new` d'un service ; imports circulaires refusés
  (`forwardRef` est un symptôme, pas une solution).
- `ValidationPipe({ whitelist: true, forbidNonWhitelisted: true, transform: true })`
  global : un champ inconnu est une `400`, pas une propriété ignorée.
- Identité : `IdentityGuard` global lit le jeton/en-tête et pose
  `request.identity` ; un DTO qui porte `tenant_id` est refusé au lint.
- Erreurs : un `ExceptionFilter` global qui rend `ProblemDetails` depuis la
  table `[CLASS] → statut` ; aucune exception brute au client.
- SSE : `@Sse()` ou `res.write` explicite avec keepalive ; `request.on("close")`
  → annulation du run.
- Config : `ConfigService.getOrThrow()` typé depuis `configuration.ts` ; jamais
  `process.env` dans un service.
- `tsconfig` : `emitDecoratorMetadata` et `experimentalDecorators` **conservés**
  (Nest en dépend), `strict: true`, `strictNullChecks` jamais désactivé.

---

## 5. Librairies

Source de vérité : `nestjs.libs.json`.

**CORE** : `@nestjs/core`, `@nestjs/common`, `@nestjs/platform-express`,
`@nestjs/config`, `@nestjs/swagger` (publication), `reflect-metadata`, `rxjs`,
`zod`, `nestjs-zod`, `helmet`, `pino`, `nestjs-pino`, `pino-http`,
`typescript`, `@nestjs/cli`, `@nestjs/schematics`, `@nestjs/testing`, `jest`,
`ts-jest`, `@types/node`, `@types/express`, `@types/jest`, `eslint`,
`typescript-eslint`, `prettier`.

**ON-DEMAND** : `@nestjs/jwt` (`auth-jwt`), `@nestjs/terminus`
(`healthcheck` — si l'on veut les indicateurs prêts à l'emploi plutôt qu'un
contrôleur maison), `@nestjs/throttler` (`rate-limit`), `@nestjs/platform-fastify`
(`fastify-adapter`), `supertest` + `@types/supertest` (`e2e-http`),
`@opentelemetry/sdk-node` + `@opentelemetry/api` (`otel-http`).

**Absents par conception** : `typeorm`, `@nestjs/typeorm`, `prisma`,
`passport`, `passport-local`, `argon2`, `class-validator` en doublon de Zod.

---

## 6. Interdits

- `synchronize`, migrations, entité : il n'y a pas de base possédée ;
- logique métier dans un contrôleur ; appel de modèle hors `agents/` ;
- `new SomeService()` ; `@Injectable()` oublié ; import circulaire ;
- `process.env` dans un service ; secret en dur ;
- `ValidationPipe` sans `whitelist` ; DTO de sortie qui expose un champ interne ;
- `app.enableCors({ origin: true })` ; `helmet` absent de `main.ts` ;
- stack trace au client (filtre absent) ; `console.log` ;
- `any`, `!` sur une valeur non prouvée, `strictNullChecks: false` ;
- `emitDecoratorMetadata` retiré ; TypeScript remonté au-delà de `6.0.x` ;
- `dist/`, `node_modules/`, `.env` commités ; `npm` et `pnpm` mélangés.

---

## 7. Pièges connus

1. **Deux contrats.** `@nestjs/swagger` génère un document depuis les
   décorateurs ; l'IR en fournit un autre. Le second fait foi : le premier est
   **confronté** par `validate-api-contract`, pas publié tel quel.
2. **Le Domaine décoré.** Un `@Injectable()` sur une règle métier la lie à Nest ;
   elle ne se teste plus sans conteneur. Le Domaine reste du TypeScript nu.
3. **Temps de démarrage.** Un `/readyz` qui répond avant que la composition ait
   fini fait router du trafic vers un service pas prêt : l'indicateur de
   disponibilité attend `onApplicationBootstrap`.
4. **`forbidNonWhitelisted` et les événements SSE.** Il s'applique aux entrées,
   pas aux sorties ; ne pas le confondre avec la validation de `RunResponse`,
   qui est un contrôle de sortie explicite.

---

## 8. Smoke

```bash
cd workspace/src/{AppName}
pnpm install --frozen-lockfile
pnpm tsc --noEmit && pnpm eslint . && pnpm build
node dist/main.js & PID=$!; sleep 5
curl -sf http://localhost:8080/healthz -o /dev/null
curl -sf http://localhost:8080/openapi.json > /tmp/openapi.json
kill $PID
python .sdda/sdda.py validate-api-contract --mission {n} --json
```

Timeout : 240 s (installation + type-check + build).
