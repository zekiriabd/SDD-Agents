"""§6 « Contrats produits » de la topologie : un FICHIER par ligne, ou « aucun ».

Vu au premier run réel : l'architecte avait écrit de la prose dans la colonne
`Fichier` (« générés depuis ## Active Data Sources … », « aucun (rag/none) — rien
à produire »). La prose est refusée, le marqueur « aucun » est une déclaration.
"""
from __future__ import annotations

from pathlib import Path

from conftest import run_main
from sdda_scripts import validate_topology

SECTION = "## 6. Contrats produits"


def _append_row(project: Path, row: str) -> None:
    topo = project / "workspace/feats/topology/1-topology.md"
    text = topo.read_text(encoding="utf-8")
    head, _, tail = text.partition(SECTION)
    table_end = tail.index("\n\n", tail.index("|---"))
    tail = tail[:table_end] + "\n" + row + tail[table_end:]
    topo.write_text(head + SECTION + tail, encoding="utf-8", newline="\n")


def test_the_reference_topology_passes_the_full_pass(project: Path) -> None:
    code, out = run_main(validate_topology.main, ["--root", str(project), "--mission", "1"])
    assert code == 0, out


def test_a_none_marker_is_a_declaration_not_a_missing_contract(project: Path) -> None:
    _append_row(project, "| retrieval | aucun |")
    _append_row(project, "| data | NONE |")
    code, out = run_main(validate_topology.main, ["--root", str(project), "--mission", "1"])
    assert code == 0, out
    assert "TOPOLOGY_CONTRACT_MISSING" not in out


def test_prose_in_the_file_column_is_refused(project: Path) -> None:
    _append_row(project, "| tool | générés depuis `## Active Data Sources` — périmètre : `orders_lookup` |")
    code, out = run_main(validate_topology.main, ["--root", str(project), "--mission", "1"])
    assert code == 1 and "TOPOLOGY_CONTRACT_MISSING" in out
