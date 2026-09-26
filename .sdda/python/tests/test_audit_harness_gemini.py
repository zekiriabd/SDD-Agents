"""Gemini CLI — commandes TOML, sous-agents `.gemini/agents/`, hooks `BeforeTool`, mémoire importée.

Sources : geminicli.com/docs (cli/custom-commands, core/subagents, hooks,
hooks/reference, reference/tools, cli/gemini-md, cli/model) et le code de
github.com/google-gemini/gemini-cli (atFileProcessor, memoryImportProcessor).
"""
from __future__ import annotations

import io
import json
import re
import shutil

import pytest

from sdda_admin import framework_smoke
from sdda_admin import harness_build as hb
from sdda_admin import hooks_selfcheck
from sdda_hooks import _hook

tomllib = pytest.importorskip("tomllib")


def _plan():
    matrix = hb.load_matrix()
    plan, _counts = hb.build_harness("gemini-cli", matrix["gemini-cli"])
    return plan, {p.relative_to(hb.ROOT).as_posix(): c for p, c in plan.files.items()}


def test_commands_parse_and_carry_no_injection_gemini_would_execute() -> None:
    _plan_obj, files = _plan()
    commands = {k: v for k, v in files.items() if k.startswith(".gemini/commands/")}
    assert len(commands) == len(list((hb.SDDA / "commands").glob("*.md")))
    for rel, text in commands.items():
        data = tomllib.loads(text)
        assert data["prompt"].strip() and isinstance(data["description"], str), rel
        assert not re.search(r"[@!]\{", data["prompt"]), rel


def test_a_description_with_a_backslash_or_a_quote_stays_valid_toml() -> None:
    value = 'chemin C:\\x et "guillemets"'
    assert tomllib.loads(f"description = {hb.toml_string(value)}\n")["description"] == value


def test_agents_are_native_subagents_with_gemini_tool_names() -> None:
    _plan_obj, files = _plan()
    known = {t for tools in hb.GEMINI_TOOLS.values() for t in tools}
    agents = {k: v for k, v in files.items() if k.startswith(".gemini/agents/")}
    assert len(agents) == len(list((hb.SDDA / "agents").glob("*.md")))
    assert not any(k.startswith(".gemini/agents-inline/") for k in files)
    matrix = hb.load_matrix()
    for rel, text in agents.items():
        problems: list[str] = []
        values = framework_smoke._strict_frontmatter(hb.ROOT / rel, text, ("name", "description"), problems)
        assert not problems, problems
        assert hb.GEMINI_AGENT_NAME_RE.match(values["name"])
        assert set(values["tools"]) <= known, rel
        assert values["model"] in matrix["gemini-cli"].tier_models.values()


def test_settings_wire_before_tool_on_writes_and_on_each_agent_tool() -> None:
    plan, files = _plan()
    hooks = json.loads(files[".gemini/settings.json"])["hooks"]
    assert list(hooks) == ["BeforeTool"]
    matchers = {e["matcher"]: e for e in hooks["BeforeTool"]}
    assert "^(write_file|replace)$" in matchers
    spawn = next(m for m in matchers if "dev-agent" in m)
    assert re.fullmatch(spawn, "dev-agent") and not re.fullmatch(spawn, "write_file")
    assert "\\-" not in spawn  # refusé par une regex JavaScript en mode `u`
    for entry in hooks["BeforeTool"]:
        for hook in entry["hooks"]:
            assert hook["type"] == "command" and "SDDA_HARNESS=gemini-cli" in hook["command"]
            assert "$GEMINI_PROJECT_DIR/" in hook["command"]
    assert plan.ported["preflight_forbidden_reads"].status == "absent"
    assert plan.ported["postflight_trace_present"].status == "absent"


def test_the_root_pointer_imports_the_facade_and_the_facade_imports_nothing_else() -> None:
    _plan_obj, files = _plan()
    assert "\n@./.gemini/GEMINI.md\n" in files["GEMINI.md"]
    assert framework_smoke._memport_imports(files[".gemini/GEMINI.md"]) == []
    assert framework_smoke._memport_imports("voir @./x.md et `@./y.md` et recall@k") == ["./x.md"]


def _normalized(monkeypatch: pytest.MonkeyPatch, payload: dict) -> list[dict]:
    monkeypatch.setenv(_hook.HARNESS_ENV, "gemini-cli")
    return _hook.foreign_payloads(payload)


def test_gemini_payloads_translate_to_the_shape_the_hooks_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    (write,) = _normalized(monkeypatch, {"tool_name": "write_file", "tool_input": {"file_path": "a", "content": ""}})
    assert write["tool_name"] == "Write" and write["tool_input"]["file_path"] == "a"
    (grep,) = _normalized(monkeypatch, {"tool_name": "grep_search",
                                        "tool_input": {"pattern": "x", "dir_path": "src", "include_pattern": ".env*"}})
    assert grep["tool_name"] == "Grep" and grep["tool_input"] == {"pattern": "x", "path": "src", "glob": ".env*"}
    (shell,) = _normalized(monkeypatch, {"tool_name": "run_shell_command", "cwd": "/r",
                                         "tool_input": {"command": "ls", "dir_path": "sub"}})
    assert shell["tool_name"] == "Bash" and shell["tool_input"]["command"] == "ls" and shell["cwd"].endswith("sub")
    (spawn,) = _normalized(monkeypatch, {"tool_name": "dev-agent", "tool_input": {"objective": "SDDA-INSTANCE: x"}})
    assert _hook.agent_of(spawn) == "dev-agent" and "SDDA-INSTANCE: x" in spawn["tool_input"]["prompt"]
    reads = _normalized(monkeypatch, {"tool_name": "read_many_files", "tool_input": {"include": ["a.md", "**/.env"]}})
    assert [r["tool_name"] for r in reads] == ["Read", "Grep"]


@pytest.mark.skipif(shutil.which("bash") is None and shutil.which("sh") is None, reason="shell POSIX requis")
def test_the_wired_hooks_start_and_refuse_a_forged_gate_report() -> None:
    root = hb.ROOT
    report = hooks_selfcheck.run(root, root / ".gemini" / "settings.json")
    assert report.ok, [e.message for e in report.errors]
    checks = [c for c in report.data["checks"] if c["harness"] == "gemini-cli"]
    assert any(c["code"] == 2 for c in checks) and all(c["code"] == c["expected"] for c in checks)


def test_the_hook_denies_through_run_when_translated(monkeypatch: pytest.MonkeyPatch) -> None:
    def judge(_root, data):
        return _hook.DENY if data["tool_name"] == "Edit" else _hook.ALLOW

    monkeypatch.setenv(_hook.HARNESS_ENV, "gemini-cli")
    monkeypatch.setattr(_hook.sys, "argv", ["pytest"])
    monkeypatch.setattr(_hook.sys, "stdin", io.StringIO(json.dumps(
        {"tool_name": "replace", "tool_input": {"file_path": "x"}, "cwd": "."})))
    assert _hook.run("h", judge) == _hook.DENY
