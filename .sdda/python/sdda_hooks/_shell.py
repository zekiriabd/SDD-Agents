"""Classifieur de commandes shell — ce qu'une commande ÉCRIT, LIT, et ce qu'elle CACHE.

Partagé par le hook `preflight_bash_ownership` (outils `Bash` et `PowerShell`).
Le hook d'avant découpait la commande sur `;`/`&&`/`|` et lisait des jetons :
une analyse lexicale honnête, mais testée contournable par une douzaine de
formes ordinaires — `cd <zone> && echo x > f`, `python -c "open(…,'w')"`,
`printf x > "$PWD/…"`, `git checkout -- <prompt>`, `install`, `Workspace/…`
sous Windows, `work*/…`, `$X/…`, `$(…)`, `bash -c "…"`, `./src/../…`,
`/g/…` de Git Bash, `base64 -d | sh`.

Ce module ne devient pas un interpréteur de shell — un hook qui se trompe en
refusant se fait désactiver, avec tous les invariants qu'il portait. Il fait
trois choses de plus, et les dit :

1. **Il RÉSOUT ce qui se résout sans exécuter** : le répertoire courant suivi
   à travers `cd`/`pushd`/`Set-Location`/`git -C`, les variables affectées
   dans la commande et `$PWD`, `..`, `/g/…`, la casse du système de fichiers,
   les jokers développés sur le disque, `bash -c`/`sh -c`/`eval`/`pwsh
   -Command`/`-EncodedCommand` analysés RÉCURSIVEMENT, `$(…)` et les
   backticks analysés comme des commandes à part entière.
2. **Il DÉCLARE OPAQUE ce qui ne se résout pas** : une cible d'écriture qui
   dépend d'une variable inconnue ou d'une substitution, du code passé à un
   interpréteur par l'entrée standard (`… | sh`, `… | python`, `| iex`), du
   code en ligne obscurci (`exec`, `base64`, `chr(`…) qui écrit. L'appelant
   refuse l'opaque quand la commande touche une zone régie, ou quand l'agent
   est un sous-agent régi — jamais pour le fil principal.
3. **Il réduit les faux positifs** : le motif de `grep -v workspace` n'est pas
   un chemin, pas plus que le script de `sed`/`awk` ou le filtre de `jq` ; une
   chaîne Python qui NOMME un chemin sans rien écrire est une lecture.

Ce qui reste hors de portée, et qu'il faut dire : un script sur disque
(`python tools/gen.py`) qui écrit de l'intérieur, et du code en ligne qui
fabrique un chemin régi par calcul sans aucun indicateur reconnu. Les deux sont
rattrapés APRÈS la phase par `audit-ownership --since-snapshot`, qui compare le
disque à l'instantané pris avant elle.
"""
from __future__ import annotations

import base64
import glob as _glob
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

MARK = "\x00"                      # une valeur que l'analyse ne connaît pas
#: `.claude`, `.codex`, `.gemini`, `.agents` portent les hooks et les façades
#: que les harnais exécutent ; `.git` porte ses propres hooks. Un sous-agent
#: qui les réécrit au shell (`echo {} > .claude/settings.json`) neutralise la
#: matrice à la session suivante — `Write` les refusait déjà, pas le shell.
GOVERNED_ROOTS = ("workspace", ".sdda", ".claude", ".codex", ".gemini", ".agents", ".git")
MAX_DEPTH = 4                      # récursion bash -c / eval / $(…)


@dataclass
class Analysis:
    writes: list[str] = field(default_factory=list)
    reads: list[tuple[str, str]] = field(default_factory=list)
    #: Écritures récursives (`rm -r`, `rmdir`, `git clean`, `find -delete`) :
    #: le répertoire ET tout ce qu'il contient.
    recursive_writes: list[str] = field(default_factory=list)
    #: Recherches récursives qui lisent les fichiers cachés (GNU `grep -r`,
    #: `rg --hidden`) — un `.env` dessous serait rendu.
    hidden_content_reads: list[str] = field(default_factory=list)
    #: Répertoires CRÉÉS (`mkdir -p`, `New-Item -ItemType Directory`) : jugés
    #: sur ce qu'ils contiendront — `agents/billing` est à l'instance qui écrira
    #: `agents/billing/x`, même si le répertoire lui-même ne matche aucun motif.
    mkdirs: list[str] = field(default_factory=list)
    #: Raisons d'opacité : une écriture dont la cible ne se résout pas.
    opaque: list[str] = field(default_factory=list)
    #: Jetons de lecture non résolus (variable, substitution).
    unresolved_reads: list[str] = field(default_factory=list)
    mentions_governed: bool = False
    cwd_governed: bool = False

    def merge(self, other: "Analysis") -> None:
        self.writes += other.writes
        self.reads += other.reads
        self.recursive_writes += other.recursive_writes
        self.hidden_content_reads += other.hidden_content_reads
        self.mkdirs += other.mkdirs
        self.opaque += other.opaque
        self.unresolved_reads += other.unresolved_reads
        self.mentions_governed |= other.mentions_governed
        self.cwd_governed |= other.cwd_governed


# ---------------------------------------------------------------------------
# Tables de verbes
# ---------------------------------------------------------------------------
#: Tous les arguments-chemins sont écrits (créés, détruits, modifiés).
WRITE_ALL = {
    "rm", "rmdir", "mkdir", "touch", "truncate", "unlink", "shred", "chmod", "chown", "chgrp",
    "chattr", "setfacl", "mkfifo", "mknod", "del", "erase", "rd",
}
#: Le dernier argument est la destination (écrite), les autres sont lus.
WRITE_LAST = {"cp", "copy", "install", "ln", "link", "rsync", "scp", "xcopy", "robocopy"}
#: Source ET destination changent.
WRITE_MOVE = {"mv", "move", "ren", "rename"}
TEE = {"tee"}
#: Lecture de contenu, fichier par fichier.
READ_FILE = {
    "cat", "tac", "nl", "type", "head", "tail", "less", "more", "strings", "wc", "diff", "cmp",
    "base64", "base32", "xxd", "od", "hexdump", "hd", "md5sum", "sha1sum", "sha256sum",
    "sha512sum", "cksum", "file", "stat", "cut", "paste", "column", "fold", "fmt", "rev",
    "iconv", "tr", "comm", "join", "expand", "unexpand", "pr", "look",
}
#: Premier argument positionnel = un PROGRAMME, pas un chemin.
READ_WITH_PROGRAM = {"sed", "awk", "gawk", "mawk", "nawk", "jq", "yq"}
#: Recherches : la racine rend le contenu de ce qu'elle contient ; premier
#: positionnel = le MOTIF (sauf `-e`/`-f`/`--regexp`).
READ_TREE = {"grep", "egrep", "fgrep", "rg", "ag", "ack", "findstr"}
LIST_TREE = {"ls", "dir", "tree", "du", "fd", "fdfind", "find"}
#: Interpréteurs de shell : `-c CODE` est une commande, analysée récursivement.
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "ash", "busybox"}
POWERSHELLS = {"pwsh", "powershell"}
#: Interpréteurs de code : `-c`/`-e` portent du code en ligne.
CODE_INLINE_FLAGS = {
    "python": {"-c"}, "node": {"-e", "-p", "--eval", "--print"}, "deno": {"eval"},
    "bun": {"-e", "--eval"}, "perl": {"-e", "-E"}, "ruby": {"-e"}, "php": {"-r"},
}
#: `python -m` de modules qui écrivent les fichiers qu'on leur nomme.
WRITING_MODULES = {"json", "zipfile", "tarfile", "gzip", "shutil", "py_compile", "compileall", "venv",
                   "pip", "ensurepip", "http", "urllib"}
#: Drapeaux Python sans valeur qui peuvent précéder `c` dans un groupe (`-Ic`, `-IScode`).
_PY_NOARG_FLAGS = "bBdEhiIOPqRsSuvx"


