"""Brief de spawn : ce qu'un harnais reçoit avant de lancer un agent.

Trois choses comptent ici, et toutes viennent d'une exécution réelle du
pipeline : le périmètre d'écriture ne doit pas arriver avec des placeholders
non résolus (l'agent devinerait), un `tier_floor` ne doit jamais céder, et un
agent privé d'une lecture par l'ownership doit quand même recevoir les faits
dérivés dont sa sortie a besoin — sinon sa fiche est intenable.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main  # type: ignore
from sdda_scripts import spawn_brief


def _json(root: Path, argv: list[str]) -> tuple[int, dict]:
    code, out = run_main(spawn_brief.main, argv + ["--root", str(root), "--json"])
    return code, json.loads(out)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = make_project(tmp_path)
    # Les packs sont construits : sans eux, tout agent qui en lit un est rouge.
    from sdda_scripts import context_pack

    run_main(context_pack.main, ["build", "--agent", "all", "--root", str(root)])
    return root


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------
def test_write_paths_arrive_resolved_not_templated(project: Path) -> None:
    code, brief = _json(project, ["--agent", "po-capabilities", "--mission", "1"])
    assert code == 0
    assert "workspace/caps/1-{m}-*.md" in brief["data"]["ownership"]["writes"]  # {m} = index de CAP, pas une MISSION
    assert "{n}" not in json.dumps(brief["data"]["ownership"])


def test_the_other_placeholder_stays_literal_so_the_rule_keeps_its_meaning(project: Path) -> None:
    """`missions/{other}-*.md` élargi en `*` interdirait à l'agent sa propre MISSION."""
    code, brief = _json(project, ["--agent", "po-capabilities", "--mission", "1"])
    assert code == 0 and "workspace/missions/{other}-*.md" in brief["data"]["ownership"]["forbiddenReads"]
    assert "{m} et {other} désignent" in brief["data"]["prompt"]


def test_an_agent_without_declared_writes_is_flagged(project: Path, tmp_path: Path) -> None:
    loader = project / ".sdda" / "loader.yml"
    loader.parent.mkdir(parents=True, exist_ok=True)
    loader.write_text("sans-perimetre:\n  model_tier: fast\n  budget_bytes: 50000\n  reads: []\n", encoding="utf-8")
    (project / ".sdda" / "agents").mkdir(parents=True, exist_ok=True)
    (project / ".sdda" / "agents" / "sans-perimetre.md").write_text("---\nname: sans-perimetre\n---\n# fiche\n", encoding="utf-8")
    code, brief = _json(project, ["--agent", "sans-perimetre"])
    assert code == 0 and any(w["class"] == "OWNERSHIP_VIOLATION" for w in brief["warnings"])


# ---------------------------------------------------------------------------
# Tier
# ---------------------------------------------------------------------------
def test_the_tier_floor_is_not_negotiable(project: Path) -> None:
    code, brief = _json(project, ["--agent", "architect-topology", "--mission", "1", "--tier", "fast"])
    assert code == 0 and brief["data"]["modelTier"] == "balanced"
    assert any(w["class"] == "CONFIG_SECURITY_DOWNGRADE" for w in brief["warnings"])


def test_the_tier_ceiling_clamps_the_other_way(project: Path) -> None:
    code, brief = _json(project, ["--agent", "po-elicitor", "--mission", "1", "--tier", "deep"])
    assert code == 0 and brief["data"]["modelTier"] == "balanced"
    assert any(w["class"] == "SCOPE_CREEP" for w in brief["warnings"])


def test_the_default_tier_comes_from_agent_bounds(project: Path) -> None:
    code, brief = _json(project, ["--agent", "architect-topology", "--mission", "1"])
    assert code == 0 and brief["data"]["modelTier"] == "deep" and brief["data"]["tierNote"] is None


# ---------------------------------------------------------------------------
# Exécution et faits injectés
# ---------------------------------------------------------------------------
def test_the_interactive_agent_is_declared_inline_not_spawned(project: Path) -> None:
    """Un sous-agent ne parle à personne : ses cinq questions deviendraient cinq hypothèses."""
    code, brief = _json(project, ["--agent", "po-elicitor", "--mission", "1"])
    assert code == 0 and brief["data"]["execution"] == "inline"
    code, brief = _json(project, ["--agent", "po-capabilities", "--mission", "1"])
    assert code == 0 and brief["data"]["execution"] == "spawn"


def test_an_agent_forbidden_from_stack_md_still_receives_the_active_stacks(project: Path) -> None:
    """Sans ce pont, `## Required Stack` reste vide et G0 le refuse — fiche intenable."""
    code, brief = _json(project, ["--agent", "po-elicitor", "--mission", "1"])
    facts = brief["data"]["facts"]
    assert code == 0 and facts["stack.language"] == "python" and facts["stack.rag"] == "hybrid"
    assert "ne les devine pas" in brief["data"]["prompt"]


def test_a_budget_declared_in_the_project_config_is_injected_too(project: Path) -> None:
    """Le budget est une exigence fonctionnelle (P6) : l'agent doit l'avoir sous les yeux."""
    from sdda_lib import paths

    stack = paths.stack_md_path(project)
    stack.write_text(stack.read_text(encoding="utf-8").replace(
        "## Project Config", "## Project Config\n\nCostPerRunTargetUsd: 0.05", 1), encoding="utf-8")
    code, brief = _json(project, ["--agent", "po-elicitor", "--mission", "1"])
    assert code == 0 and brief["data"]["facts"]["config.CostPerRunTargetUsd"] == "0.05"


def test_an_agent_allowed_to_read_stack_md_gets_no_injection(project: Path) -> None:
    code, brief = _json(project, ["--agent", "architect-tools", "--mission", "1"])
    assert code == 0 and brief["data"]["facts"] == {}


def test_extra_facts_are_passed_through(project: Path) -> None:
    code, brief = _json(project, ["--agent", "po-elicitor", "--mission", "1", "--fact", "brief=brief-client.md"])
    assert code == 0 and brief["data"]["facts"]["brief"] == "brief-client.md"


# ---------------------------------------------------------------------------
# Refus
# ---------------------------------------------------------------------------
def test_an_unknown_agent_is_refused(project: Path) -> None:
    code, brief = _json(project, ["--agent", "inconnu"])
    assert code == 1 and brief["errors"][0]["class"] == "CONFIG_UNKNOWN_KEY"


def test_a_missing_pack_makes_the_brief_red(project: Path) -> None:
    from sdda_scripts import context_pack

    context_pack.pack_path(project, "architect-topology").unlink()
    code, brief = _json(project, ["--agent", "architect-topology", "--mission", "1"])
    assert code == 1 and any(e["class"] == "PACK_UNUSABLE" for e in brief["errors"])


def test_the_prompt_names_the_fiche_the_context_and_the_missing_inputs(project: Path) -> None:
    code, out = run_main(spawn_brief.main, ["--agent", "po-capabilities", "--mission", "1",
                                            "--root", str(project), "--prompt-only"])
    assert code == 0
    assert "agents/po-capabilities.md" in out
    assert "workspace/missions/1-SupportAssistant.md" in out
    assert "Contrat de sortie" in out and "Ne jamais spawner un autre agent" in out
