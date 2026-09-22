"""RETRIEVAL GATE (G4) : ce que le script doit refuser de rendre vert.

Les cas vérifiés ici sont ceux où un script naïf mentirait : pas d'exécuteur,
groundedness absente, requêtes manquantes du replay, et un recall sous le seuil
du contrat — celui-là doit dire « RETRIEVAL », pas « l'agent hallucine ».
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main  # type: ignore
from sdda_lib import paths
from sdda_scripts import ir_compiler, run_retrieval_eval
from sdda_scripts.run_retrieval_eval import ReplayExecutor

GOLDEN = "workspace/datasets/golden/contracts-index-v1.jsonl"


def _golden_items(n: int = 6) -> list[dict]:
    """n requêtes, chacune attendant le document `doc-{i}` et un voisin utile."""
    return [
        {
            "id": f"retr-contract-{i:03d}",
            "input": {"question": f"question {i}"},
            "expected_documents": [
                {"doc_id": f"doc-{i}", "relevance": 3},
                {"doc_id": f"doc-{i}-bis", "relevance": 1},
            ],
            "metadata": {"source": "real-anonymized", "difficulty": "easy", "class": "contract"},
        }
        for i in range(1, n + 1)
    ]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


@pytest.fixture
def project_with_golden(tmp_path: Path) -> Path:
    root = make_project(tmp_path)
    _write_jsonl(root / GOLDEN, _golden_items())
    ir_compiler.main(["--root", str(root), "--mission", "1", "--no-report"])
    return root


def _ir(root: Path) -> dict:
    return ir_compiler.load_ir(paths.ir_path(root, 1))


def _replay(root: Path, rows: list[dict]) -> ReplayExecutor:
    path = root / "workspace/evals/runs/retrieval.jsonl"
    _write_jsonl(path, rows)
    return ReplayExecutor.from_file(path)


def _perfect(n: int = 6, *, groundedness: float | None = 0.9) -> list[dict]:
    rows = []
    for i in range(1, n + 1):
        row = {
            "id": f"retr-contract-{i:03d}",
            "docIds": [f"doc-{i}", f"doc-{i}-bis"],
            "servedIds": [f"doc-{i}", f"doc-{i}-bis"],
            "answer": f"réponse [doc-{i}]",
        }
        if groundedness is not None:
            row["groundedness"] = groundedness
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Refus de mesurer
# ---------------------------------------------------------------------------
def test_without_executor_the_script_refuses_rather_than_assumes(project: Path) -> None:
    code, out = run_main(run_retrieval_eval.main, ["--root", str(project), "--mission", "1"])
    assert code == 1 and "RETRIEVAL_EXECUTOR_MISSING" in out


def test_no_retriever_in_ir_is_not_a_passed_gate(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    ir_compiler.main(["--root", str(root), "--mission", "1", "--no-report"])
    ir = _ir(root)
    ir["retrievers"] = []  # système sans RAG : la gate est sans objet, pas franchie
    report, payload = run_retrieval_eval.run(root, ir, ReplayExecutor({}), write_report=False)
    assert payload["applicable"] is False and report.ok
    assert not any(paths.validation_dir(root).glob("G4-*.json")) if paths.validation_dir(root).is_dir() else True


# ---------------------------------------------------------------------------
# Mesure
# ---------------------------------------------------------------------------
def test_perfect_retrieval_is_green_and_pins_index_and_dataset(project_with_golden: Path) -> None:
    root = project_with_golden
    report, payload = run_retrieval_eval.run(root, _ir(root), _replay(root, _perfect()), run_id="R1")
    assert payload["verdict"] == "green", report.render_text()
    measured = payload["retrievers"][0]
    assert measured["recallAtK"] == 1.0 and measured["queries"] == 6
    assert measured["groundedness"] == 0.9 and "OK" in measured["diagnosis"]

    gate = json.loads((paths.validation_dir(root) / "G4-1-contracts-index.json").read_text(encoding="utf-8"))
    assert gate["ok"] is True
    assert gate["pinnedHashes"]["index:1-contracts-index"] == "sha256:0123456789abcdef"
    assert f"dataset:{GOLDEN}" in gate["pinnedHashes"] and "contract:1-contracts-index" in gate["pinnedHashes"]
    assert (root / payload["written"]["report"]).is_file()


def test_low_recall_is_red_and_names_the_retrieval_layer(project_with_golden: Path) -> None:
    """Le diagnostic doit interdire de partir sur le prompt : c'est tout l'objet de P5."""
    root = project_with_golden
    rows = _perfect()
    for row in rows[:5]:
        row["docIds"] = ["doc-hors-sujet"]
        row["servedIds"] = ["doc-hors-sujet"]
    report, payload = run_retrieval_eval.run(root, _ir(root), _replay(root, rows), write_report=False)
    assert payload["verdict"] == "red"
    assert report.has("RETRIEVAL_BELOW_THRESHOLD")
    fix = next(f.fix for f in report.errors if "recallAtK" in f.message)
    assert "Ne pas toucher au prompt" in fix
    assert "RETRIEVAL" in payload["retrievers"][0]["diagnosis"]


def test_groundedness_unmeasured_is_yellow_never_green(project_with_golden: Path) -> None:
    root = project_with_golden
    report, payload = run_retrieval_eval.run(root, _ir(root), _replay(root, _perfect(groundedness=None)), write_report=False)
    assert payload["verdict"] == "yellow" and report.ok  # informe (P9), ne bloque pas
    assert payload["retrievers"][0]["groundedness"] is None
    assert report.has("RETRIEVAL_GATE_FAILED")
    assert "non mesurée" in payload["retrievers"][0]["diagnosis"]


def test_groundedness_below_threshold_is_red_and_names_the_generation_layer(project_with_golden: Path) -> None:
    root = project_with_golden
    report, payload = run_retrieval_eval.run(root, _ir(root), _replay(root, _perfect(groundedness=0.40)), write_report=False)
    assert payload["verdict"] == "red"
    assert "GÉNÉRATION" in payload["retrievers"][0]["diagnosis"]


def test_missing_replay_records_are_excluded_not_counted_as_zero(project_with_golden: Path) -> None:
    root = project_with_golden
    executor = _replay(root, _perfect(3))
    report, payload = run_retrieval_eval.run(root, _ir(root), executor, write_report=False)
    measured = payload["retrievers"][0]
    assert measured["queries"] == 3 and measured["recallAtK"] == 1.0  # pas 0.5
    assert report.has("MEASUREMENT_MISSING") and len(executor.missing) == 3


def test_dangling_citation_is_reported(project_with_golden: Path) -> None:
    root = project_with_golden
    rows = _perfect()
    rows[0]["answer"] = "réponse [doc-fantome]"
    report, payload = run_retrieval_eval.run(root, _ir(root), _replay(root, rows), write_report=False)
    assert payload["verdict"] == "red" and report.has("CITATION_UNRESOLVED")
    assert payload["retrievers"][0]["danglingCitations"] == ["doc-fantome"]


def test_small_golden_warns_about_the_confidence_interval(project_with_golden: Path) -> None:
    report, _ = run_retrieval_eval.run(project_with_golden, _ir(project_with_golden), _replay(project_with_golden, _perfect()), write_report=False)
    assert report.has("EVAL_DATASET_TOO_SMALL")  # 6 requêtes < RetrievalGoldenMinQueries


# ---------------------------------------------------------------------------
# Bypass
# ---------------------------------------------------------------------------
def test_bypass_downgrades_errors_but_leaves_the_verdict_red_and_audits(project_with_golden: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project_with_golden
    monkeypatch.setenv("SDDA_BYPASS_RETRIEVAL_GATE", "1")
    monkeypatch.setenv("SDDA_BYPASS_REASON", "index en cours de réindexation")
    rows = _perfect()
    for row in rows:
        row["docIds"] = ["doc-hors-sujet"]
    report, payload = run_retrieval_eval.run(root, _ir(root), _replay(root, rows), run_id="R2")
    assert payload["verdict"] == "red" and payload["bypassed"] is True
    assert report.ok and report.has("RETRIEVAL_BELOW_THRESHOLD")  # dégradé en avertissement
    audit = (paths.audit_dir(root) / "bypasses.jsonl").read_text(encoding="utf-8")
    assert "G4" in audit and "réindexation" in audit


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_cli_json_carries_the_payload(project_with_golden: Path) -> None:
    root = project_with_golden
    _write_jsonl(root / "workspace/evals/runs/retrieval.jsonl", _perfect())
    code, out = run_main(run_retrieval_eval.main, [
        "--root", str(root), "--mission", "1", "--replay", "workspace/evals/runs/retrieval.jsonl",
        "--retriever", "1-contracts-index", "--json", "--no-report",
    ])
    payload = json.loads(out)
    assert code == 0 and payload["verdict"] == "green" and payload["executor"] == "replay"
