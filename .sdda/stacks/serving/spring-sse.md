# Stack: spring-sse (serving)

Stack ID: serving-spring-sse
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin, java
Scope: surface d'exposition **HTTP + SSE** de l'application agentic sur la JVM (Kotlin et Java) — la surface du livrable `backend-api` quand `ApiFramework: spring-boot` : routes, streaming SSE du flux `RunEvent` (**même schéma** que `serving/cli.md` §3.2), identité de l'appelant établie au transport (`ApiAuthMode`), OpenAPI **exporté et dérivé de l'IR** (`ApiContractFirst`, confronté par `validate-api-contract`), annulation à la déconnexion, arrêt propre, **même fichier de trace** que la CLI. Pendant de `serving/fastapi-sse.md` et `serving/aspnet-minimal.md`. **Spring MVC + `SseEmitter` sur threads virtuels**, pas WebFlux (§1.1). Suppose `backend/kotlin-spring-boot.md` (la maison : projet, sécurité, Actuator, packaging) et **réutilise sans le modifier** le `RunService`, le `RunEvent` et la table `ExitCodes` de la CLI (`serving/cli-kotlin.md` / `serving/cli-java.md`). Pas de `.libs.json` : tous les artefacts sont dans `backend/kotlin-spring-boot.libs.json`.

---

## 1. Rôle et périmètre

La règle de `serving/fastapi-sse.md` §1 vaut mot pour mot : **le HTTP est un
transport, pas une couche métier.** `serving/http/` traduit une requête en
`RunService.run()` et un flux d'événements en réponse ; une règle métier qui
apparaîtrait ici est une règle que les evals ne mesureront jamais, parce
qu'elles passent par `RunService` (via la CLI), pas par le port.

