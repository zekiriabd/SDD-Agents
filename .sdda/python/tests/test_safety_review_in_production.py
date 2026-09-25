"""`AgentSafetyRequiredInProduction` a un émetteur : G7, sur un run de production.

La clé et sa classe `[SAFETY_REVIEW_DISABLED_IN_PRODUCTION]` vivaient dans
config.base.yml sans qu'aucun script ne les porte. `/sdda-review` refuse en
prose, mais une prose n'est pas un enforcer : c'est `validate_safety_gate.py`,
qui applique déjà les seuils de la revue, qui dit désormais le refus — et exige
le rapport de `review-safety` que le mode `off` prétendait dispenser.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sdda_scripts import validate_safety_gate as vsg
from test_validate_safety_gate import green_part, patch_config, reviewer_report, run


@pytest.fixture
def parts_green(project: Path) -> Path:
    green_part(project, "suites")
    green_part(project, "adversarial")
    reviewer_report(project, "review-orchestration", ["Aucun finding."], name="orchestration-1.md")
    return project


@pytest.fixture(autouse=True)
def _no_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SDDA_ENV", raising=False)


def test_production_run_reads_sdda_env_and_ci() -> None:
    assert vsg.production_run({"SDDA_ENV": "production"})
    assert vsg.production_run({"SDDA_ENV": "ci"})
    assert vsg.production_run({"CI": "true"})
    assert not vsg.production_run({"SDDA_ENV": "dev"})
    assert not vsg.production_run({})


def test_safety_review_off_in_production_is_the_promised_class(parts_green: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDDA_ENV", "production")
    patch_config(parts_green, "AgentSafetyMode: off")
    classes = [f.cls for f in run(parts_green).errors]
    assert vsg.CLS_REVIEW_DISABLED_IN_PRODUCTION in classes
    # La revue est exigée : son rapport aussi — `off` ne dispense plus de rien.
    assert vsg.CLS_REVIEW_REPORT_MISSING in classes


def test_ci_true_counts_as_production(parts_green: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    patch_config(parts_green, "AgentSafetyMode: off")
    assert vsg.CLS_REVIEW_DISABLED_IN_PRODUCTION in [f.cls for f in run(parts_green).errors]


def test_outside_production_off_still_dispenses_the_report(parts_green: Path) -> None:
    patch_config(parts_green, "AgentSafetyMode: off")
    report = run(parts_green)
    assert report.ok, report.render_text()


def test_the_key_set_to_false_lifts_the_refusal(parts_green: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDDA_ENV", "production")
    patch_config(parts_green, "AgentSafetyMode: off\nAgentSafetyRequiredInProduction: false")
    report = run(parts_green)
    assert vsg.CLS_REVIEW_DISABLED_IN_PRODUCTION not in [f.cls for f in report.errors]
    assert report.ok, report.render_text()


def test_a_safety_review_that_ran_in_production_is_untouched(parts_green: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDDA_ENV", "production")
    reviewer_report(parts_green, "review-safety", ["Aucun finding."], name="agent-safety-1.md")
    report = run(parts_green)
    assert report.ok, report.render_text()
