"""`sdda_lib.executors` : l'application se charge depuis `workspace/src/`, et une
dépendance absente dit quel interpréteur lancer.

Au premier run réel, `pyAgentic1.evals.executor:InProcessExecutor` sortait
`No module named 'pyAgentic1'` : le paquet vit sous `workspace/src/`, que rien
ne mettait sur le chemin.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sdda_lib import executors


def _app(tmp_path: Path, name: str, body: str) -> Path:
    root = tmp_path / "proj"
    pkg = root / "workspace" / "src" / name
    (pkg / "evals").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "evals" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "evals" / "executor.py").write_text(body, encoding="utf-8")
    return root


def test_the_app_package_is_found_under_workspace_src(tmp_path: Path) -> None:
    root = _app(tmp_path, "AppExecOk", "class Exec:\n    def run(self, *a, **k):\n        return {}\n")
    obj = executors.load_executor("AppExecOk.evals.executor:Exec", root=root)
    assert hasattr(obj, "run")
    sys.path.remove(str(root / "workspace" / "src"))


def test_a_missing_dependency_names_the_app_interpreter(tmp_path: Path) -> None:
    root = _app(tmp_path, "AppExecDep", "import sdda_absent_sdk_xyz\nclass Exec:\n    def run(self):\n        pass\n")
    with pytest.raises(executors.ExecutorLoadError) as err:
        executors.load_executor("AppExecDep.evals.executor:Exec", root=root)
    assert "sdda_absent_sdk_xyz" in str(err.value) and "uv run --project workspace/src/AppExecDep" in str(err.value)
    sys.path.remove(str(root / "workspace" / "src"))


def test_a_missing_method_is_refused(tmp_path: Path) -> None:
    root = _app(tmp_path, "AppExecNoRun", "class Exec:\n    pass\n")
    with pytest.raises(executors.ExecutorLoadError):
        executors.load_executor("AppExecNoRun.evals.executor:Exec", method="retrieve", root=root)
    sys.path.remove(str(root / "workspace" / "src"))
