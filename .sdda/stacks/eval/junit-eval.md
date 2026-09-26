# Stack: junit-eval (eval)

> §2.3 (Librairies) suit `junit-eval.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: eval-junit-eval
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin, java
Scope: la couche **test et évaluation** d'une application générée sur la JVM (Kotlin et Java) — JUnit 6 (Jupiter) pour les tests déterministes L0-L2 et pour la vérification, côté application, du **contrat d'évaluation** (`serving/cli.md` §3.5) ; Kotest optionnel en Kotlin ; rapport JUnit XML de Gradle ; marquage `network` par `@Tag("network")` et classes `*NetworkTest`. Les **évaluations scorées** (L3-L8 : k runs, variance, verdict vert/jaune/rouge, tuple d'épinglage, baseline, calibration du juge) **ne sont pas réimplémentées en JUnit** : elles restent jouées par le runner du framework, `python .sdda/sdda.py eval-runner`, qui **lance** l'application. Pendant JVM de `eval/pytest-eval.md`.

---

## 1. Rôle et périmètre

**Un test assert, une eval score** (`eval/pytest-eval.md` §1). En Python, les
deux vivent dans pytest parce que le runner d'eval du framework **est** du
Python et que l'application l'est aussi. En JVM, la frontière passe ailleurs,
et c'est voulu :

| Ce qui assert (L0-L2) | Ce qui score (L3-L8) |
|---|---|
| dans l'application, en **JUnit** | hors de l'application, dans **`sdda_eval`** (Python, stdlib) |
| lancé par `./gradlew test` | lancé par `python .sdda/sdda.py eval-runner --executor cli` |
| rapport `build/test-results/test/TEST-*.xml` | rapport `workspace/.sys/reports/{suite}-{run-id}.json` |
| LLM **toujours** simulé | LLM réel, k runs, traces lues |

Pourquoi ne pas écrire k runs, verdict et baseline en Java : le protocole
SDD_Agents (k ≥ 2, table de verdict close, `PinTuple` à cinq champs, holdout
refusé hors G8, juge calibré) a **une** implémentation, testée
(`.sdda/python/sdda_lib/sdda_eval/`, `eval_runner.py`). Une seconde, en JUnit,
divergerait dès la première règle ajoutée d'un côté — et deux verdicts pour la
même suite, c'est aucun verdict. Le runner du framework ne charge pas
l'application : il la **lance par sa CLI** (`serving/cli.md` §3.5,
`sdda_lib/executors.py`), ce qui le rend indifférent au langage. C'est aussi
ce qui fait de la CLI la surface des evals : ce qu'on mesure est ce qu'on
livre.

Périmètre de la fiche : outillage JUnit, conventions de nommage lues par les
scripts de gate, tests L1 du contrat d'évaluation côté application, rapport,
commandes. **Hors périmètre** : le contenu des datasets et des suites
(`qa-evals`), les graders et le juge (`sdda_lib/graders`), l'observabilité (les
evals lisent les traces de `observability/otel-genai-jvm.md`).

---

## 2. Identité

### 2.1 Identité

- **Stack ID** : `eval-junit-eval`
- **Langage** : Kotlin ou Java sur JDK 21 (`lang/kotlin.md`, `lang/java.md`)
- **Runner de tests** : JUnit Platform 6 sous Gradle (`./gradlew test`, `useJUnitPlatform()`)
- **Runner d'eval** : `python .sdda/sdda.py eval-runner` · `run-retrieval-eval` · `run-adversarial-suite`, avec `--executor cli` (ou `--executor cmd:<commande>`)
- **Paramètres STACK.md** : ceux de `eval/pytest-eval.md` §2.1 (`EvalRuns`, `EvalVarianceWarnPct`, `JudgeCalibrationMinKappa`, `HoldoutDisjointCheck`, `RegressionTolerancePct`, tailles minimales) — lus par le runner du framework, pas par JUnit
- **Ownership** : `workspace/src/**/tests/**` → `qa-tests` (et les `dev-*` pour les tests de leur couche) ; `workspace/pipeline/{datasets,suites,calibration,fixtures,baselines}/**` → `qa-evals` et les scripts, **jamais** un `dev-*`

### 2.3 Librairies

Source de vérité : `junit-eval.libs.json`. Toutes en `testImplementation` /
`testRuntimeOnly` : le livrable n'embarque pas ses tests.

| Artefact | Version | Rôle |
|---|---|---|
| `org.springframework.boot:spring-boot-starter-test` | BOM 4.1.1 | JUnit Jupiter, AssertJ, Mockito, `MockRestServiceServer` |
| `org.junit.jupiter:junit-jupiter-params` | 6.0.3 (BOM) | tests de contrat paramétrés |
| `org.junit.platform:junit-platform-launcher` | 6.0.3 (BOM) | exigé par Gradle 9 en `testRuntimeOnly` |
| `org.assertj:assertj-core` | 3.27.7 (BOM) | assertions |
| `com.networknt:json-schema-validator` | 3.0.7 | sorties d'outils et `RunEvent` validés contre leur schéma |
| `org.testcontainers:testcontainers-junit-jupiter` (on-demand) | 2.0.5 (BOM) | classes `*NetworkTest` avec base réelle |
| `io.kotest:kotest-runner-junit5`, `kotest-assertions-core` (on-demand, Kotlin) | 6.2.5 | specs Kotest sur la plateforme JUnit |
| `io.mockk:mockk` (on-demand, Kotlin) | 1.14.11 | doublures de classes Kotlin finales |

**Absents par conception** : `junit-pioneer` (`@RetryingTest` masquerait la
variance), les évaluateurs `RelevancyEvaluator` / `FactCheckingEvaluator` de
Spring AI (juges non calibrés, second juge à côté de celui du framework),
tout chargeur de `.env` en test.

---

## 3. Mapping des concepts SDD_Agents → idiomes JUnit et runner

| Concept (DOMAIN-MODEL) | Où | Idiome |
|---|---|---|
| **L0** statique | build | `./gradlew spotlessCheck compileJava` (Java : Error Prone + NullAway) / `ktlintCheck detekt` (Kotlin) |
| **L1** fonctions pures | JUnit | `RrfFusionTest`, `ExitCodesTest`, `RunEventJsonTest`, `HashingTest` (vecteurs Python), `RedactionTest` — sans contexte Spring |
| **L2** contrat d'outil | JUnit | `{Outil}Test` paramétré par cas (happy, chaque erreur déclarée, timeout, auth KO, idempotence, rate limit) ; `{Outil}NetworkTest` pour la connectivité réelle |
| **Isolement L4** — côté application | JUnit | `EvalContractTest` : avec `SDDA_EVAL_ISOLATION=mocked` et un dossier de fixtures temporaire, `run` n'ouvre aucune connexion, sert les outils depuis `tools/{outil}.jsonl`, et refuse (code 8) un outil sans fixture |
| **G4 sans agent** — côté application | JUnit | `EvalContractTest` : `retrieve --json --index ID --query-file -` émet `retrieval` puis `run_finished`, sans appel au modèle de chat |
| **EVAL SUITE**, **k runs**, **verdict**, **baseline**, **calibration** | runner du framework | `eval/pytest-eval.md` §3 — le runner lance `java -jar …/{AppName}.jar run --json --input-file -` k fois par item et lit NDJSON, code de sortie et trace |
| **L3 retrieval** | runner du framework | `run-retrieval-eval --executor cli` → `retrieve … --k N` |
| **L5 trajectoire / L7 coût, latence** | runner du framework | lit la trace JSONL (`observability/otel-genai-jvm.md`) — jamais la réponse |
| **L8 adversarial** | runner du framework | `run-adversarial-suite --executor cli` ; **code de sortie 4 attendu** sur un refus |

### 3.1 Conventions de nommage — ce que les scripts de gate lisent

Ce sont des **contrats avec l'outillage**, pas des préférences de style :

1. **Fichiers de test en `*Test.kt` / `*Test.java`**, sous
   `workspace/src/{AppName}/tests/{couche}/` (disposition à plat,
   `lang/*.md` §4).
2. **Le nom d'affichage d'un test de contrat paramétré contient l'identifiant
   du cas** : `@ParameterizedTest(name = "{0}", quoteTextArguments = false)`,
   premier argument = id du cas du contrat (`happy`, `err-not-found`,
   `timeout`, `auth-failed`…). Le rapport porte alors
   `<testcase name="err-not-found" …>`, que G3 rapproche des erreurs déclarées
   §4 du contrat. Sans `quoteTextArguments = false`, JUnit 6 écrit
   `name="&quot;err-not-found&quot;"` (constaté sur 6.0.3).
3. **Les tests réseau vivent dans des classes dont le nom contient `Network`
   et portent `@Tag("network")`** (`OrdersLookupNetworkTest`). Le tag est ce
   que Gradle filtre ; le nom est ce qu'un script ou un humain voit sans
   ouvrir le fichier. Une classe `network` sans l'un des deux est un finding.
4. **Un tag `llm`** marque un test qui appellerait un modèle réel — il ne doit
   pas en exister en L0-L2 ; le filtre par défaut l'exclut quand même.

**Java**

```java
// tests/tools/OrdersLookupTest.java — package {package}.tools
class OrdersLookupTest {
    static Stream<Arguments> cases() {
        return ContractCases.load("1-orders-lookup").stream()          // cas dérivés du tool-contract (§4 erreurs)
                .map(c -> Arguments.of(c.id(), c));
    }

    @ParameterizedTest(name = "{0}", quoteTextArguments = false)
    @MethodSource("cases")
    void contract(String caseId, ContractCase c) {
        ToolOutcome out = harness.call(c.args(), c.stubbedResponse());  // transport simulé, jamais le vrai service
        assertThat(out.errorCode()).isEqualTo(c.expectedErrorCode());   // null pour happy
        assertThat(Schemas.validate(out.payload(), c.outputSchema())).isEmpty();
    }
}

// tests/tools/OrdersLookupNetworkTest.java
@Tag("network")
class OrdersLookupNetworkTest {
    @Test void liveConnectivity() { /* le vrai service : auth, joignabilité — seconde moitié de la TOOL GATE */ }
}
```

**Kotlin**

```kotlin
// tests/tools/OrdersLookupTest.kt — package {package}.tools
class OrdersLookupTest {
    companion object {
        @JvmStatic fun cases() = ContractCases.load("1-orders-lookup").map { Arguments.of(it.id, it) }
    }

