"""Le budget de contexte a un PLAFOND par tier — calculé, pas déclaré.

`qa-tests` portait 800 Ko : ~200 000 tokens, la fenêtre entière du modèle avant
le premier tour. Le hook ne comparait que deux nombres écrits par le même auteur.
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr
from pathlib import Path

from conftest import make_project
from sdda_hooks import _hook
from sdda_hooks import preflight_agent_budget as hook
from sdda_scripts import context_pack as cp

SDDA = Path(__file__).resolve().parents[2]


def test_the_ceiling_is_window_times_bytes_per_token_minus_the_turn_reserve() -> None:
    assert hook.ceiling_for("deep") == 200_000 * 4 * 60 // 100
    assert hook.ceiling_for("fast") <= hook.ceiling_for("deep")


def test_every_declared_budget_fits_under_its_tier_ceiling() -> None:
    loader = cp.load_loader(SDDA.parent)
    over = {a: (loader[a].get("budget_bytes"), hook.ceiling_for(loader[a].get("model_tier")))
            for a in cp.agent_names(loader)
            if int(loader[a].get("budget_bytes") or 0) > hook.ceiling_for(loader[a].get("model_tier"))}
    assert over == {}


def test_qa_tests_loads_one_layer_not_the_whole_application() -> None:
    reads = [str(r.get("path") if isinstance(r, dict) else r) for r in cp.load_loader(SDDA.parent)["qa-tests"]["reads"]]
    assert "workspace/src/**" not in reads
    assert "workspace/src/*/{object}/**" in reads


def test_a_budget_above_the_ceiling_is_refused_at_spawn(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / ".sdda").mkdir(exist_ok=True)
    loader = (SDDA / "loader.yml").read_text(encoding="utf-8")
    (project / ".sdda" / "loader.yml").write_text(
        loader.replace("  budget_bytes: 420000", "  budget_bytes: 800000", 1), encoding="utf-8")
    buf = io.StringIO()
    with redirect_stderr(buf):
        code = hook.check(project, {"tool_name": "Agent",
                                    "tool_input": {"subagent_type": "qa-tests", "prompt": "SDDA-LAYER: tools\nMISSION 1"}})
    assert code == _hook.DENY and "CONTEXT_BUDGET_CEILING_EXCEEDED" in buf.getvalue()


def test_the_layer_and_the_instance_are_read_from_the_prompt() -> None:
    data = {"tool_input": {"prompt": "SDDA-LAYER: tools\nSDDA-INSTANCE: billing\nMISSION : 2"}}
    assert hook._object_of(data) == "tools"
    assert hook._mission_and_target(data) == ("2", "billing")


def test_a_layer_spawn_measures_only_its_layer(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    app = project / "workspace" / "src" / "SupportDesk"
    (app / "tools").mkdir(parents=True, exist_ok=True)
    (app / "agents" / "billing").mkdir(parents=True, exist_ok=True)
    (app / "tools" / "crm.py").write_text("x" * 1000, encoding="utf-8")
    (app / "agents" / "billing" / "agent.py").write_text("y" * 5000, encoding="utf-8")
    files, _ = cp.expand(project, "workspace/src/*/{object}/**", mission="1", target=None, obj="tools")
    assert {p.name for p in files} == {"crm.py"}