def _inline_code(verb: str, args: list[str]) -> tuple[str | None, bool]:
    """(code en ligne, édition sur place ?) d'un interpréteur.

    La correspondance EXACTE des drapeaux laissait passer les formes usuelles :
    `python -Ic "…"` (drapeaux groupés), `node --eval="…"`, `perl -pe`/`-pi -e`
    (le `e` groupé à d'autres lettres), `ruby -ne`. Le code passait sans être
    lu, et `perl -pi` réécrivait les fichiers nommés sans qu'aucune écriture ne
    soit jugée.
    """
    flags = CODE_INLINE_FLAGS[verb]
    in_place = False
    code: str | None = None
    for i, a in enumerate(args):
        name, eq, val = a.partition("=")
        if verb in ("perl", "ruby") and re.match(r"^-[A-Za-z0-9.]*i", a) and not a.startswith("--"):
            in_place = True
        if code is not None:
            continue
        if eq and name in flags:
            code = val
        elif a in flags and i + 1 < len(args):
            code = args[i + 1]
        elif verb == "python":
            m = re.match(rf"^-[{_PY_NOARG_FLAGS}]*c(.*)$", a, re.S)
            if m and not a.startswith("--"):
                code = m.group(1) if m.group(1) else (args[i + 1] if i + 1 < len(args) else "")
        elif verb in ("perl", "ruby") and re.match(r"^-[A-Za-z0-9.]*[eE]$", a) and i + 1 < len(args):
            code = args[i + 1]
    return code, in_place


SKIP_WORDS = {"sudo", "time", "nice", "command", "builtin", "exec", "nohup", "stdbuf", "ionice",
              "doas", "chronic", "unbuffer", "caffeinate"}
#: Options qui prennent une valeur, par verbe (pour ne pas lire la valeur comme un chemin).
VALUE_OPTS: dict[str, set[str]] = {
    "grep": {"-e", "-f", "-m", "-A", "-B", "-C", "--include", "--exclude", "--exclude-dir",
             "--regexp", "--file", "--max-count", "--context", "--color", "-d", "-D", "--label"},
    "rg": {"-e", "-f", "-g", "-t", "-T", "-m", "-A", "-B", "-C", "-j", "-M", "--glob", "--iglob",
           "--type", "--type-not", "--regexp", "--file", "--max-count", "--context", "--color",
           "--max-depth", "--max-columns", "--sort", "--sortr", "-r", "--replace", "--encoding", "-E"},
    "find": set(),
    "head": {"-n", "-c"}, "tail": {"-n", "-c"}, "cut": {"-d", "-f", "-c", "-b"},
    "sort": {"-t", "-k", "-S", "-T", "--field-separator", "--key"},
    "sed": {"-e", "-f", "--expression", "--file", "-l"},
    "awk": {"-F", "-v", "-f"}, "gawk": {"-F", "-v", "-f", "-i"},
    "jq": {"--arg", "--argjson", "--slurpfile", "--rawfile", "-L", "--indent"},
    "tar": {"-f", "-C", "--file", "--directory", "-T", "-X", "--exclude"},
    "curl": {"-o", "--output", "-H", "-d", "--data", "-X", "-u", "-A", "-e", "-F", "--form",
             "--data-raw", "--data-binary", "-T", "--upload-file", "-K", "--config", "-w"},
    "wget": {"-O", "--output-document", "-P", "--directory-prefix", "-o", "-a", "--header", "-U"},
    "install": {"-m", "-o", "-g", "-t", "--mode", "--owner", "--group", "--target-directory"},
    "cp": {"-t", "--target-directory"}, "mv": {"-t", "--target-directory"},
    "ln": {"-t", "--target-directory"},
    "chmod": set(), "chown": set(), "git": {"-C", "-c", "--git-dir", "--work-tree"},
    "xxd": {"-l", "-s", "-c", "-g"}, "od": {"-A", "-t", "-N", "-j", "-w"},
    "base64": {"-w", "--wrap"}, "diff": {"-U", "-C", "--label"},
    "timeout": set(), "xargs": {"-I", "-n", "-P", "-d", "-L", "-s", "-E", "-a", "--arg-file"},
    "unzip": {"-d", "-x"}, "7z": set(), "patch": {"-p", "-o", "-d", "-i", "--input", "--output"},
}
#: Chmod/chown : le premier positionnel est un mode ou un propriétaire.
FIRST_IS_NOT_PATH = {"chmod", "chown", "chgrp", "chattr", "setfacl"}

#: Sous-commandes git qui écrivent l'arbre de travail (`clone`/`init`/`worktree`/
#: `submodule` : un dépôt entier déposé à l'endroit nommé).
GIT_PATH_WRITES = {"checkout", "restore", "rm", "mv", "clean", "update-index", "clone", "init", "worktree",
                   "submodule"}
#: Sous-commandes git qui LISENT le contenu des chemins nommés.
GIT_PATH_READS = {"show", "cat-file", "diff", "log", "blame", "grep"}
GIT_TREE_WRITES = {"reset", "stash", "switch", "merge", "pull", "rebase", "revert", "cherry-pick",
                   "checkout-index", "read-tree", "apply", "am"}

# --- PowerShell -------------------------------------------------------------
PS_ALIASES = {
    "sc": "set-content", "ac": "add-content", "clc": "clear-content", "gc": "get-content",
    "cat": "get-content", "type": "get-content", "ri": "remove-item", "rm": "remove-item",
    "del": "remove-item", "erase": "remove-item", "rd": "remove-item", "rmdir": "remove-item",
    "ni": "new-item", "md": "new-item", "mkdir": "new-item", "mi": "move-item", "mv": "move-item",
    "move": "move-item", "cpi": "copy-item", "cp": "copy-item", "copy": "copy-item",
    "ren": "rename-item", "rni": "rename-item", "sls": "select-string", "gci": "get-childitem",
    "ls": "get-childitem", "dir": "get-childitem", "iex": "invoke-expression", "icm": "invoke-command",
    "sl": "set-location", "cd": "set-location", "chdir": "set-location", "pushd": "push-location",
    "popd": "pop-location", "iwr": "invoke-webrequest", "irm": "invoke-restmethod",
    "curl": "invoke-webrequest", "wget": "invoke-webrequest", "tee": "tee-object",
    "si": "set-item", "gi": "get-item", "fhx": "format-hex", "saps": "start-process",
    "start": "start-process", "epcsv": "export-csv", "ipcsv": "import-csv",
}
PS_WRITE_PATH = {"set-content", "add-content", "out-file", "clear-content", "remove-item", "new-item",
                 "set-item", "export-csv", "export-clixml", "tee-object", "rename-item",
                 "set-itemproperty", "clear-item"}
PS_READ_PATH = {"get-content", "import-csv", "import-clixml", "get-filehash", "format-hex", "get-item"}
PS_LIST_PATH = {"get-childitem", "test-path", "resolve-path"}
PS_PATH_PARAMS = {"-path", "-literalpath", "-filepath", "-pspath", "-lp"}
PS_DEST_PARAMS = {"-destination", "-destinationpath", "-outfile", "-outputpath"}
PS_VALUE_PARAMS = {"-value", "-encoding", "-pattern", "-filter", "-include", "-exclude", "-itemtype",
                   "-type", "-inputobject", "-tail", "-totalcount", "-head", "-first", "-last",
                   "-delimiter", "-stream", "-newname", "-name", "-argumentlist", "-scriptblock",
                   "-uri", "-method", "-headers", "-body", "-credential", "-depth", "-context"}
PS_DOTNET_WRITE_RE = re.compile(
    r"\[(?:system\.)?io\.(?:file|directory|path)\]::(?:write|append|delete|move|copy|create|replace|"
    r"setattributes|setlastwritetime|open(?!read))|\.(?:delete|moveto|copyto|writealltext|writealllines|"
    r"writeallbytes|appendalltext|create|createtext|appendtext)\s*\(|new-object\s+(?:system\.)?io\.streamwriter|"
    r"\[(?:system\.)?io\.streamwriter\]", re.I)
#: Lectures .NET : `[IO.File]::ReadAllText('…/.env')` rendait le secret — seules
#: les écritures .NET étaient reconnues.
PS_DOTNET_READ_RE = re.compile(
    r"\[(?:system\.)?io\.file\]::(?:read|openread|opentext|open\b)|new-object\s+(?:system\.)?io\.streamreader|"
    r"\[(?:system\.)?io\.streamreader\]", re.I)
PS_OPAQUE_RE = re.compile(r"\[scriptblock\]::create|\$executioncontext|\.invoke\s*\(|add-type", re.I)

