"""Antigravity — façade `.agents/` : règles ≤ 24 000 octets, skills, sous-agents, aucun hook.

Sources : antigravity.google/docs (rules, skills, subagents, hooks,
ide/workflows, migration/workflows-to-skills). Il partageait la façade
`.gemini/`, qu'il ne lit pas.
"""
from __future__ import annotations

from sdda_admin import framework_smoke
from sdda_admin import harness_build as hb


def _plan(name: str = "antigravity"):
    matrix = hb.load_matrix()
    plan, _counts = hb.build_harness(name, matrix[name])
    return plan, {p.relative_to(hb.ROOT).as_posix(): c for p, c in plan.files.items()}


def test_it_has_its_own_facade_and_is_built_by_default() -> None:
    assert hb.ADAPTERS["antigravity"].out_dir == ".agents"
    assert "antigravity" in hb.default_targets(hb.load_matrix())
    _plan_obj, files = _plan()
    assert not any(k.startswith(".gemini/") for k in files)


def test_the_architecture_is_split_into_rules_within_the_documented_limit() -> None:
    _plan_obj, files = _plan()
    rules = {k: v for k, v in files.items() if k.startswith(".agents/rules/")}
    assert len(rules) >= 2
    for rel, text in rules.items():
        assert len(text.encode("utf-8")) <= hb.ANTIGRAVITY_RULE_MAX_BYTES, rel
        problems: list[str] = []
        values = framework_smoke._strict_frontmatter(hb.ROOT / rel, text, ("trigger", "description"), problems)
        assert not problems and values["trigger"] in hb.ANTIGRAVITY_RULE_TRIGGERS
    # Rien de l'architecture ne se perd au découpage : chaque titre `##` est quelque part.
    source = hb.memory_source().read_text(encoding="utf-8")
    joined = "\n".join(rules.values())
    for title in [ln for ln in source.splitlines() if ln.startswith("## ")]:
        assert title in joined, title


def test_split_markdown_never_exceeds_the_budget_and_keeps_the_text() -> None:
    text = "# T\n\n" + "".join(f"## S{i}\n\n" + ("mot " * 300 + "\n\n") * 3 for i in range(6))
    chunks = hb.split_markdown(text, 3000)
    assert all(len(c.encode("utf-8")) <= 3000 for c in chunks)
    assert "".join(chunks).split() == text.split()


def test_agents_use_only_documented_frontmatter_values() -> None:
    _plan_obj, files = _plan()
    agents = {k: v for k, v in files.items() if k.startswith(".agents/agents/")}
    assert len(agents) == len(list((hb.SDDA / "agents").glob("*.md")))
    for rel, text in agents.items():
        problems: list[str] = []
        values = framework_smoke._strict_frontmatter(hb.ROOT / rel, text, ("name", "description"), problems)
        assert not problems, problems
        assert values["model"] in hb.ANTIGRAVITY_MODELS and values["subagent"] is True
        assert "tools" not in values  # noms d'outils non publiés : pas de liste devinée


def test_skills_are_byte_identical_to_the_codex_ones() -> None:
    _p1, antigravity = _plan()
    _p2, codex = _plan("codex")
    shared = {k for k in antigravity if k.startswith(".agents/skills/")}
    assert shared and shared == {k for k in codex if k.startswith(".agents/skills/")}
    assert all(antigravity[k] == codex[k] for k in shared)


def test_no_hook_is_wired_and_the_impact_report_says_why() -> None:
    plan, files = _plan()
    assert ".agents/hooks.json" not in files
    assert all(p.status == "absent" and "non documentés" in p.note for p in plan.ported.values())
    assert "appliqué en différé (CI)" in files[".agents/harness-impact.md"]


def test_both_root_pointers_are_harness_neutral() -> None:
    _p1, antigravity = _plan()
    _p2, codex = _plan("codex")
    _p3, gemini = _plan("gemini-cli")
    assert antigravity["AGENTS.md"] == codex["AGENTS.md"]
    assert antigravity["GEMINI.md"] == gemini["GEMINI.md"]
    for text in (antigravity["AGENTS.md"], antigravity["GEMINI.md"]):
        assert ".agents/rules/" in text and len(text.encode("utf-8")) <= hb.ANTIGRAVITY_RULE_MAX_BYTES
