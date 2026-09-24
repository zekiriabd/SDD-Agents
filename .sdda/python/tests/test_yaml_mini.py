"""`yaml_mini` — le parseur sur lequel reposent la matrice d'ownership et les hooks.

Il lit `loader.yml` : une ligne de `forbidden_writes` perdue ici est un interdit
qu'aucun hook n'applique, sans qu'aucune erreur ne le dise. Deux familles de
tests :

- les constructions RÉELLEMENT employées par les fichiers de config du dépôt,
  avec des attentes écrites (tournent partout, sans dépendance) ;
- les six fichiers eux-mêmes, confrontés à PyYAML quand il est installé : un
  écart de lecture entre les deux est un défaut du mini-parseur, par définition.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sdda_lib import yaml_mini
from sdda_lib.yaml_mini import YamlMiniError, parse

SDDA = Path(__file__).resolve().parents[2]
CONFIG_FILES = [
    "loader.yml", "agent-bounds.yaml", "INVARIANTS.yml", "capability-matrix.yml",
    "config.base.yml", "registry/architecture-requirements.yml",
]


# ---------------------------------------------------------------------------
# Constructions employées par loader.yml
# ---------------------------------------------------------------------------
def test_nested_mappings_lists_and_inline_comments() -> None:
    doc = parse(
        "version: \"0.1.0-design\"\n"
        "dev-agent:\n"
        "  model_tier: deep\n"
        "  budget_bytes: 320000      # recalibré\n"
        "  writes:\n"
        "    - workspace/src/**/agents/{agent}/**                # répertoire DISJOINT\n"
        "  forbidden_writes:\n"
        "    - workspace/pipeline/datasets/**     # [DATASET_OWNERSHIP_VIOLATION]\n"
    )
    assert doc == {"version": "0.1.0-design", "dev-agent": {
        "model_tier": "deep", "budget_bytes": 320000,
        "writes": ["workspace/src/**/agents/{agent}/**"],
        "forbidden_writes": ["workspace/pipeline/datasets/**"]}}


def test_an_apostrophe_inside_a_word_does_not_swallow_the_comment() -> None:
    """Le défaut qui faisait écrire « l index » aux auteurs de loader.yml."""
    doc = parse("reads:\n  - workspace/pipeline/missions/{other}-*.md   # autres MISSIONs — l'index de CAP\n"
                "why: l'agent lit # et ceci est un commentaire\n")
    assert doc["reads"] == ["workspace/pipeline/missions/{other}-*.md"]
    assert doc["why"] == "l'agent lit"


def test_quoted_scalars_keep_their_hash_and_unescape() -> None:
    doc = parse("a: 'x # pas un commentaire'\nb: \"guillemet \\\" et barre \\\\\"\nc: 'l''agent'\n")
    assert doc == {"a": "x # pas un commentaire", "b": 'guillemet " et barre \\', "c": "l'agent"}


def test_list_of_mappings_with_block_scalar() -> None:
    doc = parse(
        "shared_writes:\n"
        "  - path: workspace/pipeline/decisions/ADR-*.md\n"
        "    agents: [architect-topology, architect-data]\n"
        "    mode: disjoint-by-timestamp\n"
        "    why: |\n"
        "      ligne un\n"
        "\n"
        "      # ceci est du CONTENU, pas un commentaire\n"
        "        ligne plus indentée\n"
        "  - path: x\n"
    )
    entry = doc["shared_writes"][0]
    assert entry["agents"] == ["architect-topology", "architect-data"]
    assert entry["why"] == "ligne un\n\n# ceci est du CONTENU, pas un commentaire\n  ligne plus indentée\n"
    assert doc["shared_writes"][1] == {"path": "x"}


def test_block_scalar_chomping_and_folding() -> None:
    assert parse("a: |-\n  x\n  y\n\nb: 1\n") == {"a": "x\ny", "b": 1}
    assert parse("a: |+\n  x\n\n\nb: 1\n") == {"a": "x\n\n\n", "b": 1}
    assert parse("a: >\n  un\n  deux\n\n  trois\n") == {"a": "un deux\ntrois\n"}
    assert parse("a: >-\n  un\n    indenté\n  deux\n") == {"a": "un\n  indenté\ndeux"}


def test_flow_mapping_on_the_next_line_like_agent_bounds() -> None:
    doc = parse("po-elicitor:\n  { tier_default: balanced, tier_floor: fast, tier_ceiling: deep }\n")
    assert doc == {"po-elicitor": {"tier_default": "balanced", "tier_floor": "fast", "tier_ceiling": "deep"}}


def test_nested_flow_and_typed_scalars() -> None:
    doc = parse("m: {a: [1, 2.5, true], b: null, c: 'x, y', d: on}\n")
    assert doc == {"m": {"a": [1, 2.5, True], "b": None, "c": "x, y", "d": "on"}}


def test_a_list_at_the_same_indent_as_its_key() -> None:
    assert parse("reads:\n- a\n- b\nnext: 1\n") == {"reads": ["a", "b"], "next": 1}


def test_quoted_and_numeric_keys() -> None:
    assert parse("\"G0\": mission\n'x y': 2\n2026-09-22: date\n") == {"G0": "mission", "x y": 2, "2026-09-22": "date"}


def test_duplicate_keys_are_an_error_not_a_silent_override() -> None:
    """Un second `dev-agent:` remplaçait les interdits du premier, sans bruit."""
    with pytest.raises(YamlMiniError, match="dupliquée"):
        parse("dev-agent:\n  writes: [a]\ndev-agent:\n  writes: [b]\n")


def test_bad_indentation_fails_loudly() -> None:
    with pytest.raises(YamlMiniError):
        parse("a:\n  b: 1\n    c: 2\n")


def test_crlf_and_bom_are_tolerated() -> None:
    assert parse("\ufeffa: 1\r\nb:\r\n  - x\r\n") == {"a": 1, "b": ["x"]}


def test_parse_mapping_refuses_a_root_list() -> None:
    with pytest.raises(YamlMiniError):
        yaml_mini.parse_mapping("- a\n- b\n")


# ---------------------------------------------------------------------------
# Les fichiers réels : non vides, et identiques à PyYAML quand il est là
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", CONFIG_FILES)
def test_real_config_files_parse(name: str) -> None:
    data = yaml_mini.parse_mapping((SDDA / name).read_text(encoding="utf-8"))
    assert data, name


def test_loader_keeps_every_forbidden_write() -> None:
    """Le point porteur : chaque ligne `- …` d'un `forbidden_writes:` arrive à la matrice."""
    text = (SDDA / "loader.yml").read_text(encoding="utf-8")
    loader = yaml_mini.parse_mapping(text)
    declared = sum(len(v.get("forbidden_writes") or []) for v in loader.values() if isinstance(v, dict))
    # Comptage indépendant, ligne à ligne, sur le texte brut.
    raw, inside = 0, False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("forbidden_writes:"):
            inside = True
            continue
        if inside:
            if stripped.startswith("- "):
                raw += 1
            elif stripped and not stripped.startswith("#"):
                inside = False
    assert declared == raw > 0


@pytest.mark.parametrize("name", CONFIG_FILES)
def test_real_config_files_match_pyyaml(name: str) -> None:
    yaml = pytest.importorskip("yaml")
    text = (SDDA / name).read_text(encoding="utf-8")
    assert yaml_mini.parse(text) == yaml.safe_load(text)
