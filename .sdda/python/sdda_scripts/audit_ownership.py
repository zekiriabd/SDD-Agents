#!/usr/bin/env python3
"""Matrice d'écriture — qui a le droit d'écrire quoi. 0 token.

Ce que cet audit défend : *le parallélisme des `dev-*` n'est sûr que parce que
leurs répertoires sont disjoints.* Trois `dev-agent` tournent en même temps ;
rien, au runtime, ne les empêche d'écrire dans le même fichier — sinon cette
matrice, et le fait que quelqu'un la vérifie.

Il attrape deux fautes de nature différente :

    1. **Hors zone** — un agent a écrit là où `loader.yml writes:` ne l'autorise
       pas. Le cas qui coûte le plus cher n'est pas la collision (elle se voit) :
       c'est le `dev-agent` qui retouche `workspace/pipeline/datasets/` ou
       `workspace/src/{App}/prompts/`, c'est-à-dire qui modifie le jeu qui le juge ou le
       prompt qu'il implémente. `[DATASET_OWNERSHIP_VIOLATION]`,
       `[PROMPT_OWNERSHIP_VIOLATION]` — la note devient invérifiable.

    2. **Zone contestée** — deux agents déclarent le même chemin en écriture
       sans que la matrice ne les sérialise. C'est un défaut de la DÉCLARATION,
       pas d'un run : il se corrige dans `loader.yml`, et il se voit avant le
       premier spawn.

Les chemins sont comparés en glob, avec `**` matchant **zéro segment ou plus** :
la profondeur du chemin applicatif appartient à la fiche de langage, pas à cette
matrice (cf. `rules/ownership.md`).

Et une troisième, qui est la seule à regarder le disque :

    3. **Écriture réelle hors zone** — un instantané pris AVANT une phase
       (`snapshot`), puis l'écart après elle (`--since-snapshot`) : chaque
       fichier créé, modifié ou supprimé est attribué à la zone d'un agent de
       la phase, et, pour `dev-agent`, au répertoire d'une instance déclarée.
       `--restore` révoque ce qui ne l'est pas.

Usage :
    python .sdda/sdda.py audit-ownership --declared-only --json   # cohérence de loader.yml seule
    python .sdda/sdda.py audit-ownership snapshot --mission 1 --phase 4
    python .sdda/sdda.py audit-ownership --mission 1 --phase 4 --since-snapshot --instances billing,triage
    python .sdda/sdda.py audit-ownership --agent dev-agent --wrote workspace/src/prompts/x.system.md
"""
from __future__ import annotations

import argparse
import functools
import os
import posixpath
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io, paths, yaml_mini  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

NON_AGENT_KEYS = frozenset({"version", "updated", "cross_agent_reads", "shared_writes"})

#: Répertoires dont une écriture par un `dev-*` invalide la mesure elle-même.
#: Ce ne sont pas des zones « sensibles » au sens vague : ce sont celles qui
#: portent le juge et le sujet, et les confondre rend le verdict sans valeur.
#: Des MOTIFS (globs de `loader.yml`), pas des préfixes : les prompts, skills et
#: rules vivent DANS l'application (`workspace/src/{App}/…`), et le nom de
#: l'application n'est pas connu ici.
SACRED: dict[str, tuple[str, str]] = {
    "workspace/pipeline/datasets/**": ("DATASET_OWNERSHIP_VIOLATION",
                                    "un `dev-*` qui modifie le jeu qui le juge produit une note invérifiable"),
    "workspace/src/*/prompts/**": ("PROMPT_OWNERSHIP_VIOLATION",
                                   "un `dev-*` qui réécrit le prompt qu'il implémente efface la spécification "
                                   "qu'on voulait comparer au code"),
    "workspace/src/*/skills/**": ("PROMPT_OWNERSHIP_VIOLATION",
                                  "une skill est une consigne du prompt : la réécrire depuis le code, c'est réécrire le prompt"),
    "workspace/src/*/rules/**": ("PROMPT_OWNERSHIP_VIOLATION",
                                 "une rule est une consigne du prompt : la réécrire depuis le code, c'est réécrire le prompt"),
    "workspace/pipeline/baselines/**": ("BASELINE_OWNERSHIP_VIOLATION",
                                     "déplacer la baseline de référence rend toute non-régression tautologique"),
}


def _sacred_class(path: str) -> tuple[str, str]:
    """La zone sacrée que ce chemin touche, si elle existe.

    Nommer la zone plutôt que rendre un `[OWNERSHIP_VIOLATION]` générique n'est
    pas cosmétique : « a écrit hors zone » se corrige en élargissant la matrice,
    « a modifié le jeu qui le juge » ne se corrige pas du tout — la mesure est
    perdue, et il faut la refaire.
    """
    normalized = normalize(path)
    for zone, (cls, why) in SACRED.items():
        if matches(zone, normalized) or matches(zone, normalized + "/x"):
            return cls, why
    return "", ""


#: Systèmes de fichiers insensibles à la casse : Windows, et macOS par défaut.
#: Sur eux, `Workspace/Pipeline/Datasets/x` EST `workspace/pipeline/datasets/x` —
#: un matcher sensible à la casse laissait le shell écrire le golden sous un
#: autre nom, et le fichier atterrissait au même endroit. Ailleurs, deux casses
#: sont deux fichiers, et les confondre refuserait à tort.
CASE_INSENSITIVE = os.name == "nt" or sys.platform == "darwin"

_GLOB_CHARS = "*?{["


def normalize(path: str) -> str:
    """Forme canonique LEXICALE d'un chemin relatif : `/`, sans `./`, `..` résolu.

    `path.lstrip("./")`, la forme d'avant, retirait des CARACTÈRES et non un
    préfixe : `.sdda/loader.yml` devenait `sdda/loader.yml`, `.sys/` devenait
    `sys/`. Et `workspace/src/../pipeline/datasets/x` n'était ramené à rien —
    le chemin réel était pourtant celui du golden.
    """
    p = str(path).replace("\\", "/")
    p = re.sub(r"/{2,}", "/", p)
    while p.startswith("./"):
        p = p[2:]
    if not p:
        return "."
    return posixpath.normpath(p)


_GIT_BASH_DRIVE_RE = re.compile(r"^/(?:mnt/|cygdrive/)?([A-Za-z])(?=/|$)")


def _native(path: str) -> str:
    """`/g/Dev/x` (Git Bash, MSYS), `/mnt/g/…` (WSL), `/cygdrive/g/…` -> `G:/Dev/x`.

    Sous Windows, le shell du harnais est Git Bash : un agent y écrit
    naturellement `/g/Developement/SDD-Agents/workspace/…`. `Path` ne sait pas
    le lire, le chemin semblait hors projet, et l'écriture passait.
    """
    p = str(path).replace("\\", "/")
    if os.name == "nt" or CASE_INSENSITIVE:
        m = _GIT_BASH_DRIVE_RE.match(p)
        if m:
            p = m.group(1).upper() + ":" + (p[m.end():] or "/")
    return p


def _is_absolute(p: str) -> bool:
    return p.startswith("/") or bool(re.match(r"^[A-Za-z]:/", p)) or p.startswith("//")


def relative_to_root(root: Path, target: str, cwd: Path | str | None = None) -> str:
    """Un chemin tel que l'agent l'a écrit -> relatif à la racine du projet.

    LEXICAL d'abord (`..` résolu sans toucher au disque, casse du système de
    fichiers respectée) : c'est ce qui rend `./src/../workspace/…` et
    `Workspace/…` comparables à la matrice. Un chemin hors projet est rendu
    absolu, tel quel : aucune zone ne le régit.
    """
    t = _native(str(target).strip())
    base = _native(Path(cwd).as_posix()) if cwd else _native(Path(root).as_posix())
    if not _is_absolute(t):
        t = base.rstrip("/") + "/" + t
    t = posixpath.normpath(t)
    r = posixpath.normpath(_native(Path(root).as_posix()))
    tf, rf = (t.casefold(), r.casefold()) if CASE_INSENSITIVE else (t, r)
    if tf == rf:
        return "."
    if tf.startswith(rf.rstrip("/") + "/"):
        return t[len(r.rstrip("/")) + 1:]
    return t


