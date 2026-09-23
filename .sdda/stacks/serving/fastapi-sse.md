# Stack: fastapi-sse (serving)

Stack ID: serving-fastapi-sse
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: surface d'exposition **HTTP** de l'application agentic — endpoints, streaming SSE, identité de l'appelant, reprise après interruption, annulation à la déconnexion, sondes de vivacité, OpenAPI dérivé de l'IR. C'est la surface du livrable `backend-api`. Suppose `lang/python.md` et **réutilise sans le modifier** le `RunService`, le schéma `RunEvent` et la table `[CLASS] → code` de `serving/cli.md` (§3.2, §3.3) : seul le transport change.

---

## 1. Rôle et périmètre

La CLI prouve que le système marche. Le HTTP le rend utilisable par autre chose
qu'un humain au terminal. La règle qui gouverne toute cette fiche :

> **Le HTTP est un transport, pas une couche métier.** Tout ce qui décide vit
> dans l'orchestration ; tout ce qui appelle un modèle vit dans les agents.
> `serving/` traduit une requête en `RunService.run()` et un flux d'événements
> en réponse. Une règle métier qui apparaît ici est une règle que les evals ne
> mesureront jamais, parce qu'elles passent par `RunService`, pas par le port.

Ce que cette surface apporte, et que la CLI ne peut pas donner :

1. **L'identité vient du transport.** Un en-tête authentifié, un jeton, un
   certificat client. C'est la seule façon d'établir un `tenant_id` que le
   modèle ne voit jamais et ne peut donc pas influencer — ce dont dépendent les
   vues SQL par agent et le filtrage du retrieval.
2. **Le streaming a un client réel.** Un navigateur ou un service consomme les
   `token` au fil de l'eau, et **ferme la connexion**. L'annulation propagée
   (§5.4) est ce qui empêche un run abandonné de continuer à facturer.
3. **Le contrat est publiable.** L'OpenAPI est **dérivé** des `inputSchema` /
   `outputSchema` de l'IR, pas écrit à la main : c'est l'API GATE (§6).

Hors périmètre : l'hébergement, le TLS, la passerelle, le dimensionnement.
La fiche produit une application ASGI ; la faire tourner appartient à
l'exploitation.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `serving-fastapi-sse` |
| **Langage** | Python 3.12 (`lang/python.md`) |
| **Librairies** | `fastapi` 0.128.x · `uvicorn[standard]` 0.38.x · `sse-starlette` 3.x · `pydantic` 2.x (déjà présent) — capability `serving-fastapi-sse` du `.libs.json` du framework actif |
| **Point d'entrée** | `[project.scripts] {AppName}-serve = "{AppName}.serving.http.app:main"` → `uvicorn {AppName}.serving.http.app:app` |
| **Paramètres STACK.md** | `DeliverableType: backend-api`, `ApiFramework: fastapi`, `## Active Architecture Pattern` (couches de la coquille), `## Active Backend Stack` → `backend/python-fastapi.md` (le projet, la DI, la config, le packaging autour de cette surface), `ApiContractFirst`, `ApiAuthMode`, `ServingLocalPort`, `StreamingEnabled`, `HumanInTheLoopEnabled` |
| **Contrat machine** | OpenAPI 3.1 dérivé de l'IR + flux SSE dont chaque `data:` est un `RunEvent` |
| **Codes** | HTTP en surface, `[CLASS]` en profondeur — table §3.3 |

---

## 3. Mapping des concepts SDD_Agents → idiomes HTTP

### 3.1 Endpoints

