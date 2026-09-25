"""Security-down : le sens de « plus dur » existe pour CHAQUE clé protégée.

`config.base.yml` protégeait `ApiAuthMode` et `ApiContractFirst` ; le schéma ne
les admettait pas dans `security_down_protected`, et `is_downgrade` rangeait
`ApiAuthMode` parmi les modes `full > manual > off` — où aucune de ses valeurs
n'existe. Une équipe qui imposait `api-key` voyait le projet repasser à `none`
sans un mot : la protection était déclarée, et inerte.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project
from sdda_lib import yaml_mini
from sdda_lib.errors import SddaError
from sdda_lib.layered_config import AUTH_RANK, is_downgrade, read_layered_config

SDDA = Path(__file__).resolve().parents[2]
STACK = Path("workspace") / "stack" / "STACK.md"


@pytest.mark.parametrize("team,project,downgrade", [
    ("api-key", "none", True),
    ("oauth2", "api-key", True),
    ("mtls", "none", True),
    ("none", "api-key", False),
    ("api-key", "oauth2", False),
    ("oauth2", "mtls", False),          # les trois mécanismes forts sont équivalents
    ("azure-ad", "oauth2", False),
    ("API-KEY", "none", True),          # casse ignorée
    ("api-key", "inconnu", False),      # une valeur hors domaine est du ressort de validate_config
])
def test_api_auth_mode_has_an_order(team: str, project: str, downgrade: bool) -> None:
    assert is_downgrade("ApiAuthMode", team, project) is downgrade


def test_the_auth_scale_covers_the_schema_enum() -> None:
    schema = json.loads((SDDA / "templates" / "project-config.schema.json").read_text(encoding="utf-8"))
    assert set(schema["properties"]["ApiAuthMode"]["enum"]) == set(AUTH_RANK)


def test_api_contract_first_is_a_hardened_boolean() -> None:
    assert is_downgrade("ApiContractFirst", True, False)
    assert not is_downgrade("ApiContractFirst", False, True)
    assert not is_downgrade("ApiContractFirst", True, True)


def test_both_keys_are_admitted_by_the_schema_and_protected_by_the_base() -> None:
    schema = json.loads((SDDA / "templates" / "project-config.schema.json").read_text(encoding="utf-8"))
    protected = schema["properties"]["security_down_protected"]
    for key in ("ApiAuthMode", "ApiContractFirst"):
        assert key in protected["default"] and key in protected["items"]["enum"]
    base = yaml_mini.parse_mapping((SDDA / "config.base.yml").read_text(encoding="utf-8"))
    assert {"ApiAuthMode", "ApiContractFirst"} <= set(base["security_down_protected"])
    # La base et le schéma miroitent la même liste.
    assert sorted(base["security_down_protected"]) == sorted(protected["default"])


def _team(tmp_path: Path, body: str) -> Path:
    team = tmp_path / "team.yml"
    team.write_text(body, encoding="utf-8")
    return team


def _with_project_config(project: Path, line: str) -> Path:
    stack = project / STACK
    text = stack.read_text(encoding="utf-8")
    assert "AppName: SupportAssistant\n" in text
    stack.write_text(text.replace("AppName: SupportAssistant\n", f"AppName: SupportAssistant\n{line}\n", 1),
                     encoding="utf-8")
    return project


def test_a_project_that_drops_the_caller_identity_the_team_imposed_is_refused(tmp_path: Path) -> None:
    project = _with_project_config(make_project(tmp_path), "ApiAuthMode: none")
    team = _team(tmp_path, "ApiAuthMode: api-key\n")
    with pytest.raises(SddaError) as excinfo:
        read_layered_config(project, team_path=team)
    assert excinfo.value.cls == "CONFIG_SECURITY_DOWNGRADE" and "ApiAuthMode" in str(excinfo.value.detail)


def test_a_project_that_hardens_beyond_the_team_is_accepted(tmp_path: Path) -> None:
    project = _with_project_config(make_project(tmp_path), "ApiAuthMode: mtls")
    team = _team(tmp_path, "ApiAuthMode: api-key\n")
    assert read_layered_config(project, team_path=team).get("ApiAuthMode") == "mtls"


def test_api_contract_first_cannot_be_switched_off_under_the_team(tmp_path: Path) -> None:
    project = _with_project_config(make_project(tmp_path), "ApiContractFirst: false")
    team = _team(tmp_path, "ApiContractFirst: true\n")
    with pytest.raises(SddaError) as excinfo:
        read_layered_config(project, team_path=team)
    assert excinfo.value.cls == "CONFIG_SECURITY_DOWNGRADE" and "ApiContractFirst" in str(excinfo.value.detail)
