"""Le registre résolu — GÉNÉRÉ, chargé au démarrage, jamais reconstruit au runtime.

`gen_source_tools.py` écrit `sources.json` à côté de ce module : le registre
**déjà résolu** (manifestes fusionnés, allowlists appliquées, enveloppe figée).
L'application ne lit donc jamais `STACK.md` ni les manifestes.

Trois raisons, et la troisième est la plus importante :

1. `STACK.md` est gitignoré et porte des secrets — il n'a rien à faire dans le
   processus applicatif ;
2. parser du Markdown au démarrage d'un service est une dépendance inutile ;
3. **ce qui est résolu est épinglable.** `sources.json` entre dans le tuple
   d'épinglage des evals (P10) : deux runs sur deux registres différents ne sont
   pas comparables, et le pipeline doit pouvoir le dire plutôt que d'aligner
   deux scores incomparables.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

REGISTRY_FILE = "sources.json"


class Envelope(BaseModel):
    """Les bornes. Une borne absente est une borne infinie — d'où les défauts serrés."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str = "readonly"
    read_timeout_ms: int = 5000
    max_records_returned: int = 200
    max_object_bytes: int = 52_428_800
    schema_check_sample: int = 500
    max_staleness_hours: int = 24
    forbidden_ops: tuple[str, ...] = ()
    allowed_sources: tuple[str, ...] = ()
    allowed_stores: tuple[str, ...] = ()
    egress_allowlist: tuple[str, ...] = ()
    query_logging: str = "full"


class Store(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: str
    root: str | None = None
    base_url: str | None = None
    server: str | None = None
    read_only: bool = True


class Source(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    connector: str
    store: str
    key: str
    description: str
    glob: str | None = None
    format: str | None = None
    encoding: str = "utf-8"
    delimiter: str | None = None
    sheet: str | None = None
    header_row: int = 1
    records_path: str | None = None
    filters: tuple[str, ...] = ()
    ranges: tuple[str, ...] = ()
    required_filter: tuple[str, ...] = ()
    pii: tuple[str, ...] = ()
    free_text: tuple[str, ...] = ()
    date_field: str | None = None
    max_staleness_hours: int | None = None
    trust: str = "trusted"

    @property
    def queryable(self) -> frozenset[str]:
        """Les champs sur lesquels un filtre est recevable. Rien d'autre.

        Cette frontière est la raison pour laquelle aucun prédicat libre n'est
        accepté : un champ absent d'ici n'est pas « non supporté », il est
        **refusé** (`InvalidFilter`). Ignorer un filtre inconnu rendrait tous
        les enregistrements et l'agent conclurait sur un ensemble qu'il croyait
        restreint.
        """
        return frozenset(self.filters) | frozenset(self.ranges) | frozenset(self.required_filter)


class Registry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = 1
    generated_from: str = ""
    content_hash: str = ""
    envelope: Envelope = Field(default_factory=Envelope)
    stores: tuple[Store, ...] = ()
    sources: tuple[Source, ...] = ()

    def store(self, store_id: str) -> Store | None:
        return next((s for s in self.stores if s.id == store_id), None)

    def source(self, source_id: str) -> Source | None:
        """La source, **si elle est allowlistée**. Sinon elle n'existe pas.

        Ne pas confondre « non déclarée » et « hors allowlist » serait une fuite
        d'information : les deux doivent être indiscernables de l'extérieur.
        """
        allowed = self.envelope.allowed_sources
        if allowed and source_id not in allowed:
            return None
        return next((s for s in self.sources if s.id == source_id), None)

    def staleness_hours(self, source: Source) -> int:
        return source.max_staleness_hours or self.envelope.max_staleness_hours


def registry_path(base: Path | None = None) -> Path:
    return (base or Path(__file__).resolve().parent) / REGISTRY_FILE


@lru_cache(maxsize=8)
def load_registry(path: str | None = None) -> Registry:
    """Charge le registre. Mémoïsé : il est figé pour la durée du processus."""
    target = Path(path) if path else registry_path()
    if not target.is_file():
        raise FileNotFoundError(
            f"registre de sources introuvable ({target}) — lancer "
            "`gen_source_tools.py --write`. Sans lui, aucune source n'existe pour l'application.")
    payload: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    return Registry.model_validate(payload)
