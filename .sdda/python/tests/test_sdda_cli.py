"""sdda_cli — le point d'entrée unique, et le contrat que les scanners lisent.

Ce que ces tests défendent : le registre est DÉRIVÉ du disque (un script ajouté
est appelable sans qu'une table soit tenue à jour) ; l'aller-retour
sous-commande <-> module est exact dans les deux sens ; le dispatch marche pour
les deux signatures de `main()` qui coexistent dans le dépôt ; et surtout —
`resolve()` reste la SSoT que `planned_scripts` et `framework_smoke` utilisent
pour continuer de voir un script promis mais pas écrit, maintenant que la prose
n'écrit plus de chemin.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import sdda_cli
from conftest import run_main

PYTHON_DIR = Path(sdda_cli.__file__).resolve().parent
SDDA = PYTHON_DIR.parent
ROOT = SDDA.parent


# ---------------------------------------------------------------------------
# Le registre : dérivé du disque, sans collision, réversible
# ---------------------------------------------------------------------------
def test_every_module_on_disk_is_a_subcommand() -> None:
    commands = sdda_cli.discover()
    on_disk = {
        f"{pkg}/{p.stem}.py"
        for pkg in sdda_cli.PACKAGES
        for p in (PYTHON_DIR / pkg).glob("*.py")
        if not p.stem.startswith("_")
    }
    assert {sdda_cli.resolve(n) for n in commands} == on_disk


def test_subcommand_names_do_not_collide() -> None:
    """Un espace de noms plat n'est tenable que sans collision — et c'est vérifié, pas espéré."""
    names = [
        sdda_cli._subcommand(p.stem)
        for pkg in sdda_cli.PACKAGES
        for p in (PYTHON_DIR / pkg).glob("*.py")
        if not p.stem.startswith("_")
    ]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("name,expected", [
    ("validate-mission", "sdda_scripts/validate_mission.py"),
    ("state", "sdda_scripts/sdda_state.py"),          # seul module préfixé : `sdda sdda-state` serait une redite
    ("framework-smoke", "sdda_admin/framework_smoke.py"),
    ("preflight-agent-bounds", "sdda_hooks/preflight_agent_bounds.py"),
])
def test_known_subcommands_resolve_to_their_module(name: str, expected: str) -> None:
    assert sdda_cli.resolve(name) == expected


def test_an_unknown_subcommand_resolves_to_nothing_but_still_has_a_home() -> None:
    """Un script « Planifié » ne résout pas ; l'inventaire doit quand même le nommer.

    Le nom est fictif à dessein : l'exemple précédent (`cost-report`) a été
    écrit, et un test qui dépend du backlog casse le jour où on le solde.
    """
    assert sdda_cli.resolve("never-written-report") is None
    assert sdda_cli.expected_path("never-written-report") == "sdda_scripts/never_written_report.py"


def test_the_name_transform_is_reversible() -> None:
    for pkg in sdda_cli.PACKAGES:
        for p in (PYTHON_DIR / pkg).glob("*.py"):
            if p.stem.startswith("_"):
                continue
            assert sdda_cli._module_stem(sdda_cli._subcommand(p.stem)) == p.stem


# ---------------------------------------------------------------------------
# Le dispatch
# ---------------------------------------------------------------------------
def test_no_argument_lists_the_commands_grouped_by_package() -> None:
    code, out = run_main(sdda_cli.main, [])
    assert code == 0
    assert "validate-mission" in out and "framework-smoke" in out
    assert "sdda_scripts/" in out and "sdda_admin/" in out and "sdda_hooks/" in out
    assert "sdda-state" not in out                      # le préfixe est retiré, pas affiché


def test_an_unknown_subcommand_is_a_three_line_error_with_suggestions() -> None:
    code, out = run_main(sdda_cli.main, ["validate-mision"])
    assert code == 1
    assert "[CLI_COMMAND_UNKNOWN]" in out
    assert out.splitlines()[0].startswith("ERROR:")
    assert "validate-mission" in out                    # difflib propose le voisin


def test_dispatch_reaches_a_main_taking_argv(project: Path) -> None:
    """`validate_mission.main(argv)` — 39 scripts ont cette signature."""
    code, out = run_main(sdda_cli.main, ["validate-mission", "--root", str(project), "--mission", "1", "--json"])
    assert code == 0 and '"ok"' in out


def test_dispatch_reaches_a_main_reading_sys_argv(project: Path) -> None:
    """`list_graders.main()` sans paramètre — 21 scripts lisent `sys.argv`."""
    code, out = run_main(sdda_cli.main, ["list-graders"])
    assert code == 0 and "llm-judge" in out


def test_dispatch_forwards_the_exit_code(project: Path) -> None:
    code, _ = run_main(sdda_cli.main, ["validate-mission", "--root", str(project), "--mission", "404"])
    assert code != 0


def _launch(*args: str) -> tuple[int, str]:
    """Le lanceur, dans un vrai sous-processus, depuis la racine du dépôt.

    En process, `argparse` de Python 3.14 déduit son `prog` de `sys.orig_argv`
    quand l'interpréteur a été lancé par `-m` (le cas sous pytest) : le `usage:`
    rendu serait celui de pytest, pas celui qu'un utilisateur verrait. Ce qu'on
    veut vérifier est justement ce que l'utilisateur voit.
    """
    proc = subprocess.run(
        [sys.executable, str(SDDA / "sdda.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_the_launcher_runs_from_a_bare_clone_without_installation() -> None:
    code, out = _launch()
    assert code == 0 and "validate-mission" in out


def test_argparse_shows_the_short_name_as_prog() -> None:
    """`usage: sdda validate-cap`, pas un chemin à cinq segments — c'est ce qu'on tape."""
    code, out = _launch("validate-cap", "--help")
    assert code == 0 and "usage: sdda validate-cap" in out


def test_the_launcher_forwards_the_exit_code_of_an_unknown_subcommand() -> None:
    code, out = _launch("no-such-thing")
    assert code == 1 and "[CLI_COMMAND_UNKNOWN]" in out


# ---------------------------------------------------------------------------
# Le lanceur et le contrat avec les scanners
# ---------------------------------------------------------------------------
def test_the_launcher_needs_no_installation() -> None:
    """`.sdda/sdda.py` n'importe que la stdlib et `sdda_cli` : un clone nu doit suffire."""
    text = (SDDA / "sdda.py").read_text(encoding="utf-8")
    assert "sys.path.insert" in text and "from sdda_cli import main" in text


