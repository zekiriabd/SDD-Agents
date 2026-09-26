# Stack: cli-java (serving)

Stack ID: serving-cli-java
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: java
Scope: surface d'exposition **ligne de commande JVM en Java** de l'application agentic — commandes, entrée/sortie, streaming, mode machine (`--json` NDJSON), reprise, **codes de sortie stables** mappés sur la taxonomie `[CLASS]`, publication en jar exécutable `build/libs/{AppName}.jar`. C'est la surface du livrable `cli-exe` sur `lang/java.md`, et le **défaut** pour un projet Java. Pendant exact de `serving/cli-kotlin.md` : contrat d'événements et mapping `[CLASS] → code` **identiques** à `serving/cli.md` §3.2-3.3, contrat d'évaluation identique à §3.5. `picocli` pour l'analyse des arguments ; pas de `.libs.json` propre (ajouté au catalogue du framework actif, capability `serving-cli-java`).

---

## 1. Rôle et périmètre

Le pendant Java de `serving/cli.md`, pour la même raison que `cli-dotnet.md`,
`cli-node.md` et `cli-kotlin.md` : un défaut qu'un langage ne peut pas honorer
est un piège. Les trois raisons de la CLI valent ici (`serving/cli.md` §1) —
déterministe à câbler, surface des evals, bornes visibles par le code de
sortie — et la règle aussi : **le contrat ne se réécrit pas, il se réutilise.**
Ce que cette fiche ajoute, c'est comment le tenir avec picocli **sous** Spring
Boot sans que Spring casse `stdout` ni le code de sortie.

Hors périmètre : la maison HTTP (`backend/kotlin-spring-boot.md`, `Languages:
kotlin, java`), qui réutilise le même `RunService` et le même `RunEvent`.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `serving-cli-java` |
| **Langage** | Java 21 (`lang/java.md`) |
| **Librairies** | `picocli` 4.7.x — ajouté au catalogue du framework actif (`framework/spring-ai.libs.json`), capability `serving-cli-java` |
| **Point d'entrée** | `{package}.app.Application` (`@SpringBootApplication`) ; la CLI est un bean `CommandLineRunner` + `ExitCodeGenerator` dans `serving/cli/` |
| **Livrable** | `build/libs/{AppName}.jar` (`bootJar`, nom fixé — `lang/java.md` §8.2) |
| **Paramètres STACK.md** | `StreamingEnabled`, `HumanInTheLoopEnabled` (active `resume`), `TraceLevel` |
| **Contrat machine** | `--json` : une ligne NDJSON par événement, `RunEvent` (record sérialisé par Jackson 3, `event_schema: "1"`) |
| **Codes de sortie** | table `serving/cli.md` §3.3 — stables, dans `--help`, testés en L1 |

**Pourquoi picocli** (et non Spring Shell) : picocli est une bibliothèque
d'analyse d'arguments sans dépendance, dont le code de retour d'`execute()` est
le code de sortie (`CommandLine.ExitCode.USAGE` vaut `2`, exactement la
réservation de `serving/cli.md` §3.3 — vérifié : une option inconnue sort en 2
à travers Spring Boot). Spring Shell (`spring-shell-starter` 4.0.3) vise
l'interactif (REPL), que le MVP exclut (`serving/cli.md` §5.12), et démarre
plus lourd. `picocli-spring-boot-starter` 4.7.7 existe, mais sa compatibilité
avec Spring Boot 4 n'est **pas vérifiée** : la fabrique Spring est écrite en
dix lignes (§4.1) plutôt que tirée d'un starter.

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

### 4.1 picocli sous Spring Boot

```java
// serving/cli/Cli.java — package {package}.serving.cli
@Component
final class Cli implements CommandLineRunner, ExitCodeGenerator {
    private final ApplicationContext context;
    private int exitCode = ExitCodes.INTERNAL;

    Cli(ApplicationContext context) { this.context = context; }

    @Override
    public void run(String... args) {
        CommandLine.IFactory factory = new CommandLine.IFactory() {
            @Override
            public <K> K create(Class<K> type) throws Exception {
                try {
                    return context.getBean(type);                 // commandes = beans : injection par constructeur
                } catch (BeansException notABean) {
                    return CommandLine.defaultFactory().create(type);   // convertisseurs, mixins
                }
            }
        };
        exitCode = new CommandLine(RootCommand.class, factory)
                .setExitCodeExceptionMapper(ExitCodes::resolve)     // [CLASS] -> code, un seul endroit
                .setExecutionExceptionHandler(new NdjsonErrorHandler())
                .execute(args);
    }

    @Override
    public int getExitCode() { return exitCode; }
}
```

