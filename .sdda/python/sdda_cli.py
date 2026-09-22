"""`sdda` — un point d'entrée unique pour les 60 scripts déterministes (0 token).

Pourquoi un dispatcher plutôt que 60 chemins à cinq segments : chaque appel
écrit dans un prompt d'agent ou une commande coûtait
`python .sdda/sdda.py validate-mission` — 44 caractères de
plomberie avant le premier argument utile. Multiplié par les ~140 appels que
portent les 22 fiches d'agents et les 11 commandes, c'est un budget de contexte
dépensé à répéter une arborescence que personne ne lit. La forme courte est
`python .sdda/sdda.py validate-mission` : même script, même code de sortie, 24
caractères de moins, et un seul chemin à corriger si l'arborescence bouge.

Deux façons d'appeler, la même fonction derrière :

    python .sdda/sdda.py validate-mission --mission 1   # depuis un clone nu
    sdda validate-mission --mission 1                   # après `pip install -e .sdda/python`

La première est celle qu'écrivent les prompts et les commandes : elle ne
suppose **aucune installation**. Un framework dont les prompts exigent un
`pip install` préalable échoue au premier clone, et l'agent qui reçoit
`command not found` invente la sortie du script plutôt que de s'arrêter.

Le registre des sous-commandes est **dérivé du disque**, jamais écrit à la
main : tout module de `sdda_scripts/`, `sdda_admin/` ou `sdda_hooks/` portant
un `main()` est une sous-commande, nommée par son fichier (`validate_mission.py`
-> `validate-mission`). Un script ajouté est disponible sans que personne ne
tienne une table à jour — c'est la même règle que le registre d'erreurs
(`rules/error-classification.md §6`) : une liste tenue à la main dérive dans
les deux sens.

`sdda_state.py` perd son préfixe (`state`, pas `sdda-state`) : `sdda sdda-state`
serait une redite, et c'est le seul module préfixé du dépôt.

Ce module est aussi la **SSoT que lisent les scanners** : `planned_scripts.py`
et `framework_smoke.py` résolvent `python .sdda/sdda.py {cmd}` vers son module
par `resolve()`, pour continuer de détecter un script promis mais pas écrit.
Sans cela, migrer la prose vers la forme courte aurait rendu muet le contrôle
qui empêche un prompt d'annoncer un outil qui n'existe pas.
"""
from __future__ import annotations

import difflib
import importlib
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Any, Callable

#: Les paquets balayés, dans l'ordre de résolution. `sdda_hooks` y est parce
#: que deux commandes appellent des hooks en post-step comme des scripts
#: ordinaires ; leur câblage dans les façades garde le chemin complet
#: (`harness_build.py`), qui est une déclaration d'emplacement, pas un appel.
PACKAGES: tuple[str, ...] = ("sdda_scripts", "sdda_admin", "sdda_hooks")

#: Le paquet où vit un script qu'un prompt réclame mais que personne n'a encore
#: écrit : c'est là que vivent les scripts appelés par les agents et les
#: commandes, donc c'est le chemin que `PLANNED-SCRIPTS.md` doit annoncer.
DEFAULT_PACKAGE = "sdda_scripts"

LAUNCHER = "python .sdda/sdda.py"

PREFIX = "sdda_"


def _subcommand(stem: str) -> str:
    """`validate_mission` -> `validate-mission` ; `sdda_state` -> `state`."""
    base = stem[len(PREFIX):] if stem.startswith(PREFIX) else stem
    return base.replace("_", "-")


def _module_stem(name: str) -> str:
    """L'inverse : `validate-mission` -> `validate_mission`, `state` -> `sdda_state`."""
    stem = name.replace("-", "_")
    return stem if stem != "state" else "sdda_state"


def discover() -> dict[str, tuple[str, str]]:
    """{sous-commande: (paquet, module)} — dérivé du disque, jamais d'une table.

    `pkgutil` plutôt qu'un glob : le registre reste juste que le paquet soit lu
    depuis les sources ou depuis une roue installée.
    """
    out: dict[str, tuple[str, str]] = {}
    for package in PACKAGES:
        try:
            mod = importlib.import_module(package)
        except ImportError:
            continue
        for info in pkgutil.iter_modules(list(getattr(mod, "__path__", []))):
            if info.ispkg or info.name.startswith("_"):
                continue
            out.setdefault(_subcommand(info.name), (package, info.name))
    return out


