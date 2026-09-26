"""Cohérence prompt ↔ code — les constats de l'audit des prompts du 2026-09-25.

Chaque test fixe un MÉCANISME que la prose d'une fiche ou d'une commande doit
porter, parce qu'un script en dépend : une recompilation d'IR sans laquelle un
hook refuse le spawn, une lecture sans laquelle une gate est rouge par
construction, une ligne de commande qu'argparse refuserait. La prose n'est pas
jugée sur son style — seulement sur ce qu'un modèle qui l'exécute ferait.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SDDA = Path(__file__).resolve().parents[2]
AGENTS = SDDA / "agents"
COMMANDS = SDDA / "commands"
RULES = SDDA / "rules"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    j = text.index(end, i + len(start))
    return text[i:j]


# ---------------------------------------------------------------------------
# 1. command-flags parse réellement la ligne
# ---------------------------------------------------------------------------
def _mission_only_parser():
    """Un parser qui n'accepte que `--mission` — la forme de `validate-ir` au
    moment de l'audit, quand la fiche lui passait un positionnel."""
    import argparse

    p = argparse.ArgumentParser(prog="sdda x")
    p.add_argument("--mission")
    p.add_argument("--json", action="store_true")
    return p


def test_a_positional_that_argparse_refuses_is_reported() -> None:
    """`validate-ir workspace/…json` n'avait aucune option inconnue — et pourtant
    argparse le refusait. La passe FLAG seule se déclarait verte dessus."""
    from sdda_admin import command_flags

    problem = command_flags.parse_problem(
        _mission_only_parser(), command_flags.command_tokens(" workspace/.sys/.ir/{n}-system.ir.json"))
    assert problem and "positionnel" in problem


def test_the_correct_form_parses() -> None:
    from sdda_admin import command_flags

    assert command_flags.parse_problem(
        _mission_only_parser(), command_flags.command_tokens(" --mission {n} --json")) is None


def test_a_missing_subcommand_choice_is_reported() -> None:
    from sdda_admin import command_flags

    parser = command_flags._load_parser("sdda_scripts/audit_ownership.py")
    assert parser is not None
    assert command_flags.parse_problem(parser, command_flags.command_tokens(" snapshot --mission {n} --phase 3")) is None
    assert command_flags.parse_problem(parser, command_flags.command_tokens(" snapshots --mission {n}")) is not None


def test_a_script_without_build_parser_is_no_longer_skipped() -> None:
    """Sept scripts construisaient leur parser dans `main()` et échappaient au
    contrôle. Le parser est capturé avant que `main()` ne fasse quoi que ce soit."""
    from sdda_admin import command_flags

    flags = command_flags._parser_flags("sdda_scripts/validate_safety_gate.py")
    assert flags is not None and "--fail-on" in flags and "--mission" in flags
    parser = command_flags._load_parser("sdda_scripts/validate_safety_gate.py")
    # `--mission` y est requis : une ligne qui l'oublie est refusée
    assert command_flags.parse_problem(parser, command_flags.command_tokens(" --json")) is not None


def test_prose_templates_become_plausible_values() -> None:
    from sdda_admin import command_flags

    toks = command_flags.command_tokens(
        ' set-item --status {pass|fail} --inputs-hash "$H" [--mission {n}] '
        "$( [ -n \"$RUNS\" ] && echo --runs \"$RUNS\" ) | tee x  # commentaire")
    assert toks == ["set-item", "--status", "pass", "--inputs-hash", "1", "--mission", "1"]
    # `$(python .sdda/sdda.py project-profile)` : la parenthèse fermante n'est pas un argument
    assert command_flags.command_tokens(")") == []
    # un gabarit à espaces est UNE valeur
    assert command_flags.command_tokens(" --instances {agents de la vague, séparés}") == ["--instances", "1"]


def test_every_cited_command_line_parses() -> None:
    from sdda_admin import command_flags

    assert command_flags.scan() == []


