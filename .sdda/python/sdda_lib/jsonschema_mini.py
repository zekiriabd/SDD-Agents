"""Validateur JSON Schema minimal (draft 2020-12, sous-ensemble), stdlib uniquement.

Couvre exactement ce que `ir.schema.json` et `golden-set.schema.json`
utilisent : type (dont tableaux de types), required, properties,
additionalProperties, propertyNames, items, minItems, enum, const, pattern,
minimum, exclusiveMinimum, maximum, minLength, $ref local (`#/$defs/…`,
`#/properties/…`), allOf, anyOf, oneOf, if/then, not. `format` et `default`
sont ignorés (informatifs). Toute autre clé est ignorée.
"""
from __future__ import annotations

import re
from typing import Any

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaValidator:
    def __init__(self, schema: dict[str, Any]):
        self.root = schema

    def _resolve(self, ref: str) -> dict[str, Any]:
        if not ref.startswith("#/"):
            raise ValueError(f"$ref non local non supporté : {ref}")
        node: Any = self.root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            node = node[part]
        return node

    def validate(self, instance: Any, schema: dict[str, Any] | None = None, path: str = "$") -> list[str]:
        """Liste des violations « chemin: message » (vide si conforme)."""
        errors: list[str] = []
        self._check(instance, self.root if schema is None else schema, path, errors)
        return errors

    def is_valid(self, instance: Any, schema: dict[str, Any] | None = None) -> bool:
        return not self.validate(instance, schema)

    def _check(self, v: Any, s: dict[str, Any], path: str, errors: list[str]) -> None:
        if "$ref" in s:
            merged = dict(self._resolve(s["$ref"]))
            merged.update({k: val for k, val in s.items() if k != "$ref"})
            s = merged

        if "type" in s:
            types = s["type"] if isinstance(s["type"], list) else [s["type"]]
            if not any(_TYPE_CHECKS[t](v) for t in types):
                errors.append(f"{path}: type attendu {'/'.join(types)}, trouvé {type(v).__name__}")
                return
        if "const" in s and v != s["const"]:
            errors.append(f"{path}: valeur attendue {s['const']!r}, trouvé {v!r}")
        if "enum" in s and v not in s["enum"]:
            errors.append(f"{path}: {v!r} hors de l'énumération {s['enum']}")

        if isinstance(v, str):
            if "pattern" in s and not re.search(s["pattern"], v):
                errors.append(f"{path}: {v!r} ne respecte pas le motif {s['pattern']}")
            if "minLength" in s and len(v) < s["minLength"]:
                errors.append(f"{path}: longueur {len(v)} < minLength {s['minLength']}")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if "minimum" in s and v < s["minimum"]:
                errors.append(f"{path}: {v} < minimum {s['minimum']}")
            if "exclusiveMinimum" in s and v <= s["exclusiveMinimum"]:
                errors.append(f"{path}: {v} <= exclusiveMinimum {s['exclusiveMinimum']}")
            if "maximum" in s and v > s["maximum"]:
                errors.append(f"{path}: {v} > maximum {s['maximum']}")

        if isinstance(v, dict):
            for req in s.get("required", []):
                if req not in v:
                    errors.append(f"{path}: propriété obligatoire manquante « {req} »")
            props = s.get("properties", {})
            for k, val in v.items():
                if k in props:
                    self._check(val, props[k], f"{path}.{k}", errors)
                elif "additionalProperties" in s:
                    ap = s["additionalProperties"]
                    if ap is False:
                        errors.append(f"{path}: propriété non autorisée « {k} »")
                    elif isinstance(ap, dict):
                        self._check(val, ap, f"{path}.{k}", errors)
                if "propertyNames" in s:
                    self._check(k, s["propertyNames"], f"{path}.{k} (nom)", errors)

        if isinstance(v, list):
            if "minItems" in s and len(v) < s["minItems"]:
                errors.append(f"{path}: {len(v)} élément(s) < minItems {s['minItems']}")
            if "items" in s:
                for i, item in enumerate(v):
                    self._check(item, s["items"], f"{path}[{i}]", errors)

        for sub in s.get("allOf", []):
            self._check(v, sub, path, errors)
        if "anyOf" in s and not any(self.is_valid(v, sub) for sub in s["anyOf"]):
            errors.append(f"{path}: aucune des {len(s['anyOf'])} alternatives `anyOf` n'est satisfaite")
        if "oneOf" in s:
            matches = sum(1 for sub in s["oneOf"] if self.is_valid(v, sub))
            if matches != 1:
                errors.append(f"{path}: {matches} alternative(s) `oneOf` satisfaite(s), exactement 1 attendue")
        if "not" in s and self.is_valid(v, s["not"]):
            errors.append(f"{path}: valeur interdite par `not`")
        if "if" in s:
            if self.is_valid(v, s["if"]):
                if "then" in s:
                    self._check(v, s["then"], path, errors)
            elif "else" in s:
                self._check(v, s["else"], path, errors)