# --- Code en ligne ----------------------------------------------------------
CODE_WRITE_RE = re.compile(
    r"open\s*\([^)]*,\s*(?:mode\s*=\s*)?[fbrtu]*['\"][^'\"]*[wax+]|mode\s*=\s*['\"][^'\"]*[wax+]|"
    r",\s*['\"][rbt]*[wax][rbt+]*['\"]\s*[,)]|"
    r"\.write(?:_text|_bytes)?\s*\(|\.(?:unlink|rmdir|mkdir|touch|rename|replace|symlink_to|hardlink_to|"
    r"chmod)\s*\(|os\.(?:remove|unlink|rename|replace|makedirs|mkdir|rmdir|removedirs|system|popen|"
    r"truncate|link|symlink|chmod)|shutil\.|subprocess|popen|check_call|check_output|"
    r"writefile|appendfile|createwritestream|copyfile|rmsync|rm\s*\(|child_process|execsync|spawnsync|"
    r"file\.(?:write|open|delete)|fileutils|io\.(?:open|write|popen)|\bsystem\s*\(|`|"
    r"open\s*\(?\s*\w*\s*,?\s*['\"]\s*[>+]|\bunlink\b|\brename\b|file_put_contents|fwrite|fopen", re.I)
CODE_OBFUSCATION_RE = re.compile(
    r"\bexec\s*\(|\beval\s*\(|\bcompile\s*\(|__import__|getattr\s*\(|importlib|base64|b64decode|codecs|"
    r"fromhex|\bchr\s*\(|\\x[0-9a-f]{2}|\\u00[0-9a-f]{2}|rot.?13|\bfunction\s*\(|atob\s*\(|"
    r"buffer\.from|\bpack\s*\(|\bord\s*\(", re.I)
GOVERNED_LITERAL_RE = re.compile(
    r"""(?:[A-Za-z]:[\\/]|/)?(?:[^\s'"`;|&<>()]*[\\/])?(?:workspace|\.sdda)(?:[\\/][^\s'"`;|&<>(),]*)?""", re.I)

_WINDOWS_PATH_RE = re.compile(r"(?:^|[\s=\"'>])(?:[A-Za-z]:\\|\\\\)")
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)|\$\{?env:([A-Za-z_][A-Za-z0-9_]*)\}?", re.I)
_REDIR_RE = re.compile(r"^(\d*|&|\*)?(>>|>\||>|<<<|<<-?|<)(.*)$")
_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
_PS_ASSIGN_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", re.S)
_GLOB_CHARS = "*?["


# ---------------------------------------------------------------------------
# Lexique
# ---------------------------------------------------------------------------
@dataclass
class _Fragment:
    text: str
    piped_in: bool = False
    heredoc: str | None = None


def _matching_paren(s: str, i: int) -> int:
    """Index de la `)` qui ferme la `(` en `s[i]` — guillemets respectés."""
    depth = 0
    quote: str | None = None
    k = i
    while k < len(s):
        ch = s[k]
        if quote:
            if ch == "\\" and quote == '"':
                k += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return k
        k += 1
    return len(s) - 1


def lex(command: str, dialect: str = "bash") -> tuple[list[_Fragment], list[str]]:
    """Commande -> (fragments, substitutions). Guillemets, échappements,
    `$(…)`, backticks, substitutions de processus et heredocs respectés.

    Les séparateurs ne coupent plus À L'INTÉRIEUR d'une chaîne : `echo "a;b"`
    est un seul fragment. Un heredoc est rattaché au fragment qui l'ouvre, et
    son corps n'est jamais lu comme des commandes — sinon le texte d'un
    fichier qu'on écrit se faisait juger comme un script.
    """
    ps = dialect == "powershell"
    frags: list[_Fragment] = []
    subs: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    piped = False
    pending_heredocs: list[tuple[str, bool]] = []
    heredoc_body: list[str] = []
    esc = "`" if ps else "\\"
    i = 0
    n = len(command)

    def flush(next_piped: bool) -> None:
        nonlocal buf, piped, heredoc_body
        text = "".join(buf).strip()
        if text or heredoc_body:
            frags.append(_Fragment(text, piped, "\n".join(heredoc_body) if heredoc_body else None))
        buf, heredoc_body = [], []
        piped = next_piped

    while i < n:
        ch = command[i]
        if quote == "'":
            buf.append(ch)
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == esc and i + 1 < n:
            if command[i + 1] == "\n":
                i += 2                   # continuation de ligne
                continue
            buf.append(ch)
            buf.append(command[i + 1])
            i += 2
            continue
        if ch == '"':
            quote = None if quote == '"' else '"'
            buf.append(ch)
            i += 1
            continue
        if ch == "'" and quote is None:
            quote = "'"
            buf.append(ch)
            i += 1
            continue
        # Substitutions : `$(…)`, backticks (bash), `<(…)` / `>(…)`.
        if ch == "$" and i + 1 < n and command[i + 1] == "(":
            end = _matching_paren(command, i + 1)
            subs.append(command[i + 2:end])
            buf.append(MARK)
            i = end + 1
            continue
        if not ps and ch == "`":
            end = command.find("`", i + 1)
            end = n if end == -1 else end
            subs.append(command[i + 1:end])
            buf.append(MARK)
            i = end + 1
            continue
        if quote is None and not ps and ch in "<>" and i + 1 < n and command[i + 1] == "(":
            end = _matching_paren(command, i + 1)
            subs.append(command[i + 2:end])
            buf.append(MARK)
            i = end + 1
            continue
        if quote is None:
            if ch == "#" and (not buf or buf[-1] in " \t\n;|&("):
                while i < n and command[i] != "\n":
                    i += 1
                continue
            if not ps and command.startswith("<<", i) and not command.startswith("<<<", i):
                m = re.match(r"<<(-?)\s*(['\"]?)([A-Za-z0-9_.-]+)\2", command[i:])
                if m:
                    pending_heredocs.append((m.group(3), bool(m.group(1))))
                    buf.append(" ")
                    i += m.end()
                    continue
            if ch in "<>":
                # Une redirection COLLÉE (`echo x>f`, `'x'>f`, `2>f`, `&>f`) :
                # `shlex` ne coupe pas sur `>`, le jeton `x>f` ne commençait
                # pas par l'opérateur et l'écriture passait — golden écrit,
                # rapport de gate fabriqué. L'opérateur devient un jeton à part,
                # son descripteur (`2`, `&`) avec lui s'il ouvre un mot.
                j = i + 1
                op = ch
                while j < n and command[j] in "<>" and len(op) < 3:
                    op += command[j]
                    j += 1
                if j < n and command[j] in "|&":
                    op += command[j]
                    j += 1
                    if op.endswith("&"):
                        while j < n and (command[j].isdigit() or command[j] == "-"):
                            op += command[j]
                            j += 1
                k = len(buf)
                while k > 0 and buf[k - 1].isdigit():
                    k -= 1
                if k > 0 and buf[k - 1] == "&" and op.startswith(">"):
                    k -= 1
                prefix = buf[k:] if (k == 0 or buf[k - 1] in " \t") else []
                if prefix:
                    del buf[k:]
                buf.extend([" ", *prefix, *op, " "])
                i = j
                continue
            if ch == "\n":
                flush(False)
                i += 1
                # Corps des heredocs ouverts sur la ligne qui vient de finir.
                while pending_heredocs:
                    delim, strip = pending_heredocs.pop(0)
                    body: list[str] = []
                    while i < n:
                        end = command.find("\n", i)
                        end = n if end == -1 else end
                        line = command[i:end]
                        i = end + 1
                        if (line.lstrip("\t") if strip else line) == delim:
                            break
                        body.append(line)
                    if frags:
                        frags[-1].heredoc = "\n".join(body)
                continue
            two = command[i:i + 2]
            if two in ("&&", "||"):
                flush(False)
                i += 2
                continue
            if two == "|&" and not ps:
                flush(True)
                i += 2
                continue
            if ch == "|":
                flush(True)
                i += 1
                continue
            if ch == ";":
                flush(False)
                i += 1
                continue
            if ch == "&" and not ps:
                prev = buf[-1] if buf else ""
                nxt = command[i + 1] if i + 1 < n else ""
                if prev in "<>" or nxt == ">":
                    buf.append(ch)
                    i += 1
                    continue
                flush(False)
                i += 1
                continue
            if ps and ch in "{}()":
                # Blocs de script et regroupements : chacun est une commande.
                if ch == "(" and buf and buf[-1] == "$":
                    buf[-1] = MARK
                flush(False)
                i += 1
                continue
        buf.append(ch)
        i += 1
    flush(False)
    return frags, subs


def _tokens(fragment: str, dialect: str) -> list[str]:
    if dialect == "powershell":
        return _ps_tokens(fragment)
    if _WINDOWS_PATH_RE.search(fragment):
        # `shlex` POSIX lit `\` comme un échappement : `C:\Users\…` devenait
        # `C:Users…`, plus un chemin, donc plus rien de régi.
        fragment = fragment.replace("\\", "/")
    try:
        return shlex.split(fragment, posix=True)
    except ValueError:
        return fragment.split()


