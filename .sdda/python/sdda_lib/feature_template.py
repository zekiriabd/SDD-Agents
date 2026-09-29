"""Gabarits conditionnels — un runtime de RÉFÉRENCE, un livrable EFFECTIF.

Ce que ce module défend : *le framework sait tout faire, l'application générée ne
porte que ce que sa spécification exige.* Les runtimes de `templates/runtime/`
sont l'architecture de référence : lecteurs CSV, TSV, JSONL, comptage, filtre
d'identité, fraîcheur, routeur, mémoire… Les copier en entier livrait à une
application « 1 agent, 2 outils, 1 fichier JSON » un lecteur CSV que rien
n'appelle, un `CountRecords` qu'aucun outil n'expose et un moteur de fraîcheur
qu'aucune source ne demande. Du code sans exigence n'est pas gratuit : il se
relit, se teste, se maintient, et il élargit la surface d'attaque.

Deux marqueurs, en commentaire du langage (`//` en C#, `#` en Python), chacun
SEUL sur sa ligne :

    // @sdda-file-if data.format.delimited        (première ligne : le fichier entier)
    // @sdda-if data.staleness                    (un bloc)
    // @sdda-else
    // @sdda-endif

L'expression est une capacité (`data.tool.count`), sa négation (`!data.staleness`)
ou une disjonction (`data.format.csv|data.format.tsv`). Les blocs s'imbriquent.
Les lignes de marqueur disparaissent du rendu : le fichier émis ne garde aucune
trace du mécanisme, et deux rendus du même ensemble de capacités sont identiques
à l'octet — c'est ce que `--check` compare.

Rendu avec TOUTES les capacités (`render(text, ALL)`), un gabarit redonne le
runtime de référence : c'est ainsi que ses tests l'exercent en entier.
"""
from __future__ import annotations

import re
from typing import AbstractSet

#: Sentinelle : « toutes les capacités » — l'architecture de référence complète.
ALL: AbstractSet[str] = frozenset({"*"})

_MARKER_RE = re.compile(r"^\s*(?://|#)\s*@sdda-(file-if|if|else|endif)\b\s*(.*?)\s*$")


class TemplateError(ValueError):
    """Un gabarit mal formé : bloc non fermé, `else` orphelin, expression vide."""


def enabled(expr: str, features: AbstractSet[str]) -> bool:
    """`a|b` : vrai si l'une est active ; `!a` : vrai si `a` ne l'est pas."""
    expr = expr.strip()
    if not expr:
        raise TemplateError("expression de capacité vide")
    if features is ALL or "*" in features:
        # La référence complète : toute capacité est présente, aucune négation ne tient.
        return not all(term.strip().startswith("!") for term in expr.split("|"))
    for term in expr.split("|"):
        term = term.strip()
        if term.startswith("!"):
            if term[1:].strip() not in features:
                return True
        elif term in features:
            return True
    return False


def file_condition(text: str) -> str | None:
    """L'expression de `@sdda-file-if` en tête de fichier, ou None."""
    for line in text.split("\n", 3)[:3]:
        match = _MARKER_RE.match(line)
        if match and match.group(1) == "file-if":
            return match.group(2)
    return None


def wanted(text: str, features: AbstractSet[str]) -> bool:
    """Le fichier doit-il être émis pour cet ensemble de capacités ?"""
    condition = file_condition(text)
    return condition is None or enabled(condition, features)


def render(text: str, features: AbstractSet[str]) -> str:
    """Le gabarit, blocs inactifs retirés et marqueurs effacés."""
    out: list[str] = []
    # Pile de (bloc actif ?, parent actif ?, `else` déjà vu ?).
    stack: list[tuple[bool, bool, bool]] = []
    active = True
    for number, line in enumerate(text.split("\n"), start=1):
        match = _MARKER_RE.match(line)
        if match is None:
            if active:
                out.append(line)
            continue
        kind, expr = match.group(1), match.group(2)
        if kind == "file-if":
            continue
        if kind == "if":
            parent = active
            active = parent and enabled(expr, features)
            stack.append((active, parent, False))
        elif kind == "else":
            if not stack or stack[-1][2]:
                raise TemplateError(f"ligne {number} : `@sdda-else` sans `@sdda-if` ouvert")
            was, parent, _ = stack[-1]
            active = parent and not was
            stack[-1] = (active, parent, True)
        else:  # endif
            if not stack:
                raise TemplateError(f"ligne {number} : `@sdda-endif` sans `@sdda-if` ouvert")
            _, parent, _ = stack.pop()
            active = parent
    if stack:
        raise TemplateError(f"{len(stack)} bloc(s) `@sdda-if` non fermé(s)")
    return "\n".join(out)
