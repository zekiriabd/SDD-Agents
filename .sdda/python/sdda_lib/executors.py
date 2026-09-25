"""Charger l'exécuteur `module:attr` du système évalué — une seule fois, pour les trois runners.

`eval-runner`, `run-adversarial-suite` et `run-retrieval-eval` recopiaient la
même fonction, et la même lacune : le module était cherché « depuis le cwd ».
Or l'application générée est un paquet à plat, `workspace/src/{App}/` — son nom
n'est importable que si `workspace/src/` est sur le chemin. Au premier run réel,
`pyAgentic1.evals.executor:InProcessExecutor` sortait `ModuleNotFoundError: No
module named 'pyAgentic1'`, et G5 ne se jouait pas.

Deux causes, deux réponses :

- le PAQUET introuvable — `workspace/src/` est ajouté au chemin, toujours : c'est
  là que le framework range l'application, ce n'est pas une option ;
- une DÉPENDANCE introuvable (le SDK du fournisseur, le framework d'agents) —
  l'outillage est stdlib seule et ne l'installera pas. Le message dit de lancer
  le runner avec l'interpréteur de l'application, où `project-init` a fait
  `uv sync` : `uv run --project workspace/src/{App} python .sdda/sdda.py …`.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from sdda_lib import paths


class ExecutorLoadError(ValueError):
    """L'exécuteur ne se charge pas ; le message porte déjà la correction."""


def _ensure_src_on_path(root: Path | None) -> None:
    if root is None:
        return
    src = paths.workspace(root) / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def load_executor(spec: str, *, method: str = "run", root: Path | None = None) -> Any:
    """`module:attr` -> objet qui expose `method`. L'attribut peut être une fabrique sans argument."""
    if ":" not in spec:
        raise ExecutorLoadError(f"`{spec}` : attendu `module:attr`")
    mod_name, attr = spec.rsplit(":", 1)
    _ensure_src_on_path(root)
    try:
        module = importlib.import_module(mod_name)
    except ModuleNotFoundError as exc:
        app = mod_name.split(".", 1)[0]
        missing = exc.name or ""
        if missing and missing.split(".", 1)[0] != app:
            raise ExecutorLoadError(
                f"`{spec}` : dépendance `{missing}` absente de cet interpréteur — l'outillage est "
                f"stdlib seule ; lancer le runner avec celui de l'application : "
                f"`uv run --project workspace/src/{app} python .sdda/sdda.py …`") from exc
        raise ExecutorLoadError(
            f"`{spec}` : module `{mod_name}` introuvable, y compris sous workspace/src/") from exc
    obj = getattr(module, attr)
    if isinstance(obj, type) or (callable(obj) and not hasattr(obj, method)):
        obj = obj()
    if not hasattr(obj, method):
        raise ExecutorLoadError(f"`{spec}` ne fournit pas de méthode `{method}`")
    return obj
