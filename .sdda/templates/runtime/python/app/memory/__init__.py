"""memory/ — l'implémentation du contrat de mémoire (`feats/contracts/memory/{n}-memory.md`).

Fenêtre de conversation (`ShortTermPolicy`, `ShortTermMaxTurns`), état partagé
entre agents (`CrossAgentSharedState`, matrice d'ownership du contrat, appliquée
au runtime), politique PII à l'écriture (`MemoryPIIPolicy`). Aucune mémoire
longue tant que `LongTermEnabled: false`.

Owner : `dev-orchestration` (PHASE 5) — il possède déjà l'état du graphe, et une
mémoire est un état qui survit au tour. GÉNÉRÉ vide par le squelette : ce module
existe pour que le contrat ait un endroit où devenir du code.
"""
