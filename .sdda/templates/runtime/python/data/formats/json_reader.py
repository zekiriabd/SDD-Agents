"""JSON — `object`, `array`, `jsonl`."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from ..errors import SourceUnavailable
from ..registry import Source


def _walk(payload: Any, dotted: str, source_id: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(payload, dict) or part not in payload:
            raise SourceUnavailable(
                f"`records_path: {dotted}` : segment `{part}` absent", source=source_id)
        payload = payload[part]
    return payload


def read_json(path: Path, source: Source) -> Iterator[dict[str, Any]]:
    encoding = source.encoding or "utf-8"
    if encoding.lower().replace("_", "-") in ("utf-8", "utf8"):
        # Un BOM en tête (export Windows) faisait lever `json.loads` sur la
        # première ligne : la source entière passait pour illisible.
        encoding = "utf-8-sig"

    if source.format == "jsonl":
        # Ligne à ligne : un fichier de 200 Mo ne doit jamais tenir en mémoire
        # d'un coup, et une ligne corrompue en fin de fichier ne doit pas
        # invalider les 400 000 précédentes.
        with path.open("r", encoding=encoding, errors="strict") as handle:
            for line in handle:
                if line.strip():
                    value = json.loads(line)
                    if isinstance(value, dict):
                        yield value
        return

    payload = json.loads(path.read_text(encoding=encoding, errors="strict"))
    if source.records_path:
        payload = _walk(payload, source.records_path, source.id)

    if isinstance(payload, list):
        yield from (item for item in payload if isinstance(item, dict))
    elif isinstance(payload, dict):
        yield payload
