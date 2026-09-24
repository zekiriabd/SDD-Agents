"""Ce que le runner d'eval charge pour mesurer le système. GÉNÉRÉ, ne pas éditer.

    executor.py    `InProcessExecutor` (L4, agent isolé) et `CliExecutor` (L5/L7/L9)

Le paquet ne contient **ni datasets, ni suites, ni baselines** : ils vivent dans
`workspace/pipeline/datasets/` et `workspace/pipeline/`, et leur ownership appartient à
`qa-evals`. Le code qui est jugé ne peut pas écrire le jeu qui le juge — sans
cette séparation, l'auto-confirmation est garantie.
"""
from __future__ import annotations

from .executor import CliExecutor, InProcessExecutor, frozen_retrieval, mocked_toolset

__all__ = ["CliExecutor", "InProcessExecutor", "frozen_retrieval", "mocked_toolset"]
