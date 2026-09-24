"""Garde : aucun test n'écrit sous le `workspace/` RÉEL du dépôt.

`workspace/.sys/.audit/bypasses.test-pollution-20260924.jsonl` en est la
preuve : 1 187 lignes de faux bypass (`SDDA_BYPASS_NOPE`, raison « test »,
« MCP indisponible en CI ») écrites par la suite de tests dans le journal
d'audit du VRAI projet. `preflight_force_cumul` trouve sa racine en remontant
depuis le répertoire courant ; lancé en sous-processus depuis le dépôt, il
écrivait donc chez l'utilisateur. Le test fautif a été corrigé
(`test_stack_combo_and_cumul._run` impose un bac à sable) — mais rien
n'empêchait le suivant. Un journal d'audit pollué n'est plus relu, et c'est
alors le vrai bypass qu'on rate.

Deux mailles, parce qu'aucune ne suffit seule :

1. **Dans le processus** — un audit hook (`sys.addaudithook`, PEP 578)
   intercepte toute ouverture en écriture, création de répertoire, renommage
   ou suppression sous `workspace/` réel, et la REFUSE avant qu'elle ait lieu
   (`WorkspaceWriteForbidden`, qui n'hérite pas d'`OSError` pour ne pas être
   avalée par un `except OSError`). La violation est aussi notée : le test
   échoue même si son code attrape l'exception.
2. **Hors du processus** — un sous-processus échappe à l'audit hook. L'arbre
   `workspace/` est donc photographié au début de la session (chemin, taille,
   date) et comparé à la fin : toute différence fait échouer la session, avec
   la liste des fichiers touchés.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

#: La racine du dépôt (ou du worktree) qui porte ces tests, et SON workspace.
REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_WORKSPACE = REPO_ROOT / "workspace"

_PREFIX = os.path.normcase(os.path.abspath(REAL_WORKSPACE)) + os.sep
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_PATH_EVENTS = {"os.mkdir": 0, "os.rename": (0, 1), "os.remove": 0, "os.rmdir": 0,
                "os.truncate": 0, "os.link": (0, 1), "os.symlink": (0, 1), "shutil.rmtree": 0,
                "shutil.copyfile": 1, "shutil.move": 1}


class WorkspaceWriteForbidden(RuntimeError):
    """Un test a tenté d'écrire sous le `workspace/` réel du dépôt."""


class _State:
    active = False
    expected = 0
    violations: list[str] = []


def _under_real_workspace(target: Any) -> str | None:
    if isinstance(target, int) or target is None:
        return None
    try:
        raw = os.fsdecode(target)
    except TypeError:
        return None
    full = os.path.normcase(os.path.abspath(raw))
    return raw if (full + os.sep).startswith(_PREFIX) else None


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if not _State.active:
        return
    targets: list[Any] = []
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                  (isinstance(flags, int) and bool(flags & _WRITE_FLAGS))
        if writing:
            targets.append(path)
    elif event in _PATH_EVENTS:
        spec = _PATH_EVENTS[event]
        for index in (spec if isinstance(spec, tuple) else (spec,)):
            if index < len(args):
                targets.append(args[index])
    for target in targets:
        hit = _under_real_workspace(target)
        if hit is not None:
            message = f"{event} sous le workspace RÉEL : {hit}"
            if _State.expected:
                _State.expected -= 1
            else:
                _State.violations.append(message)
            raise WorkspaceWriteForbidden(
                f"{message} — un test travaille sur une copie (`make_project(tmp_path)`) ou passe "
                "`--root` / `cwd` vers un bac à sable, jamais sur le dépôt")


_installed = False


def install() -> None:
    global _installed
    if not _installed:
        sys.addaudithook(_audit)
        _installed = True


@contextmanager
def armed() -> Iterator[None]:
    """Active la garde pour un test ; échoue au démontage si une écriture a été tentée."""
    _State.active, _State.violations = True, []
    try:
        yield
    finally:
        _State.active = False
    if _State.violations:
        raise AssertionError("écriture(s) sous le workspace réel : " + " ; ".join(_State.violations[:5]))


@contextmanager
def expect_violation() -> Iterator[None]:
    """Pour le test de la garde elle-même : la prochaine violation est attendue, pas une faute."""
    _State.expected += 1
    try:
        yield
    finally:
        _State.expected = 0


@contextmanager
def disarmed() -> Iterator[None]:
    """Le nettoyage du test de la garde, et lui seul : retirer une sonde si la garde a failli."""
    previous, _State.active = _State.active, False
    try:
        yield
    finally:
        _State.active = previous


def snapshot(root: Path | None = None) -> dict[str, tuple[int, int]]:
    """chemin relatif -> (taille, mtime_ns) de tout fichier (et répertoire) sous `workspace/` réel."""
    base = root or REAL_WORKSPACE
    out: dict[str, tuple[int, int]] = {}
    if not base.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            rel = os.path.relpath(path, base).replace(os.sep, "/")
            out[rel] = (st.st_size if name in filenames else -1, st.st_mtime_ns if name in filenames else 0)
    return out


def diff(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[str]:
    changes = [f"+ {p}" for p in sorted(set(after) - set(before))]
    changes += [f"- {p}" for p in sorted(set(before) - set(after))]
    changes += [f"~ {p}" for p in sorted(set(before) & set(after)) if before[p] != after[p]]
    return changes
