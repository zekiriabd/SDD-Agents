# Stack: kotlin (lang)

Stack ID: lang-kotlin
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin
Scope: langage et runtime de l'application agentic générée sur la JVM en Kotlin — JDK, outil de build, disposition de projet **à plat par couche**, conventions transverses, commande de test et rapport JUnit XML, **contrat d'exécution** (la commande que les runners du framework lancent). Le framework agentic est hors périmètre → `framework/spring-ai.md`. Pendant Kotlin de `lang/java.md` : les fiches JVM partagées (`rag/hybrid-jvm.md`, `vectorstore/pgvector-jvm.md`, `dataaccess/view-per-agent-jvm.md`, `tools/mcp-jvm.md`, `eval/junit-eval.md`, `observability/otel-genai-jvm.md`, `backend/kotlin-spring-boot.md`, `serving/spring-sse.md`) servent les deux langages. Pas de `.libs.json` : les pins vivent dans les catalogues JVM actifs, dépendances propres à Kotlin sous la capability `lang-kotlin`. **Réserve** : aucun générateur de squelette JVM n'existe — `dev-backend` écrit le squelette depuis cette fiche.

---

## 1. Rôle et périmètre

Cette fiche fixe **le socle JVM/Kotlin** des stacks Kotlin de SDD_Agents
(`framework/spring-ai.md`, `serving/cli-kotlin.md`, les fiches JVM partagées).
Elle décide :

- la version du langage, du JDK et de l'outil de build ;
- l'outillage déterministe (format, analyse statique, tests) en L0/L1 sans
  token ;
- la **disposition de projet** — la même qu'en Java et qu'en Python : un
  répertoire par couche d'ownership ;
- les conventions qui rendent les principes du framework vérifiables : aucun
  prompt inline (P1), aucun secret par `System.getenv` hors démarrage, toute
  borne typée (P12), `!!` interdit ;
- la commande que les runners d'évaluation lancent (§8).

