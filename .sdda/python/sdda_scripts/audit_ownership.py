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

Usage :
    python .sdda/sdda.py audit-ownership --mission 1 --phase 4
    python .sdda/sdda.py audit-ownership --declared-only --json   # cohérence de loader.yml seule
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
    name = str(path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return name == ".env" or (name.startswith(".env.") and name not in _ENV_TEMPLATES)


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

    # Un agent qui n'écrit nulle part est un reviewer : c'est légitime, et c'est
    # une information — pas un défaut.
    return {"agents": len(agents), "readOnlyAgents": without,
            "contested": sorted(contested), "sharedZones": sorted(shared)}


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


def run(root: Path, *, agent: str | None = None, wrote: list[str] | None = None,
        declared_only: bool = False) -> Report:
    report = Report(name="OWNERSHIP", target=str(root))
    loader = load_loader(root)

    report.data.update(check_declaration(loader, report))
    if declared_only:
        return report

    if agent and wrote:
        verdict = {p: check_write(loader, agent, p, report) for p in wrote}
        report.data["checked"] = verdict
    elif agent or wrote:
        report.error("INVALID_ARG", "`--agent` et `--wrote` vont ensemble",
                     fix="passer les deux, ou `--declared-only`")
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Matrice d'écriture : qui a le droit d'écrire quoi (0 token)")
    p.add_argument("--mission", default=None, help="numéro de mission (contexte du rapport de gate)")
    p.add_argument("--phase", default=None, help="phase du pipeline (contexte du rapport de gate)")
    p.add_argument("--agent", default=None, help="agent dont on vérifie les écritures")
    p.add_argument("--wrote", nargs="*", default=None, help="chemins écrits, relatifs à la racine")
    p.add_argument("--declared-only", action="store_true", help="ne vérifier que la cohérence de loader.yml")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = run(root, agent=args.agent, wrote=args.wrote, declared_only=args.declared_only)

    if not args.no_report:
        try:
            write_gate_report(root, "G5", args.mission or "stack", report, pinned={}, part="ownership")
        except OSError:
            pass
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
