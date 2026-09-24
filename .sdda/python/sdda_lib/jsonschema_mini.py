"""Validateur JSON Schema minimal (draft 2020-12, sous-ensemble), stdlib uniquement.

Couvre ce que `ir.schema.json`, `golden-set.schema.json`, les méta-schémas de
`templates/` et les `outputSchema` des contrats d'outils utilisent : type (dont
tableaux de types), required, properties, additionalProperties,
patternProperties, propertyNames, minProperties, maxProperties,
dependentRequired, items, prefixItems, contains, minItems, maxItems,
uniqueItems, enum, const, pattern, minLength, maxLength, minimum, maximum,
exclusiveMinimum, exclusiveMaximum, multipleOf, $ref local (`#`, `#/$defs/…`,
`#/properties/…`), allOf, anyOf, oneOf, if/then/else, not. `format` et
`default` sont ignorés (informatifs). Toute autre clé est ignorée.

**Trois écarts silencieux corrigés, et pourquoi ils comptaient.** Ce module
juge l'IR (TOPOLOGY GATE) et les sorties des agents (grader `schema`) : un mot
clé ignoré y est une contrainte déclarée que rien n'applique.

1. `uniqueItems` et `minProperties` figuraient dans les schémas du dépôt et
   n'étaient pas implémentés : une liste de doublons passait pour valide.
2. `enum`/`const` comparaient en Python, où `True == 1` : `{"enum": [1]}`
   acceptait `true`. JSON distingue booléen et nombre ; la comparaison aussi.
3. `integer` refusait `1.0`, qu'un sérialiseur JSON émet couramment et que la
   spécification tient pour un entier.

Écart ASSUMÉ avec la norme : un `$ref` accompagné de mots clés frères est
FUSIONNÉ avec eux (le frère gagne), au lieu d'être évalué en parallèle. Les
schémas du dépôt en dépendent (`additionalProperties: false` à côté d'un
`$ref` qui porte les `properties`) ; la norme exigerait `unevaluatedProperties`.
"""
from __future__ import annotations

import math
import re
from typing import Any


def _is_integer(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, float) and math.isfinite(v) and v.is_integer()


_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": _is_integer,
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def json_equal(a: Any, b: Any) -> bool:
    """Égalité au sens JSON : `true` n'est pas `1`, `1` est `1.0`."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


class SchemaValidator:
    def __init__(self, schema: dict[str, Any]):
        self.root = schema

    def _resolve(self, ref: str) -> dict[str, Any]:
        if ref == "#":
            return self.root
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

    def _check(self, v: Any, s: Any, path: str, errors: list[str]) -> None:
        if s is True or s == {}:
            return
        if s is False:
            errors.append(f"{path}: aucune valeur n'est admise ici (schéma `false`)")
            return
        if not isinstance(s, dict):
            return
        if "$ref" in s:
            merged = dict(self._resolve(s["$ref"]))
            merged.update({k: val for k, val in s.items() if k != "$ref"})
            s = merged

        if "type" in s:
            types = s["type"] if isinstance(s["type"], list) else [s["type"]]
            if not any(_TYPE_CHECKS.get(t, lambda _v: False)(v) for t in types):
                errors.append(f"{path}: type attendu {'/'.join(types)}, trouvé {type(v).__name__}")
                return
        if "const" in s and not json_equal(v, s["const"]):
            errors.append(f"{path}: valeur attendue {s['const']!r}, trouvé {v!r}")
        if "enum" in s and not any(json_equal(v, e) for e in s["enum"]):
            errors.append(f"{path}: {v!r} hors de l'énumération {s['enum']}")

        if isinstance(v, str):
            if "pattern" in s and not re.search(s["pattern"], v):
                errors.append(f"{path}: {v!r} ne respecte pas le motif {s['pattern']}")
            if "minLength" in s and len(v) < s["minLength"]:
                errors.append(f"{path}: longueur {len(v)} < minLength {s['minLength']}")
            if "maxLength" in s and len(v) > s["maxLength"]:
                errors.append(f"{path}: longueur {len(v)} > maxLength {s['maxLength']}")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if "minimum" in s and v < s["minimum"]:
                errors.append(f"{path}: {v} < minimum {s['minimum']}")
            if "exclusiveMinimum" in s and v <= s["exclusiveMinimum"]:
                errors.append(f"{path}: {v} <= exclusiveMinimum {s['exclusiveMinimum']}")
            if "maximum" in s and v > s["maximum"]:
                errors.append(f"{path}: {v} > maximum {s['maximum']}")
            if "exclusiveMaximum" in s and v >= s["exclusiveMaximum"]:
                errors.append(f"{path}: {v} >= exclusiveMaximum {s['exclusiveMaximum']}")
            if "multipleOf" in s and s["multipleOf"]:
                q = v / s["multipleOf"]
                if not math.isclose(q, round(q), rel_tol=0, abs_tol=1e-9):
                    errors.append(f"{path}: {v} n'est pas un multiple de {s['multipleOf']}")

        if isinstance(v, dict):
            for req in s.get("required", []):
                if req not in v:
                    errors.append(f"{path}: propriété obligatoire manquante « {req} »")
            if "minProperties" in s and len(v) < s["minProperties"]:
                errors.append(f"{path}: {len(v)} propriété(s) < minProperties {s['minProperties']}")
            if "maxProperties" in s and len(v) > s["maxProperties"]:
                errors.append(f"{path}: {len(v)} propriété(s) > maxProperties {s['maxProperties']}")
            for key, deps in (s.get("dependentRequired") or {}).items():
                if key in v:
                    for dep in deps:
                        if dep not in v:
                            errors.append(f"{path}: « {key} » exige « {dep} »")
            props = s.get("properties", {})
            patterns = s.get("patternProperties", {})
            for k, val in v.items():
                matched = False
                if k in props:
                    matched = True
                    self._check(val, props[k], f"{path}.{k}", errors)
                for pat, sub in patterns.items():
                    if re.search(pat, k):
                        matched = True
                        self._check(val, sub, f"{path}.{k}", errors)
                if not matched and "additionalProperties" in s:
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
            if "maxItems" in s and len(v) > s["maxItems"]:
                errors.append(f"{path}: {len(v)} élément(s) > maxItems {s['maxItems']}")
            if s.get("uniqueItems"):
                for i in range(len(v)):
                    if any(json_equal(v[i], v[j]) for j in range(i)):
                        errors.append(f"{path}[{i}]: doublon — uniqueItems exige des éléments distincts")
                        break
            prefix = s.get("prefixItems") or []
            for i, sub in enumerate(prefix[: len(v)]):
                self._check(v[i], sub, f"{path}[{i}]", errors)
            if "items" in s:
                for i, item in enumerate(v[len(prefix):], start=len(prefix)):
                    self._check(item, s["items"], f"{path}[{i}]", errors)
            if "contains" in s and not any(self.is_valid(item, s["contains"]) for item in v):
                errors.append(f"{path}: aucun élément ne satisfait `contains`")

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
