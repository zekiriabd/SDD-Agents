"""La boucle bornée et le socle de graphe. GÉNÉRÉ, ne pas éditer.

Deux choses, et elles répondent à deux questions différentes.

**1. La boucle (`BoundedLoop`)** — comment un agent raisonne sans jamais boucler.
C'est le pattern `single-agent` : un modèle, N outils, et la seule boucle
autorisée du paquet. Elle est écrite `for iteration in range(...)` et non
`while True` : un `while True` dans `agents/` ou `orchestration/` est
`[UNBOUNDED_LOOP]`, détecté par lint AST, parce qu'une boucle dont la sortie
dépend du modèle est une boucle dont la sortie dépend d'un tirage.

Les cinq bornes sont matérialisées **dans** cette boucle, et dans l'ordre qui
compte :

    - le budget et le temps sont vérifiés À CHAQUE tour, avant l'appel ;
    - le lot d'appels d'outils est vérifié AVANT exécution — vérifier après
      protège de quoi ? l'effet de bord a eu lieu, et sur un outil destructif
      il ne se reprend pas ;
    - le dépassement produit un RÉSULTAT (`fail-explicit`, `degrade`,
      `escalate-human`), pas une exception qui remonte nue : l'appelant doit
      pouvoir rendre un code de sortie et une réponse structurée.

**2. Le graphe (`Graph`)** — ce que le code a réellement construit. Chaque
pattern déclare ses nœuds et ses arêtes au moment du câblage, puis
`dump_graph()` les rend sous la forme que `diff_code_vs_ir.py` attend. Le
manifeste n'est pas une description écrite à côté : il est l'introspection du
graphe, et `generatedBy` dit d'où il vient. Un manifeste recopié depuis l'IR
rendrait la comparaison tautologique — elle passerait toujours, y compris le
jour où le code ne fait plus ce que le dessin annonce.
"""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..bounds import BoundExceeded, BoundGuard, Bounds
from ..guardrails import Guardrails
from ..models import Completion, LLMClient, Message, ToolCall
from ..tracing import Tracer
from ..trust import Untrusted, wrap

#: Le nom que `diff_code_vs_ir.py` cherche sous `workspace/src/**/orchestration/`.
MANIFEST_NAME = "graph.manifest.json"

#: Qui a produit le manifeste. Lu par `diff_code_vs_ir.py` : un manifeste sans
#: attribution reçoit `[ORCH_MANIFEST_UNATTRIBUTED]`, parce qu'on ne peut plus
#: distinguer une introspection d'une recopie.
GENERATED_BY = "orchestration.dump_graph"

#: Condition d'une arête inconditionnelle. La même graphie que l'IR
#: (`diff_code_vs_ir._norm_condition`) : `""` et `None` y deviennent `always`,
#: et une troisième graphie ferait diverger deux graphes identiques.
ALWAYS = "always"

#: Le graphe de FRAMEWORK qui tient l'orchestration, s'il y en a un. Quand
#: `dev-orchestration` écrit un graphe LangGraph sous `orchestration/`, c'est
#: LUI qui émet le manifeste, et le squelette se tait : `diff_code_vs_ir.py`
#: refuse deux manifestes sous `**/orchestration/`, et le manifeste trivial de
#: la boucle de démarrage écraserait celui du vrai graphe — un diff vert contre
#: un dessin qui n'est plus celui du code. `RunService` le déclare dès qu'une
#: `agent_factory` lui est fournie ; un orchestrateur peut aussi le déclarer
#: lui-même.
_FRAMEWORK_GRAPH: dict[str, str | None] = {"origin": None}


#: Le garde de l'agent EN COURS. Un agent appelé depuis un outil d'un autre
#: agent (délégation) le trouve ici : il démarre à la profondeur de son
#: appelant + 1, et c'est la borne `max_delegation_depth` de l'APPELANT qui
#: tombe s'il n'avait pas le droit de déléguer. Un `ContextVar` : deux agents
#: en parallèle ne se prennent pas pour le parent l'un de l'autre.
_CURRENT_GUARD: contextvars.ContextVar[BoundGuard | None] = contextvars.ContextVar(
    "sdda_current_guard", default=None)


