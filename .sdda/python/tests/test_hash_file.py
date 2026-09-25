"""hash-file — le hash que les gates épinglent, rendu par un script et non recopié par un agent."""
from __future__ import annotations

import json
from pathlib import Path

from conftest import run_main
from sdda_lib import hashing, paths
from sdda_scripts import hash_file, resolve_cap_hash_sentinel, validate_mission
from sdda_cli import discover


def _run(*argv: str) -> tuple[int, str]:
    return run_main(hash_file.main, list(argv))


def test_the_subcommand_is_discovered_by_the_launcher() -> None:
    assert discover()["hash-file"] == ("sdda_scripts", "hash_file")


def test_spec_short_matches_what_validate_cap_compares(project: Path) -> None:
    mission = sorted(paths.missions_dir(project).glob("1-*.md"))[0]
    code, out = _run("--root", str(project), "--file", str(mission), "--spec", "--short")
    assert code == 0, out
    printed = out.strip().splitlines()[-1]
    spec = validate_mission.load_mission(project, mission.stem)
    # Même longueur que le sentinel résolu et que le gabarit (`{mission-hash-8}`),
    # même préfixe que le hash complet : c'est ce que `hashes_match` compare.
    assert printed == hashing.short(spec.hash)
    assert hashing.hashes_match(printed, spec.hash)
    assert printed == resolve_cap_hash_sentinel.mission_hash(project, 1)[0]


def test_a_glob_that_resolves_to_one_file_is_accepted(project: Path) -> None:
    code, out = _run("--root", str(project), "--file", "workspace/pipeline/missions/1-*.md", "--spec")
    assert code == 0, out
    mission = sorted(paths.missions_dir(project).glob("1-*.md"))[0]
    assert out.strip().splitlines()[-1] == hashing.sha256_spec_file(mission)


def test_without_spec_the_raw_hash_differs_and_the_status_header_is_pointed_out(project: Path) -> None:
    mission = sorted(paths.missions_dir(project).glob("1-*.md"))[0]
    code, out = _run("--root", str(project), "--file", str(mission))
    assert code == 0
    assert out.strip().splitlines()[-1] == hashing.sha256_file(mission)
    assert "--spec" in out   # le rappel sur stderr : le hash brut périme au premier `Status:` réécrit


def test_index_manifest_is_written_and_stable(tmp_path: Path) -> None:
    index = tmp_path / "project" / "workspace" / "src" / "App" / "retrieval" / "contracts-index"
    index.mkdir(parents=True)
    (index / "chunks.jsonl").write_text('{"id": 1}\n', encoding="utf-8")
    (index / "vectors.bin").write_bytes(b"\x00\x01\x02")
    (index / "index.manifest.json").write_text(json.dumps({"embeddingModel": "voyage-3", "chunk": {"size": 400}}), encoding="utf-8")
    root = tmp_path / "project"
    (root / "workspace").mkdir(exist_ok=True)

    code, out = _run("--root", str(root), "--index", str(index), "--manifest")
    assert code == 0, out
    digest = out.strip().splitlines()[-1]
    assert hashing.is_hash_ref(digest) and len(hashing.hex_of(digest)) == 64
    manifest = json.loads((index / "index.manifest.json").read_text(encoding="utf-8"))
    assert manifest["indexHash"] == digest
    assert manifest["embeddingModel"] == "voyage-3" and manifest["chunk"] == {"size": 400}   # conservé
    assert [f["path"] for f in manifest["files"]] == ["chunks.jsonl", "vectors.bin"]        # le manifeste n'entre pas dans son propre hash
    assert manifest["generatedAt"].endswith("Z")

    # Idempotent : recalculé sur le même index, le même hash — le manifeste écrit n'a rien changé.
    code, out2 = _run("--root", str(root), "--index", str(index))
    assert code == 0 and out2.strip().splitlines()[-1] == digest


def test_an_ambiguous_glob_or_a_missing_file_is_refused(project: Path) -> None:
    caps = paths.caps_dir(project)
    code, out = _run("--root", str(project), "--file", str(caps / "1-*.md"))
    assert code == 1 and "[INVALID_ARG]" in out
    code, out = _run("--root", str(project), "--file", "workspace/nulle-part.md")
    assert code == 1 and "[INVALID_ARG]" in out
