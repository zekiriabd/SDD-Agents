"""Les contrôles qui se déclaraient actifs et ne s'exécutaient pas.

Un enforcer absent se voit. Un enforcer **muet** ne se voit pas : `INVARIANTS.yml`
continue de le déclarer câblé, la fiche continue de le citer, et on cesse de
chercher ailleurs. C'est la forme la plus coûteuse de dette, et l'audit en a
trouvé cinq d'un coup.

Chaque test ici fixe le mécanisme, pas la prose : il échoue si le contrôle
redevient décoratif.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from conftest import run_main

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdda_hooks"))

import _hook  # noqa: E402
from sdda_hooks import preflight_ownership  # noqa: E402
from sdda_scripts import build_trace, sdda_state  # noqa: E402


# ---------------------------------------------------------------------------
# 1. L'identité du sous-agent dans le payload du harnais
# ---------------------------------------------------------------------------
def test_the_agent_is_read_where_the_harness_puts_it_on_a_spawn() -> None:
    assert _hook.agent_of({"tool_input": {"subagent_type": "dev-agent"}}) == "dev-agent"


def test_the_agent_is_read_at_the_root_for_an_action_inside_a_subagent() -> None:
    """Le cas qui désarmait trois hooks d'ownership.

    Sur un `Write` émis PAR un sous-agent, `tool_input` ne décrit que le fichier ;
    l'auteur arrive à la racine, en `agent_type`. Ne lire que `tool_input`
    rendait `""`, qu'un hook d'ownership interprète comme « fil principal », donc
    AUTORISE. Toutes les écritures de tous les sous-agents passaient.
    """
    payload = {"tool_name": "Write", "agent_type": "dev-agent",
               "tool_input": {"file_path": "workspace/pipeline/datasets/golden/x.jsonl"}}
    assert _hook.agent_of(payload) == "dev-agent"


def test_an_opaque_instance_id_is_not_taken_for_an_agent_name() -> None:
    """La matrice d'ownership est indexée par NOM. Accepter `agent_id` ferait
    chercher `a3f2c1…` dans loader.yml, donc « agent hors matrice », donc
    autoriser : une panne silencieuse de plus."""
    assert _hook.agent_of({"agent_id": "a3f2c1d4e5"}) == ""


def test_a_payload_naming_nobody_is_still_the_main_thread() -> None:
    assert _hook.agent_of({"tool_name": "Write", "tool_input": {"file_path": "x"}}) == ""


def test_a_dev_agent_can_no_longer_write_the_dataset_that_judges_it(project: Path) -> None:
    payload = {"tool_name": "Write", "agent_type": "dev-agent",
               "tool_input": {"file_path": str(project / "workspace/pipeline/datasets/golden/billing-v1.jsonl")}}
    assert preflight_ownership.check(project, payload) == _hook.DENY


def test_a_dev_agent_can_no_longer_rewrite_the_prompt_it_implements(project: Path) -> None:
    payload = {"tool_name": "Write", "agent_type": "dev-agent",
               "tool_input": {"file_path": str(project / "workspace/src/SupportAssistant/prompts/billing-specialist.system.md")}}
    assert preflight_ownership.check(project, payload) == _hook.DENY


def test_a_dev_agent_keeps_writing_in_its_own_zone(project: Path) -> None:
    payload = {"tool_name": "Write", "agent_type": "dev-agent",
               "tool_input": {"file_path": str(project / "workspace/src/agents/billing-specialist/agent.py")}}
    assert preflight_ownership.check(project, payload) == _hook.ALLOW


def test_the_baseline_is_refused_to_everyone_including_the_main_thread(project: Path) -> None:
    """Le seul contrôle d'ownership qui ne dépend d'aucune identification :
    `promote_baseline` écrit en E/S Python, jamais par l'outil Write."""
    payload = {"tool_name": "Write",
               "tool_input": {"file_path": str(project / "workspace/pipeline/baselines/1-system.json")}}
    assert _hook.agent_of(payload) == ""
    assert preflight_ownership.check(project, payload) == _hook.DENY


