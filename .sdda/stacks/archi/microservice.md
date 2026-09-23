# Stack: microservice (archi)

Stack ID: archi-microservice
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *
Scope: pattern d'architecture de la **coquille applicative** quand le système agentic est **un service déployé seul, appelé par d'autres services** — santé, métriques, traces propagées, contrat versionné, idempotence, résilience sur les appels sortants, configuration externalisée, image conteneur. Hérité de SDD_Pro `archi/microservice.md`, transposé : **un `/sdda-full` produit UN service**, sans base partagée et, par défaut, sans écriture. Suppose `DeliverableType: backend-api` ou `container`, et une fiche `backend/*.md` active. En interne, réutilise `archi/ddd.md` ou `archi/mvc.md` — cette fiche dit comment le service parle au monde, pas comment il se range dedans.

---

## 1. Rôle et périmètre

`mvc` et `ddd` disent comment la coquille est structurée **à l'intérieur**.
`microservice` dit comment le système se comporte **à l'extérieur** : ce qu'un
orchestrateur de conteneurs, une passerelle, un autre service et un ingénieur
d'astreinte doivent trouver, sans lire le code.

Le contexte agentic ajoute un fait que les microservices classiques n'ont pas :
**chaque requête coûte de l'argent et dure des secondes.** Un microservice
agentic sans idempotence facture deux fois le même run à la première
réémission réseau ; sans annulation à la déconnexion, il continue à payer pour
un appelant parti ; sans budget par requête, un appelant l'épuise en quelques
appels. Les cinq piliers hérités sont donc relus sous cet angle.

| Pilier | Sens classique | Sens agentic |
|---|---|---|
| **Données par service** | une base privée | aucune base partagée ; les sources déclarées (`dataaccess/`) sont **lues** sous enveloppe, jamais possédées |
| **Communication explicite** | REST/gRPC ou événements | `POST /v1/runs` + SSE, contrat **dérivé de l'IR** (API GATE) |
| **Résilience** | timeout, retry, disjoncteur sur les sorties | idem — sur le fournisseur de modèles et les serveurs MCP ; **jamais** de retry sur un outil non idempotent |
| **Observabilité** | logs, métriques, traces | traces **OTel-GenAI** avec coût recalculé (`observability/otel-genai.md`), métriques de tokens et de budget |
| **Idempotence et version** | `Idempotency-Key`, `/v1/` | un `run_id` par clé, une seule exécution facturée ; version du contrat = version de l'IR |

Hors périmètre : l'écosystème (mesh, broker, Kubernetes), l'hébergement, le
TLS terminé en amont. La fiche produit **une image et un contrat** ; les faire
tourner appartient à l'exploitation (ARCHITECTURE §10).

---

## 2. Ce que le service expose, obligatoirement

| Route | Rôle | Coûte des tokens | Source |
|---|---|:-:|---|
| `POST /v1/runs` | une exécution ; `Accept: text/event-stream` → SSE | oui | `serving/fastapi-sse.md` §3 ou `serving/aspnet-minimal.md` |
| `GET /v1/runs/{id}` | état et résultat d'un run | non | idem |
| `POST /v1/runs/{id}/resume` | reprise après interruption — **seulement si** `HumanInTheLoopEnabled` | oui | idem |
| `GET /healthz` | vivacité : le processus répond | non | fiche serving |
| `GET /readyz` | disponibilité : fournisseur joignable, checkpointer partagé, sources déclarées lisibles | non | fiche serving |
| `GET /metrics` | Prometheus/OpenMetrics : requêtes, latence, **tokens**, **coût**, bornes atteintes | non | `observability/otel-genai.md` |
| `GET /openapi.json` | le contrat, **dérivé de l'IR** | non | API GATE (G6, part `api`) |

Aucune autre route sans contrat dans l'IR : une route publiée que rien ne
soutient est `[API_ROUTE_UNBACKED]`.

---

## 3. Principes non négociables

**Frontière et contrat** :
- version dans le chemin (`/v1/`), jamais dans un en-tête ; un changement
  incompatible du `inputSchema`/`outputSchema` est un `/v2/` ;
- `Idempotency-Key` accepté sur `POST /v1/runs` : même clé → même `run_id`,
  aucune seconde exécution, aucune seconde facture ;
- l'identité de l'appelant vient du transport (`ApiAuthMode` ≠ `none` dès qu'une
  donnée est touchée : `validate_packaging` le refuse sinon).

**Budget et durée** :
- chaque run reçoit `{deadline, budget}` du contexte d'exécution ; le service
  ne dépasse ni l'un ni l'autre, et le dit par un événement typé, pas par un
  timeout de passerelle ;
- déconnexion du client → annulation propagée au graphe ;
- quota par appelant (`rate-limit`) : sans lui, le budget du produit est à la
  merci d'un seul client.

**Résilience sortante** :
- timeout explicite, backoff exponentiel avec gigue, disjoncteur sur le
  fournisseur de modèles et chaque serveur MCP ;
- **la classe d'effet de bord de l'outil gouverne le retry** : `read-only` peut
  être rejoué, `write`/`destructive` jamais sans clé d'idempotence déclarée au
  contrat.

**État** :
- service **sans état local** : le checkpointer est partagé (base ou store)
  dès qu'il y a plus d'un réplica, sinon une reprise tombe sur un réplica qui
  n'a jamais vu le run — vérifié par `/readyz` ;
- aucune écriture métier par défaut ; une MISSION qui écrit passe par
  `dataaccess/repository-tools.md`, un ADR, et alors seulement une outbox.

