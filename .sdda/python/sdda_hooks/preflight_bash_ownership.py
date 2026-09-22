#!/usr/bin/env python3
"""Le trou `Bash` de la matrice d'ownership — fermé sur les canaux ordinaires.

`preflight_ownership` s'exécute sur `Write` et `Edit`. Un agent qui écrit
`echo … > workspace/proof/datasets/golden/x.jsonl` ou `rm workspace/src/prompts/a.system.md`
ne passe par aucun des deux : la matrice était contournable par le shell, et
c'est par là qu'un `dev-agent` peut retoucher le jeu qui le juge sans qu'aucun
hook ne le voie.

Ce hook lit la commande et en extrait ce qu'elle ÉCRIT et ce qu'elle LIT :

    écritures : redirections `>` `>>`, `tee`, `rm` `rmdir` `mv` `cp` `mkdir`
                `touch` `truncate` `sed -i`, `dd of=`, et les verbes PowerShell
                `Set-Content` `Add-Content` `Out-File` `Remove-Item` `Move-Item`
                `Copy-Item` `New-Item`
    lectures  : `cat` `type` `head` `tail` `less` `more` `Get-Content`,
                `grep` `rg` `Select-String` (racine de recherche), redirection `<`

puis confronte chaque chemin sous `workspace/` ou `.sdda/` à `loader.yml` —
écritures via `audit_ownership.check_write`, lectures via `check_read`.

**Ce que le hook ne prétend pas.** Un `python -c "open(p,'w')"` ou un script
qui écrit de l'intérieur lui échappent : l'analyse est lexicale, pas
sémantique. Il ferme les canaux *ordinaires*, ceux qu'un agent emploie quand il
veut « juste corriger vite » ; le reste est rattrapé après coup par
`audit_ownership.py --agent … --wrote …` en post-step de commande. Un hook qui
essaierait d'interpréter le shell se tromperait, et un hook qui se trompe en
refusant se fait désactiver — avec tous les invariants qu'il portait.

Le fil principal (aucun `subagent_type`) passe : la matrice ne régit que les
agents.
"""
from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, agent_of, deny, run, unknown_subagent  # noqa: E402

HOOK = "preflight_bash_ownership"

#: Câblage — lu par `harness_build.py`. `applies_to` vide : la matrice vaut
#: pour chaque commande shell, quel qu'en soit l'auteur agent.
WIRING = {"event": "PreToolUse", "matcher": "Bash", "applies_to": ()}

#: Verbes dont TOUS les arguments-chemins sont des écritures (ou destructions).
WRITE_ALL = {
    "rm", "rmdir", "mkdir", "touch", "truncate", "unlink",
    "remove-item", "new-item", "set-content", "add-content", "out-file", "clear-content",
}
#: Verbes dont le DERNIER argument-chemin est l'écriture (la destination) ; les
#: autres sont des lectures — et pour `mv`, la source disparaît : écriture aussi.
WRITE_LAST = {"cp", "copy", "move-item", "copy-item"}
WRITE_ALL_MOVE = {"mv", "move", "rename-item"}
#: `tee` écrit dans chaque fichier nommé.
WRITE_TEE = {"tee"}
#: `sed -i` réécrit ses fichiers en place ; sans `-i`, il lit.
SED = {"sed"}
#: Verbes de lecture : chaque chemin rend du contenu.
READ_FILE = {"cat", "type", "head", "tail", "less", "more", "get-content", "gc", "strings", "wc", "diff"}
#: Recherches : la racine rend le contenu de tout ce qu'elle contient.
READ_TREE = {"grep", "rg", "egrep", "fgrep", "ag", "ack", "select-string", "sls", "findstr"}
#: Recherches par NOM seulement.
LIST_TREE = {"ls", "find", "dir", "get-childitem", "gci", "tree"}

#: Préfixes de commande à ignorer avant le verbe : environnement, sudo, time.
SKIP_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
SKIP_WORDS = {"sudo", "time", "nice", "env", "command", "builtin", "exec", "nohup"}

#: Séparateurs de sous-commandes. `|` sépare aussi : `cat a | tee b` sont deux
#: verbes, et chacun doit être jugé.
SPLIT_RE = re.compile(r"\s*(?:\|\||&&|;|\||\n)\s*")

#: Seules les zones que la matrice régit sont jugées. Un `pip install` ou un
#: `git status` ne nomment aucun chemin de ces zones et passent sans frais.
GOVERNED_PREFIXES = ("workspace/", ".sdda/")


def _split_commands(command: str) -> list[str]:
    return [c for c in SPLIT_RE.split(command) if c.strip()]


#: Un chemin Windows dans la commande : `C:\Users\…` ou `\\serveur\…`.
_WINDOWS_PATH_RE = re.compile(r"(?:^|[\s=\"'>])(?:[A-Za-z]:\\|\\\\)")


def _tokens(fragment: str) -> list[str]:
    """Découpe shell d'un fragment de commande.

    `shlex` en mode POSIX traite `\\` comme un caractère d'échappement :
    `echo x > C:\\Users\\me\\workspace\\proof\\datasets\\g.jsonl` sortait le jeton
    `C:Usersmeworkspaceproofdatasetsg.jsonl` — plus un chemin, donc plus rien de
    régi, donc ALLOW. Tout chemin absolu tapé à la façon de Windows échappait au
    hook, alors que c'est la forme que le harnais lui-même emploie. On bascule
    les séparateurs en `/` avant la découpe quand la commande en contient : la
    normalisation en aval ne fait pas la différence, et le sens de la commande
    est intact pour ce qui nous regarde, c'est-à-dire QUELS chemins elle nomme.
    """
    if _WINDOWS_PATH_RE.search(fragment):
        fragment = fragment.replace("\\", "/")
    try:
        return shlex.split(fragment, posix=True)
    except ValueError:
        return fragment.split()


