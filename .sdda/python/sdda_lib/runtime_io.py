"""Horloge, écriture atomique, sortie UTF-8 — les trois helpers que huit fichiers recopiaient.

Trois fonctions minuscules, et pourtant huit copies légèrement différentes :
`_now_iso` par-ci sans `SOURCE_DATE_EPOCH`, `default_compiled_at` par-là avec,
un `isoformat(timespec="seconds")` qui rend `+00:00` là où les autres rendent
`Z` — et `sdda_state.bypasses_of` compare ces chaînes lexicographiquement.
Une copie qui dérive n'est pas un défaut de style : c'est un horodatage qui
ne se trie plus avec les autres.

- **Horloge** : `utc_now()` honore `SOURCE_DATE_EPOCH` (builds reproductibles,
  même contrat que `ir_compiler`) et tronque à la seconde. `now_iso()` rend
  `2026-09-22T14:03:00Z` ; `run_id_now()` rend `20260922T140300Z`.
- **Écriture atomique** : temporaire + `os.replace`. Un rapport, une baseline,
  un état de run ne sont jamais lus à moitié écrits.
- **Sortie UTF-8** : `ensure_utf8_stdout()` reconfigure stdout ET stderr — un
  `FIX:` qui arrive abîmé en cp1252 arrive au moment précis où il doit être lu.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any

RUN_ID_FORMAT = "%Y%m%dT%H%M%SZ"


def utc_now(at: _dt.datetime | None = None) -> _dt.datetime:
    """L'instant courant en UTC, à la seconde — ou `SOURCE_DATE_EPOCH` s'il est posé.

    `at` permet de figer l'horloge depuis un appelant (tests, rejeu) sans passer
    par l'environnement.
    """
    if at is not None:
        return at.astimezone(_dt.timezone.utc).replace(microsecond=0) if at.tzinfo else at.replace(microsecond=0, tzinfo=_dt.timezone.utc)
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if epoch.isdigit():
        return _dt.datetime.fromtimestamp(int(epoch), _dt.timezone.utc)
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def now_iso(at: _dt.datetime | None = None) -> str:
    """`2026-09-22T14:03:00Z` — ISO 8601 UTC, suffixe `Z`, jamais `+00:00`."""
    return utc_now(at).isoformat().replace("+00:00", "Z")


def run_id_now(at: _dt.datetime | None = None) -> str:
    """`20260922T140300Z` — l'horodatage compact des identifiants de run."""
    return utc_now(at).strftime(RUN_ID_FORMAT)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Temporaire + `os.replace` : le fichier est entier ou absent, jamais tronqué."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)
    return path


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2, sort_keys: bool = True) -> Path:
    """JSON indenté, clés triées, fin de ligne finale — la forme diffable des rapports."""
    return atomic_write_text(path, json.dumps(payload, indent=indent, ensure_ascii=False, sort_keys=sort_keys) + "\n")


def ensure_utf8_stdout() -> None:
    """Sortie UTF-8 tolérante sur stdout et stderr.

    Sans effet quand le flux est redirigé vers un tampon (tests) ou ne sait pas
    se reconfigurer : un glyphe 🟢 ne doit pas faire planter une console cp1252,
    et un hook ne doit pas planter parce que sa console est étroite.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


_MANGLED_SLASH_COMMAND = __import__("re").compile(r"^[A-Za-z]:[\/](?:.*?[\/])?(sdda[\w-]*)((?:\s.*)?)$")


def slash_command(value: str) -> str:
    """Rend `/sdda-full` tel que l'opérateur l'a tapé, quel que soit le shell.

    Git Bash (MSYS) réécrit tout argument qui commence par `/` en chemin
    Windows : `--command /sdda-full` arrivait en `C:/Program Files/Git/sdda-full`,
    et c'est ce chemin que l'état du run et le journal d'audit gardaient. Un
    audit qui nomme une commande qui n'existe pas ne se relit plus par commande.
    `sdda-full` sans barre est accepté pour la même raison : c'est la forme
    qu'on écrit quand on sait que le shell mange la barre.
    """
    text = str(value or "").strip()
    match = _MANGLED_SLASH_COMMAND.match(text)
    if match:
        return "/" + match.group(1) + match.group(2)
    if text.startswith("sdda"):
        return "/" + text
    return text
