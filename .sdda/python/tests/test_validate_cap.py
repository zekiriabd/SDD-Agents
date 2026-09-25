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


def test_fields_on_a_grader_other_than_exact_is_rejected(project: Path) -> None:
    """`fields:` ne projette que `exact` : ailleurs, l'AC croirait exclure un champ que l'eval compare."""
    cap = project / "workspace/pipeline/caps/1-1-ClassifyIntent.md"
    text = cap.read_text(encoding="utf-8")
    cap.write_text(text.replace("  - runs: 5\n", "  - runs: 5\n  - fields: intent\n", 1), encoding="utf-8")
    code, out = run_main(validate_cap.main, ["--root", str(project), "--no-report", str(cap)])
    assert code == 0, out
    cap.write_text(text.replace("  - grader: exact\n", "  - grader: regex\n", 1)
                   .replace("  - runs: 5\n", "  - runs: 5\n  - fields: intent\n", 1), encoding="utf-8")
    code, out = run_main(validate_cap.main, ["--root", str(project), "--no-report", str(cap)])
    assert code == 1 and "[AC_NOT_EVALUABLE]" in out and "fields" in out


FREE_TEXT_OUTPUT = ('- output: {"type": "object", "properties": {"intent": {"enum": ["billing", "technical", "other"]}, '
                    '"message": {"type": "string"}}, "required": ["intent", "message"]}')
ENUM_OUTPUT = ('- output: {"type": "object", "properties": {"intent": {"enum": ["billing", "technical", "other"]}}, '
               '"required": ["intent"]}')


def _gate(project: Path) -> tuple[int, str]:
    return run_main(validate_cap.main, ["--root", str(project), "--no-report", "--mission", "1"])


def test_exact_on_an_output_with_required_free_text_is_rejected_at_g1(project: Path) -> None:
    """Premier run réel : `qa-evals` le renvoyait après 17 minutes ; G1 le dit à 0 token.

    Le grader `exact` compare la sortie entière ; un `message` libre requis en
    fait partie, et une réponse parfaite rend 0.0.
    """
    cap = project / "workspace/pipeline/caps/1-1-ClassifyIntent.md"
    text = cap.read_text(encoding="utf-8")
    assert ENUM_OUTPUT in text
    cap.write_text(text.replace(ENUM_OUTPUT, FREE_TEXT_OUTPUT), encoding="utf-8")
    code, out = _gate(project)
    assert code == 1 and "AC-1 : grader `exact` sans `fields:`" in out and "['message']" in out
    # Projeter la comparaison sur le champ mesuré lève le renvoi de l'AC (pas celui de
    # l'objectif chiffré, qui a son propre grader — test suivant).
    cap.write_text(text.replace(ENUM_OUTPUT, FREE_TEXT_OUTPUT)
                   .replace("  - runs: 5\n", "  - runs: 5\n  - fields: intent\n", 1), encoding="utf-8")
    _, out = _gate(project)
    assert "AC-1 : grader `exact` sans `fields:`" not in out, out


def test_the_goal_graded_exact_on_free_text_is_rejected(project: Path) -> None:
    cap = project / "workspace/pipeline/caps/1-1-ClassifyIntent.md"
    text = cap.read_text(encoding="utf-8")
    cap.write_text(text.replace(ENUM_OUTPUT, FREE_TEXT_OUTPUT)
                   .replace("  - runs: 5\n", "  - runs: 5\n  - fields: intent\n", 1), encoding="utf-8")
    code, out = _gate(project)
    assert code == 1 and "Quantified Goal `Grader: exact`" in out


def test_one_dataset_read_with_incompatible_expected_shapes_is_rejected(project: Path) -> None:
    """Premier run réel : `schema` (objet) et `regex` (motif) sur le même fichier."""
    cap = project / "workspace/pipeline/caps/1-2-ExplainInvoiceLine.md"
    text = cap.read_text(encoding="utf-8")
    # `llm-judge` n'impose rien sur `expected` : partager son jeu avec `exact` est légitime.
    assert _gate(project)[0] == 0
    cap.write_text(text.replace("  - grader: llm-judge\n", "  - grader: schema\n", 1), encoding="utf-8")
    code, out = _gate(project)
    assert code == 1 and "billing-v1.jsonl" in out and "incompatibles" in out


def test_ac_missing_one_field_is_rejected(project: Path) -> None:
    cap = project / "workspace/pipeline/caps/1-1-ClassifyIntent.md"
    text = cap.read_text(encoding="utf-8").replace("  - dataset: workspace/pipeline/datasets/golden/routing-v1.jsonl\n", "")
    cap.write_text(text, encoding="utf-8")
    code, out = run_main(validate_cap.main, ["--root", str(project), "--no-report", str(cap)])
    assert code == 1
    assert "[AC_NOT_EVALUABLE]" in out and "dataset" in out