> **Ce qui change par rapport à Python.** Comme en C#, le typage est appliqué à
> la compilation : une frontière de confiance déclarée en type (`Untrusted<T>`
> comme `value class`) est une erreur de build, pas une convention. La
> null-safety de Kotlin est le second garde-fou : `!!` est la seule façon de la
> contourner, et c'est pour cela qu'il est interdit. **Ce qui change par
> rapport à Java** : Java tient la même frontière par JSpecify + NullAway
> (`lang/java.md` §1) ; Kotlin l'a dans son système de types.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `lang-kotlin` |
| **Langage** | Kotlin **2.3** (K2) — `-Xjsr305=strict`, `allWarningsAsErrors = true` ; version = celle que gère le BOM Spring Boot 4.1.1 (`framework/spring-ai.libs.json`) |
| **Runtime** | JDK **21 LTS** — `kotlin { jvmToolchain(21) }` |
| **Build** | Gradle **Kotlin DSL** (`build.gradle.kts` + `settings.gradle.kts` à la racine de l'application), wrapper versionné, catalogue `gradle/libs.versions.toml` — une seule version par artefact |
| **Plugins Gradle** | `kotlin("jvm")`, `kotlin("plugin.spring")`, `org.springframework.boot`, `application`, `org.jlleitschuh.gradle.ktlint`, `io.gitlab.arturbosch.detekt` |
| **Format / statique** | `ktlint` + `detekt` — L0 |
| **Tests** | **JUnit Jupiter (JUnit 6)** + AssertJ par défaut ; Kotest **optionnel** (capability `kotest`, `eval/junit-eval.md`) — L1/L2 |
| **Sérialisation** | Jackson 3 (`tools.jackson.module:jackson-module-kotlin`, celui de Spring Boot 4) ; `kotlinx.serialization` hors Spring — un seul par projet |
| **Async** | `kotlinx-coroutines-core` ; `runBlocking` seulement aux deux ponts documentés (point d'entrée CLI, `DocumentRetriever` de `rag/hybrid-jvm.md`) et en test |
| **Logs** | `kotlin-logging` sur SLF4J / Logback, **tout sur stderr** (§8.3) |
| **Racine** | `workspace/src/{AppName}/` — un répertoire par couche (`app/`, `agents/`, `tools/`…, §4), tests sous `tests/` |

> Les versions vivent dans `framework/spring-ai.libs.json` (et les catalogues
> JVM des fiches actives). `kotlin` seul n'installe rien. Kotlin 2.4.20 est
> publié ; la fiche reste sur 2.3.21 tant qu'aucun build ne mesure 2.4 sous
> Spring Boot 4.1.1.

### 2.1 Init (idempotent)

```bash
if [ ! -f "workspace/src/{AppName}/settings.gradle.kts" ]; then
  mkdir -p workspace/src/{AppName}/{app/resources,agents,tools,data,retrieval,memory,orchestration,serving,shared,prompts,skills,rules,tests}
  cd workspace/src/{AppName}
  gradle wrapper --gradle-version {gradle}          # version du catalogue du framework
  # puis : settings.gradle.kts, build.gradle.kts (§4.1, §8.2), gradle/libs.versions.toml depuis les .libs.json actifs
fi
```

`gradle init --type kotlin-application` n'est **pas** utilisé : il crée
`app/src/main/kotlin/…` en sous-projet, la disposition Maven que la matrice
d'ownership refuse (§4).

---

## 3. Mapping des concepts SDD_Agents → idiomes Kotlin

### 3.1 Chargement et hash des prompts

`PromptLoader.load(agent)` lit la ressource `classpath:prompts/{agent}.system.md`
— le fichier `workspace/src/{App}/prompts/{agent}.system.md` embarqué tel quel
dans le jar (§4.1) — calcule son hash et le compare au hash épinglé de l'IR.
Le hash est **exactement** celui de `.sdda/python/sdda_lib/hashing.py`
(`sha256_text`) : BOM retiré, `\r\n` et `\r` → `\n`, espaces et tabulations de
fin de ligne retirés, lignes vides finales retirées, un `\n` final, SHA-256 des
octets UTF-8, préfixe `sha256:` ; les structures se hashent sur le JSON
canonique de `canonical_json` (clés triées, séparateurs `,` `:`, non-ASCII
brut). `shared/Hashing.kt` est testé en L1 contre des vecteurs calculés par le
script Python. Un prompt en chaîne littérale — y compris en *raw string*
`"""` — est interdit (P1) ; `detekt` signale toute chaîne de plus de 200
caractères dans un constructeur de message.

### 3.2 Bornes

```kotlin
// app/Bounds.kt — package {package}.app
data class Bounds(
    val maxIterations: Int,
    val maxToolCalls: Int,
    val maxDelegationDepth: Int,
    val timeout: Duration,
    val budgetUsd: BigDecimal,
    val onBoundExceeded: OnBoundExceeded,
) { init { require(maxIterations > 0); require(maxToolCalls > 0) } }

enum class OnBoundExceeded { FAIL_EXPLICIT, DEGRADE, ESCALATE_HUMAN }
```

Chargées depuis `app_config.json` ; une boucle sans `Bounds` en paramètre est
non bornée (P12).

### 3.3 Frontière de confiance

```kotlin
// shared/Untrusted.kt
@JvmInline value class Untrusted<T>(val value: T)
```

Toute sortie d'outil `trust: untrusted` et tout champ `free_text` d'une source
déclarée est enveloppé **au parsing** ; le compilateur refuse de le passer là
où un `String` maîtrisé est attendu.

---

## 4. Structure de fichiers générée

La matrice d'ownership (`.sdda/loader.yml`) attribue les zones d'écriture par
**répertoire de couche en minuscules, directement sous
`workspace/src/{AppName}/`**. La convention Maven/Gradle
`src/main/kotlin/{package}/…`, `src/main/resources/**`, `src/test/kotlin/**`
n'appartient à **aucun** agent : un fichier écrit là est refusé. Le build est
donc configuré pour l'arbre à plat — c'est le build qui s'adapte, parce que la
matrice est ce qui empêche l'agent qui écrit le code de toucher au prompt ou au
jeu qui le juge.

```
workspace/src/{AppName}/
├── settings.gradle.kts · build.gradle.kts · gradle/libs.versions.toml · gradlew · gradle/wrapper/   (dev-backend)
├── README.md · .env (copié depuis assets/.env, gitignoré) · Dockerfile (si container)
├── app/                 # Application.kt · Composition · RunService · config/ · Models (généré) · Bounds · domain/   (dev-backend)
│   ├── tracing/         # helpers de spans + exporteur JSONL (observability/otel-genai-jvm.md)
│   ├── isolation/       # fixtures L4 (§8.1)
│   └── resources/       # application.yml · logback-spring.xml — racine du classpath
├── shared/              # types partagés posés par la pré-passe (dev-orchestration)
├── agents/{agent}/      # dev-agent — une instance par agent
├── orchestration/       # dev-orchestration
├── memory/              # dev-orchestration (interface en pré-passe)
├── tools/               # dev-tools
├── retrieval/           # dev-retrieval ; retrieval/migrations/ = Flyway du schéma rag
├── data/                # dev-data ; data/schemas/, data/views/, data/migrations/
├── serving/cli/         # dev-api — serving/cli-kotlin.md (serving/http/ si backend-api)
├── prompts/ · skills/ · rules/   # dev-prompt — embarqués tels quels
└── tests/{couche}/      # qa-tests + tests de couche — *Test.kt
```

### 4.1 Le build à plat

```kotlin
// build.gradle.kts — vérifié le 2026-09-26 (Gradle 9.5.0, JDK 21, Kotlin 2.3.21, Spring Boot 4.1.1) :
// jar produit, classes et ressources aux bons chemins, tests JUnit et Kotest découverts sous tests/
val layers = listOf("app", "agents", "tools", "data", "retrieval", "memory", "orchestration", "serving", "shared")

kotlin {
    jvmToolchain(21)
    sourceSets {
        main {
            kotlin.setSrcDirs(layers)
            kotlin.exclude("**/tests/**")
            resources.setSrcDirs(listOf("app/resources", "."))
            resources.include("application.yml", "logback-spring.xml", "app_config.json",
                              "prompts/**", "skills/**", "rules/**", "data/schemas/**",
                              "data/views/**", "data/migrations/**", "retrieval/migrations/**")
        }
        test {
            kotlin.setSrcDirs(listOf("tests"))
            resources.setSrcDirs(listOf("tests/resources"))
        }
    }
}
```

`kotlin { sourceSets { main { kotlin… } } }` : le `KotlinSourceSet` porte ses
propres `kotlin` et `resources` ; c'est la forme vérifiée avec le plugin Kotlin
2.3.21. `application.yml` à la racine du classpath (Spring Boot le trouve sans
réglage) ; prompts, skills, rules et schémas gardent leur chemin relatif dans
le jar.

### 4.2 Paquets et répertoires

Chaque fichier déclare `package {package}.{couche}[.{sous-couche}]`
(`package com.acme.shop.tools` dans `tools/OrdersLookup.kt`) ; le répertoire ne
reproduit pas le paquet. **Kotlin le permet** (la correspondance
répertoire/paquet est une recommandation de style, pas une règle du
compilateur) — vérifié : le build compile. `detekt` a une règle qui signale
l'écart (`InvalidPackageDeclaration`, jeu `naming` — non exécutée dans le build
de vérification, à confirmer au premier `./gradlew detekt`) : elle est
**désactivée** dans `detekt.yml`, avec cette raison en commentaire. `@SpringBootApplication(scanBasePackages = ["{package}"])`
dans `app/`, sinon seuls les beans de `{package}.app.*` sont trouvés.

---

## 5. Conventions imposées

### 5.1 Style

- `camelCase` / `PascalCase` / `SCREAMING_SNAKE_CASE` ; un fichier par classe
  publique, fichiers d'extensions nommés par sujet ;
- `data class` `val` pour tout modèle ; `sealed interface` pour les résultats
  nommés (`RunOutcome`) ; `value class` pour les identifiants ;
- injection par constructeur ; aucun singleton `object` porteur d'état
  (`object` sans état, pour une fonction pure comme `RrfFusion`, est permis) ;
- `suspend fun` pour l'I/O ; `runBlocking` seulement aux ponts documentés.

### 5.2 Interdits — vérifiés en L0

- `!!` sans justification écrite ; `lateinit var` hors tests ;
- `println`, `System.out` hors de `serving/cli/` ;
- `System.getenv` hors du démarrage (la config le lit une fois) ;
- Lombok ; `var` dans un modèle ;
- prompt littéral ; secret littéral ;
- `SNAPSHOT`, versions non épinglées dans le catalogue ;
- `/**` dans un KDoc (commentaire imbriqué jamais fermé) ;
- fichier sous `src/main/**` ou `src/test/**` (§4) ;
- `TODO`, `FIXME` dans le code livré.

---

## 6. Testing

| Niveau | Quoi | Outil |
|---|---|---|
| L0 | format, analyse statique | `./gradlew ktlintCheck detekt` |
| L1 | fonctions pures : fusion RRF, codes de sortie, sérialisation `RunEvent`, redaction, hash | JUnit Jupiter + AssertJ (ou Kotest), sans contexte Spring |
| L2 | contrats d'outils, enveloppe de données, MCP : happy, chaque erreur déclarée, timeout, auth KO | JUnit Jupiter, `MockRestServiceServer`, Testcontainers, MockK |

**Conventions de nommage — c'est ce que les scripts de gate lisent** (détail :
`eval/junit-eval.md` §3.1) :

