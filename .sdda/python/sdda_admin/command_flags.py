#!/usr/bin/env python3
"""Toute ligne de commande citée par un prompt se PARSE dans le script qu'elle invoque.

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

**Deux passes, parce qu'une seule laissait passer le positionnel.** La
première compare les options longues citées à celles du parser. Elle se
déclarait verte sur `validate-ir workspace/.sys/.ir/{n}-system.ir.json` : aucune
option inconnue, et pourtant argparse refuse la ligne (`unrecognized
arguments`), et la fiche de `dev-orchestration` concluait « IR rouge, STOP ».
La seconde passe PARSE réellement la ligne (`parse_known_args`), gabarits
substitués : positionnel en trop, sous-commande inconnue, argument requis
absent — tout ce qu'argparse refuserait au runtime.

**Les scripts sans `build_parser()` ne sont plus sautés.** Sept scripts
construisaient leur parser dans `main()` et échappaient au contrôle — dont
`validate-safety-gate` et `validate-packaging`. Leur parser est capturé au
moment où `main()` l'utilise : `parse_args` est intercepté AVANT toute
exécution, donc rien ne tourne. Les hooks, qui ne parsent pas avec argparse,
sont jugés contre les options que `_hook.parse_argv` accepte.

Le contrôle est possible parce que les deux bouts sont dérivés du disque :
`sdda_cli.discover()` donne le module d'une sous-commande, et son parser donne
ses options réelles. Aucune table à tenir des deux côtés, donc aucune table à
laisser dériver.

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
import shlex
import sys
from pathlib import Path
from typing import Any

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

#: Ce qui termine une commande shell dans la prose : pipe, chaînage,
#: redirection, commentaire, parenthèse de prose (`(/sdda-build STEP 3.0)`).
_STOP_TOKENS = frozenset({"|", "||", "&&", ";", "&"})
_STOP_PREFIXES = (">", "2>", "1>", "#", "(", "<")

#: `$( … )` : une sous-commande shell — ses options sont jugées par la passe
#: FLAG, pas par le parsing de la ligne qui l'englobe.
_SUBSHELL_RE = re.compile(r"\$\([^()]*\)")
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_ANGLE_RE = re.compile(r"<[^<>\s]*>")

#: Valeur de substitution d'un gabarit sans alternatives (`{n}`, `$RUN_ID`).
#: Un entier : c'est le type le plus exigeant qu'un gabarit porte (`--mission`).
_FILL = "1"


class _Captured(Exception):
    """Le parser d'un `main()`, intercepté avant qu'il ne parse quoi que ce soit."""

    def __init__(self, parser: argparse.ArgumentParser):
        super().__init__("parser capturé")
        self.parser = parser


def _capture_main_parser(module: Any) -> argparse.ArgumentParser | None:
    """Le parser qu'un `main()` construit, sans exécuter la suite de `main()`.

    Les scripts concernés construisent leur parser en tête de `main()` ; on
    intercepte `parse_known_args` (que `parse_args` appelle), qui lève avant tout
    travail. Si `main()` fait autre chose avant de parser, on n'en saura rien :
    l'exception d'interception n'est jamais atteinte, on rend `None`, et le
    contrôle s'abstient au lieu d'accuser.
    """
    main = getattr(module, "main", None)
    if not callable(main):
        return None
    original = argparse.ArgumentParser.parse_known_args

    def intercept(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise _Captured(self)

    saved_argv, saved_stdin = sys.argv, sys.stdin
    argparse.ArgumentParser.parse_known_args = intercept  # type: ignore[method-assign]
    sys.stdin = io.StringIO("")
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                main([])
            except TypeError:
                sys.argv = [getattr(module, "__name__", "sdda")]
                main()
    except _Captured as cap:
        return cap.parser
    except BaseException:  # noqa: BLE001 — un main qui échoue n'est pas l'objet de ce contrôle
        return None
    finally:
        argparse.ArgumentParser.parse_known_args = original  # type: ignore[method-assign]
        sys.argv, sys.stdin = saved_argv, saved_stdin
    return None


def _load_parser(module_path: str) -> argparse.ArgumentParser | None:
    """Le parser réel d'un script : `build_parser()`, sinon celui de `main()`."""
    pkg, _, stem = module_path.replace(".py", "").partition("/")
    if pkg == "sdda_hooks":
        return None  # jugés par `_hook_flags`
    try:
        module = importlib.import_module(f"{pkg}.{stem}")
    except BaseException:  # noqa: BLE001 — un import cassé est le problème d'un autre contrôle
        return None

    builder = getattr(module, "build_parser", None)
    if callable(builder):
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return builder()
        except BaseException:  # noqa: BLE001
            return None
    return _capture_main_parser(module)


