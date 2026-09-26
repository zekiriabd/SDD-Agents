# Stack: cli-kotlin (serving)

Stack ID: serving-cli-kotlin
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin
Scope: surface d'exposition **ligne de commande JVM en Kotlin** de l'application agentic — commandes, entrée/sortie, streaming, mode machine (`--json` NDJSON), reprise, **codes de sortie stables** mappés sur la taxonomie `[CLASS]`, publication en jar exécutable `build/libs/{AppName}.jar`. C'est la surface du livrable `cli-exe` sur `lang/kotlin.md`, et le **défaut** pour un projet Kotlin. Pendant exact de `serving/cli-java.md` : contrat d'événements et mapping `[CLASS] → code` **identiques** à `serving/cli.md` §3.2-3.3, contrat d'évaluation identique à §3.5. `clikt` pour l'analyse des arguments ; pas de `.libs.json` propre (ajouté au catalogue du framework actif, capability `serving-cli`).

---

## 1. Rôle et périmètre

Le pendant JVM de `serving/cli.md`, pour la même raison que `cli-dotnet.md`,
`cli-node.md` et `cli-java.md` : un défaut qu'un langage ne peut pas honorer
est un piège. Les trois raisons de la CLI valent ici (`serving/cli.md` §1), et
la règle aussi : **le contrat ne se réécrit pas, il se réutilise.** Ce que
cette fiche ajoute, c'est comment le tenir avec clikt **sous** Spring Boot sans
que Spring casse `stdout` ni le code de sortie, et sans que clikt sorte du
processus avant que les traces soient écrites.

Hors périmètre : la maison HTTP (`backend/kotlin-spring-boot.md`,
`serving/spring-sse.md`), qui réutilise le même `RunService` et le même
`RunEvent`.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `serving-cli-kotlin` |
| **Langage** | Kotlin 2.3, JDK 21 (`lang/kotlin.md`) |
| **Librairies** | `clikt` 5.x — catalogue du framework actif (`framework/spring-ai.libs.json`), capability `serving-cli` |
| **Point d'entrée** | `{package}.app.ApplicationKt` (`fun main` de `app/Application.kt`) ; la CLI est un bean `CommandLineRunner` + `ExitCodeGenerator` dans `serving/cli/` |
| **Livrable** | `build/libs/{AppName}.jar` (`bootJar`, nom fixé — `lang/kotlin.md` §8.2) |
| **Paramètres STACK.md** | `StreamingEnabled`, `HumanInTheLoopEnabled` (active `resume`), `TraceLevel` |
| **Contrat machine** | `--json` : une ligne NDJSON par événement, `RunEvent` (data class sérialisée par Jackson 3, `event_schema: "1"`) |
| **Codes de sortie** | table `serving/cli.md` §3.3 — stables, dans `--help`, testés en L1 |

---

## 3. Commandes

| Commande | Rôle | Coûte des tokens |
|---|---|:-:|
| `{AppName} run [--json] [--tenant ID] [--thread-id ULID] [--input TEXT \| --input-file PATH\|-] [--max-budget-usd X]` | une exécution | oui |
| `{AppName} resume --thread-id ULID [--decision TEXT \| --decision-file PATH] [--json]` | reprise (si `HumanInTheLoopEnabled` et checkpointer) | oui |
| `{AppName} retrieve --json --index ID --query-file - [--k N]` | retrieval seul, G4 (`serving/cli.md` §3.5) | non (embedding de la requête seulement) |
| `{AppName} health [--live] [--json]` | Settings OK, prompts hashés, outils == contrats, IR à jour | non |
| `{AppName} inspect [--graph] [--json]` | le graphe compilé en Mermaid, l'IR résumé | non |
| `{AppName} trace --run-id ID [--json]` | relit la trace JSONL d'un run | non |
| `{AppName} version [--json]` | version, hash de l'IR, `event_schema`, `semconv_version` | non |

Identité par `--tenant` (ou `SDDA_TENANT_ID`), jamais par l'entrée ; aucune
option ne porte de secret. `resume` n'est **pas enregistrée** si la stack
active ne fournit pas de checkpointer (absente de `--help` plutôt que présente
et cassée — `serving/cli.md` §5.9).

---

## 4. Idiomes imposés

### 4.1 clikt sous Spring Boot : `parse()`, jamais `main()`

`CliktCommand.main(args)` appelle `exitProcess` : le processus sortirait
**avant** que Spring ferme son contexte, donc avant le `forceFlush` des traces.
Et une erreur d'usage clikt porte `statusCode = 1` par défaut — **vérifié le
2026-09-26** sur clikt 5.1.0 sous Spring Boot 4.1.1 : une option inconnue
sortait en `1`, là où `serving/cli.md` §3.3 réserve `2`. D'où :

