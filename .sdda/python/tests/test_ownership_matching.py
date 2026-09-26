"""Le matching de la matrice d'ownership est SEGMENTÉ : `*` = un segment, `**` = n.

Le repli `fnmatch(path, pattern.rstrip("/*") + "/*")` employait un `*` qui
traverse les `/` : `workspace/src/*/*` de `dev-backend`, censé désigner les
fichiers à la racine du projet applicatif, couvrait TOUT `src/`. Ces tests
fixent, agent par agent, le sens voulu de chaque motif de `loader.yml`.
"""
from __future__ import annotations

import pytest

from sdda_lib.errors import Report
from sdda_scripts import audit_ownership as ao

APP = "workspace/src/SupportDesk"


@pytest.fixture(scope="module")
def loader() -> dict:
    from pathlib import Path
    return ao.load_loader(Path(__file__).resolve().parents[3])


def allowed(loader: dict, agent: str, path: str, bindings: dict | None = None) -> bool:
    return ao.check_write(loader, agent, path, Report(name="t", target="."), bindings)


# ---------------------------------------------------------------------------
# Le moteur de motifs
# ---------------------------------------------------------------------------
def test_a_single_star_stays_inside_one_segment() -> None:
    assert ao.matches("workspace/src/*/*", f"{APP}/pyproject.toml")
    assert not ao.matches("workspace/src/*/*", f"{APP}/skills/triage.md")
    assert not ao.matches("workspace/src/*/*", f"{APP}/agents/billing/agent.py")


def test_double_star_spans_zero_or_more_segments() -> None:
    for path in ("workspace/src/tools/x.py", f"{APP}/tools/x.py", "workspace/src/A/src/A/tools/x.py"):
        assert ao.matches("workspace/src/**/tools/**", path), path
    assert not ao.matches("workspace/src/**/tools/**", f"{APP}/tools.py")


def test_a_literal_last_segment_names_a_directory_and_covers_its_content() -> None:
    assert ao.matches("workspace/pipeline/datasets", "workspace/pipeline/datasets/golden/x.jsonl")
    # Un dernier segment à joker ne nomme pas un répertoire.
    assert not ao.matches("workspace/pipeline/missions/{n}-*.md", "workspace/pipeline/missions/1-X.md/y")


def test_placeholders_are_one_segment_unless_bound() -> None:
    pattern = "workspace/src/**/agents/{agent}/**"
    assert ao.matches(pattern, f"{APP}/agents/triage/x.py")
    assert ao.matches(pattern, f"{APP}/agents/billing/x.py", {"agent": "billing"})
    assert not ao.matches(pattern, f"{APP}/agents/triage/x.py", {"agent": "billing"})


def test_normalize_resolves_traversal_and_keeps_leading_dots() -> None:
    assert ao.normalize("./workspace/src/../pipeline/datasets/x") == "workspace/pipeline/datasets/x"
    assert ao.normalize(".sdda\\loader.yml") == ".sdda/loader.yml"
    assert ao.normalize("workspace//src/") == "workspace/src"


def test_case_folding_follows_the_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ao, "CASE_INSENSITIVE", True)
    assert ao.matches("workspace/pipeline/datasets/**", "Workspace/Pipeline/DATASETS/g.jsonl")
    monkeypatch.setattr(ao, "CASE_INSENSITIVE", False)
    assert not ao.matches("workspace/pipeline/datasets/**", "Workspace/Pipeline/DATASETS/g.jsonl")


