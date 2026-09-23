# Stack: langgraph (framework)

> §2.3 (Librairies) régénérée depuis `langgraph.libs.json` — ne pas éditer manuellement.

Stack ID: framework-langgraph
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: framework d'orchestration agentic Python — graphe d'état, cycles bornés, checkpointing, interruption humaine. Cible du générateur `IR → Python/LangGraph`. Suppose `lang/python.md` actif ; inclut `langchain-core` (tools, messages, modèles) comme dépendance de fait.

---

## 1. Rôle et périmètre

LangGraph est **le framework recommandé** de SDD_Agents pour toute topologie qui
comporte un cycle, une délégation, une reprise ou un humain dans la boucle —
c'est-à-dire tous les patterns d'orchestration sauf `single-agent` trivial et
`sequential` sans retour arrière. Raisons, dans l'ordre :

1. **Le graphe est explicite.** Nœuds, arêtes, conditions sont des objets Python
   inspectables : le mapping depuis l'Agentic IR est mécanique, et la
   comparaison code ↔ IR (spec-compliance) est possible.
2. **Les bornes se matérialisent dans l'état.** Un compteur de hops dans le
   state, lu par chaque arête conditionnelle, est la traduction directe de
   `maxHops` (P12). Ce n'est pas une option de configuration : c'est du graphe.
3. **Checkpointing natif.** Reprise, `interrupt()`, HITL et time-travel sont
   disponibles sans code ad hoc — indispensables pour `escalate-human`.
4. **Streaming par événements** (`updates`, `messages`, `custom`), ce dont la
   surface de serving a besoin pour émettre des traces intermédiaires.

Cette fiche couvre : le mapping IR → LangGraph, la matérialisation des bornes,
le checkpointing, l'interruption, les modes de streaming, la structure générée
et les pièges. Elle **ne couvre pas** : la rédaction des prompts
(`rules/prompt-authoring.md`), l'évaluation (`eval/*.md`), l'observabilité
(`observability/*.md`) — LangGraph n'est pas LangSmith ; ce dernier est une
stack distincte (`framework/langsmith.md`), optionnelle.

> Rappel P11 : **aucun** identifiant de cette fiche (`StateGraph`, `START`,
> `END`, `interrupt`, `Command`, `add_conditional_edges`…) ne doit apparaître
> dans un contrat, une CAP, une TOPOLOGY ni dans l'IR. Ils vivent ici et dans
> `workspace/src/{AppName}/orchestration/`.

---

## 2. Identité

### 2.1 Identité

- **Stack ID** : `framework-langgraph`
- **Langage** : Python 3.12 (`lang/python.md`)
- **Framework** : `langgraph` 1.x + `langchain-core` 1.x
- **Checkpointer** : `langgraph-checkpoint-postgres` (prod) · `langgraph-checkpoint-sqlite` (dev/eval) · `InMemorySaver` (tests L1/L4 uniquement)
- **Build tool** : `uv`
- **Combinaisons validées** (SSoT : `.sdda/registry/compatibility.matrix.json`) : `python × langgraph × {single-agent, router, sequential, parallel, supervisor, graph, plan-execute, reflection}`. `blackboard` : toléré, non validé.

### 2.2 Outils

