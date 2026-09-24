"""Les deux gardes réintroduits : chargeabilité du catalogue et anti-cumul.

`preflight_stack_combo` et `preflight_force_cumul` étaient tous deux **nommés**
— par `compatibility.matrix.json`, par `STACK.md.template`, par le STEP 1.bis de
`/sdda-full` — et aucun des deux n'existait. Le second cas est le plus
instructif : un `bash` appelant un script absent sort en 127, que la table de la
commande n'interprétait ni comme `0` ni comme `1`. Le HARD-GATE anti-cumul
laissait donc passer tout ce qu'il prétendait refuser.

Ces tests gardent le comportement, pas l'implémentation : ce qui doit rester
vrai est *ce qui est refusé*, et surtout *ce qui ne l'est pas*.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import sys
from pathlib import Path

import pytest

from conftest import make_project  # noqa: E402

PYTHON_DIR = Path(__file__).resolve().parents[1]
SDDA = PYTHON_DIR.parent
HOOKS = PYTHON_DIR / "sdda_hooks"
SCRIPTS = PYTHON_DIR / "sdda_scripts"


def _run(script: Path, argv: list[str], env: dict[str, str] | None = None,
         cwd: Path | None = None) -> tuple[int, str]:
    """Exécute en sous-processus : un hook est un exécutable, pas une fonction.

    Le tester en import raterait précisément ce qui a cassé — le code de sortie,
    qui est le seul canal par lequel un hook refuse une action.

    **Toujours depuis un bac à sable.** `preflight_force_cumul` trouve sa racine
    en remontant depuis le répertoire courant, et ÉCRIT l'audit des bypass. Lancé
    depuis le dépôt, chaque passe de la suite ajoutait une dizaine de faux bypass
    (`SDDA_BYPASS_NOPE`, raison « test ») au journal du VRAI projet : 1 484
    lignes, dont aucune n'était un contournement réel. Un journal d'audit qu'on
    pollue n'est plus relu, et c'est alors le vrai bypass qu'on rate.
    """
    if cwd is None:
        sandbox = tempfile.mkdtemp(prefix="sdda-hook-")
        (Path(sandbox) / "workspace").mkdir()
        cwd = Path(sandbox)
    environment = dict(os.environ)
    # L'environnement du testeur ne doit pas fuir dans le test : une variable
    # `SDDA_BYPASS_*` posée par ailleurs rendrait le résultat dépendant de la
    # machine.
    for key in list(environment):
        if key.startswith("SDDA_"):
            environment.pop(key)
    environment.update(env or {})
    proc = subprocess.run(
        [sys.executable, str(script), *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=environment, stdin=subprocess.DEVNULL, cwd=cwd,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ---------------------------------------------------------------------------
# preflight_stack_combo
# ---------------------------------------------------------------------------
def test_a_complete_combo_is_allowed(tmp_path: Path) -> None:
    """La combo C1 de la fixture a toutes ses fiches : elle passe."""
    project = make_project(tmp_path)
    code, out = _run(HOOKS / "preflight_stack_combo.py", ["--root", str(project)])
    assert code == 0, out


def test_an_untested_component_is_said_not_refused(tmp_path: Path) -> None:
    """`untested` est l'état nominal en phase de conception — il se dit, il ne bloque pas.

    Refuser ici bloquerait la combo C1 que la ROADMAP construit, donc tout le
    framework. C'est la distinction qui rend le hook utilisable.
    """
    project = make_project(tmp_path)
    code, out = _run(HOOKS / "preflight_stack_combo.py", ["--root", str(project)])
    assert code == 0
    assert "non mesuré" in out, f"le niveau doit être DIT, pas tu : {out}"


#: Nom de fiche délibérément impossible. Une version antérieure de ces tests
#: utilisait `fastapi-sse` comme exemple d'absence — et ils ont cassé le jour où
#: cette fiche a été écrite. Un test dont la validité dépend de l'état du
#: catalogue devient rouge pour la meilleure des raisons, ce qui apprend à
#: l'ignorer.
NEVER_A_FICHE = "surface-qui-nexiste-pas"


def _with_missing_fiche(tmp_path: Path) -> Path:
    project = make_project(tmp_path)
    stack = project / "workspace" / "stack" / "STACK.md"
    stack.write_text(
        stack.read_text(encoding="utf-8").replace(
            "- .sdda/stacks/serving/cli.md", f"- .sdda/stacks/serving/{NEVER_A_FICHE}.md"),
        encoding="utf-8")
    return project


def test_a_component_without_a_fiche_is_refused(tmp_path: Path) -> None:
    """Une ligne activée sans fiche ne charge rien : c'est un refus, pas un avertissement."""
    code, out = _run(HOOKS / "preflight_stack_combo.py", ["--root", str(_with_missing_fiche(tmp_path))])
    assert code == 2, out
    assert "STACK_COMBO_UNLOADABLE" in out
    assert f"serving/{NEVER_A_FICHE}" in out


