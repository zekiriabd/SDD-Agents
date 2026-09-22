# Stack: otel-genai (observability)

> §2.3 (Librairies) régénérée depuis `otel-genai.libs.json` — ne pas éditer manuellement.

Stack ID: observability-otel-genai
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: observabilité de l'application générée via **OpenTelemetry** et les **conventions sémantiques GenAI** — spans à émettre (tour d'agent, appel LLM, appel d'outil, retrieval), attributs, métriques, export OTLP **et** export JSONL par run (`workspace/traces/runs/{run-id}.jsonl`, invariant `trace-emitted-per-run`), redaction PII avant export. Neutre vis-à-vis du backend (Collector, Jaeger, Tempo, Grafana, Datadog, Langfuse en récepteur OTLP…). Suppose `lang/python.md`.

---

## 1. Rôle et périmètre

Sans trace, un système non déterministe n'est pas débogable : il n'y a pas de
stack trace à lire (ARCHITECTURE §8). La trace est **l'artefact de première
classe** sur lequel s'appuient :

- la **L5** (trajectoires : ordre des outils, nombre de hops, bornes atteintes) —
  elle se mesure sur la trace, pas sur la réponse ;
- la **G6** (coût et latence **mesurés** ≤ budget déclaré) — le coût se somme sur
  les spans LLM ;
- la **console de validation** (coût par CAP, dérive des scores, top des outils
  en échec) — qui lit `workspace/traces/runs/*.jsonl` ;
- le **post-mortem** — quel document a été retrouvé, avec quel score, quel outil
  a échoué, quelle borne a coupé.

`otel-genai` est la stack d'observabilité **recommandée par défaut** parce
qu'elle est neutre : un seul jeu de spans alimente n'importe quel backend, et le
vocabulaire (`gen_ai.*`) est celui que les outils du marché lisent. LangSmith et
Langfuse sont des stacks alternatives ou complémentaires (`langsmith.md`,
`langfuse.md`) — Langfuse accepte l'OTLP produit ici.

Périmètre : spans et attributs (standards + extension `sdda.*`), métriques,
double export, redaction, structure générée, pièges. **Hors périmètre** : le
stockage et la visualisation (le backend), les métriques d'infrastructure
(CPU, HTTP côté serveur — instrumentation standard OTel, pas cette fiche).

### 1.1 Honnêteté sur la stabilité

Les conventions sémantiques GenAI d'OpenTelemetry sont **en développement**
(statut *Development*, pas *Stable*) à la date de rédaction. Les noms ont bougé
(`gen_ai.system` → `gen_ai.provider.name` en semconv 1.37). Conséquences
imposées : (1) tous les noms d'attributs vivent dans **un seul module**
(`tracing/semconv.py`), jamais en littéral ailleurs ; (2) la version de
`opentelemetry-semantic-conventions` est épinglée ; (3) le schéma JSONL émis
porte `semconv_version` pour que la console sache lire des traces anciennes.

---

## 2. Identité

### 2.1 Identité

- **Stack ID** : `observability-otel-genai`
- **Langage** : Python 3.12 (`lang/python.md`)
- **SDK** : `opentelemetry-api` / `opentelemetry-sdk` 1.4x · `opentelemetry-semantic-conventions` 0.6xb0 (incubating, `gen_ai.*`)
- **Export** : OTLP/HTTP (protobuf) vers `OTEL_EXPORTER_OTLP_ENDPOINT` **+** `JsonlSpanExporter` maison vers `workspace/traces/runs/{run-id}.jsonl` — les deux, toujours
- **Paramètres STACK.md** : `TraceLevel: full`, `TraceSampleRate: 1.0`, `TracePIIPolicy: redact`, `CostTrackingEnabled: true`
- **Opt-in de stabilité** : `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` posé par `tracing/setup.py`
- **Capture de contenu** : `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` — **`false` par défaut** ; `true` uniquement si `TracePIIPolicy: raw` (exige un ADR)

