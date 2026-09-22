# Stack: langchain (framework)

> §2.3 (Librairies) régénérée depuis `langchain.libs.json` — ne pas éditer manuellement.

Stack ID: framework-langchain
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: LangChain **seul, sans graphe** — outils, chaînes LCEL, sorties structurées, appel d'outils en un ou quelques tours. Suppose `lang/python.md` actif. Cette fiche dit explicitement ce que la stack **ne permet pas** et quand il faut activer `framework/langgraph.md` en plus.

---

## 1. Rôle et périmètre

LangChain sans LangGraph est la stack **minimale** de SDD_Agents en Python : une
couche d'abstraction sur les providers (`init_chat_model`, `bind_tools`,
`with_structured_output`), une définition d'outils typée (`@tool`,
`args_schema`), et un langage de composition de chaînes (LCEL) qui décrit un
**DAG** — jamais un cycle.

Elle est **suffisante** pour :

| Pattern d'orchestration | Condition |
|---|---|
| `single-agent` | ≤ 2 tours d'appel d'outils, boucle écrite à la main et bornée (§3.3) |
| `sequential` | pipeline d'étapes à schéma validé entre chaque étape, sans retour arrière |
| `parallel` | `RunnableParallel` + nœud de fusion déterministe |
| `router` | classification puis dispatch **en un seul passage** (pas de re-routage) |

Elle est **insuffisante** — et `STACK.md` doit alors activer aussi
`framework/langgraph.md` — dès que la TOPOLOGY comporte :

- un **cycle** (`supervisor`, `reflection`, `plan-execute` avec replanification,
  `graph`, `blackboard`) : LCEL ne sait pas revenir en arrière ; une boucle Python
  à la main au-delà de 2-3 tours n'a ni état persisté ni reprise ;
- une **reprise après interruption** ou `HumanInTheLoopEnabled: true` : aucun
  checkpointing ;
- `OnBoundExceeded: escalate-human` : l'escalade exige de suspendre et reprendre ;
- `max_delegation_depth > 0` : la délégation entre agents est un cycle déguisé ;
- un besoin de **trajectoire observable par étape** pour la L5 : LCEL trace des
  runnables, pas des nœuds de décision nommés.

> **Décision par défaut** : si la question « LangChain seul suffit-il ? » se pose,
> la réponse est presque toujours « oui pour le socle (outils, retrieval,
> structured output), non pour l'orchestration ». Les deux fiches sont conçues
> pour être **actives ensemble** — `langchain.md` porte les outils et les
> chaînes, `langgraph.md` porte le graphe. Cette fiche seule correspond au
> `TOPOLOGY` le plus simple, celui que P7 impose comme point de départ.

---

## 2. Identité

### 2.1 Identité

- **Stack ID** : `framework-langchain`
- **Langage** : Python 3.12 (`lang/python.md`)
- **Framework** : `langchain-core` 1.x (+ `langchain` 1.x pour `init_chat_model` et les utilitaires)
- **Build tool** : `uv`
- **Combinaisons validées** (SSoT : `compatibility.matrix.json`) : `python × langchain × {single-agent, sequential, parallel, router}`. Toute autre combinaison sans `langgraph` actif → `[STACK_COMBO_UNSUPPORTED]` au preflight.

### 2.2 Ce qui est délibérément exclu

| API | Statut | Raison |
|---|---|---|
| `AgentExecutor`, `create_react_agent` (legacy `langchain.agents`) | **interdit** | déprécié en 1.x ; `max_iterations` existe mais `early_stopping_method="force"` renvoie une chaîne canned, pas un état partiel structuré : ce n'est pas `fail-explicit`. Aucun checkpoint. |
| `langchain.agents.create_agent` (1.x) | **interdit dans cette stack** | c'est un graphe LangGraph sous le capot. L'utiliser = activer `langgraph.md` de fait sans le déclarer → `[STACK_COMBO_UNDECLARED]`. |
| `ConversationBufferMemory` et famille `langchain.memory` | **interdit** | supprimées/dépréciées ; la mémoire court terme est une liste de messages gérée par `memory/buffer.md`. |
| `langchain-community` | **on-demand uniquement**, par intégration nommée | surface immense, qualité hétérogène ; chaque import doit être justifié dans le `.libs.json`. |

