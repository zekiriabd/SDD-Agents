# Stack: kotlin-spring-boot (backend)

> §5 (Librairies) suit `kotlin-spring-boot.libs.json` — ce fichier seul fait foi pour les versions.

Stack ID: backend-kotlin-spring-boot
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin
Scope: la **maison HTTP** en Spring Boot 3.5 + Kotlin quand `DeliverableType: backend-api` et `ApiFramework: spring-boot` — projet Gradle Kotlin DSL, injection par constructeur, `@ConfigurationProperties`, `@RestControllerAdvice`, Actuator, sécurité en ressource OAuth2, packaging. Hérité de SDD_Pro `backend/kotlin-spring-boot.md`, transposé : JPA, Flyway et le scaffolding Database-First sont retirés — aucune base possédée ; le contrat OpenAPI est **dérivé de l'IR**, `springdoc` ne fait que le publier. Suppose `lang/kotlin.md` et, pour le moteur, `framework/spring-ai.md`.

---

## 1. Rôle et périmètre

Spring Boot apporte à la coquille ce que le JVM sait faire de mieux : un
conteneur d'injection mûr, une configuration typée et profilée, des sondes de
santé et des métriques (Actuator, Micrometer) prêtes à l'emploi, une sécurité
qui établit l'identité au transport avant que le code applicatif ne s'exécute.
C'est la maison la plus complète du catalogue — et la plus lourde à démarrer.

Ce que la fiche garde de SDD_Pro : data classes immuables, injection par
constructeur seulement, `!!` interdit, `@RestControllerAdvice` unique,
`application.yml` sans valeur secrète (`${ENV_VAR}`), `ktlint` + `detekt` en
L0, les pièges de compilation documentés (KDoc `/**`, `AntPathRequestMatcher`).
Ce qui est retiré : `spring-boot-starter-data-jpa`, Flyway, `open-in-view`,
les entités — l'accès aux données passe par les sources déclarées de
`dataaccess/`, jamais par un `JpaRepository`.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `backend-kotlin-spring-boot` |
| **Langage** | Kotlin 2.x sur JDK 21 LTS (`lang/kotlin.md`) |
| **Framework** | Spring Boot 3.5.x (ligne en place ; Spring Boot 4 est publié, la montée est une tâche dédiée — §7) |
| **Build** | Gradle Kotlin DSL, catalogue `gradle/libs.versions.toml`, wrapper versionné |
| **Validation** | Bean Validation (`@Valid`) sur des data classes **générées** depuis l'IR |
| **Config** | `@ConfigurationProperties` typées + `application.yml` avec `${ENV_VAR}` ; profils `dev` / `prod` |
| **Logs** | `kotlin-logging` sur SLF4J, encodeur JSON (`logstash-logback-encoder`) |
| **Sondes** | Actuator : `/actuator/health/{liveness,readiness}` **mappés** sur `/healthz` `/readyz` (contrat commun aux surfaces) |
| **Paramètres STACK.md** | `DeliverableType: backend-api`, `ApiFramework: spring-boot`, `ApiAuthMode`, `ApiContractFirst`, `## Active Architecture Pattern` |
| **Smoke** | §8 |

---

## 3. Mapping couche → répertoire

Racine `workspace/src/{AppName}/src/main/kotlin/{package}/` (cf. `lang/kotlin.md` §4) :

| Couche | Emplacement |
|---|---|
| Bootstrap | `{AppName}Application.kt` (`@SpringBootApplication`) |
| Entrée HTTP | `serving/http/RunsController.kt`, `HealthController.kt` (délègue à Actuator), `OpenApiController.kt` |
| Service | `app/RunService.kt` (`@Service`) · `app/usecases/` |
| Domaine | `app/domain/{contexte}/` — `Values.kt`, `Rules.kt`, `Ports.kt` (`interface`) — **sans annotation Spring** |
| Composition | `app/CompositionConfig.kt` (`@Configuration`, `@Bean`) — la seule classe qui instancie le moteur |
| Config | `app/config/AppProperties.kt` (`@ConfigurationProperties`) · `src/main/resources/application.yml` · `app_config.json` |
| Modèle | `app/Models.kt` (généré) · `data/schemas/` (ressources) |
| Transverse | `common/ProblemDetailsAdvice.kt` (`@RestControllerAdvice`), `common/IdentityFilter.kt`, `common/CorrelationFilter.kt` |
| Sécurité | `security/SecurityConfig.kt` — ressource OAuth2 (`ApiAuthMode: oauth2 | azure-ad`) |
| Résilience | `app/Resilience.kt` — Resilience4j, une politique nommée par dépendance |
| Tests | `src/test/kotlin/…` (Kotest + `spring-boot-starter-test`) |
| Projet | `build.gradle.kts` · `settings.gradle.kts` · `gradle/libs.versions.toml` · `README.md` · `Dockerfile` |

Routes : `POST /v1/runs` (JSON ou `text/event-stream` via `SseEmitter` /
WebFlux `Flux<ServerSentEvent>`), `GET /v1/runs/{id}`,
`POST /v1/runs/{id}/resume`, `/healthz`, `/readyz`, `/openapi.json`.

---

## 4. Idiomes imposés