<!-- CORE_PACKAGES_START -->
```bash
# Auto-généré depuis otel-genai.libs.json — ne pas éditer.
uv add --project workspace/src/{AppName} \
  opentelemetry-api==1.40.0 \
  opentelemetry-sdk==1.40.0 \
  opentelemetry-exporter-otlp-proto-http==1.40.0 \
  opentelemetry-semantic-conventions==0.61b0 \
  pydantic==2.13.5 \
  pydantic-settings==2.15.0 \
  structlog==26.1.0 \
  ruff==0.16.5 \
  mypy==2.3.1
```
<!-- CORE_PACKAGES_END -->

<!-- ONDEMAND_PACKAGES_START -->
```bash
# Auto-généré depuis otel-genai.libs.json (on-demand).
# capability: instrument-httpx
uv add --project workspace/src/{AppName} opentelemetry-instrumentation-httpx==0.61b0
# capability: instrument-psycopg
uv add --project workspace/src/{AppName} opentelemetry-instrumentation-psycopg==0.61b0
# capability: exporter-grpc
uv add --project workspace/src/{AppName} opentelemetry-exporter-otlp-proto-grpc==1.40.0
```
<!-- ONDEMAND_PACKAGES_END -->

<!-- LIBS_CATALOG_START -->
### 2.3 Librairies

> Source de vérité : `.sdda/stacks/observability/otel-genai.libs.json`. Pins à
> re-résoudre contre PyPI au premier bootstrap (design-phase) ; `api`, `sdk`,
> `exporter` **doivent** partager la même version ; `semantic-conventions` et
> `instrumentation-*` partagent la leur (`0.NNb0`).

| Lib | Version | Rôle |
|---|---|---|
| opentelemetry-api | 1.40.0 | `trace.get_tracer`, `Span`, propagation de contexte |
| opentelemetry-sdk | 1.40.0 | `TracerProvider`, `BatchSpanProcessor`, `SpanExporter`, `MeterProvider` |
| opentelemetry-exporter-otlp-proto-http | 1.40.0 | export OTLP/HTTP vers le Collector |
| opentelemetry-semantic-conventions | 0.61b0 | constantes `gen_ai.*`, `db.*` (incubating) |
| pydantic / pydantic-settings | 2.13.5 / 2.15.0 | `TraceEvent` (schéma JSONL), config |
| structlog | 26.1.0 | logs corrélés (`trace_id`, `span_id` injectés) |
| ruff / mypy | 0.16.5 / 2.3.1 | L0 |

On-demand : `opentelemetry-instrumentation-httpx` (spans HTTP sortants des
outils REST), `opentelemetry-instrumentation-psycopg` (spans SQL de
`view-per-agent` et `pgvector` — attention `db.query.text`, §5.4),
`opentelemetry-exporter-otlp-proto-grpc` si le Collector n'expose que gRPC.
<!-- LIBS_CATALOG_END -->

---

## 3. Mapping des concepts SDD_Agents → spans et attributs

### 3.1 Les spans à émettre

