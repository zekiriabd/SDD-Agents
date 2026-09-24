"""Fraîcheur de l'IR : la pré-condition de toute génération de code.

Le contrôle vaut surtout par ce qu'il attrape et que `compiledFrom` ignorait
jusqu'ici — l'édition d'un CONTRAT. Un schéma d'outil modifié laissait l'IR se
déclarer frais tout en décrivant autre chose, et le code se générait depuis une
représentation périmée sans qu'aucune gate ne s'en aperçoive.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main  # type: ignore
from sdda_lib import paths
from sdda_scripts import check_ir_freshness, ir_compiler


@pytest.fixture
def compiled(tmp_path: Path) -> Path:
    root = make_project(tmp_path)
    ir_compiler.main(["--root", str(root), "--mission", "1", "--no-report"])
    return root


def _check(root: Path, *extra: str) -> tuple[int, dict]:
    code, out = run_main(check_ir_freshness.main, ["--root", str(root), "--json", *extra])
    return code, json.loads(out)


def _touch(path: Path, suffix: str = "\n<!-- édité -->\n") -> None:
    path.write_text(path.read_text(encoding="utf-8") + suffix, encoding="utf-8")


def test_a_freshly_compiled_ir_is_fresh(compiled: Path) -> None:
    code, payload = _check(compiled, "--mission", "1")
    assert code == 0 and payload["data"]["fresh"] is True


def test_editing_the_mission_makes_the_ir_stale(compiled: Path) -> None:
    _touch(compiled / "workspace/pipeline/missions/1-SupportAssistant.md")
    code, payload = _check(compiled, "--mission", "1")
    assert code == 1 and payload["errors"][0]["class"] == "IR_STALE"
    assert "missionHash" in payload["data"]["missions"][0]["moved"]


def test_editing_a_cap_names_that_cap(compiled: Path) -> None:
    _touch(compiled / "workspace/pipeline/caps/1-2-ExplainInvoiceLine.md")
    _, payload = _check(compiled, "--mission", "1")
    assert payload["data"]["missions"][0]["moved"]["capHashes"] == ["1-2-ExplainInvoiceLine"]


def test_editing_a_tool_contract_makes_the_ir_stale(compiled: Path) -> None:
    """Le trou historique : les contrats n'étaient pas dans `compiledFrom`."""
    _touch(compiled / "workspace/pipeline/contracts/tools/1-invoice-lookup.tool.md")
    code, payload = _check(compiled, "--mission", "1")
    assert code == 1
    assert payload["data"]["missions"][0]["moved"]["contractHashes"] == ["workspace/pipeline/contracts/tools/1-invoice-lookup.tool.md"]


def test_a_new_contract_also_counts_as_a_move(compiled: Path) -> None:
    (compiled / "workspace/pipeline/contracts/tools/1-nouveau.tool.md").write_text("# TOOL CONTRACT: 1-nouveau\n", encoding="utf-8")
    _, payload = _check(compiled, "--mission", "1")
    assert payload["data"]["missions"][0]["moved"]["contractHashes"] == ["workspace/pipeline/contracts/tools/1-nouveau.tool.md (nouveau)"]


def test_a_deleted_contract_is_named_too(compiled: Path) -> None:
    (compiled / "workspace/pipeline/contracts/tools/1-invoice-lookup.tool.md").unlink()
    _, payload = _check(compiled, "--mission", "1")
    assert "(disparu)" in payload["data"]["missions"][0]["moved"]["contractHashes"][0]


def test_editing_the_topology_graph_counts(compiled: Path) -> None:
    """Le graphe est une section de la topologie : l'éditer, c'est éditer la topologie."""
    topo = compiled / "workspace/pipeline/topology/1-topology.md"
    text = topo.read_text(encoding="utf-8")
    assert "  clarify --> finalize\n" in text
    topo.write_text(text.replace("  clarify --> finalize\n", "  clarify --> finalize\n  %% édité\n", 1), encoding="utf-8")
    _, payload = _check(compiled, "--mission", "1")
    assert "topologyHash" in payload["data"]["missions"][0]["moved"]


def test_a_missing_ir_is_an_error_not_a_silent_pass(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    code, payload = _check(root, "--mission", "1")
    assert code == 1 and payload["errors"][0]["class"] == "IR_NOT_FOUND"


def test_without_mission_every_compiled_ir_is_checked(compiled: Path) -> None:
    code, payload = _check(compiled)
    assert code == 0 and [m["mission"] for m in payload["data"]["missions"]] == [1]


def test_no_compiled_ir_at_all_is_an_error(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    code, payload = _check(root)
    assert code == 1 and payload["errors"][0]["class"] == "IR_NOT_FOUND"


def test_the_fix_points_at_recompilation(compiled: Path) -> None:
    _touch(compiled / "workspace/pipeline/contracts/tools/1-invoice-lookup.tool.md")
    _, payload = _check(compiled, "--mission", "1")
    assert "--recompile-only" in payload["errors"][0]["fix"]


def test_recompiling_makes_it_fresh_again(compiled: Path) -> None:
    _touch(compiled / "workspace/pipeline/contracts/tools/1-invoice-lookup.tool.md")
    assert _check(compiled, "--mission", "1")[0] == 1
    ir_compiler.main(["--root", str(compiled), "--mission", "1", "--no-report"])
    assert _check(compiled, "--mission", "1")[0] == 0
    assert paths.ir_path(compiled, 1).is_file()