def real_relative_to_root(root: Path, target: str, cwd: Path | str | None = None) -> str | None:
    """Comme `relative_to_root`, liens symboliques et jonctions RÉSOLUS — ou None.

    Un lien `src/App/vendor -> ../../pipeline/datasets` rend lexicalement un
    chemin anodin et physiquement le golden. On rend l'autre lecture quand elle
    diffère ; l'appelant juge les deux.
    """
    lexical = relative_to_root(root, target, cwd)
    try:
        absolute = Path(root) / lexical if not _is_absolute(lexical) else Path(lexical)
        real = Path(os.path.realpath(absolute))
        real_root = Path(os.path.realpath(root))
        rel = real.relative_to(real_root).as_posix() if real != real_root else "."
    except (OSError, ValueError):
        return None
    if (rel.casefold() if CASE_INSENSITIVE else rel) == (lexical.casefold() if CASE_INSENSITIVE else lexical):
        return None
    return rel


@functools.lru_cache(maxsize=4096)
def _to_regex(pattern: str, bindings: tuple[tuple[str, str], ...] = (),
              fold: bool = False) -> re.Pattern[str]:
    """Glob de `loader.yml` -> regex, SEGMENTÉ : `*` = dans un segment, `**` = n segments.

    `workspace/src/**/data/**` doit matcher `workspace/src/data/x.py` ET
    `workspace/src/App/src/App/data/x.py` : un enforcer qui exigerait au moins
    un segment déclarerait hors zone toutes les écritures d'un projet à
    arborescence plate — et un enforcer qui dit l'inverse de la vérité est pire
    qu'aucun enforcer.

    `bindings` fixe la valeur d'un placeholder (`{agent}` -> `billing`) : c'est
    la liaison d'instance de `dev-agent`. Sans liaison, `{x}` vaut un segment
    non vide quelconque — donc N'IMPORTE QUELLE instance.
    """
    bound = dict(bindings)
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append(r"(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(r".*")
            i += 2
        elif pattern[i] == "*":
            out.append(r"[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append(r"[^/]")
            i += 1
        elif pattern[i] == "{":
            end = pattern.find("}", i)
            if end == -1:
                out.append(re.escape(pattern[i]))
                i += 1
            else:
                inner = pattern[i + 1:end]
                if "," in inner:
                    out.append("(?:" + "|".join(re.escape(p) for p in inner.split(",")) + ")")
                elif inner in bound:
                    out.append(re.escape(bound[inner]))
                else:
                    out.append(r"[^/]+")
                i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE if fold else 0)


def matches(pattern: str, path: str, bindings: dict[str, str] | None = None) -> bool:
    """Le chemin est-il dans la zone que le motif désigne ?

    Le matching est SEGMENTÉ, sans repli. Le repli d'avant —
    `fnmatch(path, pattern.rstrip("/*") + "/*")` — employait `fnmatch`, dont
    le `*` traverse les `/` : `workspace/src/*/*` de `dev-backend`, censé
    désigner les fichiers à la racine du projet applicatif, devenait
    `workspace/src/*` au sens de fnmatch, c'est-à-dire TOUT `src/` — skills,
    rules, memory, shared, et les agents eux-mêmes.

    Ce que le repli voulait dire reste vrai, restreint à ce qu'il signifiait :
    un motif dont le dernier segment est LITTÉRAL nomme un répertoire (ou un
    fichier), et couvre ce qu'il contient — `workspace/pipeline/datasets` et
    `workspace/pipeline/datasets/**` désignent la même zone pour un humain. Un
    dernier segment à joker (`*`, `{n}-*.md`) ne nomme rien de tel.
    """
    normalized = normalize(path)
    key = tuple(sorted((bindings or {}).items()))
    regex = _to_regex(pattern, key, CASE_INSENSITIVE)
    if regex.match(normalized):
        return True
    last = pattern.rstrip("/").rsplit("/", 1)[-1]
    if last and not any(ch in last for ch in _GLOB_CHARS):
        return bool(_to_regex(pattern.rstrip("/") + "/**", key, CASE_INSENSITIVE).match(normalized))
    return False


# ---------------------------------------------------------------------------
# Zones protégées — écrites par des SCRIPTS, jamais par un outil d'édition
# ---------------------------------------------------------------------------
#: Trois répertoires ne sont écrits que par des scripts, en E/S Python. Un
#: `Write` sur l'un d'eux est fautif sans qu'on ait besoin de savoir QUI le
#: tente — sauf une exception, déclarée et étroite (cf. `protected_write`).
#:
#:   pipeline/baselines/  `promote_baseline.py` — retouchée à la main, la
#:                        référence rend toute non-régression tautologique ;
#:   .sys/.validation/    les rapports de gate. Ils sont du JSON en clair, non
#:                        signé, et `gate_status` ne lit que leur `ok` : un
#:                        rapport `{"ok": true}` déposé par `Write` rendait
#:                        n'importe quelle gate verte, et `.sys/` est gitignoré,
#:                        donc la contrefaçon n'atteignait jamais une revue ;
#:   .sys/.audit/         `bypasses.jsonl`, append-only « hooks framework ».
#:
#: Nommées en constantes pour que `sync_error_registry` les voie : il ne lit que
#: les littéraux (`deny(HOOK, "CLASS", …)` ou `CLS_X = "CLASS"`).
CLS_GATE_REPORT_FORGERY = "GATE_REPORT_FORGERY"
CLS_BASELINE_OWNERSHIP_VIOLATION = "BASELINE_OWNERSHIP_VIOLATION"

PROTECTED_ZONES: dict[str, tuple[str, str]] = {
    "workspace/pipeline/baselines": (
        CLS_BASELINE_OWNERSHIP_VIOLATION,
        "la baseline s'écrit par `python .sdda/sdda.py promote-baseline`, jamais par Write/Edit : "
        "déplacer la référence rend toute non-régression tautologique"),
    "workspace/.sys/.validation": (
        CLS_GATE_REPORT_FORGERY,
        "un rapport de gate est écrit par le script de la gate, jamais par Write/Edit : "
        "un `{\"ok\": true}` déposé à la main rend verte une gate que rien n'a mesurée"),
    "workspace/.sys/.audit": (
        CLS_GATE_REPORT_FORGERY,
        "le journal des bypasses est append-only et n'est écrit que par les scripts : "
        "un audit qu'on peut réécrire n'est pas un audit"),
}

#: Ce qui, dans `.sys/.validation/`, est un RAPPORT DE GATE — ce que
#: `gate_status` lit. Aucun agent ne l'écrit par un outil d'édition, même si un
#: motif de ses `writes:` le couvrait : c'est la ligne qui ne se négocie pas.
_GATE_REPORT_SUFFIXES = (".json",)


def protected_zone(path: str) -> tuple[str, str, str] | None:
    """`(zone, classe, fix)` si `path` est dans une zone protégée, sinon None."""
    rel = normalize(path)
    folded = rel.casefold() if CASE_INSENSITIVE else rel
    for zone, (cls, fix) in PROTECTED_ZONES.items():
        z = zone.casefold() if CASE_INSENSITIVE else zone
        if folded == z or folded.startswith(z + "/"):
            return zone, cls, fix
    return None


def protected_write(loader: dict[str, Any], agent: str, path: str,
                    bindings: dict[str, str] | None = None) -> tuple[str, str] | None:
    """Verdict d'une écriture en zone protégée : None si permise, sinon `(classe, fix)`.

    La règle était « personne », jouée AVANT l'identité — or `loader.yml` y
    déclare les rapports des six reviewers (`reports/*-{n}.md`,
    `adversarial-findings/{n}.jsonl`) : la phase 7 ne pouvait rien écrire. Le
    refus aveugle protégeait les gates en rendant la revue impossible.

    La règle devient étroite et nommée :

    - le fil principal (pas d'agent) reste refusé : un humain qui écrit un
      rapport de gate à la main fabrique une gate verte ;
    - un agent l'est aussi, SAUF pour un chemin qu'un motif de SES `writes:`
      couvre ET dont le préfixe littéral est DANS la zone — un motif large
      (`workspace/**`) n'ouvre pas une zone protégée par accident ;
    - un rapport de gate (`.json` sous `.sys/.validation/`) reste refusé à
      tous, déclaré ou non. Les `.md` des reviewers ne sont pas lus par
      `gate_status` ; le `.json` est ce qu'il lit.
    """
    found = protected_zone(path)
    if found is None:
        return None
    zone, cls, fix = found
    rel = normalize(path)
    if not agent or not isinstance(loader.get(agent), dict):
        return cls, fix
    if zone.endswith("/.validation") and rel.lower().endswith(_GATE_REPORT_SUFFIXES):
        return cls, fix
    for pattern in writes_of(loader, agent):
        literal = _zone_root(pattern)
        if (literal == zone or literal.startswith(zone + "/")) and matches(pattern, rel, bindings):
            if not any(matches(f, rel, bindings) for f in forbidden_of(loader, agent)):
                return None
    return cls, fix


def load_loader(root: Path) -> dict[str, Any]:
    local = root / ".sdda" / "loader.yml"
    path = local if local.is_file() else paths.FRAMEWORK_SDDA_DIR / "loader.yml"
    try:
        return yaml_mini.parse_mapping(markdown_io.read_text(path))
    except (yaml_mini.YamlMiniError, OSError):
        return {}


def writes_of(loader: dict[str, Any], agent: str) -> list[str]:
    spec = loader.get(agent)
    return [str(w) for w in (spec.get("writes") or [])] if isinstance(spec, dict) else []


def forbidden_of(loader: dict[str, Any], agent: str) -> list[str]:
    spec = loader.get(agent)
    return [str(w) for w in (spec.get("forbidden_writes") or [])] if isinstance(spec, dict) else []


#: Fichiers de secrets : aucun agent ne les lit, quel qu'il soit. Les gabarits
#: (`.env.example`) ne portent que des noms, et restent lisibles.
_ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})