# ---------------------------------------------------------------------------
# 2. L'IR recompilée là où les scripts l'exigent
# ---------------------------------------------------------------------------
def test_the_build_recompiles_the_ir_between_prompts_and_agents() -> None:
    """Sans recompilation, `preflight_agent_bounds` refuse chaque `dev-agent`
    (`[PROMPT_NOT_PINNED]`) : l'IR de la PHASE 2 ne porte aucun `promptHash`."""
    text = _read(COMMANDS / "sdda-build.md")
    between = _section(text, "### 4.1 — `dev-prompt`", "### 4.2 — `dev-agent`")
    assert "python .sdda/sdda.py ir-compiler --mission {n}" in between
    assert "python .sdda/sdda.py validate-ir --mission {n}" in between
    assert "injecté dans l'IR" not in text


def test_the_build_recompiles_the_ir_once_the_datasets_exist() -> None:
    """Le holdout, L9 et les suites L5/L7 ne sont projetés qu'à la compilation."""
    text = _read(COMMANDS / "sdda-build.md")
    junction = _section(text, "### 3.0c", "### 3.1 — Dispatch")
    assert junction.index("validate-datasets --freeze") < junction.index("ir-compiler --mission {n}")


@pytest.mark.parametrize("command,start,runner", [
    ("sdda-build.md", "### 5.4 — ORCH GATE (G6)", "eval-runner"),
    ("sdda-eval.md", "## STEP 7 — Mode `--acceptance`", "eval-runner"),
])
def test_freshness_is_checked_before_measuring(command: str, start: str, runner: str) -> None:
    section = _section(_read(COMMANDS / command), start, "\n## ")
    assert section.index("check-ir-freshness --mission {n}") < section.index(runner)


def test_dev_orchestration_validates_the_ir_with_a_line_that_parses() -> None:
    """Même forme que partout ailleurs (`--ir` / `--mission`), jamais un positionnel nu."""
    text = _read(AGENTS / "dev-orchestration.md")
    assert re.search(r"validate-ir --(ir|mission) ", text)
    assert not re.search(r"validate-ir workspace/", text)


def test_recompile_only_replays_the_pinned_declaration_parts() -> None:
    """`packaging`, `architecture` et `adr` de G2 sont épinglées : une recompilation
    après édition qui ne les rejoue pas laisse G2 périmée."""
    text = _read(COMMANDS / "sdda-topology.md")
    line = next(ln for ln in text.splitlines() if ln.startswith("Si `--recompile-only`"))
    block = text[text.index(line): text.index(line) + 600]
    for script in ("validate-packaging", "validate-architecture", "validate-adr"):
        assert script in block


def test_the_build_no_longer_replays_g3_g4_after_recompiling() -> None:
    section = _section(_read(COMMANDS / "sdda-build.md"), "### 4.1 bis", "### 4.2")
    assert "run-tool-suites" not in section and "run-retrieval-eval" not in section
    assert "estimate-budget --mission {n}" in section


# ---------------------------------------------------------------------------
# 3. G8 : la non-régression est une part, pas une redirection
# ---------------------------------------------------------------------------
def test_acceptance_stops_on_regression_before_reading_the_gate() -> None:
    step = _section(_read(COMMANDS / "sdda-eval.md"), "## STEP 7 — Mode `--acceptance`", "\n## ")
    assert "> workspace/.sys/.validation/regression-" not in step
    assert step.index("check-regression --mission {n}") < step.index("--require-gate G8")
    assert "part **`regression`** de G8" in step


def test_the_full_recap_reads_the_regression_part() -> None:
    text = _read(COMMANDS / "sdda-full.md")
    assert "regression-{n}.json" not in text
    assert ".regression.json" in text


# ---------------------------------------------------------------------------
# 4. qa-tests : les suites qu'il doit exercer, la couche du Domaine
# ---------------------------------------------------------------------------
def test_qa_tests_reads_the_tool_suites_that_g3_matches() -> None:
    from sdda_lib import yaml_mini

    loader = yaml_mini.parse_mapping(_read(SDDA / "loader.yml"))
    assert "workspace/pipeline/suites/tool-{n}-*.yaml" in loader["qa-tests"]["reads"]
    fiche = _read(AGENTS / "qa-tests.md")
    assert "pipeline/suites/tool-{n}-{outil}.yaml" in fiche and "cases[].id" in fiche