```kotlin
// serving/cli/Cli.kt — package {package}.serving.cli
@Component
class Cli(private val root: RootCommand) : CommandLineRunner, ExitCodeGenerator {
    private var exitCode = ExitCodes.INTERNAL

    override fun run(vararg args: String) {
        exitCode = try {
            root.parse(args.toList())            // n'appelle jamais exitProcess
            root.outcomeCode                     // code posé par la sous-commande, issu d'ExitCodes.of(outcome)
        } catch (e: PrintHelpMessage) {
            System.out.print(root.getFormattedHelp(e)); ExitCodes.OK
        } catch (e: UsageError) {
            System.err.println(root.getFormattedHelp(e)); ExitCodes.USAGE        // 2, et non le statusCode = 1 de clikt
        } catch (e: CliktError) {
            e.statusCode
        } catch (e: Throwable) {
            ExitCodes.resolve(e)                 // [CLASS] -> code, un seul endroit
        }
    }

    override fun getExitCode(): Int = exitCode
}
```

```kotlin
// app/Application.kt — package {package}.app
fun main(args: Array<String>) {
    val app = SpringApplication(Application::class.java)
    app.setAddCommandLineProperties(false)                // `--tenant x` n'est pas une propriété Spring
    exitProcess(SpringApplication.exit(app.run(*args)))   // sortie APRÈS la fermeture du contexte (traces vidées)
}
```

- Les sous-commandes (`Run`, `Resume`, `Retrieve`, `Health`, `Inspect`,
  `Trace`, `Version`) sont des beans ; `RootCommand` les reçoit par
  constructeur et les enregistre par `subcommands(...)`. Aucune ne construit
  le moteur : la composition est dans `app/`.
- Aucune commande n'appelle `exitProcess` ni ne lève `ProgramResult` : elle
  **pose** son code (`outcomeCode`) depuis `ExitCodes.of(outcome)`.
- `runBlocking { … }` dans le `run()` d'une sous-commande est le **seul** pont
  coroutines autorisé côté CLI (`lang/kotlin.md` §2).
- `stdout` = résultat (NDJSON en `--json`), `stderr` = journaux (Logback sur
  `System.err`, bannière coupée — `lang/kotlin.md` §8.3). Le seul code qui écrit
  sur `stdout` est `NdjsonWriter`, UTF-8, `flush()` après chaque événement.
- `RunEvent` : `sealed interface` de data classes, sérialisé par **le même**
  `JsonMapper` Jackson 3 que le reste du projet (jamais kotlinx en plus) ;
  `event_schema` et `run_id` sur chaque événement.

### 4.2 Entrée

`--input-file -` (et `--query-file -` pour `retrieve`) lit **stdin** en UTF-8,
plafonné à `max_input_bytes` (au-delà : code `2` avant tout appel LLM). Sur un
TTY sans entrée : code `2` avec message, pas d'attente indéfinie.

### 4.3 Codes de sortie

`serving/cli/ExitCodes.kt` porte la table de `serving/cli.md` §3.3 (`OK = 0`,
`INTERNAL = 1`, `USAGE = 2`, `BOUND = 3`, `REFUSED = 4`, `BUDGET = 5`,
`TOOL = 6`, `DEGRADED = 7`, `CONFIG = 8`, `OUTPUT_INVALID = 9`,
`INTERRUPTED = 10`, `RESUME_FAILED = 11`, `SIGINT = 130`) et le mapping
`[CLASS] → code` — la même table que `serving/cli-java.md` §4.2. Test L1 :
table exhaustive, classe inconnue → `1`, codes `3/4/5/7/10` émettent `final`
ou `interrupted` avant `run_finished`.

### 4.4 Signaux

Spring Boot pose un hook d'arrêt qui ferme le contexte ; le `RunService`
s'y abonne (`@PreDestroy`) pour annuler la coroutine racine, émettre
`run_finished` avec `exit_code: 130` et laisser la fermeture vider les traces.

---

## 5. Contrat d'exécution

L'application implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** — commandes, NDJSON `RunEvent`, codes de sortie — et les trois
points de `serving/cli.md` §3.5 :

