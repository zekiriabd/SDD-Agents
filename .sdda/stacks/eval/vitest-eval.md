# Stack: vitest-eval (eval)

> §2.3 (Librairies) suit `vitest-eval.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: eval-vitest-eval
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: tests **déterministes L0–L2** de l'application TypeScript dans `vitest` (fonctions pures, contrats d'outils, connectivité live), rapport **JUnit** que le framework parse pour la TOOL GATE, conventions de nommage vérifiables par grep (identifiant de cas, marquage `network`). Les **évaluations** L3–L9 (k runs, variance, verdict vert/jaune/rouge, épinglage, baseline, calibration) ne sont **pas** dans `vitest` : elles restent jouées par le runner du framework, `python .sdda/sdda.py eval-runner` (et `run-retrieval-eval`, `run-adversarial-suite`), qui lance l'application par sa CLI. Pendant TypeScript de `eval/pytest-eval.md` pour la part « test », et renvoi explicite vers le runner commun pour la part « eval ». Suppose `lang/typescript.md`.

---

## 1. Rôle et périmètre

**Un test assert, une eval score** (TESTING-AND-EVAL). En Python, `pytest-eval`
fait les deux dans le même processus parce que l'application, ses tests et le
runner d'eval parlent la même langue. En TypeScript ils ne la parlent pas : le
runner d'eval, ses graders, sa statistique, son verdict et son épinglage sont du
Python (`.sdda/python/`), et **ils doivent le rester**, pour trois raisons :

1. **Un seul protocole de verdict.** La table vert/jaune/rouge, le k minimal,
   le refus du rerun, le tuple d'épinglage à cinq champs et le holdout hors de
   portée du développeur sont écrits **une fois**. Un second runner en
   TypeScript serait une seconde implémentation qui dérive, et les gates n'en
   liraient qu'une.
2. **Ce qu'on mesure est ce qu'on livre.** Le runner lance l'application par
   `node …/dist/cli.js run --json` (§7) : il mesure l'exécutable construit, pas
   un module importé dans un test.
3. **Le langage de l'application ne doit pas changer la note.** Deux
   applications de la même MISSION, l'une Python, l'autre TypeScript, sont
   jugées par le même code sur le même jeu.

Il reste donc à `vitest` ce que `pytest` fait nativement pour Python : les
tests **pass/fail** des niveaux L0 à L2, écrits par `qa-tests` (et par les
`dev-*` pour les tests de leur couche), lancés par le framework en PHASE 3
(`run-tool-suites`, part G3) et par la CI.

**Hors périmètre** : le contenu des jeux (`qa-evals`), les graders (Python), les
suites YAML (`workspace/pipeline/suites/`, communes aux langages), la trace
(`observability/otel-genai-node.md`, que les graders `trajectory`, `cost`,
`latency` relisent).

---

## 2. Identité

### 2.1 Identité

- **Stack ID** : `eval-vitest-eval`
- **Runner de tests** : `vitest` 5.x (Node ≥ 22.12), pair `vite` épinglé
- **Runner d'eval** : `python .sdda/sdda.py eval-runner` — **commun à tous les langages**, inchangé
- **Commande du framework** : `npx --no-install vitest run --reporter=junit --outputFile=<fichier>` lancée **depuis la racine de l'application** (`workspace/src/{AppName}/`)
- **Fichiers de test** : `*.test.ts`, dans le dossier `tests/` **de la couche** qu'ils testent (`tools/tests/`, `retrieval/{index}/tests/`…) ; tests transverses sous `workspace/src/{AppName}/tests/`
- **Ownership** : `workspace/src/**/tests/**` → `qa-tests` (zone partagée avec les `dev-*`, par couche) ; `workspace/pipeline/{datasets,suites,baselines,calibration,fixtures}/**` → jamais un `dev-*`, inchangé

`--no-install` n'est pas un détail : sans lui, `npx` télécharge une version de
`vitest` absente du lockfile, et le rapport JUnit d'une gate proviendrait d'un
outil que personne n'a épinglé.

### 2.2 Configuration

```ts
// workspace/src/{AppName}/vitest.config.ts — écrit par dev-backend (racine du projet)
import { defineConfig } from "vitest/config";

export default defineConfig({
  // `#app/*` pointe dist/ à l'exécution (lang/typescript.md §4.1) ; les tests L0–L2 lisent les SOURCES.
  resolve: { alias: [{ find: /^#app\/(.*)\.js$/, replacement: `${import.meta.dirname}/$1.ts` }] },
  test: {
    include: ["**/tests/**/*.test.ts"],
    exclude: ["dist/**", "node_modules/**"],
    environment: "node",
    pool: "forks",                   // un processus par fichier : un test qui fuit un handle (pool pg, sous-processus MCP) ne contamine pas le suivant
    testTimeout: 10_000,
    retry: 0,                        // jamais de rerun : un test flaky est un défaut, pas du bruit
    tags: [{ name: "network", description: "exige une base, un serveur MCP ou un provider joignable" }],
    reporters: ["default"],          // le framework impose --reporter=junit en ligne de commande
  },
});
```

### 2.3 Librairies

Source de vérité : `vitest-eval.libs.json`.

**CORE** (dev) : `vitest`, `vite` (pair non optionnel de vitest 5), `zod`.
**ON-DEMAND** : `@vitest/coverage-v8` (`coverage`).
**Absents par conception** : un runner d'eval embarqué (`vitest-evals` ou
équivalent), `jest`, `msw` — raisons dans le catalogue.

---

## 3. Mapping des concepts SDD_Agents → idiomes vitest

| Concept | Idiome | Qui le lance |
|---|---|---|
| **L0** statique | `tsc --noEmit`, `eslint` (règles interdites de `lang/typescript.md` §5.2), tests « grep » (`*.test.ts` qui lisent le code source : pas de prompt inline, pas de `SET` hors transaction, pas de `client.embed` hors `embed.ts`) | CI, smoke |
| **L1** fonctions pures | `rrfFuse`, `queryPrep`, table `[CLASS] → code`, redaction, sérialisation canonique, schémas `RunEvent` | CI, smoke |
| **L2** contrat d'outil | un `it.each` par outil : happy + **chaque** erreur déclarée + timeout + auth KO (+ idempotence, rate limit si déclarés) | `run-tool-suites` (G3) via JUnit |
| **Connectivité live** | `describe("network …")` — vraie base, vrai serveur MCP | `run-tool-suites --live`, jamais le smoke |
| **L3–L9** evals | **pas de vitest** — `eval-runner`, `run-retrieval-eval`, `run-adversarial-suite` lancent `dist/cli.js` | PHASE 6–8 |

### 3.1 Un test de contrat, un cas par ligne

```ts
// workspace/src/{AppName}/tools/tests/orders-lookup.contract.test.ts
import { describe, it, expect } from "vitest";
import { ordersLookup } from "../orders-lookup.js";
import { fakeFetch } from "./fake-fetch.js";

