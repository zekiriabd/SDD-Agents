"""Lot A — le workflow tel que les commandes l'enchaînent, et non tel qu'un script isolé le voit.

Chaque test ici rejoue une COUTURE entre deux étapes du pipeline : ce qu'une
phase écrit, et ce que la suivante en lit. Les scripts étaient verts pris un
par un ; c'est à la jointure que le pipeline retombait.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import make_project, run_main
from sdda_lib import hashing
from sdda_scripts import compute_status, validate_cap


# ---------------------------------------------------------------------------
# A1 — la PHASE 2 ne périme pas G1
# ---------------------------------------------------------------------------
def _g1_verdicts(root: Path) -> tuple[str, str]:
    code, out = run_main(compute_status.main, ["--root", str(root), "--mission", "1", "--json", "--no-write"])
    data = json.loads(out)["data"]["missions"][0]
    cap = next(c for c in data["caps"] if c["id"] == "1-1-ClassifyIntent")
    return data["gates"]["G1"], cap["gates"]["G1"]


def test_cap_spec_hash_ignores_allocated_to_but_not_the_acs() -> None:
    cap = ("# CAP\n\nID: 1-1-X\nStatus: Draft\n\n## Acceptance Criteria\n- AC-1:\n  - threshold: >= 0.9\n\n"
           "## Allocated To\n- agents: <à déterminer>\n\n## Dependencies\n- NONE\n")
    allocated = cap.replace("<à déterminer>", "`intent-classifier`")
    assert hashing.sha256_cap_spec_text(cap) == hashing.sha256_cap_spec_text(allocated)
    # Le hash COMPLET (celui de G2 et de l'IR) voit l'allocation : une
    # réallocation périme bien la topologie.
    assert hashing.sha256_spec_text(cap) != hashing.sha256_spec_text(allocated)
    assert hashing.sha256_cap_spec_text(cap) != hashing.sha256_cap_spec_text(cap.replace(">= 0.9", ">= 0.8"))
    # Les sections qui suivent l'allocation restent dans le hash.
    assert "## Dependencies" in hashing.cap_spec_text(cap)


def test_allocation_written_in_phase_2_keeps_g1_green(tmp_path: Path) -> None:
    """Le scénario réel : G1 verte, architect-topology remplit `## Allocated To`,
    la MISSION retombait à `Draft` et `--resume` repartait en PHASE 1."""
    root = make_project(tmp_path)
    code, out = run_main(validate_cap.main, ["--root", str(root), "--mission", "1"])
    assert code == 0, out
    assert _g1_verdicts(root) == ("green", "green")

    cap = root / "workspace/pipeline/caps/1-1-ClassifyIntent.md"
    text = cap.read_text(encoding="utf-8")
    cap.write_text(text.replace("- tools: aucun", "- tools: `lookup_invoice`"), encoding="utf-8", newline="\n")
    assert _g1_verdicts(root) == ("green", "green")

    cap.write_text(cap.read_text(encoding="utf-8").replace(">= 0.95", ">= 0.90"), encoding="utf-8", newline="\n")
    assert _g1_verdicts(root) == ("stale", "stale")
