#!/usr/bin/env python3
"""Toute option citée par un prompt existe dans le script qu'elle invoque.

`planned_scripts.py` ferme la porte du script réclamé mais jamais écrit. Il
restait la porte voisine, plus discrète et tout aussi coûteuse : le script
existe, la commande l'appelle avec une option qu'il n'a pas.

Cinq l'ont franchie ensemble, et pendant tout un lot :

    validate-topology     --pre
    validate-datasets     --require / --min-items
    validate-tool-contract --static
    eval-runner           --isolated / --dataset

Ce que cela produit n'est pas une erreur visible. `argparse` sort en code 2 sur
`stderr` avec un `usage:`, là où le protocole du framework promet un bloc
`ERROR/CAUSE/FIX` avec une classe `[CLASS]`. L'agent qui orchestre lit une
sortie qu'aucune table de sa fiche ne décrit, conclut que le contrôle « n'a rien
dit », et poursuit. Le post-step censé refuser un contrat d'outil fautif avant
de compiler l'IR n'a alors jamais rien refusé — et rien, nulle part, ne le dit.

C'est le même défaut que les enforcers déclarés dans `INVARIANTS.yml` sans
chemin d'exécution : un contrôle qu'on croit actif coûte plus cher qu'un
contrôle absent, parce qu'on cesse de chercher ailleurs.

Le contrôle est possible parce que les deux bouts sont dérivés du disque :
`sdda_cli.discover()` donne le module d'une sous-commande, et son
`build_parser()` donne ses options réelles. Aucune table à tenir des deux côtés,
donc aucune table à laisser dériver.

Usage :
    python .sdda/sdda.py command-flags
    python .sdda/sdda.py command-flags --json
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import re
import sys
from pathlib import Path

SDDA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SDDA / "python"))

import sdda_cli  # noqa: E402
from sdda_lib.runtime_io import ensure_utf8_stdout  # noqa: E402

ensure_utf8_stdout()

#: La prose qui invoque des scripts. Mêmes fichiers que `planned_scripts.py` :
#: ce sont ceux qu'un modèle lit et exécute.
SCAN = ("agents/*.md", "commands/*.md", "rules/*.md")

#: `python .sdda/sdda.py {cmd}` puis tout ce qui suit jusqu'à la fin de la
#: commande shell. Les continuations `\` et les pipes sont suivies, parce que
#: les commandes écrivent leurs appels sur plusieurs lignes.
#
#: L'alternative de continuation passe EN PREMIER, et ce n'est pas un détail :
#: placée en second, `[^\n`]` consommait le `\` de fin de ligne avant elle, la
#: continuation ne matchait jamais, et le scanner ne voyait que la première
#: ligne de chaque appel. Il se déclarait vert en n'ayant lu qu'un tiers des
#: options — exactement le faux vert qu'il existe pour empêcher.
INVOCATION_RE = re.compile(
    r"python \.sdda/sdda\.py\s+([a-z][a-z0-9-]*)((?:\\\s*\n|[^\n`])*)",
)

#: Une option longue. On ignore volontairement les options courtes : aucune
#: n'est employée dans la prose, et `-1` d'un exemple numérique en serait une.
FLAG_RE = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")

#: Le CONTENU d'une chaîne entre guillemets n'est pas une option.
#: `--fact "brief={--from-brief path | aucun}"` passe une VALEUR qui décrit un
#: gabarit ; la compter comme option accusait `spawn-brief` d'un défaut qu'il
#: n'a pas. Un scanner qui crie au loup se fait désactiver, et on perd le vrai
#: avec — c'est la même raison qui fait qu'un hook dégrade au lieu de bloquer.
QUOTED_RE = re.compile(r'"[^"\n]*"|\'[^\'\n]*\'')

#: Options que la prose écrit en gabarit plutôt qu'en littéral, ou qui sont
#: interprétées par le modèle et non par argparse. Les compter ferait échouer
#: le contrôle sur du texte qui n'est pas une ligne de commande.
IGNORED = frozenset({
    "--force",       # drapeau de commande slash, lu par le modèle
    "--resume",
    "--from-phase",
    "--no-review",
    "--datasets-only",
    "--acceptance",
    "--layer",
    "--recompile-only",
    "--explain",      # convention d'aide de validate_architecture, testée à part
    "--fail-on",
})


def _parser_flags(module_path: str) -> set[str] | None:
    """Les options réelles d'un script, lues depuis son `build_parser()`.

    `None` quand le script n'expose pas de parser nommé : on ne conclut alors
    rien plutôt que d'accuser à tort. Un contrôle qui produit des faux positifs
    se fait désactiver, et on perd le vrai avec.
    """
    pkg, _, stem = module_path.replace(".py", "").partition("/")
    try:
        module = importlib.import_module(f"{pkg}.{stem}")
    except BaseException:  # noqa: BLE001 — un import cassé est le problème d'un autre contrôle
        return None

    builder = getattr(module, "build_parser", None)
    if not callable(builder):
        return None
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            parser = builder()
    except BaseException:  # noqa: BLE001
        return None

    flags: set[str] = set()

    def collect(p: argparse.ArgumentParser) -> None:
        for action in p._actions:  # noqa: SLF001 — argparse n'expose pas d'API publique
            flags.update(o for o in action.option_strings if o.startswith("--"))
            for value in (getattr(action, "choices", None) or {}).values() if isinstance(
                    getattr(action, "choices", None), dict) else ():
                if isinstance(value, argparse.ArgumentParser):
                    collect(value)

    collect(parser)
    return flags


def scan(root: Path = SDDA) -> list[dict[str, object]]:
    """Une entrée par option citée qui n'existe pas dans le script appelé."""
    findings: list[dict[str, object]] = []
    known = sdda_cli.discover()

    for pattern in SCAN:
        for path in sorted(root.glob(pattern)):
            text = path.read_text(encoding="utf-8")
            for match in INVOCATION_RE.finditer(text):
                command, tail = match.group(1), match.group(2)
                if command not in known:
                    continue  # script pas encore écrit : c'est `planned_scripts` qui le dit
                module_path = sdda_cli.resolve(command)
                real = _parser_flags(module_path) if module_path else None
                if real is None:
                    continue
                line = text[: match.start()].count("\n") + 1
                for flag in FLAG_RE.findall(QUOTED_RE.sub(" ", tail)):
                    if flag in IGNORED or flag in real:
                        continue
                    findings.append({
                        "file": f".sdda/{path.relative_to(root).as_posix()}",
                        "line": line,
                        "command": command,
                        "flag": flag,
                        "script": module_path,
                    })
    return findings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Options citées par les prompts mais absentes des scripts appelés")
    p.add_argument("--json", action="store_true", help="sortie machine")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    findings = scan()

    if args.json:
        print(json.dumps({"ok": not findings, "findings": findings}, ensure_ascii=False, indent=2))
        return 1 if findings else 0

    if not findings:
        print("  Toutes les options citées existent dans les scripts appelés.")
        return 0

    print(f"  {len(findings)} option(s) citée(s) et inexistante(s) :\n")
    for f in findings:
        print(f"  {f['file']}:{f['line']}  `{f['command']} {f['flag']}` -> absente de {f['script']}")
    print("\n  Une option inconnue rend un `usage:` argparse, pas un bloc ERROR/CAUSE/FIX :")
    print("  l'agent qui orchestre conclut que le contrôle n'a rien dit, et poursuit.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