def _settle(future: asyncio.Future[Any], *, result: Any = None, error: BaseException | None = None) -> None:
    if future.done():   # le délai est déjà tombé : le résultat tardif est jeté
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)


def _in_daemon_thread(work: Callable[[], Any]) -> asyncio.Future[Any]:
    """Exécute `work` dans un thread DÉMON, rend un futur de la boucle courante.

    Pourquoi pas `asyncio.to_thread` : à l'expiration du délai, le thread d'un
    appel bloqué continue, et `asyncio.run` puis l'interpréteur attendent la fin
    de chaque thread de l'exécuteur par défaut — la CLI restait suspendue
    jusqu'à ce que l'appel qu'on venait d'abandonner se termine. Un thread
    démon n'empêche pas le processus de rendre la main.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()
    context = contextvars.copy_context()

    def target() -> None:
        try:
            value = context.run(work)
        except BaseException as exc:  # noqa: BLE001 - transmise telle quelle à l'appelant
            outcome: dict[str, Any] = {"error": exc}
        else:
            outcome = {"result": value}
        try:
            loop.call_soon_threadsafe(lambda: _settle(future, **outcome))
        except RuntimeError:   # boucle déjà fermée : personne n'attend plus ce résultat
            pass

    threading.Thread(target=target, name="sdda-bounded-call", daemon=True).start()
    return future


class CallTimedOut(Exception):
    """Le délai PROPRE d'un appel (celui d'un outil) est tombé avant celui du run."""


async def within_deadline(guard: BoundGuard, work: Callable[[], Any], *,
                          cap_s: float | None = None) -> Any:
    """Exécute `work` (sync ou async) sous le temps qui RESTE au garde.

    `timeout_s` n'était vérifié qu'entre deux tours : un appel au modèle ou à
    un outil qui pendait trois minutes passait entre deux vérifications, et la
    borne n'existait pas. Ici le reste du budget de temps devient le délai de
    l'appel en cours. `cap_s` (délai propre d'un outil) le resserre : s'il tombe
    le premier, c'est `CallTimedOut` — une erreur de l'outil, pas du run.
    """
    remaining = guard.remaining_time()
    if remaining <= 0:
        guard.fail("timeout_s", guard.bounds.timeout_s, round(guard.elapsed, 3))
    own_cap = cap_s is not None and cap_s < remaining
    delay = float(cap_s) if own_cap and cap_s is not None else remaining

    async def attempt() -> Any:
        value = await _in_daemon_thread(work)
        if inspect.isawaitable(value):
            value = await value
        return value

    try:
        return await asyncio.wait_for(attempt(), timeout=delay)
    except asyncio.TimeoutError:
        if own_cap:
            raise CallTimedOut(f"délai propre de {cap_s} s dépassé") from None
        guard.fail("timeout_s", guard.bounds.timeout_s, round(guard.elapsed, 3))
        raise  # pragma: no cover - `fail` lève toujours


def declare_framework_graph(origin: str | None) -> None:
    """Déclare (ou retire, avec `None`) le graphe de framework qui porte l'orchestration."""
    _FRAMEWORK_GRAPH["origin"] = origin


def framework_graph_origin() -> str | None:
    return _FRAMEWORK_GRAPH["origin"]


def foreign_manifest(path: Path) -> bool:
    """Vrai si `path` est un manifeste écrit par autre chose que ce module."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and str(data.get("generatedBy") or "") not in ("", GENERATED_BY)


# ---------------------------------------------------------------------------
# Le graphe et son manifeste
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Node:
    """Un nœud. `kind` suit la grammaire de l'IR : agent, router, function, tool."""

    id: str
    kind: str = "agent"
    ref: str = ""


@dataclass(frozen=True)
class Edge:
    """Une arête. `condition` est prise TELLE QUE L'IR L'ÉCRIT.

    Reformuler une condition en la codant (« intent=='billing' » devenu
    « is_billing ») est une décision non tracée : le diff la signale, et il a
    raison de la signaler.
    """

    source: str
    target: str
    condition: str = ALWAYS
    is_fallback: bool = False


