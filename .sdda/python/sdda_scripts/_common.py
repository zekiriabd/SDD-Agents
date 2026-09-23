"""Plomberie CLI partagée par les scripts : racine, config, sortie, code retour."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permet `python sdda_scripts/x.py` sans installation du paquet.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths  # noqa: E402
from sdda_lib.errors import Report, SddaError, emit  # noqa: E402
from sdda_lib.layered_config import LayeredConfig, read_layered_config  # noqa: E402


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=None, help="racine du projet (contient workspace/) ; défaut : détection depuis le cwd")
    parser.add_argument("--json", action="store_true", help="sortie machine JSON")
    parser.add_argument("--no-report", action="store_true", help="ne pas écrire de rapport de gate dans workspace/.sys/.validation/")


def resolve_root(args: argparse.Namespace) -> Path:
    # Chaque script passe ici juste après `parse_args` : c'est l'endroit qui
    # garantit que sa sortie est en UTF-8. Huit scripts de gate (validate-*,
    # ir-compiler, estimate-budget, compute-status) ne l'appelaient pas et
    # plantaient en `UnicodeEncodeError` sur une console Windows cp1252 en
    # affichant leur propre rapport — un verdict rendu illisible par un
    # accent n'est pas un verdict. Idempotent : le rappeler ne coûte rien.
    ensure_utf8_stdout()
    return (args.root or paths.find_root()).resolve()


def load_config(root: Path, report: Report) -> LayeredConfig:
    """Config en 3 couches ; une violation devient un finding et une config vide est rendue."""
    try:
        return read_layered_config(root)
    except SddaError as exc:
        report.findings.append(exc.to_finding())
        return LayeredConfig(config={}, sources={})


def finish(report: Report, args: argparse.Namespace) -> int:
    return emit(report, args.json)


from sdda_lib.runtime_io import ensure_utf8_stdout  # noqa: E402,F401 — ré-exporté : tous les scripts l'importent d'ici
