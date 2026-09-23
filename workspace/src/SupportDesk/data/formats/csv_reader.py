"""CSV / TSV — tout est une chaîne, et ça le reste.

Le lecteur ne type RIEN. Le schéma figé porte les types, et c'est lui qui fait
foi. Convertir ici rejouerait, à chaque lecture, l'erreur que l'inférence a déjà
tranchée une fois sous relecture humaine — et le résultat dépendrait alors de la
version de Python plutôt que d'un fichier versionné.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Iterator

from ..registry import Source

# 10 Mo : un champ plus long qu'un roman n'est pas une donnée, c'est un fichier
# corrompu — et le défaut de `csv` (128 Ko) casse sur des exports légitimes.
csv.field_size_limit(10 * 1024 * 1024)


def read_csv(path: Path, source: Source) -> Iterator[dict[str, Any]]:
    delimiter = source.delimiter or ("\t" if source.format == "tsv" else ",")
    header_row = max(1, int(source.header_row or 1))
    text = path.read_text(encoding=source.encoding or "utf-8", errors="strict")

    # `header_row` compte les lignes du fichier, pas celles du CSV : un export
    # avec deux lignes de titre avant l'en-tête est banal, et deviner où
    # commence l'en-tête produirait des colonnes décalées en silence.
    lines = text.split("\n")[header_row - 1:]
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    for row in reader:
        # `None` en clé : une ligne a plus de colonnes que l'en-tête. On écarte
        # le surplus plutôt que de l'inventer un nom.
        yield {k: ("" if v is None else v) for k, v in row.items() if k is not None}