# ---------------------------------------------------------------------------
# Chaque agent de loader.yml : le sens voulu de ses motifs
# ---------------------------------------------------------------------------
EXPECTED: dict[str, tuple[list[str], list[str]]] = {
    "dev-backend": (
        [f"{APP}/pyproject.toml", f"{APP}/README.md", f"{APP}/app/config.py", f"{APP}/app/domain/rules.py"],
        [f"{APP}/skills/triage.md", f"{APP}/rules/tone.md", f"{APP}/memory/store.py", f"{APP}/shared/types.py",
         f"{APP}/agents/billing/agent.py", f"{APP}/prompts/billing.system.md", f"{APP}/tools/crm.py",
         f"{APP}/orchestration/graph.py", "workspace/pipeline/datasets/golden/x.jsonl"],
    ),
    "dev-tools": (
        [f"{APP}/tools/crm.py", f"{APP}/tools/tests/test_crm.py", "workspace/src/Tools/tools/crm.py"],
        # `workspace/src/tools/crm.py` n'a pas de segment d'application : les
        # couches sont ancrées APRÈS lui, pour qu'une application nommée
        # `Tools`, `Data` ou `App` ne soit pas lue comme la couche du même nom.
        [f"{APP}/agents/billing/agent.py", f"{APP}/prompts/a.system.md", "workspace/pipeline/datasets/g.jsonl",
         f"{APP}/pyproject.toml", "workspace/src/tools/crm.py"],
    ),
    "dev-retrieval": ([f"{APP}/retrieval/index.py"], [f"{APP}/tools/x.py", "workspace/pipeline/baselines/b.json"]),
    "dev-data": ([f"{APP}/data/views.py", f"{APP}/data/tools/query.py"], [f"{APP}/tools/x.py", f"{APP}/prompts/p.md"]),
    "dev-agent": (
        [f"{APP}/agents/billing/agent.py", f"{APP}/agents/billing/tests/test_agent.py"],
        [f"{APP}/prompts/billing.system.md", "workspace/pipeline/datasets/golden/x.jsonl",
         "workspace/pipeline/suites/s.yaml", f"{APP}/orchestration/graph.py", f"{APP}/agents.py"],
    ),
    "dev-orchestration": (
        [f"{APP}/orchestration/graph.py", f"{APP}/memory/store.py", f"{APP}/shared/types.py"],
        [f"{APP}/agents/billing/agent.py", f"{APP}/prompts/p.system.md"],
    ),
    "dev-api": ([f"{APP}/serving/cli.py"], [f"{APP}/app/config.py"]),
    "dev-prompt": (
        [f"{APP}/prompts/billing.system.md", f"{APP}/skills/triage.md", f"{APP}/rules/tone.md",
         "workspace/pipeline/contracts/agents/1-billing.agent.md"],
        [f"{APP}/skills/sub/triage.md", f"{APP}/agents/billing/agent.py", "workspace/pipeline/datasets/g.jsonl"],
    ),
    "qa-tests": (
        [f"{APP}/tests/test_e2e.py", f"{APP}/tools/tests/test_crm.py"],
        [f"{APP}/tools/crm.py", "workspace/pipeline/datasets/g.jsonl"],
    ),
    "qa-evals": (
        ["workspace/pipeline/datasets/golden/x.jsonl", "workspace/pipeline/suites/s.yaml",
         "workspace/pipeline/calibration/k.json"],
        ["workspace/pipeline/baselines/b.json", f"{APP}/tools/x.py"],
    ),
    "architect-tools": (["workspace/pipeline/contracts/tools/1-crm.tool.md"], ["workspace/pipeline/topology/1-topology.md"]),
    "architect-data": (["workspace/pipeline/contracts/tools/1-data-orders.tool.md"], ["workspace/pipeline/contracts/tools/1-crm.tool.md"]),
    "po-capabilities": (["workspace/pipeline/caps/1-2-Billing.md"], ["workspace/pipeline/caps/1-2/Billing.md"]),
    "po-elicitor": (["workspace/pipeline/missions/1-SupportDesk.md"], ["workspace/stack/STACK.md"]),
}


@pytest.mark.parametrize("agent", sorted(EXPECTED))
def test_each_agent_writes_exactly_where_intended(loader: dict, agent: str) -> None:
    ok, ko = EXPECTED[agent]
    for path in ok:
        assert allowed(loader, agent, path), f"{agent} devrait écrire {path}"
    for path in ko:
        assert not allowed(loader, agent, path), f"{agent} ne devrait pas écrire {path}"
