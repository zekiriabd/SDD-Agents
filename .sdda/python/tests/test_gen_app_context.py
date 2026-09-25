"""Contexte projet et init — ce que les `dev-*` lisent à la place de STACK.md.

Quatre propriétés qui ne tiennent pas toutes seules :

1. **Il est déterministe.** Aucun horodatage : deux rendus donnent les mêmes
   octets, sinon `--check` crie à chaque passage et on cesse de le lire.
2. **Il dit quand il est périmé.** Un STACK.md ou un IR qui change après l'init
   rend `[PROJECT_CONTEXT_STALE]` ; un fichier absent, `[PROJECT_NOT_INIT]`.
3. **Il porte le nom que le harnais charge.** `memory_file` de la matrice, pas
   un `CLAUDE.md` en dur — et `loader.yml` le désigne par `{memoryfile}`.
4. **Ses données sont dérivées.** Les propriétaires viennent de `loader.yml`,
   la stack résolue de STACK.md sans ses commentaires.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import run_main
from sdda_lib import markdown_io, paths
from sdda_lib.layered_config import harness_memory_file
from sdda_scripts import context_pack, gen_app_context as gac, ir_compiler, project_init

APP = "SupportAssistant"


@pytest.fixture
def compiled(project: Path) -> Path:
    _, report = ir_compiler.compile_to_file(project, 1)
    assert report.ok, [f.cls for f in report.errors]
    return project


def _context(root: Path) -> Path:
    return paths.app_dir(root, APP) / "CLAUDE.md"


def test_check_on_a_fresh_project_is_not_init(compiled: Path) -> None:
    report = gac.run(compiled, mode="check", mission="1")
    assert report.has(gac.CLS_NOT_INIT)
    assert not report.ok


def test_write_then_check_is_green_and_byte_identical(compiled: Path) -> None:
    assert gac.run(compiled, mode="write", mission="1").ok
    first = markdown_io.read_text(_context(compiled))
    again = gac.run(compiled, mode="write", mission="1")
    assert again.ok and again.data["written"] is False
    assert markdown_io.read_text(_context(compiled)) == first
    assert gac.run(compiled, mode="check", mission="1").ok


def test_a_stack_change_makes_the_context_stale(compiled: Path) -> None:
    gac.run(compiled, mode="write", mission="1")
    stack = paths.stack_md_path(compiled)
    before = markdown_io.read_text(stack)
    assert "TraceLevel: full" in before
    stack.write_text(before.replace("TraceLevel: full", "TraceLevel: sampled"), encoding="utf-8")
    report = gac.run(compiled, mode="check", mission="1")
    assert report.has(gac.CLS_STALE), report.render_text()


def test_a_hand_edit_is_reported_as_stale(compiled: Path) -> None:
    gac.run(compiled, mode="write", mission="1")
    target = _context(compiled)
    target.write_text(markdown_io.read_text(target) + "\nretouche\n", encoding="utf-8")
    assert gac.run(compiled, mode="check", mission="1").has(gac.CLS_STALE)


def test_content_carries_the_project_the_ir_and_no_comment(compiled: Path) -> None:
    gac.run(compiled, mode="write", mission="1")
    text = markdown_io.read_text(_context(compiled))
    for heading in ("## 1. Projet", "## 2. Arborescence", "## 3. Commandes", "## 4. Dépendances",
                    "## 5. Système", "## 6. Stack résolue", "## 7. Règles"):
        assert heading in text
    assert "`1-billing-specialist`" in text and "`1-intent-classifier`" in text
    assert "### Project Config" in text and "\n## Project Config" not in text
    # Sans commentaires : la bannière de STACK.md et les `# …` en marge n'y sont plus.
    assert "CONTRAT DE PROPAGATION" not in text
    assert "generatedAt" not in text and "compiledAt" not in text


def test_owners_are_derived_from_the_loader(compiled: Path) -> None:
    owners = gac.layer_owners(compiled)
    assert owners["agents"] == "dev-agent"
    assert owners["tools"] == "dev-tools"
    assert owners["prompts"] == "dev-prompt"
    assert owners["app"] == "dev-backend"


def test_the_file_is_named_after_the_active_harness(compiled: Path) -> None:
    stack = paths.stack_md_path(compiled)
    stack.write_text(markdown_io.read_text(stack).replace("Harness: claude-code", "Harness: codex"),
                     encoding="utf-8")
    assert harness_memory_file(compiled) == "AGENTS.md"
    assert gac.run(compiled, mode="write", mission="1").ok
    assert (paths.app_dir(compiled, APP) / "AGENTS.md").is_file()
    assert not _context(compiled).exists()


def test_the_loader_placeholder_resolves_to_the_memory_file(compiled: Path) -> None:
    gac.run(compiled, mode="write", mission="1")
    files, widened = context_pack.expand(compiled, "workspace/src/*/{memoryfile}", mission="1", target=None,
                                         obj=None, stack=context_pack.active_stack_values(compiled))
    assert widened == []
    assert [f.name for f in files] == ["CLAUDE.md"]


def test_a_missing_ir_is_refused(project: Path) -> None:
    report = gac.run(project, mode="write", mission="1")
    assert report.has("IR_NOT_FOUND")
    assert not _context(project).exists()


def test_project_init_writes_skeleton_and_context_without_installing(compiled: Path) -> None:
    report = project_init.run(compiled, mission="1", install=False)
    assert report.ok, report.render_text()
    assert (paths.app_dir(compiled, APP) / "pyproject.toml").is_file()
    assert _context(compiled).is_file()
    assert report.data["steps"]["dependencies"] == {"skipped": "--no-install"}


def test_project_init_cli_is_idempotent(compiled: Path) -> None:
    argv = ["--root", str(compiled), "--mission", "1", "--no-install"]
    assert run_main(project_init.main, argv)[0] == 0
    assert run_main(project_init.main, argv)[0] == 0
    code, out = run_main(gac.main, ["--root", str(compiled), "--mission", "1", "--check"])
    assert code == 0, out
