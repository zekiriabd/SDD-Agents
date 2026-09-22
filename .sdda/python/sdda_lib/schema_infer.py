"""Inférence de schéma depuis des enregistrements observés — déterministe, stdlib.

Ni un CSV, ni un XLSX, ni une réponse d'API n'ont de schéma. Sans épinglage,
une colonne qui change de type change le comportement de l'agent en silence :
c'est le mode d'échec que ce module existe pour rendre visible **une fois**, au
moment où un humain relit, plutôt qu'à chaque run sans que personne le voie.

Ce que le module produit est un **brouillon à relire**, jamais une vérité. Il
le dit dans la `description` du schéma qu'il écrit, et le générateur refuse de
figer un schéma que personne n'a relu (`Status: Draft` du contrat).

Trois décisions valent d'être connues, parce qu'elles se voient en production :

1. **`required` = présent et non nul dans 100 % des enregistrements.** Un champ
   présent à 99,8 % n'est pas obligatoire : il est optionnel avec un biais
   d'échantillonnage, et le déclarer requis fait échouer le 1 enregistrement
   sur 500 qui compte.
2. **Un `enum` ne s'infère pas depuis trois lignes.** Il faut au moins trois
   observations par valeur distincte, sinon un échantillon maigre fabrique une
   liste close que la première donnée réelle viole.
3. **Un identifiant reste une chaîne.** `0012345` typé `integer` devient
   `12345`, la jointure ne trouve plus rien, et l'agent répond « ce client
   n'existe pas ». Les champs listés dans `string_only` ne sont jamais coercés,
   et une valeur à zéro initial force le type `string` même sans être listée.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

#: Au-delà, une valeur distincte de plus n'est plus une énumération métier.
ENUM_MAX_DISTINCT = 12

#: Observations minimales par valeur distincte pour qu'un `enum` soit crédible.
ENUM_MIN_RATIO = 3

#: Au-delà, on cesse de mémoriser les valeurs distinctes d'un champ : il n'est
#: de toute façon plus candidat à un `enum`, et la mémoire n'est pas gratuite.
_DISTINCT_CAP = 64

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$"
)
_DATETIME_NAIVE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$")
_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")
_BOOL_TRUE = frozenset({"true", "vrai", "yes", "oui", "1"})
_BOOL_FALSE = frozenset({"false", "faux", "no", "non", "0"})


def json_type(value: Any) -> str:
    # `bool` avant `int` : en Python `True` est un entier, et laisser passer
    # l'ordre naturel typerait tous les booléens en `integer`.
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "string"


def must_stay_string(raw: str) -> bool:
    """Cette cellule interdit-elle de typer son champ autrement qu'en chaîne ?

    Un zéro initial ou un `+` initial sont les deux formes qu'un identifiant
    prend dans un export — code postal, référence produit, numéro de compte —
    et les deux que le typage automatique détruit sans rien dire.

    La règle vaut pour le **champ entier**, pas pour la valeur : une colonne
    où `01000` reste une chaîne et `75001` devient un entier est pire que les
    deux, parce que la moitié des jointures marche et l'autre pas.
    """
    s = raw.strip()
    return bool(s) and (s[0] == "+" or (len(s) > 1 and s[0] == "0" and s[1] not in ".eE"))


def coerce_scalar(raw: str) -> Any:
    """Typage d'une cellule texte (CSV/XLSX), conservateur par construction."""
    s = raw.strip()
    if s == "":
        return None
    low = s.lower()
    if low in _BOOL_TRUE and low not in ("1",):
        return True
    if low in _BOOL_FALSE and low not in ("0",):
        return False
    if must_stay_string(s):
        return s
    if _INT_RE.match(s):
        return int(s)
    if _FLOAT_RE.match(s):
        return float(s)
    return s


@dataclass
class FieldStats:
    """Ce qu'on a observé d'un champ, et rien de plus."""

    seen: int = 0                                   # enregistrements où la clé existe
    nulls: int = 0                                  # dont valeur nulle ou vide
    force_string: bool = False                      # une cellule au moins interdit le typage
    types: Counter = field(default_factory=Counter)
    distinct: set[Any] = field(default_factory=set)
    distinct_overflow: bool = False
    max_length: int = 0
    total_length: int = 0
    strings: int = 0
    dates: int = 0
    datetimes: int = 0
    naive_datetimes: int = 0

    def observe(self, value: Any) -> None:
        self.seen += 1
        t = json_type(value)
        if value is None:
            self.nulls += 1
            self.types["null"] += 1
            return
        self.types[t] += 1
        if t == "string":
            self.strings += 1
            self.max_length = max(self.max_length, len(value))
            self.total_length += len(value)
            if _DATETIME_RE.match(value):
                self.datetimes += 1
            elif _DATETIME_NAIVE_RE.match(value):
                self.naive_datetimes += 1
            elif _DATE_RE.match(value):
                self.dates += 1
        if t in ("string", "integer", "number", "boolean") and not self.distinct_overflow:
            self.distinct.add(value)
            if len(self.distinct) > _DISTINCT_CAP:
                self.distinct_overflow = True
                self.distinct.clear()


@dataclass
class InferenceResult:
    schema: dict[str, Any]
    records: int
    notes: list[str] = field(default_factory=list)


