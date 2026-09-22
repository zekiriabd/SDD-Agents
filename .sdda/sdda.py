#!/usr/bin/env python3
"""Lanceur : `python .sdda/sdda.py {sous-commande} [args]`.

Trois lignes utiles, et c'est voulu. Tout le dispatch vit dans
`python/sdda_cli.py` ; ce fichier n'existe que pour donner aux prompts et aux
commandes un chemin court qui marche **depuis un clone nu**, sans
`pip install`, sans variable d'environnement, sans activation de venv.

L'équivalent installé est `sdda {sous-commande}` (entrée console déclarée dans
`python/pyproject.toml`) — même fonction, même code de sortie.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))

from sdda_cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