- `Application.main` appelle `System.exit(SpringApplication.exit(app.run(args)))`
  (`lang/java.md` §8.3) : le code vient de l'`ExitCodeGenerator`, et
  `System.exit` n'intervient **qu'après** la fermeture du contexte, donc après
  le `forceFlush` des traces. Jamais `System.exit` dans une commande.
- Chaque sous-commande est un `@Command` qui implémente `Callable<Integer>` et
  **retourne** le code issu de `ExitCodes.of(outcome)` ; aucune ne choisit un
  entier à la main.
- `app.setAddCommandLineProperties(false)` : les options de la CLI ne
  deviennent pas des propriétés Spring.
- `stdout` = résultat (NDJSON en `--json`), `stderr` = journaux (Logback sur
  `System.err`, `lang/java.md` §8.3). Le seul code autorisé à écrire sur
  `stdout` est `NdjsonWriter` : un `PrintStream` UTF-8 avec `flush()` après
  chaque événement (`serving/cli.md` §5.4).
- `RunEvent` est un `sealed interface` de `record`, sérialisé par **le même**
  `JsonMapper` Jackson 3 que le reste du projet ; `event_schema` et `run_id`
  sur chaque événement, comme le runtime Python.

### 4.2 Les codes de sortie

```java
// serving/cli/ExitCodes.java
public final class ExitCodes {
    public static final int OK = 0, INTERNAL = 1, USAGE = 2, BOUND = 3, REFUSED = 4, BUDGET = 5,
            TOOL = 6, DEGRADED = 7, CONFIG = 8, OUTPUT_INVALID = 9, INTERRUPTED = 10, RESUME_FAILED = 11,
            SIGINT = 130;

    private static final Map<String, Integer> CLASS_TO_EXIT = Map.ofEntries(
            Map.entry("BUDGET_BOUND_EXCEEDED", BOUND), Map.entry("UNBOUNDED_LOOP", BOUND),
            Map.entry("SAFETY_GUARDRAIL_TRIPPED", REFUSED), Map.entry("AGENT_REFUSED", REFUSED),
            Map.entry("BUDGET_EXCEEDED_MEASURED", BUDGET),
            Map.entry("TOOL_CONTRACT_FAILED", TOOL), Map.entry("TOOL_MCP_DISCONNECTED", TOOL),
            Map.entry("CONFIG_INVALID", CONFIG), Map.entry("PROMPT_MISSING", CONFIG),
            Map.entry("TOOL_SCHEMA_DRIFT", CONFIG), Map.entry("IR_STALE", CONFIG),
            Map.entry("AGENT_OUTPUT_INVALID", OUTPUT_INVALID),
            Map.entry("AGENT_INTERRUPTED", INTERRUPTED), Map.entry("RESUME_FAILED", RESUME_FAILED));

    public static int of(String errorClass) { return CLASS_TO_EXIT.getOrDefault(errorClass, INTERNAL); }

    public static int resolve(Throwable error) {
        return error instanceof ClassifiedException c ? of(c.errorClass()) : INTERNAL;
    }
}
```

Test L1 : table exhaustive (toute classe connue a un code ; une classe inconnue
rend `1`) ; les codes `3`, `4`, `5`, `7`, `10` émettent `final` ou
`interrupted` **avant** `run_finished`.

### 4.3 Signaux