<!-- CORE_PACKAGES_START -->
```bash
# Auto-généré depuis langchain.libs.json — ne pas éditer.
uv add --project workspace/src/{AppName} \
  langchain-core==1.1.6 \
  langchain==1.1.6 \
  langchain-text-splitters==1.0.2 \
  pydantic==2.13.5 \
  pydantic-settings==2.15.0 \
  structlog==26.1.0 \
  httpx==0.28.1 \
  tenacity==9.1.4 \
  ruff==0.16.5 \
  mypy==2.3.1
```
<!-- CORE_PACKAGES_END -->

<!-- ONDEMAND_PACKAGES_START -->
```bash
# Auto-généré depuis langchain.libs.json (on-demand).
# capability: provider-anthropic
uv add --project workspace/src/{AppName} langchain-anthropic==1.2.3
# capability: provider-openai
uv add --project workspace/src/{AppName} langchain-openai==1.2.1
# capability: provider-google
uv add --project workspace/src/{AppName} langchain-google-genai==3.2.0
```
<!-- ONDEMAND_PACKAGES_END -->

<!-- LIBS_CATALOG_START -->
### 2.3 Librairies

> Source de vérité : `.sdda/stacks/framework/langchain.libs.json`. Pins à
> re-résoudre contre PyPI au premier bootstrap (statut design-phase).

| Lib | Version | Rôle |
|---|---|---|
| langchain-core | 1.1.6 | `Runnable`, LCEL, messages, `@tool`, `BaseChatModel`, `with_structured_output` |
| langchain | 1.1.6 | `init_chat_model` (résolution provider par chaîne), utilitaires |
| langchain-text-splitters | 1.0.2 | `RecursiveCharacterTextSplitter` etc. — ingestion (cf. `rag/*.md`) |
| pydantic / pydantic-settings | 2.13.5 / 2.15.0 | `args_schema`, sorties structurées, config |
| structlog | 26.1.0 | logs |
| httpx / tenacity | 0.28.1 / 9.1.4 | outils HTTP + retry idempotent |
| ruff / mypy | 0.16.5 / 2.3.1 | L0 |
<!-- LIBS_CATALOG_END -->

---

## 3. Mapping des concepts SDD_Agents → idiomes LangChain