def _ps_tokens(fragment: str) -> list[str]:
    """Découpe PowerShell : `'…'` littéral (`''`), `"…"` avec échappements `` ` ``,
    et `\\` n'échappe rien."""
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    has = False
    while i < len(fragment):
        ch = fragment[i]
        if quote == "'":
            if ch == "'":
                if i + 1 < len(fragment) and fragment[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                quote = None
            else:
                buf.append(ch)
            i += 1
            continue
        if ch == "`" and i + 1 < len(fragment):
            buf.append(fragment[i + 1])
            has = True
            i += 2
            continue
        if quote == '"':
            if ch == '"':
                quote = None
            else:
                buf.append(ch)
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            has = True
            i += 1
            continue
        if ch.isspace():
            if buf or has:
                out.append("".join(buf))
            buf, has = [], False
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf or has:
        out.append("".join(buf))
    return out


# ---------------------------------------------------------------------------
# L'analyse
# ---------------------------------------------------------------------------
def _fold(s: str) -> str:
    from sdda_scripts.audit_ownership import CASE_INSENSITIVE
    return s.casefold() if CASE_INSENSITIVE else s


def is_governed(rel: str) -> bool:
    if not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:/", rel):
        return False
    head = _fold(rel.split("/", 1)[0])
    return head in GOVERNED_ROOTS


def mentions_governed(text: str) -> bool:
    folded = text.replace("\\", "/").casefold()
    return any(re.search(r"(?:^|[^a-z0-9_])" + re.escape(g) + r"(?:$|[^a-z0-9_])", folded)
               for g in GOVERNED_ROOTS)


class _Ctx:
    def __init__(self, root: Path, cwd: str | None, env: dict[str, str], depth: int, dialect: str) -> None:
        self.root = root
        self.cwd = cwd
        self.env = env
        self.depth = depth
        self.dialect = dialect


def analyze(root: Path, command: str, cwd: str | Path | None = None, dialect: str = "bash",
            _env: dict[str, str] | None = None, _depth: int = 0) -> Analysis:
    """Ce que la commande écrit, lit, et ce qu'elle cache — chemins relatifs à `root`."""
    from sdda_scripts import audit_ownership as ao

    start = ao._native(Path(cwd).as_posix()) if cwd else ao._native(Path(root).as_posix())
    ctx = _Ctx(Path(root), start, dict(_env or {}), _depth, dialect)
    result = Analysis()
    result.mentions_governed = mentions_governed(command)
    if ctx.cwd and is_governed(ao.relative_to_root(root, ctx.cwd)):
        result.cwd_governed = True
    if dialect == "powershell":
        # Au niveau de la COMMANDE, pas du fragment : le lexique PowerShell
        # coupe sur les parenthèses, si bien que `[IO.File]::WriteAllText` et
        # son argument `('workspace/…')` finissent dans deux fragments.
        if PS_DOTNET_WRITE_RE.search(command):
            lits = [x.group(0) for x in GOVERNED_LITERAL_RE.finditer(command)]
            for lit in lits:
                _add_write(ctx, result, lit.rstrip(".,"), why="appel .NET d'écriture")
            if not lits and _cwd_governed(ctx):
                result.opaque.append("appel .NET d'écriture depuis un répertoire courant régi")
        if PS_DOTNET_READ_RE.search(command):
            for lit in (x.group(0) for x in GOVERNED_LITERAL_RE.finditer(command)):
                _add_read(ctx, result, lit.rstrip(".,"), "file")
        if PS_OPAQUE_RE.search(command) and (result.mentions_governed or _cwd_governed(ctx)):
            result.opaque.append("PowerShell dynamique ([scriptblock]::Create, .Invoke, Add-Type)")
    frags, subs = lex(command, dialect)
    for frag in frags:
        _fragment(ctx, frag, result)
    if _depth < MAX_DEPTH:
        for sub in subs:
            result.merge(analyze(root, sub, ctx.cwd, dialect, ctx.env, _depth + 1))
    return result


def _expand(ctx: _Ctx, token: str) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1) or m.group(2) or m.group(3) or ""
        if name.upper() == "PWD" and ctx.cwd:
            return ctx.cwd
        if name in ctx.env:
            return ctx.env[name]
        return MARK
    out = _VAR_RE.sub(repl, token)
    if out.startswith("~"):
        out = os.path.expanduser(out)
    return out


def _resolve(ctx: _Ctx, token: str) -> tuple[list[str], bool]:
    """Jeton -> (chemins relatifs à la racine, résolu ?).

    Un jeton non résolu (variable inconnue, substitution, répertoire courant
    inconnu) rend `([], False)` : l'appelant décide si c'est opaque.
    """
    from sdda_scripts import audit_ownership as ao

    tok = _expand(ctx, token)
    if MARK in tok:
        return [], False
    if not ao._is_absolute(ao._native(tok)) and ctx.cwd is None:
        return [], False
    if any(c in tok for c in _GLOB_CHARS):
        base = ctx.cwd or Path(ctx.root).as_posix()
        pattern = tok if ao._is_absolute(ao._native(tok)) else base.rstrip("/") + "/" + tok
        try:
            found = _glob.glob(ao._native(pattern), recursive=True)
        except (OSError, re.error):
            found = []
        if found:
            return [ao.relative_to_root(ctx.root, f) for f in found], True
        # Aucun fichier : le shell passe le motif tel quel. Si le motif PEUT
        # désigner une zone régie (`work*/…`), on ne sait pas ce qu'il écrira.
        rel = ao.relative_to_root(ctx.root, tok, ctx.cwd)
        if not is_governed(rel) and _glob_could_be_governed(ctx, tok):
            return [], False
        return [rel], True
    return [ao.relative_to_root(ctx.root, tok, ctx.cwd)], True


def _glob_could_be_governed(ctx: _Ctx, token: str) -> bool:
    """`work*/…`, `w?rkspace/…`, `[w]orkspace/…` : un joker qui PEUT désigner une zone régie."""
    import fnmatch

    from sdda_scripts import audit_ownership as ao

    if not any(c in token for c in _GLOB_CHARS):
        return False
    rel = ao.relative_to_root(ctx.root, token, ctx.cwd)
    head = rel.split("/", 1)[0]
    return any(fnmatch.fnmatch(g, _fold(head)) for g in GOVERNED_ROOTS)


def _might_be_governed(ctx: _Ctx, token: str) -> bool:
    """Un jeton non résolu pourrait-il désigner une zone régie ?"""
    from sdda_scripts import audit_ownership as ao

    tok = _expand(ctx, token)
    literal = tok.replace(MARK, "")
    if mentions_governed(literal):
        return True
    if tok.startswith(MARK):
        return True                    # la racine même est inconnue
    if _glob_could_be_governed(ctx, literal):
        return True
    native = ao._native(literal)
    if not ao._is_absolute(native):
        return ctx.cwd is None or is_governed(ao.relative_to_root(ctx.root, ctx.cwd or "."))
    rel = ao.relative_to_root(ctx.root, native)
    return is_governed(rel)


def _add_write(ctx: _Ctx, res: Analysis, token: str, *, recursive: bool = False, why: str = "",
               directory: bool = False) -> None:
    paths, ok = _resolve(ctx, token)
    if not ok:
        if _might_be_governed(ctx, token):
            res.opaque.append(f"{why or 'écriture'} vers `{token.replace(MARK, '$?')}` — cible non résolue")
        return
    for p in paths:
        if recursive and _contains_root(ctx, p):
            p = "."
        # `.` (la racine du projet) récursif : `git reset --hard`, `rm -rf .` —
        # tout le projet, zones régies comprises.
        if is_governed(p) or (recursive and p == "."):
            res.writes.append(p)
            if recursive:
                res.recursive_writes.append(p)
            if directory:
                res.mkdirs.append(p)


def _add_read(ctx: _Ctx, res: Analysis, token: str, scope: str) -> None:
    paths, ok = _resolve(ctx, token)
    if not ok:
        literal = _expand(ctx, token).replace(MARK, "")
        if ".env" in literal.casefold() or _might_be_governed(ctx, token):
            res.unresolved_reads.append(token.replace(MARK, "$?"))
        return
    for p in paths:
        if scope != "file" and _contains_root(ctx, p):
            p = "."
        if is_governed(p) or p in (".",) or p.rsplit("/", 1)[-1].casefold().startswith(".env"):
            res.reads.append((p, scope))


