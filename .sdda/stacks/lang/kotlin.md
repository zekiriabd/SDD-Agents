# Stack: kotlin (lang)

Stack ID: lang-kotlin
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin
Scope: langage et runtime de l'application agentic générée (outillage, structure de projet, conventions transverses) sur la JVM en Kotlin. Le framework agentic est hors périmètre → `framework/spring-ai.md`. **Réserve à lire d'abord** : cette fiche existe, mais aucune combo de bootstrap ne l'active — `eval/pytest-eval.md` et `observability/otel-genai.md` sont `[python]`, et aucun générateur de squelette JVM n'existe : `dev-backend` écrit le squelette depuis cette fiche. Une combo Kotlin exige d'abord une fiche `eval/` et une fiche `observability/` dans ce langage. Les pins Maven de cette famille sont `versionsVerified: false` (§7).

---

## 1. Rôle et périmètre

Cette fiche fixe **le socle JVM/Kotlin** des stacks Kotlin de SDD_Agents
(`framework/spring-ai.md`, `serving/cli-kotlin.md`,
`backend/kotlin-spring-boot.md`). Elle décide :

- la version du langage, du JDK et de l'outil de build ;
- l'outillage déterministe (format, analyse statique, tests) en L0/L1 sans
  token ;
- la **structure de projet agentic** ;
- les conventions qui rendent les principes du framework vérifiables : aucun
  prompt inline (P1), aucun secret par `System.getenv` hors démarrage, toute
  borne typée (P12), `!!` interdit.

> **Ce qui change par rapport à Python.** Comme en C#, le typage est appliqué à
> la compilation : une frontière de confiance déclarée en type (`Untrusted<T>`
> comme `value class`) est une erreur de build, pas une convention. La
> null-safety de Kotlin est le second garde-fou : `!!` est la seule façon de la
> contourner, et c'est pour cela qu'il est interdit.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `lang-kotlin` |
| **Langage** | Kotlin **2.x** (K2) — `-Xjsr305=strict`, `allWarningsAsErrors = true` |
| **Runtime** | JDK **21 LTS** — `toolchain { languageVersion = JavaLanguageVersion.of(21) }` |
| **Build** | Gradle **Kotlin DSL** (`build.gradle.kts`), wrapper versionné, catalogue `gradle/libs.versions.toml` — une seule version par artefact |
| **Format / statique** | `ktlint` (plugin `org.jlleitschuh.gradle.ktlint`) + `detekt` — L0 |
| **Tests** | Kotest (`kotest-runner-junit5`, `kotest-assertions-core`) — L1/L2 |
| **Sérialisation** | `jackson-module-kotlin` (Spring) ou `kotlinx.serialization` (hors Spring) — un seul par projet |
| **Async** | `kotlinx-coroutines-core` ; `runBlocking` réservé aux tests |
| **Logs** | `kotlin-logging` sur SLF4J, encodeur JSON |
| **Racine** | `workspace/src/{AppName}/` — code sous `src/main/kotlin/{package}/`, tests sous `src/test/kotlin/` |

> Les versions vivent dans `framework/spring-ai.libs.json` et
> `backend/kotlin-spring-boot.libs.json`. `kotlin` seul n'installe rien.

### 2.1 Init (idempotent)

```bash
if [ ! -f "workspace/src/{AppName}/settings.gradle.kts" ]; then
  mkdir -p workspace/src/{AppName}/src/main/kotlin/{package} workspace/src/{AppName}/src/test/kotlin
  cd workspace/src/{AppName}
  gradle init --type kotlin-application --dsl kotlin --project-name {AppName} --package {package} --no-split-project
  # puis : gradle/libs.versions.toml depuis le .libs.json actif ; wrapper versionné (./gradlew)
fi
```

---

## 3. Mapping des concepts SDD_Agents → idiomes Kotlin

### 3.1 Chargement et hash des prompts

`PromptLoader.load(agent)` lit `workspace/src/{App}/prompts/{agent}.system.md` (ou la
ressource embarquée dans le jar), calcule son SHA-256 et le compare au hash
épinglé dans le contrat. Un prompt en chaîne littérale est interdit (P1) ;
`detekt` signale toute chaîne de plus de 200 caractères dans un constructeur de
message.

### 3.2 Bornes

```kotlin
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
@JvmInline value class Untrusted<T>(val value: T)
```

Toute sortie d'outil `trust: untrusted` et tout champ `free_text` d'une source
déclarée est enveloppé **au parsing** ; le compilateur refuse de le passer là
où un `String` maîtrisé est attendu.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── settings.gradle.kts · build.gradle.kts · gradle/libs.versions.toml · gradlew
├── README.md · Dockerfile (si container)
├── src/main/kotlin/{package}/
│   ├── app/                 # Composition · RunService · config/ · Models (généré) · Bounds · domain/
│   ├── agents/{agent}/      # dev-agent
│   ├── orchestration/       # dev-orchestration
│   ├── tools/               # dev-tools
│   ├── retrieval/           # dev-retrieval
│   ├── data/                # dev-data (+ schemas/ en ressources)
│   └── serving/{cli,http}/  # dev-api
├── src/main/resources/      # application.yml · prompts embarqués · schemas/
└── src/test/kotlin/         # qa-tests + tests de couche
```

---

## 5. Conventions imposées

### 5.1 Style

- `camelCase` / `PascalCase` / `SCREAMING_SNAKE_CASE` ; un fichier par classe
  publique, fichiers d'extensions nommés par sujet ;
- `data class` `val` pour tout modèle ; `sealed interface` pour les résultats
  nommés (`RunOutcome`) ; `value class` pour les identifiants ;
- injection par constructeur ; aucun singleton `object` porteur d'état ;
- `suspend fun` pour l'I/O ; jamais `runBlocking` hors tests.

### 5.2 Interdits — vérifiés en L0

- `!!` sans justification écrite ; `lateinit var` hors tests ;
- `println`, `System.out` ;
- `System.getenv` hors du démarrage (la config le lit une fois) ;
- Lombok ; `var` dans un modèle ;
- prompt littéral ; secret littéral ;
- `SNAPSHOT`, versions non épinglées dans le catalogue ;
- `/**` dans un KDoc (commentaire imbriqué jamais fermé) ;
- `TODO`, `FIXME` dans le code livré.

---

## 6. Commande de smoke

```bash
cd workspace/src/{AppName}
./gradlew ktlintCheck detekt build
./gradlew test
```

Timeout : 180 s (première résolution Gradle comprise).

---

## 7. Pièges connus

1. **Les pins Maven ne sont pas vérifiés.** Au 2026-09-23, `search.maven.org`
   ne répondait que partiellement ; les catalogues Kotlin portent
   `versionsVerified: false` et le smoke du framework le dit en WARN. Résoudre
   les versions contre Maven Central **avant** le premier bootstrap réel, puis
   dater `verifiedAt`.
2. **`/**` dans un KDoc.** `/api/v1/**` ouvre un commentaire imbriqué jamais
   fermé → `Unclosed comment` à la compilation. Écrire `/api/v1/[...]`.
3. **Deux sérialiseurs.** Jackson (Spring) et `kotlinx.serialization` dans le
   même projet produisent deux formes du même `RunEvent`. Un seul.
4. **Temps de démarrage JVM sous `/readyz`.** Un indicateur qui répond `UP`
   avant que prompts et schémas soient chargés fait router du trafic vers un
   service pas prêt.
5. **Aucun générateur de squelette.** Comme en TypeScript : `dev-backend` écrit
   `Config`, `Bounds`, `Models` et la CLI depuis cette fiche et
   `serving/cli-kotlin.md`.