**Configuration** :
- externalisée : `.env` en développement, variables d'environnement ou coffre
  en production, lues par le mécanisme natif de l'écosystème ; aucune valeur
  dans l'image ;
- `app_config.json` (bornes, tiers, tarifs) est **versionné avec le code** : ce
  qui est épinglé est comparable (P10).

**Observabilité** :
- logs JSON avec `run_id`, `trace_id`, `span_id`, `tenant` (haché selon
  `TracePIIPolicy`) ; aucun secret, aucune PII en clair ;
- propagation du `traceparent` W3C depuis l'appelant jusqu'aux spans d'agents ;
- métriques minimales : `runs_total{status}`, `run_duration_seconds`,
  `tokens_total{direction}`, `cost_usd_total`, `bounds_exceeded_total{bound}`.

**Livraison** :
- `Dockerfile` multi-étages, utilisateur non root, `HEALTHCHECK` sur `/healthz`,
  image de l'écosystème (`python:3.12-slim`, `node:22-alpine`,
  `eclipse-temurin:21-jre`, `mcr.microsoft.com/dotnet/aspnet`) ;
- les prompts et les schémas figés sont **dans l'image** : ce sont des actifs
  d'exécution (ARCHITECTURE §2.ter).

---

## 4. Anti-patterns rejetés

| Anti-pattern | Conséquence agentic |
|---|---|
| `POST /v1/runs` sans `Idempotency-Key` | une réémission réseau = un second run facturé |
| retry par défaut de la librairie HTTP sur un outil `write` | trois tickets créés pour une demande |
| checkpointer en mémoire avec deux réplicas | reprise impossible une fois sur deux, sans erreur claire |
| route métier hors IR (`/v1/orders/{id}`) | un contrat que l'API GATE ne peut pas confronter |
| logs `print`/`console.log` | post-mortem d'un run non déterministe impossible à automatiser |
| secret dans l'image ou `appsettings.json` commité | `[SECRET_LEAK]` — et il reste dans l'historique du registre |
| service qui lit la base d'un autre service | la source n'est ni déclarée ni enveloppée ; personne ne relit son périmètre |
| `microservice` avec `DeliverableType: cli-exe` | contradictoire : un exécutable lancé à la main n'a ni `/readyz` ni appelant à authentifier |

---

## 5. Mapping → répertoires (en plus de `mvc`/`ddd`)

| Élément | Emplacement |
|---|---|
| Surface HTTP + santé + métriques | `…/serving/http/` (fiche `serving/*` du langage) |
| Résilience sortante | `…/app/resilience.*` — politiques nommées, une par dépendance |
| Télémétrie | `…/app/telemetry.*` (config OTel, exporteur) |
| Contrat publié | `…/serving/http/openapi.json` **généré** |
| Conteneur | `workspace/src/{AppName}/Dockerfile` · `.dockerignore` |
| Configuration | `…/app/config.*` + variables d'environnement documentées dans `README.md` du projet |

---

## 6. Surcharges par écosystème

| Concept | Python | TypeScript | Kotlin | .NET |
|---|---|---|---|---|
| Résilience | `tenacity` + disjoncteur maison ou `httpx` timeouts | `p-retry` + `AbortController` | Resilience4j | `Microsoft.Extensions.Http.Resilience` + Polly |
| Métriques | `prometheus-client` via OTel | `@opentelemetry/sdk-node` | Micrometer (Actuator) | OpenTelemetry .NET |
| Santé | routes FastAPI | routes Express/Nest (`@nestjs/terminus`) | Actuator `/actuator/health/{liveness,readiness}` mappé sur `/healthz` `/readyz` | `Microsoft.Extensions.Diagnostics.HealthChecks` |
| Image | `python:3.12-slim` + `uv` | `node:22-alpine` → `distroless` | `gradle:jdk21` → `eclipse-temurin:21-jre` | `dotnet/sdk` → `dotnet/aspnet` |

---

## 7. Quand choisir `microservice`

| Oui | Non |
|---|---|
| l'appelant est un autre logiciel, déployé indépendamment | l'utilisateur lance le programme (`cli-exe`) |
| le système doit tourner en plusieurs réplicas | un processus suffit |
| une équipe d'exploitation gère déjà conteneurs, métriques, alertes | personne n'a de tableau de bord à brancher dessus |
| le contrat doit survivre à des appelants qu'on ne contrôle pas | l'appelant est dans le même dépôt |

Le coût est réel : santé, métriques, résilience, image, contrat versionné. La
fiche ne le cache pas — elle refuse simplement de les laisser « pour plus
tard », parce que c'est en production qu'on découvre qu'ils manquent.

---

## 8. Pour les agents

- **`dev-backend`** : composition, résilience, télémétrie, `Dockerfile`,
  configuration externalisée, `README.md` d'exploitation (variables, sondes,
  codes de sortie). Il lit `archi/{mvc|ddd}.md` pour l'intérieur.
- **`dev-api`** : les routes du §2, l'idempotence, l'annulation, les sondes —
  depuis la fiche `serving/*` du langage.
- **`validate_packaging`** refuse la combinaison avec `cli-exe` et exige
  `ApiAuthMode` ≠ `none` dès qu'une donnée est touchée.
- **`review-cost`** lit `cost_usd_total` et `bounds_exceeded_total` : ce sont
  les métriques de ce pattern qui rendent son rapport possible sans relire les
  traces une à une.