def _bulk_read(ctx: _Ctx, res: Analysis, token: str) -> None:
    """Une lecture EN VRAC d'un répertoire — fichiers cachés compris.

    `find … -exec cat {} +`, `tar -c`, `cp -r`, `Copy-Item -Recurse` lisent
    tout ce qui est dessous, sans les exclusions de ripgrep : un `.env` y est
    rendu comme le reste. Jugée comme `grep -r` (`hidden_content_reads`).
    """
    _add_read(ctx, res, token, "content")
    paths, ok = _resolve(ctx, token)
    if ok:
        res.hidden_content_reads.extend("." if _contains_root(ctx, p) else p for p in paths)


def _contains_root(ctx: _Ctx, rel: str) -> bool:
    """Un chemin ABSOLU hors projet qui CONTIENT la racine (`..`, `G:/Dev`).

    Rendu tel quel par `relative_to_root`, il semblait « hors projet » : `grep
    -r LLM_ ..` fouillait `workspace/assets/.env`, et `forbidden_reads` ne voyait
    rien. Le lire comme la racine elle-même rend la lecture jugeable.
    """
    from sdda_scripts import audit_ownership as ao

    if not ao._is_absolute(rel):
        return False
    import posixpath
    r = posixpath.normpath(ao._native(Path(ctx.root).as_posix()))
    t = posixpath.normpath(ao._native(rel)).rstrip("/")
    rf, tf = (r.casefold(), t.casefold()) if ao.CASE_INSENSITIVE else (r, t)
    return rf.startswith(tf + "/") or rf == tf


def _verb_of(token: str) -> str:
    v = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if v.endswith(ext):
            v = v[: -len(ext)]
    if re.match(r"^python[0-9.]*w?$|^py$", v):
        return "python"
    if re.match(r"^(node|nodejs)$", v):
        return "node"
    if re.match(r"^perl[0-9.]*$", v):
        return "perl"
    if re.match(r"^ruby[0-9.]*$", v):
        return "ruby"
    if re.match(r"^php[0-9.]*$", v):
        return "php"
    return v


def _positionals(args: list[str], verb: str) -> list[str]:
    value_opts = VALUE_OPTS.get(verb, set())
    out: list[str] = []
    i = 0
    after_dd = False
    while i < len(args):
        a = args[i]
        if after_dd:
            out.append(a)
        elif a == "--":
            after_dd = True
        elif a.startswith("-") and len(a) > 1:
            name = a.split("=", 1)[0]
            if name in value_opts and "=" not in a:
                i += 1
        else:
            out.append(a)
        i += 1
    return out


def _option_values(args: list[str], names: set[str]) -> list[str]:
    out: list[str] = []
    for i, a in enumerate(args):
        name, eq, val = a.partition("=")
        if name in names:
            if eq:
                out.append(val)
            elif i + 1 < len(args):
                out.append(args[i + 1])
        elif any(a.startswith(n) and len(n) == 2 and len(a) > 2 and not a.startswith("--") for n in names):
            out.append(a[2:])   # -ofile
    return out


def _code_analysis(ctx: _Ctx, res: Analysis, code: str, lang: str) -> None:
    """Code passé en ligne à un interpréteur (`python -c`, `node -e`, heredoc…).

    Une chaîne qui NOMME un chemin régi sans rien écrire est une lecture : le
    refus systématique d'avant bloquait `python -c "print(open('…').read())"`.
    Du code qui écrit ET nomme un chemin régi voit ses chemins jugés comme des
    écritures — la matrice décide, pas le hook. Du code obscurci qui écrit, ou
    qui écrit depuis un répertoire courant régi, est opaque.
    """
    writes = bool(CODE_WRITE_RE.search(code))
    obfuscated = bool(CODE_OBFUSCATION_RE.search(code))
    literals = [m.group(0) for m in GOVERNED_LITERAL_RE.finditer(code)]
    if literals:
        res.mentions_governed = True
    if writes and obfuscated:
        res.opaque.append(f"code {lang} en ligne obscurci qui écrit (exec/eval/base64/chr…)")
        return
    for lit in literals:
        lit = lit.rstrip(".,")
        if writes:
            _add_write(ctx, res, lit, why=f"code {lang} en ligne")
        else:
            _add_read(ctx, res, lit, "file")
    if writes and not literals and (res.cwd_governed or _cwd_governed(ctx)):
        res.opaque.append(f"code {lang} en ligne qui écrit depuis un répertoire courant régi")
    # Une chaîne de secret nommée dans le code est une lecture de secret.
    for m in re.finditer(r"[^\s'\"]*\.env(?:\.[A-Za-z0-9_]+)?(?=['\"\s)]|$)", code, re.I):
        _add_read(ctx, res, m.group(0), "file")


def _cwd_governed(ctx: _Ctx) -> bool:
    from sdda_scripts import audit_ownership as ao
    return ctx.cwd is None or is_governed(ao.relative_to_root(ctx.root, ctx.cwd))


def _recurse(ctx: _Ctx, res: Analysis, code: str, dialect: str, why: str) -> None:
    if ctx.depth >= MAX_DEPTH:
        res.opaque.append(f"{why} imbriqué au-delà de {MAX_DEPTH} niveaux")
        return
    res.merge(analyze(ctx.root, code, ctx.cwd, dialect, ctx.env, ctx.depth + 1))


def _set_cwd(ctx: _Ctx, target: str | None) -> None:
    from sdda_scripts import audit_ownership as ao
    if target is None or target in ("-", "~") or target.startswith("~"):
        ctx.cwd = None if target != "~" else ao._native(os.path.expanduser("~"))
        return
    tok = _expand(ctx, target)
    if MARK in tok:
        ctx.cwd = None
        return
    native = ao._native(tok)
    if not ao._is_absolute(native):
        if ctx.cwd is None:
            return
        native = ctx.cwd.rstrip("/") + "/" + native
    import posixpath
    ctx.cwd = posixpath.normpath(native)


def _fragment(ctx: _Ctx, frag: _Fragment, res: Analysis) -> None:
    if ctx.dialect == "powershell":
        _ps_fragment(ctx, frag, res)
        return
    tokens = _tokens(frag.text, "bash")

    # Affectations seules (`X=workspace`, `export X=…`) : mémorisées pour la suite.
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        m = _REDIR_RE.match(tok)
        if m and not tok.startswith("-"):
            op, inline = m.group(2), m.group(3)
            target = inline or (tokens[i + 1] if i + 1 < len(tokens) else "")
            if not inline:
                i += 1
            if op in ("<<", "<<-", "<<<") or target.startswith("&"):
                i += 1
                continue
            if op == "<":
                _add_read(ctx, res, target, "file")
            else:
                _add_write(ctx, res, target, why="redirection")
            i += 1
            continue
        rest.append(tok)
        i += 1
    tokens = rest

    while tokens and _ASSIGN_RE.match(tokens[0]):
        name, value = _ASSIGN_RE.match(tokens[0]).groups()  # type: ignore[union-attr]
        ctx.env[name] = _expand(ctx, value)
        tokens = tokens[1:]
    if tokens and tokens[0] in ("export", "declare", "local", "readonly", "typeset"):
        for t in tokens[1:]:
            m = _ASSIGN_RE.match(t)
            if m:
                ctx.env[m.group(1)] = _expand(ctx, m.group(2))
        return
    if not tokens:
        return

    # Préfixes : sudo, time, env, timeout, nice -n…
    while tokens:
        v = _verb_of(tokens[0])
        if v in SKIP_WORDS:
            tokens = tokens[1:]
            while tokens and tokens[0].startswith("-"):
                tokens = tokens[1:]
            continue
        if v == "env":
            tokens = tokens[1:]
            while tokens and (tokens[0].startswith("-") or _ASSIGN_RE.match(tokens[0])):
                m = _ASSIGN_RE.match(tokens[0])
                if m:
                    ctx.env[m.group(1)] = _expand(ctx, m.group(2))
                tokens = tokens[1:]
            continue
        if v == "timeout":
            tokens = tokens[1:]
            while tokens and tokens[0].startswith("-"):
                tokens = tokens[1:]
            tokens = tokens[1:]          # la durée
            continue
        break
    if not tokens:
        return

    verb = _verb_of(tokens[0])
    args = tokens[1:]
    _verb(ctx, res, verb, args, frag)