# ---------------------------------------------------------------------------
# 2. Le matcher de délégation
# ---------------------------------------------------------------------------
def test_every_spawn_hook_matches_both_names_of_the_delegation_tool() -> None:
    """`Task` dans Claude Code, `Agent` dans l'Agent SDK. Un matcher qui n'en
    nomme qu'un ne se plaint pas — il ne se déclenche jamais."""
    hooks_dir = Path(_hook.__file__).resolve().parent
    spawn_matchers = set()
    for path in sorted(hooks_dir.glob("preflight_*.py")):
        text = path.read_text(encoding="utf-8")
        if '"matcher": "Task' in text:
            spawn_matchers.add(path.stem)
            assert '"matcher": "Task|Agent"' in text, f"{path.name} ne nomme qu'un des deux outils"
    assert len(spawn_matchers) >= 9


# ---------------------------------------------------------------------------
# 3. Le tier, appliqué et non seulement déclaré
# ---------------------------------------------------------------------------
def test_the_generated_facades_carry_the_model_key_the_harness_reads() -> None:
    """Sans `model:`, les 22 agents héritaient du modèle du fil parent : le
    mécanisme de tiers existait, était borné, testé — et ne changeait aucun
    appel. La seule trace de l'écart était la facture."""
    from sdda_lib import markdown_io

    root = Path(_hook.__file__).resolve().parents[3]
    facades = sorted((root / ".claude" / "agents").glob("*.md"))
    sources = sorted((root / ".sdda" / "agents").glob("*.md"))
    # Une façade par fiche source, ni plus ni moins : le compte suit le roster,
    # il ne se fige pas dans un test (22 hier, 23 avec dev-backend).
    assert [f.name for f in facades] == [s.name for s in sources]
    expected = {"deep": "opus", "balanced": "sonnet", "fast": "haiku"}
    for path in facades:
        meta = markdown_io.parse_header_fields(path.read_text(encoding="utf-8"))
        front = path.read_text(encoding="utf-8").split("---")[1]
        values = dict(line.split(": ", 1) for line in front.strip().splitlines() if ": " in line)
        tier = values.get("model_tier", "").strip()
        assert values.get("model", "").strip() == expected[tier], f"{path.name} : tier {tier}"


def test_the_tier_to_model_table_is_declared_once_in_the_capability_matrix() -> None:
    from sdda_admin import harness_build

    matrix = harness_build.load_matrix()
    assert matrix["claude-code"].model_for("deep") == "opus"
    assert matrix["claude-code"].model_for("balanced") == "sonnet"
    # Un harnais sans table n'invente pas de modèle : l'héritage joue.
    assert matrix["codex"].model_for("deep") == ""


# ---------------------------------------------------------------------------
# 4. Le plafond de coût, alimenté
# ---------------------------------------------------------------------------
def _open_run(root: Path) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        sdda_state.main(["new-run", "--root", str(root), "--mission", "1", "--command", "/sdda-build"])
    return buf.getvalue().strip().splitlines()[-1]