| Méthode | Chemin | Rôle | Coûte des tokens |
|---|---|---|:-:|
| `POST` | `/v1/runs` | une exécution. `Accept: text/event-stream` → SSE ; `application/json` → réponse unique | oui |
| `POST` | `/v1/runs/{thread_id}/resume` | reprend un run interrompu (décision humaine) | oui |
| `GET` | `/v1/runs/{run_id}` | état et résultat d'un run terminé (lu depuis la trace) | non |
| `DELETE` | `/v1/runs/{run_id}` | annule un run en cours | non |
| `GET` | `/v1/inspect` | IR résumé : agents, outils, bornes, pattern, budget estimé | non |
| `GET` | `/healthz` | **vivacité** : le process répond. Aucune dépendance. | non |
| `GET` | `/readyz` | **disponibilité** : config chargée, prompts hashés, outils = contrats, IR à jour, checkpointer joignable | non |
| `GET` | `/openapi.json` · `/docs` | contrat dérivé de l'IR (§6) | non |

`/healthz` et `/readyz` sont **deux** endpoints et non un seul : un
orchestrateur de conteneurs redémarre sur `liveness` et retire du service sur
`readiness`. Les confondre fait redémarrer en boucle un service dont la seule
faute est que sa base est momentanément injoignable.

Aucun endpoint d'ingestion ni d'évaluation : l'ingestion appartient à
`retrieval/{index}/ingest.py` (`dev-retrieval`), l'évaluation à `qa-evals`.
Le service HTTP n'écrit **jamais** dans `datasets/`, `evals/`, `prompts/`.

### 3.2 Le flux SSE

Un `RunEvent` par message, **schéma identique à celui de la CLI** (`cli.md`
§3.2) — c'est ce qui permet à un même test de conformité de couvrir les deux
surfaces :

```
event: token
data: {"event":"token","agent_id":"classifier","text":"Le "}

event: tool_call
data: {"event":"tool_call","agent_id":"classifier","tool":"crm_search","call_id":"01J…","args":{"customer_id":"«redacted»"}}

event: final
data: {"event":"final","output":{…},"degraded":false,"citations":[…]}

event: run_finished
data: {"event":"run_finished","exit_code":0,"cost_usd":0.041,"duration_ms":6210,"hops":4,"trace_path":"…"}
```

Trois règles non négociables :

- **`run_finished` est toujours le dernier message**, y compris après `error`,
  y compris sur annulation. Un client qui ne le reçoit pas sait que la
  connexion a été coupée, et non que le run a réussi silencieusement.
- **Un commentaire SSE (`: keepalive`) toutes les 15 s.** Sans lui, un
  reverse-proxy coupe une connexion inactive pendant qu'un agent réfléchit, et
  le client voit un échec là où il n'y en a pas.
- **`Cache-Control: no-cache`, `X-Accel-Buffering: no`.** Sans le second,
  nginx bufferise le flux et le streaming n'arrive qu'à la fin — c'est-à-dire
  qu'il n'existe pas, tout en ayant l'air implémenté.

### 3.3 Statuts HTTP ↔ classes `[CLASS]`

Le mapping vit dans `serving/http/status.py`, **dérivé** de `exit_codes.py` de
la CLI : une seule table de vérité, deux projections.

| HTTP | Quand | `[CLASS]` typiques |
|---|---|---|
| `200` | succès (réponse unique) | — |
| `200` + SSE | succès streamé — le verdict est dans `run_finished`, pas dans le statut | — |
| `202` | run accepté en asynchrone (lot différé) | — |
| `400` | entrée non conforme à `inputSchema` | `[CLI_USAGE]`, `[INPUT_INVALID]` |
| `401` | identité absente ou jeton invalide | `[SERVING_IDENTITY_MISSING]` |
| `403` | identité établie, tenant non autorisé sur la ressource | `[TOOL_SCOPE_EXCESS]` |
| `409` | `thread_id` déjà en cours, ou reprise d'un run non interrompu | `[RESUME_FAILED]` |
| `413` | corps au-dessus de `max_input_bytes` | `[INPUT_TOO_LARGE]` |
| `422` | sortie du modèle non conforme au schéma après guardrail | `[AGENT_OUTPUT_INVALID]` |
| `429` | quota d'appelant dépassé | `[RATE_LIMITED]` |
| `499` | client déconnecté — run annulé (journalisé, non servi) | `[RUN_CANCELLED]` |
| `500` | erreur interne non classée | `[INTERNAL_ERROR]` |
| `502` | outil ou MCP injoignable | `[TOOL_MCP_DISCONNECTED]` |
| `503` | `/readyz` rouge : config, prompts, IR périmé | `[CONFIG_INVALID]`, `[IR_STALE]` |
| `504` | `AgentTimeoutSec` atteint | `[AGENT_TIMEOUT]` |