// Les identifiants de cas sont CEUX DU CONTRAT (tool-contract, section des erreurs déclarées) :
// le framework relie une ligne JUnit à un cas déclaré par ce nom, pas par la position.
const cases = [
  ["happy-1", { status: 200, body: { id: "A-1" } }, { ok: true }],
  ["error-NOT_FOUND", { status: 404, body: {} }, { ok: false, code: "NOT_FOUND" }],
  ["timeout-1", { delayMs: 60_000 }, { ok: false, code: "TIMEOUT" }],
  ["auth-401", { status: 401, body: {} }, { ok: false, code: "AUTH_FAILED" }],
] as const;

describe("orders_lookup contract", () => {
  it.each(cases)("%s", async (_id, reply, expected) => {
    const out = await ordersLookup({ order_id: "A-1" }, testCtx({ fetch: fakeFetch(reply) }));
    expect(out).toMatchObject(expected);
  });
});

describe("network orders_lookup live", { tags: ["network"] }, () => {
  it("live-1", async () => { /* vrai endpoint de test, identité de test */ });
});
```

Dans le rapport JUnit, le `name` d'un `testcase` est le titre complet, ancêtres
compris, joint par ` > ` (défaut de vitest) : `orders_lookup contract > happy-1`,
`network orders_lookup live > live-1`. C'est ce nom que le framework lit.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── vitest.config.ts                       # §2.2 — racine, zone dev-backend
├── tools/tests/
│   ├── {tool}.contract.test.ts            # L2 — it.each(cases)("%s", …), un cas par erreur déclarée
│   ├── {tool}.live.test.ts                # describe("network …")
│   └── fake-fetch.ts                      # double de fetch injecté par le ToolContext
├── retrieval/{index}/tests/*.test.ts      # L1 fusion / query-prep ; network : parité SQL
├── data/tests/*.test.ts                   # L0 catalogue / SQL ; L2 enveloppe ; network : vues
├── serving/tests/*.test.ts                # L1 : codes de sortie, RunEvent, NDJSON seul sur stdout
├── app/tests/*.test.ts                    # L1 : config, bornes, tracing (redaction, forme JSONL)
└── tests/                                 # transverses (qa-tests)
    └── eval-contract.test.ts              # L1 : serving/cli.md §3.5 — stdin, isolement, retrieve (processus lancé sur dist/cli.js)

workspace/.sys/reports/                    # le JUnit écrit par le framework (--outputFile) atterrit ici — jamais dans src/
```

