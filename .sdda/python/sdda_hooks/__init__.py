"""Hooks du harnais — contrôles bloquants joués AU MOMENT de l'action.

Sur `claude-code` (niveau A) ils s'exécutent avant chaque `Write`/`Edit` et avant
chaque spawn d'agent. Sur les harnais de niveau B, les mêmes invariants
basculent en contrôles CI : plus tard, après que le travail a été fait sur une
base fausse. La différence est mesurée dans `docs/MULTI-HARNESS.md`, pas supposée.
"""
