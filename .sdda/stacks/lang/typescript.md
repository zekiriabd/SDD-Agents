# Stack: typescript (lang)

Stack ID: lang-typescript
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: langage et runtime de l'application agentic générée (outillage, structure de projet, conventions transverses, **point d'entrée exécutable**) sur Node.js. Le framework agentic est hors périmètre → `framework/langgraph-js.md`. Les fiches du cœur C1 existent en TypeScript : `eval/vitest-eval.md` (tests L0–L2), `observability/otel-genai-node.md`, `rag/hybrid-node.md`, `vectorstore/pgvector-node.md`, `dataaccess/view-per-agent-node.md`, `tools/mcp-node.md`, `serving/cli-node.md`. **Réserve à lire d'abord** : aucun générateur de squelette TypeScript n'existe (`gen_app_skeleton` est Python) — `dev-backend` écrit le squelette depuis cette fiche — et aucune combinaison TypeScript n'a été exécutée.

---

## 1. Rôle et périmètre

Cette fiche fixe **le socle Node/TypeScript** sur lequel s'appuient les stacks
TypeScript de SDD_Agents. Elle décide :

- la version du runtime, du langage et du gestionnaire de paquets ;
- l'outillage déterministe (format, lint, typage, tests) exécuté en L0/L1 sans
  aucun token ;
- la **structure de projet agentic** — où vivent agents, outils, retrieval,
  orchestration, prompts chargés, bornes, tracing — alignée **répertoire pour
  répertoire** sur la matrice d'ownership ;
- le **point d'entrée** que lancent les runners du framework (`dist/cli.js`) ;
- les conventions qui rendent les principes du framework **vérifiables par un
  analyseur** : aucun prompt inline (P1), aucun secret lu par `process.env`
  hors de la config, toute borne typée (P12).

Elle **ne décide pas** : le framework d'agents, le pattern d'orchestration, la
surface, le framework HTTP.

> **Ce qui change par rapport à Python.** Le typage TypeScript est effacé à
> l'exécution : une frontière de confiance déclarée en type (`Untrusted<string>`)
> ne protège rien si le parsing d'entrée ne la pose pas. D'où Zod **au bord**,
> systématiquement : le type est la conséquence du schéma, jamais l'inverse.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `lang-typescript` |
| **Runtime** | Node.js **22 LTS**, ≥ **22.12** (plancher de `vitest` 5) — `"engines": { "node": ">=22.12" }`, `.nvmrc` versionné |
| **Langage** | TypeScript **6.0.x** — plafonné sous 7 (§7) ; `"strict": true`, `"noUncheckedIndexedAccess": true`, `"exactOptionalPropertyTypes": true`, `"verbatimModuleSyntax": true` |
| **Modules** | ESM (`"type": "module"`, `"module": "NodeNext"`) ; imports internes par alias `#app/*` → `./dist/*` à l'exécution (`imports` de `package.json`, §4.1) |
| **Gestionnaire** | `pnpm` — `pnpm-lock.yaml` versionné, `pnpm.overrides` pour plafonner `typescript` |
| **Build / dev** | `tsc -p tsconfig.build.json` (build vers `dist/`) · `tsx` (exécution directe en dev) |
| **Lint / format** | `eslint` + `typescript-eslint` (règles `strict-type-checked`) ; formatage par `prettier` — tous deux en L0 |
| **Tests** | `vitest` — L0/L1/L2 (`eval/vitest-eval.md`) ; les **evals** L3–L9 sont jouées par le runner du framework |
| **Schémas** | `zod` 4 — la seule voie d'entrée d'une donnée non maîtrisée |
| **Logs** | `pino`, JSON sur stderr |
| **Racine** | `workspace/src/{AppName}/` — **pas de sous-dossier `src/`** : les couches sont à la racine (§4) |

> Les versions vivent dans le `.libs.json` du framework actif
> (`framework/langgraph-js.libs.json`) et des fiches actives
> (`eval/vitest-eval.libs.json`, `observability/otel-genai-node.libs.json`,
> `vectorstore/pgvector-node.libs.json`, fiche `backend/`). Comme en Python,
> `typescript` seul n'installe rien.

### 2.1 Init (idempotent)

```bash
if [ ! -f "workspace/src/{AppName}/package.json" ]; then
  mkdir -p workspace/src/{AppName}
  cd workspace/src/{AppName}
  pnpm init
  # package.json : "type": "module", "engines": {"node": ">=22.12"}, "bin", "imports", "scripts" — cf. §4.1
  # tsconfig.json (typage, tests compris) + tsconfig.build.json (build, tests exclus) — cf. §4.1
fi
```

