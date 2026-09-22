"""Chaque combo du bootstrap produit un STACK.md chargeable et cohérent.

Le bootstrap est la première chose qu'un utilisateur exécute. Une combo qui
génère un `STACK.md` activant une fiche absente ne produit pas une erreur : elle
produit un projet où les agents travaillent **sans** le mapping de couches, sans
les idiomes et sans les versions épinglées. Ils inventent, et le résultat
compile parfois — ce qui est pire.

Deux défauts réels que ces tests ont attrapés à l'écriture :

- `vectorstore/none.md` et `embedding/none.md` n'existent pas, et toutes les
  combos sans RAG les activaient ;
- la combo `c1-api` exposait en HTTP une base en `view-per-agent` avec
  `ApiAuthMode: none` — le filtrage par tenant s'appuyait sur une identité que
  personne n'établissait.

Les tests passent par `build_stack_md`, le **vrai** code du bootstrap. Une
première version du harnais recopiait la substitution et rendait six verts sur
un gabarit dont les placeholders n'étaient pas remplacés : un harnais qui teste
sa propre copie ne teste rien.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bootstrap as bs  # noqa: E402

PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}")
COMBO_IDS = sorted(bs.COMBOS)


@pytest.fixture(params=COMBO_IDS)
def rendered(request, tmp_path: Path) -> tuple[str, Path, str]:
    """`(combo_id, racine de projet, texte du STACK.md)` pour chaque combo."""
    cid = request.param
    text = bs.build_stack_md("Demo", bs.COMBOS[cid], {})
    stack = tmp_path / "workspace" / "stack"
    stack.mkdir(parents=True)
    (stack / "STACK.md").write_text(text, encoding="utf-8")
    return cid, tmp_path, text


def _run(script: str, root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / script), "--root", str(root), *extra],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", stdin=subprocess.DEVNULL,
    )


def test_every_placeholder_is_substituted(rendered) -> None:
    cid, _, text = rendered
    leftovers = sorted(set(PLACEHOLDER_RE.findall(text)))
    assert not leftovers, (
        f"combo `{cid}` : {leftovers} non substitué(s). Un placeholder qui survit "
        "est lu comme une valeur littérale par la config en 3 couches — et la "
        "valeur littérale `{{ApiAuthMode}}` n'est pas `none`, donc aucun contrôle "
        "de sécurité ne se déclenche."
    )


def test_every_combo_is_loadable(rendered) -> None:
    """Aucune ligne activée ne désigne une fiche absente."""
    cid, root, _ = rendered
    proc = _run(".sdda/python/sdda_hooks/preflight_stack_combo.py", root)
    assert proc.returncode == 0, (
        f"combo `{cid}` : STACK.md non chargeable\n{proc.stderr}"
    )


def test_every_combo_has_a_coherent_deliverable(rendered) -> None:
    """Livrable x langage x surface x identité s'accordent."""
    cid, root, _ = rendered
    proc = _run(".sdda/python/sdda_scripts/validate_packaging.py", root, "--no-report")
    assert proc.returncode == 0, (
        f"combo `{cid}` : packaging incohérent\n{proc.stdout}{proc.stderr}"
    )


def test_an_api_combo_never_ships_without_caller_identity() -> None:
    """Une combo qui livre une API réseau déclare comment elle authentifie.

    Un défaut livré avec `ApiAuthMode: none` est une décision prise par le
    framework au nom de l'utilisateur, et prise dans le mauvais sens : le
    provisoire d'un POC devient la configuration de production.
    """
    offenders = [cid for cid, c in bs.COMBOS.items()
                 if c.deliverable in ("backend-api", "mcp-server") and c.api_auth == "none"]
    assert not offenders, (
        f"combo(s) livrant une API sans authentification : {offenders}. "
        "Choisir `api_auth` (api-key, oauth2, azure-ad, mtls) — ou assumer "
        "explicitement un acteur anonyme dans la MISSION."
    )


def test_a_combo_without_rag_activates_no_retrieval_stack() -> None:
    """`rag: none` n'active ni vector store ni modèle d'embedding.

    `vectorstore/none.md` et `embedding/none.md` n'existent pas : les activer
    produisait deux lignes qui ne chargent rien, en silence.
    """
    for cid, combo in bs.COMBOS.items():
        if combo.rag != "none":
            continue
        text = bs.build_stack_md("Demo", combo, {})
        active = [l.strip() for l in text.splitlines()
                  if l.strip().startswith("- .sdda/stacks/vectorstore/")
                  or l.strip().startswith("- .sdda/stacks/embedding/")]
        assert not active, f"combo `{cid}` (rag=none) active {active}"