Ce que la surface apporte et que la CLI ne peut pas : l'**identité vient du
transport** (jeton, clé, certificat — jamais du corps), le streaming a un
client réel qui **ferme la connexion** (l'annulation arrête la facture), et le
contrat est **publiable** (OpenAPI dérivé de l'IR : l'API GATE, part `api` de
G6). `backend-api` n'est pas une présentation plus propre de `cli-exe` : c'est
un service qu'un autre logiciel appelle, et la CLI reste générée à côté — c'est
elle que le runner d'eval lance.

### 1.1 Spring MVC + `SseEmitter`, pas WebFlux — pourquoi

| | Spring MVC + `SseEmitter` + threads virtuels | WebFlux + `Flux<ServerSentEvent>` |
|---|---|---|
| `RunService` | appelé tel quel : il est **synchrone** (`ChatClient.call()`, `JdbcClient`, SDK MCP synchrone) | exige soit une réécriture réactive de bout en bout, soit des appels bloquants sur la boucle Netty — la panne la plus classique de WebFlux |
| coût d'un run long bloqué | un thread **virtuel** par requête (`spring.threads.virtual.enabled: true`) : bloquer ne coûte presque rien | un thread de boucle bloqué gèle toutes les connexions qu'il sert |
| même code que la CLI | oui : la CLI et le HTTP appellent la même méthode, avec le même `Consumer<RunEvent>` | non : deux chemins d'exécution, donc deux comportements à évaluer |
| streaming des `token` | `ChatClient.stream()` (Reactor) consommé dans le thread du run et poussé dans l'émetteur | natif |
| dépendances | `spring-boot-starter-webmvc` (Tomcat) | `spring-boot-starter-webflux` (Netty) + opérateurs Reactor partout |

Le gain de WebFlux (des milliers de connexions sur peu de threads) est
exactement ce que les threads virtuels donnent au modèle bloquant, sans
réécrire un seul agent. Le choix est donc **un seul** modèle d'exécution pour
la CLI, le HTTP et les evals. WebFlux reste possible par ADR, si un
`RunService` réactif est écrit et mesuré.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `serving-spring-sse` |
| **Langage** | Kotlin 2.3 ou Java 21, JDK 21 (`lang/kotlin.md`, `lang/java.md`) |
| **Maison** | `backend/kotlin-spring-boot.md` (`Languages: kotlin, java`) — Spring Boot 4.1.1, Spring Security 7, Actuator |
| **Transport** | Spring MVC (`spring-boot-starter-webmvc`, Tomcat embarqué), `SseEmitter`, threads virtuels |
| **Point d'entrée** | le même jar `build/libs/{AppName}.jar` : `java -jar …/{AppName}.jar serve` démarre le serveur ; toute autre commande est la CLI (`web-application-type` choisi au démarrage, §3.5) |
| **Paramètres STACK.md** | `DeliverableType: backend-api`, `ApiFramework: spring-boot`, `ApiAuthMode` (`none` \| `api-key` \| `oauth2` \| `azure-ad` \| `mtls`), `ApiContractFirst`, `ServingLocalPort`, `StreamingEnabled`, `HumanInTheLoopEnabled` |
| **Contrat machine** | OpenAPI 3.1 **dérivé de l'IR**, publié en `serving/http/openapi.json` et servi tel quel sur `/openapi.json` ; flux SSE dont chaque `data:` est un `RunEvent` |
| **Codes** | HTTP en surface, `[CLASS]` en profondeur — table `serving/fastapi-sse.md` §3.3, projetée depuis `ExitCodes` |

---

## 3. Mapping des concepts SDD_Agents → idiomes Spring MVC

### 3.1 Routes

Identiques à `serving/fastapi-sse.md` §3.1 — un appelant ne doit pas savoir
dans quel langage le service est écrit :

| Méthode | Chemin | Rôle | Coûte des tokens |
|---|---|---|:-:|
| `POST` | `/v1/runs` | une exécution ; `Accept: text/event-stream` → SSE, `application/json` → réponse unique | oui |
| `POST` | `/v1/runs/{thread_id}/resume` | reprise (seulement si l'IR a `humanInTheLoop` **et** un checkpointer existe) | oui |
| `GET` | `/v1/runs/{run_id}` | état et résultat d'un run terminé (lu depuis la trace) | non |
| `DELETE` | `/v1/runs/{run_id}` | annule un run en cours | non |
| `GET` | `/v1/inspect` | IR résumé | non |
| `GET` | `/healthz` | vivacité — `/actuator/health/liveness` mappé | non |
| `GET` | `/readyz` | disponibilité — `/actuator/health/readiness` avec un `HealthIndicator` qui vérifie le `RunService` (prompts hashés, outils = contrats, IR à jour, checkpointer joignable) | non |
| `GET` | `/openapi.json` | le fichier **dérivé de l'IR**, servi tel quel (§6) | non |

Aucune route d'ingestion ni d'évaluation.

### 3.2 Le flux SSE

**Java**

```java
// serving/http/RunsController.java — package {package}.serving.http
@RestController
@RequestMapping("/v1/runs")
final class RunsController {
    private final RunService runs;                 // PARTAGÉ avec la CLI — inchangé
    private final RunEventJson json;               // le même sérialiseur que NdjsonWriter
    private final ExecutorService runExecutor;     // Context.taskWrapping(newVirtualThreadPerTaskExecutor())

    @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE, produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    SseEmitter stream(@RequestBody RunRequest body, Identity who) {   // Identity : résolu depuis Spring Security, jamais le corps
        body.rejectIdentityFields();                                   // tenant_id dans le corps -> 400 [SERVING_IDENTITY_FROM_PAYLOAD]
        SseEmitter emitter = new SseEmitter(0L);                       // pas de timeout d'émetteur : la borne est AgentTimeoutSec
        CancellationToken cancel = new CancellationToken();
        emitter.onCompletion(cancel::cancel);                          // client parti -> le run s'arrête, le budget aussi
        emitter.onTimeout(cancel::cancel);
        emitter.onError(e -> cancel.cancel());
        runExecutor.submit(() -> runs.run(body.toInput(), RunContext.from(who, cancel), event -> {
            try {
                emitter.send(SseEmitter.event().name(event.event()).data(json.write(event), MediaType.APPLICATION_JSON));
            } catch (IOException disconnected) {
                cancel.cancel();                                       // l'envoi a échoué : même chose qu'une déconnexion
            }
            if (event instanceof RunEvent.RunFinished) emitter.complete();
        }));
        return emitter;
    }
}
```

**Kotlin**

```kotlin
// serving/http/RunsController.kt — package {package}.serving.http
@RestController
@RequestMapping("/v1/runs")
class RunsController(
    private val runs: RunService,
    private val json: RunEventJson,
    private val runExecutor: ExecutorService,
) {
    @PostMapping(consumes = [MediaType.APPLICATION_JSON_VALUE], produces = [MediaType.TEXT_EVENT_STREAM_VALUE])
    fun stream(@RequestBody body: RunRequest, who: Identity): SseEmitter {
        body.rejectIdentityFields()
        val emitter = SseEmitter(0L)
        val cancel = CancellationToken()
        emitter.onCompletion(cancel::cancel)
        emitter.onTimeout(cancel::cancel)
        emitter.onError { cancel.cancel() }
        runExecutor.submit {
            runs.run(body.toInput(), RunContext.from(who, cancel)) { event ->
                runCatching {
                    emitter.send(SseEmitter.event().name(event.event).data(json.write(event), MediaType.APPLICATION_JSON))
                }.onFailure { cancel.cancel() }
                if (event is RunEvent.RunFinished) emitter.complete()
            }
        }
        return emitter
    }
}
```

Les trois règles de `serving/fastapi-sse.md` §3.2 s'appliquent : **`run_finished`
toujours dernier** (y compris après `error` et sur annulation), **commentaire
`: keepalive` toutes les 15 s** (un `ScheduledExecutorService` envoie
`SseEmitter.event().comment("keepalive")` tant que l'émetteur est ouvert),
en-têtes **`Cache-Control: no-cache`** et **`X-Accel-Buffering: no`**. Les
en-têtes `X-Run-Id` et `X-Trace-Id` sont posés avant le premier événement.

### 3.3 Statuts HTTP ↔ `[CLASS]`

La table est celle de `serving/fastapi-sse.md` §3.3 (400, 401, 403, 409, 413,
422, 429, 499, 500, 502, 503, 504 ; **une borne atteinte n'est jamais un
`5xx`**). Elle vit dans **`serving/http/Status.{kt,java}`**, dérivée de
`ExitCodes` de la CLI — une table de vérité, deux projections — et un seul
`@RestControllerAdvice` la rend en `ProblemDetail` `{class, message, run_id}`,
jamais une pile d'appels. En SSE, une erreur après le premier octet ne peut
plus changer le statut : elle sort en événement `error` puis `run_finished`.

### 3.4 Identité de l'appelant

| `ApiAuthMode` | Mécanisme Spring Security 7 | `Identity` |
|---|---|---|
| `oauth2`, `azure-ad` | `oauth2ResourceServer { jwt { } }` (`spring-boot-starter-security-oauth2-resource-server`), émetteur et audience depuis la configuration | `tenant` = claim déclaré dans `STACK.md` |
| `api-key` | filtre `OncePerRequestFilter` : en-tête `X-Api-Key` comparé **en temps constant** (`MessageDigest.isEqual`) à la table des clés (valeurs par NOM de variable) | `tenant` = celui associé à la clé |
| `mtls` | `x509 { }` ; TLS client exigé au connecteur | `tenant` = extrait du sujet du certificat selon la règle déclarée |
| `none` | aucune — exige que la MISSION déclare un acteur anonyme, sinon `/readyz` rouge `[CONFIG_INVALID]` | tenant unique de configuration |

Un `HandlerMethodArgumentResolver` construit `Identity` depuis
l'`Authentication` établie par Spring Security ; c'est le **seul** chemin vers
`RunContext` et `ToolContext`. `requestMatchers("/healthz", "/readyz",
"/openapi.json").permitAll()` en chemins littéraux ; tout le reste authentifié
dès que `ApiAuthMode` ≠ `none`. CORS fermé par défaut.

### 3.5 Un jar, deux modes

Le livrable `backend-api` reste **un** jar, parce que le contrat d'exécution
lance `build/libs/{AppName}.jar` et que la CLI est la surface des evals :

```java
// app/Application.java — le mode est décidé AVANT le démarrage du contexte
public static void main(String[] args) {
    SpringApplication app = new SpringApplication(Application.class);
    app.setAddCommandLineProperties(false);
    boolean serve = args.length > 0 && args[0].equals("serve");
    app.setWebApplicationType(serve ? WebApplicationType.SERVLET : WebApplicationType.NONE);
    System.exit(SpringApplication.exit(app.run(args)));   // en mode serve, run() ne rend la main qu'à l'arrêt
}
```

En mode CLI (`NONE`), aucun serveur n'est démarré et les contrôleurs ne sont
pas enregistrés ; en mode `serve`, la CLI n'exécute aucune commande.

### 3.6 Arrêt propre

```yaml
# app/resources/application.yml (profil serve)
server:
  port: ${SERVING_LOCAL_PORT:8080}
  shutdown: graceful                      # plus de nouvelles requêtes, les runs en cours se terminent
spring:
  lifecycle:
    timeout-per-shutdown-phase: 30s       # au-delà : les runs restants sont annulés -> run_finished {cancelled}
  threads.virtual.enabled: true
```

Sur `SIGTERM`, les émetteurs encore ouverts reçoivent `run_finished` avec
`status: "cancelled"` avant la fermeture du contexte, qui vide les traces
(`observability/otel-genai-jvm.md` §5.3). Le dernier run avant l'arrêt est
celui qu'on voudra relire.

---

## 4. Structure de fichiers générée

Zone de `dev-api` (`workspace/src/**/serving/**`), disposition à plat
(`lang/*.md` §4). Le reste de la maison (sécurité, Actuator, composition) est
dans `app/` (`backend/kotlin-spring-boot.md`).

```
workspace/src/{AppName}/serving/
├── cli/                     # la CLI — inchangée (serving/cli-kotlin.md | cli-java.md)
└── http/
    ├── RunsController       # POST /v1/runs (SSE | JSON), resume, GET, DELETE — aucun métier
    ├── InspectController    # /v1/inspect
    ├── OpenApiController    # GET /openapi.json -> la ressource classpath:serving/http/openapi.json, octet pour octet
    ├── openapi.json         # DÉRIVÉ de l'IR (§6) — la seule copie que validate-api-contract doit trouver
    ├── SseSupport           # keepalive, en-têtes, run_finished garanti
    ├── IdentityResolver     # Authentication -> Identity ; JAMAIS depuis le corps
    ├── Status               # [CLASS] -> HTTP, dérivé d'ExitCodes
    ├── ProblemAdvice        # @RestControllerAdvice unique
    └── RunRequest · RunResponse   # GÉNÉRÉS depuis l'IR

workspace/src/{AppName}/tests/serving/http/
├── OpenApiMatchesIrTest     # L1 : API GATE — isomorphie openapi.json <-> IR (même règles que test_openapi_matches_ir.py)
├── IdentityTest             # L1 : tenant du corps -> 400 ; sans jeton -> 401 ; clé comparée en temps constant
├── SseContractTest          # L1 : chaque data: parse en RunEvent ; run_finished en dernier ; keepalive
├── StatusMappingTest        # L1 : table exhaustive [CLASS] -> HTTP ; bornes != 5xx
└── CancellationTest         # L1 : déconnexion -> CancellationToken levé, run arrêté, run_finished {cancelled} dans la trace
```

`openapi.json` est déclaré en ressource (`resources.include("serving/http/openapi.json")`
en plus de la liste de `lang/*.md` §4.1) pour être servi depuis le jar.

---

## 5. Conventions imposées

Les dix règles de `serving/fastapi-sse.md` §5 s'appliquent (identité jamais du
corps, `ApiAuthMode: none` justifié, aucun secret dans une réponse,
déconnexion = annulation, `/readyz` à 0 token, corps plafonné avant
désérialisation, `409` sur thread en cours, `resume` absent sans
checkpointer, flush des traces à l'arrêt, CORS fermé). Propres à Spring :

1. **`@RequestBody` borné avant Jackson** : un filtre refuse en `413` un
   `Content-Length` au-dessus de `max_input_bytes`, et plafonne le flux quand
   il est absent.
2. **Jamais `spring.mvc.async.request-timeout` comme borne d'agent** : la borne
   est `AgentTimeoutSec` dans le `RunService` ; le délai MVC ne ferait que
   couper la connexion sans arrêter le run.
3. **`springdoc` ne génère pas le contrat** : `springdoc.api-docs.enabled:
   false` ; s'il est présent pour l'interface `/docs`, il est pointé sur
   `/openapi.json` (§6).
4. **Pas d'`@Async` sur les contrôleurs** : l'exécution du run passe par
   l'`ExecutorService` de la composition, enveloppé par `Context.taskWrapping`
   pour que la trace suive (`observability/otel-genai-jvm.md` §9.5).

---

## 6. L'API GATE — l'OpenAPI est dérivé, jamais écrit

Même couture que `serving/fastapi-sse.md` §6 : `RunRequest.input` ←
`inputSchema` de l'agent racine, `RunResponse.output` et `RunEvent.final.output`
← `outputSchema`, `max_budget_usd` ← `budget`, présence de `/resume` ←
`orchestration.humanInTheLoop`. Le fichier `serving/http/openapi.json` est
**produit depuis l'IR**, versionné, et servi tel quel ; `validate-api-contract`
le cherche sous `workspace/src/` et le confronte à l'IR (`[API_CONTRACT_DRIFT]`,
`[API_ROUTE_UNBACKED]`, `[API_STATUS_UNMAPPED]`), et `OpenApiMatchesIrTest`
refait la même confrontation en L1.

Pourquoi ne pas laisser springdoc générer le document depuis les contrôleurs :
ce serait **un second contrat**, celui du code, qui dirait ce que le service
fait plutôt que ce que la spécification exige — c'est la divergence que la gate
existe pour attraper. `ApiContractFirst: false` désactive la confrontation et
exige un ADR (`registry/adr-requirements.yml`).

> **Réserve outillage.** À la rédaction, `validate_api_contract.py` cherche le
> module de mapping de statuts sous les motifs `serving/status.py`,
> `Serving/Status.cs`, `serving/status.ts` — pas `serving/http/Status.kt` ni
> `.java` — et n'exclut pas `build/` de sa recherche de `openapi.json` : la
> copie que Gradle place dans `build/resources/main/serving/http/` peut être
> trouvée avant la source (ordre alphabétique). Tant que l'outillage ne le
> prend pas en compte, lancer la gate après `./gradlew clean` ou sur un arbre
> sans `build/`.

---

## 7. Commande de smoke

Déterministe, 0 token :

```bash
cd workspace/src/{AppName}
./gradlew bootJar
./gradlew test --tests '*.serving.http.*'                     # API GATE + identité + SSE + annulation
cd ../../..
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar serve & PID=$!
sleep 20
curl -fsS localhost:${ServingLocalPort}/healthz
curl -fsS localhost:${ServingLocalPort}/readyz                 # 503 si IR périmé / prompt absent
curl -fsS localhost:${ServingLocalPort}/openapi.json | cmp - workspace/src/{AppName}/serving/http/openapi.json
curl -s -o /dev/null -w '%{http_code}' localhost:${ServingLocalPort}/v1/runs -X POST -d '{}' -H 'Content-Type: application/json'
#   -> 401 attendu si ApiAuthMode != none
kill -TERM $PID; wait $PID                                     # arrêt propre : code 0, trace du dernier run complète
python .sdda/sdda.py validate-api-contract --mission {n} --json
```

Le `cmp` vérifie que le service sert **exactement** le fichier dérivé de
l'IR — pas une version régénérée à la volée.

---

## 8. Contrat d'exécution

Le livrable `backend-api` JVM implémente **aussi** la CLI de `serving/cli.md`
§3.1-3.3 **à l'identique** (commandes, NDJSON `RunEvent`, codes de sortie) :
c'est elle que les runners lancent, et le HTTP en est une projection. Les trois
points de `serving/cli.md` §3.5 :

1. `run --json --input-file -` lit l'entrée sur `stdin` ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'application sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil) et le retrieval
   figé depuis le même dossier, sans aucun appel réseau d'outil — en mode CLI
   comme en mode `serve` ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished`.

Commande de lancement : `java -jar workspace/src/{AppName}/build/libs/{AppName}.jar`
(CLI, celle des runners) et `java -jar workspace/src/{AppName}/build/libs/{AppName}.jar serve`
(serveur), depuis la racine du dépôt ; en développement `./gradlew run
--args='serve'`. `--executor cli` dérive la commande CLI du langage actif,
`--executor cmd:<commande>` l'impose. Le build produit exactement
`build/libs/{AppName}.jar`. Le suite adversariale peut viser le serveur par
`adversarial-target-check --endpoint` (voie HTTP neutre) ou la CLI par
`--executor cli`.

---

## 9. Pièges connus

Les treize pièges de `serving/fastapi-sse.md` §8 s'appliquent (tenant lu dans
le corps, buffering du proxy, keepalive absent, run non annulé, `500` sur une
borne, `/readyz` qui appelle le modèle, OpenAPI écrit à la main, CORS `*`, pile
d'appels rendue, `thread_id` prévisible, traces perdues au `SIGTERM`, workers
multiples avec checkpointer en mémoire). Propres à Spring MVC :

1. **`new SseEmitter()` sans argument.** Le délai par défaut est celui du
   conteneur asynchrone : la connexion se ferme au milieu d'un run long, le
   client voit une panne, et le run continue sans lecteur si `onTimeout`
   n'annule pas.
2. **Le run exécuté dans le thread de la requête.** Le contrôleur ne rend
   l'émetteur qu'à la fin : aucun événement n'est streamé. Le run part sur
   l'`ExecutorService`, l'émetteur est rendu immédiatement.
3. **`emitter.send` depuis plusieurs threads.** L'émetteur n'est pas conçu
   pour des envois concurrents : le keepalive et les événements passent par un
   même verrou.
4. **`springdoc` qui publie son propre `/v3/api-docs`.** Un second contrat,
   public par défaut : `springdoc.api-docs.enabled: false`.
5. **`AntPathRequestMatcher`** : retiré en Spring Security 7 ; chemins
   littéraux dans `requestMatchers(...)`.
6. **Threads virtuels et `synchronized`** autour d'un envoi réseau : épinglage
   du thread porteur, le service ne sert plus que quelques flux à la fois
   (`lang/java.md` §9.6).