@dataclass
class Graph:
    """Le graphe RÉELLEMENT construit, et son introspection.

    Les nœuds s'enregistrent au câblage avec leur handler : un nœud déclaré et
    sans handler serait un nœud du manifeste qu'aucun run n'atteint — et le
    diff avec l'IR passerait au vert sur un graphe qui ne tourne pas.
    """

    entry_node: str = ""
    terminal_nodes: list[str] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    max_hops: int = 1

    def add_node(self, node_id: str, *, kind: str = "agent", ref: str = "",
                 handler: Callable[..., Any] | None = None) -> Node:
        if any(n.id == node_id for n in self.nodes):
            raise ValueError(f"nœud `{node_id}` déclaré deux fois — l'identifiant est ce que "
                             "le diff avec l'IR compare, il doit être unique")
        node = Node(id=node_id, kind=kind, ref=ref)
        self.nodes.append(node)
        if handler is not None:
            self.handlers[node_id] = handler
        return node

    def add_edge(self, source: str, target: str, *, condition: str = ALWAYS,
                 is_fallback: bool = False) -> Edge:
        known = {n.id for n in self.nodes}
        unknown = [n for n in (source, target) if n not in known]
        if unknown:
            raise ValueError(f"arête {source}->{target} : nœud(s) inconnu(s) {unknown} — "
                             "une arête vers un nœud absent est un chemin que rien n'exécute")
        edge = Edge(source=source, target=target,
                    condition=(condition or ALWAYS).strip() or ALWAYS, is_fallback=is_fallback)
        self.edges.append(edge)
        return edge

    def dump_graph(self) -> dict[str, Any]:
        """Le manifeste, introspecté. Forme exacte attendue par `diff_code_vs_ir.py`."""
        return {
            "generatedBy": GENERATED_BY,
            "entryNode": self.entry_node,
            "terminalNodes": sorted(self.terminal_nodes),
            "maxHops": self.max_hops,
            "nodes": [{"id": n.id, "kind": n.kind, **({"ref": n.ref} if n.ref else {})}
                      for n in self.nodes],
            "edges": [{"from": e.source, "to": e.target, "condition": e.condition,
                       **({"isFallback": True} if e.is_fallback else {})}
                      for e in self.edges],
        }

    def write_manifest(self, directory: Path) -> Path | None:
        """Écrit `graph.manifest.json` à côté du code d'orchestration.

        Écrit à CHAQUE construction du graphe, pas à la génération : c'est ce
        qui fait qu'un graphe modifié à la main produit un manifeste modifié,
        donc un diff rouge, plutôt qu'un fichier figé qui continue de décrire un
        code qui a changé.

        Rend `None`, sans rien écrire, quand un graphe de FRAMEWORK tient
        l'orchestration — déclaré (`declare_framework_graph`) ou reconnu à un
        manifeste déjà présent qui ne vient pas d'ici (`generatedBy` étranger).
        Le squelette n'a alors rien à dire : le graphe comparé à l'IR est celui
        du framework, pas la boucle de démarrage.
        """
        path = Path(directory) / MANIFEST_NAME
        if framework_graph_origin() is not None or foreign_manifest(path):
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.dump_graph(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        return path


class SingleAgentGraph(Graph):
    """Le graphe du pattern par défaut : un nœud, aucune arête, `maxHops` = 1.

    Il existe bien qu'il soit trivial : sans lui, un projet `single-agent`
    n'émettrait aucun manifeste, `diff_code_vs_ir` répondrait
    `[ORCH_MANIFEST_MISSING]`, et la part `orchestration` de G6 resterait
    absente — donc rouge — pour le pattern que le framework recommande.
    """

    def __init__(self, node_id: str = "agent", *, ref: str = "",
                 handler: Callable[..., Any] | None = None) -> None:
        super().__init__(entry_node=node_id, terminal_nodes=[node_id], max_hops=1)
        self.add_node(node_id, kind="agent", ref=ref, handler=handler)


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolOutcome:
    """Ce qu'un outil rend à la boucle. `error_code` est une erreur DÉCLARÉE."""

    content: str
    ok: bool = True
    error_code: str = ""
    side_effect_class: str = "read-only"
    trust: str = "trusted"


@dataclass
class DictToolset:
    """Un périmètre d'outils CLOS, décrit par un dictionnaire.

    Clos, et non extensible à la demande : `get` refuse un outil absent au lieu
    d'en chercher un ailleurs. L'écart entre outils exposés et outils exigés est
    un finding bloquant de `review-safety` (`[TOOL_SCOPE_EXCESS]`) — le rendre
    impossible vaut mieux que le détecter après coup.

    Quand le paquet `tools/` est généré, `ToolRegistry.get_for_agent` joue ce
    rôle avec, en plus, le contrôle de cohabitation destructif/non maîtrisé.
    Cette classe est le minimum qui permet à la boucle de tourner et d'être
    testée sans lui.
    """

    tools: Mapping[str, Callable[..., Any]] = field(default_factory=dict)
    schemas: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Par outil : `side_effect_class`, `trust`, et les bornes du CONTRAT
    #: (`timeout_s`, `rate_limit_rpm`, `max_response_bytes`) — appliquées ici et
    #: dans la boucle, pas seulement écrites dans `tool_specs.json`.
    metadata: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    clock: Callable[[], float] = time.monotonic
    _calls: dict[str, deque[float]] = field(default_factory=dict, repr=False)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.tools))

    def specs(self) -> list[Mapping[str, Any]]:
        return [self.schemas[n] for n in self.names() if n in self.schemas]

    def meta(self, name: str) -> Mapping[str, Any]:
        return self.metadata.get(name, {})

    def _rate_limited(self, name: str) -> bool:
        """`rate_limit_rpm` du contrat, en fenêtre glissante de 60 s."""
        try:
            rpm = int(self.meta(name).get("rate_limit_rpm") or 0)
        except (TypeError, ValueError):
            rpm = 0
        if rpm <= 0:
            return False
        now = self.clock()
        window = self._calls.setdefault(name, deque())
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= rpm:
            return True
        window.append(now)
        return False

    async def call(self, name: str, arguments: Mapping[str, Any]) -> ToolOutcome:
        fn = self.tools.get(name)
        if fn is None:
            # Le modèle a halluciné un outil : c'est une erreur DÉCLARÉE de la
            # boucle, pas une exception. Il doit pouvoir corriger au tour
            # suivant plutôt que faire tomber le run.
            return ToolOutcome(content=f"outil `{name}` inconnu", ok=False,
                               error_code="TOOL_NOT_REGISTERED")
        if self._rate_limited(name):
            return ToolOutcome(content=f"outil `{name}` : plafond d'appels par minute atteint",
                               ok=False, error_code="TOOL_RATE_LIMITED")
        meta = self.meta(name)
        kwargs = dict(arguments)
        # Un outil SYNCHRONE hors de la boucle d'événements : exécuté dedans, il
        # la bloquait, et aucun délai (`within_deadline`) ne pouvait tomber.
        result = (fn(**kwargs) if inspect.iscoroutinefunction(fn)
                  else await _in_daemon_thread(lambda: fn(**kwargs)))
        if inspect.isawaitable(result):
            result = await result
        outcome = result if isinstance(result, ToolOutcome) else ToolOutcome(
            content=result if isinstance(result, str) else json.dumps(
                result, ensure_ascii=False, sort_keys=True, default=str),
            side_effect_class=str(meta.get("side_effect_class", "read-only")),
            trust=str(meta.get("trust", "trusted")))
        limit = meta.get("max_response_bytes")
        if isinstance(limit, int) and limit > 0 and len(outcome.content.encode("utf-8")) > limit:
            # Refusée, pas tronquée : une réponse coupée en silence est prise
            # pour complète, et l'agent conclut sur la moitié des données.
            return ToolOutcome(content=f"réponse de `{name}` au-delà de {limit} octets", ok=False,
                               error_code="TOOL_RESPONSE_TOO_LARGE",
                               side_effect_class=outcome.side_effect_class, trust=outcome.trust)
        return outcome


