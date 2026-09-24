"""G1 — la règle la plus structurante (P2) : un AC non mesurable est rejeté."""
from __future__ import annotations

from pathlib import Path

from conftest import make_project, run_main
from sdda_scripts import validate_cap


def test_project_ok_passes_cap_gate(project: Path) -> None:
    code, out = run_main(validate_cap.main, ["--root", str(project), "--no-report"])
    assert code == 0, out
    assert "AC_NOT_EVALUABLE" not in out


def test_prose_ac_is_rejected(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_ac_unmeasurable")
    code, out = run_main(validate_cap.main, ["--root", str(root), "--no-report"])
    assert code == 1
    assert "[AC_NOT_EVALUABLE]" in out
    # Le bloc ERROR fait 3 lignes : ERROR / CAUSE [CLASS] / FIX.
    block = [l for l in out.splitlines() if l.startswith(("ERROR:", "CAUSE:", "FIX:"))]
    assert any(l.startswith("CAUSE: [AC_NOT_EVALUABLE]") for l in block)
    assert any(l.startswith("FIX:") for l in block)


def test_ac_missing_one_field_is_rejected(project: Path) -> None:
    cap = project / "workspace/pipeline/caps/1-1-ClassifyIntent.md"
    text = cap.read_text(encoding="utf-8").replace("  - dataset: workspace/pipeline/datasets/golden/routing-v1.jsonl\n", "")
    cap.write_text(text, encoding="utf-8")
    code, out = run_main(validate_cap.main, ["--root", str(project), "--no-report", str(cap)])
    assert code == 1
    assert "[AC_NOT_EVALUABLE]" in out and "dataset" in out
