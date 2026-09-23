"""Algorithmes de graphe orienté pour l'IR : cycles, atteignabilité, tri, SCC.

Les graphes d'orchestration sont petits (dizaines de nœuds) : les
implémentations privilégient la lisibilité et la reproductibilité (ordre
d'itération trié) à la performance asymptotique.
"""
from __future__ import annotations

from collections import deque
from typing import Hashable, Iterable, TypeVar

N = TypeVar("N", bound=Hashable)


class GraphCycleError(ValueError):
    """Levée quand un tri topologique est demandé sur un graphe cyclique."""


class Graph:
    def __init__(self, nodes: Iterable[str] = (), edges: Iterable[tuple[str, str]] = ()):
        self.nodes: list[str] = []
        self.adj: dict[str, list[str]] = {}
        self.radj: dict[str, list[str]] = {}
        for n in nodes:
            self.add_node(n)
        for a, b in edges:
            self.add_edge(a, b)

    # -- construction ------------------------------------------------------
    def add_node(self, n: str) -> None:
        if n not in self.adj:
            self.nodes.append(n)
            self.adj[n] = []
            self.radj[n] = []

    def add_edge(self, a: str, b: str) -> None:
        self.add_node(a)
        self.add_node(b)
        if b not in self.adj[a]:
            self.adj[a].append(b)
            self.radj[b].append(a)

    @property
    def edges(self) -> list[tuple[str, str]]:
        return [(a, b) for a in self.nodes for b in self.adj[a]]

    # -- atteignabilité ----------------------------------------------------
    def reachable_from(self, start: str) -> set[str]:
        """Nœuds atteignables depuis `start` (inclus)."""
        if start not in self.adj:
            return set()
        seen = {start}
        queue = deque([start])
        while queue:
            n = queue.popleft()
            for m in self.adj[n]:
                if m not in seen:
                    seen.add(m)
                    queue.append(m)
        return seen

    def unreachable_from(self, start: str) -> list[str]:
        reach = self.reachable_from(start)
        return sorted(n for n in self.nodes if n not in reach)

    def can_reach(self, targets: Iterable[str]) -> set[str]:
        """Nœuds depuis lesquels au moins un des `targets` est atteignable (BFS inverse)."""
        seen: set[str] = set()
        queue: deque[str] = deque()
        for t in targets:
            if t in self.adj and t not in seen:
                seen.add(t)
                queue.append(t)
        while queue:
            n = queue.popleft()
            for m in self.radj[n]:
                if m not in seen:
                    seen.add(m)
                    queue.append(m)
        return seen

    def nodes_without_path_to(self, targets: Iterable[str]) -> list[str]:
        ok = self.can_reach(targets)
        return sorted(n for n in self.nodes if n not in ok)

    # -- composantes fortement connexes (Tarjan, itératif) -----------------
    def strongly_connected_components(self) -> list[list[str]]:
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        sccs: list[list[str]] = []
        counter = 0

        for root in self.nodes:
            if root in index:
                continue
            work: list[tuple[str, int]] = [(root, 0)]
            while work:
                node, pos = work[-1]
                if pos == 0:
                    index[node] = low[node] = counter
                    counter += 1
                    stack.append(node)
                    on_stack.add(node)
                succs = self.adj[node]
                if pos < len(succs):
                    work[-1] = (node, pos + 1)
                    nxt = succs[pos]
                    if nxt not in index:
                        work.append((nxt, 0))
                    elif nxt in on_stack:
                        low[node] = min(low[node], index[nxt])
                else:
                    work.pop()
                    if work:
                        parent = work[-1][0]
                        low[parent] = min(low[parent], low[node])
                    if low[node] == index[node]:
                        comp: list[str] = []
                        while True:
                            m = stack.pop()
                            on_stack.discard(m)
                            comp.append(m)
                            if m == node:
                                break
                        sccs.append(sorted(comp))
        return sccs

    def has_self_loop(self, n: str) -> bool:
        return n in self.adj.get(n, [])

    # -- cycles élémentaires -----------------------------------------------
    def elementary_cycles(self, max_cycles: int = 500) -> list[list[str]]:
        """Cycles élémentaires, chacun rendu comme liste de nœuds (fermé implicitement).

        Énumération par DFS dans chaque SCC, en ne visitant que les nœuds
        d'indice >= celui du nœud de départ (évite les rotations dupliquées).
        Plafonnée à `max_cycles` : au-delà, le graphe est de toute façon
        inévaluable et sera refusé pour d'autres raisons.
        """
        cycles: list[list[str]] = []
        for comp in self.strongly_connected_components():
            comp_set = set(comp)
            order = {n: i for i, n in enumerate(comp)}
            for start in comp:
                path = [start]
                on_path = {start}

                def dfs(node: str) -> None:
                    for nxt in sorted(self.adj[node]):
                        if len(cycles) >= max_cycles:
                            return
                        if nxt == start:
                            cycles.append(list(path))
                        elif nxt in comp_set and nxt not in on_path and order[nxt] > order[start]:
                            path.append(nxt)
                            on_path.add(nxt)
                            dfs(nxt)
                            path.pop()
                            on_path.discard(nxt)

                dfs(start)
        return cycles

    @staticmethod
    def cycle_edges(cycle: list[str]) -> list[tuple[str, str]]:
        return [(cycle[i], cycle[(i + 1) % len(cycle)]) for i in range(len(cycle))]

    # -- tri topologique ---------------------------------------------------
    def topological_order(self) -> list[str]:
        """Kahn, départages par ordre alphabétique (reproductible). Lève sur cycle."""
        indeg = {n: 0 for n in self.nodes}
        for a in self.nodes:
            for b in self.adj[a]:
                indeg[b] += 1
        ready = sorted(n for n in self.nodes if indeg[n] == 0)
        out: list[str] = []
        while ready:
            n = ready.pop(0)
            out.append(n)
            for m in self.adj[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
                    ready.sort()
        if len(out) != len(self.nodes):
            raise GraphCycleError("le graphe contient un cycle : pas de tri topologique")
        return out

    # -- condensation & plus long chemin -----------------------------------
    def condensation(self) -> tuple["Graph", dict[str, str]]:
        """DAG des SCC. Chaque SCC est nommée par son plus petit nœud."""
        mapping: dict[str, str] = {}
        for comp in self.strongly_connected_components():
            for n in comp:
                mapping[n] = comp[0]
        dag = Graph(sorted(set(mapping.values())))
        for a, b in self.edges:
            if mapping[a] != mapping[b]:
                dag.add_edge(mapping[a], mapping[b])
        return dag, mapping

    def longest_path(self, weights: dict[str, float], start: str | None = None) -> tuple[float, list[str]]:
        """Chemin de poids maximal dans un DAG (poids sur les nœuds). Lève sur cycle."""
        order = self.topological_order()
        best: dict[str, float] = {}
        prev: dict[str, str | None] = {}
        for n in order:
            if start is not None and n != start and n not in best:
                continue
            base = weights.get(n, 0.0)
            if n not in best:
                best[n] = base
                prev[n] = None
            for m in self.adj[n]:
                cand = best[n] + weights.get(m, 0.0)
                if m not in best or cand > best[m]:
                    best[m] = cand
                    prev[m] = n
        if not best:
            return 0.0, []
        end = max(sorted(best), key=lambda k: best[k])
        path: list[str] = []
        cur: str | None = end
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        return best[end], list(reversed(path))
