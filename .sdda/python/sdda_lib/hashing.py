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
import re
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


_STATUS_HEADER_RE = re.compile(r"^Status:[ \t]*[^\n]*\n?", re.M)


def spec_text(text: str) -> str:
    """Le texte d'un fichier de SPÉCIFICATION tel qu'on le hashe : sans sa ligne `Status:`.

    `Status:` n'est pas une spécification, c'est un ÉTAT DÉRIVÉ des rapports de
    gate (LIFECYCLE R1) — `compute_status.py` l'écrit et le réécrit. Le laisser
    dans le hash faisait qu'une gate verte périmait elle-même : G0 épinglait la
    MISSION avec `Status: Blocked` (écrit sur l'échec précédent), le statut
    calculé repassait à `Draft`, le script réécrivait l'en-tête, et le rapport
    G0 devenait `stale` sans qu'une ligne de la spécification ait changé. Même
    boucle pour le `Parent MISSION hash` des CAPs et les `compiledFrom` de l'IR.

    Seule la ligne `Status:` de l'EN-TÊTE est retirée (avant le premier titre
    `## `) : un `Status:` dans le corps est du contenu.
    """
    normalized = normalize_text(text)
    cut = re.search(r"^## ", normalized, re.M)
    head, body = (normalized[:cut.start()], normalized[cut.start():]) if cut else (normalized, "")
    return _STATUS_HEADER_RE.sub("", head, count=1) + body


def sha256_spec_text(text: str) -> str:
    """Hash d'une spécification (MISSION, CAP, topologie) — `Status:` exclu, cf. `spec_text`."""
    return sha256_text(spec_text(text), normalize=False)


def sha256_spec_file(path: Path) -> str:
    """Hash d'un fichier de spécification — `Status:` exclu, cf. `spec_text`."""
    return sha256_spec_text(Path(path).read_text(encoding="utf-8-sig"))


#: Sections d'une CAP écrites par un AUTRE owner que celui de la spécification.
#: `## Allocated To` appartient à `architect-topology` (Edit narrow, PHASE 2) :
#: c'est une projection de la topologie dans la CAP, pas une exigence.
CAP_FOREIGN_SECTIONS: tuple[str, ...] = ("Allocated To",)


def cap_spec_text(text: str) -> str:
    """Le texte d'une CAP tel que G1 le juge : sans `Status:` NI `## Allocated To`.

    G1 valide ce que `po-capabilities` a écrit — AC, métriques, seuils, `Covers`.
    `## Allocated To` est rempli ensuite par `architect-topology` en PHASE 2
    (`rules/ownership.md` : Edit narrow, ce champ seul). Le laisser dans le hash
    de G1 faisait de la PHASE 2 la cause de la péremption de la PHASE 1 : la
    topologie remplissait l'allocation, le hash de la CAP bougeait, G1 devenait
    `stale`, la MISSION redescendait à `Draft` et `--resume` repartait en PHASE 1
    — pour repayer `po-capabilities` sur une spécification que personne n'avait
    touchée. Une gate ne doit se périmer que sur ce qu'elle a jugé.

    Ce n'est pas une porte ouverte : l'allocation reste dans le hash COMPLET de
    la CAP (`sha256_spec_text`), que G2 et l'IR épinglent (`cap:{id}`). Une
    réallocation après compilation périme donc la topologie, qui est bien le
    niveau qu'elle remet en cause.
    """
    body = spec_text(text)
    for title in CAP_FOREIGN_SECTIONS:
        # La section court de son titre jusqu'au titre `## ` suivant (ou la fin).
        body = re.sub(rf"^## {re.escape(title)}[ \t]*\n(?:(?!## ).*\n?)*", "", body, flags=re.M)
    return body


def sha256_cap_spec_text(text: str) -> str:
    """Hash d'une CAP tel que G1 l'épingle — `Status:` et `## Allocated To` exclus."""
    return sha256_text(cap_spec_text(text), normalize=False)


def sha256_cap_spec_file(path: Path) -> str:
    """Hash d'un fichier de CAP tel que G1 l'épingle, cf. `cap_spec_text`."""
    return sha256_cap_spec_text(Path(path).read_text(encoding="utf-8-sig"))


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