def test_the_console_entry_points_at_the_dispatcher() -> None:
    text = (PYTHON_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert 'sdda = "sdda_cli:main"' in text
    assert 'py-modules = ["sdda_cli"]' in text


def test_no_prose_still_calls_a_script_by_its_five_segment_path() -> None:
    """La migration est complète, et le reste : un nouvel appel long serait une régression."""
    skip = {"tests", "digests", "__pycache__", ".claude", ".codex", ".gemini"}
    rx = re.compile(r"python3? \.sdda/python/(?:sdda_scripts|sdda_admin|sdda_hooks)/")
    offenders = [
        f"{p.relative_to(ROOT).as_posix()}:{i}"
        for pat in ("*.md", "*.yml", "*.py")
        for p in (SDDA).rglob(pat)
        if p.is_file() and not (skip & set(p.parts))
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if rx.search(line)
    ]
    assert offenders == []


def test_every_subcommand_called_by_the_prose_resolves_or_is_declared_planned() -> None:
    """Le contrôle que la migration risquait de rendre muet : un prompt ne peut pas
    appeler `sdda {cmd}` pour un script que personne n'a écrit sans le DIRE."""
    from sdda_admin import planned_scripts

    pathed, _bare, declared = planned_scripts.collect()
    silent = planned_scripts.undeclared_missing(pathed, declared)
    assert silent == {}


def test_the_scanner_resolves_the_short_form_to_the_same_module() -> None:
    """`planned_scripts` doit compter un appel court comme l'appel long qu'il remplace."""
    from sdda_admin import planned_scripts

    pathed, _bare, _declared = planned_scripts.collect()
    # `compute_status.py` est appelé par plusieurs commandes, toutes migrées : s'il
    # n'était plus vu, la migration aurait rendu l'inventaire aveugle.
    assert "sdda_scripts/compute_status.py" in pathed
    assert len(pathed["sdda_scripts/compute_status.py"]) >= 3
