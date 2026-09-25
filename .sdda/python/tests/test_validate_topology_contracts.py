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
    topo = project / "workspace/pipeline/topology/1-topology.md"
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


def test_a_note_after_the_path_is_not_part_of_the_path(project: Path) -> None:
    # Vu au deuxième run réel : « `chemin` *(généré par `gen-source-tools`)* »
    # était lu comme un seul chemin, et un contrat présent déclaré introuvable.
    rel = "workspace/pipeline/contracts/tools/1-orders-lookup.tool.md"
    (project / rel).parent.mkdir(parents=True, exist_ok=True)
    (project / rel).write_text("# tool\n", encoding="utf-8")
    _append_row(project, f"| tool | `{rel}` *(généré par `gen-source-tools --scope contracts`)* |")
    code, out = run_main(validate_topology.main, ["--root", str(project), "--mission", "1"])
    assert code == 0, out
    assert "TOPOLOGY_CONTRACT_MISSING" not in out


def test_prose_in_the_file_column_is_refused(project: Path) -> None:
    _append_row(project, "| tool | générés depuis `## Active Data Sources` — périmètre : `orders_lookup` |")
    code, out = run_main(validate_topology.main, ["--root", str(project), "--mission", "1"])
    assert code == 1 and "TOPOLOGY_CONTRACT_MISSING" in out


def test_every_contract_of_a_multi_span_cell_is_checked(project: Path) -> None:
    # Une cellule `Fichier` qui liste DEUX contrats : n'en lire que le premier
    # déclarait le second présent sans l'avoir ouvert.
    present = "workspace/pipeline/contracts/tools/1-orders-lookup.tool.md"
    (project / present).parent.mkdir(parents=True, exist_ok=True)
    (project / present).write_text("# tool\n", encoding="utf-8")
    absent = "workspace/pipeline/contracts/tools/1-orders-count.tool.md"
    _append_row(project, f"| tool | `{present}`, `{absent}` *(générés)* |")
    code, out = run_main(validate_topology.main, ["--root", str(project), "--mission", "1"])
    assert code != 0
    assert "TOPOLOGY_CONTRACT_MISSING" in out and "1-orders-count" in out and "1-orders-lookup" not in out.split("TOPOLOGY_CONTRACT_MISSING", 1)[1].split("\n")[0]