| Événement SDD_Agents | Nom du span (semconv) | `gen_ai.operation.name` | Attributs standard | Extension `sdda.*` |
|---|---|---|---|---|
| **RUN** (racine) | `sdda.run {mission_id}` | — | `service.name`, `service.version` | `sdda.run.id`, `sdda.mission.id`, `sdda.ir.hash`, `sdda.stack.hash`, `sdda.serving.surface`, `sdda.caller.tenant_hash` |
| **Tour d'agent** (AGENT) | `invoke_agent {gen_ai.agent.name}` | `invoke_agent` | `gen_ai.agent.id`, `gen_ai.agent.name`, `gen_ai.conversation.id` (= `thread_id`) | `sdda.agent.iteration`, `sdda.agent.model_tier`, `sdda.prompt.hash`, `sdda.cap.ids[]`, `sdda.bounds.max_iterations`, `sdda.bounds.max_tool_calls`, `sdda.bounds.budget_usd`, `sdda.bound.exceeded` (nom ou absent), `sdda.bound.policy_applied` |
| **Appel LLM** | `chat {gen_ai.request.model}` | `chat` | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.request.temperature`, `gen_ai.request.max_tokens`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons[]`, `gen_ai.response.id`, `server.address` | `sdda.usage.cache_read_tokens`, `sdda.usage.cache_write_tokens`, `sdda.cost.usd`, `sdda.pricing.version`, `sdda.model.tier`, `sdda.llm.structured_output` (bool), `sdda.llm.tool_calls_requested` (int) |
| **Contenu LLM** (opt-in) | événements sur le span `chat` | — | `gen_ai.system_instructions`, `gen_ai.input.messages`, `gen_ai.output.messages` | émis **uniquement** si `TracePIIPolicy: raw` ; sinon `sdda.input.hash`, `sdda.output.hash` (sha256 du contenu) |
| **Appel d'outil** (TOOL) | `execute_tool {gen_ai.tool.name}` | `execute_tool` | `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.tool.type` (`function` \| `extension` pour MCP \| `datastore` pour view-per-agent), `gen_ai.tool.description` (**hash** en `sdda.tool.description.hash`, pas le texte), `error.type` | `sdda.tool.id` (contrat), `sdda.tool.side_effect_class`, `sdda.tool.trust`, `sdda.tool.transport`, `sdda.tool.server`, `sdda.tool.error_code` (déclaré), `sdda.tool.args` (**redigés**, JSON), `sdda.tool.result.bytes`, `sdda.tool.result.truncated`, `sdda.tool.retry_attempt` |
| **Embedding** | `embeddings {gen_ai.request.model}` | `embeddings` | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.request.encoding_formats[]` | `sdda.cost.usd`, `sdda.embedding.dims`, `sdda.embedding.batch_size` |
| **Retrieval** (RETRIEVER) | `sdda.retrieve {index_id}` | — (pas de semconv stable) | `db.system.name` (`postgresql`), `db.namespace`, `db.operation.name` (`select`), `db.response.returned_rows` | `sdda.retrieval.index_id`, `sdda.retrieval.index_hash`, `sdda.retrieval.pattern` (`hybrid`), `sdda.retrieval.legs[]`, `sdda.retrieval.top_k`, `sdda.retrieval.candidates_per_leg`, `sdda.retrieval.rrf_k`, `sdda.retrieval.weights` (JSON), `sdda.retrieval.query.hash`, `sdda.retrieval.result.ids[]`, `sdda.retrieval.result.scores[]`, `sdda.retrieval.result.provenance` (JSON), `sdda.retrieval.result.doc_ids[]` |
| **Requête DATA ACCESS** | `sdda.data.query {view}` | — | `db.system.name`, `db.namespace` (`agent_views`), `db.operation.name`, `db.query.text` (**paramétré**, jamais avec valeurs), `db.response.returned_rows`, `db.response.status_code` | `sdda.data.view`, `sdda.data.role`, `sdda.data.truncated`, `sdda.data.filters.keys[]` |
| **Guardrail** | `sdda.guardrail {id}` | — | — | `sdda.guardrail.id`, `sdda.guardrail.stage` (`input` \| `output`), `sdda.guardrail.passed`, `sdda.guardrail.on_trip`, `sdda.guardrail.reason.hash` |
| **Franchissement de gate** (construction) | `sdda.gate {gate}` | — | — | `sdda.gate.id`, `sdda.gate.verdict` (`green` \| `yellow` \| `red`), `sdda.gate.error_class` |
| **Borne atteinte** | événement `sdda.bound_exceeded` sur le span agent | — | — | `sdda.bound.name`, `sdda.bound.limit`, `sdda.bound.observed`, `sdda.bound.policy` |

Règles de forme :
- **Hiérarchie** : `sdda.run` → `invoke_agent` → (`chat` \| `execute_tool` \| `sdda.retrieve` → `embeddings` + `sdda.data.query`) ; un sous-agent est un `invoke_agent` enfant (la profondeur de délégation se lit dans l'arbre).
- **Statut** : `StatusCode.ERROR` + `error.type` pour toute exception ; une borne atteinte avec politique `fail-explicit` est `ERROR`, avec `degrade` est `OK` + événement, avec `escalate-human` est `OK` + événement `sdda.interrupted`.
- **Tout attribut de texte libre est soit hashé, soit redigé** (§5.4). Les scores, identifiants, compteurs, coûts sont en clair : c'est ce que la L5 et la console consomment.

### 3.2 Métriques

| Instrument | Type | Attributs | Usage |
|---|---|---|---|
| `gen_ai.client.token.usage` | histogramme | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.token.type` (`input` \| `output`) | standard semconv |
| `gen_ai.client.operation.duration` | histogramme (s) | idem + `error.type` | standard semconv |
| `sdda.run.cost_usd` | histogramme | `sdda.mission.id`, `sdda.serving.surface` | distribution du coût par run vs `CostPerRunTargetUsd` |
| `sdda.run.hops` | histogramme | `sdda.mission.id` | la queue à 12 hops (TESTING-AND-EVAL §6) |
| `sdda.bound.exceeded` | compteur | `sdda.bound.name`, `sdda.bound.policy`, `gen_ai.agent.name` | fréquence des bornes atteintes |
| `sdda.tool.errors` | compteur | `gen_ai.tool.name`, `sdda.tool.error_code` | top des outils en échec |
| `sdda.guardrail.trips` | compteur | `sdda.guardrail.id`, `sdda.guardrail.stage` | |

