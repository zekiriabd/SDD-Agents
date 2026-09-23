# Stack: python-fastapi (backend)

Stack ID: backend-python-fastapi
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: la **maison HTTP** autour de la surface `serving/fastapi-sse.md` quand `DeliverableType: backend-api` et `ApiFramework: fastapi` — projet, composition, configuration, middlewares transverses, sécurité, journalisation, packaging. Hérité de SDD_Pro `backend/python-fastapi.md`, transposé : aucun ORM, aucune entité, aucune migration — l'accès aux données appartient à `dataaccess/`, le contrat HTTP est dérivé de l'IR. **Pas de `.libs.json` propre** : les pins vivent dans `serving/fastapi-sse.libs.json`, seul fichier qui fasse foi pour la famille FastAPI ; deux catalogues sur les mêmes paquets seraient deux vérités.

---

## 1. Rôle et périmètre

`serving/fastapi-sse.md` dit **par où l'on entre** : les routes, le flux SSE,
l'identité au transport, le contrat dérivé de l'IR. Cette fiche dit **dans
quelle maison** ces routes vivent : comment le projet est construit, comment
les dépendances sont composées et injectées, d'où vient la configuration,
quels middlewares s'appliquent à toute requête, comment on journalise, et ce
qu'on livre à la fin. C'est le périmètre de `dev-backend` ; `dev-api` habite la
maison, il ne la construit pas.

Ce que la fiche impose et que SDD_Pro imposait déjà, gardé tel quel : la
séparation stricte des couches (`archi/{mvc|ddd}.md`), l'injection de
dépendances systématique, la validation déclarative au bord, la gestion
centralisée des erreurs en `ProblemDetails`, les logs structurés, l'absence de
tout secret dans le code. Ce qui est **retiré**, et pourquoi : SQLAlchemy,
Alembic, le scaffolding Database-First — un système agentic lit ses sources
sous l'enveloppe de `## Active Data Access`, il ne possède pas de schéma
relationnel ; un ORM dans la coquille est le premier pas vers une règle métier
hors du chemin que les evals parcourent.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `backend-python-fastapi` |
| **Langage** | Python 3.12 (`lang/python.md`) |
| **Framework HTTP** | FastAPI 0.141.x · `uvicorn[standard]` 0.53.x — pins et rationales dans `serving/fastapi-sse.libs.json` |
| **Build** | `uv` — `pyproject.toml` + `uv.lock` à `workspace/src/{AppName}/` |
| **Validation** | Pydantic v2 (modèles **générés** depuis l'IR par `gen_app_skeleton`) |
| **Config** | `pydantic-settings` — lit les NOMS de variables ; valeurs dans `.env` (dev) ou l'environnement (prod) |
| **Logs** | `structlog`, JSON sur stderr ; le canal de résultat ne porte que du NDJSON/SSE |
| **Surface associée** | `serving/fastapi-sse.md` (obligatoire) · `serving/cli.md` (toujours générée : smoke et evals) |
| **Paramètres STACK.md** | `DeliverableType: backend-api`, `ApiFramework: fastapi`, `ApiAuthMode`, `ApiContractFirst`, `## Active Architecture Pattern` |
| **Smoke** | §8 — `uv run {AppName}-serve` puis `GET /healthz` → 200, `GET /openapi.json` conforme à l'IR |

---

## 3. Mapping couche → répertoire

Sur `archi/mvc.md` §3, surchargé pour l'écosystème (racine
`workspace/src/{AppName}/src/{AppName}/`) :

| Couche | Emplacement |
|---|---|
| Entrée HTTP | `serving/http/app.py` (factory `create_app()`), `serving/http/routes/runs.py`, `serving/http/routes/health.py`, `serving/http/openapi.py` (export du contrat) |
| Service | `app/run_service.py` (`RunService`) · `app/usecases/` |
| Domaine | `app/domain/{contexte}/` — `values.py`, `rules.py`, `ports.py` (`Protocol`) |
| Composition | `app/composition.py` — la seule fonction qui instancie : `build_system(settings) -> RunService` |
| Config | `app/config.py` (`Settings(BaseSettings)`) · `app/app_config.json` (généré : bornes, tiers, tarifs) |
| Modèle | `app/models.py` (généré) · `data/schemas/` (schémas figés) |
| Middlewares | `serving/http/middleware/` — erreurs → ProblemDetails, identité, en-têtes de sécurité, corrélation |
| Résilience | `app/resilience.py` — politiques `tenacity` nommées par dépendance |
| Tests transverses | `workspace/src/{AppName}/tests/` |
| Projet | `workspace/src/{AppName}/pyproject.toml` · `README.md` · `Dockerfile` (si `container`) |

Les répertoires du moteur (`agents/`, `orchestration/`, `tools/`, `retrieval/`,
`data/`) ne sont **pas** touchés par cette fiche : la matrice d'ownership les
possède déjà.

---

## 4. Idiomes imposés

**Composition et injection** :
- une seule factory `create_app()` qui reçoit un `RunService` **déjà composé**
  par `app/composition.py` ; l'app FastAPI ne compose rien ;
- `Depends()` pour tout ce qui varie par requête (identité, `run_id`,
  `deadline`) ; jamais pour construire le système ;
- aucun `global`, aucun singleton module-level hors `settings`.

**Configuration** :
- `class Settings(BaseSettings)` avec `SettingsConfigDict(env_file=".env")` ;
  chaque champ sensible est un `SecretStr` ; le nom de la variable est celui de
  `STACK.md ## Active Secrets` ;
- **fail-fast** : une variable manquante arrête le démarrage avec le nom de la
  variable, jamais avec une valeur ;