1. **Un fichier de test se termine par `Test.kt`**, sous `tests/{couche}/`.
2. **Un test de contrat paramétré porte l'identifiant du cas dans son nom
   d'affichage** : `@ParameterizedTest(name = "{0}", quoteTextArguments = false)`
   — sans `quoteTextArguments = false`, JUnit 6 écrit `"happy"` entre
   guillemets dans le rapport (vérifié sur 6.0.3). En Kotest :
   `test("err-not-found") { … }`.
3. **Un test réseau vit dans une classe dont le nom contient `Network`** et
   porte `@Tag("network")` (Kotest : `tags(Tag("network"))`).

**Commande de test** — celle que `gen-app-context` recopie et que le framework
lance (le wrapper s'il existe, `gradle` sinon) :

```bash
cd workspace/src/{AppName}
./gradlew test                                  # L1/L2 hors réseau
./gradlew test -PincludeTags=network            # L2 réseau
```

```kotlin
// build.gradle.kts
tasks.test {
    useJUnitPlatform {
        val include = project.findProperty("includeTags") as String?
        if (include != null) includeTags(include) else excludeTags("network", "llm")
    }
    reports.junitXml.required = true
}
dependencies { testRuntimeOnly("org.junit.platform:junit-platform-launcher") }   // exigé par Gradle 9
```

**Rapport** : `workspace/src/{AppName}/build/test-results/test/TEST-*.xml`, un
fichier par classe (et par spec Kotest — vérifié : Kotest 6.2.5 s'exécute sur
la plateforme JUnit 6.0.3 de Spring Boot 4.1.1 et écrit au même endroit). Une
classe exclue par tag n'y apparaît pas : l'absence n'est pas un vert.

---

## 7. Commande de smoke

```bash
cd workspace/src/{AppName}
./gradlew ktlintCheck detekt build -x test
test -f build/libs/{AppName}.jar
./gradlew test
cd ../../..
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar --help
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar version --json
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar health --json
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar --option-inconnue; test $? -eq 2
```

Timeout : 240 s (première résolution Gradle comprise). Le jar est lancé depuis
la racine du dépôt, comme par les runners.

---

## 8. Contrat d'exécution

L'application Kotlin implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** : mêmes commandes (`run`, `resume`, `health`, `inspect`,
`trace`, `version`, plus `retrieve`), même protocole NDJSON `RunEvent`
(`event_schema: "1"`, `run_finished` toujours dernier), même table de codes de
sortie. Le transport est dans `serving/cli-kotlin.md`.

