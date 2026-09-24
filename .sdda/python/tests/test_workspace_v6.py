"""Workspace v6 : ce que l'humain fournit à la racine, ce que le framework produit sous `pipeline/`.

Trois propriétés tenues par des tests plutôt que par la prose :

1. **La migration range chaque fichier à sa place**, et les références suivent :
   une AC qui pointait `proof/datasets/…` pointe `pipeline/datasets/…`.
2. **Le `.env` de l'humain est dans `assets/`**, copié sans LLM vers
   `src/{App}/.env` par `install-env`, qui ne dit jamais une valeur.
3. **Aucun agent ne lit un fichier de secrets** — la règle n'existait qu'en
   prose ; elle devient nécessaire dès que `.env` vit dans `assets/`, que
   `architect-data` parcourt pour inférer les schémas.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_hooks import _hook, preflight_bash_ownership, preflight_forbidden_reads
from sdda_lib import paths
from sdda_lib import workspace as ws
from sdda_lib.layered_config import app_name
from sdda_scripts import audit_ownership as ao
from sdda_scripts import install_env, migrate_workspace

SECRET = "sk-test-DO-NOT-PRINT-0123456789"


# ---------------------------------------------------------------------------
# 1. La migration v5 -> v6
# ---------------------------------------------------------------------------
@pytest.fixture
def v5_workspace(tmp_path: Path) -> Path:
    root = make_project(tmp_path)
    w = root / "workspace"
    # make_project est déjà en v6 : on le remet dans la forme v5 qu'il avait.
    for sub in ("missions", "caps", "topology", "contracts", "decisions"):
        if (w / "pipeline" / sub).is_dir():
            (w / "feats").mkdir(exist_ok=True)
            (w / "pipeline" / sub).rename(w / "feats" / sub)
    for sub in ("datasets", "suites", "baselines", "calibration"):
        if (w / "pipeline" / sub).is_dir():
            (w / "proof").mkdir(exist_ok=True)
            (w / "pipeline" / sub).rename(w / "proof" / sub)
    (w / "feats/briefs").mkdir(parents=True, exist_ok=True)
    (w / "feats/briefs/1-SupportAssistant.md").write_text("# Brief\n", encoding="utf-8")
    (w / "feats/topology/1-roster.md").write_text("# Roster\n```yaml\nagents: []\n```\n", encoding="utf-8")
    (w / "proof/seed").mkdir(parents=True, exist_ok=True)
    (w / "proof/seed/scenarios.jsonl").write_text('{"id": "SC-1"}\n', encoding="utf-8")
    cap = next((w / "feats/caps").glob("1-1-*.md"))
    text = cap.read_text(encoding="utf-8").replace("workspace/pipeline/datasets/", "workspace/proof/datasets/")
    cap.write_text(text, encoding="utf-8")
    env = paths.env_path(root, app_name(root))
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text(f"LLM_API_KEY={SECRET}\n", encoding="utf-8")
    if (w / "assets/.env").exists():
        (w / "assets/.env").unlink()
    ws.write_workspace_version(root, version=5, written_by="test")
    return root


def test_v6_puts_human_inputs_at_the_root_and_the_rest_under_pipeline(v5_workspace: Path) -> None:
    root = v5_workspace
    code, out = run_main(migrate_workspace.main, ["--root", str(root), "--json"])
    assert code == 0, out
    w = root / "workspace"
    assert (w / "feats/1-SupportAssistant.md").is_file()           # le brief, à plat
    assert (w / "feats/1-roster.md").is_file()                     # le roster, avec ses specs
    assert (w / "seed/scenarios.jsonl").is_file()                  # la vérité terrain, à la racine
    assert list((w / "pipeline/missions").glob("1-*.md"))          # le reste, sous pipeline/
    assert list((w / "pipeline/caps").glob("1-*.md"))
    assert (w / "pipeline/topology/1-topology.md").is_file()
    for gone in ("proof", "feats/briefs", "feats/missions", "feats/caps", "feats/topology", "feats/contracts"):
        assert not (w / gone).exists(), gone
    cap = next((w / "pipeline/caps").glob("1-1-*.md")).read_text(encoding="utf-8")
    assert "workspace/proof/" not in cap and "workspace/pipeline/datasets/" in cap   # les références suivent
    # Le `.env` a désormais sa source humaine dans assets/, et la valeur n'est pas dite.
    assert (w / "assets/.env").read_text(encoding="utf-8") == f"LLM_API_KEY={SECRET}\n"
    assert SECRET not in out
    assert ws.read_workspace_version(root) == ws.WORKSPACE_VERSION == 6


def test_v6_migration_is_idempotent(v5_workspace: Path) -> None:
    run_main(migrate_workspace.main, ["--root", str(v5_workspace)])
    code, out = run_main(migrate_workspace.main, ["--root", str(v5_workspace), "--json"])
    assert code == 0 and json.loads(out)["data"]["applied"] == []


# ---------------------------------------------------------------------------
# 2. install-env : assets/.env -> src/{App}/.env, sans jamais dire une valeur
# ---------------------------------------------------------------------------
def _stack_declares(root: Path, *names: str) -> None:
    stack = paths.stack_md_path(root)
    text = stack.read_text(encoding="utf-8")
    block = "".join(f" - {n}: ${{{n}}}\n" for n in names)
    if "## Active Secrets" in text:
        text = text.replace("## Active Secrets\n", "## Active Secrets\n" + block, 1)
    else:
        text += "\n## Active Secrets\n" + block
    stack.write_text(text, encoding="utf-8")


def test_install_env_copies_and_never_prints_a_value(project: Path) -> None:
    source = paths.env_source_path(project)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(f"LLM_API_KEY={SECRET}\n", encoding="utf-8")
    code, out = run_main(install_env.main, ["--root", str(project)])
    assert code == 0, out
    target = paths.env_path(project, app_name(project))
    assert target.read_bytes() == source.read_bytes()
    assert SECRET not in out and "LLM_API_KEY" in out
    # Relancé : rien à faire.
    code, out = run_main(install_env.main, ["--root", str(project), "--json"])
    assert code == 0 and json.loads(out)["data"]["copied"] is False


def test_a_declared_variable_missing_from_assets_env_is_an_error(project: Path) -> None:
    _stack_declares(project, "LLM_API_KEY", "CRM_TOKEN")
    source = paths.env_source_path(project)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(f"LLM_API_KEY={SECRET}\n", encoding="utf-8")
    code, out = run_main(install_env.main, ["--root", str(project)])
    assert code != 0 and "SECRET_VAR_UNDECLARED" in out and "CRM_TOKEN" in out
    assert SECRET not in out


def test_a_commented_example_store_declares_no_variable(project: Path) -> None:
    """Les stores d'exemple du gabarit (`# auth: {… key_env: CRM_API_KEY }`) ne réclament rien."""
    stack = paths.stack_md_path(project)
    text = stack.read_text(encoding="utf-8")
    example = "#    auth: { mode: api-key, header: X-API-Key, key_env: CRM_API_KEY }\n"
    if "## Active Data Sources" in text:
        text = text.replace("## Active Data Sources\n", "## Active Data Sources\n" + example, 1)
    else:
        text += "\n## Active Data Sources\n" + example
    stack.write_text(text, encoding="utf-8")
    assert "CRM_API_KEY" not in install_env.declared_names(project)