def resolve(name: str) -> str | None:
    """`validate-mission` -> `sdda_scripts/validate_mission.py` ; None si inconnue.

    Lue par `planned_scripts.py` et `framework_smoke.py` : ce qui ne résout pas
    ici est un script réclamé par un prompt et pas encore écrit.
    """
    found = discover().get(name)
    return f"{found[0]}/{found[1]}.py" if found else None


def expected_path(name: str) -> str:
    """Le chemin qu'AURAIT une sous-commande inconnue, pour que l'inventaire la nomme."""
    return f"{DEFAULT_PACKAGE}/{_module_stem(name)}.py"


def _call(entry: Callable[..., Any], rest: list[str]) -> int:
    """Appelle le `main()` d'un script, quelle que soit sa signature.

    39 scripts prennent `argv`, 21 lisent `sys.argv` : les deux formes
    existent depuis le début et les unifier toucherait 60 fichiers pour un
    gain nul. `sys.argv` est posé dans les deux cas, pour qu'argparse écrive
    `usage: sdda {cmd}` plutôt que le nom du lanceur.
    """
    try:
        takes_argv = bool([
            p for p in inspect.signature(entry).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ])
    except (TypeError, ValueError):
        takes_argv = False
    return int(entry(rest) or 0) if takes_argv else int(entry() or 0)


def render_list(commands: dict[str, tuple[str, str]]) -> str:
    """Les sous-commandes, groupées par paquet — ce que `sdda` sans argument écrit."""
    titles = {
        "sdda_scripts": "sdda_scripts/ — gates, compilation, évaluation, état",
        "sdda_admin": "sdda_admin/ — artefacts dérivés du framework (registres, façades, digests)",
        "sdda_hooks": "sdda_hooks/ — gates bloquantes, appelables aussi en post-step",
    }
    lines = [f"sdda — {len(commands)} sous-commandes déterministes (0 token, 0 réseau)", ""]
    for package in PACKAGES:
        names = sorted(n for n, (pkg, _) in commands.items() if pkg == package)
        if not names:
            continue
        lines += [f"  {titles.get(package, package)}", ""]
        for i in range(0, len(names), 3):
            lines.append("    " + "  ".join(f"{n:<30}" for n in names[i:i + 3]).rstrip())
        lines.append("")
    lines += [
        f"  {LAUNCHER} {{sous-commande}} --help   pour les arguments de l'une d'elles",
        "  `sdda {sous-commande}` est équivalent après `pip install -e .sdda/python`.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    commands = discover()

    if not args or args[0] in ("-h", "--help", "help", "--list"):
        from sdda_lib.runtime_io import ensure_utf8_stdout

        ensure_utf8_stdout()
        print(render_list(commands))
        return 0

    name, rest = args[0], args[1:]
    found = commands.get(name)
    if found is None:
        near = difflib.get_close_matches(name, commands, n=3, cutoff=0.6)
        sys.stderr.write(
            f"ERROR: sdda {name} — sous-commande inconnue\n"
            f"CAUSE: [CLI_COMMAND_UNKNOWN] aucun module `{expected_path(name)}` "
            f"ni équivalent dans {', '.join(PACKAGES)}\n"
            f"FIX: {'essayer ' + ', '.join(near) + ' ; ou ' if near else ''}"
            f"`{LAUNCHER}` pour la liste complète\n"
        )
        return 1

    package, stem = found
    module = importlib.import_module(f"{package}.{stem}")
    entry = getattr(module, "main", None)
    if not callable(entry):
        sys.stderr.write(
            f"ERROR: sdda {name} — module non exécutable\n"
            f"CAUSE: [CLI_COMMAND_UNKNOWN] `{package}/{stem}.py` n'expose pas de `main()`\n"
            f"FIX: ajouter `def main(argv=None) -> int` à ce module, ou l'appeler autrement\n"
        )
        return 1

    sys.argv = [f"sdda {name}", *rest]
    return _call(entry, rest)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
