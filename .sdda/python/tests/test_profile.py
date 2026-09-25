"""Profil `poc` — une couche de défauts, un aiguillage des commandes, un agent de plus.

Ce que ces tests fixent :

1. **Le profil est une couche, pas une exception.** Ses défauts passent sous
   l'équipe et sous le projet : ce que STACK.md écrit gagne toujours.
2. **Les commandes lisent une seule réponse** (`project-profile`), pas STACK.md.
3. **`dev-app` écrit toute l'application, sauf ce qui juge et ce qui est hashé** :
   prompts, skills, rules, jeux, contexte projet — la frontière ne dépend pas du profil.
"""
from __future__ import annotations

import io
from pathlib import Path

from conftest import run_main
from sdda_lib import markdown_io, paths, yaml_mini
from sdda_lib.errors import Report
from sdda_lib.layered_config import read_layered_config
from sdda_scripts import audit_ownership, project_profile

LOADER = Path(__file__).resolve().parents[2] / "loader.yml"


def _set_profile(project: Path, profile: str, *, drop_minimums: bool = True) -> None:
    stack = paths.stack_md_path(project)
    lines = markdown_io.read_text(stack).split("\n")
    if drop_minimums:
        lines = [l for l in lines if not l.startswith(("GoldenSetMinItems", "HoldoutSetMinItems", "AdversarialSetMinItems"))]
    at = lines.index("AppName: SupportAssistant")
    lines.insert(at + 1, f"Profile: {profile}")
    stack.write_text("\n".join(lines), encoding="utf-8")


def _config(project: Path, tmp_path: Path, team: str | None = None):
    team_path = tmp_path / "team.yml"
    if team is not None:
        team_path.write_text(team, encoding="utf-8")
    return read_layered_config(project, team_path=team_path, warn_stream=io.StringIO())


def test_poc_defaults_apply_when_stack_says_nothing(project: Path, tmp_path: Path) -> None:
    _set_profile(project, "poc")
    config = _config(project, tmp_path)
    assert config.get_int("GoldenSetMinItems", 0) == 15
    assert config.sources["GoldenSetMinItems"] == "profile"
    assert config.layer_paths["profile"].replace("\\", "/").endswith("profiles/poc.yml")


def test_what_the_project_writes_beats_the_profile(project: Path, tmp_path: Path) -> None:
    _set_profile(project, "poc", drop_minimums=False)
    config = _config(project, tmp_path)
    assert config.get_int("GoldenSetMinItems", 0) == 5 and config.sources["GoldenSetMinItems"] == "project"


def test_the_team_beats_the_profile(project: Path, tmp_path: Path) -> None:
    _set_profile(project, "poc")
    config = _config(project, tmp_path, team="GoldenSetMinItems: 40\n")
    assert config.get_int("GoldenSetMinItems", 0) == 40 and config.sources["GoldenSetMinItems"] == "team"


def test_standard_has_no_defaults_file_and_changes_nothing(project: Path, tmp_path: Path) -> None:
    _set_profile(project, "standard")
    config = _config(project, tmp_path)
    assert config.get_int("GoldenSetMinItems", 0) == 50 and config.sources["GoldenSetMinItems"] == "base"


def test_project_profile_prints_the_effective_profile(project: Path) -> None:
    code, out = run_main(project_profile.main, ["--root", str(project)])
    assert code == 0 and out.strip() == "standard"
    _set_profile(project, "poc")
    code, out = run_main(project_profile.main, ["--root", str(project)])
    assert code == 0 and out.strip() == "poc"


def test_dev_app_writes_the_application_but_not_what_judges_or_is_hashed() -> None:
    loader = yaml_mini.parse_mapping(LOADER.read_text(encoding="utf-8"))

    def allowed(path: str) -> bool:
        return audit_ownership.check_write(loader, "dev-app", path, Report(name="t"))

    for ok in ("workspace/src/App/pyproject.toml", "workspace/src/App/app/composition.py",
               "workspace/src/App/agents/order_assistant/agent.py", "workspace/src/App/tools/registry.py",
               "workspace/src/App/data/tools/orders_lookup.py", "workspace/src/App/serving/cli.py",
               "workspace/src/App/orchestration/graph.py", "workspace/src/App/agents/x/tests/test_x.py"):
        assert allowed(ok), ok
    for ko in ("workspace/src/App/prompts/order-assistant.system.md", "workspace/src/App/skills/s.md",
               "workspace/src/App/CLAUDE.md", "workspace/pipeline/datasets/golden/x.jsonl",
               "workspace/pipeline/suites/1-x.yaml"):
        assert not allowed(ko), ko


def test_the_declaration_stays_clean_with_dev_app() -> None:
    """Le partage est déclaré PAR PAIRE : la détection entre dev-* reste entière."""
    loader = yaml_mini.parse_mapping(LOADER.read_text(encoding="utf-8"))
    report = Report(name="t")
    summary = audit_ownership.check_declaration(loader, report)
    assert report.ok, report.render_text()
    assert summary["overlaps"] == []