def is_secret_file(path: str) -> bool:
    """`.env`, `.env.local`… : les VALEURS des secrets de l'application générée.

    La règle « aucun agent ne lit `.env` » n'existait qu'en prose : aucune
    matrice ne la portait, aucun hook ne la tenait. Elle devient nécessaire dès
    que l'humain dépose son `.env` dans `assets/`, que `architect-data` et les
    générateurs parcourent pour inférer les schémas des sources. Le harnais de
    CONSTRUCTION paie ses tokens avec son propre compte : il n'a aucune raison
    de voir la clé du RUNTIME, et `install-env` la copie sans LLM.
    """
    return _secret_name(str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1])


def _secret_name(name: str) -> bool:
    """Le NOM désigne-t-il un fichier de secrets, tel que le système de fichiers le lira ?

    La comparaison était sensible à la casse : `.ENV` passait, alors que sous
    Windows et macOS c'est le même fichier. Trois autres graphies ouvrent le
    même fichier sous Windows et sont normalisées ici : les points et espaces
    finaux (`.env.`, `.env `), et le flux de données NTFS (`.env::$DATA`,
    `.env:x`). Ce qui n'est PAS couvert, et qu'il faut dire : le nom court 8.3
    (`ENV~1`), et un lien dont le nom ne dit rien — le second est rattrapé par
    la lecture du chemin résolu (`real_relative_to_root`), le premier non.
    """
    name = name.split(":", 1)[0] if ":" in name else name   # flux NTFS
    name = name.rstrip(" .").casefold()
    if not name:
        return False
    return name == ".env" or (name.startswith(".env.") and name not in _ENV_TEMPLATES)


#: Où vivent les fichiers de secrets d'un workspace — pour savoir si une
#: recherche récursive dans un répertoire rend le contenu de l'un d'eux.
SECRET_LOCATIONS = ("workspace/assets/.env*", "workspace/src/*/.env*", ".env*")


def secret_files(root: Path) -> list[str]:
    """Les fichiers de secrets EXISTANTS aux emplacements connus (chemins relatifs)."""
    out: list[str] = []
    for pattern in SECRET_LOCATIONS:
        try:
            for p in Path(root).glob(pattern):
                if p.is_file() and is_secret_file(p.name):
                    out.append(p.relative_to(root).as_posix())
        except OSError:
            continue
    return sorted(set(out))


def secrets_under(root: Path, directory: str) -> list[str]:
    """Les fichiers de secrets qu'une lecture RÉCURSIVE de `directory` rendrait."""
    d = normalize(directory)
    fold = (lambda s: s.casefold()) if CASE_INSENSITIVE else (lambda s: s)
    out = []
    for secret in secret_files(root):
        if d == "." or fold(secret).startswith(fold(d).rstrip("/") + "/"):
            out.append(secret)
    return out


def forbidden_reads_of(loader: dict[str, Any], agent: str) -> list[str]:
    spec = loader.get(agent)
    return [str(w) for w in (spec.get("forbidden_reads") or [])] if isinstance(spec, dict) else []


def reads_of(loader: dict[str, Any], agent: str) -> list[str]:
    spec = loader.get(agent)
    return [str(w) for w in (spec.get("reads") or [])] if isinstance(spec, dict) else []


def _covers(broad: str, narrow: str) -> bool:
    """Le motif `broad` recouvre-t-il tout ce que `narrow` autorise ?

    Approximation volontairement prudente : on teste `narrow` débarrassé de ses
    jokers de fin contre `broad`. Un faux négatif laisse passer une
    contradiction (le cas d'avant), un faux positif refuserait une déclaration
    correcte — on préfère le premier, quitte à ne pas tout attraper.
    """
    probe = narrow.replace("**", "x").replace("*", "x").replace("{", "").replace("}", "")
    return matches(broad, probe)


def agent_names(loader: dict[str, Any]) -> list[str]:
    return sorted(k for k, v in loader.items() if k not in NON_AGENT_KEYS and isinstance(v, dict))


# ---------------------------------------------------------------------------
# Recouvrement RÉEL entre deux motifs
# ---------------------------------------------------------------------------
# `check_declaration` comparait des CHAÎNES : deux agents qui déclaraient
# `contracts/tools/{n}-*.tool.md` et `contracts/tools/{n}-data-*.tool.md`
# n'étaient pas « le même chemin », donc pas contestés — alors que le premier
# englobe le second, et que les deux tournent en même temps (phase 2). Idem
# `src/**/tools/**` (dev-tools) contre `src/**/data/**` (dev-data) : le
# répertoire `data/tools/` appartenait aux deux, en pleine phase 3 parallèle.
#
# Ce qui suit calcule si deux globs désignent un chemin COMMUN, et en exhibe
# des exemples : un recouvrement se juge sur un chemin qu'on peut montrer, pas
# sur une ressemblance de texte.
_SEG_ANY = "\x00any"      # un segment non vide quelconque ({n}, dernier `**`)
_DOUBLE = "**"


