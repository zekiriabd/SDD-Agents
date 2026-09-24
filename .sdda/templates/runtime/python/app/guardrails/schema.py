"""Validation des SORTIES contre l'`outputSchema` de l'IR — `schema-validation.md`. GÉNÉRÉ.

« Le guardrail le moins cher et le plus efficace du catalogue » : une sortie
consommée par du code est contrainte par un JSON Schema et validée AVANT d'être
transmise. Un attaquant qui contrôle le modèle ne contrôle pas le schéma.

Sans dépendance : le sous-ensemble de JSON Schema que les contrats de l'IR
emploient (`type` simple ou liste, `enum`, `const`, `required`, `properties`,
`additionalProperties`, `items`, `minItems`/`maxItems`, `minLength`/`maxLength`,
`pattern`, `minimum`/`maximum`, `anyOf`/`oneOf`/`allOf`, `$ref` local). Un
mot-clé hors de ce sous-ensemble n'est PAS ignoré en silence : il est signalé,
parce qu'un validateur qui saute une contrainte la déclare satisfaite.

La contrainte native du fournisseur (sortie structurée) reste préférable quand
elle existe ; la validation a posteriori demeure de toute façon — une contrainte
native mal supportée échoue en silence, et c'est ce validateur qui s'en aperçoit.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

#: Mots-clés compris. Les annotations (`description`, `title`, `examples`,
#: `default`, `$schema`, `$id`, `$defs`, `format`) n'imposent rien à valider.
_KNOWN = frozenset({
    "type", "enum", "const", "required", "properties", "additionalProperties", "items",
    "minItems", "maxItems", "minLength", "maxLength", "pattern", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "anyOf", "oneOf", "allOf", "$ref",
    "description", "title", "examples", "default", "$schema", "$id", "$defs", "definitions",
    "format", "uniqueItems", "nullable",
})

_TYPES: dict[str, Any] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaValidationError(ValueError):
    """La sortie ne respecte pas son schéma. Porte la liste des violations, pour le retry."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations[:10]))
        self.violations = violations


def coerce_output(output: Any) -> Any:
    """Une sortie de modèle arrive souvent en TEXTE : un JSON attendu est décodé, strictement."""
    if isinstance(output, str):
        text = output.strip()
        if text.startswith(("{", "[")):
            try:
                return json.loads(text)
            except ValueError:
                return output
    return output


def validate(instance: Any, schema: Mapping[str, Any]) -> list[str]:
    """Les violations « chemin : message » ; liste vide si conforme."""
    errors: list[str] = []
    _check(instance, schema, "$", errors, schema)
    return errors


def ensure_valid(instance: Any, schema: Mapping[str, Any]) -> Any:
    """La sortie décodée si elle est conforme ; `SchemaValidationError` sinon."""
    value = coerce_output(instance)
    violations = validate(value, schema)
    if violations:
        raise SchemaValidationError(violations)
    return value


def _resolve(root: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"$ref non local non supporté : {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    if not isinstance(node, Mapping):
        raise ValueError(f"$ref vers un nœud non objet : {ref}")
    return node


def _check(value: Any, schema: Mapping[str, Any], path: str, errors: list[str],
           root: Mapping[str, Any]) -> None:
    unknown = sorted(set(schema) - _KNOWN)
    if unknown:
        errors.append(f"{path} : mot-clé de schéma non supporté {unknown} — "
                      "contrainte non vérifiée, donc refusée")
    if "$ref" in schema:
        _check(value, _resolve(root, str(schema["$ref"])), path, errors, root)
    declared = schema.get("type")
    if declared is not None:
        types = declared if isinstance(declared, list) else [declared]
        if schema.get("nullable") is True:
            types = [*types, "null"]
        if not any(_TYPES.get(str(t), lambda _v: False)(value) for t in types):
            expected = "/".join(str(t) for t in types)
            errors.append(f"{path} : type attendu {expected}, trouvé {type(value).__name__}")
            return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} : {value!r} hors de l'énumération {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path} : attendu {schema['const']!r}, trouvé {value!r}")
    if isinstance(value, dict):
        _check_object(value, schema, path, errors, root)
    if isinstance(value, list):
        _check_array(value, schema, path, errors, root)
    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            errors.append(f"{path} : longueur {len(value)} < {schema['minLength']}")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            errors.append(f"{path} : longueur {len(value)} > {schema['maxLength']}")
        if "pattern" in schema and not re.search(str(schema["pattern"]), value):
            errors.append(f"{path} : ne correspond pas au motif {schema['pattern']!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < float(schema["minimum"]):
            errors.append(f"{path} : {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > float(schema["maximum"]):
            errors.append(f"{path} : {value} > maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= float(schema["exclusiveMinimum"]):
            errors.append(f"{path} : {value} <= {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= float(schema["exclusiveMaximum"]):
            errors.append(f"{path} : {value} >= {schema['exclusiveMaximum']}")
    for key in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(key)
        if not isinstance(branches, list):
            continue
        results = [validate_branch(value, b, path, root) for b in branches if isinstance(b, Mapping)]
        passing = sum(1 for r in results if not r)
        if key == "allOf":
            for r in results:
                errors.extend(r)
        elif key == "anyOf" and passing == 0:
            errors.append(f"{path} : aucune branche de anyOf ne correspond")
        elif key == "oneOf" and passing != 1:
            errors.append(f"{path} : {passing} branche(s) de oneOf correspondent (exactement 1 attendue)")


def validate_branch(value: Any, schema: Mapping[str, Any], path: str, root: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    _check(value, schema, path, errors, root)
    return errors


def _check_object(value: dict[str, Any], schema: Mapping[str, Any], path: str, errors: list[str],
                  root: Mapping[str, Any]) -> None:
    for key in schema.get("required") or []:
        if key not in value:
            errors.append(f"{path} : champ requis `{key}` absent")
    properties = schema.get("properties") or {}
    for key, item in value.items():
        sub = f"{path}.{key}"
        if key in properties and isinstance(properties[key], Mapping):
            _check(item, properties[key], sub, errors, root)
            continue
        extra = schema.get("additionalProperties", True)
        if extra is False:
            errors.append(f"{path} : champ `{key}` non déclaré (additionalProperties: false)")
        elif isinstance(extra, Mapping):
            _check(item, extra, sub, errors, root)


def _check_array(value: list[Any], schema: Mapping[str, Any], path: str, errors: list[str],
                 root: Mapping[str, Any]) -> None:
    if "minItems" in schema and len(value) < int(schema["minItems"]):
        errors.append(f"{path} : {len(value)} élément(s) < minItems {schema['minItems']}")
    if "maxItems" in schema and len(value) > int(schema["maxItems"]):
        errors.append(f"{path} : {len(value)} élément(s) > maxItems {schema['maxItems']}")
    items = schema.get("items")
    if isinstance(items, Mapping):
        for index, item in enumerate(value):
            _check(item, items, f"{path}[{index}]", errors, root)
    if schema.get("uniqueItems") is True:
        seen = [json.dumps(v, sort_keys=True, default=str) for v in value]
        if len(seen) != len(set(seen)):
            errors.append(f"{path} : éléments en double (uniqueItems)")