### 8.1 Les trois points de `serving/cli.md` §3.5

1. **Entrée par `stdin`** — `run --json --input-file -` lit l'entrée entière sur
   l'entrée standard (ligne de commande limitée, visible de `ps`, et une entrée
   commençant par `-` y serait lue comme une option).
2. **Isolement L4** — si `SDDA_EVAL_ISOLATION=mocked` est posée, l'application
   sert chaque outil depuis `SDDA_EVAL_FIXTURES/tools/{outil}.jsonl` et le
   retrieval figé depuis `SDDA_EVAL_FIXTURES/retrieval/{index}.jsonl`, **sans
   aucun appel réseau d'outil ni d'index** ; elle refuse de démarrer (code `8`,
   `[CONFIG_INVALID]`) si un outil n'a pas de fixture. Grammaire des lignes :
   celle de `templates/runtime/python/app/isolation.py` (`lang/java.md` §8.1).
3. **Retrieval sans agent (G4)** — `retrieve --json --index ID --query-file -
   [--k N]` lit la requête sur `stdin` et émet un événement `retrieval`
   (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished` ; `--k`
   remplace le `topK` du contrat pour l'appel. Aucun appel au modèle.

### 8.2 La commande

| Usage | Commande (depuis la racine du dépôt) |
|---|---|
| **Livrable — celle des runners** | `java -jar workspace/src/{AppName}/build/libs/{AppName}.jar …` |
| Développement | `cd workspace/src/{AppName} && ./gradlew run --args='run --json --input-file -'` |

`--executor cli` dérive exactement cette commande du langage actif (Kotlin et
Java partagent la même entrée de `LAUNCH_COMMANDS`, `sdda_lib/executors.py`) ;
`--executor cmd:<commande>` en impose une autre. Le build **doit** produire
ce nom de jar :

```kotlin
// build.gradle.kts
plugins {
    alias(libs.plugins.kotlin.jvm)          // org.jetbrains.kotlin.jvm, version kotlin du catalogue
    alias(libs.plugins.kotlin.spring)       // org.jetbrains.kotlin.plugin.spring
    alias(libs.plugins.spring.boot)
    application
}

application { mainClass = "{package}.app.ApplicationKt" }   // fonction main de premier niveau dans app/Application.kt

tasks.bootJar { archiveFileName = "{AppName}.jar" }         // le chemin du contrat, exactement
tasks.jar { enabled = false }                                 // pas de {AppName}-plain.jar à côté
tasks.named<JavaExec>("run") { standardInput = System.`in` } // --input-file - en dev
```

`bootJar` plutôt que `installDist` ou `shadowJar` : le contrat lance **un
jar**, et Spring Boot ne lit ses auto-configurations que depuis son propre
format de jar. `installDist` produit un script de lancement que
`LAUNCH_COMMANDS` ne connaît pas.

### 8.3 Spring Boot, `stdout` et le code de sortie

Les réglages de `lang/java.md` §8.3 s'appliquent tels quels — **vérifiés en
Kotlin le 2026-09-26** : sans eux, Spring Boot écrit ses journaux de démarrage
sur `stdout` avant le premier événement.

```yaml
# app/resources/application.yml
spring:
  main:
    banner-mode: off
    web-application-type: none      # cli-exe ; serving/spring-sse.md le choisit au démarrage pour backend-api
    log-startup-info: false
```

`app/resources/logback-spring.xml` envoie **tout** sur `System.err`.

```kotlin
// app/Application.kt — package {package}.app
@SpringBootApplication(scanBasePackages = ["{package}"])
class Application

fun main(args: Array<String>) {
    val app = SpringApplication(Application::class.java)
    app.setAddCommandLineProperties(false)                // les options de la CLI ne sont pas des propriétés Spring
    exitProcess(SpringApplication.exit(app.run(*args)))   // code rendu par l'ExitCodeGenerator de la CLI, APRÈS fermeture du contexte
}
```

**clikt et le code `2`.** Une erreur d'usage clikt (`NoSuchOption`,
`MissingArgument`…) porte `statusCode = 1` par défaut — **vérifié** sur clikt
5.1.0 : une option inconnue sortait en `1`, là où `serving/cli.md` §3.3 réserve
`2`. La CLI Kotlin appelle donc `parse(args)` (et non `main(args)`, qui
appelle `exitProcess` avant la fermeture du contexte) et convertit :

```kotlin
code = try {
    root.parse(args.toList()); outcomeCode          // code issu d'ExitCodes.of(outcome)
} catch (e: PrintHelpMessage) {
    echoHelp(e); 0
} catch (e: UsageError) {
    echoUsage(e); ExitCodes.USAGE                   // 2, et non le statusCode = 1 de clikt
} catch (e: CliktError) {
    e.statusCode
}
```

### 8.4 Workspace et secrets

Comme `lang/java.md` §8.4 : répertoire courant = racine du dépôt,
environnement réduit (`BASE_ENV`), workspace = `SDDA_WORKSPACE_ROOT` sinon
déduit du jar (`build/libs` → quatre parents), secrets = environnement puis
`workspace/src/{AppName}/.env` (qui complète sans écraser), lu par la
configuration au démarrage, jamais par `spring.config.import`. Traces dans
`{workspace}/.sys/traces/runs/{run_id}.jsonl`.

---

## 9. Pièges connus

1. **Journaux Spring sur `stdout`.** Constaté : trois lignes `INFO … Starting`
   avant le NDJSON. `banner-mode: off` + Logback sur stderr (§8.3).
2. **Usage clikt en `1`.** Constaté : remapper `UsageError` sur `2` (§8.3).
3. **`/**` dans un KDoc.** `/api/v1/**` ouvre un commentaire imbriqué jamais
   fermé → `Unclosed comment` à la compilation. Écrire `/api/v1/[...]`.
4. **Deux sérialiseurs.** Jackson (Spring) et `kotlinx.serialization` dans le
   même projet produisent deux formes du même `RunEvent`. Un seul.
5. **Jackson 2 par réflexe.** `com.fasterxml.jackson.module:jackson-module-kotlin`
   est Jackson 2 ; Spring Boot 4 est sur `tools.jackson.module` (Jackson 3).
6. **Temps de démarrage JVM + Spring.** 1,5 à 5 s mesurés par invocation sur un
   poste de développement ; le runner l'additionne à chaque item.
7. **Disposition Maven par réflexe.** `src/main/kotlin/…` est refusé par les
   hooks d'ownership et ne serait pas compilé (les racines sont les couches).
8. **`detekt` et Kotlin 2.3.** detekt 1.23.8 embarque son propre analyseur ; une
   syntaxe récente peut produire de faux positifs — detekt 2.x n'est publié
   qu'en alpha. Signaler plutôt que désactiver une règle entière.
9. **Aucun générateur de squelette.** `dev-backend` écrit `Application`,
   `Config`, `Bounds`, `Models` et la CLI depuis cette fiche et
   `serving/cli-kotlin.md`.