def _verb(ctx: _Ctx, res: Analysis, verb: str, args: list[str], frag: _Fragment) -> None:
    pos = _positionals(args, verb)

    # --- navigation ---------------------------------------------------------
    if verb in ("cd", "pushd", "chdir", "set-location", "sl", "push-location"):
        _set_cwd(ctx, pos[0] if pos else None)
        if _cwd_governed(ctx):
            res.cwd_governed = True
        return
    if verb == "popd":
        ctx.cwd = None
        return

    # --- code : shells, interpréteurs, eval --------------------------------
    if verb in SHELLS:
        code = _option_values(args, {"-c"})
        if code:
            _recurse(ctx, res, code[0], "bash", f"`{verb} -c`")
        elif frag.heredoc is not None and not pos:
            _recurse(ctx, res, frag.heredoc, "bash", f"`{verb}` sur heredoc")
        elif frag.piped_in and not pos:
            res.opaque.append(f"code shell lu sur l'entrée standard (`… | {verb}`)")
        elif pos:
            _script_file(ctx, res, pos[0], verb)
        return
    if verb in POWERSHELLS:
        _powershell_invocation(ctx, res, args, frag)
        return
    if verb == "cmd":
        code = [a for a in args if a.lower() in ("/c", "/k")]
        if code:
            k = [a.lower() for a in args].index(code[0].lower())
            # cmd n'a pas d'échappement `\` : relu en dialecte bash, `workspace\
            # assets\.env` devenait `workspaceassets.env` et plus rien n'était régi.
            _recurse(ctx, res, " ".join(args[k + 1:]).replace("\\", "/"), "bash", "`cmd /c`")
        return
    if verb in ("eval",):
        _recurse(ctx, res, " ".join(args), "bash", "`eval`")
        return
    if verb in ("source", "."):
        if pos:
            _script_file(ctx, res, pos[0], verb)
        return
    if verb in CODE_INLINE_FLAGS:
        code, in_place = _inline_code(verb, args)
        if code is not None:
            _code_analysis(ctx, res, code, verb)
            if in_place:
                # `perl -pi -e … fichier` : les fichiers nommés sont RÉÉCRITS.
                for p in [a for a in pos if a != code]:
                    _add_write(ctx, res, p, why=f"`{verb} -i`")
            return
        if verb == "python" and "-m" in args:
            # Un module installé : son code est hors de portée lexicale, mais
            # ses ARGUMENTS ne le sont pas — `python -m base64 .env` lit le
            # secret, `python -m json.tool a.json <golden>` l'écrit.
            k = args.index("-m")
            module = args[k + 1] if k + 1 < len(args) else ""
            margs = [a for a in args[k + 2:] if not a.startswith("-")]
            for p in margs:
                _add_read(ctx, res, p, "file")
            if module.split(".", 1)[0] in WRITING_MODULES and margs and (
                    res.mentions_governed or _cwd_governed(ctx)):
                res.opaque.append(f"`python -m {module}` — écrit des fichiers que l'analyse ne nomme pas")
            return
        stdin_code = (not pos) or (pos and pos[0] == "-")
        if frag.heredoc is not None and stdin_code:
            _code_analysis(ctx, res, frag.heredoc, verb)
        elif frag.piped_in and stdin_code:
            res.opaque.append(f"code {verb} lu sur l'entrée standard (`… | {verb}`)")
        elif pos and pos[0] != "-":
            _script_file(ctx, res, pos[0], verb)
        return
    if verb == "xargs":
        inner = pos
        if inner:
            iv = _verb_of(inner[0])
            # Toujours opaque : `… | base64 -d | xargs rm -f` ne nomme aucun
            # chemin régi dans son texte — c'est justement l'intérêt de l'encoder.
            if iv in WRITE_ALL | WRITE_LAST | WRITE_MOVE | TEE or iv in SHELLS or iv in CODE_INLINE_FLAGS \
                    or iv in ("sed", "perl", "ruby", "dd", "truncate"):
                res.opaque.append(f"`xargs {iv}` — les chemins arrivent par l'entrée standard")
        return

    # --- git ----------------------------------------------------------------
    if verb == "git":
        _git(ctx, res, args)
        return

    # --- écritures ------------------------------------------------------------
    if verb in WRITE_ALL:
        paths = pos[1:] if verb in FIRST_IS_NOT_PATH else pos
        recursive = verb in ("rmdir", "rd") or (
            verb in ("rm", "del", "erase", "chmod", "chown", "chgrp") and any(_recursive_flag(a) for a in args))
        for p in paths:
            _add_write(ctx, res, p, recursive=recursive, why=f"`{verb}`", directory=verb == "mkdir")
        return
    if verb in WRITE_MOVE:
        for p in pos:
            _add_write(ctx, res, p, recursive=True, why=f"`{verb}`")
        for t in _option_values(args, {"-t", "--target-directory"}):
            _add_write(ctx, res, t, why=f"`{verb}`")
        return
    if verb in ("ln", "link"):
        # La CIBLE d'un lien est une écriture : `ln -s workspace/pipeline/datasets
        # vendor` puis `echo x > vendor/golden/g.jsonl` écrivait le golden sous
        # un nom anodin. Juger la cible comme écrite ferme la voie au lien.
        for p in pos + _option_values(args, {"-t", "--target-directory"}):
            _add_write(ctx, res, p, why=f"`{verb}`")
        return
    if verb in WRITE_LAST:
        targets = _option_values(args, {"-t", "--target-directory"})
        if targets:
            for t in targets:
                _add_write(ctx, res, t, recursive=True, why=f"`{verb}`")
            for p in pos:
                (_bulk_read(ctx, res, p) if any(_recursive_flag(a) for a in args) else _add_read(ctx, res, p, "file"))
        elif pos:
            _add_write(ctx, res, pos[-1], recursive=True, why=f"`{verb}`")
            for p in pos[:-1]:
                (_bulk_read(ctx, res, p) if any(_recursive_flag(a) for a in args) else _add_read(ctx, res, p, "content"))
        return
    if verb in TEE:
        for p in pos:
            _add_write(ctx, res, p, why="`tee`")
        return
    if verb == "dd":
        for a in args:
            if a.startswith("of="):
                _add_write(ctx, res, a[3:], why="`dd of=`")
            elif a.startswith("if="):
                _add_read(ctx, res, a[3:], "file")
        return
    if verb in ("curl", "wget"):
        for t in _option_values(args, {"-o", "--output", "-O", "--output-document"} if verb == "wget"
                                else {"-o", "--output"}):
            _add_write(ctx, res, t, why=f"`{verb}`")
        for t in _option_values(args, {"-P", "--directory-prefix"} if verb == "wget" else set()):
            _add_write(ctx, res, t, recursive=True, why=f"`{verb}`")
        if verb == "curl" and any(a in ("-O", "--remote-name", "-J") for a in args) and _cwd_governed(ctx):
            res.opaque.append("`curl -O` écrit dans le répertoire courant, régi")
        return
    if verb == "tar":
        first = args[0] if args else ""
        letters = first.lstrip("-") if not first.startswith("--") else ""
        extract = "x" in letters or any(a in ("--extract", "--get") for a in args)
        create = "c" in letters or any(a in ("--create", "--append", "--update") for a in args) \
            or "r" in letters or "u" in letters
        for d in _option_values(args, {"-C", "--directory"}):
            (_add_write(ctx, res, d, recursive=True, why="`tar -x`") if extract else _add_read(ctx, res, d, "content"))
        if extract and not _option_values(args, {"-C", "--directory"}):
            # Une archive porte ses propres chemins (`workspace/pipeline/…`) :
            # extraite depuis la racine, elle écrit n'importe où dessous.
            res.opaque.append("`tar -x` sans `-C` — les chemins écrits sont ceux de l'archive")
        for f in _option_values(args, {"-f", "--file"}):
            (_add_write(ctx, res, f, why="`tar -c`") if create else _add_read(ctx, res, f, "file"))
        if create:
            # `tar -cf - <chemins>` LIT les chemins nommés : `| cat` rendait un `.env`.
            skip = set(_option_values(args, {"-f", "--file", "-C", "--directory"}))
            for p in [a for a in args[1:] if not a.startswith("-") and a not in skip]:
                _bulk_read(ctx, res, p)
        return
    if verb in ("unzip", "7z", "7za"):
        dests = _option_values(args, {"-d"}) + [a[2:] for a in args if a.startswith("-o") and verb.startswith("7z")]
        for d in dests:
            _add_write(ctx, res, d, recursive=True, why=f"`{verb}`")
        if not dests and (verb == "unzip" or "x" in [a.lower() for a in args[:1]] or "e" in [a.lower() for a in args[:1]]):
            res.opaque.append(f"`{verb}` sans destination — les chemins écrits sont ceux de l'archive")
        return
    if verb == "mklink":
        # `mklink /J lien cible` : un lien vers une zone régie est une écriture
        # future de cette zone, sous un nom anodin.
        for p in [a for a in args if not a.startswith("/")]:
            _add_write(ctx, res, p, why="`mklink`")
        return
    if verb == "patch":
        for t in _option_values(args, {"-o", "--output"}):
            _add_write(ctx, res, t, why="`patch`")
        for p in pos:
            _add_write(ctx, res, p, why="`patch`")
        return
    if verb == "sort":
        for t in _option_values(args, {"-o", "--output"}):
            _add_write(ctx, res, t, why="`sort -o`")
        for p in pos:
            _add_read(ctx, res, p, "file")
        return
    if verb in ("uniq",):
        if len(pos) >= 2:
            _add_write(ctx, res, pos[1], why="`uniq`")
        if pos:
            _add_read(ctx, res, pos[0], "file")
        return
    if verb in ("mktemp",):
        for t in _option_values(args, {"-p", "--tmpdir"}):
            _add_write(ctx, res, t, recursive=True, why="`mktemp`")
        return
    if verb in READ_WITH_PROGRAM:
        # `-Ei`, `-ni` (lettres groupées) et `--in-place=.bak` réécrivent aussi :
        # seules `-i…` et `--in-place` nus étaient vus.
        in_place = verb == "sed" and any(
            a == "--in-place" or a.startswith("--in-place=")
            or (not a.startswith("--") and re.match(r"^-[A-Za-z]*i", a) is not None) for a in args) \
            or (verb in ("gawk", "awk") and "inplace" in args)
        has_script_opt = bool(_option_values(args, {"-e", "-f", "--expression", "--file"}))
        files = pos if has_script_opt else pos[1:]
        for p in files:
            (_add_write(ctx, res, p, why=f"`{verb} -i`") if in_place else _add_read(ctx, res, p, "file"))
        return
    # --- lectures -----------------------------------------------------------
    if verb in READ_FILE:
        for p in pos:
            _add_read(ctx, res, p, "file")
        return
    if verb in READ_TREE:
        explicit = bool(_option_values(args, {"-e", "-f", "--regexp", "--file"}))
        roots = pos if explicit else pos[1:]
        if verb == "findstr":
            roots = [a for a in args if not a.startswith("/")][1:]
        recursive = verb in ("rg", "ag", "ack") or any(
            a in ("-r", "-R", "--recursive", "--dereference-recursive", "/s", "/S") or
            (a.startswith("-") and not a.startswith("--") and ("r" in a[1:] or "R" in a[1:])) for a in args)
        hidden = verb == "grep" or verb in ("egrep", "fgrep", "findstr") or \
            any(a in ("--hidden", "-u", "-uu", "-uuu", "--no-ignore", "-.") for a in args)
        if not roots:
            roots = ["."]
        for p in roots:
            _add_read(ctx, res, p, "content")
            if recursive and hidden:
                paths, ok = _resolve(ctx, p)
                excluded = any(".env" in a for a in args if a.startswith(("--exclude", "-g", "--glob", "--iglob")))
                if ok and not excluded:
                    res.hidden_content_reads.extend("." if _contains_root(ctx, x) else x for x in paths)
        return
    if verb in LIST_TREE:
        if verb == "find":
            roots = []
            for a in args:
                if a.startswith(("-", "(", "!", ")")):
                    break
                roots.append(a)
            roots = roots or ["."]
            destructive = "-delete" in args
            exec_verbs = [args[i + 1] for i, a in enumerate(args) if a in ("-exec", "-execdir", "-ok", "-okdir")
                          and i + 1 < len(args)]
            for r in roots:
                _add_read(ctx, res, r, "names")
                if destructive or any(_verb_of(v) in WRITE_ALL | WRITE_MOVE | WRITE_LAST for v in exec_verbs):
                    _add_write(ctx, res, r, recursive=True, why="`find -delete/-exec`")
                elif any(_verb_of(v) in SHELLS or _verb_of(v) in CODE_INLINE_FLAGS for v in exec_verbs):
                    res.opaque.append("`find -exec` d'un interpréteur")
                elif any(_verb_of(v) in READ_FILE | READ_TREE for v in exec_verbs):
                    _bulk_read(ctx, res, r)
            for t in _option_values(args, {"-fprint", "-fprint0", "-fprintf", "-fls"}):
                _add_write(ctx, res, t, why="`find -fprint`")
            return
        for p in (pos or []):
            _add_read(ctx, res, p, "names")
        return
    # --- cmdlets PowerShell appelées depuis bash (et alias PS en dialecte PS)
    if verb in PS_WRITE_PATH | PS_READ_PATH | PS_LIST_PATH or verb in (
            "copy-item", "move-item", "select-string", "invoke-expression", "invoke-webrequest",
            "invoke-restmethod", "expand-archive", "compress-archive", "start-process", "invoke-command"):
        _ps_cmdlet(ctx, res, verb, args, frag)
        return