- **Project file** : `workspace/src/{AppName}/pyproject.toml`
- **Smoke** : cf. §6
- **Visualisation** : `graph.get_graph().draw_mermaid()` → doit être **identique**
  (modulo mise en forme) au bloc ```mermaid de `workspace/feats/topology/{n}-topology.md` §4. Vérifié par
  `sdda_scripts/diff_graph_vs_ir.py` (0 token).

<!-- CORE_PACKAGES_START -->
```bash
# Auto-généré depuis langgraph.libs.json — ne pas éditer.
uv add --project workspace/src/{AppName} \
  langgraph==1.0.9 \
  langchain-core==1.1.6 \
  langgraph-checkpoint==3.0.2 \
  langgraph-checkpoint-postgres==3.0.4 \
  langgraph-checkpoint-sqlite==3.0.2 \
  psycopg[binary,pool]==3.3.2 \
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
# Auto-généré depuis langgraph.libs.json (on-demand) — installé selon RuntimeProvider / STACK.md.
# capability: provider-anthropic
uv add --project workspace/src/{AppName} langchain-anthropic==1.2.3
# capability: provider-openai
uv add --project workspace/src/{AppName} langchain-openai==1.2.1
# capability: provider-google
uv add --project workspace/src/{AppName} langchain-google-genai==3.2.0
# capability: mcp-tools
uv add --project workspace/src/{AppName} langchain-mcp-adapters==0.2.3
```
<!-- ONDEMAND_PACKAGES_END -->

<!-- LIBS_CATALOG_START -->
### 2.3 Librairies

> Source de vérité : `.sdda/stacks/framework/langgraph.libs.json`. Pins à
> re-résoudre contre PyPI au premier bootstrap (statut design-phase) ; aucune
> montée de majeure sans smoke vert.

| Lib | Version | Rôle |
|---|---|---|
| langgraph | 1.0.9 | StateGraph, nœuds, arêtes, Command, interrupt, Send |
| langchain-core | 1.1.6 | messages, `@tool`, `BaseChatModel`, `bind_tools`, `with_structured_output` |
| langgraph-checkpoint | 3.0.2 | interface `BaseCheckpointSaver`, `InMemorySaver` |
| langgraph-checkpoint-postgres | 3.0.4 | `PostgresSaver` / `AsyncPostgresSaver` — prod |
| langgraph-checkpoint-sqlite | 3.0.2 | `SqliteSaver` / `AsyncSqliteSaver` — dev, evals locales |
| psycopg[binary,pool] | 3.3.2 | driver du checkpointer Postgres |
| pydantic / pydantic-settings | 2.13.5 / 2.15.0 | schémas d'état, config |
| structlog | 26.1.0 | logs structurés |
| httpx / tenacity | 0.28.1 / 9.1.4 | outils HTTP + retry (idempotents seulement) |
| ruff / mypy | 0.16.5 / 2.3.1 | L0 |

On-demand : `langchain-anthropic`, `langchain-openai`, `langchain-google-genai`
(un seul par tier résolu dans `RuntimeTierMap` — le mixage cross-provider est
autorisé) ; `langchain-mcp-adapters` si `tools/mcp.md` actif.
<!-- LIBS_CATALOG_END -->

---

## 3. Mapping des concepts SDD_Agents → idiomes LangGraph

### 3.1 Table de correspondance IR → LangGraph

| IR (`system.ir.json`) | LangGraph | Notes |
|---|---|---|
| `orchestration.nodes[kind=agent]` | `graph.add_node(id, agent_node)` — fonction `async def (state, config) -> dict` qui appelle le `Runnable` de l'agent | un agent avec ses propres outils et itérations est un **sous-graphe** compilé (`add_node(id, subgraph)`) |
| `orchestration.nodes[kind=retriever]` | `add_node(id, retrieve_node)` — appelle `retrieval.{index}.retrieve` et écrit `state["retrieved"]` | la sortie est `Untrusted` |
| `orchestration.nodes[kind=function]` | `add_node(id, fn)` — nœud déterministe, testé en L1 | fusion, formatage, validation de schéma |
| `orchestration.nodes[kind=tool]` (si isolé) | `ToolNode(tools, handle_tool_errors=False)` | cf. piège §7.6 |
| `orchestration.entryNode` | `graph.add_edge(START, entryNode)` | |
| `orchestration.terminalNodes[]` | `graph.add_edge(node, END)` | tout `terminalNode` doit avoir une arête vers `END` et aucune arête sortante autre |
| `edges[].condition == "always"` | `graph.add_edge(from, to)` | |
| `edges[].condition` (expression) | `graph.add_conditional_edges(from, router_fn, {label: to, ...})` | `router_fn` est **pure**, lit uniquement `state`, testée en L1 avec une table de cas |
| `edges[].countsAsHop == true` | le nœud `from` retourne `{"hops": 1}` sur un champ à réducteur `operator.add` | c'est le compteur que `maxHops` borne |
| `orchestration.maxHops` | champ `hops` + garde **dans chaque routeur** + nœud `bound_exceeded` | cf. §3.3 |
| `orchestration.checkpointing` | `graph.compile(checkpointer=PostgresSaver(...))` ; `config={"configurable": {"thread_id": run_id}}` | obligatoire si `humanInTheLoop`, `escalate-human`, ou reprise exigée |
| `orchestration.humanInTheLoop` | `interrupt(payload)` dans le nœud + `graph.invoke(Command(resume=...), config)` | cf. §3.4 |
| `agents[].bounds.maxIterations` | compteur `iterations` dans le **state du sous-graphe agent**, garde dans le routeur `should_continue` | `recursion_limit` n'est qu'un filet, cf. §3.3 |
| `agents[].bounds.maxToolCalls` | compteur `tool_calls` incrémenté par le nœud outil (`len(tool_calls)`) ; garde avant exécution | |
| `agents[].bounds.maxDelegationDepth` | champ `delegation_depth` passé en **entrée** du sous-graphe (`depth + 1`) ; refus si `> max` | |
| `agents[].bounds.timeoutSec` | `asyncio.timeout(timeout_s)` autour de `subgraph.ainvoke(...)` dans le nœud parent | pas d'API LangGraph pour un timeout par agent : c'est du Python |
| `agents[].bounds.budgetUsd` | champ `cost_usd` (réducteur `add`) alimenté depuis `response.usage_metadata` × pricing ; garde dans le routeur | |
| `agents[].onBoundExceeded` | arête du routeur vers un nœud `bound_exceeded` qui applique la politique | `fail-explicit` → sortie structurée d'échec ; `degrade` → réponse partielle ; `escalate-human` → `interrupt()` |
| `agents[].promptRef` / `promptHash` | `load_system_prompt(slug)` au **build**, jamais dans le nœud ; hash émis dans le span | P1 |
| `agents[].modelTier` | `models.resolve(tier).bind_tools(tools)` | jamais un nom de modèle dans `orchestration/` |
| `agents[].tools[]` | liste **close** passée à `bind_tools` — exactement les outils du contrat | `[TOOL_SCOPE_EXCESS]` sinon |
| `agents[].outputSchema` | `model.with_structured_output(OutputModel)` sur le dernier tour, ou nœud `validate_output` | `schema-validation` guardrail |
| `agents[].trustPosture.untrustedInputs` | contenu inséré via `wrap_untrusted(...)` dans un `HumanMessage`/`ToolMessage`, **jamais** dans le `SystemMessage` | P8 |
| Pattern `parallel` | `Send(node, payload)` retourné par un nœud fan-out ; nœud `gather` avec réducteur de liste | la stratégie de fusion est un nœud `function` évalué séparément |
| Pattern `supervisor` | routeur retournant `Command(goto=specialist, update={...})` ; retour vers `supervisor` avec `countsAsHop` | `maxHops` dur |
| Pattern `reflection` | cycle `writer → critic → writer` ; compteur `revisions` borné | le critique est un agent distinct (P7 raison 4) |
| Mémoire `shared` (`CrossAgentSharedState: scoped`) | clés du state annotées par lecteur/écrivain ; le sous-graphe ne reçoit que ses clés (`input`/`output` schemas du sous-graphe) | |
| Mémoire long terme `store` | `BaseStore` (`PostgresStore`) passé à `compile(store=...)` | hors MVP, cf. `memory/store.md` |

### 3.2 État : le contrat en Python

```python
# src/{AppName}/orchestration/state.py
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class OrchestrationState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    # --- bornes (P12) : compteurs à réducteur additif, JAMAIS écrasés ---
    hops: Annotated[int, operator.add]
    tool_calls: Annotated[int, operator.add]
    cost_usd: Annotated[float, operator.add]
    # --- routage ---
    intent: str | None
    resolved: bool
    bound_exceeded: str | None          # nom de la borne franchie, sinon None
    # --- sorties ---
    final: dict[str, object] | None
