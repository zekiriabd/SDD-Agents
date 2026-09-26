# Stack: kotlin-spring-boot (backend)

> §5 (Librairies) suit `kotlin-spring-boot.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: backend-kotlin-spring-boot
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin, java
Scope: la **maison HTTP** en **Spring Boot 4.1.1**, en Kotlin **ou en Java**, quand `DeliverableType: backend-api` et `ApiFramework: spring-boot` — projet Gradle Kotlin DSL en disposition à plat, injection par constructeur, `@ConfigurationProperties`, `@RestControllerAdvice`, Actuator, sécurité en ressource OAuth2 (Spring Security 7), packaging. La surface elle-même (routes, SSE, identité, OpenAPI dérivé) est `serving/spring-sse.md`. Hérité de SDD_Pro `backend/kotlin-spring-boot.md`, transposé : JPA, Flyway métier et le scaffolding Database-First sont retirés — aucune base possédée ; le contrat OpenAPI est **dérivé de l'IR**. Le nom de la fiche reste `kotlin-spring-boot` (c'est l'identifiant que `validate_packaging` associe à `ApiFramework: spring-boot`) ; elle sert les deux langages JVM. Suppose `lang/kotlin.md` ou `lang/java.md` et, pour le moteur, `framework/spring-ai.md`.

---

## 1. Rôle et périmètre

Spring Boot apporte à la coquille ce que la JVM sait faire de mieux : un
conteneur d'injection mûr, une configuration typée et profilée, des sondes de
santé et des métriques (Actuator, Micrometer) prêtes à l'emploi, une sécurité
qui établit l'identité au transport avant que le code applicatif ne s'exécute.
C'est la maison la plus complète du catalogue — et la plus lourde à démarrer.

Ce que la fiche garde de SDD_Pro : modèles immuables (`data class` / `record`),
injection par constructeur seulement, `!!` interdit en Kotlin, nullabilité
vérifiée en Java (JSpecify + NullAway), `@RestControllerAdvice` unique,
`application.yml` sans valeur secrète (`${ENV_VAR}`), L0 outillé, les pièges de
compilation documentés (KDoc `/**`, `AntPathRequestMatcher`). Ce qui est
retiré : `spring-boot-starter-data-jpa`, Flyway **métier**, `open-in-view`, les
entités — l'accès aux données passe par `dataaccess/`, jamais par un
`JpaRepository`.

**Pourquoi Spring Boot 4.1.1 et plus 3.5** : Spring AI 2.0.1 l'exige (POM
`spring-ai-autoconfigure-model-anthropic` 2.0.1 → `spring-boot-autoconfigure`
4.1.1, Spring Framework 7.0.9). Le catalogue précédent épinglait Boot 3.5.3
avec le groupe Jackson 3 (`tools.jackson.module`), que seul Boot 4 gère : une
résolution qui aurait échoué au premier build.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `backend-kotlin-spring-boot` |
| **Langage** | Kotlin 2.3 (`lang/kotlin.md`) ou Java 21 (`lang/java.md`), JDK 21 LTS |
| **Framework** | Spring Boot **4.1.1**, Spring Framework 7.0.9, Spring Security 7 |
| **Surface** | `serving/spring-sse.md` — Spring MVC + `SseEmitter` sur threads virtuels |
| **Build** | Gradle Kotlin DSL, plateforme `spring-boot-dependencies`, catalogue `gradle/libs.versions.toml`, wrapper versionné, disposition à plat (`lang/*.md` §4) |
| **Validation** | Bean Validation (`@Valid`) sur des modèles **générés** depuis l'IR |
| **Sérialisation** | Jackson 3 (`tools.jackson.*`) — `jackson-module-kotlin` 3 en Kotlin |
| **Config** | `@ConfigurationProperties` typées + `app/resources/application.yml` avec `${ENV_VAR}` ; profils `dev` / `prod` |
| **Logs** | Logback + `logstash-logback-encoder` 9 (Jackson 3), **sur stderr** |
| **Sondes** | Actuator : `/actuator/health/{liveness,readiness}` **mappés** sur `/healthz` `/readyz` |
| **Paramètres STACK.md** | `DeliverableType: backend-api`, `ApiFramework: spring-boot`, `ApiAuthMode`, `ApiContractFirst`, `ServingLocalPort`, `## Active Architecture Pattern` |
| **Smoke** | §8 |

---

## 3. Mapping couche → répertoire

Racine `workspace/src/{AppName}/`, un répertoire par couche d'ownership
(`lang/kotlin.md` §4, `lang/java.md` §4) ; extension `.kt` ou `.java` :

| Couche | Emplacement | Propriétaire |
|---|---|---|
| Bootstrap | `app/Application` (`@SpringBootApplication(scanBasePackages = "{package}")`) — choix CLI / `serve` au démarrage (`serving/spring-sse.md` §3.5) | `dev-backend` |
| Entrée HTTP | `serving/http/RunsController`, `InspectController`, `OpenApiController` | `dev-api` |
| Service | `app/RunService` · `app/usecases/` | `dev-backend` |
| Domaine | `app/domain/{contexte}/` — `Values`, `Rules`, `Ports` (`interface`) — **sans annotation Spring** | `dev-backend` |
| Composition | `app/CompositionConfig` (`@Configuration`, `@Bean`) — la seule classe qui instancie le moteur | `dev-backend` |
| Config | `app/config/AppProperties` (`@ConfigurationProperties`) · `app/resources/application.yml` · `app_config.json` | `dev-backend` |
| Modèle | `app/Models` (généré) · `data/schemas/` | `dev-backend` / `dev-data` |
| Transverse | `app/web/ProblemAdvice` (`@RestControllerAdvice`), `app/web/CorrelationFilter` | `dev-backend` |
| Sécurité | `app/security/SecurityConfig` — ressource OAuth2 (`ApiAuthMode: oauth2 \| azure-ad`), clé d'API, mTLS | `dev-backend` |
| Résilience | `app/Resilience` — Resilience4j, une politique nommée par dépendance | `dev-backend` |
| Tests | `tests/{couche}/…Test` (JUnit 6 ; Kotest optionnel en Kotlin) | `qa-tests` |
| Projet | `build.gradle.kts` · `settings.gradle.kts` · `gradle/libs.versions.toml` · `README.md` · `Dockerfile` | `dev-backend` |

Routes (détaillées par `serving/spring-sse.md` §3.1) : `POST /v1/runs` (JSON
ou `text/event-stream` via `SseEmitter`), `GET /v1/runs/{id}`,
`POST /v1/runs/{id}/resume`, `DELETE /v1/runs/{id}`, `/healthz`, `/readyz`,
`/openapi.json`.

---

## 4. Idiomes imposés

- **Modèles immuables** : `data class` à `val` (Kotlin), `record` (Java) ;
  jamais `var` dans un modèle, jamais de setter ; jamais Lombok.
- **Injection par constructeur** (`class RunsController(private val runs: RunService)` /
  un seul constructeur Java) ; `@Autowired` sur un champ est refusé.
- **Nullabilité** : `!!` interdit sauf justification écrite (Kotlin : `?:`,
  `let`, `requireNotNull`) ; `@NullMarked` + NullAway en Java.
- **Modèle d'exécution** : Spring MVC bloquant **sur threads virtuels**
  (`spring.threads.virtual.enabled: true`) — pas de WebFlux, pas de mélange ;
  en Kotlin, `runBlocking` seulement aux deux ponts documentés (point d'entrée
  CLI, `DocumentRetriever`).
- **Identité** : un `HandlerMethodArgumentResolver` construit `Identity` depuis
  l'`Authentication` établie par Spring Security ; un DTO qui la porte est
  refusé (`400`, `serving/spring-sse.md` §3.4).
- **Erreurs** : un seul `@RestControllerAdvice` → `ProblemDetail` depuis la
  table `serving/http/Status` (dérivée d'`ExitCodes`).
- **Config** : `@ConfigurationProperties` + `@Validated` ; un secret manquant
  arrête le démarrage avec son **nom** ; `System.getenv` interdit hors du
  démarrage.
- **Sécurité** : `requestMatchers("/healthz", "/readyz", "/openapi.json").permitAll()`
  en chemins littéraux (jamais `AntPathRequestMatcher`, retiré en Security 7) ;
  tout le reste authentifié dès que `ApiAuthMode` ≠ `none`.
- **Logs** : `KotlinLogging.logger {}` (Kotlin) / `LoggerFactory.getLogger` (Java),
  MDC `run_id`, `trace_id`, `tenant` haché ; jamais `println` / `System.out`.

```kotlin
// app/config/AppProperties.kt
@ConfigurationProperties("app")
@Validated
data class AppProperties(
    @field:NotBlank val llmApiKeyEnv: String,        // le NOM de la variable, jamais la valeur
    @field:Positive val maxInputBytes: Int = 262_144,
)
```

```java
// app/config/AppProperties.java
@ConfigurationProperties("app")
@Validated
public record AppProperties(
        @NotBlank String llmApiKeyEnv,                // le NOM de la variable, jamais la valeur
        @Positive int maxInputBytes) {}
```

---

## 5. Librairies

Source de vérité : `kotlin-spring-boot.libs.json` (BOM Spring Boot 4.1.1 ; les
versions individuelles sont celles du BOM sauf mention).

**CORE** (Kotlin et Java) : `spring-boot-starter-webmvc`,
`spring-boot-starter-actuator`, `spring-boot-starter-validation`,
`spring-boot-starter-security`, `spring-boot-starter-security-oauth2-resource-server`,
`logstash-logback-encoder` 9.0, `resilience4j-spring-boot4` 2.4.0 ; en test :
`spring-boot-starter-test`, `spring-boot-starter-webmvc-test`,
`spring-boot-starter-security-test`.

**ON-DEMAND** : `springdoc-openapi-starter-webmvc-ui` 3.1.1 (`openapi-ui` —
interface `/docs` pointée sur le fichier dérivé de l'IR, génération coupée),
`micrometer-registry-prometheus` (`metrics`), `jackson-module-kotlin` 3 et
`kotlin-reflect` (`lang-kotlin`), `kotest-extensions-spring` 6.2.5 (`kotest`).
L0 (ktlint, detekt, Error Prone, NullAway, Spotless) : `framework/spring-ai.libs.json`.
Observabilité : `observability/otel-genai-jvm.md`.

**Absents par conception** : `spring-boot-starter-data-jpa`, `flyway-core`
métier, `spring-boot-starter-webflux`, Lombok, MapStruct,
`resilience4j-spring-boot3`, `com.fasterxml.jackson.module:jackson-module-kotlin`
(Jackson 2).

---

## 6. Interdits

- `!!` non justifié ; `@Autowired` sur champ ; `var` ou setter dans un modèle ;
- `runBlocking` hors des deux ponts documentés ; `println` / `System.out` ; Lombok ;
- `hibernate.ddl-auto`, `open-in-view`, toute entité : pas de base possédée ;
- `AntPathRequestMatcher` ; `/**` dans un KDoc (commentaire imbriqué jamais
  fermé → erreur de compilation) ;
- secret dans `application*.yml` (toujours `${ENV_VAR}`) ; `System.getenv` hors
  démarrage ;
- logique métier dans un contrôleur ; appel de modèle hors `agents/` ;
- `try/catch` de formatage HTTP dans un contrôleur ;
- versions non épinglées dans le catalogue Gradle ; `SNAPSHOT` ; un module d'une
  famille gérée par le BOM surclassé à la main ;
- `springdoc` générant le contrat (`springdoc.api-docs.enabled` doit rester
  `false`) ; `/v3/api-docs` exposé ;
- fichier sous `src/main/**` ou `src/test/**` (disposition à plat) ;
- `TODO`, `FIXME`, code commenté.

---

## 7. Pièges connus

1. **Spring Boot 4 / Security 7.** `AntPathRequestMatcher` a disparu, plusieurs
   starters ont changé de nom (`webmvc`, `security-oauth2-resource-server`), le
   groupe Jackson est `tools.jackson`. Un exemple copié de la documentation
   Boot 3 compile rarement.
2. **Le KDoc `/**` + `/api/v1/**`.** Kotlin imbrique les commentaires : le
   `/**` d'un chemin ouvre un niveau jamais fermé. Écrire `/api/v1/[...]`.
3. **Temps de démarrage sous `/readyz`.** Actuator répond `UP` avant que la
   composition du moteur ait chargé prompts et schémas : brancher la
   disponibilité sur un `HealthIndicator` qui vérifie le `RunService`.
4. **Jackson et les modèles sans valeur par défaut.** Un champ manquant dans
   `RunRequest` devient une exception de désérialisation opaque : le corps est
   validé contre le schéma de l'IR **avant** Jackson, puis Bean Validation.
5. **Deux contrats.** `springdoc` génère un document depuis les contrôleurs ;
   l'IR en fournit un autre. Le second fait foi, le premier est coupé.
6. **Jackson 2 réintroduit par une dépendance.** Une bibliothèque tierce qui
   tire `com.fasterxml.jackson.*` fait coexister deux sérialiseurs ; vérifier
   `./gradlew dependencies` au smoke.
7. **Deux jars.** Le plugin Spring Boot produit `{AppName}-plain.jar` à côté du
   jar exécutable si `tasks.jar` n'est pas désactivé ; le contrat d'exécution
   lance un chemin exact.

---

## 8. Smoke

```bash
cd workspace/src/{AppName}
./gradlew ktlintCheck detekt build -x test      # Kotlin
./gradlew spotlessCheck build -x test           # Java
test -f build/libs/{AppName}.jar
cd ../../..
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar serve & PID=$!; sleep 25
curl -sf http://localhost:${ServingLocalPort}/healthz -o /dev/null
curl -sf http://localhost:${ServingLocalPort}/openapi.json > /tmp/openapi.json
kill -TERM $PID; wait $PID
python .sdda/sdda.py validate-api-contract --mission {n} --json
```

Timeout : 180 s (Gradle + démarrage Spring).

---

## 9. Contrat d'exécution

Le livrable `backend-api` implémente **aussi** la CLI de `serving/cli.md`
§3.1-3.3 **à l'identique** (commandes, NDJSON `RunEvent`, codes de sortie) —
c'est la surface que les runners d'évaluation lancent, le HTTP en est une
projection sur le même `RunService`. Les trois points de `serving/cli.md` §3.5 :

1. `run --json --input-file -` lit l'entrée sur `stdin` ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'application sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil) et le retrieval
   figé depuis le même dossier, sans aucun appel réseau d'outil ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished`.

Commande de lancement, depuis la racine du dépôt :
`java -jar workspace/src/{AppName}/build/libs/{AppName}.jar` (CLI — celle que
`--executor cli` dérive du langage actif, Kotlin comme Java) et
`java -jar workspace/src/{AppName}/build/libs/{AppName}.jar serve` (serveur) ;
en développement `./gradlew run --args='…'`. `--executor cmd:<commande>` impose
une autre commande. Le build produit exactement `build/libs/{AppName}.jar`
(`tasks.bootJar { archiveFileName = "{AppName}.jar" }`,
`tasks.jar { enabled = false }`).