    @ParameterizedTest(name = "{0}", quoteTextArguments = false)
    @MethodSource("cases")
    fun contract(caseId: String, c: ContractCase) {
        val out = harness.call(c.args, c.stubbedResponse)
        assertThat(out.errorCode).isEqualTo(c.expectedErrorCode)
        assertThat(Schemas.validate(out.payload, c.outputSchema)).isEmpty()
    }
}

@Tag("network")
class OrdersLookupNetworkTest {
    @Test fun liveConnectivity() { /* … */ }
}
```

En Kotlin avec Kotest (on-demand), un `FunSpec` porte le même id dans le nom du
test (`test("err-not-found") { … }`) et la classe réseau porte
`tags(Tag("network"))` **et** un nom contenant `Network` ; le rapport XML est
écrit au même endroit (vérifié).

### 3.2 La sélection par tag

```kotlin
// build.gradle.kts
tasks.test {
    useJUnitPlatform {
        val include = project.findProperty("includeTags") as String?
        if (include != null) includeTags(include) else excludeTags("network", "llm")
    }
    reports.junitXml.required = true
}
dependencies { testRuntimeOnly("org.junit.platform:junit-platform-launcher") }
```

Une classe exclue par tag **n'apparaît pas** dans `build/test-results/test/`
(constaté) : l'absence n'est pas un vert. La commande `-PincludeTags=network`
est celle de la seconde moitié de G3 ; elle exige les dépendances réelles
(Docker pour Testcontainers, serveurs MCP joignables).

### 3.3 Le test du contrat d'évaluation, côté application

`tests/serving/EvalContractTest` démarre l'application **en processus** (le
`RunService` et la CLI, modèle de chat simulé) et vérifie les trois points de
`serving/cli.md` §3.5 — c'est la moitié du contrat que le runner ne peut pas
tester lui-même sans coût :

| Cas | Attendu |
|---|---|
| `run --json --input-file -` avec une entrée commençant par `-` | lue depuis stdin, pas prise pour une option ; dernière ligne `run_finished` |
| `SDDA_EVAL_ISOLATION=mocked`, fixtures complètes | aucun `DataSource`, aucun client MCP, aucun appel d'embedding construits ; réponses des fixtures |
| `SDDA_EVAL_ISOLATION=mocked`, un outil sans fixture | événement `error` `[CONFIG_INVALID]`, `run_finished`, code `8` |
| `retrieve --json --index kb --query-file - --k 3` en mode isolé | `["retrieval", "run_finished"]`, au plus 3 identifiants |
| chaque ligne de `stdout` | parse en `RunEvent` ; rien d'autre sur `stdout` |

Les variables d'environnement sont injectées dans la **configuration** de
l'application (le `Settings` reçoit une `Map`), pas posées sur le processus de
test : un test qui modifie `System.getenv` n'existe pas en Java, et le simuler
par réflexion rend le test dépendant de l'ordre d'exécution.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/tests/            # racine de sources de test (sourceSets.test)
├── resources/                            # fixtures de TEST (réponses simulées, serveur MCP stdio de test) — pas les fixtures d'eval
├── serving/  ExitCodesTest · RunEventJsonTest · CliJsonTest · EvalContractTest
├── tools/    {Outil}Test · {Outil}NetworkTest · mcp/…
├── data/     ViewCatalogTest · ViewQueryEnvelopeTest · {Vue}Test · {Vue}NetworkTest
├── retrieval/ RrfFusionTest · QueryPrepTest · VoyageEmbeddingClientTest · HybridSqlNetworkTest
├── app/      HashingTest · RedactionTest · BoundsTest · TraceShapeTest
└── agents/{agent}/  {Agent}Test            # L1 : LLM simulé, bornes, sortie structurée

workspace/pipeline/                        # ownership qa-evals — lu par le runner du framework
├── suites/{n}-{m}-{grader}.yaml           # format de eval/pytest-eval.md §3.1, inchangé
├── datasets/{golden,holdout,calibration,adversarial}/*.jsonl
├── fixtures/tools/{outil}.jsonl · fixtures/retrieval/{index}.jsonl   # SDDA_EVAL_FIXTURES
└── baselines/ · calibration/              # scripts uniquement
```