**Un run qui atteint une borne n'est pas un `500`.** Budget dépassé, borne
atteinte, refus de guardrail : ce sont des **résultats**. En SSE ils sortent en
`bound_exceeded` puis `final` sous `200` ; en réponse unique, en `200` avec
`degraded: true` ou en `422` selon `OnBoundExceeded`. Les rendre en `5xx`
déclencherait les alertes d'exploitation pour un comportement nominal — et les
alertes qui crient pour rien finissent coupées.

### 3.4 Mapping des entités

| Concept | Idiome HTTP |
|---|---|
| **MISSION** | un service = une mission ; `mission_id` dans `/v1/inspect` |
| **RUN** | `run_id` (ULID) retourné dans `run_started` et l'en-tête `X-Run-Id` |
| **thread** | `thread_id` dans le corps ; = `gen_ai.conversation.id` ; clé du checkpointer |
| **Entrée utilisateur** | `Untrusted` — `wrap_untrusted(source="http:body")`, **toujours**, même authentifiée |
| **Identité de l'appelant** | **exclusivement** dérivée de `Authorization` / `X-Api-Key` / certificat client selon `ApiAuthMode`. Un `tenant_id` présent dans le corps est un `400`, pas une valeur par défaut (§5.1) |
| **Bornes** | héritées de l'IR ; le corps peut **baisser** `max_budget_usd`, jamais le lever |
| **TRACE** | `trace_path` dans `run_finished` et en-tête `X-Trace-Id` ; `traceparent` W3C propagé si présent |

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/serving/
├── run_service.py        # PARTAGÉ avec la CLI — inchangé
├── events.py             # RunEvent — inchangé
├── exit_codes.py         # [CLASS] -> code — inchangé, source de status.py
├── context.py            # ToolContext — inchangé
└── http/
    ├── __init__.py
    ├── app.py            # create_app() ; lifespan (warmup, flush traces) ; main() uvicorn
    ├── routes_runs.py    # POST /v1/runs, resume, GET, DELETE — aucun métier
    ├── routes_health.py  # /healthz, /readyz, /v1/inspect
    ├── sse.py            # RunEvent -> EventSourceResponse ; keepalive ; annulation
    ├── security.py       # ApiAuthMode -> dépendance FastAPI -> Identity ; JAMAIS depuis le corps
    ├── status.py         # [CLASS] -> HTTP, dérivé d'exit_codes.py
    ├── errors.py         # handler global : toute exception sort en {class, message, run_id}
    └── schemas.py        # RunRequest / RunResponse GÉNÉRÉS depuis l'IR (§6)

