# Stack: java (lang)

Stack ID: lang-java
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: java
Scope: langage et runtime de l'application agentic générée en **Java** sur la JVM — JDK, outil de build, disposition de projet **à plat par couche**, conventions transverses, commande de test et rapport JUnit XML, **contrat d'exécution** (la commande que les runners du framework lancent). Pendant exact de `lang/kotlin.md` : mêmes bibliothèques (Spring AI, Spring Boot, JUnit), même disposition, même livrable ; seuls les idiomes du langage changent. Le framework agentic est hors périmètre → `framework/spring-ai.md`. Pas de `.libs.json` : les pins vivent dans les catalogues des fiches JVM actives (`framework/spring-ai.libs.json`, `vectorstore/pgvector-jvm.libs.json`, `eval/junit-eval.libs.json`, `observability/otel-genai-jvm.libs.json`), qui servent Kotlin et Java à la fois.

---

## 1. Rôle et périmètre

Cette fiche fixe le **socle Java** des fiches JVM partagées
(`framework/spring-ai.md`, `rag/hybrid-jvm.md`, `vectorstore/pgvector-jvm.md`,
`dataaccess/view-per-agent-jvm.md`, `tools/mcp-jvm.md`, `eval/junit-eval.md`,
`observability/otel-genai-jvm.md`, `backend/kotlin-spring-boot.md`) et de la
surface `serving/cli-java.md`. Elle décide :

- la version du JDK et l'outil de build ;
- l'outillage déterministe L0/L1 (format, analyse statique, tests), sans token ;
- la disposition du projet — **la même qu'en Kotlin et qu'en Python** : un
  répertoire par couche d'ownership à la racine de l'application ;
- les conventions qui rendent les principes du framework vérifiables : aucun
  prompt inline (P1), aucun secret lu hors du démarrage, toute borne typée
  (P12), aucune valeur `null` qui traverse une frontière sans être dite ;
- la commande que les runners d'évaluation lancent (§8).

> **Pourquoi Java et Kotlin partagent tout sauf cette fiche.** Spring AI,
> Spring Boot, pgvector-java, le SDK MCP Java sont écrits en Java et exposés
> tels quels à Kotlin. Deux jeux de fiches pour un même écosystème feraient
> deux vérités sur les mêmes pins. Ce qui diffère vraiment — nullabilité,
> immuabilité, concurrence, sérialisation — tient dans la fiche de langage.