def _segment_alternatives(seg: str) -> list[list[tuple[str, str]]]:
    """Un segment de glob -> ses alternatives, chacune une suite d'atomes.

    Atomes : `("lit", c)`, `("any", "")` (un caractère), `("star", "")`.
    `{a,b}` démultiplie ; un placeholder `{n}` vaut « au moins un caractère ».
    """
    variants: list[list[tuple[str, str]]] = [[]]
    i = 0
    while i < len(seg):
        ch = seg[i]
        if ch == "{":
            end = seg.find("}", i)
            if end != -1:
                inner = seg[i + 1:end]
                if "," in inner:
                    variants = [v + [("lit", c) for c in alt] for v in variants for alt in inner.split(",")]
                else:
                    variants = [v + [("any", ""), ("star", "")] for v in variants]
                i = end + 1
                continue
        if ch == "*":
            variants = [v + [("star", "")] for v in variants]
        elif ch == "?":
            variants = [v + [("any", "")] for v in variants]
        else:
            variants = [v + [("lit", ch.casefold() if CASE_INSENSITIVE else ch)] for v in variants]
        i += 1
    return variants


def _segments(pattern: str) -> list[Any]:
    """Motif -> segments. Un `**` final désigne un FICHIER sous le répertoire :
    au moins un segment (le `.*` de la regex exige le `/` qui le précède)."""
    parts = normalize(pattern).split("/")
    out: list[Any] = []
    for k, part in enumerate(parts):
        if part == _DOUBLE:
            if k == len(parts) - 1:
                out.extend([_SEG_ANY, _DOUBLE])
            else:
                out.append(_DOUBLE)
        else:
            out.append(part)
    return out


def _atoms_witness(a: list[tuple[str, str]], b: list[tuple[str, str]]) -> str | None:
    """Une chaîne reconnue par les deux suites d'atomes, ou None (DP mémoïsé)."""
    memo: dict[tuple[int, int], str | None] = {}

    def f(i: int, j: int) -> str | None:
        key = (i, j)
        if key in memo:
            return memo[key]
        memo[key] = None  # coupe les cycles star/star
        res: str | None = None
        if i == len(a) and j == len(b):
            res = ""
        if res is None and i < len(a) and a[i][0] == "star":
            res = f(i + 1, j)
            if res is None and j < len(b) and b[j][0] != "star":
                tail = f(i, j + 1)
                if tail is not None:
                    res = (b[j][1] if b[j][0] == "lit" else "x") + tail
        if res is None and j < len(b) and b[j][0] == "star":
            res = f(i, j + 1)
            if res is None and i < len(a) and a[i][0] != "star":
                tail = f(i + 1, j)
                if tail is not None:
                    res = (a[i][1] if a[i][0] == "lit" else "x") + tail
        if res is None and i < len(a) and j < len(b) and a[i][0] != "star" and b[j][0] != "star":
            ca = a[i][1] if a[i][0] == "lit" else None
            cb = b[j][1] if b[j][0] == "lit" else None
            if ca is None or cb is None or ca == cb:
                tail = f(i + 1, j + 1)
                if tail is not None:
                    res = (ca or cb or "x") + tail
        memo[key] = res
        return res

    return f(0, 0)


def _segment_witness(sa: str, sb: str) -> str | None:
    """Un nom de segment reconnu par les deux segments de glob, ou None."""
    alts_a = [[("any", ""), ("star", "")]] if sa == _SEG_ANY else _segment_alternatives(sa)
    alts_b = [[("any", ""), ("star", "")]] if sb == _SEG_ANY else _segment_alternatives(sb)
    for x in alts_a:
        for y in alts_b:
            w = _atoms_witness(x, y)
            if w:
                return w
    return None


def _sample(seg: str) -> str:
    """Un nom concret pour un segment seul (absorbé par le `**` d'en face)."""
    return _segment_witness(seg, _SEG_ANY) or "x"


def overlap_witnesses(a: str, b: str, limit: int = 64) -> list[str]:
    """Des chemins que les DEUX motifs désignent — vide s'ils sont disjoints.

    Chaque branche où un `**` absorbe les segments d'en face donne une forme
    différente (`src/data/tools/x` ET `src/tools/data/x`) : c'est ce qui permet
    à l'appelant d'écarter les formes qu'un `forbidden_writes` retire, et de ne
    signaler que celles qui restent réellement à deux propriétaires.
    """
    A, B = _segments(a), _segments(b)
    out: list[str] = []
    seen: set[str] = set()

    def walk(i: int, j: int, acc: tuple[str, ...], depth: int) -> None:
        if len(out) >= limit or depth > len(A) + len(B) + 4:
            return
        if i == len(A) and j == len(B):
            path = "/".join(acc)
            if path not in seen:
                seen.add(path)
                out.append(path)
            return
        if i < len(A) and A[i] == _DOUBLE:
            walk(i + 1, j, acc, depth + 1)
            if j < len(B) and B[j] != _DOUBLE:
                walk(i, j + 1, acc + (_sample(B[j]),), depth + 1)
        if j < len(B) and B[j] == _DOUBLE:
            walk(i, j + 1, acc, depth + 1)
            if i < len(A) and A[i] != _DOUBLE:
                walk(i + 1, j, acc + (_sample(A[i]),), depth + 1)
        if i < len(A) and j < len(B) and A[i] != _DOUBLE and B[j] != _DOUBLE:
            name = _segment_witness(A[i], B[j])
            if name:
                walk(i + 1, j + 1, acc + (name,), depth + 1)

    walk(0, 0, (), 0)
    # Filet : un exemple doit être reconnu par la regex elle-même des deux côtés.
    return [w for w in out if matches(a, w) and matches(b, w)]


def overlaps(a: str, b: str) -> bool:
    return bool(overlap_witnesses(a, b, limit=1))


# ---------------------------------------------------------------------------
# 1. La déclaration se tient-elle debout ?
# ---------------------------------------------------------------------------
#: Modes de partage admis. Un mode inconnu est refusé : « partagé » sans dire
#: pourquoi il n'y a pas de course est une autorisation sans raison.
SHARED_MODES = frozenset({
    "disjoint-by-timestamp", "disjoint-by-object", "disjoint-by-agent",
    # `disjoint-by-layer` : chaque agent écrit sous SA couche d'un arbre commun
    # (`src/tools/tests/`, `src/retrieval/tests/`…). Distinct de
    # `disjoint-by-agent`, où c'est l'identité de l'agent qui nomme le chemin :
    # ici c'est la couche, et un agent peut en servir plusieurs.
    "disjoint-by-layer",
    "append-only", "serialized", "exclusive-pipeline",
    # `exclusive-by-profile` : les deux agents ne tournent jamais dans le même
    # profil (`dev-app` sous `Profile: poc`, les `dev-*` de couche sinon) — la
    # commande choisit un chemin ou l'autre, jamais les deux.
    "exclusive-by-profile",
})


def shared_writes(loader: dict[str, Any], report: Report) -> dict[str, dict[str, Any]]:
    """`shared_writes:` -> {chemin: entrée}. Une entrée mal formée ne protège rien."""
    out: dict[str, dict[str, Any]] = {}
    for entry in loader.get("shared_writes") or []:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        mode = str(entry.get("mode") or "").strip()
        if not path:
            continue
        if mode not in SHARED_MODES:
            report.error(
                "OWNERSHIP_SHARE_MODE_UNKNOWN",
                f"`{path}` : mode de partage `{mode or '<absent>'}` inconnu",
                fix=f"choisir parmi {sorted(SHARED_MODES)} — le mode dit POURQUOI il n'y a pas de "
                    "course ; sans lui, le partage est une autorisation sans raison",
                location=".sdda/loader.yml",
            )
            continue
        if not str(entry.get("why") or "").strip():
            report.warn("OWNERSHIP_SHARE_UNJUSTIFIED", f"`{path}` : partage déclaré sans `why`",
                        fix="écrire ce qui garantit l'absence de course", location=".sdda/loader.yml")
        out[path] = entry
    return out


