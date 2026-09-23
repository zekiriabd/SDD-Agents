"""Lecteurs de format — un fichier en entrée, des dictionnaires en sortie.

Ils ne font **aucune** conversion implicite : le schéma figé fait foi, et une
valeur convertie en silence est une donnée fausse sans message d'erreur. Le cas
d'école du CSV : `0012345` typé en entier devient `12345`, la jointure ne trouve
plus rien, et l'agent répond « ce client n'existe pas ».

L'encodage est déclaré par source et lu en `errors="strict"` — jamais
`replace`, qui remplacerait un caractère mal décodé par `?` et produirait une
donnée plausible et fausse au milieu d'un run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from ..errors import SourceUnavailable
from ..registry import Source
from .csv_reader import read_csv
from .json_reader import read_json
from .tabular import read_parquet, read_xlsx

__all__ = ["read_records", "read_csv", "read_json", "read_xlsx", "read_parquet"]

_READERS = {
    "object": read_json, "array": read_json, "jsonl": read_json,
    "csv": read_csv, "tsv": read_csv,
    "xlsx": read_xlsx, "parquet": read_parquet,
}


def read_records(path: Path, source: Source) -> Iterator[dict[str, Any]]:
    reader = _READERS.get(str(source.format or ""))
    if reader is None:
        raise SourceUnavailable(
            f"format `{source.format}` sans lecteur", source=source.id,
            detail=f"formats admis : {sorted(_READERS)}")
    try:
        yield from reader(path, source)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        # Le producteur peut réécrire le fichier pendant qu'on le parcourt :
        # le JSON est alors tronqué au milieu. C'est une indisponibilité, pas
        # une erreur de format — et elle mérite un retry unique, pas un abandon.
        raise SourceUnavailable(f"`{path.name}` illisible", source=source.id,
                                detail=f"{exc.__class__.__name__}: {exc}") from exc
