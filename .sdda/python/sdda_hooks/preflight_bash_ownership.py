#!/usr/bin/env python3
"""Le trou du shell dans la matrice d'ownership — `Bash` et `PowerShell`.

`preflight_ownership` s'exécute sur `Write`, `Edit`, `NotebookEdit`. Un agent qui
écrit `echo … > workspace/pipeline/datasets/golden/x.jsonl` ou
`Remove-Item workspace/src/{App}/prompts/a.system.md` ne passe par aucun des
trois : la matrice était contournable par le shell, et c'est par là qu'un
`dev-agent` peut retoucher le jeu qui le juge sans qu'aucun hook ne le voie.

La commande est analysée par `_shell.analyze` (cf. son docstring : ce qu'il
résout, ce qu'il déclare opaque, ce qui reste hors de portée), puis chaque
chemin régi est confronté à `loader.yml` — écritures via `check_write` (et
`check_recursive_write` pour un répertoire entier), lectures via `check_read`.

L'outil `PowerShell` n'était surveillé par AUCUN hook : un sous-agent sous
Windows écrivait le golden par `Set-Content` sans que rien ne s'y oppose. Il
passe désormais par ce hook, avec le dialecte PowerShell (alias `sc`, `gc`,
`ni`, `ri`…, paramètres `-Path`/`-Destination`, `iex`, `-EncodedCommand`).

Le fil principal (aucun `agent_type`) passe, sauf sur les zones protégées que
même lui n'écrit pas au shell (baseline, journal d'audit) : la matrice ne régit
que les agents. Les formes OPAQUES ne sont refusées qu'aux sous-agents.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, agent_of, deny, run, unknown_subagent  # noqa: E402

HOOK = "preflight_bash_ownership"

#: Câblage — lu par `harness_build.py`. `applies_to` vide : la matrice vaut
#: pour chaque commande shell, quel qu'en soit l'auteur agent. `PowerShell`
#: est l'outil shell de Claude Code sous Windows : sans lui dans le matcher, un
#: sous-agent y avait un shell sans aucun hook.
WIRING = {"event": "PreToolUse", "matcher": "Bash|PowerShell", "applies_to": ()}

#: Une écriture dont la cible ne se résout pas sans exécuter la commande.
CLS_SHELL_OPAQUE = "OWNERSHIP_SHELL_OPAQUE"

#: Zones protégées que le fil principal lui-même n'écrit pas au shell. Pas
#: `.sys/.validation/` : les commandes y redirigent la sortie JSON de leurs
#: scripts (`eval-runner … > workspace/.sys/.validation/{n}-G5-agent.json`),
#: c'est le mécanisme documenté par lequel un rapport de gate est déposé. Le
#: fil principal y reste refusé à `Write`/`Edit` — la contrefaçon à la main —
#: et les sous-agents y sont refusés au shell comme à l'éditeur.
MAIN_THREAD_SHELL_PROTECTED = ("workspace/pipeline/baselines", "workspace/.sys/.audit")


def classify(root: Path, command: str, cwd: str | None = None, dialect: str = "bash") -> tuple[list[str], list[tuple[str, str]]]:
    """(écritures, lectures) nommées par la commande — chemins sous zones régies.

    Conservé pour les appelants et les tests d'avant ; l'analyse complète
    (opacité, récursivité, recherches qui lisent les fichiers cachés) est
    `_shell.analyze`.
    """
    import _shell  # noqa: E402

    res = _shell.analyze(root, command, cwd, dialect)
    return res.writes, res.reads


def _protected_verdict(ao, loader: dict, agent: str, writes: list[str]) -> int:
    """Les zones protégées (`audit_ownership.PROTECTED_ZONES`) — les MÊMES que
    `preflight_ownership`. Une redirection `>` vers `.sys/.validation/` passait
    le shell alors que l'éditeur la refusait : la protection tenait à l'outil
    choisi, c'est-à-dire à rien."""
    for path in writes:
        found = ao.protected_zone(path)
        if found is None:
            continue
        zone = found[0]
        if not agent and zone not in MAIN_THREAD_SHELL_PROTECTED:
            continue
        verdict = ao.protected_write(loader, agent, path)
        if verdict is not None:
            cls, fix = verdict
            return deny(HOOK, cls, f"via le shell — `{path}` écrit{f' par `{agent}`' if agent else ''}", fix)
    return ALLOW