| Concept | Idiome LangChain | Notes |
|---|---|---|
| **MODEL BINDING** (tier) | `init_chat_model(settings.runtime_tier_map[tier], temperature=...)` dans `models.resolve(tier)` | seul endroit où un nom de modèle circule |
| **PROMPT** | `ChatPromptTemplate.from_messages([("system", loaded.text), MessagesPlaceholder("messages")])` où `loaded = load_system_prompt(slug)` | le texte vient du fichier ; **aucune** chaîne système littérale (`[PROMPT_INLINE]`) |
| **TOOL** | `StructuredTool.from_function(coroutine=fn, name=spec.name, description=spec.description, args_schema=InputModel)` — ou `@tool(args_schema=...)` | `description` vient du **tool-contract**, pas de la docstring ; `ToolSpec` (side_effect_class, trust, timeout) est porté à côté, LangChain ne le connaît pas |
| **TOOL** `output_schema` | la fonction retourne un `OutputModel` pydantic sérialisé en JSON dans le `ToolMessage` | validé avant retour → une sortie non conforme est `[TOOL_CONTRACT_FAILED]`, pas un texte que le modèle interprète |
| **TOOL** `errors[]` | exceptions typées (`ToolDeclaredError(code, agent_behavior)`) converties en `ToolMessage` structuré `{"error": code, "hint": agent_behavior}` par le wrapper | toute autre exception remonte |
| **AGENT** (single-agent) | `model.bind_tools(tools)` + boucle manuelle bornée (§3.3) + `with_structured_output(OutputModel)` pour le tour final | pas d'`AgentExecutor` |
| **AGENT** `outputSchema` | `model.with_structured_output(OutputModel, method="function_calling"\|"json_schema")` | `schema-validation` guardrail gratuit |
| **AGENT** `bounds` | `Bounds` de `lang/python.md` appliqué par la boucle manuelle ; `timeout_s` via `asyncio.timeout` ; `budget_usd` via `response.usage_metadata` | LangChain n'offre **aucune** de ces bornes |
| **AGENT** `trustPosture` | `HumanMessage(wrap_untrusted(...))` / `ToolMessage(...)` — jamais concaténé au `system` | P8 |
| Pattern `sequential` | `step1 \| validate(StepSchema1) \| step2 \| validate(StepSchema2)` — `validate` est un `RunnableLambda` déterministe | une eval par étape (L4) en plus de l'eval bout-en-bout |
| Pattern `parallel` | `RunnableParallel(a=chain_a, b=chain_b) \| merge` où `merge` est une fonction pure testée L1 | la stratégie de fusion est spécifiée dans la TOPOLOGY |
| Pattern `router` | `classify = model_fast.with_structured_output(Intent)` puis `dict[Intent, Runnable]` — **un seul passage**, `fallback` obligatoire | `RunnableBranch` toléré ; pas de retour vers le classifieur |
| **RETRIEVER** | fonction `retrieve(...)` de `retrieval/{index}/` enveloppée en `RunnableLambda` ; `BaseRetriever` toléré si un `VectorStore` LangChain est utilisé | la sortie est `Untrusted` |
| **MEMORY** court terme | `list[BaseMessage]` + `trim_messages(max_tokens=..., strategy="last")` déterministe | `memory/buffer.md` |
| **Streaming** | `chain.astream(...)` / `astream_events(version="v2")` | événements `on_chat_model_stream`, `on_tool_start/end` → spans |
| **TRACE SPAN** | callback `BaseCallbackHandler` maison branché sur `config={"callbacks": [tracer]}` | pas de `LANGCHAIN_TRACING_V2` implicite : LangSmith est une stack séparée et opt-in |

### 3.1 Outil : le contrat d'abord

```python
# src/{AppName}/tools/invoice_lookup.py
from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from {AppName}.tools.spec import ToolSpec, ToolDeclaredError

SPEC = ToolSpec.from_contract("1-invoice-lookup")   # lit workspace/feats/contracts/tools/1-invoice-lookup.tool.md → name, description, side_effect_class, trust, timeout_s, errors


class InvoiceLookupInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    invoice_id: str = Field(pattern=r"^INV-\d{4}-\d{4}$")


class InvoiceLookupOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    invoice_id: str
    amount_due: float
    status: str


async def _run(invoice_id: str) -> str:
    row = await _repo.get(invoice_id)
    if row is None:
        raise ToolDeclaredError("NOT_FOUND", agent_behavior="informer l'utilisateur, ne pas réessayer")
    return InvoiceLookupOutput(**row).model_dump_json()


invoice_lookup = StructuredTool.from_function(
    coroutine=_run,
    name=SPEC.name,
    description=SPEC.description,       # le contrat, pas la docstring
    args_schema=InvoiceLookupInput,
)
```

`extra="forbid"` : un modèle qui invente un argument reçoit une erreur de
validation explicite au lieu d'un appel silencieusement tronqué.

### 3.2 Sortie structurée

```python
structured = models.resolve(tier).with_structured_output(BillingAnswer, include_raw=True)
result = await structured.ainvoke(messages)
answer: BillingAnswer = result["parsed"]          # None si parsing échoué → guardrail schema-validation → on_trip
usage = result["raw"].usage_metadata               # tokens → cost_usd
```

`include_raw=True` est imposé : sans lui on perd `usage_metadata`, donc le
budget (P6) et la trace (§8 ARCHITECTURE).

### 3.3 La boucle single-agent bornée — la seule boucle autorisée ici