Les fixtures d'**évaluation** (`workspace/pipeline/fixtures/`) et les
ressources de **test** (`tests/resources/`) ne se mélangent pas : les
premières appartiennent à `qa-evals` et jugent l'agent, les secondes à
`qa-tests` et vérifient le code. Un `dev-*` ne peut pas écrire les premières
(`rules/ownership.md`).

---

## 5. Conventions imposées

1. **LLM simulé en L0-L2, toujours.** Un `ChatModel` de test rend des réponses
   écrites ; aucun test JUnit n'a de clé d'API.
2. **Pas de relance** : ni `@RetryingTest`, ni `retry` Gradle
   (`org.gradle.test-retry`) sur les tests de contrat — un contrat qui passe une
   fois sur deux est rouge.
3. **Le verdict d'une eval n'est jamais calculé en JUnit.** Aucune classe de
   test ne lit `workspace/pipeline/datasets/` ni n'écrit dans
   `workspace/pipeline/baselines/`.
4. **Les noms des cas viennent du contrat** (`ContractCases.load(contractId)`),
   jamais d'une liste recopiée : un cas ajouté au contrat sans test devient un
   écart visible dans le rapport.
5. **`reports.junitXml.required = true`** et aucun `ignoreFailures = true` : le
   rapport est la mesure, et un échec doit rester un échec pour Gradle.