def test_the_domain_layer_has_a_tester() -> None:
    """`dev-backend` confie les tests des règles métier à `qa-tests` : la
    couche `app` doit être l'une de celles pour lesquelles il est lancé."""
    assert "`app`" in _section(_read(AGENTS / "qa-tests.md"), "SDDA-LAYER", "Lire tout")
    assert "`app`" in _section(_read(COMMANDS / "sdda-eval.md"), "`qa-tests` part **une fois par couche", "en vagues")


# ---------------------------------------------------------------------------
# 5. Posture des agents de construction
# ---------------------------------------------------------------------------
EXPOSED = ("architect-data", "architect-rag", "qa-evals", "po-elicitor", "review-adversarial",
           "review-safety", "review-orchestration", "review-rag", "review-cost", "review-spec",
           "dev-data", "dev-retrieval")


def test_the_output_protocol_carries_the_build_agent_posture() -> None:
    text = _read(RULES / "output-protocol.md")
    assert "## 8. Posture de l'agent de construction" in text
    assert "jamais une consigne" in text


@pytest.mark.parametrize("agent", EXPOSED)
def test_each_exposed_agent_recalls_the_posture(agent: str) -> None:
    assert "`rules/output-protocol.md` §8" in _read(AGENTS / f"{agent}.md")


# ---------------------------------------------------------------------------
# 6. Ce qui n'est outillé qu'en Python le dit
# ---------------------------------------------------------------------------
def _files() -> list[Path]:
    return sorted(AGENTS.glob("*.md")) + sorted(COMMANDS.glob("*.md"))


def test_every_app_interpreter_run_is_marked_python_only() -> None:
    """`uv run --project … --executor module:attr` importe un objet Python : hors
    Python, la ligne ne peut pas marcher, et la prose doit le dire à côté."""
    unmarked = []
    for path in _files():
        lines = _read(path).splitlines()
        for i, line in enumerate(lines):
            if re.match(r"\s*uv run --project", line):
                context = "\n".join(lines[max(0, i - 2): i + 1])
                if "Python" not in context:
                    unmarked.append(f"{path.name}:{i + 1}")
    assert unmarked == []


def test_the_skeleton_check_is_never_asked_outside_python() -> None:
    """Hors Python, `gen-app-skeleton --check` rend `[STACK_LANGUAGE_MISMATCH]`."""
    unmarked = []
    for path in _files():
        lines = _read(path).splitlines()
        for i, line in enumerate(lines):
            if "gen-app-skeleton --check" in line:
                context = "\n".join(lines[max(0, i - 1): i + 1])
                if "Python" not in context:
                    unmarked.append(f"{path.name}:{i + 1}")
    assert unmarked == []


def test_end_to_end_runners_use_the_language_neutral_executor() -> None:
    """L5/L7/L8/L9 et G4 passent par `--executor cli` : un `module:attr` n'existe
    qu'en Python, et seule la L4 en processus le garde, marquée « (Python) »."""
    offenders = []
    for path in sorted(COMMANDS.glob("*.md")):
        for i, line in enumerate(_read(path).splitlines(), 1):
            if re.search(r"--executor \{module\}:\{(CliExecutor|Executor|Retriever)\}", line):
                offenders.append(f"{path.name}:{i}")
    assert offenders == []


def test_every_skeleton_call_names_its_mission() -> None:
    """Plusieurs MISSIONs → `gen-app-skeleton` sans `--mission` sort `[MISSION_AMBIGUOUS]`."""
    offenders = []
    for path in _files():
        for i, line in enumerate(_read(path).splitlines(), 1):
            if re.search(r"gen-app-skeleton --(check|write)\b", line) and "--mission" not in line:
                offenders.append(f"{path.name}:{i}")
    assert offenders == []


def test_the_composition_accepts_every_isolation_override() -> None:
    text = _read(AGENTS / "dev-backend.md")
    assert "retriever=None" in text and "build_retriever(settings)" in text


def test_qa_tests_names_the_language_runner_first() -> None:
    step = _section(_read(AGENTS / "qa-tests.md"), "## STEP 6 — Exécuter", "\n---")
    assert step.index("lang/{lang}.md ## Testing") < step.index("pytest")
