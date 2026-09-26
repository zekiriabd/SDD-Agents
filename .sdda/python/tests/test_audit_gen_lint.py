"""Audit du 2026-09-25, M14 — le code GÉNÉRÉ passe le `ruff` que son propre pyproject exige.

L'application naissait rouge sur sa règle : 5 erreurs `ruff` (E501, I001, B905,
S110) et 3 `mypy --strict` dans le seul squelette. `mypy` est tenu par
`test_generated_app_typechecks.py` ; ce test tient `ruff`, avec le même
interpréteur (`SDDA_TYPECHECK_PYTHON`, ou l'interpréteur courant s'il a ruff).
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import make_project
from sdda_scripts import gen_app_skeleton, gen_source_tools


def _python() -> str | None:
    explicit = os.environ.get("SDDA_TYPECHECK_PYTHON")
    if explicit:
        return explicit
    return sys.executable if importlib.util.find_spec("ruff") else None


def test_the_generated_app_is_clean_under_its_own_ruff_rules(tmp_path: Path) -> None:
    python = _python()
    if python is None:
        pytest.skip("ruff indisponible : poser SDDA_TYPECHECK_PYTHON sur un interpréteur qui l'a")
    project = make_project(tmp_path, "project_declared_sources")
    assert gen_source_tools.run(project, mode="write").ok
    assert gen_app_skeleton.run(project, mode="write").ok
    app = next(p for p in (project / "workspace/src").iterdir() if (p / "pyproject.toml").is_file())
    proc = subprocess.run([python, "-m", "ruff", "check", ".", "--output-format", "concise"], cwd=app,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                          check=False)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-1000:]