6. **Un test par couche, dans le répertoire de sa couche** (`tests/{couche}/`)
   — c'est ce qui rend lisible, dans le rapport, qui a cassé quoi.

---

## 6. Commande de smoke

Déterministe, 0 token — vérifie la chaîne de test et le contrat d'évaluation,
pas les agents :

```bash
cd workspace/src/{AppName}
./gradlew test                                              # L0 compilé + L1/L2 hors réseau
ls build/test-results/test/TEST-*.xml                        # le rapport que les scripts lisent
./gradlew test --tests '*.serving.EvalContractTest'          # le contrat §3.5, côté application
cd ../../..
python .sdda/sdda.py eval-runner --mission {n} --executor cli --help   # le runner résout la commande du langage actif
```

Le premier `eval-runner` réel (k runs, LLM réel) appartient à la PHASE 6 et
coûte des tokens — il n'est pas dans le smoke.

---

## 7. Contrat d'exécution

L'application JVM implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** — commandes, NDJSON `RunEvent`, codes de sortie — et les trois
points de `serving/cli.md` §3.5, que cette fiche **teste** côté application
(§3.3) :

1. `run --json --input-file -` lit l'entrée sur `stdin` ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'application sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil) et le retrieval
   figé depuis le même dossier, sans aucun appel réseau d'outil ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished`.