# ---------------------------------------------------------------------------
# Résultat d'un tour d'agent
# ---------------------------------------------------------------------------
#: Les quatre issues possibles d'un agent. Liste close : chacune a un code de
#: sortie distinct dans `serving/exit_codes.py`, et « dégradé » n'est pas
#: « réussi » — le runner d'eval le compte en jaune.
STATUSES: tuple[str, ...] = ("ok", "degraded", "failed", "interrupted")


@dataclass
class AgentResult:
    """Ce qu'un agent rend. `status` est ce que l'appelant traduit en code."""

    output: Any = None
    status: str = "ok"
    bound_exceeded: str = ""
    bound_policy: str = ""
    #: Valeur OBSERVÉE de la borne tombée (tours, appels, secondes, dollars…).
    #: L'événement `bound_exceeded` publiait toujours le coût, quelle que soit
    #: la borne : un dépassement d'itérations affichait « observed: 0.002 ».
    bound_observed: float | None = None
    bound_limit: float | None = None
    error_class: str = ""
    message: str = ""
    iterations: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    partial_state: dict[str, Any] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return self.status == "degraded"

    def to_dict(self) -> dict[str, Any]:
        return {"output": self.output, "status": self.status, "degraded": self.degraded,
                "boundExceeded": self.bound_exceeded, "boundPolicy": self.bound_policy,
                "errorClass": self.error_class, "message": self.message,
                "iterations": self.iterations, "toolCalls": self.tool_calls,
                "costUsd": round(self.cost_usd, 6)}


