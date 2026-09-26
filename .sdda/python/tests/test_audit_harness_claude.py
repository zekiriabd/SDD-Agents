"""Claude Code — commandes : pas de `name:`, et aucun `$N` que le harnais prendrait pour un argument.

`$0`, `$1`… sont des placeholders d'arguments dans une commande Claude Code
(https://code.claude.com/docs/en/skills) : `coût $0.09` devenait `coût 1.09`
pour `/sdda-full 1`, et un placeholder servi supprimait l'ajout de
`ARGUMENTS: …` — `--resume` n'arrivait jamais au modèle.
"""
from __future__ import annotations

import json
import re

from sdda_admin import harness_build as hb


def _plan() -> dict:
    matrix = hb.load_matrix()
    plan, _counts = hb.build_harness("claude-code", matrix["claude-code"])
    return {p.relative_to(hb.ROOT).as_posix(): c for p, c in plan.files.items()}


def test_commands_carry_no_name_key_and_a_strict_description() -> None:
    commands = {k: v for k, v in _plan().items() if k.startswith(".claude/commands/")}
    assert len(commands) == len(list((hb.SDDA / "commands").glob("*.md")))
    for rel, text in commands.items():
        head = text.split("\n---\n", 1)[0]
        assert "\nname:" not in head, rel
        (line,) = [ln for ln in head.splitlines() if ln.startswith("description:")]
        value = line.partition(":")[2].strip()
        assert hb.YAML_PLAIN_RE.match(value) or json.loads(value), rel


def test_no_unescaped_indexed_placeholder_survives_in_a_command() -> None:
    for rel, text in _plan().items():
        if rel.startswith(".claude/commands/"):
            assert not re.search(r"(?<!\\)\$\d", text), rel


def test_the_escape_is_the_documented_backslash_and_is_idempotent() -> None:
    assert hb.CLAUDE_INDEXED_ARG_RE.sub(r"\\$", "coût $0.09 > $1") == r"coût \$0.09 > \$1"
    assert hb.CLAUDE_INDEXED_ARG_RE.sub(r"\\$", r"déjà \$0.09") == r"déjà \$0.09"
    assert hb.CLAUDE_INDEXED_ARG_RE.sub(r"\\$", "$TAGS $ARGUMENTS") == "$TAGS $ARGUMENTS"


def test_agents_keep_their_model_from_the_tier_table() -> None:
    matrix = hb.load_matrix()
    for rel, text in _plan().items():
        if rel.startswith(".claude/agents/"):
            head = text.split("\n---\n", 1)[0]
            tier = re.search(r"^model_tier: (\S+)$", head, re.M).group(1)
            assert f"\nmodel: {matrix['claude-code'].model_for(tier)}" in head, rel