> **Ce qui change par rapport à Kotlin.** Java n'a pas de null-safety dans le
> système de types : la frontière est tenue par **JSpecify** (`@NullMarked` au
> niveau du package, `@Nullable` explicite) vérifié par **NullAway** à la
> compilation. Sans ce couple, un `null` issu d'un outil ou d'une source
> déclarée traverse l'agent jusqu'au modèle sans que rien ne le signale — ce que
> Kotlin refuse par construction. Java n'a pas non plus de coroutines : l'I/O
> concurrente passe par les **threads virtuels** (JDK 21) ;
> `StructuredTaskScope` n'est pas utilisé (préversion en JDK 21).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `lang-java` |
| **Langage** | Java **21** (niveau de langage = niveau du JDK) — `-Xlint:all -Werror`, `-parameters` |
| **Runtime** | JDK **21 LTS** — `java { toolchain { languageVersion = JavaLanguageVersion.of(21) } }` |
| **Build** | **Gradle, Kotlin DSL** (`build.gradle.kts` + `settings.gradle.kts` à la racine de l'application), wrapper versionné (`./gradlew`), catalogue `gradle/libs.versions.toml` — une seule version par artefact |
| **Plugins Gradle** | `java`, `application`, `org.springframework.boot`, `net.ltgt.errorprone`, `com.diffplug.spotless` |
| **Format / statique** | Spotless (`google-java-format`) + Error Prone + NullAway — L0 |
| **Nullabilité** | JSpecify : `@NullMarked` dans chaque `package-info.java`, `@Nullable` sur tout ce qui peut manquer |
| **Tests** | JUnit Jupiter (JUnit 6), AssertJ, `spring-boot-starter-test` — L1/L2 ; conventions de nommage imposées au §6 |
| **Sérialisation** | Jackson 3 (`tools.jackson.*`), celui de Spring Boot 4 — un seul sérialiseur par projet |
| **Concurrence** | threads virtuels (`spring.threads.virtual.enabled=true`, `Executors.newVirtualThreadPerTaskExecutor()`) ; jamais `parallelStream()` pour de l'I/O |
| **Logs** | SLF4J + Logback (fournis par Spring Boot), **tout sur stderr** (§8.3) |
| **Racine** | `workspace/src/{AppName}/` — un répertoire par couche (`app/`, `agents/`, `tools/`…, §4), tests sous `tests/` |

Les versions (JDK, Gradle, Spring, JUnit, plugins) sont celles des catalogues
JVM ; cette fiche n'en porte aucune pour ne pas en porter une seconde.

### 2.1 Pourquoi Gradle et pas Maven

Maven est la voie la plus répandue en Java, et elle serait défendable seule.
Gradle est retenu pour trois raisons propres au framework :

1. **Un seul outil pour les deux langages JVM.** `lang/kotlin.md` impose Gradle
   Kotlin DSL ; les fiches partagées décrivent donc une seule chaîne de build,
   une seule commande de test (`./gradlew test`), un seul emplacement de
   rapport JUnit (`build/test-results/test/`) et un seul emplacement de jar
   (`build/libs/{AppName}.jar`). Deux outils doubleraient chaque smoke et chaque
   commande que `gen-app-context` recopie.
2. **La disposition à plat s'exprime en une déclaration.** Les répertoires de
   couche deviennent des racines de sources par `sourceSets` (§4.1) ; Maven
   n'accepte qu'**un** répertoire de sources principal sans greffon
   supplémentaire (`build-helper-maven-plugin`).
3. **Le nom du jar se fixe en une ligne** (`bootJar { archiveFileName }`), et
   c'est un contrat (§8) : les runners lancent un chemin exact.

Maven reste possible (`buildSystem: maven` existe dans le schéma des
catalogues), mais il exigerait une variante de chaque smoke, de chaque
commande de lancement et de la disposition : c'est une décision
d'architecture, donc un ADR, pas un réglage.

### 2.2 Init (idempotent)

```bash
if [ ! -f "workspace/src/{AppName}/settings.gradle.kts" ]; then
  mkdir -p workspace/src/{AppName}/{app/resources,agents,tools,data,retrieval,memory,orchestration,serving,shared,prompts,skills,rules,tests}
  cd workspace/src/{AppName}
  gradle wrapper --gradle-version {gradle}          # version du catalogue du framework
  # puis : settings.gradle.kts, build.gradle.kts (§4.1, §8.2), gradle/libs.versions.toml depuis les .libs.json actifs
fi
```

`gradle init --type java-application` n'est **pas** utilisé : il crée
`app/src/main/java/…` en sous-projet, c'est-à-dire exactement la disposition
Maven que la matrice d'ownership refuse (§4). Le squelette est écrit par
`dev-backend` depuis cette fiche.

---

## 3. Mapping des concepts SDD_Agents → idiomes Java

### 3.1 Chargement et hash des prompts

`PromptLoader.load(agent)` lit la ressource `classpath:prompts/{agent}.system.md`
— le fichier `workspace/src/{App}/prompts/{agent}.system.md` embarqué tel quel
dans le jar (§4.1) — calcule son hash et le compare au hash épinglé de l'IR.
Le hash est **exactement** celui de `.sdda/python/sdda_lib/hashing.py`
(`sha256_text`) : BOM retiré, `\r\n` et `\r` → `\n`, espaces et tabulations
de fin de ligne retirés, lignes vides finales retirées, un `\n` final, SHA-256
des octets UTF-8, préfixe `sha256:`. Les structures (schémas d'outils) se
hashent sur le JSON canonique de `canonical_json` : clés triées, séparateurs
`,` et `:` sans espace, non-ASCII non échappé. Une normalisation qui diffère
d'un octet rend toutes les baselines périmées en permanence
(`eval/pytest-eval.md` §7.10) : la classe `shared/Hashing.java` est testée en
L1 contre des vecteurs calculés par le script Python. Un prompt en chaîne
littérale — y compris en *text block* `"""` — est interdit (P1).

### 3.2 Bornes

```java
// app/Bounds.java — package {package}.app
public record Bounds(
        int maxIterations,
        int maxToolCalls,
        int maxDelegationDepth,
        Duration timeout,
        BigDecimal budgetUsd,
        OnBoundExceeded onBoundExceeded) {

    public Bounds {
        if (maxIterations <= 0 || maxToolCalls <= 0) {
            throw new IllegalArgumentException("[CONFIG_INVALID] bornes non positives");
        }
        Objects.requireNonNull(timeout, "timeout");
        Objects.requireNonNull(budgetUsd, "budgetUsd");
        Objects.requireNonNull(onBoundExceeded, "onBoundExceeded");
    }
}

public enum OnBoundExceeded { FAIL_EXPLICIT, DEGRADE, ESCALATE_HUMAN }
```

Chargées depuis `app_config.json` (projeté de l'IR). Une boucle qui ne reçoit
pas `Bounds` en paramètre est non bornée (P12). Le `record` rend la borne
immuable — une borne qu'un agent peut relever n'en est pas une.

### 3.3 Frontière de confiance

```java
// shared/Untrusted.java
public record Untrusted<T>(T value, String source) {
    public Untrusted { Objects.requireNonNull(value); Objects.requireNonNull(source); }
}
```

Toute sortie d'outil `trust: untrusted`, tout champ `@free-text`, tout
contenu de chunk est enveloppé **au parsing**. Java ne peut pas interdire
qu'on appelle `.value()` ; ce qui le rend visible est que les constructeurs de
message n'acceptent que `String` maîtrisé ou `Untrusted<?>` rendu par
`UntrustedRenderer` (balisage). Un `.value()` hors de `UntrustedRenderer` est
un finding de revue.

### 3.4 Résultats nommés

```java
public sealed interface RunOutcome permits RunOutcome.Final, RunOutcome.BoundExceeded,
        RunOutcome.Refused, RunOutcome.Interrupted, RunOutcome.Failed {
    record Final(JsonNode output, boolean degraded, List<Citation> citations) implements RunOutcome {}
    record BoundExceeded(String bound, long limit, long observed, OnBoundExceeded policy) implements RunOutcome {}
    record Refused(String errorClass) implements RunOutcome {}
    record Interrupted(String threadId, String reason) implements RunOutcome {}
    record Failed(String errorClass, String message) implements RunOutcome {}
}
```

`switch` exhaustif sur `RunOutcome` (JDK 21) : un résultat ajouté sans être
traité est une erreur de compilation, pas un code de sortie `1` en production.

---

## 4. Structure de fichiers générée

La matrice d'ownership (`.sdda/loader.yml`) attribue les zones d'écriture par
**répertoire de couche en minuscules, directement sous
`workspace/src/{AppName}/`**. La convention Maven/Gradle
`src/main/java/{package}/…`, `src/main/resources/**`, `src/test/java/**`
n'appartient à **aucun** agent : un fichier écrit là est refusé par les hooks.
Le build est donc configuré pour l'arbre à plat — et c'est le build qui
s'adapte, pas la matrice, parce que la matrice est ce qui empêche l'agent qui
écrit le code de toucher au prompt ou au jeu qui le juge.

```
workspace/src/{AppName}/
├── settings.gradle.kts · build.gradle.kts · gradle/libs.versions.toml · gradlew · gradle/wrapper/   (dev-backend)
├── README.md · .env (copié depuis assets/.env, gitignoré) · Dockerfile (si container)
├── app/                 # Application.java · Composition · RunService · config/ · Models (généré) · Bounds · domain/   (dev-backend)
│   ├── tracing/         # helpers de spans + exporteur JSONL (observability/otel-genai-jvm.md) — pendant de tracing.py, coquille
│   ├── isolation/       # fixtures L4 (§8.1) — pendant de isolation.py, coquille
│   └── resources/       # application.yml · logback-spring.xml — racine du classpath   (dev-backend)
├── shared/              # types partagés posés par la pré-passe (dev-orchestration)
├── agents/{agent}/      # dev-agent — une instance par agent
├── orchestration/       # dev-orchestration
├── memory/              # dev-orchestration (interface en pré-passe)
├── tools/               # dev-tools
├── retrieval/           # dev-retrieval ; retrieval/migrations/ = Flyway du schéma rag (vectorstore/pgvector-jvm.md)
├── data/                # dev-data ; data/schemas/ = schémas figés ; data/views/ + data/migrations/ (view-per-agent-jvm.md)
├── serving/cli/         # dev-api — serving/cli-java.md
├── prompts/ · skills/ · rules/   # dev-prompt — embarqués tels quels (classpath:prompts/…)
└── tests/{couche}/      # qa-tests + tests de couche — *Test.java
```

### 4.1 Le build à plat

```kotlin
// build.gradle.kts — vérifié le 2026-09-26 (Gradle 9.5.0, JDK 21, Spring Boot 4.1.1) : jar produit,
// classes et ressources aux bons chemins, tests découverts sous tests/
val layers = listOf("app", "agents", "tools", "data", "retrieval", "memory", "orchestration", "serving", "shared")

sourceSets {
    main {
        java.setSrcDirs(layers)
        java.exclude("**/tests/**")
        resources.setSrcDirs(listOf("app/resources", "."))
        resources.include("application.yml", "logback-spring.xml", "app_config.json",
                          "prompts/**", "skills/**", "rules/**", "data/schemas/**",
                          "data/views/**", "data/migrations/**", "retrieval/migrations/**")
    }
    test {
        java.setSrcDirs(listOf("tests"))
        resources.setSrcDirs(listOf("tests/resources"))
    }
}
```

Pourquoi `resources.setSrcDirs(listOf("app/resources", "."))` avec des
`include` : `application.yml` doit être à la **racine** du classpath pour que
Spring Boot le trouve sans réglage, tandis que prompts, skills, rules et
schémas gardent leur chemin relatif (`prompts/billing.system.md` dans le jar)
— un seul chemin pour le fichier source et la ressource, et deux fragments
`skills/x.md` et `rules/x.md` ne peuvent pas se masquer l'un l'autre.

### 4.2 Paquets et répertoires — un écart assumé

Chaque fichier déclare `package {package}.{couche}[.{sous-couche}]`
(`package com.acme.shop.tools;` dans `tools/OrdersLookup.java`), où `{package}`
est le paquet racine que `dev-backend` dérive de l'`AppName`. Le répertoire ne
reproduit donc **pas** le paquet (`tools/` et non `com/acme/shop/tools/`).

C'est permis, et c'est un choix, pas un oubli :

- **`javac` ne l'exige pas** pour les fichiers qu'on lui passe explicitement ;
  Gradle passe la liste complète des sources (il ne compile pas par
  `-sourcepath`). Vérifié : le build compile, Error Prone et NullAway actifs ;
- **Error Prone** a une vérification `PackageLocation` qui signalerait l'écart :
  elle est **désactivée explicitement** (`check("PackageLocation",
  CheckSeverity.OFF)`) pour ne pas dépendre de son défaut ;
  ```kotlin
  // build.gradle.kts — vérifié le 2026-09-26 (Error Prone 2.50.0, NullAway 0.14.2, plugin net.ltgt.errorprone 5.1.1)
  import net.ltgt.gradle.errorprone.CheckSeverity
  import net.ltgt.gradle.errorprone.errorprone      // sans cet import, `options.errorprone { }` ne compile pas

  dependencies {
      errorprone(libs.error.prone.core)
      errorprone(libs.nullaway)
      implementation(libs.jspecify)
  }
  tasks.withType<JavaCompile>().configureEach {
      options.compilerArgs.addAll(listOf("-Xlint:all", "-Werror", "-parameters"))   // -Werror : non inclus dans le build de vérification
      options.errorprone {
          check("NullAway", CheckSeverity.ERROR)
          option("NullAway:OnlyNullMarked", "true")
          check("PackageLocation", CheckSeverity.OFF)   // disposition à plat, choix documenté ci-dessus
      }
  }
  ```
- **les IDE avertissent** (« package name does not correspond to the file
  path ») : l'avertissement est accepté ; le marquer comme racine de sources
  (ce que l'import Gradle fait) suffit à la navigation.

L'alternative — des paquets nommés d'après la couche seule (`package tools;`)
— alignerait répertoire et paquet, mais mettrait des paquets de premier niveau
`app`, `data`, `tools` sur le classpath, où ils peuvent entrer en collision
avec ceux d'une dépendance, et obligerait `@SpringBootApplication` à scanner
des paquets racine. Un avertissement d'IDE coûte moins qu'une collision de
classes.

`@SpringBootApplication(scanBasePackages = "{package}")` dans `app/` : la
classe d'application vit dans `{package}.app`, et sans `scanBasePackages` Spring
ne scannerait que `{package}.app.*` — les beans de `tools/` et `agents/` ne
seraient jamais trouvés.

---

## 5. Conventions imposées

### 5.1 Style

- `PascalCase` pour les types, `camelCase` pour les membres,
  `SCREAMING_SNAKE_CASE` pour les constantes ; un fichier par type public ;
- `record` pour tout modèle d'échange et toute valeur ; `sealed interface` pour
  les résultats nommés ; aucun setter sur un modèle ;
- injection **par constructeur** uniquement (un seul constructeur, sans
  `@Autowired`) ;
- `Optional` en valeur de retour seulement, jamais en paramètre ni en champ ;
- `var` local autorisé quand le type se lit à droite.

### 5.2 Interdits — vérifiés en L0

- Lombok (les `record` le rendent inutile, et il masque aux analyseurs ce que
  le compilateur voit) ;
- `System.out`, `System.err.println`, `printStackTrace()` hors de
  `serving/cli/` — `stdout` est le canal NDJSON (`serving/cli-java.md`) ;
- `System.getenv` hors de la configuration de démarrage ;
- champ non `final` dans un bean Spring ; `@Autowired` sur un champ ;
- prompt littéral (chaîne ou *text block*) ; secret littéral ;
- `catch (Exception e) {}` vide ; `catch (Throwable …)` ;
- `SNAPSHOT`, version absente du catalogue ;
- fichier sous `src/main/**` ou `src/test/**` (§4) ;
- `TODO`, `FIXME` dans le code livré.

---

## 6. Testing

| Niveau | Quoi | Outil |
|---|---|---|
| L0 | format, analyse statique, nullabilité | `./gradlew spotlessCheck compileJava` (Error Prone + NullAway échouent la compilation) |
| L1 | fonctions pures : fusion RRF, table des codes de sortie, sérialisation `RunEvent`, redaction | JUnit Jupiter + AssertJ, sans Spring (`new`, pas de contexte) |
| L2 | contrats d'outils, enveloppe de données, MCP : happy, chaque erreur déclarée, timeout, auth KO | JUnit Jupiter, `MockRestServiceServer`, Testcontainers PostgreSQL |

**Conventions de nommage — c'est ce que les scripts de gate lisent** :

1. **Un fichier de test se termine par `Test.java`** (`OrdersLookupTest.java`),
   sous `tests/{couche}/`.
2. **Un test de contrat paramétré porte l'identifiant du cas dans son nom
   d'affichage** : `@ParameterizedTest(name = "{0}", quoteTextArguments = false)`,
   le premier argument étant l'id du cas (`happy`, `err-not-found`, `timeout`,
   `auth-failed`). Le rapport XML porte alors `<testcase name="err-not-found">`,
   que G3 rapproche des erreurs déclarées du contrat. `quoteTextArguments =
   false` est nécessaire : en JUnit 6, `{0}` met un argument texte entre
   guillemets (`"err-not-found"`) — vérifié le 2026-09-26 sur JUnit 6.0.3.
3. **Un test qui exige une connectivité vit dans une classe dont le nom
   contient `Network`** (`OrdersLookupNetworkTest.java`) **et** porte
   `@Tag("network")`. Le nom rend le partage visible sans ouvrir le fichier ;
   le tag est ce que Gradle filtre. L'un sans l'autre est un finding de revue.

```java
// tests/tools/OrdersLookupTest.java
class OrdersLookupTest {
    @ParameterizedTest(name = "{0}", quoteTextArguments = false)
    @MethodSource("{package}.tools.ContractCases#ordersLookup")
    void contract(String caseId, ContractCase c) { /* happy, chaque erreur déclarée, timeout, auth KO */ }
}

// tests/tools/OrdersLookupNetworkTest.java
@Tag("network")
class OrdersLookupNetworkTest {
    @Test void liveConnectivity() { /* connectivité réelle — seconde moitié de la TOOL GATE */ }
}
```

**Commande de test** — celle que `gen-app-context` recopie et que le framework
lance (le wrapper s'il existe, `gradle` sinon) :

```bash
cd workspace/src/{AppName}
./gradlew test                                  # L0 compilé + L1/L2 hors réseau
./gradlew test -PincludeTags=network            # L2 réseau (Testcontainers, serveurs MCP réels)
```

```kotlin
// build.gradle.kts — la sélection par tag est déclarée, pas tapée à la main
tasks.test {
    useJUnitPlatform {
        val include = project.findProperty("includeTags") as String?
        if (include != null) includeTags(include) else excludeTags("network", "llm")
    }
    reports.junitXml.required = true
}
dependencies { testRuntimeOnly("org.junit.platform:junit-platform-launcher") }   // exigé par Gradle 9
```

**Rapport** : Gradle écrit un fichier JUnit XML par classe de test dans
`workspace/src/{AppName}/build/test-results/test/TEST-*.xml` — le format que
lit `parse_junit`, celui du `--junitxml` de pytest. Une classe exclue par tag
n'y apparaît **pas du tout** (vérifié) : un test `network` exclu n'est pas
« vert », il est absent, et c'est à l'outillage de le compter comme tel.

---

## 7. Commande de smoke

```bash
cd workspace/src/{AppName}
./gradlew spotlessCheck build -x test          # L0 : format, Error Prone, NullAway, jar produit
test -f build/libs/{AppName}.jar                # le chemin exact du contrat d'exécution (§8)
test -z "$(find . -path ./build -prune -o -path '*/src/main/*' -print -o -path '*/src/test/*' -print)"
./gradlew test                                  # L1/L2 hors réseau
cd ../../..
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar --help
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar version --json
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar health --json
```

Timeout : 240 s (première résolution Gradle et démarrage Spring compris). Le
smoke lance le jar **depuis la racine du dépôt**, comme les runners : c'est là
que se résolvent `workspace/.sys/traces/` et le `.env` (§8.4).

---

## 8. Contrat d'exécution

L'application Java implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** : mêmes commandes (`run`, `resume`, `health`, `inspect`,
`trace`, `version`, plus `retrieve`), même protocole NDJSON `RunEvent`
(`event_schema: "1"`, `run_finished` toujours dernier), même table de codes de
sortie (`0`…`11`, `130`). Le transport est dans `serving/cli-java.md` ; ce qui
suit est ce que les runners supposent.

### 8.1 Les trois points de `serving/cli.md` §3.5

1. **Entrée par `stdin`** — `run --json --input-file -` lit l'entrée entière sur
   l'entrée standard. Pas en argument : la ligne de commande est limitée
   (32 767 caractères sous Windows), visible de `ps`, et une entrée qui
   commence par `-` y est lue comme une option.
2. **Isolement L4** — si la variable `SDDA_EVAL_ISOLATION=mocked` est posée,
   l'application sert chaque outil depuis `SDDA_EVAL_FIXTURES/tools/{outil}.jsonl`
   (une réponse enregistrée par ligne) et le retrieval figé depuis
   `SDDA_EVAL_FIXTURES/retrieval/{index}.jsonl`, **sans aucun appel réseau
   d'outil ni d'index**. L'application refuse de démarrer (code `8`,
   `[CONFIG_INVALID]`) si l'isolement est demandé et qu'un outil n'a pas de
   fixture.
3. **Retrieval sans agent (G4)** — `retrieve --json --index ID --query-file -
   [--k N]` lit la requête sur `stdin` et émet un événement `retrieval`
   (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished`. `--k` est
   facultatif (défaut : le `topK` du contrat de retrieval) ; le runner le passe
   pour mesurer recall@k au k de l'AC. Aucun appel au modèle.

La grammaire des fixtures est celle du runtime Python
(`templates/runtime/python/app/isolation.py`) : une ligne `{"tool", "args",
"result"}` ou `{"tool", "args", "error": {"code", "message"}}`, une ligne sans
`args` pour la réponse par défaut ; un appel qu'aucune ligne ne couvre rend
l'erreur `TOOL_FIXTURE_MISSING`, jamais une réponse vide. Côté retrieval :
`{"query" | "query_hash", "results": [{id, score}]}` ou `result_ids[]` +
`scores[]`. Deux langages qui liraient deux grammaires feraient deux L4.

### 8.2 La commande

| Usage | Commande (depuis la racine du dépôt) |
|---|---|
| **Livrable — celle des runners** | `java -jar workspace/src/{AppName}/build/libs/{AppName}.jar …` |
| Développement | `cd workspace/src/{AppName} && ./gradlew run --args='run --json --input-file -'` |

Les runners (`eval-runner`, `run-retrieval-eval`, `run-adversarial-suite`)
l'invoquent de deux façons :

- `--executor cli` — la commande est dérivée du langage actif
  (`## Active Language & Runtime`) : pour Java comme pour Kotlin, c'est
  **exactement** `java -jar workspace/src/{AppName}/build/libs/{AppName}.jar`
  (`sdda_lib/executors.py`, `LAUNCH_COMMANDS`) ;
- `--executor cmd:<commande>` — la commande est imposée, par exemple
  `--executor "cmd:java -Xmx1g -jar workspace/src/Shop/build/libs/Shop.jar"`.

Le build **doit** donc produire ce nom de jar, sans version ni suffixe. Avec le
plugin Spring Boot, le jar exécutable est le `bootJar` :

```kotlin
// build.gradle.kts
plugins {
    java
    application
    alias(libs.plugins.spring.boot)
    alias(libs.plugins.errorprone)
}

application { mainClass = "{package}.app.Application" }

tasks.bootJar { archiveFileName = "{AppName}.jar" }   // le chemin du contrat, exactement
tasks.jar { enabled = false }                           // pas de {AppName}-plain.jar à côté : un seul jar à lancer

tasks.named<JavaExec>("run") { standardInput = System.`in` }   // --input-file - en dev
```

Pourquoi `bootJar` et non `shadowJar` : Spring Boot lit ses propres
métadonnées (`META-INF/spring/*.imports`) depuis son format de jar imbriqué ;
un jar « à plat » fusionne ces fichiers et perd des auto-configurations sans
erreur. Pourquoi `tasks.jar { enabled = false }` : un second jar dans
`build/libs/` est celui qu'un humain lance par erreur, et il ne démarre pas.
Pourquoi `standardInput` : la tâche `run` du plugin `application` ne transmet
pas `stdin` par défaut ; sans elle, `--input-file -` attend en développement
une entrée qui n'arrive jamais alors que le jar fonctionne — une différence
entre ce qu'on teste et ce qu'on livre.

### 8.3 Ce que Spring Boot ne doit pas faire à la CLI

Par défaut, Spring Boot écrit sa bannière **et ses journaux sur `stdout`**
(constaté le 2026-09-26 : trois lignes `INFO … Starting Application` avant le
premier événement). Le runner lit `stdout` comme du NDJSON : il faut donc les
deux réglages suivants, et un test L1 qui vérifie que chaque ligne de
`stdout` parse en `RunEvent`.

```yaml
# app/resources/application.yml
spring:
  main:
    banner-mode: off                 # la bannière s'écrit sur stdout
    web-application-type: none       # cli-exe : aucun serveur embarqué, démarrage plus court
    log-startup-info: false
  threads.virtual.enabled: true
```

```xml
<!-- app/resources/logback-spring.xml : TOUT le journal sur stderr -->
<configuration>
  <appender name="STDERR" class="ch.qos.logback.core.ConsoleAppender">
    <target>System.err</target>
    <encoder class="net.logstash.logback.encoder.LogstashEncoder"/>
  </appender>
  <root level="INFO"><appender-ref ref="STDERR"/></root>
</configuration>
```

```java
// app/Application.java — package {package}.app
@SpringBootApplication(scanBasePackages = "{package}")
public class Application {
    public static void main(String[] args) {
        SpringApplication app = new SpringApplication(Application.class);
        app.setAddCommandLineProperties(false);   // `--tenant x` n'est pas une propriété Spring
        System.exit(SpringApplication.exit(app.run(args)));   // code rendu par l'ExitCodeGenerator de la CLI
    }
}
```

`setAddCommandLineProperties(false)` : sans lui, Spring transforme chaque
`--option=valeur` de la CLI en propriété de configuration — une option
utilisateur pourrait surcharger un réglage. `System.exit` n'est appelé
qu'**après** `SpringApplication.exit`, qui ferme le contexte — donc le
`TracerProvider` et son `forceFlush` (`observability/otel-genai-jvm.md` §5).

### 8.4 Où l'application trouve son workspace et ses secrets

Le runner lance le jar avec pour répertoire courant **la racine du dépôt** et
un environnement réduit (`sdda_lib/executors.py`, `BASE_ENV` : `PATH`,
`JAVA_HOME`, `SDDA_WORKSPACE_ROOT`, `SDDA_TENANT_ID`, les variables
d'isolement — aucune clé d'API). D'où, dans cet ordre :

1. **Workspace** : `SDDA_WORKSPACE_ROOT` s'il est posé ; sinon, déduit de
   l'emplacement du jar (`ApplicationHome` de Spring Boot :
   `…/workspace/src/{AppName}/build/libs` → quatre parents → `workspace/`) ;
   sinon `./workspace` sous le répertoire courant. Les traces vont dans
   `{workspace}/.sys/traces/runs/{run_id}.jsonl`.
2. **Secrets** : l'environnement du processus d'abord, puis le fichier
   `workspace/src/{AppName}/.env` (deux parents au-dessus du jar), qui
   complète sans jamais écraser. Même ordre que le runtime Python
   (`templates/runtime/python/app/config.py`) : en production l'orchestrateur
   injecte, et un `.env` resté dans une image ne doit pas pouvoir remplacer ce
   qu'il a injecté.

Le `.env` est lu par la classe de configuration au démarrage, jamais par
`spring.config.import` : Spring en ferait des propriétés visibles de tout
`@Value` (et de `/actuator/env` en `backend-api`), alors qu'un secret ne doit
exister qu'en un point (`SecretValue`, dont `toString()` rend `[REDACTED]`).

---

## 9. Pièges connus

1. **Bannière et logs sur `stdout`.** Le défaut de Spring Boot (§8.3) : le
   runner ne lit alors aucun événement et conclut à « sortie vide ».
2. **Jar versionné.** Le défaut de `bootJar` est `{AppName}-{version}.jar` ;
   `--executor cli` échoue alors avec « Unable to access jarfile ». Le nom est
   fixé dans `build.gradle.kts` et vérifié par le smoke (§7).
3. **Disposition Maven par réflexe.** Un agent qui crée `src/main/java/…` est
   refusé par les hooks d'ownership ; un fichier qui y serait posé par un
   script ne serait de toute façon pas compilé (les racines sont les couches).
4. **`scanBasePackages` oublié.** L'application démarre, mais aucun outil ni
   agent n'est un bean : `health` échoue sur « outils ≠ contrats ».
5. **`null` à la frontière.** Un champ JSON absent désérialisé en `null`
   dans un `record` passe la compilation. NullAway ne voit que le code, pas les
   données : les modèles d'entrée sont validés contre leur schéma (généré de
   l'IR) **avant** la construction du `record`.
6. **Threads virtuels et `synchronized`.** Un bloc `synchronized` autour d'un
   appel réseau épingle le thread porteur (JDK 21). `ReentrantLock` autour de
   l'I/O ; le symptôme est un débit qui s'effondre sous charge sans erreur.
7. **Temps de démarrage.** Le runner additionne le démarrage JVM + Spring à
   chaque item (2 à 5 s mesurés sur un poste de développement pour un contexte
   minimal). Le mesurer au smoke et le déclarer dans le budget de latence
   plutôt que le découvrir en G6.
8. **`text block` pris pour une exception à P1.** Un prompt en `"""…"""`
   reste un prompt inline : non hashé, non épinglé, invisible de la revue.
9. **Aucun générateur de squelette.** `gen-app-skeleton` est Python :
   `dev-backend` écrit `Application`, `Config`, `Bounds`, `Models` et le build
   depuis cette fiche et `serving/cli-java.md`.
