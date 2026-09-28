"""Le graphe LangGraph du pattern par défaut. GÉNÉRÉ, ne pas éditer.

Émis seulement quand `framework/langgraph.md` est actif (`gen_app_skeleton`).
C'est la moitié `orchestration/` de l'alignement sur la stack déclarée : le
squelette faisait tourner la boucle bornée seule, hors de tout graphe, alors
que STACK.md déclarait LangGraph — `validate-framework` rendait G6 rouge
(`[FRAMEWORK_DRIFT]`) sur une application qui marchait.

`single-agent` devient ici un `StateGraph` à un nœud (`START -> agent -> END`),
compilé, et exécuté par `ainvoke` — le « graphe à un nœud » de
`langgraph.md`. Le nœud appelle `BoundedLoop.run` : les cinq bornes restent
dans la boucle (`langgraph.md §5.4` — `recursion_limit` est un filet, jamais la
borne), et un `GraphRecursionError` est un bug du graphe, rendu
`[UNBOUNDED_LOOP]`.

Ce que `dev-orchestration` écrit pour un `router`, un `sequential` ou un
`supervisor` suit la même fiche et REMPLACE ce graphe derrière `agent_factory` ;
`manifest_from_compiled` lui sert tel quel pour introspecter le sien.

Le manifeste est l'introspection du graphe COMPILÉ (`get_graph()`), jamais une
recopie de l'IR : `generatedBy` le dit (`orchestration.langgraph`), et la
boucle de démarrage ne l'écrase pas (`base.foreign_manifest`).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from ..trust import Untrusted
from .base import ALWAYS, MANIFEST_NAME, AgentResult, BoundedLoop

#: Qui a produit le manifeste — lu par `diff_code_vs_ir.py`.
GENERATED_BY = "orchestration.langgraph"


class AgentGraphState(TypedDict, total=False):
    """L'état du graphe. Aucun secret, aucun texte de prompt (`langgraph.md §3.2`)."""

    input: Untrusted
    thread_id: str
    result: AgentResult


def recursion_limit(max_hops: int) -> int:
    """Le filet de `langgraph.md §3.3` : `max_hops * 4 + 10` super-steps."""
    return max(1, int(max_hops)) * 4 + 10


def manifest_from_compiled(compiled: Any, *, max_hops: int, kinds: Mapping[str, str] | None = None,
                           refs: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Le manifeste que `diff_code_vs_ir.py` attend, lu dans le graphe compilé.

    `START` et `END` ne sont pas des nœuds de l'IR : l'arête qui part de `START`
    désigne `entryNode`, celles qui mènent à `END` les `terminalNodes`. Une arête
    conditionnelle porte son étiquette (`data`) — la condition telle que l'IR
    l'écrit, si le graphe l'a nommée ainsi.
    """
    kinds, refs = dict(kinds or {}), dict(refs or {})
    drawn = compiled.get_graph()
    ends = {START, END}
    nodes = [n for n in drawn.nodes if n not in ends]
    entry = [e.target for e in drawn.edges if e.source == START]
    terminals = sorted({e.source for e in drawn.edges if e.target == END})
    edges = [{"from": e.source, "to": e.target,
              "condition": str(e.data) if e.conditional and e.data else ALWAYS}
             for e in drawn.edges if e.source not in ends and e.target not in ends]
    return {
        "generatedBy": GENERATED_BY,
        "entryNode": entry[0] if entry else "",
        "terminalNodes": terminals,
        "maxHops": int(max_hops),
        "nodes": [{"id": n, "kind": kinds.get(n, "agent"), **({"ref": refs[n]} if refs.get(n) else {})}
                  for n in nodes],
        "edges": sorted(edges, key=lambda e: (e["from"], e["to"], e["condition"])),
    }


class LangGraphAgent:
    """`single-agent` : la boucle bornée, portée par un `StateGraph` compilé.

    Même interface que `BoundedLoop` (`agent_id`, `run(user_input, thread_id=)`
    -> `AgentResult`) : `RunService` l'exécute sans savoir qu'un graphe le porte.
    """

    def __init__(self, loop: BoundedLoop, *, node_id: str = "agent", ref: str = "", max_hops: int = 1,
                 checkpointer: Any = None) -> None:
        self.loop = loop
        self.agent_id = loop.agent_id
        self.node_id = node_id
        self.ref = ref
        self.max_hops = max(1, int(max_hops))
        graph = StateGraph(AgentGraphState)
        graph.add_node(node_id, self._agent_node)
        graph.add_edge(START, node_id)
        graph.add_edge(node_id, END)
        # Sans checkpointer par défaut : `single-agent` n'a ni reprise ni
        # `escalate-human` à porter. Un contrat qui les exige passe le sien.
        self.checkpointer = checkpointer
        self.compiled = graph.compile(checkpointer=checkpointer)

    async def _agent_node(self, state: AgentGraphState) -> dict[str, Any]:
        return {"result": await self.loop.run(state["input"], thread_id=state.get("thread_id", ""))}

    async def run(self, user_input: Untrusted, *, thread_id: str = "") -> AgentResult:
        config: RunnableConfig = {"recursion_limit": recursion_limit(self.max_hops)}
        if self.checkpointer is not None:
            # `thread_id` = `run_id` (`langgraph.md §5.7`) : pas de reprise sans lui.
            config["configurable"] = {"thread_id": thread_id or self.agent_id}
        try:
            inputs: AgentGraphState = {"input": user_input, "thread_id": thread_id}
            final: Any = await self.compiled.ainvoke(inputs, config=config)
        except GraphRecursionError as exc:
            return AgentResult(status="failed", error_class="UNBOUNDED_LOOP",
                               message=f"filet `recursion_limit` atteint — bug du graphe : {exc}")
        result = final.get("result") if isinstance(final, Mapping) else None
        if not isinstance(result, AgentResult):
            return AgentResult(status="failed", error_class="INTERNAL_ERROR",
                               message=f"le nœud `{self.node_id}` n'a rendu aucun résultat d'agent")
        return result

    def dump_graph(self) -> dict[str, Any]:
        return manifest_from_compiled(self.compiled, max_hops=self.max_hops,
                                      refs={self.node_id: self.ref} if self.ref else None)

    def write_manifest(self, directory: Path) -> Path:
        """Écrit `graph.manifest.json` — à chaque construction, comme `base.Graph`."""
        path = Path(directory) / MANIFEST_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.dump_graph(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        return path
