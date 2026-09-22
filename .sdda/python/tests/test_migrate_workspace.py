"""`migrate_workspace` : un vieux workspace monte, sans rien perdre.

Deux cas réels, l'un après l'autre.

v0 -> v1 : `workspace/.sys/` portait `.routing`, `.cache` et `.reverse` — créés
par un bootstrap qui recopiait sa propre liste d'arborescence, lus par aucun
script. Sans version sur disque, aucun outil ne pouvait dire si ce workspace
était « à jour ».

v1 -> v2 : l'arbre portait douze répertoires au même niveau qui mélangeaient
quatre natures sans le dire — ce qu'on spécifie, ce qu'on configure, ce qu'on
produit, ce qui juge. C'est la première migration qui DÉPLACE du contenu, et
donc la première qui peut détruire le travail de quelqu'un. Les tests qui
suivent existent surtout pour cela : vérifier qu'après la montée, chaque
fichier écrit avant est toujours là, et lisible au nouvel endroit.
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

#: L'arborescence telle qu'elle était en v1. Figée ici, en dur, et c'est
#: voulu : une migration se teste contre l'état RÉEL dont elle part, pas contre
#: la liste courante — qui, elle, bougera encore. Dériver ce fixture de
#: `WORKSPACE_TREE` reviendrait à tester la migration contre son propre
#: résultat.
TREE_V1: tuple[str, ...] = (
    "stack", "stack/sources",
    "contracts/dataaccess/schemas",
    "missions", "caps", "topology",
    "contracts/agents", "contracts/tools", "contracts/retrieval", "contracts/memory",
    "prompts",
    "datasets/golden", "datasets/holdout", "datasets/calibration", "datasets/adversarial",
    "evals/suites", "evals/baselines", "evals/reports", "evals/calibration",
    "traces/runs",
    "src", "docs",
    ".sys/.ir", ".sys/.context/adrs", ".sys/.context/packs",
    ".sys/.state", ".sys/.validation", ".sys/.audit",
)

#: Ce qu'un utilisateur avait écrit, et où on doit le retrouver après la montée.
CONTENT_V1: tuple[tuple[str, str], ...] = (
    ("missions/1-Demo.md", "feats/missions/1-Demo.md"),
    ("caps/1-1-Classify.md", "feats/caps/1-1-Classify.md"),
    ("topology/1-topology.md", "feats/topology/1-topology.md"),
    ("contracts/agents/1-router.agent.md", "feats/contracts/agents/1-router.agent.md"),
    ("prompts/router.system.md", "src/prompts/router.system.md"),
    ("datasets/golden/g-v1.jsonl", "proof/datasets/golden/g-v1.jsonl"),
    ("datasets/holdout/mission-1-v1.jsonl", "proof/datasets/holdout/mission-1-v1.jsonl"),
    ("evals/suites/s.yaml", "proof/suites/s.yaml"),
    ("evals/baselines/1-system.json", "proof/baselines/1-system.json"),
    ("evals/calibration/groundedness.json", "proof/calibration/groundedness.json"),
    ("evals/reports/1-run.json", ".sys/reports/1-run.json"),
    ("traces/runs/run-1.jsonl", ".sys/traces/runs/run-1.jsonl"),
    ("docs/adr/ADR-depuis-docs.md", "feats/decisions/ADR-depuis-docs.md"),
    (".sys/.context/adrs/ADR-depuis-sys.md", "feats/decisions/ADR-depuis-sys.md"),
)

MISSING_DIR = "proof/calibration"     # un répertoire attendu qu'un vieux bootstrap ne créait pas


@pytest.fixture
def v0(tmp_path: Path) -> Path:
    """Un workspace amorcé avant la v1 : tree v1 incomplet, trois fantômes, pas de workspace.json."""
    root = tmp_path / "old"
    for rel in TREE_V1:
        if rel != "evals/calibration":
            (root / "workspace" / rel).mkdir(parents=True)
    for ghost in migrate_workspace.GHOST_DIRS_V1:
        d = root / "workspace" / ghost
        d.mkdir(parents=True)
        (d / ".gitkeep").touch()
    stack = root / "workspace/stack/STACK.md"
    stack.write_text(bs.build_stack_md("Legacy", bs.COMBOS["c1"], {}), encoding="utf-8")
    return root


@pytest.fixture
def v1_with_content(tmp_path: Path) -> Path:
    """Un workspace v1 complet ET peuplé : le cas où une migration peut détruire."""
    root = tmp_path / "projet"
    for rel in TREE_V1:
        (root / "workspace" / rel).mkdir(parents=True, exist_ok=True)
    for old, _new in CONTENT_V1:
        target = root / "workspace" / old
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"contenu de {old}", encoding="utf-8")
    (root / "workspace/stack/STACK.md").write_text(
        bs.build_stack_md("Projet", bs.COMBOS["c1"], {}), encoding="utf-8")
    ws.write_workspace_version(root, version=1, written_by="test")
    return root


def _snapshot(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def _ghosts_present(root: Path) -> list[str]:
    return [g for g in migrate_workspace.GHOST_DIRS_V1 if (root / "workspace" / g).exists()]


# ---------------------------------------------------------------------------
# v0 -> courant
# ---------------------------------------------------------------------------
def test_a_v0_workspace_reaches_the_current_version(v0: Path) -> None:
    code, out = run_main(migrate_workspace.main, ["--root", str(v0)])
    assert code == 0, out
    assert (v0 / "workspace" / MISSING_DIR).is_dir()
    assert _ghosts_present(v0) == []
    assert ws.read_workspace_version(v0) == ws.WORKSPACE_VERSION
    data = json.loads(ws.workspace_json_path(v0).read_text(encoding="utf-8"))
    assert data["createdBy"].startswith("migrate_workspace ") and data["createdAt"].endswith("Z")
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
    assert _ghosts_present(v0) == [".sys/.reverse"]
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
    assert {"mkdir", "rmdir", "write"} <= {a["op"] for a in actions}
    assert all(a["applied"] is False for a in actions)
    assert _snapshot(v0) == before
    assert ws.read_workspace_version(v0) is None


# ---------------------------------------------------------------------------
# v1 -> v2 : la première migration qui DÉPLACE
# ---------------------------------------------------------------------------
def test_every_file_of_a_v1_workspace_survives_at_its_new_place(v1_with_content: Path) -> None:
    root = v1_with_content
    code, out = run_main(migrate_workspace.main, ["--root", str(root)])
    assert code == 0, out
    assert ws.read_workspace_version(root) == ws.WORKSPACE_VERSION

    for old, new in CONTENT_V1:
        target = root / "workspace" / new
        assert target.is_file(), f"perdu : {old} -> {new}"
        assert target.read_text(encoding="utf-8") == f"contenu de {old}"
        assert not (root / "workspace" / old).exists(), f"resté en double : {old}"


def test_the_two_adr_locations_are_merged_into_one(v1_with_content: Path) -> None:
    """La question « cet ADR a-t-il été écrit ? » avait deux réponses possibles."""
    root = v1_with_content
    assert run_main(migrate_workspace.main, ["--root", str(root)])[0] == 0
    decisions = sorted(p.name for p in (root / "workspace/feats/decisions").glob("ADR-*.md"))
    assert decisions == ["ADR-depuis-docs.md", "ADR-depuis-sys.md"]
    assert not (root / "workspace/.sys/.context/adrs").exists()
    assert not (root / "workspace/docs/adr").exists()


def test_a_migrated_workspace_passes_the_smoke(v1_with_content: Path) -> None:
    root = v1_with_content
    assert run_main(migrate_workspace.main, ["--root", str(root)])[0] == 0
    code, out = run_main(smoke_check.main, ["--root", str(root)])
    assert code == 0, out


def test_an_empty_legacy_dir_does_not_block_the_migration(tmp_path: Path) -> None:
    """Un sous-répertoire jamais peuplé empêchait son parent d'être retiré, et la
    migration échouait sur un workspace parfaitement sain."""
    root = tmp_path / "vide"
    for rel in TREE_V1:
        (root / "workspace" / rel).mkdir(parents=True, exist_ok=True)
    (root / "workspace/stack/STACK.md").write_text(
        bs.build_stack_md("Vide", bs.COMBOS["c1"], {}), encoding="utf-8")
    ws.write_workspace_version(root, version=1, written_by="test")

    code, out = run_main(migrate_workspace.main, ["--root", str(root)])
    assert code == 0, out
    assert not (root / "workspace/evals").exists()


# ---------------------------------------------------------------------------
# Le registre
# ---------------------------------------------------------------------------
def test_smoke_flags_a_missing_then_an_outdated_version(v0: Path) -> None:
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
    assert not (set(smoke_check.WORKSPACE_TREE) & set(migrate_workspace.GHOST_DIRS_V2))