def test_the_missing_fiche_refusal_is_bypassable_and_says_so(tmp_path: Path) -> None:
    code, out = _run(HOOKS / "preflight_stack_combo.py", ["--root", str(_with_missing_fiche(tmp_path))],
                     {"SDDA_ALLOW_UNTESTED_COMBO": "1"})
    assert code == 0, out
    assert "SDDA_ALLOW_UNTESTED_COMBO" in out, "un bypass silencieux ne vaut pas mieux qu'une absence de garde"


def test_a_language_fiche_is_found_under_its_real_directory(tmp_path: Path) -> None:
    """`language/python` vit sous `stacks/lang/`.

    Sans la table catégorie -> répertoire, un composant parfaitement présent est
    déclaré absent. Un faux rouge sur le langage — c'est-à-dire sur CHAQUE
    projet — et le premier réflexe serait de couper le hook.
    """
    project = make_project(tmp_path)
    code, out = _run(HOOKS / "preflight_stack_combo.py", ["--root", str(project)])
    assert "language/python" not in out or "non mesuré" in out, out


# ---------------------------------------------------------------------------
# preflight_stack_combo — cohérence de langage
#
# Le trou que ces tests ferment : le contrôle de chargeabilité ne regarde que
# l'EXISTENCE du fichier. `lang/csharp.md` + `rag/hybrid.md` +
# `vectorstore/pgvector.md` sortait EXIT=0 — les trois fiches existent, mais les
# deux dernières ne contiennent que du psycopg et déclarent « Suppose
# lang/python.md ». Le générateur .NET recevait du Python comme référence.
# ---------------------------------------------------------------------------
def _patch_stack(project: Path, old: str, new: str) -> Path:
    stack = project / "workspace" / "stack" / "STACK.md"
    text = stack.read_text(encoding="utf-8")
    assert old in text, f"ancre absente de la fixture : {old!r}"
    stack.write_text(text.replace(old, new), encoding="utf-8")
    return project


def _as_csharp(tmp_path: Path) -> Path:
    """La fixture C1, avec le seul langage changé — tout le reste reste Python."""
    return _patch_stack(make_project(tmp_path),
                        "- .sdda/stacks/lang/python.md", "- .sdda/stacks/lang/csharp.md")


def test_a_fiche_of_another_language_is_refused(tmp_path: Path) -> None:
    """Une fiche présente mais écrite pour un autre runtime est PIRE qu'une absente.

    L'absence se voit — le hook la nomme. La traduction inventée, non : le code
    généré compile peut-être, et personne ne sait d'où vient son modèle.
    """
    code, out = _run(HOOKS / "preflight_stack_combo.py", ["--root", str(_as_csharp(tmp_path))])
    assert code == 2, out
    assert "STACK_LANGUAGE_MISMATCH" in out
    assert "vectorstore/pgvector" in out and "python" in out


def test_the_language_refusal_is_bypassable_for_polyglot_projects(tmp_path: Path) -> None:
    """Le cas légitime existe — un reranker Python en side-car d'une application .NET.

    Il est rare, donc il s'assume explicitement plutôt que d'affaiblir le garde.
    """
    code, out = _run(HOOKS / "preflight_stack_combo.py", ["--root", str(_as_csharp(tmp_path))],
                     {"SDDA_ALLOW_LANG_MISMATCH": "1"})
    assert code == 0, out
    assert "SDDA_ALLOW_LANG_MISMATCH" in out


