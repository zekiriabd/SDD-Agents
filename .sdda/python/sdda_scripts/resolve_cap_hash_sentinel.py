#!/usr/bin/env python3
"""Remplace le sentinel `Parent MISSION hash: sha256:COMPUTE_REQUIRED` des CAPs.

`po-capabilities` n'a pas `Bash` : il ne peut pas hasher la MISSION, et on ne
veut pas qu'il le fasse — un agent qui calcule un hash de tête l'invente. Il
écrit donc un **sentinel** et la commande le résout en post-step, 0 token,
idempotent : `/sdda-caps` STEP 5.bis.

Ce que le script fait, et rien d'autre :

    - lit la MISSION `{n}` et calcule son hash (`validate_mission.parse_mission`,
      le MÊME calcul que la CAP GATE — deux calculs seraient deux vérités) ;
    - dans chaque `workspace/caps/{n}-*-*.md`, remplace le sentinel (ou un
      `<placeholder>`) par `sha256:{8}` ; un hash déjà posé n'est PAS touché,
      même périmé — c'est `validate_cap` qui juge `[CAP_PARENT_HASH_STALE]`,
      et réécrire ici masquerait exactement ce qu'il cherche ;
    - relit et vérifie qu'aucun sentinel ne subsiste.

Exit : 0 résolu (ou rien à faire) · 2 le sentinel persiste après écriture
[CAP_HASH_PLACEHOLDER] · 3 MISSION introuvable ou disque en lecture seule
[INFRA_BLOCKED].

Usage :
    python resolve_cap_hash_sentinel.py --mission 1
    python resolve_cap_hash_sentinel.py --mission 1 --check   # ne rien écrire, dire
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_scripts import validate_mission  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

SENTINEL_RE = re.compile(
    r"^(?P<key>Parent MISSION hash:\s*)(?P<value>sha256:COMPUTE_REQUIRED|sha256:\{[^}\n]*\}|<[^>\n]*>)\s*$",
    re.M,
)

EXIT_OK, EXIT_PERSISTS, EXIT_INFRA = 0, 2, 3


def mission_hash(root: Path, number: int) -> tuple[str | None, str]:
    """(hash court, chemin) de la MISSION `{n}`, ou (None, raison)."""
    candidates = sorted(paths.missions_dir(root).glob(f"{number}-*.md"))
    if not candidates:
        return None, f"aucune MISSION `{number}-*.md` dans {paths.rel(root, paths.missions_dir(root))}"
    if len(candidates) > 1:
        return None, f"{len(candidates)} fichiers `{number}-*.md` : la MISSION est ambiguë"
    spec = validate_mission.parse_mission(markdown_io.read_text(candidates[0]), candidates[0])
    return hashing.short(spec.hash), paths.rel(root, candidates[0])


def resolve_file(path: Path, short_hash: str, *, write: bool) -> tuple[int, int]:
    """(sentinels trouvés, sentinels restants après traitement)."""
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    found = len(SENTINEL_RE.findall(text))
    if not found:
        return 0, 0
    if not write:
        return found, found
    new_text = SENTINEL_RE.sub(lambda m: f"{m.group('key')}{short_hash}", text)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(new_text)
    tmp.replace(path)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        remaining = len(SENTINEL_RE.findall(fh.read()))
    return found, remaining


def run(root: Path, number: int, *, write: bool = True) -> tuple[Report, int]:
    report = Report(name="CAP.hash-sentinel", target=f"mission {number}")
    short_hash, where = mission_hash(root, number)
    if short_hash is None:
        report.error("INFRA_BLOCKED", where, "vérifier workspace/missions/ ; /sdda-mission écrit `{n}-{Name}.md`")
        return report, EXIT_INFRA
    report.data.update({"mission": where, "hash": short_hash, "files": []})

    caps = sorted(paths.caps_dir(root).glob(f"{number}-*-*.md"))
    persisting: list[str] = []
    resolved = 0
    for cap in caps:
        loc = paths.rel(root, cap)
        try:
            found, remaining = resolve_file(cap, short_hash, write=write)
        except OSError as exc:
            report.error("INFRA_BLOCKED", f"`{loc}` : {exc}", "vérifier les permissions du système de fichiers", loc)
            return report, EXIT_INFRA
        report.data["files"].append({"path": loc, "sentinels": found, "remaining": remaining})
        if write and remaining:
            persisting.append(loc)
        resolved += found - remaining

    report.data.update({"resolved": resolved, "caps": len(caps)})
    if persisting:
        report.error("CAP_HASH_PLACEHOLDER",
                     f"le sentinel persiste dans {len(persisting)} CAP(s) après écriture : {', '.join(persisting[:4])}",
                     "vérifier les permissions FS et l'encodage du fichier ; relancer", persisting[0])
        return report, EXIT_PERSISTS
    if not write:
        pending = sum(f["sentinels"] for f in report.data["files"])
        if pending:
            report.warn("CAP_HASH_PLACEHOLDER", f"{pending} sentinel(s) à résoudre (--check : rien écrit)",
                        f"relancer sans --check pour écrire `{short_hash}`")
    return report, EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Résout le sentinel `Parent MISSION hash` des CAPs (0 token, idempotent)")
    p.add_argument("--mission", type=int, required=True, help="numéro de mission")
    p.add_argument("--check", action="store_true", help="ne rien écrire ; dire ce qui serait résolu")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report, code = run(root, args.mission, write=not args.check and not args.no_report)
    if not args.json and code == EXIT_OK:
        print(f"  {report.data.get('resolved', 0)} sentinel(s) résolu(s) sur {report.data.get('caps', 0)} CAP(s) -> {report.data.get('hash')}")
    finish(report, args)
    return code


if __name__ == "__main__":
    sys.exit(main())
