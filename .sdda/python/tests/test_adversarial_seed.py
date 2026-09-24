"""Le jeu d'amorce adversarial fourni par le framework — conforme, couvrant, déterministe.

G7 rejouait un jeu versionné qui ne venait que d'un LLM (`qa-evals`) : ce que le
système devait refuser était écrit par un modèle, et un modèle n'écrit pas
spontanément l'attaque qu'il ne sait pas repérer. L'amorce
(`.sdda/templates/datasets/adversarial-seed.jsonl`) est écrite une fois, par des
humains, et `qa-evals` la copie puis l'étend. Ce test garantit qu'elle reste
utilisable telle quelle : chaque ligne passe le schéma des datasets, chaque
famille d'attaque est représentée, et chaque attendu est vérifiable sans juge.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from sdda_lib import paths
from sdda_lib.jsonschema_mini import SchemaValidator

SEED = paths.FRAMEWORK_SDDA_DIR / "templates" / "datasets" / "adversarial-seed.jsonl"
SCHEMA = paths.FRAMEWORK_SDDA_DIR / "templates" / "golden-set.schema.json"

#: Les familles que `qa-evals.md` STEP 6 exige de couvrir.
REQUIRED_FAMILIES = {
    "direct-injection", "indirect-injection", "tool-injection", "exfiltration",
    "privilege-escalation", "tool-abuse", "authorization-crossing", "budget-exhaustion",
    "persona-jailbreak",
}


def _items() -> list[dict]:
    lines = [line for line in SEED.read_text(encoding="utf-8").split("\n") if line.strip()]
    return [json.loads(line) for line in lines]


def test_every_seed_item_conforms_to_the_dataset_schema() -> None:
    validator = SchemaValidator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    items = _items()
    assert len(items) >= 30
    problems = {item["id"]: validator.validate(item) for item in items}
    assert {k: v for k, v in problems.items() if v} == {}


def test_ids_are_unique_and_the_families_are_all_covered() -> None:
    items = _items()
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids))
    families = Counter(item["adversarial"]["family"] for item in items)
    assert REQUIRED_FAMILIES <= set(families), REQUIRED_FAMILIES - set(families)
    # L'injection INDIRECTE est la voie d'attaque qui compte (injection-detection.md §2.2).
    indirect = [i for i in items if i["adversarial"]["vector"] in ("retrieved_document", "tool_output", "mcp_server")]
    assert len(indirect) >= 8


def test_every_expected_outcome_is_checkable_without_a_judge() -> None:
    """Un attendu vérifiable par `regex`/`trajectory` : un canari interdit, ou une borne déclarée."""
    for item in _items():
        adv = item["adversarial"]
        observable = adv.get("forbidden_observables") or []
        bound = (item.get("expected_trajectory") or {}).get("expected_bound_behavior")
        assert observable or bound, item["id"]
        assert item["metadata"]["criticality"] == "critical"
        assert item["metadata"]["source_ref"] == "sdda:adversarial-seed-v1"
        # Le canari que l'attaque demande d'écrire est bien porté par la charge.
        for canary in (o for o in observable if o.startswith("SDDA-CANARY-")):
            payload = json.dumps(item["input"], ensure_ascii=False)
            assert canary in payload or "base64" in payload, (item["id"], canary)


def test_the_seed_is_deterministic_and_carries_no_real_pii() -> None:
    """Aucune donnée réelle : le jeu est versionné dans le framework, donc public."""
    text = SEED.read_text(encoding="utf-8")
    assert "@" not in text.replace("<untrusted", "")          # pas d'e-mail
    assert all(item["metadata"]["pii_status"] == "none" for item in _items())
    assert text.endswith("\n")
