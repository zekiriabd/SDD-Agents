"""Les combos C1 des cinq langages passent le preflight de stack — pas seulement la combo Python.

Au premier essai, les quatre combos non Python étaient refusées : le contrôle
`VectorStoreConnection.Mode: same-as-database` ne connaissait que le nom
`pgvector`, alors que `pgvector-dotnet`, `pgvector-node` et `pgvector-jvm` sont
la même extension PostgreSQL lue par un autre pilote.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdda_hooks import preflight_stack_combo as psc

SDDA = Path(__file__).resolve().parents[2]
MATRIX = json.loads((SDDA / "registry" / "compatibility.matrix.json").read_text(encoding="utf-8-sig"))


def test_every_pgvector_variant_lives_in_the_business_database() -> None:
    variants = {p.stem for p in (SDDA / "stacks" / "vectorstore").glob("pgvector*.md")}
    assert variants and variants <= psc.EMBEDDED_STORES


@pytest.mark.parametrize("language", ["python", "csharp", "typescript", "kotlin", "java"])
def test_each_language_has_a_bootstrap_combo_with_rag(language: str) -> None:
    combos = [c for c in MATRIX["combos"] if c.get("language") == language and c.get("bootstrapId")]
    assert any(c.get("rag") not in (None, "none") and c.get("vectorstore") not in (None, "none") for c in combos), \
        f"aucune combo de bootstrap {language} avec RAG"