1. `run --json --input-file -` lit l'entrée sur `stdin` ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'application sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil,
   `tools/{outil}.jsonl`) et le retrieval figé depuis le même dossier
   (`retrieval/{index}.jsonl`), sans aucun appel réseau d'outil ; un outil sans
   fixture → refus de démarrer, code `8` `[CONFIG_INVALID]` ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished` ;
   `--k` remplace le `topK` du contrat pour l'appel.

**Commande de lancement** (depuis la racine du dépôt) :
`java -jar workspace/src/{AppName}/build/libs/{AppName}.jar …` pour le
livrable ; en développement `./gradlew run --args='…'` depuis
`workspace/src/{AppName}` (`standardInput = System.in` sur la tâche `run`).
Les runners l'obtiennent par `--executor cli` (dérivée du langage actif,
`sdda_lib/executors.py`) ou l'imposent par `--executor cmd:<commande>`. Le
build **doit** produire exactement `build/libs/{AppName}.jar`
(`tasks.bootJar { archiveFileName = "{AppName}.jar" }`, `tasks.jar { enabled =
false }` — `lang/kotlin.md` §8.2).

`run_finished` porte `run_id`, `status`, `exit_code`, `cost_usd`,
`duration_ms`, `hops`, `tool_calls` et `trace_path` : c'est ce que
`CommandExecutor.run` lit, et un champ absent y devient un zéro.

---

## 6. Structure de fichiers générée

```
workspace/src/{AppName}/serving/cli/
├── Cli.kt                 # CommandLineRunner + ExitCodeGenerator ; parse(), remappage UsageError -> 2 (§4.1)
├── RootCommand.kt         # CliktCommand(name = "{AppName}") ; subcommands(...) ; table des codes dans --help
├── RunCommand.kt · ResumeCommand.kt · RetrieveCommand.kt · HealthCommand.kt · InspectCommand.kt · TraceCommand.kt · VersionCommand.kt
├── RunEvent.kt            # sealed interface + data classes, event_schema = "1"
├── NdjsonWriter.kt        # seul écrivain de stdout ; flush par événement ; UTF-8
├── ExitCodes.kt           # table §4.3
└── InputReader.kt         # --input | --input-file PATH | - (stdin) ; plafond ; TTY sans entrée -> 2

workspace/src/{AppName}/tests/serving/
├── ExitCodesTest.kt       # L1 : table exhaustive ; classe inconnue -> 1
├── RunEventJsonTest.kt    # L1 : chaque RunEvent sérialisable ; event_schema présent ; args d'outil redigés
├── CliUsageTest.kt        # L1 : option inconnue -> 2 ; --help -> 0 ; aucune sortie de processus pendant parse()
└── CliContractTest.kt     # L1 : --input-file - lit stdin ; retrieve émet retrieval puis run_finished ; isolement sans fixture -> 8 ;
                           #      chaque ligne de stdout parse en RunEvent
```

---

## 7. Smoke

```bash
cd workspace/src/{AppName}
./gradlew bootJar
test -f build/libs/{AppName}.jar
cd ../../..
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar --help                 # exit 0 ; liste la table des codes
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar version --json | python -c "import sys,json; [json.loads(l) for l in sys.stdin]"
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar health --json          # exit 0, ou 8 avec la [CLASS]
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar inspect --graph > /tmp/graph.mmd
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar --option-inconnue; test $? -eq 2
(cd workspace/src/{AppName} && ./gradlew test --tests '*.serving.*')
```

Le second appel parse **chaque** ligne de `stdout` : c'est lui qui attrape une
bannière ou un journal Spring égaré. `installDist` n'est pas utilisé : le
contrat lance un jar, pas un script de distribution.

---

## 8. Pièges connus

1. **`main(args)` de clikt.** Il appelle `exitProcess` avant la fermeture du
   contexte Spring : les derniers spans du run — souvent ceux de l'erreur —
   sont perdus. `parse()`, toujours.
2. **Usage en `1`.** Le `statusCode` par défaut des erreurs d'usage clikt est
   `1` (constaté) ; sans remappage, un script qui distingue « mauvais appel »
   (`2`) d'« erreur interne » (`1`) se trompe.
3. **Journaux Spring sur `stdout`.** Constaté au démarrage par défaut :
   `banner-mode: off` et Logback sur `System.err` (`lang/kotlin.md` §8.3).
4. **`ProgramResult` / `exitProcess` dans une sous-commande.** Même effet que
   `main()` : court-circuite l'`ExitCodeGenerator`.
5. **Deux sérialiseurs.** `RunEvent` en Jackson côté CLI et kotlinx côté HTTP
   produisent deux formes du même événement.
6. **`println` de debug.** Casse le NDJSON ; `kotlin-logging` sur stderr.
7. **Temps de démarrage JVM + Spring.** 1,5 à 5 s par invocation mesurés sur un
   poste de développement ; le runner le paie à chaque item et à chaque run —
   le déclarer dans le budget de latence.