Les paquets s'ajoutent depuis les `.libs.json` actifs, versions épinglées,
jamais `pnpm add x@latest`.

---

## 3. Mapping des concepts SDD_Agents → idiomes TypeScript

### 3.1 Chargement et hash des prompts

Un prompt est un fichier `workspace/src/{App}/prompts/{agent}.system.md`, chargé par
`loadPrompt(agent)` qui calcule son SHA-256 et le compare au hash épinglé dans
le contrat d'agent. Un littéral de prompt dans le code est interdit (P1) et
détectable au lint : toute chaîne de plus de 200 caractères passée à un
constructeur de message est signalée. Les prompts sont lus **depuis
`prompts/`** (racine de l'application), jamais copiés dans `dist/` : l'actif
hashé est un seul fichier.

### 3.2 Bornes

```ts
export const BoundsSchema = z.object({
  maxIterations: z.number().int().positive(),
  maxToolCalls: z.number().int().positive(),
  maxDelegationDepth: z.number().int().nonnegative(),
  timeoutMs: z.number().int().positive(),
  budgetUsd: z.number().positive(),
  onBoundExceeded: z.enum(["fail-explicit", "degrade", "escalate-human"]),
});
export type Bounds = z.infer<typeof BoundsSchema>;
```

Les bornes viennent de `app_config.json` (générées depuis l'IR), jamais d'une
constante dans un agent. Une boucle sans `Bounds` en paramètre est une boucle
non bornée (P12).

### 3.3 Frontière de confiance

```ts
export type Untrusted<T> = { readonly __untrusted: true; readonly value: T };
export const wrapUntrusted = <T>(value: T): Untrusted<T> => ({ __untrusted: true, value });
```

Toute sortie d'outil dont le contrat dit `trust: untrusted` et tout champ
`free_text` d'une source déclarée passe par `wrapUntrusted` **au moment du
parsing** ; le prompt les reçoit balisés. Le type seul ne suffit pas (effacé à
l'exécution) : c'est le parsing qui pose la marque.

---

## 4. Structure de fichiers générée

La matrice d'ownership (`.sdda/loader.yml`) attribue les zones par **répertoire
de couche en minuscules directement sous `workspace/src/{AppName}/`**. Le
projet TypeScript suit ce découpage **sans sous-dossier `src/`** : un
`src/tools/` serait couvert par le motif `workspace/src/**/tools/**`, mais un
fichier `src/x.ts` tomberait dans la zone racine de `dev-backend` sans
appartenir à la coquille — l'ambiguïté est évitée à la source.

```
workspace/src/{AppName}/
├── package.json · pnpm-lock.yaml · tsconfig.json · tsconfig.build.json    # dev-backend (racine)
├── .nvmrc · eslint.config.js · vitest.config.ts · README.md · Dockerfile (si container)
├── cli.ts                  # point d'entrée : une ligne, `import "./serving/cli/main.js";` → dist/cli.js (§4.1)
├── app/                    # dev-backend : composition.ts · run-service.ts · config.ts · models.ts · bounds.ts · domain/ · tracing/
├── shared/                 # dev-orchestration (pré-passe 4.0) : types de handoff, gelés
├── agents/{agent}/         # dev-agent, une instance par agent
├── orchestration/          # dev-orchestration : graphe LangGraph.js compilé depuis l'IR
├── memory/                 # dev-orchestration : interface (pré-passe) puis implémentation
├── tools/                  # dev-tools (tools/mcp/ compris)
├── retrieval/              # dev-retrieval (retrieval/migrations/, retrieval/{index}/)
├── data/                   # dev-data (data/migrations/, data/tools/, schemas/ figés)
├── serving/                # dev-api : serving/cli/main.ts, serving/http/ si backend-api
├── prompts/ · skills/ · rules/                                            # dev-prompt — lus, jamais écrits par le code
├── tests/                  # qa-tests : tests transverses (contrat d'exécution §8)
├── {couche}/tests/*.test.ts                                               # tests de couche, à côté de ce qu'ils testent
└── dist/                   # SORTIE de build, jamais versionnée ni éditée — dist/cli.js est le point d'entrée
```

### 4.1 Point d'entrée et build

Le point d'entrée est **exactement** `workspace/src/{AppName}/dist/cli.js`. Ce
n'est pas une préférence : `--executor cli` des runners du framework dérive la
commande du langage actif, et pour TypeScript cette commande est
`node workspace/src/{AppName}/dist/cli.js` (`serving/cli.md` §3.5). Un build
qui produit `dist/serving/cli/main.js` seulement est une application que le
framework ne sait pas lancer.

```jsonc
// package.json (extrait)
{
  "type": "module",
  "engines": { "node": ">=22.12" },
  "bin": { "{AppName}": "./dist/cli.js" },
  "imports": { "#app/*": "./dist/*" },
  "scripts": {
    "build": "tsc -p tsconfig.build.json && node ./copy-assets.mjs",   // racine (zone dev-backend) : copie **/sql/*.sql et app_config.json dans dist/
    "test": "vitest run",
    "lint": "eslint . && tsc --noEmit"
  },
  "pnpm": { "overrides": { "typescript": "6.0.3" } }
}
```

```jsonc
// tsconfig.build.json (extrait)
{
  "extends": "./tsconfig.json",
  "compilerOptions": { "rootDir": ".", "outDir": "dist", "noEmit": false },
  "include": ["cli.ts", "serve.ts", "app", "shared", "agents", "orchestration", "memory", "tools", "retrieval", "data", "serving"],
  "exclude": ["**/tests/**", "dist", "node_modules"]
}
```

`rootDir: "."` fait de `cli.ts` → `dist/cli.js` et de `serving/cli/main.ts` →
`dist/serving/cli/main.js`. `serving/cli-node.md` décrit la CLI elle-même ;
`cli.ts` à la racine n'en est qu'un alias d'une ligne, écrit par `dev-backend`
(zone racine), pour que le point d'entrée ne dépende pas de l'arborescence
interne de `serving/`. `#app/*` pointe `dist/` : c'est le chemin qui existe à
l'exécution. `tsc` ne copie ni les `.sql` ni les `.json` de configuration : le
script `copy-assets.mjs` le fait, et le smoke échoue si l'un manque.

---

## 5. Conventions imposées

### 5.1 Style

- `camelCase` pour variables et fonctions, `PascalCase` pour types et classes,
  `SCREAMING_SNAKE_CASE` pour les constantes ; fichiers en `kebab-case.ts` ;
- un export principal par fichier ; barrels (`index.ts`) seulement à la racine
  d'une couche ;
- `readonly` par défaut ; `interface` pour les ports, `type` pour les unions ;
- `async/await` ; jamais de promesse non attendue (`@typescript-eslint/no-floating-promises`).

### 5.2 Interdits — vérifiés en L0

- `any`, `as unknown as T`, `// @ts-ignore` / `// @ts-expect-error` sans
  justification écrite ;
- `console.*` ;
- `process.env` hors `app/config.ts` ;
- `require()` ; `eval()` ; `new Function()` ;
- prompt littéral dans le code ;
- `fetch` sans `AbortSignal.timeout` ;
- `import … from "@opentelemetry/…"` hors `app/tracing/` ;
- `TODO`, `FIXME` dans le code livré ;
- `typescript` remonté au-delà de `6.0.x` ; lockfiles concurrents.

### 5.3 Frontière de confiance dans le type system

Les schémas Zod des sorties d'outils sont **générés** depuis les contrats
(l'`inputSchema`/`outputSchema` des contrats d'outils). Une sortie non parsée
n'entre pas dans un prompt.

### 5.4 Tests (Testing)

Détail et conventions : `eval/vitest-eval.md`. Ce que tout projet TypeScript
respecte, parce que le framework le lit :

- **Commande** : le framework lance, **depuis la racine de l'application**,
  `npx --no-install vitest run --reporter=junit --outputFile=<fichier>`. Le
  rapport **JUnit** atterrit sous `workspace/.sys/reports/` (jamais sous
  `src/`) et c'est lui que parse `run-tool-suites` pour la TOOL GATE (G3).
  `--no-install` interdit à `npx` de télécharger un vitest hors lockfile.
- **Fichiers** : `*.test.ts`, dans le dossier `tests/` de la couche testée
  (`tools/tests/`, `retrieval/{index}/tests/`…), transverses sous `tests/`.
- **Identifiant de cas** : chaque test de contrat paramétré porte l'identifiant
  du cas dans son **nom affiché** — `it.each(cases)("%s", …)` avec l'identifiant
  (`happy-1`, `error-{CODE}`, `timeout-1`, `auth-401`) en première colonne. Le
  `name` JUnit (`describe > test`) le contient ; sans lui, la gate ne rattache
  pas la ligne au cas déclaré et le compte comme non couvert.
- **Réseau** : tout test qui exige une base, un serveur MCP ou un provider est
  dans un bloc `describe("network …")` — le **nom contient `network`** (tag
  `network` en plus, déclaré dans `vitest.config.ts`, pour le filtre local
  `--tags-filter="!network"`).
- **Evals** : pas dans vitest. `python .sdda/sdda.py eval-runner` (L4–L8),
  `run-retrieval-eval` (G4) et `run-adversarial-suite` (G7) lancent
  `dist/cli.js` (§8).

---

## 6. Commande de smoke

```bash
cd workspace/src/{AppName}
pnpm install --frozen-lockfile
pnpm tsc --noEmit
pnpm eslint .
npx --no-install vitest run --tags-filter="!network"
pnpm build
node dist/cli.js --help && node dist/cli.js version --json      # le point d'entrée exact existe et répond
```

Timeout : 120 s.

---

## 7. Pièges connus

1. **TypeScript 7.** Publié (7.0.2) ; `typescript-eslint` 8.70.1 déclare
   `typescript >=4.8.4 <6.1.0` et `tsx` suit avec retard. Plafonner en
   `pnpm.overrides` — c'est le piège n°1 relevé par l'audit SDD_Pro du
   2026-09-02 sur les stacks Node, il vaut ici aussi.
2. **Le type qui rassure.** `Untrusted<T>` n'existe plus à l'exécution ; sans
   `wrapUntrusted` au parsing, la marque est un commentaire.
3. **ESM et `__dirname`.** Absent en ESM : `import.meta.dirname` (Node 22).
4. **`exactOptionalPropertyTypes` et les schémas générés.** Un champ optionnel
   généré `x?: string` refuse `undefined` explicite : générer `x?: string | undefined`
   quand l'IR le permet, sinon le générateur produit du code qui ne compile pas.
5. **Aucun générateur de squelette.** Tant que `gen_app_skeleton` est Python,
   `config.ts`, `bounds.ts`, `models.ts`, `cli.ts` et la CLI sont écrits par
   `dev-backend` (et `dev-api` pour `serving/`) depuis cette fiche et
   `serving/cli-node.md`, et relus comme du code — pas régénérés.
6. **Point d'entrée déplacé.** `serving/cli-node.md` décrit `dist/serving/cli/main.js`
   comme `bin` ; le `bin` et le contrat du framework sont `dist/cli.js`
   (§4.1). Les deux coexistent, l'alias racine fait foi.
7. **`src/` par habitude.** Un `workspace/src/{AppName}/src/` casse la lecture
   par couche de la matrice d'ownership et le `rootDir` du build (§4).
8. **Alias `#app/*` vers les sources.** Pointer `#app/*` sur `./*.ts` fait
   marcher vitest (vite résout) et casse `node dist/cli.js` ; l'alias pointe
   `dist/`, et `vitest.config.ts` le remappe vers les sources
   (`resolve.alias`, `eval/vitest-eval.md` §2.2) pour que les tests L1 n'exigent
   pas un build.

---

## 8. Contrat d'exécution

L'application Node implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** — mêmes commandes (`run`, `resume`, `health`, `inspect`, `trace`,
`version`, plus `retrieve`), même flux NDJSON `RunEvent` sur stdout, mêmes
codes de sortie — et les trois points de `serving/cli.md` §3.5 :

1. `run --json --input-file -` lit l'entrée sur stdin ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'app sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil) et le retrieval
   figé depuis le même dossier, sans aucun appel réseau d'outil ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished` ;
   `--k` absent → `topK` du contrat de retrieval.

**Commande de lancement** : `node workspace/src/{AppName}/dist/cli.js` après
`pnpm build` — ou le `bin` du package (`pnpm exec {AppName}` depuis la racine
de l'application). Les runners du framework (`eval-runner`,
`run-retrieval-eval`, `run-adversarial-suite`) la choisissent par
`--executor cli` (commande dérivée du langage actif) ou l'imposent par
`--executor cmd:<commande>`.

Les fixtures d'isolement suivent **le format du squelette Python**, repris à
l'identique : `SDDA_EVAL_FIXTURES/tools/{outil}.jsonl` (lignes `{"tool", "args",
"result"}` ou `{"tool", "args", "error": {"code", "message"}}`, réponse par
défaut sans `args`, ligne sans clé structurée = la réponse elle-même ; appel non
couvert → erreur `TOOL_FIXTURE_MISSING`) et
`SDDA_EVAL_FIXTURES/retrieval/{index}.jsonl` (`{"query"}` ou
`{"query_hash": "sha256:…"}`, `results: [{id, score}]` ou `result_ids[]` +
`scores[]`). Un outil de l'agent sans fixture fait refuser le démarrage (code 8,
`[CONFIG_INVALID]`).

Le test transverse `tests/eval-contract.test.ts` (`eval/vitest-eval.md` §4)
vérifie ces trois points en lançant le build, sans token.