def apply_bound_policy(exc: BoundExceeded, *, guard: BoundGuard | None = None,
                       degraded_output: Any = None) -> AgentResult:
    """Traduit une borne tombée en RÉSULTAT, selon la politique déclarée.

    Les trois politiques ne sont pas trois façons d'échouer : ce sont trois
    réponses différentes pour l'appelant. `fail-explicit` rend un échec
    structuré avec l'état partiel — la seule honnête quand la réponse partielle
    ne vaut rien. `degrade` rend ce qu'on a en l'ANNONÇANT : une réponse
    partielle prise pour complète est pire qu'une absence de réponse.
    `escalate-human` s'arrête et attend une décision, ce qui suppose un
    checkpoint — sans lui, la reprise n'existe pas et le run est perdu.
    """
    base: dict[str, Any] = {
        "bound_exceeded": exc.bound, "bound_policy": exc.policy,
        "bound_observed": exc.observed, "bound_limit": exc.limit,
        # `guard` absent : une borne du RUN (hops, tokens) tombée hors de tout
        # agent — l'état partiel est alors celui que porte l'exception.
        "message": str(exc),
        "partial_state": dict(exc.partial_state or (guard.state() if guard is not None else {})),
        "iterations": guard.iterations if guard is not None else 0,
        "tool_calls": guard.tool_calls if guard is not None else 0,
        "cost_usd": guard.cost_usd if guard is not None else 0.0,
    }
    if exc.policy == "degrade":
        return AgentResult(output=degraded_output, status="degraded", **base)
    if exc.policy == "escalate-human":
        return AgentResult(output=None, status="interrupted", **base)
    return AgentResult(output=None, status="failed",
                       error_class="BUDGET_BOUND_EXCEEDED", **base)


