"""validate_datasets — disjonction par hash d'input, tailles, schéma des items."""
from __future__ import annotations

import json
from pathlib import Path

from conftest import make_project, run_main
from sdda_lib import paths
from sdda_lib.layered_config import read_layered_config
from sdda_scripts import validate_datasets


def _run(root: Path, *extra: str) -> tuple[int, str]:
    return run_main(validate_datasets.main, ["--root", str(root), *extra])


def test_project_ok_datasets_are_valid(project: Path) -> None:
    code, out = _run(project, "--json")
    assert code == 0, out
    data = json.loads(out)
    assert data["data"]["holdoutOverlaps"] == 0
    assert set(data["data"]["itemCounts"]) >= {"workspace/pipeline/datasets/golden/billing-v1.jsonl", "workspace/pipeline/datasets/holdout/mission-1-v1.jsonl"}
    rep = json.loads((paths.validation_dir(project) / "G8-1-SupportAssistant.datasets.json").read_text(encoding="utf-8"))
    assert rep["ok"] and rep["part"] == "datasets"
    assert "dataset:workspace/pipeline/datasets/holdout/mission-1-v1.jsonl" in rep["pinnedHashes"]


def test_golden_holdout_overlap_by_input_hash_is_rejected(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_holdout_overlap")
    code, out = _run(root, "--no-report")
    assert code == 1
    assert "[HOLDOUT_NOT_DISJOINT]" in out
    # L'item a un id différent et des clés dans un autre ordre : c'est le HASH de l'input qui parle.
    assert "billing-line-002~holdout-billing-003" in out


def test_overlap_in_warn_mode_is_a_warning_and_audited(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_holdout_overlap")
    stack = root / "workspace/stack/STACK.md"
    stack.write_text(stack.read_text(encoding="utf-8").replace("AdversarialSetMinItems: 2\n", "AdversarialSetMinItems: 2\nHoldoutDisjointCheck: warn\n"), encoding="utf-8")
    report = validate_datasets.validate_datasets(root, config=read_layered_config(root), write_report=False)
    assert report.ok
    assert "HOLDOUT_NOT_DISJOINT" in {w.cls for w in report.warnings}
    assert "HOLDOUT_NOT_DISJOINT" in (paths.audit_dir(root) / "bypasses.jsonl").read_text(encoding="utf-8")


def test_dataset_below_minimum_size_is_rejected(project: Path) -> None:
    stack = project / "workspace/stack/STACK.md"
    stack.write_text(stack.read_text(encoding="utf-8").replace("GoldenSetMinItems: 5", "GoldenSetMinItems: 50"), encoding="utf-8")
    code, out = _run(project, "--no-report")
    assert code == 1
    assert "[EVAL_DATASET_TOO_SMALL]" in out and "GoldenSetMinItems=50" in out


def test_invalid_item_is_rejected(project: Path) -> None:
    ds = project / "workspace/pipeline/datasets/golden/routing-v1.jsonl"
    with ds.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "BAD ID", "input": {"question": "x"}}\n')
        fh.write("ceci n'est pas du JSON\n")
    code, out = _run(project, "--no-report")
    assert code == 1
    assert "[DATASET_ITEM_INVALID]" in out and "BAD ID" in out and "JSON illisible" in out


def test_duplicate_id_is_rejected(project: Path) -> None:
    ds = project / "workspace/pipeline/datasets/golden/routing-v1.jsonl"
    with ds.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "routing-billing-001", "input": {"question": "Une toute autre question"}, "expected": {"intent": "billing"}, "metadata": {"source": "synthetic", "difficulty": "easy", "class": "billing"}}\n')
    code, out = _run(project, "--no-report")
    assert code == 1 and "[DATASET_DUPLICATE_ID]" in out


def test_ac_pointing_at_missing_dataset_is_rejected(project: Path) -> None:
    (project / "workspace/pipeline/datasets/golden/routing-v1.jsonl").unlink()
    code, out = _run(project, "--no-report")
    assert code == 1 and "[EVAL_DATASET_MISSING]" in out and "routing-v1" in out


def test_ac_iterating_on_holdout_is_rejected(project: Path) -> None:
    cap = project / "workspace/pipeline/caps/1-1-ClassifyIntent.md"
    cap.write_text(cap.read_text(encoding="utf-8").replace("workspace/pipeline/datasets/golden/routing-v1.jsonl", "workspace/pipeline/datasets/holdout/mission-1-v1.jsonl"), encoding="utf-8")
    code, out = _run(project, "--no-report")
    assert code == 1 and "[AC_DATASET_IS_HOLDOUT]" in out


def _golden_item(iid: str, question: str, **metadata) -> str:
    item = {"id": iid, "input": {"question": question}, "expected": {"intent": "billing"},
            "metadata": {"source": "synthetic", "difficulty": "easy", "class": "billing", **metadata}}
    return json.dumps(item, ensure_ascii=False) + "\n"


def test_a_secret_in_an_item_is_a_leak(project: Path) -> None:
    """Contrôle 4 de `/sdda-eval` : un golden set est COMMITÉ."""
    ds = project / "workspace/pipeline/datasets/golden/routing-v1.jsonl"
    with ds.open("a", encoding="utf-8") as fh:
        fh.write(_golden_item("routing-leak-001", "ma clé est sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4"))
    code, out = _run(project, "--no-report")
    assert code == 1 and "[SECRET_LEAK]" in out and "routing-v1.jsonl" in out
    assert "a1b2c3d4" not in out          # la valeur n'est jamais recopiée dans le rapport


def test_undeclared_pii_in_an_item_is_refused_and_declared_pii_is_not(project: Path) -> None:
    ds = project / "workspace/pipeline/datasets/golden/routing-v1.jsonl"
    with ds.open("a", encoding="utf-8") as fh:
        fh.write(_golden_item("routing-pii-001", "écrire à jean.dupont@entreprise-reelle.fr"))
        fh.write(_golden_item("routing-pii-002", "écrire à marie.durand@entreprise-reelle.fr", pii_status="present-authorized"))
    code, out = _run(project, "--no-report", "--json")
    assert code == 1
    data = json.loads(out)
    pii = [e for e in data["errors"] if e["class"] == "PII_IN_DATASET"]
    assert len(pii) == 1 and "routing-pii-001" in pii[0]["message"] and "routing-pii-002" not in pii[0]["message"]
    assert data["data"]["content"] == {"secrets": 0, "pii": 1, "piiAuthorized": 1}


def test_raw_trace_policy_makes_pii_a_warning_not_a_block(project: Path) -> None:
    """Même lecture que `scan_pii` : `TracePIIPolicy: raw` (ADR exigé) informe, ne bloque pas."""
    stack = project / "workspace/stack/STACK.md"
    text = stack.read_text(encoding="utf-8")
    heading = "## Active Observability\n"
    text = (text.replace(heading, heading + "TracePIIPolicy: raw\n", 1) if heading in text
            else text + "\n" + heading + "TracePIIPolicy: raw\n")
    stack.write_text(text, encoding="utf-8")
    ds = project / "workspace/pipeline/datasets/golden/routing-v1.jsonl"
    with ds.open("a", encoding="utf-8") as fh:
        fh.write(_golden_item("routing-pii-003", "écrire à jean.dupont@entreprise-reelle.fr"))
    report = validate_datasets.validate_datasets(project, config=read_layered_config(project), write_report=False)
    assert "PII_IN_DATASET" in {w.cls for w in report.warnings}
    assert "PII_IN_DATASET" not in {e.cls for e in report.errors}
