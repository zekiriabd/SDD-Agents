"""Grader `regex` — la sortie respecte-t-elle un motif ?

Usage : forme d'une sortie (un identifiant de ticket, une date ISO), présence
d'un marqueur (une balise d'escalade, une citation `doc:…`), absence d'un
fragment interdit (avec `negate: true`).

`expected` est le motif (syntaxe `re` de Python). Par défaut la recherche est
libre (`re.search`) ; `full_match: true` exige que toute la sortie corresponde.
`flags` accepte les noms `IGNORECASE`, `MULTILINE`, `DOTALL`.

Les groupes capturés (positionnels et nommés) sont exposés dans `detail` : un
grader aval (`exact` sur `detail["groups"][0]`) ou un rapport peut s'en servir
sans re-parser la sortie.

Score : 1.0 ou 0.0. Déterministe. Un motif invalide est une erreur de l'item
(`[DATASET_ITEM_INVALID]`), pas un score de 0 : c'est le jeu qu'il faut
corriger, pas le système.
"""
from __future__ import annotations

import re
from typing import Any

from sdda_lib.graders._base import (
    CLS_EXPECTED_INVALID,
    BaseGrader,
    GradeResult,
    as_text,
    config_error,
    expected_of,
    is_missing,
)

_FLAGS = {"IGNORECASE": re.IGNORECASE, "I": re.IGNORECASE, "MULTILINE": re.MULTILINE, "M": re.MULTILINE, "DOTALL": re.DOTALL, "S": re.DOTALL}


class RegexGrader(BaseGrader):
    name = "regex"
    deterministic = True
    bounded = True
    metrics = ("format_compliance", "marker_present", "citation_format", "pattern_match")

    def score(self, item: dict, output: Any, *, config: dict) -> GradeResult:
        pattern = expected_of(item)
        if is_missing(pattern) or not isinstance(pattern, str) or not pattern:
            return GradeResult.failure(CLS_EXPECTED_INVALID, f"item `{item.get('id', '?')}` : `expected` doit être un motif regex non vide", expected=None if is_missing(pattern) else pattern)
        flags = 0
        for name in config.get("flags", ()):
            if str(name).upper() not in _FLAGS:
                raise config_error(self.name, f"flag regex inconnu `{name}`", f"utiliser l'un de {sorted(k for k in _FLAGS if len(k) > 1)}")
            flags |= _FLAGS[str(name).upper()]
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            return GradeResult.failure(CLS_EXPECTED_INVALID, f"item `{item.get('id', '?')}` : motif regex invalide ({exc})", pattern=pattern)

        text = as_text(output)
        match = compiled.fullmatch(text) if config.get("full_match", False) else compiled.search(text)
        negate = bool(config.get("negate", False))
        found = match is not None
        matched = (not found) if negate else found
        detail: dict[str, Any] = {"matched": matched, "found": found, "negate": negate, "pattern": pattern, "groups": [], "named_groups": {}, "span": None}
        if match is not None:
            detail["groups"] = list(match.groups())
            detail["named_groups"] = dict(match.groupdict())
            detail["span"] = list(match.span())
        return GradeResult(score=1.0 if matched else 0.0, detail=detail)


GRADER = RegexGrader()