def check_declaration(loader: dict[str, Any], report: Report) -> dict[str, Any]:
    agents = agent_names(loader)
    if not agents:
        report.error("OWNERSHIP_MATRIX_MISSING", "loader.yml ne déclare aucun agent",
                     fix="restaurer .sdda/loader.yml")
        return {}

    without: list[str] = []
    claims: dict[str, list[str]] = {}
    self_locked: list[str] = []
    for agent in agents:
        writes = writes_of(loader, agent)
        if not writes:
            without.append(agent)
        for pattern in writes:
            claims.setdefault(pattern, []).append(agent)

        # Un `forbidden_writes` qui recouvre entièrement un `writes:` du MÊME
        # agent est une contradiction, et elle est silencieuse : les interdits
        # sont testés en premier, donc l'agent est refusé dans sa propre zone
        # sans que rien ne le signale à la lecture.
        #
        # Le défaut s'est produit deux fois, et chaque fois sur une zone
        # centrale : `dev-agent` avec `src/**/agents/{other}/**` ({other} se
        # compile comme {agent}, donc la phase 4 était impossible) et
        # `qa-tests` avec `src/**` « sauf tests/ » — que le langage de motifs
        # ne sait pas exprimer, si bien qu'aucun test n'a jamais pu être écrit.
        # Un commentaire n'est pas un mécanisme.
        for forbidden in forbidden_of(loader, agent):
            swallowed = [w for w in writes if _covers(forbidden, w)]
            if swallowed:
                self_locked.append(agent)
                report.error(
                    "OWNERSHIP_SELF_LOCKED",
                    f"`{agent}` : `forbidden_writes: {forbidden}` recouvre ses propres "
                    f"`writes:` {swallowed} — il est refusé dans sa propre zone",
                    fix="`writes:` EST l'allowlist : tout chemin absent est déjà refusé. "
                        "Retirer l'interdit trop large, ou le restreindre à ce qui n'est pas "
                        "déjà couvert. Un interdit qui avale son autorisation ne protège rien "
                        "et bloque tout",
                    location=".sdda/loader.yml",
                )

    shared = shared_writes(loader, report)
    contested: dict[str, list[str]] = {}
    for pattern, owners in sorted(p_o for p_o in claims.items() if len(p_o[1]) > 1):
        entry = shared.get(pattern)
        if entry is None:
            contested[pattern] = owners
            report.error(
                "OWNERSHIP_ZONE_CONTESTED",
                f"`{pattern}` est déclaré en écriture par {owners}, sans mode de partage",
                fix="un chemin, un propriétaire. Si le partage est voulu, le DÉCLARER dans "
                    "`shared_writes:` avec son mode et sa raison — sinon le parallélisme produit "
                    "une course que personne ne verra passer",
                location=".sdda/loader.yml",
            )
            continue
        declared = sorted(str(a) for a in (entry.get("agents") or []))
        if declared and declared != sorted(owners):
            report.error(
                "OWNERSHIP_ZONE_CONTESTED",
                f"`{pattern}` : `shared_writes` annonce {declared}, loader.yml donne {sorted(owners)}",
                fix="aligner les deux — une liste de partage périmée autorise un agent que personne "
                    "n'a voulu autoriser",
                location=".sdda/loader.yml",
            )

    overlapping = real_overlaps(loader, shared)
    for item in overlapping:
        report.error(
            "OWNERSHIP_ZONE_CONTESTED",
            f"`{item['a']}` ({item['patternA']}) et `{item['b']}` ({item['patternB']}) désignent "
            f"tous deux `{item['example']}`",
            fix="deux motifs différents peuvent désigner le même fichier : retirer la zone commune "
                "chez l'un des deux (`forbidden_writes:`), ancrer le motif, ou DÉCLARER le partage "
                "dans `shared_writes:` avec son mode — un recouvrement non déclaré est une course "
                "en pleine phase parallèle",
            location=".sdda/loader.yml",
        )

    # Un agent qui n'écrit nulle part est un reviewer : c'est légitime, et c'est
    # une information — pas un défaut.
    return {"agents": len(agents), "readOnlyAgents": without,
            "contested": sorted(contested), "sharedZones": sorted(shared),
            "overlaps": overlapping}


def _forbidden_for(loader: dict[str, Any], agent: str, path: str) -> bool:
    return any(matches(f, path) for f in forbidden_of(loader, agent))


def _shared_excuses(shared: dict[str, dict[str, Any]], a: str, b: str, path: str) -> bool:
    """Le chemin commun est-il dans une zone partagée DÉCLARÉE pour ces deux agents ?"""
    for zone, entry in shared.items():
        declared = {str(x) for x in (entry.get("agents") or [])}
        if {a, b} <= declared and matches(zone, path):
            return True
    return False


def real_overlaps(loader: dict[str, Any], shared: dict[str, dict[str, Any]] | None = None) -> list[dict[str, str]]:
    """Les recouvrements RÉELS non déclarés entre les `writes:` de deux agents.

    Global, et non par vague : l'ordre des phases vit dans les commandes, pas
    dans `loader.yml`, et une sérialisation qu'on ne peut pas lire ici ne peut
    pas servir d'excuse ici. Un partage voulu (phases sérialisées, couches
    disjointes) se DÉCLARE dans `shared_writes:` — avec son mode, qui dit
    pourquoi il n'y a pas de course.

    Un chemin commun que l'un des deux s'interdit (`forbidden_writes:`) n'est
    pas contesté : il n'a qu'un propriétaire. Les exemples viennent de
    `overlap_witnesses`, donc un signalement montre toujours un chemin réel.
    """
    if shared is None:
        shared = shared_writes(loader, Report(name="tmp", target="."))
    agents = agent_names(loader)
    found: list[dict[str, str]] = []
    for x, a in enumerate(agents):
        for b in agents[x + 1:]:
            for pa in writes_of(loader, a):
                for pb in writes_of(loader, b):
                    if pa == pb:
                        continue  # même chaîne : jugée par le contrôle des revendications
                    for w in overlap_witnesses(pa, pb):
                        if _forbidden_for(loader, a, w) or _forbidden_for(loader, b, w):
                            continue
                        if _shared_excuses(shared, a, b, w):
                            continue
                        found.append({"a": a, "b": b, "patternA": pa, "patternB": pb, "example": w})
                        break
    return found


# ---------------------------------------------------------------------------
# 2. Une écriture donnée est-elle autorisée ?
# ---------------------------------------------------------------------------
def check_write(loader: dict[str, Any], agent: str, path: str, report: Report,
                bindings: dict[str, str] | None = None) -> bool:
    """L'écriture de `path` par `agent` est-elle dans sa zone ?

    `bindings` lie les placeholders d'INSTANCE (`{agent}` de `dev-agent`) à la
    valeur de l'instance qui écrit : sans elle, `agents/{agent}/**` autorise
    le répertoire de n'importe quelle autre instance.
    """
    if not isinstance(loader.get(agent), dict):
        report.error("OWNERSHIP_AGENT_UNKNOWN", f"agent `{agent}` absent de loader.yml",
                     fix=f"agents déclarés : {', '.join(agent_names(loader))}")
        return False

    for pattern in forbidden_of(loader, agent):
        if matches(pattern, path, bindings):
            cls, why = _sacred_class(path)
            report.error(cls or "OWNERSHIP_VIOLATION",
                         f"`{agent}` a écrit `{path}` — interdit explicitement",
                         fix=why or f"`forbidden_writes:` de `{agent}` couvre `{pattern}`",
                         location=".sdda/loader.yml")
            return False

    allowed = writes_of(loader, agent)
    if any(matches(pattern, path, bindings) for pattern in allowed):
        return True

    cls, why = _sacred_class(path)
    if cls and not any(p.startswith(path.rsplit("/", 1)[0][:len(p)]) for p in allowed if p.startswith("workspace/")):
        report.error(cls, f"`{agent}` a écrit `{path}`", fix=why, location=".sdda/loader.yml")
        return False

    report.error(
        "OWNERSHIP_VIOLATION",
        f"`{agent}` a écrit `{path}`, hors de ses `writes:`",
        fix=f"zones autorisées : {allowed or 'aucune'}. Élargir la matrice si c'est légitime — "
            "mais explicitement, parce que c'est elle qui rend le parallélisme sûr",
        location=".sdda/loader.yml",
    )
    return False