def _normalize(root: Path, token: str) -> str | None:
    """Un jeton -> chemin relatif sous une zone régie, ou None s'il n'en est pas.

    Les chemins sont pris tels que l'agent les a écrits ; un chemin absolu est
    ramené à la racine du projet quand il s'y trouve. Un jeton qui ne contient
    ni `/` ni `\\` n'est pas un chemin — sauf s'il nomme une zone régie à la
    racine (`workspace`), ce qui n'arrive qu'aux commandes qui la détruisent.
    """
    tok = token.strip().strip("'\"")
    if not tok or tok.startswith("-"):
        return None
    tok = tok.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", tok) or tok.startswith("/"):
        try:
            tok = Path(tok).resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            return None
    while tok.startswith("./"):
        tok = tok[2:]
    if tok in ("workspace", ".sdda"):
        return tok
    if tok.startswith(GOVERNED_PREFIXES):
        return tok
    return None


def _paths(root: Path, tokens: list[str]) -> list[str]:
    out: list[str] = []
    for tok in tokens:
        p = _normalize(root, tok)
        if p is not None:
            out.append(p)
    return out


def classify(root: Path, command: str) -> tuple[list[str], list[tuple[str, str]]]:
    """(écritures, lectures) nommées par la commande — chemins sous zones régies.

    Une lecture est `(chemin, portée)` : `file` pour un fichier, `content` pour
    une recherche dans un arbre, `names` pour un simple listage.
    """
    writes: list[str] = []
    reads: list[tuple[str, str]] = []

    for fragment in _split_commands(command):
        tokens = _tokens(fragment)

        # Redirections : `> f`, `>> f`, `>f`, `2> f`, `< f`.
        rest: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            m = re.match(r"^(\d?)(>>?|<)(.*)$", tok)
            if m and not tok.startswith("-"):
                op, inline = m.group(2), m.group(3)
                target = inline or (tokens[i + 1] if i + 1 < len(tokens) else "")
                if not inline:
                    i += 1
                p = _normalize(root, target)
                if p is not None:
                    if op == "<":
                        reads.append((p, "file"))
                    else:
                        writes.append(p)
                i += 1
                continue
            rest.append(tok)
            i += 1
        tokens = rest

        # Le verbe : après les assignations d'environnement et les préfixes.
        while tokens and (SKIP_PREFIX_RE.match(tokens[0]) or tokens[0].lower() in SKIP_WORDS):
            tokens = tokens[1:]
        if not tokens:
            continue
        verb = Path(tokens[0].replace("\\", "/")).name.lower()
        for ext in (".exe", ".cmd", ".bat"):
            if verb.endswith(ext):
                verb = verb[: -len(ext)]
        args = tokens[1:]
        named = _paths(root, args)

        if verb in WRITE_ALL or verb in WRITE_ALL_MOVE or verb in WRITE_TEE:
            writes.extend(named)
        elif verb in WRITE_LAST:
            if named:
                writes.append(named[-1])
                reads.extend((p, "file") for p in named[:-1])
        elif verb in SED:
            in_place = any(a == "-i" or a.startswith("-i") or a == "--in-place" for a in args)
            (writes.extend(named) if in_place else reads.extend((p, "file") for p in named))
        elif verb in READ_FILE:
            reads.extend((p, "file") for p in named)
        elif verb in READ_TREE:
            # Pas de racine nommée : la recherche part du répertoire courant,
            # c'est-à-dire de tout le projet.
            reads.extend((p, "content") for p in (named or ["."]))
        elif verb in LIST_TREE:
            reads.extend((p, "names") for p in named)
        elif verb == "dd":
            for a in args:
                if a.startswith("of="):
                    p = _normalize(root, a[3:])
                    if p is not None:
                        writes.append(p)

    return writes, reads


def check(root: Path, data: dict) -> int:
    agent = agent_of(data)
    if not agent:
        return ALLOW

    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    command = str(tool_input.get("command") or data.get("command") or "")
    if not command.strip():
        return ALLOW

    writes, reads = classify(root, command)
    if not writes and not reads:
        return ALLOW

    from sdda_lib.errors import Report  # noqa: E402  (import tardif : coût de démarrage du hook)
    from sdda_scripts import audit_ownership as ao  # noqa: E402

    loader = ao.load_loader(root)
    if not isinstance(loader.get(agent), dict):
        # Sous-agent hors matrice : rien sous workspace/, ni en écriture ni en
        # lecture. Voir `_hook.unknown_subagent` pour le pourquoi.
        for path in [*writes, *(p for p, _scope in reads)]:
            verdict = unknown_subagent(HOOK, agent, str(path))
            if verdict != ALLOW:
                return verdict
        return ALLOW

    report = Report(name="BASH-HOOK", target=str(root))
    for path in writes:
        if not ao.check_write(loader, agent, path, report):
            break
    else:
        if ao.forbidden_reads_of(loader, agent):
            for path, scope in reads:
                if not ao.check_read(loader, agent, path, report, scope=scope):
                    break

    if report.ok:
        return ALLOW
    first = report.errors[0]
    return deny(HOOK, first.cls, f"via Bash — {first.message}",
                first.fix or "passer par Write/Edit dans ta zone, ou élargir la matrice explicitement")


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
