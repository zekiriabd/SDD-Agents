"""Gouvernance par STACK.md : le gabarit dit vrai, et ce qu'il déclare est lu.

Ces tests gardent le lot « configuration » : un gabarit qui décrit un `.env`
à trois endroits différents, un registre qui compte des fiches qui n'existent
plus, une clé qui n'a aucun lecteur — chacun est une phrase que l'utilisateur
croit, et que le code ne tient pas.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from conftest import make_project  # noqa: F401,E402

PYTHON_DIR = Path(__file__).resolve().parents[1]
SDDA = PYTHON_DIR.parent
ROOT = SDDA.parent
TEMPLATE = SDDA / "templates" / "STACK.md.template"


# ---------------------------------------------------------------------------
# C7 — le gabarit parle du workspace v6, et d'un seul `.env`
# ---------------------------------------------------------------------------
#: Chemins de la v5 : `feats/` rangeait topologie et contrats, `proof/` la
#: vérité terrain et les baselines. Un gabarit qui les cite fait écrire
#: l'utilisateur là où plus rien ne lit.
V5_PATHS = ("proof/", "feats/topology", "feats/contracts", "proof/baselines")


@pytest.mark.parametrize("path", [TEMPLATE, ROOT / "bootstrap.py"], ids=["template", "bootstrap"])
def test_no_v5_path_survives(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    found = [p for p in V5_PATHS if p in text]
    assert not found, f"{path.name} cite encore des chemins v5 : {found}"


def test_the_env_lives_in_assets_and_is_copied_by_install_env() -> None:
    """ARCHITECTURE §2.ter : `assets/.env`, copié par `install-env`, jamais « à la racine »."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "racine du projet" not in text, "le `.env` n'est pas à la racine du projet (§2.ter)"
    assert "workspace/assets/.env" in text
    assert "install-env" in text


def test_the_settings_layer_belongs_to_dev_backend() -> None:
    """La couche Settings vit dans `app/`, la coquille de `dev-backend` — pas dans `serving/`."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "`dev-api` les projette" not in text
    assert "`dev-backend` les projette" in text


def test_the_template_counts_four_human_inputs() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "trois fichiers" not in text
    for depot in ("`stack/`", "`feats/`", "`assets/`", "`seed/`"):
        assert depot in text, f"{depot} manque au décompte des entrées humaines"