#: Au-delà, un répertoire est jugé sur ce qu'on en a vu : une commande
#: récursive sur un arbre de cette taille n'est de toute façon pas un geste de
#: `dev-*`, et le hook doit répondre vite.
_RECURSIVE_WALK_LIMIT = 20000


def check_recursive_write(loader: dict[str, Any], agent: str, path: str, report: Report,
                          bindings: dict[str, str] | None = None, root: Path | None = None) -> bool:
    """Une écriture qui porte sur un RÉPERTOIRE et tout ce qu'il contient.

    `rm -rf workspace/src/App/agents` par `dev-backend` : le chemin lui-même
    matche `workspace/src/*/*`, donc `check_write` l'autorisait — et la
    commande effaçait le code de `dev-agent`. Un répertoire détruit, déplacé ou
    réécrit est autorisé seulement s'il est dans la zone de l'agent ET que
    chaque fichier EXISTANT dessous l'est aussi : c'est ce que la commande
    touchera, ni plus ni moins.
    """
    rel = normalize(path)
    if rel == ".":
        report.error("OWNERSHIP_VIOLATION", f"`{agent}` réécrit tout le projet (`{path}`)",
                     fix="une commande qui porte sur tout l'arbre (`git reset --hard`, `git stash`, "
                         "`rm -rf .`) touche la zone de chaque agent : la limiter à SA zone",
                     location=".sdda/loader.yml")
        return False
    probe = Report(name="probe", target=".")
    if not (check_write(loader, agent, rel, probe, bindings)
            or check_write(loader, agent, rel + "/x", Report(name="probe", target="."), bindings)):
        return check_write(loader, agent, rel, report, bindings)
    base = Path(root) / rel if root is not None else None
    if base is None or not base.is_dir():
        return True
    seen = 0
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            seen += 1
            if seen > _RECURSIVE_WALK_LIMIT:
                return True
            child = (Path(dirpath) / name).relative_to(root).as_posix()  # type: ignore[arg-type]
            sub = Report(name="probe", target=".")
            if not check_write(loader, agent, child, sub, bindings):
                first = sub.errors[0] if sub.errors else None
                report.error(first.cls if first else "OWNERSHIP_VIOLATION",
                             f"`{agent}` touche `{rel}` récursivement, et `{child}` dessous n'est pas à lui",
                             fix=(first.fix if first and first.fix else "")
                                 or "viser les fichiers de SA zone, pas le répertoire qui contient ceux des autres",
                             location=".sdda/loader.yml")
                return False
    return True


# ---------------------------------------------------------------------------
# 3. Une lecture donnée est-elle autorisée ?
# ---------------------------------------------------------------------------
#: Ce qu'un outil de lecture rend, et donc ce qu'un interdit doit couvrir.
def _zone_root(pattern: str) -> str:
    """Le préfixe littéral d'un motif : `workspace/src/**` -> `workspace/src`."""
    out: list[str] = []
    for seg in pattern.replace("\\", "/").split("/"):
        if any(ch in seg for ch in "*?{["):
            break
        out.append(seg)
    return "/".join(out)


def read_violation(loader: dict[str, Any], agent: str, path: str, *, scope: str = "file") -> str | None:
    """Le motif `forbidden_reads` que cette lecture viole, ou None.

    Trois cas selon ce que l'outil RENVOIE :

    - `file`  (Read) : le chemin lui-même matche un interdit ;
    - `names` (Glob) : la racine de recherche est À L'INTÉRIEUR d'une zone
      interdite — chercher depuis la zone, c'est lister ce qu'elle contient ;
    - `content` (Grep) : comme `names`, PLUS la racine est un ANCÊTRE d'une
      zone interdite — un grep sur `workspace/` rend le contenu de
      `workspace/stack/STACK.md` à qui n'a pas le droit de le lire. Le hook
      refuse et dit où restreindre `path` : un agent qui grep tout le
      workspace cherche en réalité ce que `forbidden_reads` lui cache.
    """
    normalized = normalize(path)
    allowed = reads_of(loader, agent)
    for pattern in forbidden_reads_of(loader, agent):
        # `{other}` veut dire « tout AUTRE que le mien ». Le compilateur de motifs
        # ne connaît pas le `{n}` courant — il traduit `{other}` comme `{n}`, en
        # `[^/]+` — donc `missions/{other}-*.md` matchait aussi la MISSION que
        # `reads:` autorise nommément, et l'interdit, testé en premier, gagnait :
        # `po-capabilities` ne pouvait pas lire la MISSION qu'il découpe. « Le
        # mien » est ce que `reads:` déclare : un chemin qu'un `reads:` couvre
        # n'est pas « autre ». Sans `{other}`, l'interdit reste absolu.
        if "{other}" in pattern and any(matches(r, normalized) for r in allowed):
            continue
        if matches(pattern, normalized):
            return pattern
        if scope in ("names", "content") and matches(pattern, normalized + "/x"):
            return pattern
        if scope == "content":
            zone = _zone_root(pattern)
            if zone and (normalized == "." or zone.startswith(normalized + "/")):
                return pattern
    return None


def check_read(loader: dict[str, Any], agent: str, path: str, report: Report, *, scope: str = "file") -> bool:
    """Une lecture hors `forbidden_reads` ? Sinon `[OWNERSHIP_READ_FORBIDDEN]`.

    `forbidden_reads` n'est pas une politique de confidentialité : c'est ce qui
    empêche un agent de décider à partir d'une information qui ne le regarde
    pas — `po-elicitor` qui lit `STACK.md` écrit une MISSION teintée de choix
    techniques, et personne ne voit d'où vient la teinte.
    """
    if not isinstance(loader.get(agent), dict):
        report.error("OWNERSHIP_AGENT_UNKNOWN", f"agent `{agent}` absent de loader.yml",
                     fix=f"agents déclarés : {', '.join(agent_names(loader))}")
        return False
    pattern = read_violation(loader, agent, path, scope=scope)
    if pattern is None:
        return True
    how = {"file": "a lu", "names": "a listé", "content": "a cherché dans"}[scope]
    allowed = reads_of(loader, agent)
    report.error(
        "OWNERSHIP_READ_FORBIDDEN",
        f"`{agent}` {how} `{path}` — couvert par `forbidden_reads: {pattern}`",
        fix=("ce que l'agent doit savoir de cette zone lui est INJECTÉ par le brief "
             "(spawn_brief.py, faits injectés), il ne le lit pas. "
             + (f"Restreindre la recherche à ses `reads:` : {allowed}" if scope == "content"
                else f"Zones lisibles : {allowed or 'aucune déclarée'}")),
        location=".sdda/loader.yml",
    )
    return False


