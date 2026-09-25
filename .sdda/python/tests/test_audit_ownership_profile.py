"""Sous `Profile: poc`, l'audit juge `dev-app` — pas les `dev-*` de couche.

Au premier run poc réel, `audit-ownership --phase 3 --since-snapshot` a rendu
`[OWNERSHIP_VIOLATION]` sur `serving/tests/test_cli_smoke.py`, écrit par
`dev-app` dans sa zone : la table des phases ne connaissait que le chemin
standard.
"""
from __future__ import annotations

from pathlib import Path

from conftest import make_project  # type: ignore
from sdda_lib.errors import Report
from sdda_scripts import audit_ownership


def _set_profile(root: Path, profile: str) -> None:
    stack = root / "workspace/stack/STACK.md"
    text = stack.read_text(encoding="utf-8")
    text = text.replace("## Project Config\n", f"## Project Config\nProfile: {profile}\n", 1)
    stack.write_text(text, encoding="utf-8")


def test_poc_phase_3_is_judged_against_dev_app(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    _set_profile(root, "poc")
    report = Report(name="T", target=str(root))
    assert audit_ownership.phase_agents(root, "3", report) == ("dev-app",)
    assert audit_ownership.phase_agents(root, "5", report) == ("dev-app",)
    # Les phases que le poc ne remplace pas gardent leurs agents.
    assert audit_ownership.phase_agents(root, "4.1", report) == ("dev-prompt",)


def test_standard_phase_3_keeps_the_layer_agents(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    report = Report(name="T", target=str(root))
    assert audit_ownership.phase_agents(root, "3", report) == ("dev-tools", "dev-retrieval", "dev-data")
