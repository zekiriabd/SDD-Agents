"""Grader `schema` — conformité de la sortie à un JSON Schema.

**Le grader le moins cher et le plus efficace** (eval-protocol.md §4). Il ne
coûte aucun token, il est reproductible, et il attrape la majorité des
défaillances réelles d'un agent structuré : champ manquant, type faux, valeur
hors énumération, champ inventé. Un `schema` sur la structure plus un `exact`
sur le champ décisif vaut mieux qu'un juge LLM sur la réponse entière.

`expected` est le schéma (objet), ou `config["schema"]` quand le même schéma
sert à tout le jeu. Une sortie chaîne est d'abord parsée en JSON
(`parse_json: true` par défaut) : une sortie qui n'est pas du JSON alors qu'on
attend une structure est une **mauvaise réponse** (score 0, violation
explicite), pas une erreur d'exécution — le système devait produire du JSON.

Piège documenté (pytest-eval.md, gotcha 13) : `additionalProperties` vaut
`true` par défaut en JSON Schema, donc une sortie avec des champs inventés
passe. `detail["additional_properties_unconstrained"]` le signale quand le
schéma racine ne le ferme pas — le résultat reste vert, mais on sait qu'il est
laxiste.

Score : 1.0 ou 0.0 — la conformité ne se gradue pas. `detail["violations"]`
liste chaque écart avec son chemin. Déterministe, validateur `jsonschema_mini`.
"""
from __future__ import annotations

import json
from typing import Any

from sdda_lib.graders._base import (
    CLS_EXPECTED_INVALID,
    BaseGrader,
    GradeResult,
    expected_of,
    is_missing,
)
from sdda_lib.jsonschema_mini import SchemaValidator

_SCHEMA_KEYWORDS = {"type", "properties", "required", "enum", "const", "anyOf", "oneOf", "allOf", "$ref", "items", "not"}


def schema_of(expected: Any) -> Any:
    """Le schéma porté par `expected` : lui-même, ou `expected["schema"]` (forme enveloppée).

    Deux graphies coexistent dans les jeux : `expected: {type: object, …}` et
    `expected: {schema: {…}}`. On ne confond pas les deux : un schéma qui
    déclare lui-même une propriété nommée `schema` porte au moins un mot-clé
    JSON Schema à sa racine.
    """
    if isinstance(expected, dict) and isinstance(expected.get("schema"), dict) and not (_SCHEMA_KEYWORDS & expected.keys()):
        return expected["schema"]
    return expected


class SchemaGrader(BaseGrader):
    name = "schema"
    deterministic = True
    bounded = True
    metrics = ("schema_valid", "output_schema_compliance", "structured_output_rate")

    def score(self, item: dict, output: Any, *, config: dict) -> GradeResult:
        schema = config.get("schema", schema_of(expected_of(item)))
        if is_missing(schema) or not isinstance(schema, dict):
            return GradeResult.failure(CLS_EXPECTED_INVALID, f"item `{item.get('id', '?')}` : `expected` (ou config.schema) doit être un objet JSON Schema")

        instance = output
        parsed = False
        if isinstance(output, str) and config.get("parse_json", True):
            try:
                instance = json.loads(output)
                parsed = True
            except (json.JSONDecodeError, ValueError) as exc:
                return GradeResult(
                    score=0.0,
                    detail={"valid": False, "parsed_json": False, "violations": [f"$: la sortie n'est pas du JSON ({exc.__class__.__name__})"], "violation_count": 1},
                )

        try:
            violations = SchemaValidator(schema).validate(instance)
        except (KeyError, ValueError, TypeError) as exc:
            # `$ref` non résolu, `type` inconnu… : le schéma est en cause, pas la sortie.
            return GradeResult.failure(CLS_EXPECTED_INVALID, f"item `{item.get('id', '?')}` : schéma inexploitable ({exc})")

        unconstrained = schema.get("type") == "object" and "additionalProperties" not in schema and "properties" in schema
        return GradeResult(
            score=1.0 if not violations else 0.0,
            detail={
                "valid": not violations,
                "parsed_json": parsed,
                "violations": violations,
                "violation_count": len(violations),
                "additional_properties_unconstrained": unconstrained,
            },
        )


GRADER = SchemaGrader()