# ---------------------------------------------------------------------------
# 4. Ce qui a RÉELLEMENT été écrit pendant une phase — instantané, puis écart
# ---------------------------------------------------------------------------
# `/sdda-build` appelait `audit-ownership --phase 4` sans `--agent` ni
# `--wrote` : seule la cohérence de loader.yml était vérifiée, jamais une
# écriture. Et la « révocation du fichier écrit (restauré depuis le hash
# précédent) » que la commande promettait n'avait aucun hash précédent où
# puiser. Ce qui suit la rend réelle : un instantané AVANT la phase (empreinte
# de chaque fichier, et copie de ceux qu'on pourra devoir restaurer), puis le
# calcul de ce qui a été créé, modifié, supprimé — attribué aux zones des
# agents de la phase. C'est le filet de tout ce que les hooks ne voient pas :
# un script qui écrit de l'intérieur, un `agent_id` absent du payload, deux
# instances qui s'échangent leurs répertoires.

#: Les agents qui écrivent pendant chaque phase de `/sdda-build` (et des
#: phases voisines), pour ne pas avoir à les lister à chaque appel.
PHASE_AGENTS: dict[str, tuple[str, ...]] = {
    "2": ("architect-topology", "architect-rag", "architect-data", "architect-memory", "architect-tools"),
    "3.0": ("dev-backend",),
    "3": ("dev-tools", "dev-retrieval", "dev-data"),
    "4.0": ("dev-orchestration",),
    "4.1": ("dev-prompt",),
    "4": ("dev-agent",),
    "5": ("dev-orchestration", "dev-api", "dev-backend"),
    "6": ("qa-evals", "qa-tests"),
    "7": ("review-spec", "review-safety", "review-cost", "review-orchestration", "review-rag",
          "review-adversarial"),
}

#: Hors instantané : l'état interne (écrit par les scripts que les agents
#: lancent — gates, traces, packs), et les caches d'outillage régénérables. Les
#: zones protégées de `.sys/` sont tenues par les hooks ; ce qu'un script y
#: écrit pendant la phase est, par construction, le fait des scripts.
_SNAPSHOT_SKIP_TOP = ("workspace/.sys",)
_SNAPSHOT_NOISE = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules",
                             ".venv", "venv", ".gradle", "bin", "obj", "build", "dist", ".git", ".idea"})
#: Copie gardée pour restauration : tout fichier sous ce seuil, hors données
#: déposées par l'humain (`assets/`, que personne ne restaure depuis une copie).
_BLOB_MAX_BYTES = 2_000_000
CLS_FROZEN_ZONE_CHANGED = "OWNERSHIP_FROZEN_ZONE_CHANGED"


def snapshot_dir(root: Path, mission: str | None, phase: str) -> Path:
    return paths.workspace(root) / ".sys" / ".state" / "ownership-snapshots" / f"{mission or 'x'}-phase-{phase}"


def _walk_workspace(root: Path):
    base = paths.workspace(root)
    if not base.is_dir():
        return
    for dirpath, dirs, files in os.walk(base):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if any(rel_dir == s or rel_dir.startswith(s + "/") for s in _SNAPSHOT_SKIP_TOP):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in _SNAPSHOT_NOISE and not d.endswith(".egg-info")]
        for name in files:
            yield Path(dirpath) / name


def _sha(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


#: Un instantané qui n'a pas pu être posé. Littéral ici pour le registre.
CLS_SNAPSHOT_FAILED = "OWNERSHIP_SNAPSHOT_FAILED"


class SnapshotError(OSError):
    """L'instantané précédent n'a pas pu être remplacé : la phase ne doit PAS s'ouvrir."""


def _replace_snapshot(tmp: Path, target: Path) -> None:
    """`tmp` devient `target`, ou lève `SnapshotError` — jamais un instantané à moitié.

    Sous Windows, un fichier de l'ancien instantané tenu ouvert (un éditeur,
    un antivirus, un `Read` d'agent) survit à `rmtree(ignore_errors=True)`, et
    `os.replace` échoue alors sur un répertoire non vide. L'exception remontait
    nue : la commande s'arrêtait sans classe, ou pire, la phase s'ouvrait
    sans instantané — et `--since-snapshot` n'avait plus rien pour juger les
    écritures réelles. On réessaie une fois (le verrou d'un scanner dure
    rarement plus d'un instant), puis on refuse, en le disant.
    """
    import shutil
    import time

    shutil.rmtree(target, ignore_errors=True)
    if target.exists():
        time.sleep(0.25)
        shutil.rmtree(target, ignore_errors=True)
    if target.exists():
        shutil.rmtree(tmp, ignore_errors=True)
        raise SnapshotError(f"l'instantané précédent `{target}` ne peut pas être supprimé (fichier tenu ouvert ?)")
    try:
        os.replace(tmp, target)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SnapshotError(f"impossible de poser l'instantané `{target}` : {exc.__class__.__name__}: {exc}") from exc


def take_snapshot(root: Path, mission: str | None, phase: str) -> dict[str, Any]:
    """Empreinte de chaque fichier du workspace (hors `.sys/`), et copie des
    fichiers restaurables. Écrit atomiquement ; rend le manifeste, ou lève
    `SnapshotError` si l'instantané précédent ne peut pas être remplacé."""
    import json
    import shutil

    target = snapshot_dir(root, mission, phase)
    tmp = target.with_name(target.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "blobs").mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, Any]] = {}
    for path in _walk_workspace(root):
        rel = path.relative_to(root).as_posix()
        try:
            digest = _sha(path)
            size = path.stat().st_size
        except OSError:
            continue
        entry: dict[str, Any] = {"sha256": digest, "bytes": size}
        restorable = size <= _BLOB_MAX_BYTES and not rel.startswith("workspace/assets/") and not is_secret_file(rel)
        if restorable:
            blob = tmp / "blobs" / digest
            if not blob.exists():
                shutil.copyfile(path, blob)
            entry["blob"] = True
        files[rel] = entry
    manifest = {"mission": mission, "phase": phase, "files": files}
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    _replace_snapshot(tmp, target)
    return manifest


def load_snapshot(root: Path, mission: str | None, phase: str) -> dict[str, Any] | None:
    import json

    path = snapshot_dir(root, mission, phase) / "manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def changes_since(root: Path, manifest: dict[str, Any]) -> dict[str, list[str]]:
    before = manifest.get("files") or {}
    now: dict[str, str] = {}
    for path in _walk_workspace(root):
        try:
            now[path.relative_to(root).as_posix()] = _sha(path)
        except OSError:
            continue
    created = sorted(p for p in now if p not in before)
    modified = sorted(p for p in now if p in before and before[p].get("sha256") != now[p])
    deleted = sorted(p for p in before if p not in now)
    return {"created": created, "modified": modified, "deleted": deleted}


def _owners(loader: dict[str, Any], agents: tuple[str, ...], path: str,
            instances: list[str] | None) -> tuple[list[str], str]:
    """Les agents de la phase dont la zone couvre `path` — et, pour un agent à
    instances, `""` si l'instance est déclarée, sinon le nom de l'instance fautive."""
    owners: list[str] = []
    escaped = ""
    for agent in agents:
        spec = loader.get(agent)
        placeholder = str(spec.get("instance_placeholder") or "") if isinstance(spec, dict) else ""
        probe = Report(name="probe", target=".")
        if not check_write(loader, agent, path, probe):
            continue
        if placeholder and instances is not None:
            if any(check_write(loader, agent, path, Report(name="probe", target="."), {placeholder: i})
                   for i in instances):
                owners.append(agent)
            else:
                escaped = path
            continue
        owners.append(agent)
    return owners, escaped


