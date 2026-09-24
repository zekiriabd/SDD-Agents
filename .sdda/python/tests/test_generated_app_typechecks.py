"""Le code que le framework GÉNÈRE passe `mypy --strict` — celui que le pyproject généré exige.

Premier run réel : 18 erreurs dans le seul code des gabarits et des wrappers
de données (`**base` non typé, `ctx.clock` optionnel appelé, `Any` rendu pour
un `Output`, dépendances optionnelles sans stubs). Le pyproject généré déclare
`strict = true` : l'application naissait donc rouge sur sa propre règle, et les
agents `dev-*` héritaient d'erreurs qu'ils n'avaient pas écrites.

`mypy` n'est pas une dépendance du framework. Le test tourne avec
l'interpréteur désigné par `SDDA_TYPECHECK_PYTHON` (celui d'une application,
qui l'a), ou avec l'interpréteur courant s'il a mypy ; sinon il est sauté.
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
    return sys.executable if importlib.util.find_spec("mypy") else None


def test_the_generated_app_is_clean_under_mypy_strict(tmp_path: Path) -> None:
    python = _python()
    if python is None:
        pytest.skip("mypy indisponible : poser SDDA_TYPECHECK_PYTHON sur un interpréteur qui l'a")
    project = make_project(tmp_path, "project_declared_sources")
    assert gen_source_tools.run(project, mode="write").ok
    assert gen_app_skeleton.main(["--root", str(project), "--write", "--no-report"]) == 0
    app = next(p for p in (project / "workspace/src").iterdir() if (p / "pyproject.toml").is_file())
    proc = subprocess.run([python, "-m", "mypy", "--no-incremental", ".", "--exclude", "(^|/)tests/"],
                          cwd=app, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=600, check=False)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-1000:]
