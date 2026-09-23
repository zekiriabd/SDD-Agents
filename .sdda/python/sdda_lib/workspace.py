"""Version du workspace : ce que le framework attend sur disque, et ce qu'il y trouve.

`workspace/.sys/workspace.json` porte un entier `workspaceVersion`. Sans lui, une
montée de version du framework qui change l'arborescence n'a aucun chemin de
migration : les répertoires fantômes s'accumulent (`.sys/.routing`, `.sys/.cache`,
`.sys/.reverse` — créés par un bootstrap ancien, lus par rien) et un répertoire
attendu manque jusqu'au premier `[WORKSPACE_TREE_INCOMPLETE]`.

`WORKSPACE_VERSION` est LA constante : `bootstrap.py` l'écrit, `smoke_check.py`
la compare, `migrate_workspace.py` fait monter un workspace jusqu'à elle. Elle
vit ici et non dans `paths.py` parce que `paths.py` ne fait que résoudre des
chemins ; lire et écrire un fichier de version, dater, retrouver la version du
framework dans `pyproject.toml`, c'est un autre sujet — et `bootstrap.py`
l'importe seul, sans le reste des scripts.

Incrémenter `WORKSPACE_VERSION` exige une entrée dans
`sdda_scripts.migrate_workspace.MIGRATIONS` : sans elle, aucun workspace
existant ne peut plus passer le smoke.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any

from sdda_lib import __version__ as _LIB_VERSION

#: Version courante de l'arborescence `workspace/`. Historique :
#:   1 — première version datée : tree canonique de `smoke_check.WORKSPACE_TREE`,
#:       retrait des répertoires fantômes `.sys/.routing`, `.sys/.cache`, `.sys/.reverse`.
#:   2 — quatre entrées : feats/ · stack/ · src/ · proof/ · .sys/.
#:   3 — l'entrée de l'utilisateur tient en trois choses : `STACK.md` versionné
#:       (secrets dans `.env`), du Markdown seul sous `feats/` (roster en
#:       `{n}-roster.md`, graphe inline), la vérité terrain sous `proof/seed/`.
#:       Les schémas figés partent avec le code (`src/{App}/.../data/schemas/`),
#:       les manifestes de sources rentrent dans STACK.md.
#:   4 — layout PLAT de l'application, comme SDD_Pro : `workspace/src/{App}/` EST
#:       le paquet (`agents/`, `tools/`, `data/`, `orchestration/`, `serving/`,
#:       `app/` à un seul niveau, `pyproject.toml` et `.env` à sa racine). Le
#:       « src layout » `{App}/src/{App}/` doublait le nom du projet et cachait
#:       l'application deux répertoires plus bas.
WORKSPACE_VERSION: int = 4

WORKSPACE_JSON_REL = "workspace/.sys/workspace.json"


def workspace_json_path(root: Path) -> Path:
    return root / "workspace" / ".sys" / "workspace.json"


def framework_version() -> str:
    """Version du framework — `.sdda/python/pyproject.toml`, sinon `sdda_lib.__version__`."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        import tomllib

        with pyproject.open("rb") as handle:
            version = tomllib.load(handle).get("project", {}).get("version")
        if isinstance(version, str) and version:
            return version
    except Exception:
        pass
    return _LIB_VERSION


def utc_now_iso() -> str:
    """Horodatage ISO 8601 UTC à la seconde ; honore `SOURCE_DATE_EPOCH` (builds reproductibles)."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch and epoch.isdigit():
        now = _dt.datetime.fromtimestamp(int(epoch), _dt.timezone.utc)
    else:
        now = _dt.datetime.now(_dt.timezone.utc)
    return now.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_workspace_json(root: Path) -> dict[str, Any] | None:
    """Le contenu de `workspace.json`, None s'il est absent ou illisible."""
    path = workspace_json_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_workspace_version(root: Path) -> int | None:
    """La version du workspace, None si le fichier manque ou ne porte pas d'entier."""
    data = read_workspace_json(root)
    if not data:
        return None
    version = data.get("workspaceVersion")
    return version if isinstance(version, int) and not isinstance(version, bool) else None


def write_workspace_version(root: Path, *, version: int = WORKSPACE_VERSION,
                            written_by: str | None = None) -> Path:
    """Écrit `workspace.json` à `version`. Renvoie le chemin.

    Un fichier existant garde son `createdBy` / `createdAt` : la migration
    ajoute `updatedBy` / `updatedAt` au lieu de réécrire l'histoire.
    """
    path = workspace_json_path(root)
    stamp = utc_now_iso()
    author = written_by or f"bootstrap {framework_version()}"
    existing = read_workspace_json(root) or {}
    data: dict[str, Any] = {
        "workspaceVersion": version,
        "createdBy": existing.get("createdBy") or author,
        "createdAt": existing.get("createdAt") or stamp,
    }
    if existing:
        data["updatedBy"] = author
        data["updatedAt"] = stamp
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