```python
# src/{AppName}/agents/{agent_slug}/agent.py
async def run_single_agent(user_input: Untrusted, *, deps: AgentDeps, bounds: Bounds) -> AgentResult:
    messages: list[BaseMessage] = [deps.system_message, HumanMessage(wrap_untrusted(user_input, source="user", max_chars=8000))]
    model = deps.model.bind_tools(deps.tools)      # liste CLOSE issue du contrat
    tool_calls_total, cost_usd = 0, 0.0

    async with asyncio.timeout(bounds.timeout_s):
        for iteration in range(bounds.max_iterations):          # borne 1 : itérations — jamais while True
            with deps.tracer.llm_call(tier=deps.tier, iteration=iteration) as span:
                ai = await model.ainvoke(messages)
                cost_usd += deps.pricing.cost(ai.usage_metadata)
                span.set_cost(cost_usd)
            messages.append(ai)

            if cost_usd > bounds.budget_usd:                    # borne 2 : budget
                return apply_bound_policy(bounds, "budget_usd", cost_usd, messages)
            if not ai.tool_calls:
                return await finalize(messages, deps)           # sortie structurée + validation
            if tool_calls_total + len(ai.tool_calls) > bounds.max_tool_calls:   # borne 3 : appels d'outils
                return apply_bound_policy(bounds, "max_tool_calls", tool_calls_total, messages)

            for call in ai.tool_calls:
                messages.append(await deps.execute_tool(call))  # wrapper : erreurs déclarées → ToolMessage structuré
                tool_calls_total += 1

    return apply_bound_policy(bounds, "max_iterations", bounds.max_iterations, messages)
```