def _deny_secret(agent: str, what: str, fix: str = "") -> int:
    return deny(HOOK, "SECRET_READ_FORBIDDEN",
                f"via le shell — `{agent}` a voulu lire `{what}` : un fichier de secrets n'est lu par aucun agent",
                fix or "`python .sdda/sdda.py install-env` copie assets/.env vers src/{App}/.env, sans LLM")


#: Un nom de fichier de secrets dans le TEXTE de la commande. L'analyse
#: lexicale ne peut pas énumérer tous les lecteurs de fichiers (`source`,
#: `perl -pe 1`, `git diff --no-index`, `[IO.File]::ReadAllText`, `cmd /c type`,
#: `Get-Content (Join-Path …)`) : chacun trouvé en ferme un, le suivant reste
#: ouvert. Aucun agent n'a de raison de NOMMER un `.env` dans une commande —
#: `install-env` le copie sans qu'on le nomme — donc un sous-agent qui le
#: nomme est refusé, quelle que soit la forme. `process.env`, `os.environ`,
#: `.venv`, `$env:` ne correspondent pas ; les gabarits (`.env.example`…) non plus.
_SECRET_IN_TEXT_RE = re.compile(r"(?<![A-Za-z0-9_$])\.env(?![A-Za-z0-9_])(?:\.[A-Za-z0-9_-]+)?|\benv~\d", re.I)
#: Motifs d'EXCLUSION (`grep --exclude='.env*'`, `rg -g '!.env'`) : nommer le
#: secret pour l'écarter est précisément ce qu'on demande de faire.
_EXCLUDE_OPT_RE = re.compile(r"""--exclude(?:-dir)?(?:=|\s+)\S+|(?:-g|--g?lob|--iglob)(?:=|\s+)['"]?!\S+""", re.I)


def _names_secret(ao, command: str) -> str | None:
    text = _EXCLUDE_OPT_RE.sub(" ", command)
    for m in _SECRET_IN_TEXT_RE.finditer(text):
        name = m.group(0)
        if name.casefold().startswith("env~") or ao.is_secret_file(name.rstrip("'\")]};,")):
            return name
    return None


def _with_real_paths(ao, root: Path, res) -> None:
    """Ajoute à l'analyse les chemins RÉELS (liens, jonctions) quand ils diffèrent.

    `_shell` résout lexicalement : une jonction `agents/a/j -> pipeline/datasets`
    rendait `agents/a/j/golden/g.jsonl`, anodin, pour une écriture du golden.
    `preflight_ownership` jugeait déjà le chemin réel ; le shell non.
    """
    extra_writes = []
    for p in res.writes:
        real = ao.real_relative_to_root(root, p)
        if real and real not in res.writes:
            extra_writes.append(real)
            if p in res.recursive_writes:
                res.recursive_writes.append(real)
            if p in res.mkdirs:
                res.mkdirs.append(real)
    res.writes.extend(extra_writes)
    extra_reads = []
    for p, scope in res.reads:
        real = ao.real_relative_to_root(root, p)
        if real and (real, scope) not in res.reads:
            extra_reads.append((real, scope))
    res.reads.extend(extra_reads)