### 3.3 Helpers imposés

```python
# src/{AppName}/tracing/spans.py — les SEULS points d'entrée du code applicatif vers OTel
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager

from opentelemetry import trace
from . import semconv as sc
from .redaction import redact_args

_tracer = trace.get_tracer("sdda", schema_url=sc.SCHEMA_URL)


@contextmanager
def agent_turn(*, agent_id: str, agent_name: str, thread_id: str, tier: str, prompt_hash: str, bounds: Bounds, iteration: int):
    with _tracer.start_as_current_span(f"invoke_agent {agent_name}") as span:
        span.set_attributes({
            sc.GEN_AI_OPERATION_NAME: "invoke_agent",
            sc.GEN_AI_AGENT_ID: agent_id, sc.GEN_AI_AGENT_NAME: agent_name,
            sc.GEN_AI_CONVERSATION_ID: thread_id,
            sc.SDDA_AGENT_MODEL_TIER: tier, sc.SDDA_PROMPT_HASH: prompt_hash,
            sc.SDDA_AGENT_ITERATION: iteration,
            sc.SDDA_BOUNDS_MAX_ITERATIONS: bounds.max_iterations,
            sc.SDDA_BOUNDS_MAX_TOOL_CALLS: bounds.max_tool_calls,
            sc.SDDA_BOUNDS_BUDGET_USD: bounds.budget_usd,
        })
        yield AgentSpan(span)          # .bound_exceeded(name, limit, observed, policy) ; .interrupted(reason)


@contextmanager
def tool_call(*, spec: ToolSpec, call_id: str, args: dict[str, object]):
    with _tracer.start_as_current_span(f"execute_tool {spec.name}") as span:
        span.set_attributes({
            sc.GEN_AI_OPERATION_NAME: "execute_tool",
            sc.GEN_AI_TOOL_NAME: spec.name, sc.GEN_AI_TOOL_CALL_ID: call_id, sc.GEN_AI_TOOL_TYPE: spec.tool_type,
            sc.SDDA_TOOL_ID: spec.id, sc.SDDA_TOOL_SIDE_EFFECT_CLASS: spec.side_effect_class, sc.SDDA_TOOL_TRUST: spec.trust,
            sc.SDDA_TOOL_ARGS: redact_args(args, pii_fields=spec.pii_fields),   # redigé AVANT d'entrer dans le span
        })
        yield ToolSpan(span)           # .result(bytes, truncated) ; .declared_error(code) ; .retry(attempt)
```

