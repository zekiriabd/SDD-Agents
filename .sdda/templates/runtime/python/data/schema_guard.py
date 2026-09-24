"""Validation au démarrage contre les schémas figés — fail-fast.

Le schéma figé fait foi, pas les données. Sans cette passe, un export qui change
de forme change le comportement de l'agent **en silence** : un champ devenu
absent produit des réponses vides, un type qui glisse produit des comparaisons
fausses, et rien ne le dit avant la plainte d'un client.

Quatre écarts, trois traitements — et la différence est délibérée :

    champ `required` absent   fail-fast, l'application ne démarre pas
    type changé               fail-fast
    valeur hors `enum`        avertissement ; l'enregistrement passe tel quel
    champ nouveau             avertissement ; le champ est OMIS de la sortie

Le dernier cas mérite d'être compris : un champ non déclaré est **retiré**, pas
remonté. Une colonne `internal_margin_eur` ajoutée par l'amont ne doit pas
arriver dans le contexte du modèle parce que personne ne l'a interdite. La liste
des champs du schéma est une allowlist de contexte, pas une documentation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import SourceUnavailable
from .index import SourceIndex
from .registry import Registry, Source

SCHEMA_DIR = "schemas"

#: Types JSON Schema -> types Python acceptés. `integer` est accepté là où
#: `number` est attendu : un entier EST un nombre, et refuser l'inverse ferait
#: échouer un export dont une colonne décimale n'a que des valeurs rondes.
_ACCEPTS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list, tuple),
}


@dataclass
class GuardReport:
    source_id: str
    checked: int = 0
    drift: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    declared_fields: frozenset[str] = frozenset()

    @property
    def ok(self) -> bool:
        return not self.drift


def schema_path(base: Path, source_id: str) -> Path:
    return base / SCHEMA_DIR / f"{source_id}.schema.json"


def load_schema(base: Path, source_id: str) -> dict[str, Any]:
    path = schema_path(base, source_id)
    if not path.is_file():
        raise SourceUnavailable(
            f"schéma figé absent ({path.name})", source=source_id,
            detail="`gen_source_tools.py --infer --source " + source_id + "`, puis RELIRE le fichier")
    schema: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return schema


def _type_ok(value: Any, declared: Any) -> bool:
    names = [declared] if isinstance(declared, str) else list(declared or [])
    if value is None:
        return "null" in names or not names
    for name in names:
        # `bool` avant `int` : en Python `True` est un entier, et l'ordre naturel
        # validerait un booléen là où un entier est déclaré.
        if name == "boolean":
            if isinstance(value, bool):
                return True
            continue
        if name == "integer" and isinstance(value, bool):
            continue
        if isinstance(value, _ACCEPTS.get(name, ())):
            return True
    return False


def check_source(base: Path, source: Source, index: SourceIndex, sample: int,
                 records: Any) -> GuardReport:
    """Confronte un échantillon borné au schéma figé."""
    schema = load_schema(base, source.id)
    properties: dict[str, Any] = schema.get("properties") or {}
    required = [str(f) for f in (schema.get("required") or [])]
    report = GuardReport(source_id=source.id, declared_fields=frozenset(properties))

    seen_extra: set[str] = set()
    for record in records:
        if report.checked >= sample:
            break
        report.checked += 1

        for name in required:
            if record.get(name) is None:
                report.drift.append(
                    f"champ requis `{name}` absent ou nul (enregistrement {report.checked})")

        for name, value in record.items():
            spec = properties.get(name)
            if spec is None:
                if name not in seen_extra:
                    seen_extra.add(name)
                    report.warnings.append(
                        f"champ `{name}` absent du schéma figé — OMIS de la sortie")
                continue
            if value is not None and not _type_ok(value, spec.get("type")):
                report.drift.append(
                    f"champ `{name}` : {type(value).__name__} au lieu de {spec.get('type')}")
            allowed = spec.get("enum")
            if isinstance(allowed, list) and allowed and value is not None and value not in allowed:
                report.warnings.append(f"champ `{name}` : valeur hors enum (`{value}`)")

    # Dédoublonner : un type qui glisse sur 500 enregistrements produirait 500
    # fois la même ligne, et le message deviendrait illisible donc ignoré.
    report.drift = sorted(set(report.drift))
    report.warnings = sorted(set(report.warnings))
    return report


def enforce(base: Path, registry: Registry, reports: list[GuardReport]) -> None:
    """Fail-fast : l'application ne démarre pas sur un schéma qui a dérivé."""
    broken = [r for r in reports if not r.ok]
    if not broken:
        return
    lines = [f"  - {r.source_id} : {'; '.join(r.drift[:3])}" for r in broken]
    raise SystemExit(
        "ERROR: démarrage refusé — schéma figé et données ont divergé\n"
        f"CAUSE: [DATA_SOURCE_SCHEMA_DRIFT] {len(broken)} source(s)\n"
        + "\n".join(lines)
        + "\nFIX: corriger la source, ou ré-inférer le schéma SI la donnée a légitimement changé\n"
          "     (`gen_source_tools.py --infer --force --source <id>`), puis RELIRE le fichier.\n"
          "     Démarrer sur un schéma périmé produit des réponses fausses et confiantes."
    )
