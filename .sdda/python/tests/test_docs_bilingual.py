"""Documentation bilingue : `X.md` (anglais, référence) et `X.fr.md` (jumeau).

Quatre mécanismes doivent suivre la convention, sans quoi elle ne tient que
par la bonne volonté des traducteurs :

- `harness_build` compile le fichier mémoire des harnais depuis le jumeau
  FRANÇAIS de l'architecture (les agents lisent des prompts français), avec
  repli sur l'anglais s'il manque ;
- `sync_counters` régénère les marqueurs des jumeaux sans qu'on les liste ;
- `framework_smoke` (`docs.parity`) refuse une paire qui diverge sur le
  contenu technique, et signale un jumeau manquant ;
- `planned_scripts` écrit l'inventaire dans les deux langues.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sdda_admin import framework_smoke, harness_build, planned_scripts, sync_counters

EN = """# Title

Intro citing [MISSION_INCOMPLETE] and <!--sdda:count agents-->23<!--/sdda:count--> agents.

## 1. Section

```bash
python .sdda/sdda.py framework-smoke   # run it
```

### 1.1 Sub

```python
# not a heading
x = 1
```
"""

FR = """# Titre

Intro qui cite [MISSION_INCOMPLETE] et <!--sdda:count agents-->23<!--/sdda:count--> agents.

## 1. Section

```bash
python .sdda/sdda.py framework-smoke   # run it
```

### 1.1 Sous-section

```python
# pas un titre
x = 1
```
"""


@pytest.fixture
def findings():
    framework_smoke.FINDINGS.clear()
    yield framework_smoke.FINDINGS
    framework_smoke.FINDINGS.clear()


# ---------------------------------------------------------------------------
# harness_build — la source du fichier mémoire
# ---------------------------------------------------------------------------
def test_memory_file_is_compiled_from_the_french_twin(tmp_path: Path) -> None:
    (tmp_path / "ARCHITECTURE.md").write_text("# English", encoding="utf-8")
    (tmp_path / "ARCHITECTURE.fr.md").write_text("# Français", encoding="utf-8")
    assert harness_build.memory_source(tmp_path).name == "ARCHITECTURE.fr.md"


def test_memory_file_falls_back_to_english_when_twin_is_missing(tmp_path: Path) -> None:
    (tmp_path / "ARCHITECTURE.md").write_text("# English", encoding="utf-8")
    assert harness_build.memory_source(tmp_path).name == "ARCHITECTURE.md"


def test_fallback_is_announced_by_the_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ARCHITECTURE.md").write_text("# English only", encoding="utf-8")
    matrix = harness_build.load_matrix()          # lit le vrai `.sdda/`, avant le patch
    monkeypatch.setattr(harness_build, "SDDA", tmp_path)
    monkeypatch.setattr(harness_build, "BUILD_NOTES", {})
    adapter = harness_build.ADAPTERS["claude-code"](matrix["claude-code"])
    plan = harness_build.BuildPlan()
    adapter.emit_memory_file(plan, tmp_path / ".claude")
    (content,) = plan.files.values()
    assert "# English only" in content
    assert "depuis .sdda/ARCHITECTURE.md" in content
    assert any("ARCHITECTURE.fr.md absent" in n for n in harness_build.BUILD_NOTES["claude-code"])


def test_claude_md_on_disk_comes_from_the_french_architecture() -> None:
    claude_md = (harness_build.ROOT / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "depuis .sdda/ARCHITECTURE.fr.md" in claude_md


# ---------------------------------------------------------------------------
# sync_counters — les jumeaux sont des cibles sans être listés
# ---------------------------------------------------------------------------
def test_sync_counters_targets_existing_twins_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_counters, "TARGETS", ("README.md", ".sdda/docs/A.md", ".sdda/docs/B.md", "x.yaml"))
    (tmp_path / ".sdda" / "docs").mkdir(parents=True)
    (tmp_path / ".sdda" / "docs" / "A.fr.md").write_text("", encoding="utf-8")
    targets = sync_counters.target_files(tmp_path)
    assert ".sdda/docs/A.fr.md" in targets
    assert ".sdda/docs/B.fr.md" not in targets          # absent : pas une dérive de compteur
    assert targets.index(".sdda/docs/A.fr.md") == targets.index(".sdda/docs/A.md") + 1


def test_architecture_twin_is_a_counter_target() -> None:
    assert ".sdda/ARCHITECTURE.fr.md" in sync_counters.target_files()


def test_subcommands_counter_is_the_launcher_registry() -> None:
    # ARCHITECTURE §2.ante annonçait « 60 » sous-commandes quand le disque en
    # portait davantage : le chiffre est désormais un marqueur régénéré.
    import sdda_cli

    assert sync_counters.COUNTERS["subcommands"]() == len(sdda_cli.discover())


# ---------------------------------------------------------------------------
# framework_smoke — docs.parity
# ---------------------------------------------------------------------------
def test_identical_technical_content_has_no_diff() -> None:
    assert framework_smoke.parity_diffs(EN, FR) == []


def test_hash_comment_inside_a_code_block_is_not_a_heading() -> None:
    fp = framework_smoke.doc_fingerprint(EN)
    assert fp["headings"] == {1: 1, 2: 1, 3: 1}


@pytest.mark.parametrize("mutation,expected", [
    (lambda t: t.replace("[MISSION_INCOMPLETE]", "MISSION_INCOMPLETE"), "classes"),
    (lambda t: t + "\n## 2. Extra\n", "titres"),
    (lambda t: t.replace("<!--sdda:count agents-->23<!--/sdda:count-->", "23"), "marqueurs"),
    (lambda t: t.replace("sdda.py framework-smoke", "sdda.py framework-smoke --json"), "commandes"),
    (lambda t: t + "\n```yaml\na: 1\n```\n", "blocs de code"),
])
def test_divergent_twin_is_reported(mutation, expected: str) -> None:
    diffs = framework_smoke.parity_diffs(EN, mutation(FR))
    assert diffs and expected in diffs[0]


def test_a_translated_shell_comment_is_not_a_divergence() -> None:
    assert framework_smoke.parity_diffs(EN, FR.replace("# run it", "# à lancer")) == []


def _docs_tree(root: Path, *, twin: str | None) -> None:
    docs = root / ".sdda" / "docs"
    docs.mkdir(parents=True)
    (root / "README.md").write_text(EN, encoding="utf-8")
    (root / "README.fr.md").write_text(FR, encoding="utf-8")
    (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")   # exempté
    (docs / "README.md").write_text("# Hub\n\n[Guide](GUIDE.md)\n", encoding="utf-8")
    (docs / "README.fr.md").write_text("# Hub\n\n[Guide](GUIDE.fr.md)\n", encoding="utf-8")
    (docs / "GUIDE.md").write_text(EN, encoding="utf-8")
    if twin is not None:
        (docs / "GUIDE.fr.md").write_text(twin, encoding="utf-8")


def _levels(findings, check: str) -> list[str]:
    return [f.level for f in findings if f.check == check]


def test_parity_ok_when_all_pairs_match(tmp_path: Path, findings) -> None:
    _docs_tree(tmp_path, twin=FR)
    framework_smoke.check_docs_parity(tmp_path)
    assert _levels(findings, "docs.parity") == ["ok"]
    assert not _levels(findings, "docs.twins")


def test_divergent_pair_fails(tmp_path: Path, findings) -> None:
    _docs_tree(tmp_path, twin=FR + "\n## 9. Only in French\n")
    framework_smoke.check_docs_parity(tmp_path)
    assert "fail" in _levels(findings, "docs.parity")


def test_missing_twin_fails_by_default() -> None:
    """Depuis la fin du lot 2 de traduction, une page sans jumeau n'entre plus."""
    assert framework_smoke.MISSING_TWIN_IS_FAILURE is True


