"""`migrate_workspace` : un workspace d'avant la version 1 monte, sans rien perdre.

Le cas réel qui a motivé le script : `workspace/.sys/` portait `.routing`,
`.cache` et `.reverse` — créés par un bootstrap qui recopiait sa propre liste
d'arborescence, lus par aucun script, et `.reverse` n'a même jamais été créé
par le bootstrap. Sans version sur disque, aucun outil ne pouvait dire si ce
workspace était « à jour ».
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import run_main
from sdda_lib import workspace as ws
from sdda_scripts import migrate_workspace, smoke_check

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bootstrap as bs  # noqa: E402

MISSING_DIR = "evals/calibration"     # un répertoire attendu qu'un vieux bootstrap ne créait pas


@pytest.fixture
def v0(tmp_path: Path) -> Path:
    """Un workspace amorcé avant la v1 : tree incomplet, trois fantômes, pas de workspace.json."""
    root = tmp_path / "old"
    for rel in smoke_check.WORKSPACE_TREE:
        if rel != MISSING_DIR:
            (root / "workspace" / rel).mkdir(parents=True)
    for ghost in migrate_workspace.GHOST_DIRS_V1:
        d = root / "workspace" / ghost
        d.mkdir(parents=True)
        (d / ".gitkeep").touch()
    stack = root / "workspace/stack/STACK.md"
    stack.write_text(bs.build_stack_md("Legacy", bs.COMBOS["c1"], {}), encoding="utf-8")
    return root


def _snapshot(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def _ghosts_present(root: Path) -> list[str]:
    return [g for g in migrate_workspace.GHOST_DIRS_V1 if (root / "workspace" / g).exists()]


def test_a_v0_workspace_reaches_v1(v0: Path) -> None:
    code, out = run_main(migrate_workspace.main, ["--root", str(v0)])
    assert code == 0, out
    assert (v0 / "workspace" / MISSING_DIR).is_dir()
    assert _ghosts_present(v0) == []
    assert ws.read_workspace_version(v0) == ws.WORKSPACE_VERSION == 1
    data = json.loads(ws.workspace_json_path(v0).read_text(encoding="utf-8"))
    assert data["createdBy"].startswith("migrate_workspace ") and data["createdAt"].endswith("Z")
    # une ligne par action, dans l'ordre : mkdir, rmdir x3, write
    lines = [l.strip() for l in out.splitlines() if l.strip().split()[0] in ("mkdir", "rmdir", "write")]
    assert [l.split()[0] for l in lines] == ["mkdir", "rmdir", "rmdir", "rmdir", "write"], out
    assert run_main(smoke_check.main, ["--root", str(v0)])[0] == 0


def test_a_ghost_dir_with_content_is_kept_and_reported(v0: Path) -> None:
    keep = v0 / "workspace/.sys/.reverse/notes.md"
    keep.write_text("quelque chose que quelqu'un a mis là", encoding="utf-8")
    code, out = run_main(migrate_workspace.main, ["--root", str(v0), "--json"])
    assert code == 1
    report = json.loads(out)
    assert {e["class"] for e in report["errors"]} == {"WORKSPACE_GHOST_DIR_NOT_EMPTY"}
    assert "notes.md" in report["errors"][0]["message"]
    assert keep.is_file()
    # les deux autres fantômes, vides, sont partis ; le tree a été complété
    assert _ghosts_present(v0) == [".sys/.reverse"]
    assert (v0 / "workspace" / MISSING_DIR).is_dir()
    # la migration n'a pas abouti : la version n'est PAS bumpée, le prochain run la rejoue
    assert ws.read_workspace_version(v0) is None
    assert report["data"]["stoppedAt"] == 1 and report["data"]["applied"] == []


def test_a_second_run_changes_nothing(v0: Path) -> None:
    assert run_main(migrate_workspace.main, ["--root", str(v0)])[0] == 0
    before = _snapshot(v0)
    stamp = ws.workspace_json_path(v0).read_text(encoding="utf-8")
    code, out = run_main(migrate_workspace.main, ["--root", str(v0), "--json"])
    assert code == 0
    assert json.loads(out)["data"]["actions"] == []
    assert _snapshot(v0) == before
    assert ws.workspace_json_path(v0).read_text(encoding="utf-8") == stamp


def test_dry_run_writes_nothing_but_lists_every_action(v0: Path) -> None:
    before = _snapshot(v0)
    code, out = run_main(migrate_workspace.main, ["--root", str(v0), "--dry-run", "--json"])
    assert code == 0, out
    actions = json.loads(out)["data"]["actions"]
    assert {a["op"] for a in actions} == {"mkdir", "rmdir", "write"}
    assert all(a["applied"] is False for a in actions)
    assert _snapshot(v0) == before
    assert ws.read_workspace_version(v0) is None


def test_smoke_flags_a_missing_then_an_outdated_version(v0: Path) -> None:
    (v0 / "workspace" / MISSING_DIR).mkdir()        # le tree seul ne suffit pas
    code, out = run_main(smoke_check.main, ["--root", str(v0), "--json"])
    assert code == 1
    errors = {e["class"]: e for e in json.loads(out)["errors"]}
    assert "WORKSPACE_VERSION_MISSING" in errors
    # Le FIX doit être une commande qu'on TAPE, pas un fichier qu'on ouvre.
    assert "python .sdda/sdda.py migrate-workspace" in errors["WORKSPACE_VERSION_MISSING"]["fix"]

    ws.write_workspace_version(v0, version=0)
    code, out = run_main(smoke_check.main, ["--root", str(v0), "--json"])
    assert code == 1
    errors = {e["class"]: e for e in json.loads(out)["errors"]}
    assert "WORKSPACE_VERSION_OUTDATED" in errors and "WORKSPACE_VERSION_MISSING" not in errors
    assert "python .sdda/sdda.py migrate-workspace" in errors["WORKSPACE_VERSION_OUTDATED"]["fix"]


def test_the_migration_chain_is_consecutive_up_to_the_current_version() -> None:
    """Ajouter une version = une fonction + une entrée ; un trou serait un workspace bloqué."""
    assert [m.target for m in migrate_workspace.MIGRATIONS] == list(range(1, ws.WORKSPACE_VERSION + 1))


def test_bootstrap_shares_the_tree_with_smoke_check() -> None:
    """Une seule liste : celle que le smoke vérifie est celle que le bootstrap crée."""
    assert bs.WORKSPACE_TREE is smoke_check.WORKSPACE_TREE
    assert not (set(smoke_check.WORKSPACE_TREE) & set(migrate_workspace.GHOST_DIRS_V1))