workspace/src/{AppName}/tests/serving/http/
├── test_openapi_matches_ir.py   # L1 : API GATE — isomorphie OpenAPI <-> IR
├── test_identity.py             # L1 : tenant du corps refusé ; sans jeton -> 401
├── test_sse_contract.py         # L1 : chaque data: parse en RunEvent ; run_finished en dernier
├── test_status_mapping.py       # L1 : table exhaustive [CLASS] -> HTTP ; bornes != 5xx
└── test_cancellation.py         # L1 : déconnexion -> run annulé, budget arrêté
```

---

## 5. Conventions imposées

1. **L'identité ne vient jamais du corps.** `security.py` produit un objet
   `Identity` depuis les en-têtes, et lui seul alimente `ToolContext`. Un
   `tenant_id` dans le corps JSON est un `400` explicite —
   `[SERVING_IDENTITY_FROM_PAYLOAD]` — et non une valeur ignorée : ignorer
   silencieusement laisse croire à l'appelant qu'il a choisi son tenant.
2. **`ApiAuthMode: none` exige que la MISSION déclare un acteur anonyme.**
   Sinon, refus au démarrage (`/readyz` rouge, `[CONFIG_INVALID]`) — un service
   qui démarre sans authentification alors que la spec n'en prévoit pas est
   l'incident qui attend son heure.
3. **Aucun secret dans une réponse, aucune trace de secret dans un log.** Le
   handler global d'erreurs rend `{class, message, run_id}` — jamais une
   `stack trace`, jamais le contenu d'un prompt, jamais l'argument d'un outil.
4. **La déconnexion annule le run.** `sse.py` surveille
   `await request.is_disconnected()` et annule la tâche : le budget s'arrête,
   `run_finished` est écrit dans la trace avec `cancelled: true`. Sans cela, un
   client qui recharge sa page paie deux runs et le second n'a pas de lecteur.
5. **`/readyz` ne coûte aucun token.** Il vérifie des hashes et des
   connexions, jamais un appel de modèle « pour tester la clé » (même piège
   qu'en CLI, §7.12).
6. **Le corps est plafonné** (`max_input_bytes`, défaut 256 Ko) **avant** toute
   désérialisation complète, et `413` sort avant le premier appel LLM.
7. **Un `thread_id` en cours refuse un second run concurrent** (`409`). Deux
   runs sur le même thread corrompent le checkpoint, et la corruption se
   découvre à la reprise suivante.
8. **`resume` n'est enregistré que si la stack fournit un checkpointer.**
   Sans lui, la route **n'existe pas** — absente de l'OpenAPI — plutôt que
   présente et cassée.
9. **Le `lifespan` flushe les traces à l'arrêt.** Un `SIGTERM` d'orchestrateur
   ne doit pas perdre les spans du dernier run : c'est précisément celui qu'on
   voudra relire.
10. **CORS fermé par défaut.** Aucune origine autorisée tant que `STACK.md` n'en
    déclare pas. Un `allow_origins=["*"]` sur un service qui porte une identité
    au transport est une faille, pas une commodité de développement.

---

## 6. L'API GATE — l'OpenAPI est dérivé, jamais écrit

C'est la transposition de l'**API Gate** de SDD_Pro, qui validait le contrat
back↔front en mémoire avant de générer le front. Ici la couture est la même,
entre l'IR et le monde extérieur.

`schemas.py` est **généré** depuis l'IR :

```
IR .agents[root].inputSchema   ->  RunRequest.input
IR .agents[root].outputSchema  ->  RunResponse.output  et  RunEvent.final.output
IR .budget                     ->  bornes de RunRequest.max_budget_usd
IR .orchestration.humanInTheLoop -> présence de /v1/runs/{thread_id}/resume
```

Le test `test_openapi_matches_ir.py` (L1, 0 token) échoue si :

| Divergence | Classe |
|---|---|
| un champ de `RunRequest` absent de `inputSchema` | `[API_CONTRACT_DRIFT]` |
| un champ requis de `inputSchema` absent de l'OpenAPI | `[API_CONTRACT_DRIFT]` |
| `/resume` publié sans `humanInTheLoop` dans l'IR | `[API_ROUTE_UNBACKED]` |
| une route publiée qu'aucun `RunService` ne sert | `[API_ROUTE_UNBACKED]` |
| un statut HTTP émis absent de `status.py` | `[API_STATUS_UNMAPPED]` |

`ApiContractFirst: false` désactive ce test — et exige un ADR référencé. C'est
le seul réglage de cette fiche qui autorise une divergence, et il doit donc
être une décision écrite.

---

## 7. Commande de smoke

Déterministe, 0 token :

```bash
cd workspace/src/{AppName}
uv run {AppName}-serve --check                      # crée l'app, n'écoute pas ; exit 0
uv run pytest tests/serving/http -q                 # API GATE + identité + SSE + annulation

