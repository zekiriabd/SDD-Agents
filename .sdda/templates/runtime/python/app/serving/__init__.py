"""Surfaces d'exposition. Toutes appellent `RunService`, seul le transport change.

    cli.py         la console — le livrable par défaut (`cli-exe`)
    exit_codes.py  la table `[CLASS]` -> code, à UN seul endroit

Les autres surfaces (`fastapi-sse`, `mcp-server`, `slack-bot`, `chainlit`) sont
écrites par `dev-api` et réutilisent le même `RunService` et le même schéma
d'événements : c'est ce qui fait que ce qu'on mesure en eval est ce qu'on livre.
"""
from __future__ import annotations

from .exit_codes import CLASS_TO_EXIT, ExitCode, resolve_exit_code

__all__ = ["CLASS_TO_EXIT", "ExitCode", "resolve_exit_code"]