```

Règles :
- **Tout compteur de borne a un réducteur additif.** Un nœud qui écrirait
  `hops = 0` remettrait la borne à zéro — c'est le bug qu'on cherche à rendre
  impossible.
- Le state ne porte **aucun secret** et **aucun texte de prompt** : il est
  persisté par le checkpointer.
- `total=False` + valeurs initiales fournies par l'entrée du graphe (`hops: 0`,
  `tool_calls: 0`, `cost_usd: 0.0`) — la fonction `initial_state(run_input)` est
  testée en L1.

### 3.3 Matérialiser `maxHops` et `max_iterations`

```python
# src/{AppName}/orchestration/routers.py
from __future__ import annotations

from typing import Literal

from .state import OrchestrationState

Route = Literal["billing", "finalize", "bound_exceeded"]


def route_from_supervisor(state: OrchestrationState, *, max_hops: int) -> Route:
    """Pure. Testée en L1 avec une table de cas. Lit l'IR via max_hops (injecté au build)."""
    if state.get("hops", 0) >= max_hops:
        return "bound_exceeded"
    if state.get("resolved"):
        return "finalize"
    if state.get("intent") == "billing":
        return "billing"
    return "finalize"
```

```python
# src/{AppName}/orchestration/graph.py (extrait)
from functools import partial