- **Data classes `val`** pour tout modèle d'échange ; jamais `var` ; jamais
  Lombok (Kotlin l'a déjà).
- **Injection par constructeur** (`class RunsController(private val runs: RunService)`) ;
  `@Autowired` sur un champ est refusé.
- **`!!` interdit** sauf justification écrite ; `?:`, `let`, `requireNotNull`.
- **Coroutines** pour l'I/O (`suspend fun`, WebFlux) ou MVC bloquant assumé —
  pas les deux mélangés ; `runBlocking` réservé aux tests.
- Identité : `IdentityFilter` lit le `Authentication` établi par Spring
  Security et pose l'identité ; un DTO qui la porte est refusé.
- Erreurs : un seul `@RestControllerAdvice` → `ProblemDetail` (Spring 6
  natif) depuis la table `[CLASS] → statut`.
- Config : `@ConfigurationProperties` + `@Validated` ; un secret manquant
  arrête le démarrage avec le nom ; `System.getenv` interdit hors du démarrage
  Spring.
- Sécurité : `requestMatchers("/healthz", "/readyz", "/openapi.json").permitAll()`
  en chemins littéraux (jamais `AntPathRequestMatcher`, retiré en Security 7) ;
  tout le reste authentifié dès que `ApiAuthMode` ≠ `none`.
- Logs : `private val log = KotlinLogging.logger {}` ; MDC porte `run_id`,
  `trace_id`, `tenant` haché ; jamais `println`.

---

## 5. Librairies

Source de vérité : `kotlin-spring-boot.libs.json` (BOM Spring Boot ; les
versions individuelles sont celles du BOM sauf mention).

**CORE** : `spring-boot-starter-web` (ou `-webflux` si coroutines),
`spring-boot-starter-actuator`, `spring-boot-starter-validation`,
`spring-boot-starter-security`, `spring-boot-starter-oauth2-resource-server`,
`jackson-module-kotlin`, `kotlin-reflect`, `kotlinx-coroutines-core`,
`kotlin-logging-jvm`, `logstash-logback-encoder`,
`springdoc-openapi-starter-webmvc-ui` (publication), `resilience4j-spring-boot3`,
`spring-boot-starter-test`, `kotest-runner-junit5`, `kotest-assertions-core`,
`kotest-extensions-spring`, `ktlint` (plugin), `detekt` (plugin).

**ON-DEMAND** : `micrometer-registry-prometheus` (`metrics`),
`opentelemetry-spring-boot-starter` (`otel-http`).

**Absents par conception** : `spring-boot-starter-data-jpa`, `flyway-core`,
`hibernate`, pilotes JDBC, Lombok, MapStruct.

---

## 6. Interdits

- `!!` non justifié ; `@Autowired` sur champ ; `var` dans un modèle ;
- `runBlocking` hors tests ; `println` ; Lombok ;
- `hibernate.ddl-auto`, `open-in-view`, toute entité : pas de base possédée ;
- `AntPathRequestMatcher` ; `/**` dans un KDoc (commentaire imbriqué jamais
  fermé → erreur de compilation) ;
- secret dans `application*.yml` (toujours `${ENV_VAR}`) ; `System.getenv` hors
  démarrage ;
- logique métier dans un contrôleur ; appel de modèle hors `agents/` ;
- `try/catch` de formatage HTTP dans un contrôleur ;
- versions non épinglées dans le catalogue Gradle ; `SNAPSHOT` ;
- `springdoc` < 2.7 avec Spring Security actif ; `/v3/api-docs` non protégé si
  `ApiAuthMode` ≠ `none` ;
- `TODO`, `FIXME`, code commenté.

---

## 7. Pièges connus

1. **Spring Boot 4 / Security 7.** Publiés ; `AntPathRequestMatcher` disparaît,
   plusieurs starters changent de nom. La ligne 3.5 reste tant qu'un bench ne
   mesure pas la 4 ; la montée est un item, pas un `bump`.
2. **Le KDoc `/**` + `/api/v1/**`.** Kotlin imbrique les commentaires : le
   `/**` d'un chemin ouvre un niveau jamais fermé. Écrire `/api/v1/[...]`.
3. **Temps de démarrage sous `/readyz`.** Actuator répond `UP` avant que la
   composition du moteur ait chargé prompts et schémas : brancher l'indicateur
   de disponibilité sur un `HealthIndicator` qui vérifie le `RunService`.
4. **`jackson-module-kotlin` et les data classes sans valeur par défaut.** Un
   champ manquant dans `RunRequest` devient une `MismatchedInputException`
   opaque : la validation Zod-like passe par Bean Validation **et** une
   configuration Jackson `FAIL_ON_NULL_FOR_PRIMITIVES`.
5. **Deux contrats.** `springdoc` génère un document depuis les contrôleurs ;
   l'IR en fournit un autre. Le second fait foi.

---

## 8. Smoke

```bash
cd workspace/src/{AppName}
./gradlew ktlintCheck detekt build -x test
./gradlew bootRun --args='--spring.profiles.active=dev' & PID=$!; sleep 25
curl -sf http://localhost:8080/healthz -o /dev/null
curl -sf http://localhost:8080/openapi.json > /tmp/openapi.json
kill $PID
python .sdda/sdda.py validate-api-contract --mission {n} --json
```

Timeout : 180 s (Gradle + démarrage Spring).