def check(root: Path, data: dict) -> int:
    import _shell  # noqa: E402

    agent = agent_of(data)
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    command = str(tool_input.get("command") or data.get("command") or "")
    if not command.strip():
        return ALLOW
    dialect = "powershell" if str(data.get("tool_name") or "").lower() == "powershell" else "bash"

    from sdda_scripts import audit_ownership as ao  # noqa: E402

    # 0. Le filet : un sous-agent qui NOMME un fichier de secrets.
    if agent:
        named = _names_secret(ao, command)
        if named:
            return _deny_secret(agent, named)

    res = _shell.analyze(root, command, data.get("cwd"), dialect)
    if not (res.writes or res.reads or res.opaque or res.unresolved_reads or res.hidden_content_reads):
        return ALLOW

    from sdda_lib.errors import Report  # noqa: E402  (import tardif : coût de démarrage du hook)

    _with_real_paths(ao, root, res)
    loader = ao.load_loader(root) if agent else {}
    verdict = _protected_verdict(ao, loader, agent, res.writes)
    if verdict != ALLOW or not agent:
        return verdict

    # 1. Secrets — nommés, devinés derrière une variable, ou sous une lecture
    #    en vrac qui lit aussi les fichiers cachés (`grep -r`, `find … -exec
    #    cat`, `tar -c`, `cp -r`), y compris depuis un parent de la racine.
    #    Jugé pour TOUT sous-agent : ce bloc ne venait qu'après la matrice,
    #    donc jamais pour un sous-agent hors matrice.
    for path, _scope in res.reads:
        if ao.is_secret_file(str(path)):
            return _deny_secret(agent, path)
    for token in res.unresolved_reads:
        if ".env" in token.casefold():
            return _deny_secret(agent, token)
    for directory in res.hidden_content_reads:
        found = ao.secrets_under(root, directory)
        if found:
            return _deny_secret(agent, f"{found[0]} (via une lecture récursive de `{directory}`)",
                                "exclure les secrets de la lecture (`--exclude='.env*'`), ou employer "
                                "`rg`, qui saute les fichiers cachés")

    known = isinstance(loader.get(agent), dict)
    if not known:
        # Sous-agent hors matrice : rien sous workspace/, ni en écriture ni en
        # lecture. Voir `_hook.unknown_subagent` pour le pourquoi.
        for path, is_write in [*((p, True) for p in res.writes), *((p, False) for p, _scope in res.reads)]:
            verdict = unknown_subagent(HOOK, agent, str(path), write=is_write)
            if verdict != ALLOW:
                return verdict

    # 2. L'opaque : une écriture qu'on ne sait pas nommer. Refusée à tout
    #    sous-agent — l'analyse ne l'a retenue que si elle PEUT toucher une zone
    #    régie (variable, substitution, code obscurci, entrée standard).
    if res.opaque:
        return deny(HOOK, CLS_SHELL_OPAQUE,
                    f"via le shell — `{agent}` : {res.opaque[0]}",
                    "écrire avec un chemin LITTÉRAL (ou par Write/Edit) : une cible que le hook ne peut "
                    "pas nommer sans exécuter la commande est une cible qu'il ne peut pas juger. "
                    "`bash -c`, `eval`, `$(…)`, `python -c` restent permis tant que ce qu'ils écrivent se lit")
    if not known:
        return ALLOW

    # 3. La matrice : écritures (répertoires entiers compris), puis lectures.
    report = Report(name="BASH-HOOK", target=str(root))
    # Un répertoire CRÉÉ se juge sur ce qu'il contiendra : `mkdir -p
    # agents/billing` est à l'instance qui écrira `agents/billing/x`.
    mkdirs = set(res.mkdirs)
    judged = [(p + "/x" if p in mkdirs else p) for p in res.writes]
    bindings, verdict = _bindings(root, loader, agent, judged, data)
    if verdict != ALLOW:
        return verdict
    recursive = set(res.recursive_writes)
    for original, path in zip(res.writes, judged):
        ok = (ao.check_recursive_write(loader, agent, path, report, bindings, root) if original in recursive
              else ao.check_write(loader, agent, path, report, bindings))
        if not ok:
            break
    else:
        if ao.forbidden_reads_of(loader, agent):
            for path, scope in res.reads:
                if not ao.check_read(loader, agent, path, report, scope=scope):
                    break

    if not report.ok:
        first = report.errors[0]
        return deny(HOOK, first.cls, f"via le shell — {first.message}",
                    first.fix or "passer par Write/Edit dans ta zone, ou élargir la matrice explicitement")
    return ALLOW


def _bindings(root: Path, loader: dict, agent: str, writes: list[str], data: dict):
    """Liaison d'instance (`{agent}` de `dev-agent`) — cf. `_instances`."""
    try:
        import _instances  # noqa: E402
    except ImportError:  # pragma: no cover
        return None, ALLOW
    bindings = None
    for path in writes:
        bindings, verdict = _instances.bindings_for_write(root, loader, agent, path, data, HOOK)
        if verdict != ALLOW:
            return None, verdict
    return bindings, ALLOW


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
