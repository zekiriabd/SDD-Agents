"""Deux régressions vues sur le premier run réel de /sdda-full.

1. Le hash d'une spécification ignorait tout sauf l'OS : `Status:` en faisait
   partie. Or `compute_status.py` ÉCRIT cette ligne — une gate verte périmait
   donc son propre rapport dès que l'état dérivé bougeait.
2. Le parseur d'AC gardait les guillemets d'une valeur : `threshold: ">= 0.85"`
   (réflexe YAML) était refusé comme non chiffré, 22 fois sur 22.
"""
from __future__ import annotations

from pathlib import Path

from conftest import make_project, run_main
from sdda_lib import hashing, markdown_io
from sdda_scripts import compute_status, validate_cap, validate_mission

MISSION = "# MISSION: Demo\n\nMISSION ID: 1-Demo\nStatus: Draft\nConfidence: high\n\n## Context\nStatus: not a header\n"


def test_spec_hash_ignores_the_header_status_only() -> None:
    blocked = MISSION.replace("Status: Draft", "Status: Blocked", 1)
    assert hashing.sha256_spec_text(MISSION) == hashing.sha256_spec_text(blocked)
    assert hashing.sha256_text(MISSION) != hashing.sha256_text(blocked)
    # Le corps compte, `Status:` compris quand il y est du contenu.
    assert hashing.sha256_spec_text(MISSION) != hashing.sha256_spec_text(MISSION.replace("not a header", "changed"))
    assert "Status: not a header" in hashing.spec_text(MISSION) and "Status: Draft" not in hashing.spec_text(MISSION)


def test_a_status_rewrite_does_not_stale_g0_nor_the_cap_parent_hash(project: Path) -> None:
    """Le scénario réel : G0 verte, compute_status réécrit `Status:`, rien ne doit se périmer."""
    mission = next((project / "workspace/pipeline/missions").glob("1-*.md"))
    before = validate_mission.parse_mission(markdown_io.read_text(mission), mission).hash
    text = mission.read_text(encoding="utf-8")
    mission.write_text(markdown_io.replace_header_field(text, "Status", "Blocked"), encoding="utf-8", newline="\n")
    after = validate_mission.parse_mission(markdown_io.read_text(mission), mission).hash
    assert before == after
    assert compute_status.current_hash(project, "mission", "1-SupportAssistant") == after
    for cap in (project / "workspace/pipeline/caps").glob("1-*.md"):
        h1 = validate_cap.parse_cap(markdown_io.read_text(cap), cap).hash
        cap.write_text(markdown_io.replace_header_field(cap.read_text(encoding="utf-8"), "Status", "Blocked"),
                       encoding="utf-8", newline="\n")
        assert validate_cap.parse_cap(markdown_io.read_text(cap), cap).hash == h1


def test_g0_stays_green_after_compute_status_writes_the_header(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    assert run_main(validate_mission.main, ["--root", str(root), "--mission", "1"])[0] == 0
    mission = next((root / "workspace/pipeline/missions").glob("1-*.md"))
    mission.write_text(markdown_io.replace_header_field(mission.read_text(encoding="utf-8"), "Status", "Blocked"),
                       encoding="utf-8", newline="\n")
    code, out = run_main(compute_status.main, ["--root", str(root), "--mission", "1", "--json"])
    assert code == 0, out
    assert "STATUS_PINNED_HASH_MOVED" not in out


def test_nested_list_fields_drop_paired_quotes() -> None:
    body = '- AC-1:\n  - metric: accuracy\n  - threshold: ">= 0.85"\n  - grader: \'exact\'\n  - notes: "unbalanced\n'
    fields = markdown_io.parse_nested_list(body)[0]["fields"]
    assert fields["threshold"] == ">= 0.85"
    assert fields["grader"] == "exact"
    assert fields["notes"] == '"unbalanced'


def test_a_quoted_threshold_is_an_evaluable_ac() -> None:
    ac = validate_cap.AcSpec("AC-1", {"metric": "accuracy", "threshold": ">= 0.85",
                                      "dataset": "workspace/pipeline/datasets/golden/x.jsonl", "grader": "exact", "runs": "3"})
    problems, _ = validate_cap.ac_problems(ac, None, "normal")
    assert not [p for p in problems if "threshold" in p]


def test_a_bullet_written_on_several_lines_keeps_its_continuation() -> None:
    """`- **Entrées non maîtrisées** : `a`, `b`\n  `c`, `d`` : les quatre, pas deux."""
    body = ("- **Entrées non maîtrisées** : `message` (client), `claims.description`\n"
            "  (client), `claims.resolution`\n  (conseiller), `orders.delivery_note`\n"
            "- **Traitement** : contenu\n")
    kv = markdown_io.parse_kv_list(body)
    assert markdown_io.split_code_list(kv["Entrées non maîtrisées"]) == [
        "message", "claims.description", "claims.resolution", "orders.delivery_note"]
    assert kv["Traitement"] == "contenu"


def test_a_value_starting_with_a_code_span_is_that_span() -> None:
    assert markdown_io.first_code_span("`workspace/pipeline/datasets/adversarial/a.jsonl` *(obligatoire — invariant)*") \
        == "workspace/pipeline/datasets/adversarial/a.jsonl"
    assert markdown_io.first_code_span("`regex`") == "regex"
    assert markdown_io.first_code_span("**`x`**") == "x"