def test_a_build_span_feeds_the_run_cumulative_cost(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`add_cost` existait, `preflight_cost_cap` lisait `costUsd`, et aucune
    commande n'appelait la primitive : le hook trouvait toujours 0,00 $ et
    autorisait toujours."""
    run_id = _open_run(project)
    monkeypatch.setenv("SDDA_RUN_ID", run_id)

    for amount in ("1.50", "2.25"):
        code, _ = run_main(build_trace.main,
                           ["agent", "--root", str(project), "--agent", "dev-agent",
                            "--item", "billing", "--tier", "deep", "--cost-usd", amount, "--json"])
        assert code == 0

    run = sdda_state.load_run(project, run_id)
    assert run["costUsd"] == pytest.approx(3.75)


def test_the_cost_cap_now_sees_what_the_run_spent(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from sdda_hooks import preflight_cost_cap

    run_id = _open_run(project)
    monkeypatch.setenv("SDDA_RUN_ID", run_id)
    run_main(build_trace.main, ["agent", "--root", str(project), "--agent", "dev-agent",
                                "--cost-usd", "999999", "--json"])
    payload = {"tool_input": {"subagent_type": "dev-agent"}, "runId": run_id}
    assert preflight_cost_cap.check(project, payload) == _hook.DENY


# ---------------------------------------------------------------------------
# 5. La boucle de correction, bornée
# ---------------------------------------------------------------------------
def test_the_build_loop_stops_at_its_declared_iteration_ceiling(project: Path) -> None:
    run_id = _open_run(project)
    args = ["should-retry-item", "--root", str(project), "--run-id", run_id,
            "--phase", "build_agents", "--item", "billing", "--max-iter", "3"]
    for _ in range(3):
        assert run_main(sdda_state.main, args)[0] == 0
        with redirect_stdout(io.StringIO()):
            sdda_state.main(["set-item", "--root", str(project), "--run-id", run_id,
                             "--phase", "build_agents", "--item", "billing", "--status", "fail"])

    code, out = run_main(sdda_state.main, args)
    assert code == 1 and "BUILD_LOOP_EXHAUSTED" in out


def test_the_build_loop_also_stops_on_its_own_budget(project: Path) -> None:
    run_id = _open_run(project)
    sdda_state.add_cost(project, run_id, usd=20.0, label="dev-agent")
    code, out = run_main(sdda_state.main,
                         ["should-retry-item", "--root", str(project), "--run-id", run_id,
                          "--phase", "build_agents", "--item", "billing",
                          "--max-iter", "99", "--max-cost-usd", "15"])
    assert code == 1 and "BUILD_LOOP_BUDGET_EXHAUSTED" in out


def test_each_item_carries_its_own_counter(project: Path) -> None:
    """Trois `dev-agent` verts ne sont pas repayés parce que le quatrième boucle."""
    run_id = _open_run(project)
    for _ in range(3):
        with redirect_stdout(io.StringIO()):
            sdda_state.main(["set-item", "--root", str(project), "--run-id", run_id,
                             "--phase", "build_agents", "--item", "billing", "--status", "fail"])
    run = sdda_state.load_run(project, run_id)
    assert sdda_state.item_attempts(run, "build_agents", "billing") == 3
    assert sdda_state.item_attempts(run, "build_agents", "routing") == 0
    allowed, _, _ = sdda_state.should_retry_item(run, "build_agents", "routing", max_iter=3, max_cost_usd=0)
    assert allowed


# ---------------------------------------------------------------------------
# 6. Le scanner qui ferme la porte pour de bon
# ---------------------------------------------------------------------------
def test_no_prompt_cites_an_option_that_does_not_exist() -> None:
    from sdda_admin import command_flags

    findings = command_flags.scan()
    assert findings == [], json.dumps(findings, ensure_ascii=False, indent=2)


def test_the_scanner_follows_line_continuations() -> None:
    """Placée en second, l'alternative de continuation ne matchait jamais : le
    scanner ne lisait que la première ligne de chaque appel et se déclarait vert
    en n'ayant vu qu'un tiers des options."""
    from sdda_admin import command_flags

    text = ("```bash\npython .sdda/sdda.py eval-runner --mission 1 \\\n"
            "  --level L9 --totally-made-up value\n```\n")
    matches = list(command_flags.INVOCATION_RE.finditer(text))
    assert matches and "--totally-made-up" in matches[0].group(2)


def test_a_template_inside_a_quoted_value_is_not_read_as_an_option() -> None:
    from sdda_admin import command_flags

    tail = ' --agent po-elicitor --fact "brief={--from-brief path | aucun}" --prompt-only'
    flags = command_flags.FLAG_RE.findall(command_flags.QUOTED_RE.sub(" ", tail))
    assert "--from-brief" not in flags
    assert "--agent" in flags and "--prompt-only" in flags
