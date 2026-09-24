"""Codex CLI lit `AGENTS.md` à la racine, Gemini CLI `GEMINI.md` : les façades
compilées sous `.codex/` et `.gemini/` n'étaient ouvertes par aucun des deux.
`harness_build` écrit donc un pointeur racine — et le dit expérimental."""

from __future__ import annotations

from sdda_admin import harness_build


def _plan(name: str) -> dict:
    matrix = harness_build.load_matrix()
    plan, _counts = harness_build.build_harness(name, matrix[name])
    return plan.files


def test_codex_and_gemini_get_a_root_pointer_to_their_facade() -> None:
    root = harness_build.ROOT
    codex = _plan("codex")[root / "AGENTS.md"]
    gemini = _plan("gemini-cli")[root / "GEMINI.md"]
    assert "`.codex/AGENTS.md`" in codex
    assert "`.gemini/GEMINI.md`" in gemini
    for text in (codex, gemini):
        assert "expérimental" in text
        assert "aucune gate bloquante au runtime" in text


def test_claude_code_reads_its_facade_natively_and_gets_no_root_file() -> None:
    root = harness_build.ROOT
    assert not any(path.parent == root for path in _plan("claude-code"))


def test_harnesses_sharing_a_facade_write_the_same_pointer() -> None:
    # `antigravity` partage `.gemini/` : un pointeur différent ferait échouer
    # `--check` sur celui des deux qui n'a pas été construit en dernier.
    root = harness_build.ROOT
    assert _plan("gemini-cli")[root / "GEMINI.md"] == _plan("antigravity")[root / "GEMINI.md"]


def test_root_pointers_on_disk_are_up_to_date() -> None:
    root = harness_build.ROOT
    for name, filename in (("codex", "AGENTS.md"), ("gemini-cli", "GEMINI.md")):
        expected = _plan(name)[root / filename]
        assert (root / filename).read_text(encoding="utf-8") == expected, (
            f"{filename} a dérivé : python .sdda/sdda.py harness-build --prune"
        )