def _recursive_flag(a: str) -> bool:
    return a in ("--recursive", "/s", "/S", "/q") or (
        a.startswith("-") and not a.startswith("--") and ("r" in a[1:] or "R" in a[1:]))


def _script_file(ctx: _Ctx, res: Analysis, script: str, verb: str) -> None:
    """Un script sur disque (`python x.py`, `bash build.sh`, `. env.sh`).

    Le hook ne l'interprète pas, et ne le refuse pas non plus : les agents
    lancent légitimement l'outillage du framework (`python .sdda/sdda.py …`) et
    le produit qu'ils construisent (smoke, tests). Ce qu'un script écrit de
    l'intérieur est la limite déclarée de l'analyse lexicale, et c'est
    `audit-ownership --since-snapshot` qui le voit, après la phase.

    Mais EXÉCUTER un fichier le LIT : `source workspace/assets/.env` rend
    toutes les valeurs dans l'environnement, et c'était un no-op — le secret
    passait. Le script est donc jugé comme une lecture.
    """
    _add_read(ctx, res, script, "file")


def _git(ctx: _Ctx, res: Analysis, args: list[str]) -> None:
    rest = list(args)
    while rest and rest[0].startswith("-"):
        if rest[0] == "-C" and len(rest) > 1:
            _set_cwd(ctx, rest[1])
            rest = rest[2:]
            continue
        if rest[0] in ("-c", "--git-dir", "--work-tree") and len(rest) > 1:
            rest = rest[2:]
            continue
        rest = rest[1:]
    if not rest:
        return
    sub, sargs = rest[0].lower(), rest[1:]
    pos = [a for a in _positionals(sargs, "git-" + sub)]
    if sub in GIT_PATH_WRITES:
        if "--" in sargs:
            paths = sargs[sargs.index("--") + 1:]
        elif sub == "checkout":
            # `git checkout <branche>` réécrit l'arbre ; `git checkout <rev> <chemin>` le chemin.
            named = [p for p in pos if any(is_governed(x) for x in _resolve(ctx, p)[0])]
            paths = named or (["."] if pos else [])
        else:
            paths = pos                     # `git mv` : source ET destination changent
        for p in paths or ["."]:
            _add_write(ctx, res, p, recursive=True, why=f"`git {sub}`")
        return
    if sub in GIT_TREE_WRITES:
        if sub == "stash" and sargs[:1] in (["list"], ["show"]):
            return
        _add_write(ctx, res, ".", recursive=True, why=f"`git {sub}`")
        return
    if sub == "grep":
        roots = [p for p in pos[1:]] or ["."]
        for r in roots:
            _add_read(ctx, res, r, "content")
        return
    if sub in GIT_PATH_READS:
        # `git diff --no-index /dev/null <fichier>` affiche un fichier entier,
        # `git show HEAD:<chemin>` un fichier versionné : ce sont des lectures.
        paths = sargs[sargs.index("--") + 1:] if "--" in sargs else pos
        for p in paths:
            _add_read(ctx, res, p.split(":", 1)[1] if re.match(r"^[^/\\:]+:[^/\\]", p) else p, "file")
        return