from langgraph.graph import END, START, StateGraph

def build_graph(ir: OrchestrationIR, deps: Deps) -> CompiledGraph:
    g = StateGraph(OrchestrationState)
    g.add_node("supervisor", deps.agents["1-supervisor"])
    g.add_node("billing", deps.agents["1-billing-specialist"])
    g.add_node("finalize", finalize_node)
    g.add_node("bound_exceeded", partial(bound_exceeded_node, policy=ir.on_bound_exceeded))

    g.add_edge(START, "supervisor")
    g.add_conditional_edges(
        "supervisor",
        partial(route_from_supervisor, max_hops=ir.max_hops),
        {"billing": "billing", "finalize": "finalize", "bound_exceeded": "bound_exceeded"},
    )
    g.add_edge("billing", "supervisor")           # billing retourne {"hops": 1}
    g.add_edge("finalize", END)
    g.add_edge("bound_exceeded", END)

    compiled = g.compile(checkpointer=deps.checkpointer)
    # Filet de sécurité, PAS la borne : si atteint, c'est un bug du graphe → GraphRecursionError
    compiled = compiled.with_config(recursion_limit=ir.max_hops * 4 + 10)
    return compiled
```

Le nœud `bound_exceeded` applique `onBoundExceeded` :

| Politique | Ce que fait le nœud |
|---|---|
| `fail-explicit` | écrit `final = {"status": "bound_exceeded", "bound": ..., "partial": ...}` conforme au schéma de sortie d'échec ; le serving mappe vers un code de sortie / HTTP dédié |
| `degrade` | produit une réponse à partir de l'état partiel, marquée `degraded: true` ; jamais une invention silencieuse |
| `escalate-human` | `interrupt({"reason": "bound_exceeded", "state_summary": ...})` — le run se met en pause, reprise humaine via `Command(resume=...)` ; exige `checkpointing: true` |

C'est **le même nœud** quelle que soit la borne franchie (hops, iterations,
tool_calls, budget, timeout) : le comportement déclaré est un endroit unique,
testé en L1 et observé en L5 (`trajectory` grader).

Le sous-graphe d'un agent applique la même mécanique à ses propres bornes :
`should_continue(state) -> "tools" | "respond" | "bound_exceeded"` lit
`iterations`, `tool_calls`, `cost_usd`.

### 3.4 Interruption et reprise

```python
from langgraph.types import Command, interrupt

async def confirm_refund_node(state: OrchestrationState) -> dict[str, object]:
    # interrupt() DOIT être la première instruction à effet du nœud : à la reprise,
    # le nœud est RE-EXÉCUTÉ depuis le début (cf. piège §7.3).
    decision = interrupt({"action": "refund", "amount": state["refund_amount"], "requires": "human"})
    if decision != "approved":
        return {"final": {"status": "refused_by_human"}}
    return {"approved": True}

# Reprise (serving) :
# await graph.ainvoke(Command(resume="approved"), config={"configurable": {"thread_id": thread_id}})
```

- `thread_id` = `run_id` SDD_Agents. C'est aussi `gen_ai.conversation.id` dans
  les traces.
- `interrupt_before=[...]` à la compilation est réservé au **debug** ; en
  production, l'interruption est dans le nœud, donc dans le graphe, donc dans l'IR.

### 3.5 Streaming

| `stream_mode` | Usage SDD_Agents |
|---|---|
| `"updates"` | événements de nœud → spans `invoke_agent`, `execute_tool` ; c'est ce que consomme `serving/cli.md` en mode `--json` |
| `"messages"` | tokens du modèle → sortie utilisateur en streaming |
| `"custom"` | `get_stream_writer()` pour émettre des événements de borne (`{"bound": "hops", "value": 3, "limit": 8}`) |
| `"values"` | debug uniquement — l'état complet à chaque étape peut contenir des documents `Untrusted` volumineux |

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── orchestration/
│   ├── __init__.py
│   ├── state.py            # OrchestrationState + initial_state()
│   ├── routers.py          # fonctions pures de routage, 1 par arête conditionnelle de l'IR
│   ├── nodes.py            # nœuds déterministes : finalize, bound_exceeded, gather, validate_output
│   ├── graph.py            # build_graph(ir, deps) -> CompiledGraph — SEUL fichier important le framework
│   ├── checkpointer.py     # make_checkpointer(settings) : Postgres | Sqlite | InMemory
│   └── ir_loader.py        # lit workspace/.sys/.ir/{n}-system.ir.json, valide, expose OrchestrationIR (pydantic frozen)
├── agents/{agent_slug}/
│   ├── agent.py            # build(deps, bounds) -> CompiledGraph (sous-graphe : model → tools → should_continue)
│   ├── state.py            # AgentState : messages, iterations, tool_calls, cost_usd, delegation_depth
│   └── schemas.py
└── tests/
    ├── orchestration/
    │   ├── test_routers.py         # table de cas par routeur — inclut le cas hops == max_hops
    │   ├── test_graph_shape.py     # nœuds/arêtes du graphe compilé == IR (diff_graph_vs_ir)
    │   └── test_bound_exceeded.py  # les 3 politiques, LLM mocké
    └── agents/{agent_slug}/test_should_continue.py
```

