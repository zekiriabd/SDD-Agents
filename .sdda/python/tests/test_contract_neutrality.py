"""Neutralité framework — invariant `framework-neutral-contracts` (P11).

`validate_ir.py` garde l'IR **d'un projet**. Ce test garde la couche qui produit
les IR : les gabarits, les schémas et les prompts d'agents de `.sdda/`. La
distinction est le tout du sujet — un `StateGraph` glissé dans
`templates/agent-contract.template.md` ne fait échouer aucun projet, il les
contamine tous, un par un, et chaque échec ressemble alors à une faute de
l'utilisateur.

Ce qui rend `STACK.md` réellement déclaratif est cette séparation : changer de
framework doit invalider le **code**, jamais la **spécification**. Le jour où un
gabarit nomme LangGraph, la spécification est soudée au framework et la promesse
multi-framework devient une affiche.

Un seul répertoire a le droit de nommer un framework : `.sdda/stacks/`. C'est sa
fonction — une fiche de stack qui ne nommerait pas sa stack ne servirait à rien.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from sdda_scripts.validate_ir import _API_RE, _NAME_RE  # noqa: E402

SDDA = Path(__file__).resolve().parents[2]

#: Ce qui doit rester neutre, et pourquoi chacun y est.
NEUTRAL_TREES = {
    "templates": "les gabarits produisent TOUS les contrats : une fuite ici contamine chaque projet",
    "registry": "les registres machine décrivent le QUOI ; la matrice de compatibilité est l'exception traitée plus bas",
    "agents": "un Developer Agent qui nomme une API écrit des contrats soudés à elle",
    "rules": "une règle opérationnelle vaut pour toutes les stacks, sinon ce n'est pas une règle",
}

#: Fichiers légitimement non neutres, avec la raison. Toute autre exception doit
#: être discutée, pas ajoutée : c'est la liste qui fait la valeur du test.
ALLOWED = {
    # La matrice de compatibilité EST la carte des frameworks : elle les nomme
    # pour dire lesquels sont validés ensemble. Les nommer est sa fonction.
    "registry/compatibility.matrix.json",
    # Le registre de patterns cite les frameworks qui implémentent chaque
    # pattern — c'est un renvoi vers stacks/, pas une dépendance de contrat.
    "registry/patterns.registry.json",
    # Le gabarit de STACK.md liste les stacks activables : c'est le fichier par
    # lequel on CHOISIT un framework.
    "templates/STACK.md.template",
    # Le catalogue de libs déclare des paquets, donc des noms de frameworks.
    "templates/libs-catalog.schema.json",
    # Le schéma de config nomme les valeurs admissibles des clés de stack.
    "templates/project-config.schema.json",
}

#: Extensions inspectées. Le binaire et les images n'ont pas de contrat à trahir.
SUFFIXES = (".md", ".template", ".json", ".yml", ".yaml")

#: Deux exemptions, au PARAGRAPHE et non au fichier. L'exemption par fichier
#: serait plus simple et c'est précisément son défaut : elle aveugle tout le
#: reste du fichier, définitivement, pour une seule mention légitime.
#:
#: Le paragraphe, et non la ligne, parce que la prose se replie : « Tu ne nommes
#: aucune API de framework. La connaissance de LangGraph… » porte l'interdiction
#: sur une ligne et son objet sur la suivante. Un découpage à la ligne
#: signalerait la seconde comme une fuite — un faux rouge, qu'on apprend à
#: ignorer, et qui emporte les vrais avec lui.
#:
#: 1. **L'interdiction qui cite ce qu'elle interdit.** « Aucun `StateGraph` » est
#:    la formulation la plus claire de la règle ; exiger qu'elle s'écrive sans
#:    nommer son objet produirait une consigne que personne ne comprend.
_PROHIBITION_RE = re.compile(
    r"\b(aucun|aucune|ni\b|jamais|neutre|interdit|sans nom)", re.IGNORECASE)
#: 2. **Le choix de stack déclaré.** `- framework: <ex. langgraph>` dans un
#:    gabarit de MISSION désigne une stack à activer, pas une API à appeler.
#:    C'est exactement la distinction que P11 protège : le QUOI peut nommer la
#:    stack retenue, il ne peut pas nommer ses types.
_STACK_CHOICE_RE = re.compile(r"^\s*[-*]?\s*\w+:\s*<ex\.")


def _leaks(text: str) -> list[str]:
    """Les identifiants de framework trouvés, dédupliqués, ordre stable.

    Les paragraphes exemptés (interdiction, choix de stack) sont retirés avant
    détection, pas après : filtrer les jetons plutôt que le texte laisserait
    passer une vraie fuite qui partage son nom avec une mention légitime.

    Limite assumée : un paragraphe qui énonce une interdiction ET commet une
    fuite passe. C'est le prix du faux rouge évité, et il se paie en revue.
    """
    found: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        if _PROHIBITION_RE.search(paragraph):
            continue
        for line in paragraph.splitlines():
            if _STACK_CHOICE_RE.match(line):
                continue
            for regex in (_API_RE, _NAME_RE):
                for match in regex.finditer(line):
                    token = match.group(1)
                    if token not in found:
                        found.append(token)
    return found


def _neutral_files() -> list[Path]:
    out: list[Path] = []
    for tree in NEUTRAL_TREES:
        for path in sorted((SDDA / tree).rglob("*")):
            if not path.is_file() or path.suffix not in SUFFIXES:
                continue
            if path.relative_to(SDDA).as_posix() in ALLOWED:
                continue
            out.append(path)
    return out


@pytest.mark.parametrize("path", _neutral_files(), ids=lambda p: p.relative_to(SDDA).as_posix())
def test_neutral_tree_names_no_framework(path: Path) -> None:
    """Aucun fichier de la couche neutre ne nomme un framework ni une de ses API."""
    rel = path.relative_to(SDDA).as_posix()
    tree = rel.split("/", 1)[0]
    leaks = _leaks(path.read_text(encoding="utf-8", errors="replace"))
    assert not leaks, (
        f"{rel} nomme {leaks} — {NEUTRAL_TREES[tree]}. "
        "Le framework vit dans .sdda/stacks/framework/ ; la spécification dit QUOI, "
        "la stack dit AVEC QUOI. Si la mention est légitime, l'ajouter à ALLOWED "
        "avec sa raison, jamais en silence."
    )


def test_ir_schema_declares_no_framework_field() -> None:
    """Le schéma d'IR n'a aucun champ qui présuppose un framework.

    Un champ `stateGraphNodes` ou `kernelPlugins` rendrait l'IR non portable au
    niveau de sa STRUCTURE, ce qu'aucune détection de chaîne n'attraperait sur
    un projet donné : le champ serait simplement vide chez les autres.
    """
    schema_path = SDDA / "registry" / "ir.schema.json"
    assert schema_path.is_file(), "ir.schema.json absent — c'est la charnière du multi-framework"
    text = schema_path.read_text(encoding="utf-8-sig")
    leaks = _leaks(text)
    assert not leaks, (
        f"registry/ir.schema.json nomme {leaks} — l'IR est la couche qui rend la génération "
        "multi-framework déterministe ; un champ soudé à un framework la supprime."
    )
    # Le schéma doit rester chargeable : un test de neutralité sur un JSON cassé
    # passerait pour vert faute de contenu à inspecter.
    json.loads(text)


def test_detection_actually_detects() -> None:
    """Le détecteur attrape ce qu'il prétend attraper.

    Sans ce contrôle, une expression régulière cassée rendrait tous les tests
    ci-dessus verts — un faux vert d'autant plus durable qu'il est silencieux.
    """
    assert _leaks("le noeud est un StateGraph compilé") == ["StateGraph"]
    assert _leaks("on passe par LangGraph pour le checkpointing") == ["LangGraph"]
    assert _leaks("le graphe d'état porte les bornes") == []
    # Mot entier uniquement : un faux positif bloquerait un contrat juste.
    assert _leaks("MyStateGraphHelper") == []
