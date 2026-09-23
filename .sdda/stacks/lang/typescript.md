# Stack: typescript (lang)

Stack ID: lang-typescript
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: langage et runtime de l'application agentic générée (outillage, structure de projet, conventions transverses) sur Node.js. Le framework agentic est hors périmètre → `framework/langgraph-js.md`. **Réserve à lire d'abord** : cette fiche existe, mais aucune combo de bootstrap ne l'active — `eval/pytest-eval.md` et `observability/otel-genai.md` sont `[python]`, et aucun générateur de squelette TypeScript n'existe (`gen_app_skeleton` est Python) : `dev-backend` écrit le squelette depuis cette fiche. Une combo TypeScript exige d'abord une fiche `eval/` et une fiche `observability/` dans ce langage.

---

## 1. Rôle et périmètre

Cette fiche fixe **le socle Node/TypeScript** sur lequel s'appuient les stacks
TypeScript de SDD_Agents (`framework/langgraph-js.md`, `serving/cli-node.md`,
`backend/node-express.md`, `backend/nestjs.md`). Elle décide :

- la version du runtime, du langage et du gestionnaire de paquets ;
- l'outillage déterministe (format, lint, typage, tests) exécuté en L0/L1 sans
  aucun token ;
- la **structure de projet agentic** — où vivent agents, outils, retrieval,
  orchestration, prompts chargés, bornes, tracing ;
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
| **Runtime** | Node.js **22 LTS** — `"engines": { "node": ">=22" }`, `.nvmrc` versionné |
| **Langage** | TypeScript **6.0.x** — plafonné sous 7 (§7) ; `"strict": true`, `"noUncheckedIndexedAccess": true`, `"exactOptionalPropertyTypes": true`, `"verbatimModuleSyntax": true` |
| **Modules** | ESM (`"type": "module"`, `"module": "NodeNext"`) ; imports internes par alias `#app/*` (`imports` de `package.json`) |
| **Gestionnaire** | `pnpm` — `pnpm-lock.yaml` versionné, `pnpm.overrides` pour plafonner `typescript` |
| **Build / dev** | `tsc` (build) · `tsx` (exécution directe en dev et en tests) |
| **Lint / format** | `eslint` + `typescript-eslint` (règles `strict-type-checked`) ; formatage par `prettier` — tous deux en L0 |
| **Tests** | `vitest` — L1 (fonctions pures, Domaine), L2 (contrats d'outils) |
| **Schémas** | `zod` 4 — la seule voie d'entrée d'une donnée non maîtrisée |
| **Logs** | `pino`, JSON sur stderr |
| **Racine** | `workspace/src/{AppName}/` — code sous `src/`, tests sous `tests/` |

> Les versions vivent dans le `.libs.json` du framework actif
> (`framework/langgraph-js.libs.json`) et de la fiche `backend/` active. Comme
> en Python, `typescript` seul n'installe rien.

### 2.1 Init (idempotent)

```bash
if [ ! -f "workspace/src/{AppName}/package.json" ]; then
  mkdir -p workspace/src/{AppName}/src workspace/src/{AppName}/tests
  cd workspace/src/{AppName}
  pnpm init
  # package.json : "type": "module", "engines": {"node": ">=22"}, "imports": {"#app/*": "./src/*"}
  # tsconfig.json : strict, NodeNext, noUncheckedIndexedAccess, exactOptionalPropertyTypes, verbatimModuleSyntax
fi
```

Les paquets s'ajoutent depuis le `.libs.json` actif, versions épinglées, jamais
`pnpm add x@latest`.

---

## 3. Mapping des concepts SDD_Agents → idiomes TypeScript

### 3.1 Chargement et hash des prompts

Un prompt est un fichier `workspace/src/{App}/prompts/{agent}.system.md`, chargé par
`loadPrompt(agent)` qui calcule son SHA-256 et le compare au hash épinglé dans
le contrat d'agent. Un littéral de prompt dans le code est interdit (P1) et
détectable au lint : toute chaîne de plus de 200 caractères passée à un
constructeur de message est signalée.

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

```
workspace/src/{AppName}/
├── package.json · pnpm-lock.yaml · tsconfig.json · .nvmrc · eslint.config.js
├── README.md · Dockerfile (si container)
├── src/
│   ├── app/                 # composition.ts · runService.ts · config.ts · models.ts (généré) · bounds.ts · domain/
│   ├── agents/{agent}/      # dev-agent
│   ├── orchestration/       # dev-orchestration
│   ├── tools/               # dev-tools
│   ├── retrieval/           # dev-retrieval
│   ├── data/                # dev-data (+ schemas/ figés)
│   └── serving/{cli,http}/  # dev-api
└── tests/                   # qa-tests (transverses) ; chaque couche a aussi ses tests à côté
```

La matrice d'ownership matche `workspace/src/**/{couche}/**` : la profondeur
`src/` de cet écosystème est couverte.

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
- `process.env` hors `src/app/config.ts` ;
- `require()` ; `eval()` ; `new Function()` ;
- prompt littéral dans le code ;
- `fetch` sans `AbortSignal.timeout` ;
- `TODO`, `FIXME` dans le code livré ;
- `typescript` remonté au-delà de `6.0.x` ; lockfiles concurrents.

### 5.3 Frontière de confiance dans le type system

Les schémas Zod des sorties d'outils sont **générés** depuis les contrats
(`gen-source-tools` pour les sources déclarées ; l'`inputSchema`/`outputSchema`
des contrats d'outils sinon). Une sortie non parsée n'entre pas dans un prompt.

---

## 6. Commande de smoke

```bash
cd workspace/src/{AppName}
pnpm install --frozen-lockfile
pnpm tsc --noEmit
pnpm eslint .
pnpm vitest run
```

Timeout : 120 s.

---

## 7. Pièges connus

1. **TypeScript 7.** Publié ; `typescript-eslint` et `tsx` suivent avec retard.
   Plafonner en `pnpm.overrides` — c'est le piège n°1 relevé par l'audit
   SDD_Pro du 2026-09-02 sur les stacks Node, il vaut ici aussi.
2. **Le type qui rassure.** `Untrusted<T>` n'existe plus à l'exécution ; sans
   `wrapUntrusted` au parsing, la marque est un commentaire.
3. **ESM et `__dirname`.** Absent en ESM : `import.meta.dirname` (Node 22).
4. **`exactOptionalPropertyTypes` et les schémas générés.** Un champ optionnel
   généré `x?: string` refuse `undefined` explicite : générer `x?: string | undefined`
   quand l'IR le permet, sinon le générateur produit du code qui ne compile pas.
5. **Aucun générateur de squelette.** Tant que `gen_app_skeleton` est Python,
   `config.ts`, `bounds.ts`, `models.ts` et la CLI sont écrits par `dev-backend`
   depuis cette fiche et `serving/cli-node.md`, et relus comme du code — pas
   régénérés. Le jour où un générateur TypeScript existe, il aura sa fiche.
