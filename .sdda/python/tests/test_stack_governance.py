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


# ---------------------------------------------------------------------------
# C8 — la matrice compte ce que le disque porte, et le bootstrap la lit
# ---------------------------------------------------------------------------
MATRIX = SDDA / "registry" / "compatibility.matrix.json"
_LANGUAGES_RE = re.compile(r"^Languages:\s*(.+)$", re.M)


def _matrix() -> dict:
    return json.loads(MATRIX.read_text(encoding="utf-8-sig"))


def _fiches_by_language() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in sorted((SDDA / "stacks").rglob("*.md")):
        match = _LANGUAGES_RE.search(path.read_text(encoding="utf-8-sig"))
        rel = path.relative_to(SDDA / "stacks").as_posix().removesuffix(".md")
        for lang in ([t.strip() for t in match.group(1).split(",")] if match else ["?"]):
            out.setdefault("neutral" if lang == "*" else lang, []).append(rel)
    return out


def test_language_coverage_is_the_disk_not_a_prose_counter() -> None:
    """La prose disait 13 fiches Python et 4 C# quand le disque en portait 15 et 5."""
    declared = {k: sorted(v["fiches"]) for k, v in _matrix()["languageCoupling"]["coverage"].items()
                if isinstance(v, dict)}
    assert declared == {k: sorted(v) for k, v in _fiches_by_language().items()}


#: Champ de combo -> répertoire de `stacks/` où sa fiche doit exister.
COMBO_FIELD_DIRS = {
    "language": "lang", "framework": "framework", "orchestration": "orchestration", "rag": "rag",
    "vectorstore": "vectorstore", "embedding": "embedding", "rerank": "rerank", "dataaccess": "dataaccess",
    "tools": "tools", "memory": "memory", "eval": "eval", "observability": "observability",
    "serving": "serving", "archi": "archi", "backend": "backend", "guardrails": "guardrails",
}
#: `none` n'a de fiche que là où l'absence est une décision documentée.
NONE_HAS_A_FICHE = {"rag", "rerank", "dataaccess"}


@pytest.mark.parametrize("combo", _matrix()["combos"], ids=lambda c: c["id"])
def test_every_combo_component_has_a_fiche(combo: dict) -> None:
    """`repository-tools` figurait dans C1 parmi les OUTILS, sans fiche et dans la mauvaise catégorie."""
    missing = []
    for field_name, directory in COMBO_FIELD_DIRS.items():
        values = combo.get(field_name)
        for value in (values if isinstance(values, list) else [values]):
            if value in (None, "") or (value == "none" and field_name not in NONE_HAS_A_FICHE):
                continue
            if not (SDDA / "stacks" / directory / f"{value}.md").is_file():
                missing.append(f"{directory}/{value}")
    assert not missing, f"combo {combo['id']} : composants sans fiche {missing}"


def test_bootstrap_offers_exactly_the_matrix_combos() -> None:
    """Une seule définition : le menu du bootstrap EST la matrice."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import bootstrap as bs

    expected = {c["bootstrapId"] for c in _matrix()["combos"] if c.get("bootstrapId")}
    assert set(bs.COMBOS) == expected
    c1 = next(c for c in _matrix()["combos"] if c["id"] == "C1")
    assert bs.COMBOS["c1"].orchestration == c1["orchestration"][0]
    assert bs.COMBOS["c1"].tools == c1["tools"]