Migrations du checkpointer : `PostgresSaver.setup()` crée ses tables
(`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`) — exécuté **une fois**
par un script de déploiement, jamais au démarrage de l'application.

---

## 5. Conventions imposées

1. **Un fichier nomme le framework.** `graph.py` (et `agents/*/agent.py`)
   importent `langgraph` ; `routers.py`, `nodes.py`, `state.py` n'importent que
   `langchain_core.messages` au plus. Ainsi les routeurs sont testables sans graphe.
2. **Chaque arête conditionnelle de l'IR = une fonction pure nommée
   `route_from_{node}`**, avec un test L1 par label de sortie **plus** le cas
   « borne atteinte ».
3. **Chaque cycle de l'IR passe par un compteur à réducteur additif**, lu par
   le routeur qui ferme le cycle. `validate_ir.py` vérifie qu'un cycle est
   borné ; `test_graph_shape.py` vérifie que le code l'implémente.
4. **`recursion_limit` est un filet, jamais la borne.** Il est dérivé
   (`max_hops * 4 + 10`) et un `GraphRecursionError` en production est classé
   `[UNBOUNDED_LOOP]` — bug du graphe, pas comportement attendu.
5. **`handle_tool_errors=False`** sur `ToolNode` : une erreur d'outil non
   déclarée doit remonter, pas devenir un `ToolMessage` que le modèle
   rationalise. Les erreurs **déclarées** du contrat sont converties en
   `ToolMessage` structuré par le wrapper d'outil, avec leur `agentBehavior`.
6. **Le `SystemMessage` est construit une fois au build** depuis
   `LoadedPrompt.text`. Aucun nœud ne le reconstruit, aucun nœud n'y concatène
   du contenu `Untrusted`.
7. **`thread_id` obligatoire** dès qu'un checkpointer est compilé ; il vaut le
   `run_id`. Pas de `thread_id` = pas de reprise = pas d'`escalate-human` possible.
8. **Sous-graphe par agent.** Un agent avec outils est un graphe compilé
   séparément (`agents/{slug}/agent.py`), évaluable **isolé** en L4 avec un
   checkpointer `InMemorySaver` et des outils mockés.
9. **Pas de `create_react_agent` / `create_agent` prébuilt** dans le code
   généré : leur boucle interne cache les compteurs. Le sous-graphe d'agent est
   écrit explicitement (≈ 40 lignes) pour que `max_iterations` et
   `max_tool_calls` soient dans le state.
10. **Le graphe Mermaid est régénéré**, jamais dessiné à la main :
    `graph.get_graph().draw_mermaid()` → comparé au bloc ```mermaid de `topology/{n}-topology.md`.

---

## 6. Commande de smoke

Déterministe, 0 token :

```bash
cd workspace/src/{AppName}
uv sync --frozen
uv run python -m {AppName}.orchestration.graph --check \
  --ir ../../.sys/.ir/{n}-system.ir.json