# ---------------------------------------------------------------------------
# La boucle
# ---------------------------------------------------------------------------
class BoundedLoop:
    """Boucle de raisonnement bornée — le pattern `single-agent`, en code.

    Sous-classer pour changer la façon de construire les messages
    (`build_messages`) ou de conclure (`finalize`) ; la mécanique des bornes,
    elle, n'est pas un point d'extension. C'est le propos : un agent ne doit pas
    pouvoir redéfinir ce qui le plafonne.
    """

    def __init__(self, *, agent_id: str, bounds: Bounds, client: LLMClient, model: str,
                 system_prompt: str = "", agent_name: str = "", tier: str = "",
                 toolset: DictToolset | None = None, tracer: Tracer | None = None,
                 guardrails: Guardrails | None = None,
                 max_output_tokens: int | None = None) -> None:
        self.agent_id = agent_id
        #: Plafond de tokens de SORTIE par appel, transmis au fournisseur.
        #: `None` : le défaut de l'adaptateur (4096 chez Anthropic, qui l'exige).
        self.max_output_tokens = max_output_tokens
        # Les guardrails s'appliquent au texte d'un TIERS qui entre dans la
        # boucle (sorties d'outils `untrusted`). Absents : rien n'est filtré,
        # ce qui est le comportement déclaré quand STACK.md n'en active aucun.
        self.guardrails = guardrails or Guardrails()
        self.agent_name = agent_name or agent_id
        self.bounds = bounds
        self.client = client
        self.model = model
        self.tier = tier
        self.system_prompt = system_prompt
        self.toolset = toolset or DictToolset()
        # La table de tarifs appartient au TRACEUR, pas à la boucle : c'est lui
        # qui recalcule le coût depuis les tokens, et deux détenteurs de la même
        # table finiraient par en avoir deux versions — dont une à zéro, qui
        # passerait sous tous les plafonds sans rien dire.
        self.tracer = tracer or Tracer()

    # -- Points d'extension -------------------------------------------------
    def build_messages(self, user_input: Untrusted) -> list[Message]:
        """Le contexte initial. Le texte de l'utilisateur est ENVELOPPÉ.

        Il entre en message `user`, jamais concaténé au système : une phrase
        déposée dans une entrée ne doit pas pouvoir se lire comme une consigne
        (P8). Le prompt système, lui, vient d'un fichier hashé — une chaîne
        système littérale dans le code est `[PROMPT_INLINE]`.
        """
        messages: list[Message] = []
        if self.system_prompt:
            messages.append(Message(role="system", content=self.system_prompt))
        messages.append(Message(role="user",
                                content=wrap(user_input, source="user", field="input")))
        return messages

    def finalize(self, messages: Sequence[Message], completion: Completion) -> Any:
        """La sortie rendue quand le modèle a conclu. Surchargée pour valider un schéma."""
        return completion.text

    def degraded_output(self, messages: Sequence[Message]) -> Any:
        """La meilleure réponse partielle, pour la politique `degrade`."""
        for message in reversed(list(messages)):
            if message.role == "assistant" and message.content:
                return message.content
        return None

    # -- La boucle ----------------------------------------------------------
    async def run(self, user_input: Untrusted, *, thread_id: str = "") -> AgentResult:
        # Délégation : appelé depuis un outil d'un autre agent, cet agent
        # compte comme un étage de plus POUR L'APPELANT. Le dépassement lève
        # ici, remonte par l'outil jusqu'à la boucle de l'appelant, qui applique
        # SA politique — c'est lui qui n'avait pas le droit de déléguer.
        parent = _CURRENT_GUARD.get()
        if parent is not None:
            parent.enter_delegation()
        guard = BoundGuard(self.bounds, depth=parent.depth if parent is not None else 0)
        token = _CURRENT_GUARD.set(guard)
        try:
            return await self._run(guard, user_input, thread_id=thread_id)
        finally:
            _CURRENT_GUARD.reset(token)
            if parent is not None:
                parent.leave_delegation()

    async def _run(self, guard: BoundGuard, user_input: Untrusted, *, thread_id: str) -> AgentResult:
        messages = self.build_messages(user_input)

        with self.tracer.agent_turn(agent_id=self.agent_id, agent_name=self.agent_name,
                                    tier=self.tier, thread_id=thread_id,
                                    bounds=self.bounds) as agent_span:
            try:
                # Borne 1 : le nombre de tours. `range`, jamais `while True` —
                # une boucle dont la sortie dépend du modèle dépend d'un tirage.
                for _ in range(self.bounds.max_iterations):
                    guard.enter_iteration()
                    agent_span.set("sdda.agent.iteration", guard.iterations)

                    completion = await self._complete(messages, guard)
                    messages.append(Message(role="assistant", content=completion.text,
                                            tool_calls=tuple(completion.tool_calls)))

                    if not completion.tool_calls:
                        return AgentResult(
                            output=self.finalize(messages, completion), status="ok",
                            iterations=guard.iterations, tool_calls=guard.tool_calls,
                            cost_usd=guard.cost_usd, messages=messages)

                    # Borne 3 : le LOT entier est vérifié avant d'exécuter quoi
                    # que ce soit. Après, l'effet de bord a déjà eu lieu.
                    guard.check_tool_calls(len(completion.tool_calls))
                    for call in completion.tool_calls:
                        messages.append(await self._execute(call, guard))
                        guard.record_tool_calls(1)

                # Sortie de boucle sans conclusion : c'est `max_iterations`, et
                # elle se traite comme toute autre borne — pas en rendant le
                # dernier message comme s'il était une réponse.
                guard.fail("max_iterations", self.bounds.max_iterations, guard.iterations)
                raise AssertionError("inatteignable")  # pragma: no cover
            except BoundExceeded as exc:
                agent_span.bound_exceeded(exc.bound, exc.limit, exc.observed, exc.policy)
                result = apply_bound_policy(exc, guard=guard,
                                            degraded_output=self.degraded_output(messages))
                result.messages = messages
                return result

    async def _complete(self, messages: Sequence[Message], guard: BoundGuard) -> Completion:
        """Un appel au modèle, tracé, avec son coût recalculé et imputé au budget."""
        options: dict[str, Any] = {}
        if self.max_output_tokens:
            options["max_tokens"] = int(self.max_output_tokens)
        history, specs = list(messages), self.toolset.specs()
        with self.tracer.llm_call(model=self.model, tier=self.tier) as span:
            # Sous le temps qui reste : `timeout_s` borne l'appel EN COURS, pas
            # seulement l'entrée du tour suivant (cf. `within_deadline`).
            completion: Completion = await within_deadline(
                guard, lambda: self.client.complete(history, model=self.model, tools=specs, **options))
            usd = self.tracer.record_usage(span, model=completion.model or self.model,
                                           usage=completion.usage,
                                           finish_reason=completion.finish_reason)
        # Borne 2 hors du span : le coût du tour doit être écrit dans la trace
        # AVANT que le dépassement n'interrompe — sinon le span qui prouve le
        # dépassement est justement celui qui manque.
        guard.add_cost(usd)
        return completion

    async def _execute(self, call: ToolCall, guard: BoundGuard) -> Message:
        """Un appel d'outil, tracé — succès comme échec, sous le temps qui reste.

        Le retour est un message de rôle `tool`, et son contenu est traité comme
        non maîtrisé : la sortie d'un outil est du texte d'un tiers.
        """
        meta = self.toolset.meta(call.name)
        try:
            cap = float(meta["timeout_s"]) if meta.get("timeout_s") else None
        except (TypeError, ValueError):
            cap = None
        with self.tracer.tool_call(tool=call.name, call_id=call.id,
                                   side_effect_class=str(meta.get("side_effect_class", "read-only")),
                                   trust=str(meta.get("trust", "trusted")),
                                   args=call.arguments) as span:
            try:
                outcome: ToolOutcome = await within_deadline(
                    guard, lambda: self.toolset.call(call.name, call.arguments), cap_s=cap)
            except CallTimedOut as exc:
                # Le délai du CONTRAT de l'outil : une erreur déclarée (`TIMEOUT`),
                # que l'agent peut traiter — pas la fin du run.
                outcome = ToolOutcome(content=str(exc), ok=False, error_code="TIMEOUT",
                                      side_effect_class=str(meta.get("side_effect_class", "read-only")),
                                      trust=str(meta.get("trust", "trusted")))
            span.set("sdda.tool.result.bytes", len(outcome.content.encode("utf-8")))
            if not outcome.ok:
                span.set("sdda.tool.error_code", outcome.error_code)
                span.error(outcome.error_code or "TOOL_ERROR")
        content = outcome.content
        if outcome.trust == "untrusted":
            # Injection INDIRECTE : la voie d'attaque qui compte. Le passage qui
            # instruit est neutralisé AVANT l'enveloppe — l'enveloppe rend la
            # frontière visible, elle n'empêche pas le modèle de lire l'ordre.
            screened = self.guardrails.screen_untrusted(content, source=f"tool:{call.name}",
                                                        tracer=self.tracer)
            content = wrap(screened, source=f"tool:{call.name}", field="result")
        else:
            # Une sortie de CONFIANCE porte quand même des PII (une ligne de
            # base, un dossier client) : elles sont rédigées avant d'atteindre
            # le modèle — donc le fournisseur et la trace —, comme l'entrée.
            content = self.guardrails.redact(content, point=f"tool:{call.name}", tracer=self.tracer)
        return Message(role="tool", content=content, name=call.name, tool_call_id=call.id,
                       is_error=not outcome.ok)
