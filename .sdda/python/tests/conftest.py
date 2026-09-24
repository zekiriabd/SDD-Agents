"""Plomberie des tests : projets temporaires construits depuis `fixtures/project_ok/` + overlays.

Chaque test travaille sur une COPIE : les scripts écrivent des rapports de gate,
un IR, et peuvent réécrire un `Status:` (compute_status R1). La fixture source ne
doit jamais bouger sous les tests.
"""
from __future__ import annotations

import io
import shutil
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Callable

import pytest

PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

FIXTURES = Path(__file__).resolve().parent / "fixtures"

import _workspace_guard  # noqa: E402 — après l'insertion de PYTHON_DIR dans sys.path

_workspace_guard.install()
_WORKSPACE_BEFORE: dict[str, tuple[int, int]] = {}


def pytest_sessionstart(session: pytest.Session) -> None:
    """Photographie le `workspace/` réel : un sous-processus échappe à l'audit hook."""
    _WORKSPACE_BEFORE.update(_workspace_guard.snapshot())


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    changes = _workspace_guard.diff(_WORKSPACE_BEFORE, _workspace_guard.snapshot())
    if changes:
        sys.stderr.write(
            "\nERREUR : la suite de tests a modifié le workspace RÉEL "
            f"({_workspace_guard.REAL_WORKSPACE}) :\n  " + "\n  ".join(changes[:20])
            + "\nUn test travaille sur `make_project(tmp_path)` et lance ses sous-processus avec "
              "`--root`/`cwd` vers un bac à sable.\n")
        session.exitstatus = 1


@pytest.fixture(autouse=True)
def _no_write_under_real_workspace():
    """Toute écriture tentée sous le `workspace/` réel est refusée, et fait échouer le test."""
    with _workspace_guard.armed():
        yield


def make_project(tmp_path: Path, *overlays: str) -> Path:
    """Copie `project_ok/` puis recouvre avec chaque overlay (fichiers différents seulement).

    Un `.gitignore` de fixture est stocké sous le nom `_gitignore` : c'est du
    CONTENU de projet testé (le validateur le lit), mais git l'honorerait aussi
    et exclurait du dépôt les `.env` / `STACK.md` de la fixture. On le renomme
    à la copie, jamais sur la source.
    """
    dst = tmp_path / "project"
    shutil.copytree(FIXTURES / "project_ok", dst)
    for name in overlays:
        src = FIXTURES / name
        assert src.is_dir(), f"overlay inconnu : {name}"
        shutil.copytree(src, dst, dirs_exist_ok=True)
    for placeholder in dst.rglob("_gitignore"):
        placeholder.replace(placeholder.with_name(".gitignore"))
    return dst


def run_main(main: Callable[[list[str] | None], int], argv: list[str]) -> tuple[int, str]:
    """Exécute le `main(argv)` d'un script en capturant stdout+stderr ; renvoie (code, sortie)."""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        code = main(argv)
    return code, buf.getvalue()


@pytest.fixture(autouse=True)
def _isolated_team_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Aucune couche `~/.sdda/config.team.yml` de la machine ne doit influencer les tests."""
    monkeypatch.setenv("SDDA_TEAM_CONFIG", str(tmp_path / "no-team-config.yml"))
    monkeypatch.delenv("SDDA_BYPASS_BUDGET_ESTIMATE", raising=False)
    # La CI tourne en `SDDA_HOOKS_STRICT=1` ; les tests unitaires fixent eux-mêmes
    # le mode qu'ils exercent, sinon les tests de dégradation deviendraient faux.
    monkeypatch.delenv("SDDA_HOOKS_STRICT", raising=False)
    monkeypatch.delenv("SDDA_BYPASS_REASON", raising=False)
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    # Le runner construit un juge RÉEL dès que la clé nommée par la fiche
    # provider est dans l'environnement : un poste de développeur qui l'exporte
    # ferait appeler l'API facturée par la suite de tests. Les tests qui veulent
    # un juge posent leur propre clé factice et un serveur local.
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
                 "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEMINI_BASE_URL", "OLLAMA_HOST", "OLLAMA_API_KEY",
                 "AZURE_OPENAI_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path)
