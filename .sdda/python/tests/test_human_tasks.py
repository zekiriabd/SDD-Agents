"""human_tasks — la file de ce qu'aucun agent n'a le droit de faire, dérivée du disque.

Ce que ces tests défendent : chaque tâche cite sa raison et son remède ; un
roster absent, à trous ou rouge est une tâche BLOQUANTE de l'architecte (P7) ;
un jaune de G5/G6/G8 est une décision, pas un avertissement ; un ADR exigé par
STACK.md apparaît tant qu'aucun fichier ADR ne nomme la clé ; et le script est
un listing (exit 0, aucun rapport écrit), jamais une gate.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import run_main
from sdda_lib.errors import Report
from sdda_lib.gate_reports import write_gate_report
from sdda_scripts import human_tasks, roster
from test_roster import COMPLETE, drop_markdown_roster, patch, write_manifest

STACK = "workspace/stack/STACK.md"


def tasks(project: Path, *extra: str) -> dict:
    code, out = run_main(human_tasks.main, ["--root", str(project), "--json", *extra])
    assert code == 0, out
    return json.loads(out)


def kinds(payload: dict) -> list[str]:
    return [t["kind"] for t in payload["tasks"]]


def only(payload: dict, kind: str) -> list[dict]:
    return [t for t in payload["tasks"] if t["kind"] == kind]


# ---------------------------------------------------------------------------
# roster — P7
# ---------------------------------------------------------------------------
def test_the_markdown_fallback_is_not_a_task(project: Path) -> None:
    """La fixture déclare son roster dans `## 2. Roster déclaré` : G2 en jugera, rien à demander."""
    assert only(tasks(project), "roster") == []


def test_no_roster_at_all_is_a_blocking_task_of_the_architect(project: Path) -> None:
    drop_markdown_roster(project)
    (found,) = only(tasks(project), "roster")
    assert found["blocking"] is True and found["mission"] == 1
    assert "/sdda-roster 1" in found["how"] and "P7" in found["why"]
    assert found["ref"].endswith("feats/1-roster.md")


def test_a_scaffolded_manifest_asks_to_fill_each_hole(project: Path) -> None:
    drop_markdown_roster(project)
    code, _ = run_main(roster.main, ["scaffold", "--root", str(project), "--mission", "1", "--json"])
    assert code == 0
    (found,) = only(tasks(project), "roster")
    assert found["blocking"] is True and "<à préciser>" in found["title"]
    assert "python .sdda/sdda.py roster validate --mission 1" in found["how"]


def test_a_red_manifest_names_its_first_class(project: Path) -> None:
    drop_markdown_roster(project)
    write_manifest(project, COMPLETE.replace("    agent: billing-specialist\n", "    agent: ghost\n"))
    (found,) = only(tasks(project), "roster")
    assert found["blocking"] is True
    assert "[ARCH_ROSTER_ALLOCATION_INVALID]" in found["why"]


def test_a_complete_manifest_is_no_task(project: Path) -> None:
    drop_markdown_roster(project)
    write_manifest(project)
    assert only(tasks(project), "roster") == []


# ---------------------------------------------------------------------------
# labels — P9
# ---------------------------------------------------------------------------
def test_labels_need_the_compiled_ir(project: Path) -> None:
    assert only(tasks(project), "labels") == []


def test_a_judge_without_human_labels_on_disk_is_a_task(project: Path) -> None:
    from sdda_scripts import ir_compiler

    _, report = ir_compiler.compile_to_file(project, 1)
    assert report.ok, [f.cls for f in report.errors]
    found = only(tasks(project), "labels")
    assert len(found) == 1
    (task,) = found
    assert "groundedness" in task["title"] and task["ref"].endswith("groundedness-v1.jsonl")
    # La fixture DÉCLARE 52 items calibrés sans labels vérifiables : la tâche existe, mais ne bloque pas.
    assert task["blocking"] is False and "pas vérifiables" in task["why"]
    assert "jamais générées par un modèle" in task["how"]


def test_enough_human_labels_on_disk_close_the_task(project: Path) -> None:
    from sdda_scripts import ir_compiler

    ir_compiler.compile_to_file(project, 1)
    labels = project / "workspace/pipeline/datasets/calibration/groundedness-v1.jsonl"
    labels.parent.mkdir(parents=True, exist_ok=True)
    labels.write_text("".join(json.dumps({"id": f"c{i}", "human": 1, "judge": 1}) + "\n" for i in range(50)), encoding="utf-8")
    assert only(tasks(project), "labels") == []


# ---------------------------------------------------------------------------
# adr — les règles des documents du framework
# ---------------------------------------------------------------------------
def test_a_default_stack_requires_no_adr(project: Path) -> None:
    assert only(tasks(project), "adr") == []


def test_a_permissive_memory_pii_policy_requires_an_adr_until_one_names_it(project: Path) -> None:
    patch(project, STACK, "MemoryPIIPolicy: redact-before-write", "MemoryPIIPolicy: allow")
    (found,) = only(tasks(project), "adr")
    assert found["blocking"] is False and "MemoryPIIPolicy" in found["title"]
    assert "pii-not-in-vector-store" in found["why"]

    adr_dir = project / "workspace/pipeline/decisions"
    adr_dir.mkdir(parents=True, exist_ok=True)
    (adr_dir / "ADR-20260922T1000-memory-pii.md").write_text("# ADR\n\nMemoryPIIPolicy: allow — base légale…\n", encoding="utf-8")
    assert only(tasks(project), "adr") == []


def test_an_adr_written_in_docs_adr_also_counts(project: Path) -> None:
    patch(project, STACK, "MemoryPIIPolicy: redact-before-write", "MemoryPIIPolicy: allow")
    adr_dir = project / "workspace/pipeline/decisions"
    adr_dir.mkdir(parents=True, exist_ok=True)
    (adr_dir / "ADR-0001-memory.md").write_text("La politique `MemoryPIIPolicy` passe à allow parce que…\n", encoding="utf-8")
    assert only(tasks(project), "adr") == []


# ---------------------------------------------------------------------------
# findings — les jaunes de G5/G6/G8
# ---------------------------------------------------------------------------
def _yellow(project: Path, gate: str, artifact: str, cls: str = "EVAL_VARIANCE_HIGH") -> None:
    report = Report(name=gate, target=artifact)
    report.warn(cls, "seuil franchi en moyenne, variance 18% > 15%", "k=5 ou corriger", artifact)
    write_gate_report(project, gate, artifact, report, {})


def test_a_yellow_agent_gate_is_a_blocking_decision(project: Path) -> None:
    _yellow(project, "G5", "1-2-ExplainInvoiceLine")
    (found,) = only(tasks(project), "findings")
    assert found["blocking"] is True and "G5" in found["title"] and "1-2-ExplainInvoiceLine" in found["title"]
    assert "[EVAL_VARIANCE_HIGH]" in found["why"] and "--force" in found["how"]


def test_a_yellow_topology_gate_is_not_a_human_task(project: Path) -> None:
    """G2/G3/G4 jaunes sont des avertissements de scripts, pas des verdicts d'eval instables."""
    _yellow(project, "G2", "1-SupportAssistant", "TOPOLOGY_SIMPLICITY_ADVISORY")
    assert only(tasks(project), "findings") == []