`llm_call(...)` et `retrieval(...)` suivent le même modèle. Le code des agents
n'importe **jamais** `opentelemetry` directement : uniquement `tracing.spans`.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/src/{AppName}/tracing/
├── __init__.py
├── semconv.py            # TOUTES les constantes d'attributs (gen_ai.* via opentelemetry.semconv._incubating, sdda.* locales) + SCHEMA_URL + SEMCONV_VERSION
├── setup.py              # configure_tracing(settings, run_id) -> TracerProvider : Resource, sampler, BatchSpanProcessor(OTLP) + SimpleSpanProcessor(Jsonl), RedactionProcessor, MeterProvider ; shutdown()
├── spans.py              # agent_turn, llm_call, tool_call, retrieval, data_query, guardrail — helpers imposés
├── redaction.py          # RedactionSpanProcessor : PII regex (e-mail, téléphone, IBAN, carte), champs @pii, valeurs de secrets connus, politique redact|hash
├── jsonl_exporter.py     # JsonlSpanExporter : 1 span = 1 ligne, schéma TraceEvent (pydantic), fichier workspace/traces/runs/{run-id}.jsonl, flush à chaque span
├── pricing.py            # cost_usd(model_id, usage) depuis .sdda/python/sdda_lib/pricing (table versionnée) — sdda.pricing.version
└── logging.py            # structlog processor : injecte trace_id / span_id dans chaque log

workspace/traces/runs/
└── {run-id}.jsonl        # généré à chaque run ; lu par la console et par la L5 ; ownership : script uniquement

workspace/src/{AppName}/tests/tracing/
├── test_redaction.py     # L1 : e-mail/IBAN/téléphone redigés ; champ @pii redigé ; valeur d'un secret connu redigée ; policy hash stable
├── test_jsonl_schema.py  # L1 : chaque ligne valide TraceEvent ; run.id présent ; semconv_version présent
├── test_span_shape.py    # L1 : avec InMemorySpanExporter, un tour d'agent mocké produit invoke_agent > chat + execute_tool avec les attributs obligatoires
└── test_cost.py          # L1 : usage → cost_usd via table de pricing épinglée ; cache tokens comptés
```

Schéma d'une ligne JSONL (`TraceEvent`) :

```json
{"semconv_version":"0.61b0","run_id":"…","trace_id":"…","span_id":"…","parent_span_id":"…",
 "name":"execute_tool billing_specialist_unpaid_invoices","start":"2026-09-20T14:12:03.120Z","end":"…","duration_ms":312,
 "status":"OK","attributes":{"gen_ai.operation.name":"execute_tool","gen_ai.tool.name":"…","sdda.tool.side_effect_class":"read-only","sdda.tool.args":"{\"customer_id\":\"[REDACTED:uuid]\"}"},
 "events":[]}