- aucun `os.environ[...]` direct sur une clé sensible
  (`[SEC_ENV_VAR_FORBIDDEN]`).

**Validation et contrat** :
- `RunRequest`/`RunResponse` sont **générés** depuis `inputSchema`/`outputSchema`
  de l'IR ; les réécrire à la main est la voie de `[API_CONTRACT_DRIFT]` ;
- `model_config = ConfigDict(frozen=True, extra="forbid")` sur tout modèle
  d'échange : un champ inconnu est refusé, pas ignoré.

**Erreurs** :
- un seul gestionnaire d'exceptions global (`register_exception_handlers`) qui
  mappe les erreurs nommées du run vers la table `[CLASS] → statut` de
  `serving/cli.md` §3.3, en `ProblemDetails` RFC 9457 ; aucun `try/except` de
  formatage dans une route ;
- une exception inattendue rend `500` avec un `run_id`, jamais une stack trace.

**Transverse** :
- `lifespan` asynccontextmanager : compose au démarrage, ferme au signal ;
- en-têtes de sécurité (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, HSTS derrière TLS) posés par un middleware, pas par route ;
- CORS explicite depuis `Settings.cors_allowed_origins` ; `*` interdit dès que
  `allow_credentials=True` ;
- `async def` partout où l'on attend ; `httpx.AsyncClient` partagé via la
  composition ; jamais `requests`, jamais `time.sleep`.

**Logs** :
- `structlog` configuré une fois ; `run_id`, `trace_id`, `tenant` (haché selon
  `TracePIIPolicy`) liés au contexte ; aucun `print()`.

---

## 5. Librairies

Source de vérité : `serving/fastapi-sse.libs.json`. Cette fiche n'ajoute
**aucune** dépendance ; elle dit lesquelles la maison utilise et pourquoi.

| Usage | Paquet | Note |
|---|---|---|
| serveur, routage, DI | `fastapi`, `uvicorn[standard]` | CORE |
| flux d'événements | `sse-starlette` | CORE — `dev-api` |
| modèles générés, settings | `pydantic`, `pydantic-settings` | CORE |
| logs, HTTP sortant, résilience | `structlog`, `httpx`, `tenacity` | CORE |
| lint, typage | `ruff`, `mypy` | CORE (L0) |
| quota par appelant | `slowapi` | ON-DEMAND `rate-limit` |
| jetons | `pyjwt[crypto]`, `msal` | ON-DEMAND `auth-jwt`, `auth-azure-ad` |
| propagation de trace | `opentelemetry-instrumentation-fastapi` | ON-DEMAND `otel-http` |

**Absents par conception** (déclarés dans le catalogue, avec la raison) :
`sqlalchemy`, `alembic`, `celery`. Toute librairie hors catalogue est
`[STACK_LIBRARY_MISSING]` : la déclarer d'abord, puis l'installer.

---

## 6. Interdits — vérifiés en L0 quand un analyseur le permet

- logique métier dans une route ou un middleware ;
- appel de modèle (`langchain`, SDK fournisseur) hors de `agents/` ;
- lecture de fichier ou de base hors de `data/` et `retrieval/` ;
- `RunRequest`/`RunResponse` écrits à la main ;
- `os.environ` / `os.getenv` sur une clé sensible ; secret littéral ; chaîne de
  connexion littérale ;
- `print()`, `logging.info()` brut ; `requests` ; `time.sleep` en async ;
- `allow_origins=["*"]` avec identifiants ; route sans dépendance d'identité
  quand `ApiAuthMode` ≠ `none` ;
- `try/except` de formatage HTTP dans une route ;
- `Any`, `dict[str, Any]` non motivé dans une signature publique ;
- `TODO`, `FIXME`, placeholder dans le code livré ;
- `pip install` ad hoc, version non épinglée, préversion sans justification.

---

## 7. Pièges connus

1. **Une seconde composition dans `create_app()`.** Le CLI et le HTTP ont alors
   deux systèmes ; les evals mesurent celui du CLI, on livre l'autre. La factory
   reçoit le `RunService`, elle ne le construit pas.
2. **`pydantic-core` sans wheel pour un Python trop récent.** SDD_Pro l'a
   rencontré en 3.14 : rester sur le Python épinglé par `lang/python.md`, ne pas
   « profiter » d'une version plus neuve du runtime.
3. **Un client HTTP par requête.** Le pool se reconstruit à chaque appel ; p95
   dominé par les poignées de main TLS. Un `httpx.AsyncClient` composé une fois.
4. **`uvicorn --workers N` avec un checkpointer en mémoire.** Une reprise tombe
   sur un worker qui n'a jamais vu le run : `/readyz` doit vérifier que le
   checkpointer est partagé dès que `HumanInTheLoopEnabled: true`.
5. **CORS « pour le dev » laissé en production.** L'origine vient de `Settings`,
   avec un défaut `http://localhost:5173` que la production **remplace**.

---

## 8. Smoke

```bash
cd workspace/src/{AppName}
uv sync --frozen
uv run ruff check . && uv run mypy src/
uv run {AppName}-serve --port 8080 & PID=$!; sleep 3
curl -sf http://localhost:8080/healthz -o /dev/null
curl -sf http://localhost:8080/openapi.json > /tmp/openapi.json
kill $PID
python .sdda/sdda.py validate-api-contract --mission {n} --json      # le contrat publié contre l'IR (G6, part api)
python .sdda/sdda.py gen-app-skeleton --check                        # le squelette n'a pas dérivé
```

Timeout : 60 s. Un smoke qui doit joindre le fournisseur de modèles pour
répondre à `/healthz` est un smoke qui échouera en CI : `/healthz` ne vérifie
que le processus, `/readyz` vérifie les dépendances.
