"""Zones protégées : les reviewers y écrivent leurs rapports, personne n'y forge une gate.

La règle était « personne n'écrit sous `.sys/.validation/` par Write/Edit »,
jouée AVANT l'identité — or `loader.yml` y déclare les rapports des six
reviewers. La phase 7 ne pouvait rien écrire. Et le shell, lui, laissait passer
une redirection `>` que l'éditeur refusait.
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from conftest import make_project
from sdda_hooks import _hook, preflight_bash_ownership, preflight_ownership
from sdda_scripts import audit_ownership as ao

ALLOW, DENY = _hook.ALLOW, _hook.DENY

REVIEWER_REPORTS = {
    "review-spec": "workspace/.sys/.validation/reports/spec-compliance-1.md",
    "review-safety": "workspace/.sys/.validation/reports/agent-safety-1.md",
    "review-cost": "workspace/.sys/.validation/reports/cost-latency-1.md",
    "review-orchestration": "workspace/.sys/.validation/reports/orchestration-1.md",
    "review-rag": "workspace/.sys/.validation/reports/rag-quality-1.md",
    "review-adversarial": "workspace/.sys/.validation/reports/adversarial-1.md",
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path)


def write(project: Path, agent: str | None, rel: str) -> tuple[int, str]:
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(project / rel), "content": "x"}}
    if agent:
        payload["agent_type"] = agent
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = preflight_ownership.check(project, payload)
    return code, buf.getvalue()


def bash(project: Path, agent: str | None, command: str) -> tuple[int, str]:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent:
        payload["agent_type"] = agent
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = preflight_bash_ownership.check(project, payload)
    return code, buf.getvalue()


@pytest.mark.parametrize("agent,report", sorted(REVIEWER_REPORTS.items()))
def test_each_reviewer_writes_its_own_report(project: Path, agent: str, report: str) -> None:
    code, err = write(project, agent, report)
    assert code == ALLOW, err
    code, err = bash(project, agent, f"echo '# verdict' > {report}")
    assert code == ALLOW, err


def test_adversarial_findings_are_writable_by_their_owner_only(project: Path) -> None:
    findings = "workspace/.sys/.validation/adversarial-findings/1.jsonl"
    assert write(project, "review-adversarial", findings)[0] == ALLOW
    code, err = write(project, "review-safety", findings)
    assert code == DENY and "GATE_REPORT_FORGERY" in err


@pytest.mark.parametrize("agent", sorted(REVIEWER_REPORTS))
def test_no_reviewer_forges_a_gate_report(project: Path, agent: str) -> None:
    for rel in ("workspace/.sys/.validation/G7-1.json", "workspace/.sys/.validation/1-G5-agent.json"):
        code, err = write(project, agent, rel)
        assert code == DENY and "GATE_REPORT_FORGERY" in err, (agent, rel)
        code, err = bash(project, agent, f"echo '{{\"ok\": true}}' > {rel}")
        assert code == DENY and "GATE_REPORT_FORGERY" in err, (agent, rel)


def test_a_reviewer_does_not_write_another_reviewers_report(project: Path) -> None:
    code, err = write(project, "review-cost", REVIEWER_REPORTS["review-safety"])
    assert code == DENY and "GATE_REPORT_FORGERY" in err


def test_the_main_thread_stays_refused_in_protected_zones_by_the_editor(project: Path) -> None:
    for rel in (REVIEWER_REPORTS["review-spec"], "workspace/.sys/.validation/G3-1.json",
                "workspace/pipeline/baselines/1.json", "workspace/.sys/.audit/bypasses.jsonl"):
        code, err = write(project, None, rel)
        assert code == DENY, rel


def test_a_builder_is_refused_in_the_validation_zone_by_the_shell(project: Path) -> None:
    code, err = bash(project, "dev-agent", "echo '{\"ok\": true}' > workspace/.sys/.validation/G3-1.json")
    assert code == DENY and "GATE_REPORT_FORGERY" in err
    code, err = bash(project, "dev-tools", "cp x.json workspace/pipeline/baselines/1.json")
    assert code == DENY and "BASELINE_OWNERSHIP_VIOLATION" in err


def test_the_main_thread_shell_keeps_the_documented_gate_redirections(project: Path) -> None:
    """Les commandes déposent les rapports par `script --json > .sys/.validation/…` :
    le fil principal y garde le shell. La baseline et l'audit, eux, jamais."""
    assert bash(project, None, "python .sdda/sdda.py eval-runner --json > workspace/.sys/.validation/1-G5-agent.json")[0] == ALLOW
    code, err = bash(project, None, "echo '{}' > workspace/pipeline/baselines/1.json")
    assert code == DENY and "BASELINE_OWNERSHIP_VIOLATION" in err
    code, err = bash(project, None, "rm workspace/.sys/.audit/bypasses.jsonl")
    assert code == DENY and "GATE_REPORT_FORGERY" in err


def test_a_broad_write_pattern_does_not_open_a_protected_zone() -> None:
    """Un motif large (`workspace/**`) ne vaut pas déclaration dans la zone."""
    loader = {"x": {"writes": ["workspace/**"]},
              "r": {"writes": ["workspace/.sys/.validation/reports/r-{n}.md"]}}
    assert ao.protected_write(loader, "x", "workspace/.sys/.validation/reports/r-1.md") is not None
    assert ao.protected_write(loader, "r", "workspace/.sys/.validation/reports/r-1.md") is None
    assert ao.protected_write(loader, "r", "workspace/.sys/.validation/reports/r-1.json") is not None
    assert ao.protected_write(loader, "", "workspace/.sys/.validation/reports/r-1.md") is not None
    assert ao.protected_write(loader, "r", "workspace/pipeline/missions/1-X.md") is None  # hors zone : pas l'affaire