```

---

## 5. Conventions imposées

### 5.1 Émission

1. **Un span racine `sdda.run` par exécution**, `run_id` unique (ULID), propagé
   en `baggage` pour que tout span enfant le porte ; `gen_ai.conversation.id` =
   `thread_id` (peut être partagé entre runs en reprise).
2. **Chaque appel LLM émet `chat`** avec les tokens et `sdda.cost.usd`.
   `CostTrackingEnabled: true` = une valeur `usage` absente est une **erreur**
   (`[TRACE_USAGE_MISSING]`), pas un zéro silencieux.
3. **Chaque appel d'outil émet `execute_tool`**, y compris les échecs, y
   compris les appels refusés par un guardrail (span avec `error.type`).
4. **Chaque retrieval émet `sdda.retrieve`** avec les identifiants et scores
   des chunks retournés — c'est ce qui rend `citation_resolve_rate` et le
   diagnostic « quelle jambe a remonté le document » possibles.
5. **Chaque borne atteinte émet l'événement `sdda.bound_exceeded`** sur le
   span agent, **et** la métrique — la L5 vérifie que la politique déclarée
   est celle observée.
6. **`TraceSampleRate: 1.0` sur tout run d'eval.** L'échantillonnage est
   réservé à la production ; une eval sans trace complète n'a pas de L5.

### 5.2 Export

7. **Double export, toujours** : OTLP (Collector) **et** JSONL local. Si
   `OTEL_EXPORTER_OTLP_ENDPOINT` est absent, l'export OTLP est désactivé avec
   un avertissement ; le JSONL n'est **jamais** désactivable
   (`trace-emitted-per-run`).
8. **`SimpleSpanProcessor` pour le JSONL** (écriture immédiate, un run qui
   crashe laisse quand même sa trace), `BatchSpanProcessor` pour OTLP.
9. **`shutdown()` explicite** en fin de run (CLI : handler `atexit` + `SIGINT`)
   → `force_flush()` des deux processeurs.

### 5.3 Noms

10. **Aucun littéral `"gen_ai.…"` ou `"sdda.…"` hors `semconv.py`.** Lint L0 :
    grep dans `src/` hors `tracing/semconv.py` → `[TRACE_ATTR_LITERAL]`.
11. **Extension `sdda.*` documentée dans `semconv.py`** avec type et
    cardinalité ; toute nouvelle clé passe par ce fichier et par la console.

### 5.4 Redaction — `TracePIIPolicy`

12. Le `RedactionSpanProcessor` s'exécute **avant** tout exporteur, sur
    `on_end` : (a) champs déclarés `@pii` par les contrats → `[REDACTED:{type}]`
    ; (b) regex déterministes : e-mail, téléphone E.164/national, IBAN,
    numéro de carte (Luhn), numéro de sécurité sociale FR → `[REDACTED:{type}]`
    ; (c) **valeurs des secrets connus** : le processeur reçoit au démarrage les
    valeurs de tous les `SecretStr` de `Settings` et remplace toute occurrence
    par `[REDACTED:secret]` — un secret qui aurait fui dans un argument d'outil
    ou une réponse ne sort jamais du processus ; (d) `hash` → sha256 tronqué
    à 16 hex au lieu de `[REDACTED]`, pour corréler sans révéler.
13. **Le contenu des messages (`gen_ai.input.messages`, `gen_ai.output.messages`,
    `gen_ai.system_instructions`) n'est émis que si `TracePIIPolicy: raw`**
    (ADR obligatoire). Sinon : hash. Le prompt système est déjà connu par son
    `sdda.prompt.hash` — l'émettre en clair à chaque appel serait un coût et
    une fuite.
14. **`db.query.text` est le SQL paramétré** (`$1`, `%(customer_id)s`), jamais
    interpolé ; l'instrumentation `psycopg` auto est configurée avec
    `enable_commenter=False` et un hook qui vérifie l'absence de littéraux
    longs (`[TRACE_SQL_LITERAL]`).
15. **Le scan G7 lit les JSONL** (`[SECRET_LEAK]`, `[PII_IN_TRACE]`) : la
    redaction est une défense, le scan est la vérification.

---

## 6. Commande de smoke

Déterministe, 0 token, sans Collector :

```bash
cd workspace/src/{AppName}
uv run pytest tests/tracing -q
uv run python -m {AppName}.tracing.setup --selftest
#   1. configure_tracing(settings, run_id="smoke-…") sans OTLP endpoint → avertissement, pas d'erreur
#   2. émet un arbre mocké : sdda.run > invoke_agent > chat + execute_tool(args avec e-mail + secret factice) + sdda.retrieve
#   3. shutdown() → force_flush
#   4. relit workspace/traces/runs/smoke-….jsonl : 5 lignes ; toutes valides TraceEvent ; aucun e-mail, aucun secret en clair ([SECRET_LEAK] sinon)
#   5. vérifie gen_ai.usage.input_tokens/output_tokens et sdda.cost.usd > 0 sur le span chat
#   6. supprime le fichier de smoke ; exit 0
```

Si un Collector est déclaré (`OTEL_EXPORTER_OTLP_ENDPOINT`), un second smoke
`network` envoie le même arbre et vérifie le code HTTP 200 de l'export.

---

## 7. Pièges connus

1. **Les conventions GenAI bougent.** `gen_ai.system` est devenu
   `gen_ai.provider.name` ; `gen_ai.usage.prompt_tokens` est devenu
   `gen_ai.usage.input_tokens`. Un backend qui attend l'ancien nom affiche des
   panneaux vides. `semconv.py` centralise, `semconv_version` est dans chaque
   ligne JSONL, et la console porte une table de renommage.
2. **Usage en fin de stream.** En streaming, `usage_metadata` arrive avec le
   dernier chunk ; un span `chat` fermé trop tôt a 0 token. Le helper
   `llm_call` ne ferme le span qu'après consommation complète du flux.
3. **Le coût n'est pas dans les semconv.** `sdda.cost.usd` est calculé
   localement depuis une table de pricing **versionnée** (`sdda.pricing.version`)
   ; un tarif qui change rétroactivement fausserait les comparaisons de
   baselines — la version épingle.
4. **Tokens de cache.** `cache_read_input_tokens` ne sont pas facturés au même
   tarif ; les compter dans `input_tokens` surestime le coût de 10× sur des
   prompts longs cachés. Champs séparés `sdda.usage.cache_read_tokens` /
   `cache_write_tokens`, pricing distinct.
5. **Cardinalité des attributs de métriques.** `gen_ai.tool.name` sur un
   compteur : fine. `sdda.retrieval.query.hash` sur une métrique : explosion
   de séries. Les hashes vont sur les **spans**, jamais sur les métriques.
6. **Documents retrouvés dans les spans.** Mettre `content` des chunks dans
   `sdda.retrieve` = PII + volume + texte hostile dans le backend de traces.
   Identifiants, scores, provenance uniquement ; le contenu se relit dans
   l'index par `chunk_id`.
7. **`BatchSpanProcessor` et sortie de process.** Sans `force_flush`, les
   derniers spans (souvent les plus intéressants : l'erreur finale) sont
   perdus. CLI : `atexit` + gestion `SIGINT` ; serveur : hook `shutdown`.
8. **Contexte async perdu.** Un `asyncio.create_task` sans copie de contexte
   produit des spans orphelins (pas de parent). Utiliser
   `asyncio.gather` dans le span courant, ou `contextvars.copy_context()`.
9. **Double instrumentation.** LangSmith (`LANGCHAIN_TRACING_V2`), Langfuse
   callbacks **et** OTel actifs = trois traces, trois coûts, des spans
   dupliqués dans le backend OTLP si Langfuse ré-exporte. Une seule stack
   d'observabilité **runtime** active ; les autres sont des récepteurs OTLP.
10. **Redaction par regex = faux négatifs.** Un nom propre, une adresse
    postale, un numéro de dossier interne ne sont pas attrapés. Les champs
    `@pii` des contrats sont la première ligne ; les regex la seconde ; le
    scan G7 la vérification ; `TracePIIPolicy: raw` un ADR.
11. **Le fichier JSONL sur Windows.** Verrou de fichier si la console lit
    pendant l'écriture ; ouvrir en mode append avec `newline="\n"`,
    `encoding="utf-8"`, flush par ligne ; la console lit en tolérant une
    dernière ligne partielle.
12. **`TraceSampleRate < 1.0` pendant une eval.** Les runs non échantillonnés
    n'ont pas de trajectoire → la L5 conclut à tort « trajectoire absente ».
    Le runner d'eval force `1.0` et refuse de démarrer sinon (`[TRACE_SAMPLED_IN_EVAL]`).
13. **Instrumentation auto `psycopg` avec `db.query.text` interpolé** par un
    ORM mal configuré (`echo=True`, `literal_binds`). Vérifier en L1 qu'aucun
    littéral > 32 caractères n'apparaît dans `db.query.text`.
