"""XLSX et Parquet — dépendances optionnelles, absence explicite.

Ces deux formats exigent un paquet hors stdlib. Leur absence n'est pas un bug :
c'est un choix du `.libs.json` du framework actif. Le message le dit, plutôt que
de laisser un `ModuleNotFoundError` nu remonter au milieu d'un run.

`openpyxl` et `pyarrow` font partie du COMPORTEMENT OBSERVABLE : deux versions
d'openpyxl ne lisent pas une date Excel de la même façon. Ils sont épinglés, et
toute montée rejoue les tests L0 de schéma.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from ..errors import SourceUnavailable
from ..registry import Source


def read_xlsx(path: Path, source: Source) -> Iterator[dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise SourceUnavailable(
            "`openpyxl` absent : la source est déclarée en `format: xlsx`",
            source=source.id,
            detail="ajouter la capability `format-xlsx` au .libs.json du framework actif") from exc

    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[source.sheet] if source.sheet else workbook[workbook.sheetnames[0]]
    header_row = max(1, int(source.header_row or 1))
    header: list[str] = []
    try:
        for index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            if index < header_row:
                continue
            if index == header_row:
                header = [str(c) if c is not None else f"col_{i}" for i, c in enumerate(row)]
                continue
            # `str()` sur tout : les dates Excel sont des nombres, et les
            # convertir ici sans le dire produirait un fuseau implicite. La
            # conversion appartient à l'ingestion, déclarée.
            yield {header[i]: ("" if c is None else str(c))
                   for i, c in enumerate(row) if i < len(header)}
    finally:
        workbook.close()


def read_parquet(path: Path, source: Source) -> Iterator[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise SourceUnavailable(
            "`pyarrow` absent : la source est déclarée en `format: parquet`",
            source=source.id,
            detail="ajouter la capability `format-parquet` au .libs.json du framework actif") from exc

    table = pq.read_table(path)
    for batch in table.to_batches():
        yield from batch.to_pylist()