#   → construit le graphe avec des agents MOCKÉS, le compile avec InMemorySaver,
#     imprime nœuds/arêtes, compare à l'IR (diff_graph_vs_ir), exit 0 si identique
uv run pytest tests/orchestration tests/agents -q -m "not llm and not network"
#   → routeurs (table de cas), bound_exceeded (3 politiques), forme du graphe
```

Smoke Timeout : 60 s. Si `checkpointing: true` en prod, un second smoke marqué
`network` vérifie `PostgresSaver` : `uv run python -m {AppName}.orchestration.checkpointer --ping`.

---

## 7. Pièges connus

1. **`recursion_limit` compte des super-steps, pas des hops.** Défaut 25. Un
   graphe `supervisor → billing → supervisor` consomme 2 super-steps par hop ;
   avec des outils, davantage. Ne jamais l'utiliser comme borne métier : le
   `GraphRecursionError` qu'il lève n'est pas votre `onBoundExceeded`, il n'a
   pas d'état partiel exploitable et il n'apparaît pas dans la trajectoire comme
   une décision.
2. **Réducteur oublié = borne remise à zéro.** Un champ `hops: int` sans
   `Annotated[int, operator.add]` est **écrasé** par chaque nœud qui l'écrit. Le
   test L1 `test_bound_exceeded.py` doit inclure un scénario à `max_hops + 1`
   passages et vérifier la sortie `bound_exceeded`.
3. **`interrupt()` ré-exécute le nœud depuis le début à la reprise.** Tout effet
   de bord placé **avant** `interrupt()` dans le même nœud s'exécute deux fois
   (deux tickets, deux e-mails). Règle : `interrupt()` en tête de nœud, effets
   dans le nœud **suivant**, ou nœud idempotent par clé.
4. **Le checkpointer persiste tout le state.** Documents retrouvés
   (`Untrusted`), messages utilisateur, arguments d'outils : tout finit dans
   `checkpoint_blobs`. C'est un store de PII de fait → `MemoryPIIPolicy`
   s'applique, rétention à déclarer, scan G7 à étendre aux tables du
   checkpointer. Ne jamais mettre un secret dans le state.
5. **`add_messages` fait grossir le contexte sans limite.** Pression de contexte
   (P7 raison 3) mesurable ; prévoir un nœud `trim` déterministe
   (`trim_messages`) ou un `summarize` borné, déclaré dans le contrat mémoire.
6. **`ToolNode(handle_tool_errors=True)` (défaut) avale les exceptions** en
   `ToolMessage("Error: ...")`. Le modèle « gère » l'erreur en inventant, et la
   TOOL GATE ne voit rien. Toujours `handle_tool_errors=False` + wrapper
   d'outil qui ne convertit que les erreurs **déclarées**.
7. **Sous-graphe et clés de state.** Un sous-graphe compilé ne partage que les
   clés communes à son schéma et à celui du parent. Un compteur `hops` défini
   dans les deux **fusionne** (réducteur appliqué) — voulu pour `cost_usd`,
   catastrophique pour `iterations` (le compteur de l'agent fuit dans le parent).
   Nommer les compteurs par niveau : `hops` (parent), `iterations` (agent).
8. **`thread_id` réutilisé = état réutilisé.** Deux runs avec le même
   `thread_id` continuent la même conversation. Le `run_id` doit être unique
   par exécution ; une reprise réutilise volontairement le même.
9. **Streaming `"values"` en production** expose l'état complet, y compris les
   documents `Untrusted` et les messages système. Réserver à debug ; le serving
   consomme `"updates"` + `"messages"`.
10. **`asyncio.timeout` autour d'un sous-graphe avec checkpointer** laisse un
    checkpoint partiel cohérent (bon), mais le nœud parent doit convertir
    `TimeoutError` en route `bound_exceeded` avec `bound="timeout_s"`, pas la
    propager (sinon pas de `onBoundExceeded`).
11. **Coût : `usage_metadata` arrive en fin de stream.** Le compteur `cost_usd`
    n'est fiable qu'après le dernier chunk ; la garde de budget s'applique donc
    **au tour suivant**. Dimensionner `budget_usd` avec la marge d'un tour.
12. **`langgraph` 1.x vs `langchain` 1.x.** `create_react_agent` a migré de
    `langgraph.prebuilt` vers `langchain.agents.create_agent` ; les tutoriels
    mélangent les deux époques. Le code généré n'utilise ni l'un ni l'autre (§5.9).
13. **Fuite de framework dans les contrats.** Un `architect-tools` qui écrit
    « le ToolNode appelle… » viole P11 ; `validate_ir.py` (règle 10) et le lint
    des contrats grep `StateGraph|ToolNode|add_node|interrupt\(` dans
    `workspace/feats/contracts/**` → `[CONTRACT_FRAMEWORK_LEAK]`.
