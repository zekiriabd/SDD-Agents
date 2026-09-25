#!/usr/bin/env python3
"""Le hash d'un fichier ou d'un index, TEL que les gates l'épinglent — 0 token.

Trois fiches d'agents demandaient `python .sdda/python/sdda_lib/hashing.py
--file …` : un module de bibliothèque, sans `main()`. L'agent recevait une
erreur d'import, et recopiait un hash de mémoire — c'est-à-dire un hash faux,
que la gate suivante refusait (`[CAP_PARENT_HASH_STALE]`) sans que personne
comprenne d'où venait l'écart. Le calcul est déterministe : il appartient à un
script, pas à un prompt.

    python .sdda/sdda.py hash-file --file workspace/pipeline/missions/1-*.md --spec --short
    python .sdda/sdda.py hash-file --file workspace/src/{App}/prompts/billing.system.md
    python .sdda/sdda.py hash-file --index workspace/src/{App}/retrieval/contracts-index --manifest

Ce que chaque option choisit, et pourquoi le choix n'est pas libre :

- `--spec` hache le fichier SANS sa ligne `Status:` (`hashing.sha256_spec_file`).
  C'est le hash que `validate_cap` compare au `Parent MISSION hash`, que
  `validate_topology` compare au `MISSION hash`, et que l'IR épingle dans
  `compiledFrom`. Sans lui, le hash brut change à chaque `Status:` réécrit par
  `compute-status`, et la CAP est périmée avant d'avoir été relue.
- `--short` rend le préfixe que le gabarit de CAP attend
  (`sha256:{mission-hash-8}`, `resolve_cap_hash_sentinel` écrit la même
  longueur) ; `hashes_match` compare par préfixe, donc un préfixe plus long
  reste valide : `--short 12` l'allonge.
- `--index DIR` rend l'`indexHash` d'un index de retrieval — l'empreinte
  canonique de ses fichiers (`hashing.sha256_struct` sur `[{path, sha256}]`
  triés) — et `--manifest` l'écrit dans `DIR/index.manifest.json`, en
  conservant les clés qu'un `dev-retrieval` y a déjà posées (config de chunk,
  modèle d'embedding). C'est cette valeur que le contrat de retrieval épingle
  (`Index Hash:`), que l'IR porte (`retrievers[].indexHash`) et que
  `eval_pinning` fait entrer dans le tuple P10.

Sortie : le hash seul sur stdout (`sha256:<hex>`), pour `HASH=$(…)`. Tout
diagnostic part sur stderr. Exit 0, ou 1 avec un bloc ERROR/CAUSE/FIX.
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, paths  # noqa: E402
from sdda_lib.errors import SddaError  # noqa: E402
from sdda_lib.runtime_io import atomic_write_json, now_iso  # noqa: E402
from sdda_scripts._common import ensure_utf8_stdout  # noqa: E402

MANIFEST_NAME = "index.manifest.json"

#: Longueur du préfixe court : celle du gabarit de CAP (`sha256:{mission-hash-8}`)
#: et de `resolve_cap_hash_sentinel` — un seul chiffre pour les deux écrivains.
SHORT_DEFAULT = 8

#: Ce qu'un index contient et qui n'est PAS l'index : le manifeste lui-même
#: (il porte le hash, il n'y entre pas — sinon le hash se périme en s'écrivant),
#: les temporaires d'une écriture interrompue, le cache de l'interpréteur.
_INDEX_SKIP_NAMES = frozenset({MANIFEST_NAME})
_INDEX_SKIP_SUFFIXES = frozenset({".tmp", ".pyc"})
_INDEX_SKIP_PARTS = frozenset({"__pycache__", ".git"})


def resolve_file(raw: str, root: Path, *, prefer_root: bool = False) -> Path:
    """Le fichier désigné — chemin exact, ou motif glob qui résout à UN fichier.

    Les fiches écrivent `workspace/pipeline/missions/{n}-*.md` : sous Git Bash
    le shell développe l'étoile, sous PowerShell il la transmet telle quelle.
    Le script accepte les deux, et refuse un motif ambigu : deux MISSIONS pour
    un même numéro, c'est un hash pour l'une des deux, et on ne sait pas laquelle.

    Un chemin relatif se résout depuis le répertoire courant, sauf quand
    `--root` a été donné (`prefer_root`) : l'opérateur qui nomme une racine
    veut CE projet, pas celui où il se trouve.
    """
    bases = (root, Path.cwd()) if prefer_root else (Path.cwd(), root)
    for base in bases:
        candidate = base / raw if not Path(raw).is_absolute() else Path(raw)
        if candidate.is_file():
            return candidate
    if any(ch in raw for ch in "*?["):
        for base in bases:
            pattern = raw if Path(raw).is_absolute() else str(base / raw)
            matches = sorted(p for p in map(Path, _glob.glob(pattern)) if p.is_file())
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise SddaError(f"`{raw}` désigne {len(matches)} fichiers", "INVALID_ARG",
                                "préciser le motif jusqu'à un seul fichier",
                                detail=", ".join(p.name for p in matches[:5]))
    raise SddaError(f"`{raw}` introuvable", "INVALID_ARG",
                    "vérifier le chemin (relatif au répertoire courant ou à la racine du projet)")


def has_status_header(path: Path) -> bool:
    """Le fichier porte-t-il un en-tête `Status:` — donc un hash de SPÉCIFICATION ?"""
    try:
        text = markdown_io.read_text(path)
    except (OSError, UnicodeDecodeError):
        return False
    return hashing.spec_text(text) != hashing.normalize_text(text)


def file_hash(path: Path, *, spec: bool) -> str:
    return hashing.sha256_spec_file(path) if spec else hashing.sha256_file(path)


def index_files(directory: Path) -> list[dict[str, str]]:
    """`[{path, sha256}]` de chaque fichier de l'index, chemins POSIX relatifs, triés."""
    out: list[dict[str, str]] = []
    for p in sorted(x for x in directory.rglob("*") if x.is_file()):
        rel = p.relative_to(directory)
        if (p.name in _INDEX_SKIP_NAMES or p.suffix.lower() in _INDEX_SKIP_SUFFIXES
                or _INDEX_SKIP_PARTS & set(rel.parts)):
            continue
        out.append({"path": rel.as_posix(), "sha256": hashing.sha256_file(p)})
    return out