# avec le service lancé (uvicorn en arrière-plan) :
curl -fsS localhost:${ServingLocalPort}/healthz
curl -fsS localhost:${ServingLocalPort}/readyz      # 503 si IR périmé / prompt absent
curl -fsS localhost:${ServingLocalPort}/openapi.json | python -m json.tool > /dev/null
curl -fsS localhost:${ServingLocalPort}/v1/runs -X POST -d '{}' -H 'Content-Type: application/json'
#   -> 401 attendu si ApiAuthMode != none : l'absence d'identité se refuse AVANT toute validation
```

Le premier run réel appartient à la L7 (ORCH GATE), via le runner d'eval qui
appelle `POST /v1/runs` en SSE avec un jeton de test et lit `run_finished`.

---

## 8. Pièges connus

1. **`tenant_id` lu dans le corps « en attendant l'authentification ».** Le
   provisoire reste, et tout le filtrage à la source devient décoratif :
   l'appelant choisit le tenant qu'il veut voir. C'est la faille la plus
   fréquente de cette surface, et elle passe toutes les revues de code parce
   qu'elle ressemble à du câblage.
2. **Buffering du proxy.** Sans `X-Accel-Buffering: no`, nginx retient le flux :
   le streaming est implémenté, testé en local, et n'existe pas en production.
3. **Pas de keepalive.** Un agent qui réfléchit 40 s derrière un proxy à
   timeout 30 s produit une déconnexion que le client lit comme une panne.
4. **`async def` avec un appel bloquant dedans.** Un client HTTP synchrone ou
   un `psycopg` non async bloque la boucle : le service sert une requête à la
   fois et le p95 s'effondre sous deux clients. Tout appel bloquant passe par
   `run_in_threadpool`.
5. **Run non annulé à la déconnexion.** Le client ferme, le run continue, le
   budget se consomme pour personne. Sur un service public, c'est aussi le
   vecteur d'épuisement de budget le moins coûteux à exploiter.
6. **Statut `500` sur une borne atteinte.** Les tableaux de bord
   d'exploitation s'allument pour un comportement nominal ; au bout d'une
   semaine, l'alerte est désactivée — y compris pour les vraies pannes.
7. **`/readyz` qui appelle le modèle.** Chaque sonde coûte des tokens, et une
   limite de quota fait retirer le service du pool alors que sa configuration
   est parfaite.
8. **OpenAPI écrit à la main « parce que la génération est pénible ».** Le
   contrat diverge de l'IR au premier changement de schéma, et le client
   découvre l'écart en production. C'est exactement ce que l'API GATE existe
   pour empêcher.
9. **CORS `*` laissé après le développement.** Le service porte une identité au
   transport : une origine libre transforme chaque navigateur visitant un site
   tiers en appelant authentifié.
10. **Erreur rendue avec sa `stack trace`.** Elle porte les chemins, parfois un
    fragment de prompt, parfois un identifiant de connexion. Le handler global
    ne rend jamais autre chose que `{class, message, run_id}`.
11. **`thread_id` deviné ou séquentiel.** Un identifiant prévisible permet de
    reprendre la conversation d'autrui. ULID, et vérification que le thread
    appartient au tenant appelant — `403` sinon.
12. **Traces perdues au `SIGTERM`.** L'orchestrateur arrête le pod, le
    `BatchSpanProcessor` n'exporte pas, et le run qu'on voulait justement
    analyser est le seul qui manque. Le `lifespan` force le flush.
13. **`uvicorn --reload` ou `--workers N` en production avec un checkpointer
    en mémoire.** Chaque worker a son état : une reprise tombe sur un worker
    qui n'a jamais vu le thread. Le checkpointer doit être partagé dès qu'il y
    a plus d'un worker — vérifié par `/readyz`.
