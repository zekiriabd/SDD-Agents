"""La pré-passe `shared/` + interface `memory/` est ORDONNANCÉE avant les agents, puis gelée.

`dev-agent` (phase 4) implémentait ses `memoryScopes` contre une mémoire que
`dev-orchestration` n'écrivait qu'en phase 5, et la pré-passe `shared/**`
annoncée par la matrice n'était ordonnancée par aucune commande.
"""
from __future__ import annotations

import re
from pathlib import Path

from conftest import make_project, run_main
from sdda_scripts import audit_ownership as ao

SDDA = Path(__file__).resolve().parents[2]
BUILD = (SDDA / "commands" / "sdda-build.md").read_text(encoding="utf-8")


def test_the_prepass_runs_before_prompts_and_agents() -> None:
    prepass = BUILD.index("### 4.0 — `dev-orchestration --prepass`")
    assert prepass < BUILD.index("### 4.1 — `dev-prompt`") < BUILD.index("### 4.2 — `dev-agent`")
    section = BUILD[prepass:BUILD.index("### 4.1")]
    assert "SDDA-PREPASS" in section and "la phase 4 dépend de cette sortie" in section
    assert "--phase 4.0 --since-snapshot" in section and "orchestration/**" in section


def test_phase_4_and_5_audits_freeze_what_the_prepass_laid_down() -> None:
    phase4 = BUILD[BUILD.index("### 4.2"):BUILD.index("### 4.3")]
    assert "--frozen 'workspace/src/**/shared/**'" in phase4 and "--frozen 'workspace/src/**/memory/**'" in phase4
    phase5 = BUILD[BUILD.index("### 5.1"):BUILD.index("### 5.2 ")]
    assert "--frozen 'workspace/src/**/memory/interface.*'" in phase5


def test_dev_agent_reads_the_frozen_interfaces_and_cannot_write_them() -> None:
    loader = ao.load_loader(SDDA.parent)
    reads = [str(r) for r in loader["dev-agent"]["reads"]]
    assert "workspace/src/*/shared/**" in reads and "workspace/src/*/memory/interface.*" in reads
    for path in ("workspace/src/SupportDesk/shared/types.py", "workspace/src/SupportDesk/memory/interface.py"):
        assert not ao.check_write(loader, "dev-agent", path, ao.Report(name="t", target="."))
        assert ao.check_write(loader, "dev-orchestration", path, ao.Report(name="t", target="."))
    assert ao.PHASE_AGENTS["4.0"] == ("dev-orchestration",)


def test_a_prepass_that_touches_orchestration_is_caught(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    base = ["--root", str(root), "--mission", "1", "--no-report"]
    run_main(ao.main, [*base, "snapshot", "--phase", "4.0"])
    app = root / "workspace" / "src" / "SupportDesk"
    for rel in ("shared/types.py", "memory/interface.py", "orchestration/graph.py"):
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        (app / rel).write_text("x\n", encoding="utf-8")
    code, out = run_main(ao.main, [*base, "--phase", "4.0", "--since-snapshot",
                                   "--frozen", "workspace/src/**/orchestration/**"])
    assert code != 0 and "OWNERSHIP_FROZEN_ZONE_CHANGED" in out
    assert re.search(r"orchestration/graph\.py", out)