`apply_bound_policy` implémente `fail-explicit` (résultat structuré d'échec avec
état partiel) et `degrade` (réponse depuis l'état partiel, `degraded: true`).
**`escalate-human` n'est pas implémentable ici** : sans checkpoint, il n'y a
rien à reprendre. Un contrat qui le déclare avec cette stack seule est refusé
par le preflight → `[STACK_CAPABILITY_MISSING: escalate-human requires checkpointing]`.

`TimeoutError` (asyncio) est convertie en `apply_bound_policy(..., "timeout_s", ...)`
par l'appelant.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/src/{AppName}/
├── models.py                    # resolve(tier) -> BaseChatModel via init_chat_model
├── tools/
│   ├── spec.py                  # ToolSpec.from_contract(id), ToolDeclaredError
│   ├── wrap.py                  # execute_tool(call) : timeout, erreurs déclarées, trace, output_schema
│   └── {tool_slug}.py           # StructuredTool + Input/Output pydantic
├── agents/{agent_slug}/
│   ├── agent.py                 # run_single_agent(...) bornée (§3.3) — ou chaîne LCEL pour sequential
│   ├── schemas.py               # Input / Output (with_structured_output)
│   └── deps.py
├── orchestration/
│   └── pipeline.py              # LCEL : sequential | parallel | router — DAG uniquement
└── tests/
    ├── tools/test_{tool_slug}.py            # L2 : happy, chaque erreur déclarée, timeout, extra="forbid"
    ├── agents/test_{agent_slug}_bounds.py   # L1 : max_iterations, max_tool_calls, budget, timeout — modèle mocké (FakeMessagesListChatModel)
    └── orchestration/test_pipeline.py       # L1 : schéma validé entre chaque étape
```

---

## 5. Conventions imposées

1. **La boucle de §3.3 est la seule forme de boucle autorisée** dans `agents/`.
   Toute autre boucle (`while`, récursion, `for` sur un itérable non borné) est
   `[UNBOUNDED_LOOP]` en L0. Si la boucle ne suffit pas, la réponse est
   `langgraph.md`, pas une boucle plus astucieuse.
2. **`description` et `name` d'un outil viennent de `ToolSpec.from_contract`**,
   jamais de la docstring ni d'un littéral. Le `tool_schema_hash` (P10) est
   calculé sur `(name, description, args_schema.model_json_schema())` — même
   fonction de hash que `sdda_lib.hashing`.
3. **`extra="forbid"` sur tout `args_schema`.**
4. **`include_raw=True` sur tout `with_structured_output`** ; `parsed is None`
   déclenche le guardrail `schema-validation`, jamais un `retry` implicite.
5. **Le modèle est résolu par tier** ; `init_chat_model("claude-…")` avec un
   littéral hors `models.py` est `[MODEL_NAME_HARDCODED]`.
6. **Pas de `RunnableWithMessageHistory`** : l'historique est une liste explicite
   passée à la chaîne, tronquée par `trim_messages` déterministe, pour que la
   L4 puisse figer l'entrée.
7. **Callbacks explicites** : `config={"callbacks": [tracer], "run_name": agent_id}`.
   Pas de variable d'environnement de tracing implicite.
8. **`FakeMessagesListChatModel` / `GenericFakeChatModel`** (`langchain_core.language_models.fake_chat_models`)
   sont les mocks obligatoires en L1/L2 : ils permettent de scripter `tool_calls`
   et donc de tester les bornes sans token.
9. **Retry uniquement via `tenacity` dans `tools/wrap.py`**, conditionné à
   `spec.retry_policy != "none"`. `model.with_retry()` est toléré sur l'appel
   LLM (idempotent par nature) avec `stop_after_attempt(2)` maximum — chaque
   retry coûte et compte dans `cost_usd`.

---

## 6. Commande de smoke

Déterministe, 0 token :

```bash
cd workspace/src/{AppName}
uv sync --frozen
uv run python -c "from {AppName}.tools.registry import all_specs; [s.validate_against_contract() for s in all_specs()]"
#   → chaque StructuredTool a name/description/args_schema identiques au tool-contract (hash)
uv run pytest tests/tools tests/agents tests/orchestration -q -m "not llm and not network"
#   → L2 outils + bornes avec modèle mocké
uv run python -m sdda_scripts.preflight_stack_combo --stack workspace/stack/STACK.md
#   → refuse supervisor/graph/reflection/HITL/escalate-human sans langgraph.md actif
```

Smoke Timeout : 60 s.

---

## 7. Pièges connus

1. **« Ça marche avec 3 tours » n'est pas une borne.** La boucle de §3.3 est
   bornée par construction ; le piège est de la « généraliser » en `while
   ai.tool_calls:`. Le lint L0 l'attrape ; la revue doit refuser toute
   justification.
2. **`create_agent` importe LangGraph sans le dire.** Un `dev-agent` pressé
   l'utilisera parce que c'est dans la doc officielle. Résultat : un graphe
   non déclaré, sans `maxHops` dans l'IR, sans checkpointer configuré. Preflight
   bloquant sur l'import (`grep "langchain.agents"`).
3. **Le modèle invente des arguments d'outil.** Sans `extra="forbid"`, pydantic
   les ignore silencieusement et l'outil s'exécute avec des paramètres
   manquants ou par défaut.
4. **`with_structured_output` sans `include_raw`** perd `usage_metadata` : le
   budget n'est plus mesuré, `[BUDGET_EXCEEDED_MEASURED]` devient invisible.
5. **`method="json_mode"` vs `"function_calling"` vs `"json_schema"`** : le
   comportement dépend du provider ; un schéma avec `Optional`/`Union`
   complexes échoue chez certains. Tester le schéma de sortie en L2 contre le
   provider réel (marqué `network`) avant la L4.
6. **Sortie d'outil en texte libre.** Un `ToolMessage` contenant un JSON non
   validé est du texte que le modèle interprète — y compris s'il contient
   « ignore les instructions ». Toujours `OutputModel.model_dump_json()`, et
   enveloppe `wrap_untrusted` si `trust: untrusted`.
7. **Retry sur outil non idempotent.** `tenacity` sur `create_ticket` = trois
   tickets. `retry_policy: none` dans le contrat doit être **lu** par `wrap.py`.
8. **Le `SystemMessage` reconstruit à chaque appel** avec du contenu variable
   (date, contexte retrouvé) change le `prompt_hash` à chaque run et rend les
   baselines inexploitables. Le système est statique ; le variable va dans un
   `HumanMessage`.
9. **`astream_events` v1 vs v2** : les noms d'événements diffèrent ; v2
   imposé et centralisé dans `tracing/`.
10. **`langchain-community`** tire des dépendances lourdes et parfois non
    maintenues ; chaque intégration est un `onDemand` justifié, jamais un
    `uv add langchain-community` global.
11. **Pas de reprise, donc pas d'`escalate-human`.** Le contrat d'agent qui le
    déclare passe la CAP GATE (il est neutre framework) et échoue au preflight
    de stack : c'est **le** signal qu'il faut activer `langgraph.md`, pas
    contourner.