def test_a_neutral_fiche_never_trips_the_language_check(tmp_path: Path) -> None:
    """`Languages: *` traverse tous les langages — sinon le garde serait inutilisable.

    Les patterns d'orchestration, les garde-fous et les modèles servis par API
    ne supposent aucun runtime. Les déclarer couplés rendrait toute combinaison
    non-Python impossible, y compris celles qui sont parfaitement cohérentes.
    """
    project = make_project(tmp_path)
    # Écrite en entier plutôt que rapiécée : ce test porte sur la NEUTRALITÉ,
    # et le rendre dépendant de ce que la fixture contient le ferait rougir au
    # premier ajout d'une fiche sans rapport.
    (project / "workspace" / "stack" / "STACK.md").write_text(
        "# Agentic Stack\n\n"
        "## Active Language & Runtime\n - .sdda/stacks/lang/csharp.md\n\n"
        "## Active Agent Framework\n - .sdda/stacks/framework/ms-agent-framework.md\n\n"
        "## Active Orchestration Pattern\n - .sdda/stacks/orchestration/router.md\n\n"
        "## Active RAG Pattern\n - .sdda/stacks/rag/none.md\n\n"
        "## Active Reranker\n - .sdda/stacks/rerank/none.md\n\n"
        "## Active Memory Strategy\n - .sdda/stacks/memory/buffer.md\n\n"
        "## Active Guardrails\n - .sdda/stacks/guardrails/schema-validation.md\n\n"
        "## Active Serving Surface\n - .sdda/stacks/serving/aspnet-minimal.md\n",
        encoding="utf-8")
    # Aucune combo .NET dans la matrice : la combinaison est hors liste, ce qui
    # est un AUTRE contrôle. On l'assume explicitement pour ne juger ici que la
    # neutralité de langage.
    code, out = _run(HOOKS / "preflight_stack_combo.py", ["--root", str(project)],
                     {"SDDA_ALLOW_UNTESTED_COMBO": "1"})
    assert code == 0, out
    assert "STACK_LANGUAGE_MISMATCH" not in out


def test_a_fiche_without_a_languages_header_is_said_never_refused(tmp_path: Path) -> None:
    """Un en-tête manquant est un défaut du FRAMEWORK, pas de l'utilisateur.

    Le refuser ici bloquerait un projet pour une fiche que son auteur n'a pas
    annotée. C'est `framework_smoke.stacks.languages` qui en fait une erreur —
    au bon endroit, c'est-à-dire en CI.
    """
    project = make_project(tmp_path)
    fiche = SDDA / "stacks" / "memory" / "buffer.md"
    original = fiche.read_text(encoding="utf-8")
    try:
        fiche.write_text(original.replace("Languages: *\n", ""), encoding="utf-8")
        code, out = _run(HOOKS / "preflight_stack_combo.py", ["--root", str(project)])
        assert code == 0, out
        assert "sans en-tête `Languages:`" in out, f"l'absence doit être DITE : {out}"
    finally:
        fiche.write_text(original, encoding="utf-8")


# ---------------------------------------------------------------------------
# preflight_stack_combo — dérive de `## Active Retrieval Stack`
# ---------------------------------------------------------------------------
def test_rerank_enabled_without_a_reranker_is_refused(tmp_path: Path) -> None:
    """`RerankEnabled: true` + `rerank/none.md` : la clé est lue, rien ne l'implémente.

    La panne ne se voit jamais à l'exécution — le reranking n'a simplement pas
    lieu, et le rapport de retrieval laisse croire le contraire.
    """
    project = _patch_stack(
        make_project(tmp_path),
        "## Active Retrieval Stack\n",
        "## Active Reranker\n - .sdda/stacks/rerank/none.md\n\n## Active Retrieval Stack\nRerankEnabled: true\n")
    code, out = _run(HOOKS / "preflight_stack_combo.py", ["--root", str(project)])
    assert code == 2, out
    assert "RETRIEVAL_CONFIG_DRIFT" in out and "RerankEnabled" in out


def test_an_external_store_cannot_claim_to_live_in_the_business_database(tmp_path: Path) -> None:
    """`Mode: same-as-database` n'est légitime que pour pgvector.

    C'est le défaut qui se cache le mieux : tant que le store EST pgvector, la
    coïncidence tient. Le jour où l'on branche un service distinct, l'index n'a
    plus aucune adresse — et rien ne le disait avant ce contrôle.
    """
    project = _patch_stack(
        make_project(tmp_path),
        " - .sdda/stacks/vectorstore/pgvector.md\n",
        " - .sdda/stacks/vectorstore/pgvector.md\nVectorStoreConnection:\n  Mode: dedicated\n  Endpoint:\n")
    code, out = _run(HOOKS / "preflight_stack_combo.py", ["--root", str(project)])
    assert code == 2, out
    assert "RETRIEVAL_CONFIG_DRIFT" in out and "Endpoint" in out


