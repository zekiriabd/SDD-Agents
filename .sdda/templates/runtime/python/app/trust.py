"""Frontière de confiance côté produit — le pendant applicatif de `data/trust.py`.

`data/trust.py` enveloppe ce qui sort d'une SOURCE de données. Ce module
enveloppe tout le reste : l'entrée de l'utilisateur, la réponse d'un outil, un
document retrouvé, le message d'un tiers. Le point commun est la seule règle qui
compte : **le texte d'un tiers est une donnée, jamais une instruction** (P8).

Les conventions ne sont pas réinventées ici — elles sont IMPORTÉES de la couche
données quand elle est générée (`.data.trust`), et seulement recopiées à
l'identique quand elle ne l'est pas (un projet sans `declared-sources` n'a pas de
paquet `data/`). Deux balises différentes pour la même frontière rendraient la
suite d'injection à moitié aveugle : elle vérifierait une graphie et le modèle en
verrait deux.

Ce que le balisage fait, et ce qu'il ne fait pas
------------------------------------------------
Il ne « protège » pas à lui seul : un modèle peut suivre une instruction malgré
la balise. Il rend la frontière **visible** dans le prompt, et il donne à la
suite L8 quelque chose à vérifier. La vraie défense reste structurelle : un
outil destructif et une entrée non maîtrisée ne cohabitent pas dans le même
agent (`tools/registry.py`).

Le type `Untrusted`
-------------------
`Untrusted` est un `NewType` sur `str` : mypy refuse de le passer là où un `str`
de confiance est attendu — en particulier à la construction d'un message
système. C'est un contrôle statique, donc absent au runtime ; `system_text()`
ajoute le contrôle dynamique qui manque, parce qu'un projet généré sans
`mypy --strict` vert n'a plus aucune des deux moitiés.
"""
from __future__ import annotations

from typing import NewType

try:  # pragma: no cover - dépend de la génération de la couche données
    # La couche données est la source des conventions quand elle existe :
    # importer plutôt que recopier évite qu'une révision de l'échappement ne
    # s'applique qu'à la moitié des textes non maîtrisés.
    from .data.trust import WRAPPER, wrap_untrusted as _wrap_untrusted
except Exception:  # ImportError, ou paquet `data/` non généré
    WRAPPER = '<untrusted source="{source}" field="{field}">\n{value}\n</untrusted>'

    _ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))

    def _wrap_untrusted(value: object, *, source: str, field: str) -> str:
        text = "" if value is None else str(value)
        for needle, replacement in _ESCAPES:
            text = text.replace(needle, replacement)
        return WRAPPER.format(source=source, field=field, value=text)


#: Tout texte dont l'origine n'est pas le code ni un prompt revu. Le type est
#: la documentation exécutable de cette origine : une fonction qui accepte
#: `Untrusted` annonce qu'elle a prévu le cas hostile.
Untrusted = NewType("Untrusted", str)


class TrustViolation(Exception):
    """Du texte non maîtrisé a atteint un emplacement de confiance.

    Levée plutôt que journalisée : à ce stade, le prompt système contient déjà
    ce que l'attaquant a écrit, et continuer reviendrait à exécuter l'attaque
    en la notant dans un fichier que personne ne lit.
    """


def untrusted(value: object) -> Untrusted:
    """Marque une valeur comme non maîtrisée. C'est la porte d'entrée du type."""
    return Untrusted("" if value is None else str(value))


def is_untrusted(value: object) -> bool:
    """Un texte porte-t-il déjà l'enveloppe ?

    Sert à ne pas envelopper deux fois : une double enveloppe ne double pas la
    sûreté, elle brouille la lecture du prompt et gonfle les tokens.
    """
    return isinstance(value, str) and value.lstrip().startswith("<untrusted ")


def wrap(value: object, *, source: str, field: str = "text",
         max_chars: int | None = 8000) -> str:
    """Enveloppe un texte de tiers, en le tronquant à une taille déclarée.

    La troncature appartient ici et pas à l'appelant : un document de 400 Ko
    inséré dans un prompt ne produit pas une erreur, il produit une facture et
    une réponse qui ignore le bas du contexte. La coupure est annoncée dans le
    texte même, pour que le modèle sache qu'il lit un extrait — un extrait pris
    pour un tout est une réponse fausse rendue avec assurance.
    """
    text = "" if value is None else str(value)
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars] + f"\n… [tronqué à {max_chars} caractères]"
    return _wrap_untrusted(text, source=source, field=field)


def system_text(value: object) -> str:
    """Le texte autorisé à devenir un message SYSTÈME. Refuse l'enveloppe.

    Le contrôle porte sur l'enveloppe et non sur le type : `Untrusted` disparaît
    à l'exécution (`NewType`), alors que la balise, elle, est dans la chaîne. Un
    texte déjà enveloppé qui remonte jusqu'ici signale que quelqu'un a concaténé
    un document retrouvé au prompt système — exactement l'injection indirecte
    que la suite L8 cherche.
    """
    text = "" if value is None else str(value)
    if is_untrusted(text):
        raise TrustViolation(
            "du texte non maîtrisé atteint le message système : il serait lu comme une "
            "consigne. Le passer en message utilisateur ou en résultat d'outil, enveloppé")
    return text