`eval-contract.test.ts` est le pendant de `test_skeleton_eval_contract.py` du
squelette Python : il **lance** `node dist/cli.js` comme le fera le runner, et
vérifie les trois points de `serving/cli.md` §3.5 sans token (RunService
remplacé par un double via l'isolement et des fixtures de test).

---

## 5. Conventions imposées

### 5.1 Nommage — vérifiable par grep

1. **Fichiers `*.test.ts`**, sous un dossier `tests/`. Un test hors de ce motif
   n'est pas collecté par la configuration §2.2 — donc n'existe pas pour G3.
2. **Chaque test de contrat paramétré porte l'identifiant du cas dans son
   nom** : `it.each(cases)("%s", …)` avec l'identifiant en première colonne
   (`happy-1`, `error-{CODE}`, `timeout-1`, `auth-401`). Grep L0 :
   `it\.each\([^)]*\)\(\s*["'\`]%s`. Un nom libre (« should work ») rend la
   ligne JUnit impossible à rattacher à un cas déclaré, et la gate compte le
   cas comme **non couvert**.
3. **Tout test qui exige le réseau est dans un bloc dont le nom commence par
   `network`** : `describe("network …", { tags: ["network"] }, …)`. Le **nom**
   est la convention que lit le framework (il apparaît dans le `name` JUnit) ;
   le **tag** est le confort local (`--tags-filter="!network"`), et il doit être
   déclaré dans `vitest.config.ts` sous peine d'erreur de vitest. Grep L0 :
   tout fichier qui ouvre une connexion (`new pg.Pool`, `StdioClientTransport`,
   `StreamableHTTPClientTransport`, `fetch(` hors double) doit contenir
   `describe("network` — sinon `[TEST_NETWORK_UNMARKED]`, avertissement.
4. **Un `it` par cas** : jamais deux assertions de cas différents dans le même
   `it` — la première qui échoue masque l'autre.

### 5.2 Règles

5. **LLM toujours mocké en L0–L2.** Aucun test `vitest` ne coûte un token :
   un modèle réel appartient aux evals, donc au runner du framework.
6. **`retry: 0`**, jamais `test.retry` ni `--retry` : relancer un test instable
   le rend vert sans l'avoir corrigé.
7. **Pas de `.only` ni de `.skip` livrés** (grep L0) ; un test désactivé porte un
   `.todo` avec la raison.
8. **Les doubles sont injectés, pas interceptés** : `fetch`, `pg.Pool`, `Client`
   MCP arrivent par le `ToolContext` ou la composition. Un intercepteur global
   laisserait passer un appel réseau réel qu'on a oublié de brancher.
9. **Le JUnit n'est jamais écrit sous `src/`** : `--outputFile` pointe
   `workspace/.sys/reports/`. Un rapport de gate dans la zone d'un `dev-*` est
   un rapport qu'un `dev-*` peut réécrire.

---

## 6. Commande de smoke

Déterministe, 0 token, sans réseau :

```bash
cd workspace/src/{AppName}
pnpm install --frozen-lockfile
pnpm tsc --noEmit && pnpm eslint .
npx --no-install vitest run --tags-filter="!network"
pnpm build && npx --no-install vitest run tests/eval-contract.test.ts    # le contrat §3.5 sur dist/cli.js
# ce que le framework lance pour G3 (depuis la racine de l'application) :
npx --no-install vitest run --reporter=junit --outputFile=../../.sys/reports/{n}-tools.junit.xml
```

Smoke Timeout : 120 s. Les evals ne sont pas dans le smoke : elles coûtent des
tokens et appartiennent aux PHASES 6–8.

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

C'est **ce contrat**, et non `vitest`, qui rend les evals possibles en
TypeScript : `eval-runner` (L4 isolée par `SDDA_EVAL_ISOLATION`, L5–L7 de bout
en bout), `run-retrieval-eval` (G4, par `retrieve`), `run-adversarial-suite`
(G7) lancent l'application et lisent le NDJSON, les codes de sortie et la trace
JSONL. La commande est choisie par `--executor cli` (dérivée du langage actif :
`node workspace/src/{AppName}/dist/cli.js`) ou imposée par
`--executor cmd:<commande>`. Le build doit donc produire **exactement**
`dist/cli.js` (`lang/typescript.md` §4) ; le `bin` du package (`pnpm exec
{AppName}`) en est un alias pour l'humain.

---

## 8. Pièges connus

1. **`npx vitest` sans `--no-install`** (§2.1) : un vitest non épinglé.
2. **Nom de cas généré** : `it.each(cases)("case %#", …)` rend `case 0`, `case 1` —
   l'identifiant du contrat disparaît du JUnit. Toujours `%s` sur la colonne
   identifiant.
3. **Tag déclaré à l'usage mais pas dans la config** : vitest refuse le tag ; le
   test n'est pas « marqué » en silence, il échoue — mieux, mais à savoir.
4. **`pool: "threads"` avec des modules natifs ou des handles** : un pool `pg`
   non fermé garde le worker vivant et `vitest run` ne se termine pas. `forks`
   et fermeture des pools en `afterAll`.
5. **Tester `src` au lieu de `dist`** pour le contrat §3.5 : un chemin
   d'import `#app/*` qui marche sous vitest (résolu par vite) peut casser sous
   `node dist/cli.js` (résolu par Node). Le test du contrat lance le **build**.
6. **Fake timers et `AbortSignal.timeout`** : `vi.useFakeTimers()` ne fait pas
   avancer les timers internes de Node utilisés par `AbortSignal.timeout` de la
   même façon selon la version ; les cas `timeout-*` utilisent un délai réel
   court (`timeoutS` de test), pas des timers simulés. **Non vérifié** sur
   Node 22 × vitest 5 : à confirmer au premier run.