def index_hash(files: list[dict[str, str]]) -> str:
    """L'empreinte de l'index : l'ordre des fichiers ne compte pas, leur contenu compte."""
    return hashing.sha256_struct(sorted((f["path"], f["sha256"]) for f in files))


def write_manifest(directory: Path, files: list[dict[str, str]], digest: str) -> Path:
    """Écrit `index.manifest.json` en CONSERVANT ce que `dev-retrieval` y a déjà mis."""
    path = directory / MANIFEST_NAME
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(markdown_io.read_text(path))
            existing = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            existing = {}
    payload = {**existing, "indexHash": digest, "files": files, "generatedAt": now_iso()}
    return atomic_write_json(path, payload)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Hash d'un fichier (`--file`) ou d'un index (`--index`), tel que les gates l'épinglent (0 token)")
    what = p.add_mutually_exclusive_group(required=True)
    what.add_argument("--file", default=None, help="fichier à hacher ; un motif glob est accepté s'il résout à un seul fichier")
    what.add_argument("--index", default=None, help="répertoire d'un index de retrieval : rend son indexHash")
    p.add_argument("--spec", action="store_true", help="hash de SPÉCIFICATION : `Status:` exclu (MISSION, CAP, topologie, contrats)")
    p.add_argument("--short", nargs="?", const=SHORT_DEFAULT, type=int, default=None, metavar="N",
                   help=f"préfixe court `sha256:{{N hex}}` (défaut N={SHORT_DEFAULT}, celui du gabarit de CAP)")
    p.add_argument("--manifest", action="store_true", help="avec --index : écrire DIR/index.manifest.json (indexHash, files, generatedAt)")
    p.add_argument("--root", type=Path, default=None, help="racine du projet ; défaut : détection depuis le cwd")
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = (args.root or paths.find_root()).resolve()
    try:
        if args.file is not None:
            path = resolve_file(args.file, root, prefer_root=args.root is not None)
            if not args.spec and path.suffix.lower() == ".md" and has_status_header(path):
                sys.stderr.write(
                    f"[hash-file] `{paths.rel(root, path)}` porte un en-tête `Status:` : les gates épinglent "
                    "son hash de SPÉCIFICATION (`--spec`), pas le hash brut.\n")
            digest = file_hash(path, spec=args.spec)
        else:
            directory = Path(args.index) if Path(args.index).is_absolute() else (root / args.index)
            if not directory.is_dir():
                raise SddaError(f"`{args.index}` n'est pas un répertoire", "INVALID_ARG",
                                "désigner le répertoire de l'index (workspace/src/{App}/retrieval/{index-slug})")
            files = index_files(directory)
            if not files:
                raise SddaError(f"`{args.index}` ne contient aucun fichier d'index", "INVALID_ARG",
                                "ingérer le corpus avant de calculer l'indexHash : un index vide n'a pas d'empreinte")
            digest = index_hash(files)
            if args.manifest:
                written = write_manifest(directory, files, digest)
                sys.stderr.write(f"[hash-file] {paths.rel(root, written)} — {len(files)} fichier(s)\n")
    except SddaError as exc:
        sys.stderr.write(exc.format_block() + "\n")
        return 1

    if args.short is not None:
        if args.short < 8 or args.short > 64:
            sys.stderr.write("ERROR: hash-file --short\nCAUSE: [INVALID_ARG] N doit être entre 8 et 64\n"
                             "FIX: `hashes_match` exige au moins 8 caractères hexadécimaux\n")
            return 1
        digest = hashing.short(digest, args.short)
    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