def infer(
    records: Iterable[Mapping[str, Any]],
    *,
    source_id: str,
    coerce: bool = False,
    string_only: Iterable[str] = (),
    no_enum: Iterable[str] = (),
    max_records: int = 200_000,
) -> InferenceResult:
    """Observe des enregistrements et rend un JSON Schema d'objet, à relire.

    `coerce` : les valeurs sont des cellules texte (CSV / XLSX) et doivent être
    typées. `string_only` : champs que le typage ne touche jamais — la `key` et
    les `required_filter` en font partie d'office côté appelant. `no_enum` :
    champs `pii` / `free_text`, où une liste close n'aurait aucun sens et
    recopierait des données réelles dans un fichier versionné.
    """
    locked = set(string_only)
    forbidden_enum = set(no_enum)
    stats: dict[str, FieldStats] = {}
    order: list[str] = []
    total = 0
    truncated = False

    for record in records:
        if total >= max_records:
            truncated = True
            break
        if not isinstance(record, Mapping):
            continue
        total += 1
        for key in record:
            if key not in stats:
                stats[key] = FieldStats()
                order.append(key)
        for key, raw in record.items():
            value = raw
            if coerce and isinstance(raw, str) and key not in locked:
                if must_stay_string(raw):
                    stats[key].force_string = True
                value = coerce_scalar(raw)
            elif coerce and isinstance(raw, str):
                value = raw.strip() or None
            stats[key].observe(value)

    notes: list[str] = []
    if truncated:
        notes.append(f"échantillon borné à {max_records} enregistrements — `required` et `enum` peuvent être trop stricts")
    if total == 0:
        notes.append("aucun enregistrement observé : le schéma est vide")

    properties: dict[str, Any] = {}
    required: list[str] = []
    for key in sorted(order):
        st = stats[key]
        spec = _field_schema(key, st, locked=key in locked, allow_enum=key not in forbidden_enum, total=total)
        properties[key] = spec
        if st.seen == total and st.nulls == 0 and total > 0:
            required.append(key)
        if st.naive_datetimes and not st.datetimes:
            notes.append(
                f"`{key}` : horodatages sans fuseau (ex. 2026-09-11T22:40:00). "
                "Le modèle supposera UTC ; exiger l'offset à l'ingestion ou le déclarer dans la description"
            )

    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": source_id,
        "description": (
            f"Schéma FIGÉ de la source `{source_id}`, inféré depuis {total} enregistrement(s) observé(s) "
            "puis RELU PAR UN HUMAIN. C'est lui qui fait foi, pas les données : un champ absent d'ici est "
            "omis de la sortie de l'outil, et un écart de type au démarrage est [DATA_SOURCE_SCHEMA_DRIFT]."
        ),
        "type": "object",
        "required": required,
        "properties": properties,
        "x-sdda": {
            "generator": "gen_source_tools.py --infer",
            "sourceId": source_id,
            "recordsObserved": total,
            "reviewed": False,
        },
    }
    return InferenceResult(schema=schema, records=total, notes=notes)


def _field_schema(name: str, st: FieldStats, *, locked: bool, allow_enum: bool, total: int) -> dict[str, Any]:
    observed = [t for t in st.types if t != "null"]
    if (locked or st.force_string) and observed:
        observed = ["string"]
    if not observed:
        types: Any = "string"
    elif set(observed) == {"integer", "number"}:
        types = "number"
    elif len(observed) == 1:
        types = observed[0]
    else:
        types = sorted(observed)

    spec: dict[str, Any] = {"type": types}
    non_null = st.seen - st.nulls

    if types == "string" and non_null:
        if st.datetimes == non_null:
            spec["format"] = "date-time"
        elif st.dates == non_null:
            spec["format"] = "date"
        # Pas de `maxLength` inféré : la plus longue valeur d'un échantillon est
        # une observation, pas une borne. La figer transformerait le premier
        # « Chronopost » après dix mille « DPD » en refus de schéma. La longueur
        # observée reste dans la description, où elle informe sans contraindre.

    if allow_enum and not st.distinct_overflow and st.distinct and non_null:
        # Un champ verrouillé en chaîne l'est aussi dans son enum : `01000` et
        # `75001` doivent y figurer sous la même forme que dans la donnée.
        values = {str(v) for v in st.distinct} if (locked or st.force_string) else set(st.distinct)
        distinct = len(values)
        if distinct <= ENUM_MAX_DISTINCT and non_null >= ENUM_MIN_RATIO * distinct:
            spec["enum"] = sorted(values, key=lambda v: (json_type(v), str(v)))

    spec["description"] = _describe(name, st, total)
    return spec


def _describe(name: str, st: FieldStats, total: int) -> str:
    """Une description de départ, factuelle — à réécrire par un humain.

    Elle dit ce qui a été observé, pas ce que le champ signifie : personne ne
    peut inférer le sens métier d'une colonne, et une description inventée est
    pire qu'une description absente parce qu'elle a l'air relue.
    """
    parts = [f"Champ `{name}`"]
    if total:
        presence = st.seen - st.nulls
        parts.append(f"renseigné dans {presence}/{total} enregistrement(s) observé(s)")
    if st.strings and st.max_length:
        parts.append(f"longueur max observée {st.max_length}")
    parts.append("DESCRIPTION À RÉÉCRIRE : dire ce que le champ signifie, son unité et son fuseau")
    return " ; ".join(parts) + "."