def _parser_flags(module_path: str) -> set[str] | None:
    """Les options réelles d'un script (sous-parsers compris).

    `None` quand aucun parser n'est trouvable : on ne conclut alors rien plutôt
    que d'accuser à tort. Un contrôle qui produit des faux positifs se fait
    désactiver, et on perd le vrai avec.
    """
    if module_path.startswith("sdda_hooks/"):
        return _hook_flags()
    parser = _load_parser(module_path)
    if parser is None:
        return None

    flags: set[str] = set()

    def collect(p: argparse.ArgumentParser) -> None:
        for action in p._actions:  # noqa: SLF001 — argparse n'expose pas d'API publique
            flags.update(o for o in action.option_strings if o.startswith("--"))
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                for value in choices.values():
                    if isinstance(value, argparse.ArgumentParser):
                        collect(value)

    collect(parser)
    return flags


def _hook_flags() -> set[str] | None:
    """Les options qu'un hook accepte en ligne de commande (`_hook.ARGV_KEYS`)."""
    try:
        sys.path.insert(0, str(SDDA / "python" / "sdda_hooks"))
        import _hook  # noqa: PLC0415
    except BaseException:  # noqa: BLE001
        return None
    return set(getattr(_hook, "ARGV_KEYS", {})) | {"--help"}


