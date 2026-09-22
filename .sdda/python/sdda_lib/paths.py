"""Résolution des chemins du framework et du workspace.

Racine de projet = le dossier qui contient `workspace/` (et en général `.sdda/`).
Le framework lui-même (`.sdda/`) est localisé depuis ce fichier, ce qui permet
aux scripts de tourner sur un workspace de test qui n'embarque pas `.sdda/`.
"""
from __future__ import annotations

import os
from pathlib import Path

# .sdda/python/sdda_lib/paths.py -> parents[2] == .sdda
FRAMEWORK_SDDA_DIR: Path = Path(__file__).resolve().parents[2]


def find_root(start: Path | None = None) -> Path:
    """Remonte depuis `start` (défaut : cwd) jusqu'à un dossier contenant `workspace/`.

    Repli : le dossier contenant `.sdda/`, sinon `start` lui-même.
    """
    cur = (start or Path(os.getcwd())).resolve()
    candidates = [cur, *cur.parents]
    for d in candidates:
        if (d / "workspace").is_dir():
            return d
    for d in candidates:
        if (d / ".sdda").is_dir():
            return d
    return cur


def workspace(root: Path) -> Path:
    return root / "workspace"


def missions_dir(root: Path) -> Path:
    return workspace(root) / "missions"


def caps_dir(root: Path) -> Path:
    return workspace(root) / "caps"


def topology_dir(root: Path) -> Path:
    return workspace(root) / "topology"


def contracts_dir(root: Path, kind: str) -> Path:
    """kind ∈ {agents, tools, retrieval, memory}."""
    return workspace(root) / "contracts" / kind


def prompts_dir(root: Path) -> Path:
    return workspace(root) / "prompts"


def datasets_dir(root: Path, kind: str | None = None) -> Path:
    base = workspace(root) / "datasets"
    return base / kind if kind else base


def evals_dir(root: Path) -> Path:
    return workspace(root) / "evals"


def ir_dir(root: Path) -> Path:
    return workspace(root) / ".sys" / ".ir"


def ir_path(root: Path, mission_number: int | str) -> Path:
    return ir_dir(root) / f"{mission_number}-system.ir.json"


def validation_dir(root: Path) -> Path:
    return workspace(root) / ".sys" / ".validation"


def state_dir(root: Path) -> Path:
    return workspace(root) / ".sys" / ".state"


def audit_dir(root: Path) -> Path:
    return workspace(root) / ".sys" / ".audit"


def stack_md_path(root: Path) -> Path:
    return workspace(root) / "stack" / "STACK.md"


def base_config_path(root: Path) -> Path:
    """`.sdda/config.base.yml` du projet, sinon celui du framework."""
    local = root / ".sdda" / "config.base.yml"
    return local if local.is_file() else FRAMEWORK_SDDA_DIR / "config.base.yml"


def project_config_schema_path(root: Path) -> Path:
    local = root / ".sdda" / "templates" / "project-config.schema.json"
    if local.is_file():
        return local
    return FRAMEWORK_SDDA_DIR / "templates" / "project-config.schema.json"


def ir_schema_path(root: Path | None = None) -> Path:
    if root is not None:
        local = root / ".sdda" / "registry" / "ir.schema.json"
        if local.is_file():
            return local
    return FRAMEWORK_SDDA_DIR / "registry" / "ir.schema.json"


def rel(root: Path, path: Path) -> str:
    """Chemin relatif POSIX (stable Windows/Linux) pour les rapports et l'IR."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_rel(root: Path, ref: str) -> Path:
    """Inverse de `rel` : une référence `workspace/...` devient un chemin absolu."""
    return (root / ref).resolve()
