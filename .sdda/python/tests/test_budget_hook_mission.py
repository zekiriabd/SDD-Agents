"""Le hook de budget mesure le contexte que l'agent LIRA, pas celui de toutes les missions.

Vu au premier projet réel : le harnais ne transmet que `tool_input.prompt`, le
hook lisait `data["mission"]` (toujours absent), chaque `{n}` s'élargissait à
toutes les missions, et chaque agent de construction était refusé.
"""
from __future__ import annotations

from pathlib import Path

from sdda_hooks import preflight_agent_budget as hook
from sdda_scripts import context_pack as cp


def test_mission_is_read_from_the_prompt_when_the_payload_has_none() -> None:
    data = {"tool_name": "Agent", "tool_input": {"subagent_type": "dev-tools", "prompt": "Tu es dev-tools.\nMISSION : 12\n"}}
    assert hook._mission_and_target(data) == ("12", None)
    assert hook._mission_and_target({"mission": "3", "tool_input": {}})[0] == "3"


def test_tool_caches_and_lockfiles_are_never_context(project: Path) -> None:
    app = project / "workspace/src/SupportAssistant"
    (app / ".mypy_cache").mkdir(parents=True, exist_ok=True)
    (app / ".mypy_cache" / "cache.db").write_bytes(b"x" * 10_000)
    (app / "uv.lock").write_text("x" * 10_000, encoding="utf-8")
    (app / "real.py").write_text("x = 1\n", encoding="utf-8")
    files, _ = cp.expand(project, "workspace/src/**", mission="1", target=None, obj=None)
    names = {p.name for p in files}
    assert "real.py" in names and "cache.db" not in names and "uv.lock" not in names