`Runtime.getRuntime().addShutdownHook(...)` est posé par Spring Boot pour
fermer le contexte ; le `RunService` s'y abonne (`@PreDestroy`) pour annuler
le run en cours, émettre `run_finished` avec `exit_code: 130` et laisser la
fermeture du contexte vider les traces. Sous Windows, un Ctrl-C dans une
console arrive aussi par ce hook ; un `TerminateProcess` ne passe par aucun
hook — la trace est alors écrite jusqu'au dernier span fermé (exporteur JSONL
synchrone, `observability/otel-genai-jvm.md` §5).

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
   fixture → refus de démarrer, code `8` ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished`.

**Commande de lancement** (depuis la racine du dépôt) :
`java -jar workspace/src/{AppName}/build/libs/{AppName}.jar …` pour le
livrable ; en développement `./gradlew run --args='…'` depuis
`workspace/src/{AppName}`. Les runners l'obtiennent par `--executor cli`
(dérivée du langage actif, `sdda_lib/executors.py`) ou l'imposent par
`--executor cmd:<commande>`. Le build **doit** produire exactement
`build/libs/{AppName}.jar` (`tasks.bootJar { archiveFileName =
"{AppName}.jar" }`, `tasks.jar { enabled = false }` — `lang/java.md` §8.2).

`run_finished` porte `run_id`, `status`, `exit_code`, `cost_usd`,
`duration_ms`, `hops`, `tool_calls` et `trace_path` : c'est ce que
`CommandExecutor.run` lit (`cost_usd`, `duration_ms`, `trace_path`, `run_id`,
`status`), et un champ absent y devient un zéro — donc un coût nul qui passe
sous n'importe quel plafond.

---

## 6. Structure de fichiers générée

```
workspace/src/{AppName}/serving/cli/
├── package-info.java      # @NullMarked
├── Cli.java               # CommandLineRunner + ExitCodeGenerator, fabrique picocli -> beans (§4.1)
├── RootCommand.java       # @Command(name = "{AppName}", mixinStandardHelpOptions = true, subcommands = {…})
├── RunCommand.java · ResumeCommand.java · RetrieveCommand.java · HealthCommand.java · InspectCommand.java · TraceCommand.java · VersionCommand.java
├── RunEvent.java          # sealed interface + records, event_schema = "1"
├── NdjsonWriter.java      # seul écrivain de stdout ; flush par événement ; UTF-8
├── ExitCodes.java         # table §4.2
├── InputReader.java       # --input | --input-file PATH | - (stdin) ; UTF-8 ; plafond max_input_bytes ; TTY sans entrée -> 2
└── NdjsonErrorHandler.java # exception -> événement error + run_finished, jamais une pile sur stdout

workspace/src/{AppName}/tests/serving/
├── ExitCodesTest.java     # L1 : table exhaustive ; classe inconnue -> 1
├── RunEventJsonTest.java  # L1 : chaque RunEvent sérialisable ; event_schema présent ; args d'outil redigés
├── CliJsonTest.java       # L1 : RunService simulé -> stdout = NDJSON valide uniquement ; codes de sortie
└── CliContractTest.java   # L1 : --input-file - lit stdin ; retrieve émet retrieval puis run_finished ; isolement sans fixture -> 8
```

La composition est importée, la CLI ne construit rien : `RunService` et les
agents sont des beans de `app/`.

---

## 7. Smoke

```bash
cd workspace/src/{AppName}
./gradlew bootJar
cd ../../..
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar --help                 # exit 0 ; liste la table des codes
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar version --json | python -c "import sys,json; [json.loads(l) for l in sys.stdin]"
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar health --json          # exit 0, ou 8 avec la [CLASS]
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar inspect --graph > /tmp/graph.mmd
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar --option-inconnue; test $? -eq 2
(cd workspace/src/{AppName} && ./gradlew test --tests '*.serving.*')
```

Le second appel parse **chaque** ligne de `stdout` : c'est lui qui attrape une
bannière ou un journal égaré.

---

## 8. Pièges connus

1. **Journal et bannière Spring sur `stdout`.** Le défaut (constaté) :
   `banner-mode: off` et Logback sur `System.err`, sinon le runner ne lit
   aucun événement.
2. **`System.exit` dans une commande.** Court-circuite la fermeture du
   contexte, donc le `forceFlush` des traces : le run le plus intéressant (celui
   qui échoue) perd ses derniers spans.
3. **`execute()` dont on ignore le retour.** picocli rend le code, il ne sort
   pas ; sans `ExitCodeGenerator`, Spring Boot sort en `0` quoi qu'il arrive.
4. **`@Option` avec valeur par défaut issue de la configuration.** picocli
   l'affiche dans `--help` : aucune option ne reçoit une valeur de `Settings`
   (`serving/cli.md` §7.8).
5. **Encodage Windows.** `System.out` suit la page de code de la console ;
   `NdjsonWriter` écrit sur `new PrintStream(new FileOutputStream(FileDescriptor.out), true, UTF_8)`,
   jamais sur `System.out` tel quel, et les journaux passent par Logback.
6. **Temps de démarrage JVM + Spring.** 2 à 5 s par invocation ; le runner le
   paie à chaque item et à chaque run : le mesurer et le déclarer dans le
   budget de latence.