def _powershell_invocation(ctx: _Ctx, res: Analysis, args: list[str], frag: _Fragment) -> None:
    lower = [a.lower() for a in args]
    for flag in ("-encodedcommand", "-enc", "-ec", "-e"):
        if flag in lower:
            k = lower.index(flag)
            if k + 1 < len(args):
                try:
                    code = base64.b64decode(args[k + 1]).decode("utf-16-le")
                except (ValueError, UnicodeDecodeError):
                    res.opaque.append("`powershell -EncodedCommand` indéchiffrable")
                    return
                _recurse(ctx, res, code, "powershell", "`powershell -EncodedCommand`")
            return
    for flag in ("-command", "-c"):
        if flag in lower:
            k = lower.index(flag)
            code = " ".join(args[k + 1:])
            if code.strip() == "-":
                if frag.piped_in:
                    res.opaque.append("code PowerShell lu sur l'entrée standard")
                return
            _recurse(ctx, res, code, "powershell", "`powershell -Command`")
            return
    for flag in ("-file", "-f"):
        if flag in lower:
            k = lower.index(flag)
            if k + 1 < len(args):
                _script_file(ctx, res, args[k + 1], "powershell -File")
            return
    if frag.piped_in:
        res.opaque.append("code PowerShell lu sur l'entrée standard")


# ---------------------------------------------------------------------------
# PowerShell
# ---------------------------------------------------------------------------
def _ps_fragment(ctx: _Ctx, frag: _Fragment, res: Analysis) -> None:
    text = frag.text
    m = _PS_ASSIGN_RE.match(text)
    if m:
        value = _ps_tokens(m.group(2))
        if len(value) == 1:
            ctx.env[m.group(1)] = _expand(ctx, value[0])
        else:
            ctx.env.pop(m.group(1), None)
        return
    tokens = _ps_tokens(text)
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        mm = _REDIR_RE.match(tok)
        if mm and not tok.startswith("-"):
            op, inline = mm.group(2), mm.group(3)
            target = inline or (tokens[i + 1] if i + 1 < len(tokens) else "")
            if not inline:
                i += 1
            if not target.startswith("&") and op not in ("<<", "<<-", "<<<"):
                (_add_read(ctx, res, target, "file") if op == "<" else _add_write(ctx, res, target, why="redirection"))
            i += 1
            continue
        rest.append(tok)
        i += 1
    tokens = rest
    if not tokens:
        return
    if tokens[0] == "&" or tokens[0] == ".":
        tokens = tokens[1:]
        if tokens:
            _script_file(ctx, res, tokens[0], "&")
        return
    raw = tokens[0].lower()
    verb = PS_ALIASES.get(raw, raw)
    verb = _verb_of(verb) if "/" in verb or "\\" in verb or verb.endswith(".exe") else verb
    args = tokens[1:]
    if verb in PS_WRITE_PATH | PS_READ_PATH | PS_LIST_PATH or verb in (
            "copy-item", "move-item", "select-string", "invoke-expression", "invoke-webrequest",
            "invoke-restmethod", "expand-archive", "compress-archive", "start-process", "invoke-command",
            "set-location", "push-location", "pop-location"):
        _ps_cmdlet(ctx, res, verb, args, frag)
        return
    if verb in ("where-object", "foreach-object", "select-object", "sort-object", "%", "?"):
        if frag.piped_in and any("remove-item" in a.lower() or "set-content" in a.lower() for a in args):
            res.opaque.append("écriture dans un bloc de pipeline")
        return
    # Programme externe appelé depuis PowerShell : mêmes règles que le shell.
    _verb(ctx, res, _verb_of(tokens[0]), args, frag)


def _ps_param(name: str) -> str:
    """Un paramètre PowerShell ABRÉGÉ -> son nom complet.

    PowerShell accepte tout préfixe non ambigu (`-Dest`, `-LiteralP`, `-Val`) :
    la correspondance exacte laissait `Copy-Item … -Dest <golden>` écrire sans
    que la destination soit vue. Un préfixe ambigu est résolu vers le chemin
    plutôt que vers la valeur : juger un argument de trop comme un chemin coûte
    moins cher que d'en laisser passer un.
    """
    known = PS_PATH_PARAMS | PS_DEST_PARAMS | PS_VALUE_PARAMS | {"-target"}
    if name in known or len(name) < 3:
        return name
    for family in (PS_PATH_PARAMS, PS_DEST_PARAMS, {"-target"}, PS_VALUE_PARAMS):
        hits = sorted(p for p in family if p.startswith(name))
        if hits:
            return hits[0]
    return name


def _ps_split(args: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    named: dict[str, list[str]] = {}
    positional: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-") and len(a) > 1 and not re.match(r"^-\d", a):
            name, colon, val = a.partition(":")
            name = _ps_param(name.lower())
            if colon:
                named.setdefault(name, []).append(val)
            elif name in PS_PATH_PARAMS | PS_DEST_PARAMS | PS_VALUE_PARAMS | {"-target"} and i + 1 < len(args):
                named.setdefault(name, []).append(args[i + 1])
                i += 1
            else:
                named.setdefault(name, [])
        else:
            positional.append(a)
        i += 1
    return named, positional


def _ps_cmdlet(ctx: _Ctx, res: Analysis, verb: str, args: list[str], frag: _Fragment) -> None:
    named, pos = _ps_split(args)
    paths = [v for p in PS_PATH_PARAMS for v in named.get(p, [])]
    dests = [v for p in PS_DEST_PARAMS for v in named.get(p, [])]
    recursive = any(k.startswith("-rec") or k == "-r" for k in named)
    if verb in ("set-location", "push-location"):
        _set_cwd(ctx, (paths or pos or [None])[0])
        return
    if verb == "pop-location":
        ctx.cwd = None
        return
    if verb == "invoke-expression":
        code = named.get("-command", []) + pos
        if code and MARK not in " ".join(code) and not any(c.startswith("$") for c in code):
            _recurse(ctx, res, " ".join(code), "powershell", "`Invoke-Expression`")
        elif frag.piped_in or code:
            res.opaque.append("`Invoke-Expression` d'une valeur que l'analyse ne connaît pas")
        return
    if verb in ("start-process", "invoke-command"):
        if res.mentions_governed or _cwd_governed(ctx):
            res.opaque.append(f"`{verb}` — la commande lancée n'est pas lisible ici")
        return
    if verb in ("invoke-webrequest", "invoke-restmethod"):
        for d in named.get("-outfile", []):
            _add_write(ctx, res, d, why=f"`{verb} -OutFile`")
        return
    if verb in ("expand-archive", "compress-archive"):
        for d in dests or pos[1:2]:
            _add_write(ctx, res, d, recursive=True, why=f"`{verb}`")
        return
    if verb == "copy-item":
        src = paths or pos[:1]
        dst = dests or pos[1:2]
        for s in src:
            (_bulk_read(ctx, res, s) if recursive else _add_read(ctx, res, s, "content"))
        for d in dst:
            _add_write(ctx, res, d, recursive=True, why="`Copy-Item`")
        return
    if verb == "move-item":
        for s in (paths or pos[:1]) + (dests or pos[1:2]):
            _add_write(ctx, res, s, recursive=True, why="`Move-Item`")
        return
    if verb == "select-string":
        roots = paths or (pos[1:] if "-pattern" not in named else pos)
        for r in roots:
            _add_read(ctx, res, r, "content")
        return
    if verb == "tee-object":
        for p in named.get("-filepath", []) + named.get("-literalpath", []) + pos[:1]:
            _add_write(ctx, res, p, why="`Tee-Object`")
        return
    targets = paths or pos[:1]
    if verb == "new-item" and named.get("-name"):
        base = targets[0] if targets else "."
        targets = [base.rstrip("/\\") + "/" + named["-name"][0]]
    if verb in PS_WRITE_PATH:
        kinds = [v.lower() for v in named.get("-itemtype", []) + named.get("-type", [])]
        is_dir = verb == "new-item" and "directory" in kinds
        for p in targets:
            _add_write(ctx, res, p, recursive=recursive, why=f"`{verb}`", directory=is_dir)
        if verb == "new-item" and any(k in ("symboliclink", "junction", "hardlink") for k in kinds):
            # La CIBLE d'un lien est une écriture future (cf. `ln`) : sans ce
            # jugement, une jonction vers `pipeline/datasets` ouvrait le golden.
            for t in named.get("-target", []) + named.get("-value", []):
                _add_write(ctx, res, t, recursive=True, why="`New-Item` lien")
        return
    if verb in PS_READ_PATH:
        for p in targets:
            _add_read(ctx, res, p, "file")
        return
    if verb in PS_LIST_PATH:
        for p in targets:
            _add_read(ctx, res, p, "content" if recursive and frag.piped_in is False and "-force" in named else "names")
        return