Les runners lancent l'application par `--executor cli`, qui dérive du langage
actif la commande `java -jar workspace/src/{AppName}/build/libs/{AppName}.jar`
(Kotlin comme Java), ou par `--executor cmd:<commande>`. En développement :
`./gradlew run --args='…'` depuis `workspace/src/{AppName}`. Le build produit
exactement `build/libs/{AppName}.jar`.

Le framework, de son côté, exécute les tests par `./gradlew test` (le wrapper
s'il existe, `gradle` sinon) depuis `workspace/src/{AppName}` et lit
`build/test-results/test/*.xml`.

> **Réserve outillage, dite telle quelle.** À la rédaction,
> `run_tool_suites.py` (G3) découvre `test_*.py` et lance pytest en dur
> (`.sdda/python/sdda_scripts/run_tool_suites.py`) ; la lecture du rapport
> Gradle ci-dessus est ce que la fiche impose à l'application, et c'est à
> l'outillage de la consommer. Tant qu'il ne le fait pas, G3 ne se mesure pas
> par ce script pour un projet JVM.

---

## 8. Pièges connus

1. **Guillemets dans le nom paramétré.** JUnit 6 cite les arguments texte
   dans `{0}` ; sans `quoteTextArguments = false`, l'id du cas du rapport ne
   correspond plus à celui du contrat.
2. **`junit-platform-launcher` absent.** Gradle 9 ne l'ajoute plus : « Failed
   to load JUnit Platform », aucun rapport, et un script qui lit un répertoire
   vide conclut « zéro échec ».
3. **Surclasser JUnit sous Spring Boot.** Déclarer `junit-bom` 6.1.3 à côté du
   BOM Spring Boot 4.1.1 (6.0.3) fait gagner la plus haute version pour une
   partie des modules seulement si l'alignement est mal déclaré ; rester sur
   la version du BOM.
4. **Le test « d'intégration » qui appelle le vrai modèle.** Il passe en local
   avec la clé du développeur, échoue en CI, et coûte à chaque commit. Le
   modèle est simulé ; le vrai modèle ne s'appelle que par le runner d'eval.
5. **Tester la CLI par `ProcessBuilder` sur le jar dans JUnit.** Lent (démarrage
   JVM par cas) et redondant avec le runner du framework. Le contrat se teste
   en processus (§3.3) ; le jar se teste par le smoke et par le runner.
6. **Kotest et JUnit dans la même classe.** Une spec Kotest n'est pas une
   classe JUnit : `@Tag` JUnit n'a pas d'effet sur elle — utiliser les tags
   Kotest, et garder le nom `*NetworkTest`.
