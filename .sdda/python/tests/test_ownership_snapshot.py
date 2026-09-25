"""Audit d'ownership APRÈS une phase : instantané, puis écart réel sur le disque.

`/sdda-build` appelait `audit-ownership --phase 4` sans `--agent`/`--wrote` :
seule la cohérence de loader.yml était vérifiée, aucune écriture réelle, et la
« révocation depuis le hash précédent » n'avait aucun hash où puiser.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_scripts import audit_ownership as ao

APP = "workspace/src/SupportDesk"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = make_project(tmp_path)
    for rel, text in {
        f"{APP}/prompts/billing.system.md": "# prompt\n",
        f"{APP}/agents/billing/agent.py": "x = 1\n",
        "workspace/pipeline/datasets/golden/billing-v1.jsonl": "{}\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def put(root: Path, rel: str, text: str = "y\n") -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def audit(root: Path, *args: str) -> tuple[int, str]:
    return run_main(ao.main, ["--root", str(root), "--mission", "1", "--no-report", *args])


def test_snapshot_then_clean_wave_is_green(project: Path) -> None:
    code, _ = audit(project, "snapshot", "--phase", "4")
    assert code == 0
    put(project, f"{APP}/agents/billing/agent.py", "x = 2\n")
    put(project, f"{APP}/agents/triage/agent.py")
    put(project, f"{APP}/agents/billing/__pycache__/agent.cpython-311.pyc")   # bruit d'outillage
    code, out = audit(project, "--phase", "4", "--since-snapshot", "--instances", "billing,triage")
    assert code == 0, out


def test_an_out_of_zone_write_is_caught_classified_and_revoked(project: Path) -> None:
    audit(project, "snapshot", "--phase", "4")
    put(project, f"{APP}/prompts/billing.system.md", "# réécrit par dev-agent\n")
    put(project, "workspace/pipeline/datasets/golden/extra.jsonl")
    (project / f"{APP}/agents/billing/agent.py").unlink()       # dans sa zone : pas une faute
    code, out = audit(project, "--phase", "4", "--since-snapshot", "--instances", "billing")
    assert code != 0
    assert "PROMPT_OWNERSHIP_VIOLATION" in out and "DATASET_OWNERSHIP_VIOLATION" in out

    code, out = audit(project, "--phase", "4", "--since-snapshot", "--instances", "billing", "--restore")
    assert (project / f"{APP}/prompts/billing.system.md").read_text(encoding="utf-8") == "# prompt\n"
    assert not (project / "workspace/pipeline/datasets/golden/extra.jsonl").exists()
    # Après révocation, l'écart restant est propre.
    code, out = audit(project, "--phase", "4", "--since-snapshot", "--instances", "billing")
    assert code == 0, out


def test_an_undeclared_instance_directory_is_an_escape(project: Path) -> None:
    audit(project, "snapshot", "--phase", "4")
    put(project, f"{APP}/agents/refund/agent.py")
    code, out = audit(project, "--phase", "4", "--since-snapshot", "--instances", "billing,triage")
    assert code != 0 and "OWNERSHIP_INSTANCE_ESCAPE" in out


def test_frozen_zones_must_not_move(project: Path) -> None:
    put(project, f"{APP}/shared/types.py", "T = 1\n")
    audit(project, "snapshot", "--phase", "5")
    put(project, f"{APP}/shared/types.py", "T = 2\n")
    code, out = audit(project, "--phase", "5", "--since-snapshot", "--frozen", "workspace/src/**/shared/**")
    assert code != 0 and "OWNERSHIP_FROZEN_ZONE_CHANGED" in out


def test_a_missing_snapshot_is_an_error_not_a_green(project: Path) -> None:
    code, out = audit(project, "--phase", "3", "--since-snapshot")
    assert code != 0 and "OWNERSHIP_SNAPSHOT_MISSING" in out


def test_the_phase_table_names_the_writers_of_each_parallel_wave() -> None:
    assert set(ao.PHASE_AGENTS["3"]) == {"dev-tools", "dev-retrieval", "dev-data"}
    assert ao.PHASE_AGENTS["4"] == ("dev-agent",)
    loader = ao.load_loader(Path(__file__).resolve().parents[3])
    for agents in ao.PHASE_AGENTS.values():
        for agent in agents:
            assert isinstance(loader.get(agent), dict), agent


def test_a_snapshot_that_cannot_replace_the_previous_one_fails_with_a_class(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sous Windows, un fichier tenu ouvert survit à `rmtree(ignore_errors=True)` et `os.replace` levait nu :
    la phase s'ouvrait sans instantané, et `--since-snapshot` n'avait plus rien pour juger."""
    import shutil

    code, _ = audit(project, "snapshot", "--phase", "4")
    assert code == 0
    target = ao.snapshot_dir(project, "1", "4")
    real_rmtree = shutil.rmtree

    def stubborn_rmtree(path, *args, **kwargs):
        if Path(path) == target:
            return None              # le répertoire « tenu ouvert » ne part pas
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", stubborn_rmtree)
    code, out = audit(project, "snapshot", "--phase", "4")
    assert code != 0
    assert "[OWNERSHIP_SNAPSHOT_FAILED]" in out
    assert not target.with_name(target.name + ".tmp").exists()     # rien d'à moitié posé
    assert (target / "manifest.json").is_file()                    # l'ancien instantané est intact
