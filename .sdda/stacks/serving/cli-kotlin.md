# Stack: cli-kotlin (serving)

Stack ID: serving-cli-kotlin
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin
Scope: surface d'exposition **ligne de commande JVM** de l'application agentic — commandes, entrée/sortie, streaming, mode machine (`--json` NDJSON), reprise, **codes de sortie stables** mappés sur la taxonomie `[CLASS]`, publication en jar exécutable. C'est la surface du livrable `cli-exe` sur `lang/kotlin.md`, et le **défaut** pour un projet Kotlin. Contrat d'événements et mapping `[CLASS] → code` **identiques** à `serving/cli.md` §3.2-3.3. `clikt` pour l'analyse des arguments ; pas de `.libs.json` propre (ajouté au catalogue du framework actif, capability `serving-cli`).

---

## 1. Rôle et périmètre

Le pendant JVM de `serving/cli.md`, pour la même raison que `cli-dotnet.md` et
`cli-node.md` : un défaut qu'un langage ne peut pas honorer est un piège. Les
trois raisons de la CLI valent ici (`serving/cli.md` §1), et la règle aussi :
**le contrat ne se réécrit pas, il se réutilise.**

Hors périmètre : la maison HTTP (`backend/kotlin-spring-boot.md`), qui
réutilise le même `RunService`.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `serving-cli-kotlin` |
| **Langage** | Kotlin 2.x, JDK 21 (`lang/kotlin.md`) |
| **Librairies** | `clikt` 5.x — ajouté au catalogue du framework actif, capability `serving-cli` |
| **Point d'entrée** | `application { mainClass = "{package}.serving.cli.MainKt" }` ; jar exécutable via `installDist` / `shadowJar` |
| **Paramètres STACK.md** | `StreamingEnabled`, `HumanInTheLoopEnabled` (active `resume`), `TraceLevel` |
| **Contrat machine** | `--json` : une ligne NDJSON par événement, `RunEvent` (data class sérialisée, `event_schema: "1"`) |
| **Codes de sortie** | table `serving/cli.md` §3.3 — stables, dans `--help`, testés en L1 |

---

## 3. Commandes

| Commande | Rôle | Coûte des tokens |
|---|---|:-:|
| `{AppName} run [--json] [--tenant ID] [--as-of DATE] [--input FILE | -]` | une exécution | oui |
| `{AppName} resume RUN_ID [--json]` | reprise (si `HumanInTheLoopEnabled`) | oui |
| `{AppName} health [--json]` | Settings OK, prompts hashés, outils == contrats, IR à jour | non |
| `{AppName} version [--json]` | version, hash de l'IR, `event_schema` | non |
| `{AppName} inspect --graph` | le graphe compilé en Mermaid | non |

Identité par `--tenant` (ou variable de l'opérateur), jamais par l'entrée ;
date de référence par `--as-of`.

---

## 4. Idiomes imposés

- `Main.kt` : `RootCommand().subcommands(Run(), Resume(), Health(), Version(), Inspect()).main(args)` ;
  chaque commande `runBlocking { … }` est la **seule** place autorisée pour
  `runBlocking` hors tests ;
- code de sortie via `throw ProgramResult(code)` de clikt, depuis la table
  `ExitCodes` partagée ; jamais `exitProcess` avant le flush ;
- stdout = résultat (NDJSON en `--json`), stderr = logs ;
- `Runtime.addShutdownHook` → annulation de la coroutine racine ; sortie avec le
  code `interrupted` ;
- `RunEvent` sérialisé par le **même** sérialiseur que le reste du projet
  (Jackson **ou** kotlinx, pas les deux) ;
- la composition est importée, la CLI ne construit rien.

---

## 5. Smoke

```bash
cd workspace/src/{AppName}
./gradlew installDist
build/install/{AppName}/bin/{AppName} --help
build/install/{AppName}/bin/{AppName} version --json
build/install/{AppName}/bin/{AppName} health --json
build/install/{AppName}/bin/{AppName} inspect --graph > /tmp/graph.mmd
./gradlew test --tests '*.serving.*'
```

---

## 6. Pièges connus

1. **Temps de démarrage JVM.** Un `health` à 800 ms n'est pas un défaut de
   la CLI, mais le runner d'eval l'additionne : le mesurer et le déclarer dans
   le budget de latence, pas le découvrir en G6.
2. **`exitProcess` avant le flush.** Même piège qu'en Node.
3. **Deux sérialiseurs.** `RunEvent` en Jackson côté CLI et kotlinx côté HTTP
   produisent deux formes du même événement.
4. **`println` de debug.** Casse le NDJSON ; `kotlin-logging` sur stderr.
