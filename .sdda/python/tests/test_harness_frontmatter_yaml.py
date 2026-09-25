"""Le frontmatter des façades Claude se relit en YAML STRICT.

`yaml_mini` relit les sources avec tolérance ; Claude Code, non. Une
description écrite sans guillemets qui porte `Profile: poc` rendait l'en-tête
invalide, et le harnais écartait l'agent en silence : `dev-app` et
`review-rag` n'existaient pas pour lui, redémarrage ou pas.

La vérification tient en stdlib : un scalaire émis est soit un identifiant nu
(`YAML_PLAIN_RE`, hors mots que YAML retype), soit du JSON — et JSON est du
YAML. PyYAML, s'il est là, rejoue la lecture pour de vrai ; s'il manque, la
règle stdlib suffit et le test ne se saute pas : c'est en CI, sans PyYAML,
que ce garde-fou doit tenir.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / ".sdda" / "python"))

from sdda_admin.harness_build import (  # noqa: E402
    YAML_PLAIN_RE,
    YAML_RETYPED,
    frontmatter_and_body,
    yaml_scalar,
)

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - la règle stdlib porte le test
    yaml = None


def _is_strict_scalar(emitted: str) -> bool:
    if YAML_PLAIN_RE.match(emitted) and emitted.lower() not in YAML_RETYPED:
        return True
    try:
        json.loads(emitted)
        return True
    except ValueError:
        return False


@pytest.mark.parametrize(
    "value",
    [
        "construit tout quand `Profile: poc`.",
        "un commentaire # qui n'en est pas un",
        "- commence par un tiret",
        "`backticks` en tête",
        "finit par deux-points:",
        "",
        "texte simple, sans piège",
        "no",
        "2026-09-25",
        "1.0",
        "ligne\nsuivante",
        "sonnet",
        "dev-app",
    ],
)
def test_yaml_scalar_is_plain_identifier_or_json(value: str) -> None:
    emitted = yaml_scalar(value)
    assert _is_strict_scalar(emitted), emitted
    # Un identifiant nu reste nu (pas de bruit de guillemets dans les façades),
    # tout le reste revient intact par JSON.
    if YAML_PLAIN_RE.match(value) and value.lower() not in YAML_RETYPED:
        assert emitted == value
    else:
        assert json.loads(emitted) == value
    if yaml is not None:
        assert yaml.safe_load(f"k: {emitted}") == {"k": value}


def test_yaml_scalar_keeps_lists_as_json() -> None:
    tools = ["Read", "Write"]
    assert json.loads(yaml_scalar(tools)) == tools


def test_frontmatter_survives_a_dash_rule_inside_a_value() -> None:
    text = '---\nname: x\ndescription: "a --- b"\n---\ncorps\n'
    meta, body = frontmatter_and_body(text)
    assert meta.get("name") == "x" and body == "corps\n"


FACADES = sorted((ROOT / ".claude" / "agents").glob("*.md")) + sorted(
    (ROOT / ".claude" / "commands").glob("*.md")
)


@pytest.mark.parametrize("path", FACADES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_generated_facade_frontmatter_is_strict_yaml(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name} : pas de frontmatter"
    front = text.split("\n---\n", 1)[0].removeprefix("---\n")
    seen: dict[str, str] = {}
    for line in front.splitlines():
        key, sep, value = line.partition(": ")
        assert sep, f"{path.name} : ligne d'en-tête sans `: ` — {line!r}"
        assert _is_strict_scalar(value), f"{path.name} : `{key}` n'est ni identifiant nu ni JSON — {value!r}"
        seen[key] = value
    assert seen.get("name") and seen.get("description"), path.name
    if yaml is not None:
        meta = yaml.safe_load(front)
        assert isinstance(meta, dict) and meta.get("name") == seen["name"].strip('"')