def check_since_snapshot(root: Path, loader: dict[str, Any], report: Report, *, mission: str | None,
                         phase: str, agents: tuple[str, ...], instances: list[str] | None = None,
                         frozen: list[str] | None = None, restore: bool = False) -> dict[str, Any]:
    """Chaque fichier créé, modifié ou supprimé depuis l'instantané est-il dans
    la zone d'un agent de la phase — et, par instance, sous SON répertoire ?"""
    import shutil

    manifest = load_snapshot(root, mission, phase)
    if manifest is None:
        report.error("OWNERSHIP_SNAPSHOT_MISSING",
                     f"aucun instantané pour la MISSION {mission or '?'} phase {phase}",
                     fix=f"`python .sdda/sdda.py audit-ownership snapshot --mission {mission or '{n}'} "
                         f"--phase {phase}` AVANT la vague — sans lui, aucune écriture réelle n'est vérifiable")
        return {}
    changes = changes_since(root, manifest)
    violations: list[dict[str, str]] = []
    for kind in ("created", "modified", "deleted"):
        for path in changes[kind]:
            if frozen and any(matches(f, path) for f in frozen):
                violations.append({"path": path, "kind": kind, "class": CLS_FROZEN_ZONE_CHANGED})
                report.error(CLS_FROZEN_ZONE_CHANGED, f"`{path}` {kind} alors que sa zone est GELÉE pour la phase {phase}",
                             fix="un type partagé ou une interface gelée par la pré-passe ne change pas pendant "
                                 "la phase qui en dépend : relancer la pré-passe, puis la phase",
                             location=path)
                continue
            owners, escaped = _owners(loader, agents, path, instances)
            if owners:
                continue
            if escaped:
                cls, why = "OWNERSHIP_INSTANCE_ESCAPE", (
                    f"`{path}` est sous le répertoire d'une instance que la vague n'a pas déclarée "
                    f"({', '.join(instances or []) or 'aucune'})")
            else:
                cls, why = _sacred_class(path)
                cls = cls or "OWNERSHIP_VIOLATION"
                why = why or f"hors de la zone de {', '.join(agents)}"
            violations.append({"path": path, "kind": kind, "class": cls})
            report.error(cls, f"phase {phase} : `{path}` {kind} — {why}",
                         fix="révoquer (`--restore`) puis relancer l'agent fautif ; si l'écriture est "
                             "légitime, c'est la matrice (`loader.yml`) qui est fausse, pas l'audit",
                         location=path)

    restored: list[str] = []
    if restore and violations:
        blobs = snapshot_dir(root, mission, phase) / "blobs"
        before = manifest.get("files") or {}
        for v in violations:
            path, kind = v["path"], v["kind"]
            target = root / path
            if kind == "created":
                try:
                    target.unlink()
                    restored.append(path)
                except OSError:
                    pass
                continue
            entry = before.get(path) or {}
            blob = blobs / str(entry.get("sha256", ""))
            if entry.get("blob") and blob.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(blob, target)
                restored.append(path)
            else:
                report.warn("OWNERSHIP_RESTORE_IMPOSSIBLE", f"`{path}` : aucune copie dans l'instantané",
                            fix="fichier trop gros ou sous assets/ : le restaurer à la main", location=path)
    return {"changes": changes, "violations": violations, "restored": restored,
            "agents": list(agents), "instances": instances}


def run(root: Path, *, agent: str | None = None, wrote: list[str] | None = None,
        declared_only: bool = False, since_snapshot: bool = False, mission: str | None = None,
        phase: str | None = None, agents: list[str] | None = None, instances: list[str] | None = None,
        frozen: list[str] | None = None, restore: bool = False) -> Report:
    report = Report(name="OWNERSHIP", target=str(root))
    loader = load_loader(root)

    report.data.update(check_declaration(loader, report))
    if declared_only:
        return report

    if since_snapshot:
        if not phase:
            report.error("INVALID_ARG", "`--since-snapshot` exige `--phase`",
                         fix="la phase nomme l'instantané ET les agents dont les zones sont jugées")
            return report
        chosen = tuple(agents or ([agent] if agent else PHASE_AGENTS.get(str(phase), ())))
        if not chosen:
            report.error("INVALID_ARG", f"phase `{phase}` : aucun agent connu",
                         fix=f"passer `--agents` ; phases connues : {sorted(PHASE_AGENTS)}")
            return report
        report.data["sinceSnapshot"] = check_since_snapshot(
            root, loader, report, mission=mission, phase=str(phase), agents=chosen,
            instances=instances, frozen=frozen, restore=restore)
        return report

    if agent and wrote:
        verdict = {p: check_write(loader, agent, p, report) for p in wrote}
        report.data["checked"] = verdict
    elif agent or wrote:
        report.error("INVALID_ARG", "`--agent` et `--wrote` vont ensemble",
                     fix="passer les deux, `--since-snapshot`, ou `--declared-only`")
    return report


def _csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Matrice d'écriture : qui a le droit d'écrire quoi (0 token)")
    p.add_argument("action", nargs="?", choices=("check", "snapshot"), default="check",
                   help="`snapshot` : instantané AVANT une phase ; `check` (défaut) : l'audit")
    p.add_argument("--mission", default=None, help="numéro de mission (contexte du rapport de gate)")
    p.add_argument("--phase", default=None, help="phase du pipeline (nomme l'instantané et ses agents)")
    p.add_argument("--agent", default=None, help="agent dont on vérifie les écritures")
    p.add_argument("--wrote", nargs="*", default=None, help="chemins écrits, relatifs à la racine")
    p.add_argument("--declared-only", action="store_true", help="ne vérifier que la cohérence de loader.yml")
    p.add_argument("--since-snapshot", action="store_true",
                   help="juger les fichiers RÉELLEMENT créés/modifiés/supprimés depuis l'instantané de la phase")
    p.add_argument("--agents", default=None, help="agents de la phase, séparés par des virgules (défaut : table)")
    p.add_argument("--instances", default=None,
                   help="instances déclarées de la vague (`dev-agent`), séparées par des virgules")
    p.add_argument("--frozen", action="append", default=None,
                   help="motif d'une zone GELÉE pendant la phase (répétable)")
    p.add_argument("--restore", action="store_true",
                   help="révoquer les écritures fautives depuis l'instantané (restaure, ou supprime une création)")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    if args.action == "snapshot":
        report = Report(name="OWNERSHIP-SNAPSHOT", target=str(root))
        if not args.phase:
            report.error("INVALID_ARG", "`snapshot` exige `--phase`", fix="nommer la phase qui va s'ouvrir")
            return finish(report, args)
        try:
            manifest = take_snapshot(root, args.mission, str(args.phase))
        except SnapshotError as exc:
            report.error(CLS_SNAPSHOT_FAILED, str(exc),
                         fix="fermer ce qui tient l'ancien instantané ouvert (éditeur, scanner), ou le supprimer à la main, "
                             "puis relancer `snapshot` AVANT la vague : une phase ouverte sans instantané n'est pas auditable",
                         location=paths.rel(root, snapshot_dir(root, args.mission, str(args.phase))))
            return finish(report, args)
        report.data.update({"files": len(manifest["files"]),
                            "path": paths.rel(root, snapshot_dir(root, args.mission, str(args.phase)))})
        return finish(report, args)
    report = run(root, agent=args.agent, wrote=args.wrote, declared_only=args.declared_only,
                 since_snapshot=args.since_snapshot, mission=args.mission, phase=args.phase,
                 agents=_csv(args.agents), instances=_csv(args.instances), frozen=args.frozen,
                 restore=args.restore)

    if not args.no_report:
        try:
            write_gate_report(root, "G5", args.mission or "stack", report, pinned={}, part="ownership")
        except OSError:
            pass
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
