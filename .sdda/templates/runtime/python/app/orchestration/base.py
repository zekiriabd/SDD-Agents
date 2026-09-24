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

import inspect
import json
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

    def write_manifest(self, directory: Path) -> Path:
        """Écrit `graph.manifest.json` à côté du code d'orchestration.

        Écrit à CHAQUE construction du graphe, pas à la génération : c'est ce
        qui fait qu'un graphe modifié à la main produit un manifeste modifié,
        donc un diff rouge, plutôt qu'un fichier figé qui continue de décrire un
        code qui a changé.
        """
        path = Path(directory) / MANIFEST_NAME
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
    metadata: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.tools))

    def specs(self) -> list[Mapping[str, Any]]:
        return [self.schemas[n] for n in self.names() if n in self.schemas]

    def meta(self, name: str) -> Mapping[str, str]:
        return self.metadata.get(name, {})

    async def call(self, name: str, arguments: Mapping[str, Any]) -> ToolOutcome:
        fn = self.tools.get(name)
        if fn is None:
            # Le modèle a halluciné un outil : c'est une erreur DÉCLARÉE de la
            # boucle, pas une exception. Il doit pouvoir corriger au tour
            # suivant plutôt que faire tomber le run.
            return ToolOutcome(content=f"outil `{name}` inconnu", ok=False,
                               error_code="TOOL_NOT_REGISTERED")
        result = fn(**dict(arguments))
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, ToolOutcome):
            return result
        meta = self.meta(name)
        return ToolOutcome(content=result if isinstance(result, str) else json.dumps(
            result, ensure_ascii=False, sort_keys=True, default=str),
            side_effect_class=meta.get("side_effect_class", "read-only"),
            trust=meta.get("trust", "trusted"))


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


def apply_bound_policy(exc: BoundExceeded, *, guard: BoundGuard,
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
        "message": str(exc), "partial_state": dict(exc.partial_state or guard.state()),
        "iterations": guard.iterations, "tool_calls": guard.tool_calls,
        "cost_usd": guard.cost_usd,
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
                 guardrails: Guardrails | None = None) -> None:
        self.agent_id = agent_id
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
        guard = BoundGuard(self.bounds)
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
                    messages.append(Message(role="assistant", content=completion.text))

                    if not completion.tool_calls:
                        return AgentResult(
                            output=self.finalize(messages, completion), status="ok",
                            iterations=guard.iterations, tool_calls=guard.tool_calls,
                            cost_usd=guard.cost_usd, messages=messages)

                    # Borne 3 : le LOT entier est vérifié avant d'exécuter quoi
                    # que ce soit. Après, l'effet de bord a déjà eu lieu.
                    guard.check_tool_calls(len(completion.tool_calls))
                    for call in completion.tool_calls:
                        messages.append(await self._execute(call))
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
        with self.tracer.llm_call(model=self.model, tier=self.tier) as span:
            result = self.client.complete(list(messages), model=self.model,
                                          tools=self.toolset.specs())
            if inspect.isawaitable(result):
                result = await result
            completion: Completion = result
            usd = self.tracer.record_usage(span, model=completion.model or self.model,
                                           usage=completion.usage,
                                           finish_reason=completion.finish_reason)
        # Borne 2 hors du span : le coût du tour doit être écrit dans la trace
        # AVANT que le dépassement n'interrompe — sinon le span qui prouve le
        # dépassement est justement celui qui manque.
        guard.add_cost(usd)
        return completion

    async def _execute(self, call: ToolCall) -> Message:
        """Un appel d'outil, tracé — succès comme échec.

        Le retour est un message de rôle `tool`, et son contenu est traité comme
        non maîtrisé : la sortie d'un outil est du texte d'un tiers.
        """
        meta = self.toolset.meta(call.name)
        with self.tracer.tool_call(tool=call.name, call_id=call.id,
                                   side_effect_class=meta.get("side_effect_class", "read-only"),
                                   trust=meta.get("trust", "trusted"),
                                   args=call.arguments) as span:
            outcome = await self.toolset.call(call.name, call.arguments)
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
        return Message(role="tool", content=content, name=call.name, tool_call_id=call.id)