def test_a_red_gate_is_blocked_not_a_task(project: Path) -> None:
    report = Report(name="G5", target="1-2-ExplainInvoiceLine")
    report.error("AGENT_EVAL_FAILED", "mean 0.71 < 0.85", "corriger", "1-2-ExplainInvoiceLine")
    write_gate_report(project, "G5", "1-2-ExplainInvoiceLine", report, {})
    assert only(tasks(project), "findings") == []


# ---------------------------------------------------------------------------
# Assemblage : ordre, portée, rendu, aucun effet de bord
# ---------------------------------------------------------------------------
def test_tasks_are_ordered_by_kind_then_mission(project: Path) -> None:
    drop_markdown_roster(project)
    patch(project, STACK, "MemoryPIIPolicy: redact-before-write", "MemoryPIIPolicy: allow")
    _yellow(project, "G5", "1-2-ExplainInvoiceLine")
    payload = tasks(project)
    assert kinds(payload) == ["roster", "adr", "findings"]
    assert payload["counts"] == {"total": 3, "blocking": 2, "byKind": {"roster": 1, "labels": 0, "adr": 1, "findings": 1}}


def test_mission_scope_keeps_project_wide_adr_tasks(project: Path) -> None:
    patch(project, STACK, "MemoryPIIPolicy: redact-before-write", "MemoryPIIPolicy: allow")
    payload = tasks(project, "--mission", "1")
    (found,) = only(payload, "adr")
    assert found["mission"] == 1
    assert kinds(tasks(project, "--mission", "7")) == ["adr"]      # MISSION inconnue : rien d'autre à dériver


def test_text_rendering_flags_blocking_tasks_and_says_none_when_empty(project: Path) -> None:
    code, out = run_main(human_tasks.main, ["--root", str(project)])
    assert code == 0 and out.strip() == "Tâches humaines : aucune."
    drop_markdown_roster(project)
    code, out = run_main(human_tasks.main, ["--root", str(project)])
    assert code == 0
    assert out.splitlines()[0] == "Tâches humaines : 1 (1 bloquante(s))"
    assert "⚠️ [roster  ] MISSION 1 — Déclarer le roster d'agents" in out
    assert "→ /sdda-roster 1" in out


def test_the_listing_writes_no_gate_report(project: Path) -> None:
    drop_markdown_roster(project)
    before = sorted(p.name for p in (project / "workspace/.sys/.validation").glob("*")) if (project / "workspace/.sys/.validation").is_dir() else []
    tasks(project)
    after = sorted(p.name for p in (project / "workspace/.sys/.validation").glob("*")) if (project / "workspace/.sys/.validation").is_dir() else []
    assert before == after
