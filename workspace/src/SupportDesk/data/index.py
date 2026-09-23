"""Index des sources fichier — construit au démarrage, déterministe.

Ce que l'index apporte, et pourquoi il est construit une fois :

  by_key        `lookup` en O(1) sans relire le fichier entier
  as_of         l'instantané de la source, JOINT À CHAQUE RÉPONSE
  content_hash  l'épinglage P10 : une eval rejouée sur des fichiers modifiés
                n'est pas la même eval, et le pipeline doit le savoir plutôt que
                de comparer deux scores incomparables

La frontière de racine est vérifiée **ici**, à la construction, et pas seulement
à la lecture : un lien symbolique ou un fichier hors racine ne doit jamais
entrer dans l'index, sinon chaque appel ultérieur le trouvera légitimement.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .errors import BoundaryViolation, SourceUnavailable
from .formats import read_records
from .registry import Registry, Source


@dataclass(frozen=True)
class Location:
    """Où retrouver un enregistrement : son fichier, et son rang dans ce fichier."""

    path: Path
    offset: int


@dataclass
class SourceIndex:
    source_id: str
    files: list[Path] = field(default_factory=list)
    by_key: dict[str, Location] = field(default_factory=dict)
    count: int = 0
    bytes: int = 0
    as_of: str = ""
    content_hash: str = ""

    @property
    def age_hours(self) -> float:
        if not self.as_of:
            return 0.0
        try:
            stamp = datetime.fromisoformat(self.as_of)
        except ValueError:
            return 0.0
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds() / 3600.0)


def resolve_root(registry: Registry, source: Source, base: Path) -> Path:
    store = registry.store(source.store)
    if store is None or not store.root:
        raise SourceUnavailable(f"store `{source.store}` sans racine", source=source.id)
    candidate = Path(store.root)
    root = candidate if candidate.is_absolute() else (base / candidate)
    try:
        return root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SourceUnavailable(f"racine `{store.root}` introuvable", source=source.id,
                                detail=str(exc)) from exc


def matching_files(root: Path, source: Source) -> list[Path]:
    """Les fichiers du glob, **dans la racine**, liens exclus.

    Un glob absolu ou remontant (`..`) est refusé avant toute expansion : la
    racine est la frontière, pas une convention de nommage.
    """
    glob = source.glob or ""
    if glob.startswith(("/", "\\")) or ".." in Path(glob).parts:
        raise BoundaryViolation(f"glob `{glob}` sort de la racine du store", source=source.id)

    found: list[Path] = []
    for path in sorted(root.glob(glob)):
        if not path.is_file():
            continue
        if path.is_symlink():
            # `SYMLINK_FOLLOW` est interdit par l'enveloppe : un lien dans une
            # racine montée en lecture seule est un contournement de frontière,
            # pas une commodité de rangement.
            raise BoundaryViolation(f"lien symbolique dans la sélection : `{path.name}`",
                                    source=source.id)
        if not path.resolve().is_relative_to(root):
            raise BoundaryViolation(f"`{path.name}` résout hors de la racine", source=source.id)
        found.append(path)
    return found


def build_index(registry: Registry, source: Source, base: Path) -> SourceIndex:
    root = resolve_root(registry, source, base)
    files = matching_files(root, source)
    index = SourceIndex(source_id=source.id, files=files)
    if not files:
        return index

    signature = hashlib.sha256()
    newest = 0.0
    for path in files:
        stat = path.stat()
        index.bytes += stat.st_size
        newest = max(newest, stat.st_mtime)
        # Le hash porte sur les MÉTADONNÉES, pas le contenu : relire 200 Mo à
        # chaque démarrage pour détecter un changement coûterait plus cher que
        # ce que l'épinglage fait gagner.
        signature.update(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())

    index.as_of = datetime.fromtimestamp(newest, tz=timezone.utc).isoformat()
    index.content_hash = "sha256:" + signature.hexdigest()

    for path in files:
        for offset, record in enumerate(read_records(path, source)):
            index.count += 1
            raw = record.get(source.key)
            if raw is None:
                continue
            key = str(raw)
            if key in index.by_key:
                # Deux exports (complet + incrémental) portant la même clé :
                # `by_key` garderait silencieusement le dernier lu, et l'ordre
                # du glob dépend du système de fichiers. Fail-fast, avec les
                # deux emplacements — sinon la réponse dépend de la machine.
                first = index.by_key[key]
                raise SourceUnavailable(
                    f"clé `{key}` en double", source=source.id,
                    detail=f"{first.path.name}#{first.offset} et {path.name}#{offset}")
            index.by_key[key] = Location(path=path, offset=offset)
    return index


def record_at(location: Location, source: Source) -> dict | None:
    """Relit l'enregistrement à son emplacement. L'index ne garde aucune donnée.

    Conserver les enregistrements en mémoire ferait tenir la source entière dans
    le processus — et `as_of` mentirait dès la première réécriture du fichier.
    """
    for offset, record in enumerate(read_records(location.path, source)):
        if offset == location.offset:
            return record
    return None
