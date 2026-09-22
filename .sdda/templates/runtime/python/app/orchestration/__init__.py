"""Orchestration — la boucle, le graphe, et le manifeste qui les rend comparables.

Trois patterns, un seul contrat : quel que soit le pattern, le module expose un
`Graph` capable de `dump_graph()`. C'est ce manifeste que `diff_code_vs_ir.py`
confronte à la section `orchestration` de l'IR, part `orchestration` de G6 — « si
le code et le dessin divergent, c'est le code qui a tort », rendu vérifiable.

    base.py        la boucle bornée (single-agent), et le socle `Graph`
    router.py      classification puis spécialiste, un seul passage, repli obligatoire
    sequential.py  N étapes validées, sans cycle

Le manifeste est produit **par le graphe réellement construit**, jamais recopié
depuis l'IR : un manifeste copié rend la comparaison tautologique.
"""
from __future__ import annotations

from .base import (
    AgentResult,
    BoundedLoop,
    DictToolset,
    Edge,
    Graph,
    MANIFEST_NAME,
    Node,
    SingleAgentGraph,
    ToolOutcome,
    apply_bound_policy,
)

__all__ = [
    "AgentResult", "BoundedLoop", "DictToolset", "Edge", "Graph", "MANIFEST_NAME",
    "Node", "SingleAgentGraph", "ToolOutcome", "apply_bound_policy",
]
