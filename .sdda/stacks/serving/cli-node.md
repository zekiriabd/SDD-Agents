# Stack: cli-node (serving)

Stack ID: serving-cli-node
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: surface d'exposition **ligne de commande Node** de l'application agentic — commandes, entrée/sortie, streaming, mode machine (`--json` NDJSON), reprise, **codes de sortie stables** mappés sur la taxonomie `[CLASS]`. C'est la surface du livrable `cli-exe` sur `lang/typescript.md`, et le **défaut** pour un projet TypeScript. Le contrat d'événements et le mapping `[CLASS] → code` sont **identiques** à `serving/cli.md` §3.2-3.3 : la même application vue depuis un autre écosystème. `commander` pour l'analyse des arguments ; pas de `.libs.json` propre (ajouté au catalogue du framework actif, capability `serving-cli`).

---

## 1. Rôle et périmètre

Le pendant Node de `serving/cli.md`. Il existe pour la même raison que
`cli-dotnet.md` : sans lui, `DeliverableType: cli-exe` — le défaut du framework
— serait inatteignable en TypeScript, et `validate_packaging` le dirait.

Les trois raisons de la CLI valent mot pour mot (`serving/cli.md` §1) : elle
est déterministe à câbler, elle est la surface des evals, elle rend les bornes
visibles. Et la règle qui gouverne toute cette fiche :

> **Le contrat ne se réécrit pas, il se réutilise.** `RunEvent` et la table
> `[CLASS] → code` appartiennent au produit, pas à l'écosystème. Le runner
> d'eval ne doit pas connaître le langage de l'application qu'il mesure.

Hors périmètre : la maison HTTP (`backend/node-express.md`, `backend/nestjs.md`),
qui réutilise le même `RunService`.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `serving-cli-node` |
| **Langage** | TypeScript 6.0.x, Node 22 (`lang/typescript.md`) |
| **Librairies** | `commander` 15.x — ajouté au `.libs.json` du framework actif, capability `serving-cli` |
| **Point d'entrée** | `"bin": { "{AppName}": "./dist/serving/cli/main.js" }` → `pnpm exec {AppName} …` ou `node dist/serving/cli/main.js` |
| **Paramètres STACK.md** | `StreamingEnabled`, `HumanInTheLoopEnabled` (active `resume`), `TraceLevel` |
| **Contrat machine** | `--json` : une ligne NDJSON par événement, schéma `RunEvent` (Zod, `event_schema: "1"`) |
| **Codes de sortie** | table `serving/cli.md` §3.3 — stables, dans `--help`, testés en L1 |

---

## 3. Commandes

| Commande | Rôle | Coûte des tokens |
|---|---|:-:|
| `{AppName} run [--json] [--tenant ID] [--as-of DATE] [--input FILE | -]` | une exécution ; `stdin` si `-` | oui |
| `{AppName} resume RUN_ID [--json]` | reprise depuis un checkpoint (si `HumanInTheLoopEnabled`) | oui |
| `{AppName} health [--json]` | Settings OK, prompts hashés, outils == contrats, IR à jour | non |
| `{AppName} version [--json]` | version, hash de l'IR, `event_schema` | non |
| `{AppName} inspect --graph` | le graphe compilé en Mermaid, à comparer à la topologie | non |

Identité : `--tenant` (ou la variable d'environnement de l'opérateur) — jamais
un champ de l'entrée. Date de référence : `--as-of`, défaut horloge ; sans elle
les evals ne sont pas rejouables.

---

## 4. Idiomes imposés

- `main.ts` : `program.parseAsync(process.argv)` ; toute commande est
  `async` ; `process.exitCode = code` puis retour — jamais `process.exit()`
  avant le flush de stdout ;
- résultat sur **stdout** (NDJSON en `--json`), logs sur **stderr** (`pino`) ;
  rien d'autre sur stdout ;
- `SIGINT`/`SIGTERM` → `AbortController.abort()` propagé au run ; sortie avec
  le code `interrupted` de la table ;
- `RunEventSchema.parse` sur chaque événement **émis** : une surface qui émet
  un événement hors schéma casse le runner d'eval ;
- `--json` désactive toute couleur et tout rendu TTY ;
- la composition est importée depuis `app/composition.ts` — la CLI ne construit
  rien.

---

## 5. Smoke

```bash
cd workspace/src/{AppName}
pnpm build
node dist/serving/cli/main.js --help
node dist/serving/cli/main.js version --json | node -e 'JSON.parse(require("fs").readFileSync(0,"utf8"))'
node dist/serving/cli/main.js health --json
node dist/serving/cli/main.js inspect --graph > /tmp/graph.mmd   # comparé au bloc mermaid de la topologie (diff-code-vs-ir)
pnpm vitest run tests/serving
```

---

## 6. Pièges connus

1. **`process.exit()` avant le flush.** Le dernier événement NDJSON est perdu ;
   `process.exitCode` et retour.
2. **Un `console.log` de debug.** Il casse le NDJSON ; tous les logs sur stderr.
3. **La couleur en `--json`.** Les codes ANSI entrent dans le flux machine.
4. **`readline` sur stdin sans fin de flux.** Lire `-` avec
   `for await (const chunk of process.stdin)`.
