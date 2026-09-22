"""Hashes canoniques (sha256) de fichiers et de structures.

Pourquoi normaliser avant de hasher : un même contrat Markdown édité sous
Windows (CRLF, parfois BOM) et sous Linux (LF) doit donner le MÊME hash, sinon
chaque `Parent MISSION hash` devient périmé au premier checkout croisé et P10
(épinglage) produit du bruit au lieu d'un signal.

Normalisation appliquée au texte :
  - BOM UTF-8 retiré ;
  - CRLF et CR -> LF ;
  - espaces de fin de ligne retirés ;
  - lignes vides finales retirées, puis un unique `\\n` terminal.

Format de sortie : `sha256:<hex64>`. Les fichiers de spécification épinglent
souvent un préfixe court (`sha256:{8 hex}`) : `hashes_match` compare par préfixe.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PREFIX = "sha256:"


def normalize_text(text: str) -> str:
    """Normalise un texte pour que son hash soit indépendant de l'OS d'édition."""
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip(" \t") for line in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def sha256_bytes(data: bytes) -> str:
    return PREFIX + hashlib.sha256(data).hexdigest()


def sha256_text(text: str, *, normalize: bool = True) -> str:
    """Hash d'un texte, normalisé par défaut (voir `normalize_text`)."""
    payload = normalize_text(text) if normalize else text
    return sha256_bytes(payload.encode("utf-8"))


def sha256_file(path: Path) -> str:
    """Hash d'un fichier. Texte UTF-8 -> normalisé ; sinon octets bruts."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return sha256_bytes(raw)
    return sha256_text(text)


def canonical_json(obj: Any) -> str:
    """Sérialisation JSON canonique : clés triées, séparateurs compacts, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_struct(obj: Any) -> str:
    """Hash d'une structure Python JSON-sérialisable (ordre des clés indifférent)."""
    return sha256_bytes(canonical_json(obj).encode("utf-8"))


def short(h: str, n: int = 8) -> str:
    """`sha256:abcdef01…` -> `sha256:abcdef01` (préfixe épinglé dans les specs)."""
    return PREFIX + hex_of(h)[:n]


def hex_of(h: str) -> str:
    return h[len(PREFIX):] if h.startswith(PREFIX) else h


def is_hash_ref(value: str) -> bool:
    """Vrai si `value` a la forme `sha256:{8..64 hex}`."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return False
    hx = hex_of(value)
    return 8 <= len(hx) <= 64 and all(c in "0123456789abcdef" for c in hx)


def hashes_match(pinned: str, current: str) -> bool:
    """Compare un hash épinglé (éventuellement court) au hash courant complet."""
    if not is_hash_ref(pinned) or not is_hash_ref(current):
        return False
    return hex_of(current).startswith(hex_of(pinned))
