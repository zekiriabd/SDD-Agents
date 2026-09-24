"""`jsonschema_mini` — ce qui juge l'IR (G2) et les sorties d'agents (grader `schema`).

Un mot clé ignoré y est une contrainte déclarée que rien n'applique. Chaque cas
porte son verdict attendu ; quand la bibliothèque `jsonschema` est installée, il
est aussi confronté à elle, et un désaccord est un défaut du mini-validateur.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdda_lib.jsonschema_mini import SchemaValidator, json_equal

SDDA = Path(__file__).resolve().parents[2]

CASES: list[tuple[str, dict, object, bool]] = [
    ("type-integer-accepts-1.0", {"type": "integer"}, 1.0, True),
    ("type-integer-refuses-1.5", {"type": "integer"}, 1.5, False),
    ("type-integer-refuses-bool", {"type": "integer"}, True, False),
    ("enum-bool-is-not-int", {"enum": [1]}, True, False),
    ("enum-int-is-not-bool", {"enum": [True]}, 1, False),
    ("enum-1-equals-1.0", {"enum": [1]}, 1.0, True),
    ("const-false-is-not-0", {"const": 0}, False, False),
    ("uniqueItems", {"type": "array", "uniqueItems": True}, ["a", "a"], False),
    ("uniqueItems-bool-vs-int", {"uniqueItems": True}, [1, True], True),
    ("uniqueItems-ok", {"uniqueItems": True}, ["a", "b"], True),
    ("minProperties", {"type": "object", "minProperties": 1}, {}, False),
    ("maxProperties", {"maxProperties": 1}, {"a": 1, "b": 2}, False),
    ("maxLength", {"maxLength": 3}, "abcd", False),
    ("maxItems", {"maxItems": 1}, [1, 2], False),
    ("exclusiveMaximum", {"exclusiveMaximum": 1}, 1, False),
    ("multipleOf", {"multipleOf": 0.5}, 1.5, True),
    ("multipleOf-ko", {"multipleOf": 2}, 3, False),
    ("patternProperties", {"patternProperties": {"^x-": {"type": "string"}}, "additionalProperties": False},
     {"x-a": "ok"}, True),
    ("patternProperties-ko", {"patternProperties": {"^x-": {"type": "string"}}, "additionalProperties": False},
     {"y": "no"}, False),
    ("dependentRequired", {"dependentRequired": {"a": ["b"]}}, {"a": 1}, False),
    ("contains", {"contains": {"const": 3}}, [1, 2], False),
    ("prefixItems", {"prefixItems": [{"type": "string"}], "items": {"type": "integer"}}, ["a", 1, 2], True),
    ("prefixItems-ko", {"prefixItems": [{"type": "string"}], "items": {"type": "integer"}}, ["a", "b"], False),
    ("ref-root", {"type": "object", "properties": {"child": {"$ref": "#"}}, "required": ["v"]},
     {"v": 1, "child": {}}, False),
    ("if-then-else", {"if": {"const": 1}, "then": {"type": "integer"}, "else": {"type": "string"}}, 2, False),
    ("oneOf-exactly-one", {"oneOf": [{"type": "integer"}, {"type": "number"}]}, 1, False),
    ("not", {"not": {"type": "null"}}, None, False),
    ("boolean-subschema-false", {"properties": {"a": False}}, {"a": 1}, False),
]


@pytest.mark.parametrize("name,schema,instance,expected", CASES, ids=[c[0] for c in CASES])
def test_cases(name: str, schema: dict, instance: object, expected: bool) -> None:
    assert SchemaValidator(schema).is_valid(instance) is expected, SchemaValidator(schema).validate(instance)


@pytest.mark.parametrize("name,schema,instance,expected", CASES, ids=[c[0] for c in CASES])
def test_cases_agree_with_the_reference_library(name: str, schema: dict, instance: object, expected: bool) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    reference = jsonschema.Draft202012Validator(schema).is_valid(instance)
    assert reference is expected, f"le cas {name} est lui-même faux"


def test_json_equal_distinguishes_booleans_from_numbers() -> None:
    assert json_equal(1, 1.0) and not json_equal(1, True) and not json_equal([0], [False])
    assert json_equal({"a": [1, {"b": None}]}, {"a": [1.0, {"b": None}]})


def test_repository_schemas_use_only_supported_keywords() -> None:
    """Tout mot clé de validation employé par un schéma du dépôt est implémenté.

    `uniqueItems` et `minProperties` y figuraient sans être implémentés : la
    contrainte était déclarée, et rien ne l'appliquait.
    """
    supported = {
        "type", "required", "properties", "additionalProperties", "patternProperties", "propertyNames",
        "minProperties", "maxProperties", "dependentRequired", "items", "prefixItems", "contains",
        "minItems", "maxItems", "uniqueItems", "enum", "const", "pattern", "minLength", "maxLength",
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "$ref", "allOf",
        "anyOf", "oneOf", "if", "then", "else", "not",
    }
    informative = {"$schema", "$id", "$defs", "$comment", "title", "description", "default", "format",
                   "examples", "deprecated", "readOnly", "writeOnly"}
    used: set[str] = set()

    def walk(node: object, in_props: bool = False) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if not in_props:
                    used.add(key)
                walk(value, key in ("properties", "$defs", "patternProperties", "dependentRequired"))
        elif isinstance(node, list):
            for item in node:
                walk(item)

    schemas = sorted((SDDA / "templates").glob("*.schema.json")) + [SDDA / "registry" / "ir.schema.json"]
    for path in schemas:
        walk(json.loads(path.read_text(encoding="utf-8")))
    assert used - supported - informative == set()


def test_ir_schema_loads_and_requires_its_root_keys() -> None:
    """Le schéma d'IR réel se charge, et un IR vide n'est pas un IR."""
    schema = json.loads((SDDA / "registry" / "ir.schema.json").read_text(encoding="utf-8"))
    validator = SchemaValidator(schema)
    assert validator.validate({}) != []           # des `required` à la racine
