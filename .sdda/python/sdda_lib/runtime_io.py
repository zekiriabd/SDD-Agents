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
- **Append verrouillé** : `append_line()` ajoute une ligne à un journal
  (`traces`, `runs.jsonl`, `bypasses.jsonl`) sous verrou exclusif entre
  processus. Ici plutôt que dans `tracing`, parce que ce module n'importe rien
  du framework — tout le monde peut l'importer sans boucle.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

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
    """`20260922T140300Z` — l'horodatage compact des identifiants de run.

    `SOURCE_DATE_EPOCH` n'est PAS honoré ici : il fige les horodatages pour
    rendre un artefact reproductible, mais un identifiant de run doit rester
    unique — figé, tous les runs écrivaient dans la même trace, qui finissait
    avec N spans racines. `at` reste le moyen de figer l'horloge en test.
    """
    if at is not None:
        return utc_now(at).strftime(RUN_ID_FORMAT)
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).strftime(RUN_ID_FORMAT)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Temporaire + `os.replace` : le fichier est entier ou absent, jamais tronqué.

    Temporaire UNIQUE (`mkstemp`, même répertoire) : avec un nom fixe
    (`x.json.tmp`), deux écrivains du même rapport se marchaient dessus, et un
    temporaire restait sur disque après une erreur. `fsync` avant le
    remplacement : sans lui, une coupure peut rendre un fichier vide. Sous
    Windows, `os.replace` échoue si un lecteur (un hook) tient la cible ouverte :
    quelques tentatives brèves plutôt qu'un rapport perdu.
    """
    import tempfile  # noqa: PLC0415

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        # `newline="\n"` : sans lui, Windows traduit chaque `\n` en `\r\n`, et un
        # ajout à un JSONL réécrit tout le fichier en CRLF — le diff d'un jeu
        # append-only montre alors toutes les lignes changées, pas celles ajoutées.
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2, sort_keys: bool = True) -> Path:
    """JSON indenté, clés triées, fin de ligne finale — la forme diffable des rapports."""
    return atomic_write_text(path, json.dumps(payload, indent=indent, ensure_ascii=False, sort_keys=sort_keys) + "\n")


# ---------------------------------------------------------------------------
# Append atomique entre PROCESSUS
# ---------------------------------------------------------------------------
#: Octet verrouillé sous Windows : loin après toute fin de fichier plausible
#: (2 Gio - 1), pour que le verrou — obligatoire sous Windows, pas consultatif —
#: ne bloque jamais un LECTEUR du fichier. `msvcrt.locking` verrouille à partir
#: de la position courante ; `O_APPEND` repositionne en fin avant chaque
#: écriture, donc la position choisie pour le verrou ne déplace aucune ligne.
_WIN_LOCK_OFFSET = 0x7FFFFFFF
#: Attente maximale du verrou. Au-delà, on écrit quand même : perdre un span de
#: trace parce qu'un autre processus est mort en tenant le verrou serait pire
#: qu'un risque d'entrelacement, et le lecteur ignore une ligne illisible.
LOCK_TIMEOUT_S = 10.0

if sys.platform == "win32":  # pragma: no cover - dépend de la plateforme
    import msvcrt

    def _lock(fd: int) -> bool:
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        delay = 0.001
        while True:
            os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(delay)
                delay = min(delay * 2, 0.05)

    def _unlock(fd: int) -> None:
        os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:  # pragma: no cover - dépend de la plateforme
    import fcntl

    def _lock(fd: int) -> bool:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _exclusive(fd: int) -> Iterator[None]:
    held = _lock(fd)
    try:
        yield
    finally:
        if held:
            _unlock(fd)


def append_line(path: Path, line: str) -> None:
    """Ajoute UNE ligne à `path`, sans qu'elle puisse s'entrelacer avec celle d'un autre processus.

    `open("a")` puis `write` ne suffisait pas : le tampon d'`io` découpe une
    ligne longue en plusieurs appels système, et sous Windows le mode append du
    CRT n'est pas atomique entre processus (positionnement en fin, PUIS
    écriture). Quatre évaluations parallèles (`EvalMaxParallel: 4`) sur le même
    fichier produisaient donc, de temps à autre, deux spans collés sur une ligne
    — que `read_spans` ignore comme illisibles. Un span perdu ne se voit pas ;
    c'est un appel d'outil absent de l'audit de scope, ou un coût qui manque.
    Même mécanique pour `runs.jsonl` et `bypasses.jsonl` : un run entrelacé ne
    se reprend plus, un bypass entrelacé n'est plus audité.

    Ici : les octets de la ligne sont construits d'abord, puis écrits sous un
    verrou exclusif (`msvcrt.locking` / `fcntl.flock`), sur un descripteur brut
    ouvert en `O_APPEND`, en boucle jusqu'au dernier octet. Le verrou est ce qui
    rend l'ensemble atomique ; `O_APPEND` est ce qui garantit la fin de fichier.
    """
    data = (line.rstrip("\n") + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags, 0o644)
    try:
        with _exclusive(fd):
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
    finally:
        os.close(fd)


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
            # Ignorable, et voulu (cf. docstring) : un tampon de test ou un
            # tube n'a pas `reconfigure`, et le flux garde alors son encodage.
            pass


_MANGLED_SLASH_COMMAND = __import__("re").compile(r"^[A-Za-z]:[\\/](?:.*?[\\/])?(sdda[\w-]*)((?:\s.*)?)$")


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