def test_same_as_database_stays_legitimate_for_pgvector(tmp_path: Path) -> None:
    project = _patch_stack(
        make_project(tmp_path),
        " - .sdda/stacks/vectorstore/pgvector.md\n",
        " - .sdda/stacks/vectorstore/pgvector.md\nVectorStoreConnection:\n  Mode: same-as-database\n")
    code, out = _run(HOOKS / "preflight_stack_combo.py", ["--root", str(project)])
    assert code == 0, out


# ---------------------------------------------------------------------------
# preflight_force_cumul
# ---------------------------------------------------------------------------
def test_no_lever_is_the_nominal_case() -> None:
    code, out = _run(SCRIPTS / "preflight_force_cumul.py", [])
    assert code == 0
    assert "aucun contournement" in out


def test_an_env_bypass_without_a_reason_is_refused() -> None:
    code, out = _run(SCRIPTS / "preflight_force_cumul.py", [],
                     {"SDDA_BYPASS_TOOL_GATE": "1"})
    assert code == 1
    assert "BYPASS_REASON_MISSING" in out


def test_force_alone_needs_no_reason() -> None:
    """`--force` est déjà nominatif : il est dans la ligne de commande.

    Une env var, elle, survit d'un run à l'autre sans que personne ne la revoie —
    c'est ce qui justifie d'en exiger la raison, et seulement d'elle.
    """
    code, out = _run(SCRIPTS / "preflight_force_cumul.py", ["--force"])
    assert code == 0, out


def test_two_levers_are_refused_without_an_explicit_allow() -> None:
    code, out = _run(SCRIPTS / "preflight_force_cumul.py", ["--force"],
                     {"SDDA_BYPASS_TOOL_GATE": "1", "SDDA_BYPASS_REASON": "MCP indisponible en CI"})
    assert code == 1
    assert "FORCE_CUMUL_REJECTED" in out


def test_the_cumul_refusal_is_itself_overridable_and_traced() -> None:
    code, out = _run(SCRIPTS / "preflight_force_cumul.py", ["--force"],
                     {"SDDA_BYPASS_TOOL_GATE": "1", "SDDA_BYPASS_REASON": "MCP indisponible en CI",
                      "SDDA_ALLOW_FORCE": "1"})
    assert code == 0, out
    assert "cumul autorisé" in out


def test_the_audit_goes_to_the_project_it_runs_in_never_elsewhere(tmp_path: Path) -> None:
    (tmp_path / "workspace").mkdir()
    code, _ = _run(SCRIPTS / "preflight_force_cumul.py", ["--force"], cwd=tmp_path)
    assert code == 0
    assert (tmp_path / "workspace/.sys/.audit/bypasses.jsonl").is_file()


def test_a_misspelled_bypass_is_refused_not_ignored() -> None:
    """Une faute de frappe produit un pipeline qu'on croit forcé et qui ne l'est pas.

    C'est le pire des deux mondes : l'opérateur pense avoir desserré une gate,
    la gate reste fermée, et le run échoue pour une raison qui n'a rien à voir.
    """
    code, out = _run(SCRIPTS / "preflight_force_cumul.py", [],
                     {"SDDA_BYPASS_TOOLGATE": "1", "SDDA_BYPASS_REASON": "test"})
    assert code == 1
    assert "BYPASS_UNKNOWN" in out


@pytest.mark.parametrize("gate", ["G0 MISSION", "G1 CAP", "G5 AGENT", "G6 ORCH", "G8 ACCEPTANCE"])
def test_the_refusal_names_the_gates_that_can_never_be_bypassed(gate: str) -> None:
    """Le refus doit rappeler ce qui reste hors de portée.

    Sans cela, l'opérateur ne sait pas s'il vient de buter sur une règle
    négociable ou sur la limite du système.
    """
    code, out = _run(SCRIPTS / "preflight_force_cumul.py", [],
                     {"SDDA_BYPASS_NOPE": "1", "SDDA_BYPASS_REASON": "test"})
    assert code == 1
    assert gate in out