def test_missing_twin_can_still_be_a_warning(tmp_path: Path, findings) -> None:
    _docs_tree(tmp_path, twin=None)
    framework_smoke.check_docs_parity(tmp_path, missing_is_failure=False)
    twins = [f for f in findings if f.check == "docs.twins"]
    assert [f.level for f in twins] == ["warn"]
    assert "listé par le hub" in twins[0].message
    assert "fail" not in _levels(findings, "docs.parity")


def test_missing_twin_fails_once_switched(tmp_path: Path, findings) -> None:
    _docs_tree(tmp_path, twin=None)
    framework_smoke.check_docs_parity(tmp_path, missing_is_failure=True)
    assert _levels(findings, "docs.twins") == ["fail"]


def test_french_page_without_english_reference_fails(tmp_path: Path, findings) -> None:
    _docs_tree(tmp_path, twin=FR)
    (tmp_path / ".sdda" / "docs" / "ORPHAN.fr.md").write_text("# Orphelin\n", encoding="utf-8")
    framework_smoke.check_docs_parity(tmp_path)
    assert any("ORPHAN.fr.md" in f.message and f.level == "fail" for f in findings)


# ---------------------------------------------------------------------------
# planned_scripts — un inventaire, deux langues
# ---------------------------------------------------------------------------
def test_planned_scripts_renders_both_languages_with_the_same_data() -> None:
    pathed = {"sdda_scripts/not_written_yet.py": {"agents/a.md", "commands/b.md"}}
    bare = {"orphan_name.py": {"rules/c.md"}}
    en = planned_scripts.render(pathed, bare, {}, lang="en")
    fr = planned_scripts.render(pathed, bare, {}, lang="fr")
    assert en.startswith("# Planned scripts") and fr.startswith("# Scripts planifiés")
    for text in (en, fr):
        assert "`not_written_yet.py` | 2 | ❌ |" in text
        assert "`orphan_name.py`" in text
    assert framework_smoke.parity_diffs(en, fr) == []


def test_planned_scripts_outputs_are_twins() -> None:
    assert planned_scripts.OUTPUTS["en"].name == "PLANNED-SCRIPTS.md"
    assert planned_scripts.OUTPUTS["fr"] == framework_smoke.twin_path(planned_scripts.OUTPUTS["en"])
