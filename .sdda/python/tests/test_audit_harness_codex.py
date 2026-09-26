"""Codex CLI — agents TOML, skills `.agents/skills/`, hooks `apply_patch`.

Sources : developers.openai.com/codex (subagents, build-skills, hooks,
custom-prompts, agents-md, models) et la grammaire `apply_patch` du dépôt
github.com/openai/codex. La façade précédente déposait du Markdown dans
`.codex/agents/` et des prompts dans `.codex/prompts/` : Codex ne lisait ni l'un
ni l'autre.
"""
from __future__ import annotations

import io
import json
import shutil

import pytest

from sdda_admin import harness_build as hb
from sdda_admin import hooks_selfcheck
from sdda_hooks import _hook

tomllib = pytest.importorskip("tomllib")


def _plan() -> dict:
    matrix = hb.load_matrix()
    plan, _counts = hb.build_harness("codex", matrix["codex"])
    return {p.relative_to(hb.ROOT).as_posix(): c for p, c in plan.files.items()}


def test_every_agent_is_a_codex_toml_with_the_required_keys() -> None:
    plan = _plan()
    matrix = hb.load_matrix()
    agents = {k: v for k, v in plan.items() if k.startswith(".codex/agents/")}
    assert len(agents) == len(list((hb.SDDA / "agents").glob("*.md")))
    for rel, text in agents.items():
        assert rel.endswith(".toml")
        data = tomllib.loads(text)
        for key in ("name", "description", "developer_instructions"):
            assert str(data[key]).strip(), (rel, key)
        assert data["sandbox_mode"] in hb.CODEX_SANDBOX_MODES
        assert data["model"] in matrix["codex"].tier_models.values()
        assert "<!--sdda:" not in data["developer_instructions"]


def test_the_tier_resolves_to_the_documented_codex_models() -> None:
    matrix = hb.load_matrix()
    assert matrix["codex"].tier_models == {"deep": "gpt-6-astra", "balanced": "gpt-6-sol", "fast": "gpt-6-luna"}


def test_commands_become_skills_and_no_prompt_is_written() -> None:
    plan = _plan()
    assert not any(k.startswith(".codex/prompts/") for k in plan)
    skills = {k: v for k, v in plan.items() if k.startswith(".agents/skills/")}
    assert len(skills) == len(list((hb.SDDA / "commands").glob("*.md")))
    for rel, text in skills.items():
        head = text.split("\n---\n", 1)[0]
        assert f"\nname: {rel.split('/')[2]}" in head
        assert "\ndescription: " in head
        assert "@.sdda/" not in text  # Codex n'a pas d'import : la réf est inlinée


def test_hooks_json_wires_only_apply_patch_and_says_why_the_rest_is_not() -> None:
    matrix = hb.load_matrix()
    plan, _ = hb.build_harness("codex", matrix["codex"])
    hooks = json.loads(plan.files[hb.ROOT / ".codex" / "hooks.json"])["hooks"]
    assert list(hooks) == ["PreToolUse"]
    (entry,) = hooks["PreToolUse"]
    assert entry["matcher"] == "^apply_patch$"
    assert all("SDDA_HARNESS=codex" in h["command"] and "git rev-parse --show-toplevel" in h["command"]
               for h in entry["hooks"])
    assert plan.ported["preflight_ownership"].status == "degraded"
    assert all(p.note for m, p in plan.ported.items() if p.status == "absent")
    impact = plan.files[hb.ROOT / ".codex" / "harness-impact.md"]
    assert "appliqué en différé (CI)" in impact and "en partie au runtime" in impact


def test_the_root_pointer_fits_project_doc_max_bytes() -> None:
    text = _plan()["AGENTS.md"]
    assert len(text.encode("utf-8")) < hb.CODEX_PROJECT_DOC_MAX_BYTES
    assert "`.codex/AGENTS.md`" in text and ".codex/agents/*.toml" in text


def _run_hook(monkeypatch: pytest.MonkeyPatch, payload: dict) -> list[dict]:
    seen: list[dict] = []

    def judge(_root, data):
        seen.append(data)
        return _hook.ALLOW

    monkeypatch.setenv(_hook.HARNESS_ENV, "codex")
    monkeypatch.setattr(_hook.sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(_hook.sys, "argv", ["pytest"])
    assert _hook.run("h", judge) == _hook.ALLOW
    return seen


def test_an_apply_patch_is_judged_once_per_file_it_touches(monkeypatch: pytest.MonkeyPatch) -> None:
    patch = ("*** Begin Patch\n*** Add File: a.txt\n+x\n*** Update File: b.txt\n*** Move to: c.txt\n@@\n-x\n+y\n"
             "*** Delete File: d.txt\n*** End Patch\n")
    seen = _run_hook(monkeypatch, {"tool_name": "apply_patch", "tool_input": {"command": patch}, "cwd": "."})
    assert [d["tool_name"] for d in seen] == ["Write"] * 4
    assert [d["tool_input"]["file_path"] for d in seen] == ["a.txt", "b.txt", "c.txt", "d.txt"]


def test_claude_payloads_are_untouched_without_the_harness_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_hook.HARNESS_ENV, raising=False)
    data = {"tool_name": "apply_patch", "tool_input": {"command": "*** Add File: x\n"}}
    assert _hook.foreign_payloads(data) == [data]


@pytest.mark.skipif(shutil.which("bash") is None and shutil.which("sh") is None, reason="shell POSIX requis")
def test_the_wired_hook_refuses_a_forged_gate_report_through_apply_patch() -> None:
    root = hb.ROOT
    report = hooks_selfcheck.run(root, root / ".codex" / "hooks.json")
    assert report.ok, [e.message for e in report.errors]
    assert any(c["harness"] == "codex" and c["code"] == 2 for c in report.data["checks"])


def test_toml_multiline_falls_back_to_an_escaped_basic_string() -> None:
    for text in ("simple", "a ''' b", "antislash \\ et \"\"\" et fin'", "ctrl \x01 et DEL \x7f"):
        assert tomllib.loads(f"k = {hb.toml_multiline(text)}\n")["k"] == text + "\n"
        assert tomllib.loads(f"k = {hb.toml_string(text)}\n")["k"] == text