def test_no_assets_env_is_a_warning_until_it_is_required(project: Path) -> None:
    assert not paths.env_source_path(project).exists()
    assert run_main(install_env.main, ["--root", str(project)])[0] == 0
    code, out = run_main(install_env.main, ["--root", str(project), "--require"])
    assert code != 0 and "SECRET_FILE_MISSING" in out


# ---------------------------------------------------------------------------
# 3. Aucun agent ne lit un fichier de secrets
# ---------------------------------------------------------------------------
def _call(module, project: Path, **payload) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = module.check(project, payload)
    return code, buf.getvalue()


@pytest.mark.parametrize("agent", ["architect-data", "dev-backend", "Explore"])
@pytest.mark.parametrize("rel", ["workspace/assets/.env", "workspace/src/SupportAssistant/.env",
                                 "workspace/assets/.env.local"])
def test_no_agent_reads_a_secret_file(project: Path, agent: str, rel: str) -> None:
    target = project / rel
    code, err = _call(preflight_forbidden_reads, project, tool_name="Read",
                      tool_input={"file_path": str(target)}, subagent_type=agent)
    assert code == _hook.DENY and "SECRET_READ_FORBIDDEN" in err
    code, err = _call(preflight_bash_ownership, project, tool_name="Bash",
                      tool_input={"command": f"cat {rel}"}, subagent_type=agent)
    assert code == _hook.DENY and "SECRET_READ_FORBIDDEN" in err


def test_an_env_template_stays_readable_and_the_main_thread_is_never_blocked(project: Path) -> None:
    assert not ao.is_secret_file("workspace/src/App/.env.example")
    assert ao.is_secret_file("workspace/assets/.env")
    code, _ = _call(preflight_forbidden_reads, project, tool_name="Read",
                    tool_input={"file_path": str(project / "workspace/assets/.env")})
    assert code == _hook.ALLOW


def test_assets_data_stays_readable_for_the_data_architect(project: Path) -> None:
    code, _ = _call(preflight_forbidden_reads, project, tool_name="Read",
                    tool_input={"file_path": str(project / "workspace/assets/orders.json")},
                    subagent_type="architect-data")
    assert code == _hook.ALLOW