def _fill(token: str) -> str:
    """Un gabarit devient une valeur plausible : `{a|b}` → `a`, `{n}` → `1`."""
    def alt(m: re.Match[str]) -> str:
        inner = m.group(1)
        return inner.split("|", 1)[0].strip() if "|" in inner else _FILL

    token = _PLACEHOLDER_RE.sub(alt, token)
    token = _ANGLE_RE.sub(_FILL, token)
    token = re.sub(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?", _FILL, token)
    return token


def command_tokens(tail: str) -> list[str]:
    """Les jetons de la commande, tels qu'argparse les recevrait.

    La prose est un gabarit, pas un script : `$(…)` retirés, crochets
    d'optionnalité (`[--mission {n}]`) ouverts, ellipses ignorées, arrêt au
    premier opérateur shell ou au premier commentaire.
    """
    text = re.sub(r"\\\s*\n", " ", tail)
    while _SUBSHELL_RE.search(text):
        text = _SUBSHELL_RE.sub(" ", text)
    # Gabarits substitués AVANT le découpage : `{agents de la vague, séparés
    # par des virgules}` est UNE valeur, que le découpage en mots aurait
    # éclatée en cinq positionnels.
    text = _fill(text)
    try:
        lexer = shlex.shlex(text, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        raw = list(lexer)
    except ValueError:
        raw = text.split()
    tokens: list[str] = []
    for tok in raw:
        if tok in _STOP_TOKENS or tok.startswith(_STOP_PREFIXES):
            break
        closes = tok.endswith(")") and "(" not in tok
        tok = tok.rstrip(")") if closes else tok   # `$(python .sdda/sdda.py cmd)`
        tok = tok.strip("[]")
        if tok and tok not in ("...", "…"):
            tokens.append(tok)
        if closes:
            break
    return tokens


def parse_problem(parser: argparse.ArgumentParser, tokens: list[str]) -> str | None:
    """Ce qu'argparse refuserait dans cette ligne, ou `None`.

    Une option longue inconnue n'est PAS rapportée ici : la passe FLAG la
    rapporte déjà, avec son nom. Ne reste que ce qu'elle ne voit pas —
    positionnel en trop, sous-commande inconnue, argument requis absent. Un
    refus causé par la valeur de substitution d'un gabarit (`invalid choice:
    '1'`) n'est pas un défaut du prompt : on s'abstient.
    """
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            _, extra = parser.parse_known_args(tokens)
    except SystemExit:
        lines = [ln for ln in err.getvalue().strip().splitlines() if ln.strip()]
        message = lines[-1] if lines else "refus argparse"
        message = re.sub(r"^.*?error: ", "", message)
        if f"'{_FILL}'" in message or "invalid int value" in message:
            return None
        return message
    positionals = [t for t in extra if not t.startswith("-")]
    if positionals and not any(t.startswith("--") for t in extra):
        return f"argument(s) positionnel(s) non reconnu(s) : {' '.join(positionals)}"
    if positionals:
        # Des positionnels mêlés à des options inconnues : ce sont les valeurs
        # de ces options (`--totally-made-up value`), déjà rapportées par FLAG.
        return None
    return None


def scan(root: Path = SDDA) -> list[dict[str, object]]:
    """Une entrée par option citée inexistante, ou par ligne qu'argparse refuserait."""
    findings: list[dict[str, object]] = []
    known = sdda_cli.discover()
    parsers: dict[str, argparse.ArgumentParser | None] = {}

    for pattern in SCAN:
        for path in sorted(root.glob(pattern)):
            text = path.read_text(encoding="utf-8")
            for match in INVOCATION_RE.finditer(text):
                command, tail = match.group(1), match.group(2)
                if command not in known:
                    continue  # script pas encore écrit : c'est `planned_scripts` qui le dit
                module_path = sdda_cli.resolve(command)
                if not module_path:
                    continue
                real = _parser_flags(module_path)
                if real is None:
                    continue
                line = text[: match.start()].count("\n") + 1
                where = f".sdda/{path.relative_to(root).as_posix()}"
                bad_flags = [flag for flag in FLAG_RE.findall(QUOTED_RE.sub(" ", tail))
                             if flag not in IGNORED and flag not in real]
                for flag in bad_flags:
                    findings.append({"file": where, "line": line, "command": command,
                                     "flag": flag, "script": module_path, "kind": "flag"})
                if bad_flags or module_path.startswith("sdda_hooks/"):
                    continue
                if module_path not in parsers:
                    parsers[module_path] = _load_parser(module_path)
                parser = parsers[module_path]
                if parser is None:
                    continue
                problem = parse_problem(parser, command_tokens(tail))
                if problem:
                    findings.append({"file": where, "line": line, "command": command,
                                     "flag": problem, "script": module_path, "kind": "parse"})
    return findings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Lignes de commande citées par les prompts, confrontées aux scripts appelés")
    p.add_argument("--json", action="store_true", help="sortie machine")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    findings = scan()

    if args.json:
        print(json.dumps({"ok": not findings, "findings": findings}, ensure_ascii=False, indent=2))
        return 1 if findings else 0

    if not findings:
        print("  Toutes les lignes de commande citées se parsent dans les scripts appelés.")
        return 0

    print(f"  {len(findings)} ligne(s) de commande citée(s) refusée(s) :\n")
    for f in findings:
        what = f"option `{f['flag']}` absente" if f.get("kind") == "flag" else f"refus argparse : {f['flag']}"
        print(f"  {f['file']}:{f['line']}  `{f['command']}` — {what} ({f['script']})")
    print("\n  Une ligne refusée rend un `usage:` argparse, pas un bloc ERROR/CAUSE/FIX :")
    print("  l'agent qui orchestre conclut que le contrôle n'a rien dit, et poursuit.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
