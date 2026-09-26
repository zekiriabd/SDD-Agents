#!/usr/bin/env python3
"""Enforcer de l'invariant `prompts-are-files` (P1) — 0 token.

Ce que cet enforcer défend : *un prompt est un fichier versionné, hashé, chargé
au runtime.* Un prompt noyé dans une f-string au milieu d'un service est un
changement de comportement invisible à la revue et introuvable en production —
et, plus grave dans ce framework, un prompt sans fichier n'a **pas de hash**,
donc pas d'épinglage P10, donc aucune eval rejouable.

Trois contrôles, dans cet ordre :

    1. LES FICHIERS existent et tiennent — un prompt par agent de l'IR, sous
       plafond de taille, sans secret, sans outil fantôme, sans instruction
       contradictoire évidente ;
    2. LES SKILLS déclarées au contrat d'agent sont nommées dans le prompt, et
       réciproquement — une skill n'ayant ni schéma ni effet de bord, le prompt
       est le seul endroit où elle peut exister, donc le seul où l'on peut
       constater qu'elle n'existe pas ;
    3. LE CODE ne contient pas de prompt inline — aucun littéral long et
       impératif hors du module de chargement.

Le troisième est le seul qui attrape la régression réelle : les fichiers restent
sagement en place pendant qu'une f-string les double dans le code.

Usage :
    python .sdda/sdda.py lint-prompts --mission 1 --json
    python .sdda/sdda.py lint-prompts --mission 1 --require-code   # après génération
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import app_name  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

#: Au-delà, un prompt système n'est plus relu par personne — et le modèle en
#: ignore le milieu. Le seuil est indicatif (WARN), la mesure ne l'est pas.
PROMPT_WARN_CHARS = 12_000
PROMPT_HARD_CHARS = 32_000

#: Motifs de secret. Volontairement identiques à ceux de `source_registry` :
#: une clé n'est pas moins dangereuse dans un prompt que dans un manifeste.
SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9])(sk-[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}|ghp_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{12,}|glpat-[A-Za-z0-9_-]{16,}|eyJ[A-Za-z0-9_-]{20,})")

#: Variables de template non résolues : `{nom}` seul, hors bloc de code.
TEMPLATE_VAR_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

#: Instructions qui s'annulent — l'une des causes les plus fréquentes de
#: « l'agent ignore la consigne » : elle est bien là, contredite 40 lignes plus bas.
CONTRADICTION_PAIRS = (
    (re.compile(r"\bne\s+jamais\s+(\w+)", re.I), re.compile(r"\btoujours\s+(\w+)", re.I)),
)

#: Un littéral de code est suspect s'il est long ET impératif : une requête SQL
#: ou un message d'erreur multi-lignes ne doit pas être signalé.
INLINE_MIN_CHARS = 200
INSTRUCTION_RE = re.compile(
    r"\b(tu es |you are |your role|ton rôle|agisse?[sz] |act as |ne jamais |never |"
    r"réponds? |respond |answer |assistant|system prompt|instructions? *:)", re.I)

#: Le seul module autorisé à porter du texte de prompt : celui qui le CHARGE.
#: Le module qui CHARGE les prompts, par langage — le seul endroit où leur nom apparaît.
PROMPT_MODULE_NAMES = ("prompts.py", "Prompts.cs", "prompts.ts", "Prompts.java", "Prompts.kt")

#: Les deux champs de l'IR qui n'existent QUE dans le prompt, et leur point de
#: rendez-vous. Sans section nommée, la symétrie contrat <-> prompt ne serait
#: vérifiable que par un LLM — donc pas vérifiable en gate déterministe.
#:
#: `skills` et `rules` obéissent au même contrat et pour la même raison : ni
#: l'un ni l'autre n'a de schéma ou d'effet de bord qu'une gate pourrait
#: exécuter. Une skill est ce que l'agent SAIT FAIRE, une rule ce qu'il DOIT
#: RESPECTER — l'architecte les déclare, `dev-prompt` les implémente, et aucun
#: des deux ne peut écrire chez l'autre. Le seul constat possible est l'écart,
#: dans les deux sens.
#:
#: Table : champ d'IR -> (titre de section, classe si non implémenté, classe si non déclaré,
#:                        emplacement au contrat, ce que l'écart coûte)
#: Déclarées en constantes plutôt qu'en littéraux dans la table : c'est la forme
#: que `sync_error_registry.py` reconnaît. Sans elles, une classe réellement
#: émise n'entrerait pas au registre canonique — et le registre se dirait « à
#: jour » en ignorant deux classes bloquantes.
CLS_SKILL_MISSING = "SKILL_NOT_IMPLEMENTED"
CLS_SKILL_EXTRA = "SKILL_UNDECLARED"
CLS_RULE_MISSING = "RULE_NOT_IMPLEMENTED"
CLS_RULE_EXTRA = "RULE_UNDECLARED"

SYMMETRIC_FIELDS = {
    "skills": (
        "Compétences", CLS_SKILL_MISSING, CLS_SKILL_EXTRA, "## 5. Skills",
        "Une compétence déclarée par l'architecte et absente du prompt est une compétence "
        "que personne n'implémente et que rien ne mesure",
    ),
    "rules": (
        "Règles", CLS_RULE_MISSING, CLS_RULE_EXTRA, "## 6. Règles",
        "Une règle déclarée par l'architecte et absente du prompt est une contrainte que "
        "l'agent ne connaît pas — et qu'aucune gate ne peut rattraper, puisqu'une règle "
        "n'a ni schéma ni effet de bord",
    ),
}

SKILL_RE = re.compile(r"`([a-z0-9][a-z0-9-]{2,})`")

#: Un slug cité sous `## Compétences` / `## Règles` sans fragment dans
#: `skills/` / `rules/` : le prompt nomme une compétence dont la matière n'est
#: écrite nulle part. Avertissement, pas erreur — le prompt reste l'exécutable,
#: le fragment est ce qu'un relecteur lit pour savoir ce que le slug veut dire.
CLS_SKILL_FILE_MISSING = "SKILL_FILE_MISSING"
CLS_RULE_FILE_MISSING = "RULE_FILE_MISSING"
FRAGMENT_DIRS = {"skills": ("Compétences", paths.skills_dir, CLS_SKILL_FILE_MISSING),
                 "rules": ("Règles", paths.rules_dir, CLS_RULE_FILE_MISSING)}


def check_fragments(root: Path, files: list[Path], report: Report) -> int:
    """Chaque slug cité par un prompt a son fragment `skills/{slug}.md` ou `rules/{slug}.md`.

    Le répertoire est celui de l'application (`workspace/src/{App}/`) : les
    fragments partent avec le prompt, ou ne servent à rien.
    """
    app = app_name(root)
    missing = 0
    for path in files:
        text = markdown_io.read_text(path)
        for field, (heading, dir_of, cls) in FRAGMENT_DIRS.items():
            slugs = prompt_slugs(text, heading) or set()
            for slug in sorted(slugs):
                fragment = dir_of(root, app) / f"{slug}.md"
                if not fragment.is_file():
                    missing += 1
                    report.warn(cls, f"prompt `{path.stem.removesuffix('.system')}` cite `{slug}` sous `## {heading}` "
                                     f"sans fragment `{paths.rel(root, fragment)}`",
                                fix=f"écrire le fragment : quand la {field[:-1]} s'applique, ce qu'elle produit, ce qui prouve "
                                    "qu'elle a joué — c'est la matière du prompt, relue par la revue",
                                location=paths.rel(root, path))
    return missing


def _unqualified(slug: str) -> str:
    """`1-explain-invoice-line` -> `explain-invoice-line`.

    L'IR qualifie par mission, le prompt écrit le nom nu. Comparer les deux
    sans normaliser produirait un faux positif sur chaque skill.
    """
    return re.sub(r"^\d+-", "", markdown_io.strip_code(str(slug)).strip())


def prompt_slugs(text: str, heading: str) -> set[str] | None:
    """Les slugs listés sous `## {heading}`. `None` si la section est absente.

    Distinguer « section absente » de « section vide » compte : la première est
    un prompt qui n'a pas été écrit contre le contrat, la seconde une
    déclaration explicite qu'il n'y a rien à nommer.
    """
    body = markdown_io.section_body(text, heading)
    if body is None:
        return None
    return {_unqualified(m.group(1)) for m in SKILL_RE.finditer(body)}


def _prompt_key(agent: dict[str, Any]) -> str:
    ref = str(agent.get("promptRef") or "")
    if ref.endswith(".system.md"):
        return ref.rsplit("/", 1)[-1][: -len(".system.md")]
    return _unqualified(agent.get("id") or "")


def declared_by_prompt(agents: list[dict[str, Any]], field: str) -> dict[str, set[str]]:
    """Clé = basename du prompt (`billing-specialist`), valeur = slugs nus."""
    out: dict[str, set[str]] = {}
    for agent in agents:
        key = _prompt_key(agent)
        if key:
            out.setdefault(key, set()).update(_unqualified(s) for s in (agent.get(field) or []))
    return out


# ---------------------------------------------------------------------------
# 1. Les fichiers de prompt
# ---------------------------------------------------------------------------
def prompt_files(root: Path) -> list[Path]:
    return sorted(paths.prompts_dir(root, app_name(root)).glob("*.system.md"))


def ir_agents(root: Path, mission: int | str | None) -> tuple[list[dict[str, Any]], str]:
    if mission is None:
        return [], ""
    path = paths.ir_path(root, mission)
    if not path.is_file():
        return [], ""
    try:
        ir = json.loads(markdown_io.read_text(path))
    except ValueError:
        return [], paths.rel(root, path)
    return [a for a in (ir.get("agents") or []) if isinstance(a, dict)], paths.rel(root, path)


def contract_tool_names(root: Path) -> set[str]:
    """Les `name:` des tool-contracts sur disque.

    L'IR n'existe qu'après compilation ; s'en remettre à lui seul rendrait le
    contrôle muet au moment où il sert le plus — juste après l'écriture des
    prompts, avant le premier build.
    """
    out: set[str] = set()
    for path in sorted(paths.contracts_dir(root, "tools").glob("*.tool.md")):
        naming = markdown_io.section_body(markdown_io.read_text(path), "Nom et description") or ""
        name = markdown_io.strip_code(markdown_io.parse_kv_list(naming).get("name", ""))
        if name:
            out.add(name)
    return out


def check_file(root: Path, path: Path, tool_names: set[str],
               declared_symmetric: dict[str, set[str] | None] | None, report: Report) -> dict[str, Any]:
    text = markdown_io.read_text(path)
    loc = paths.rel(root, path)
    slug = path.name[: -len(".system.md")]
    summary: dict[str, Any] = {
        "slug": slug, "path": loc, "chars": len(text),
        "hash": hashing.sha256_text(text),
    }

    if not text.strip():
        report.error("PROMPT_EMPTY", f"prompt `{slug}` vide",
                     fix="un prompt vide produit un agent qui improvise entièrement", location=loc)
        return summary

    if len(text) > PROMPT_HARD_CHARS:
        report.error(
            "PROMPT_TOO_LONG",
            f"prompt `{slug}` : {len(text)} caractères (plafond {PROMPT_HARD_CHARS})",
            fix="découper : ce qui est stable va dans le prompt, ce qui varie va dans le contexte "
                "ou un outil. Au-delà, le modèle traite le milieu comme du bruit",
            location=loc,
        )
    elif len(text) > PROMPT_WARN_CHARS:
        report.warn("PROMPT_LONG", f"prompt `{slug}` : {len(text)} caractères (seuil {PROMPT_WARN_CHARS})",
                    fix="vérifier que tout est encore lu — par le modèle et par un humain", location=loc)

    for match in SECRET_RE.finditer(text):
        line = text[: match.start()].count("\n") + 1
        report.error(
            "SECRET_LEAK",
            f"prompt `{slug}` ligne {line} : motif de secret (`{match.group(1)[:8]}…`)",
            fix="retirer la valeur, la faire porter par la configuration, et FAIRE TOURNER la clé — "
                "elle est dans l'historique git dès le premier commit",
            location=loc,
        )

    body = re.sub(r"```.*?```", "", text, flags=re.S)
    unresolved = sorted({v for v in TEMPLATE_VAR_RE.findall(body)} - {"n", "m"})
    if unresolved:
        report.warn(
            "PROMPT_TEMPLATE_UNRESOLVED",
            f"prompt `{slug}` : variable(s) de template `{unresolved[:5]}`",
            fix="chaque variable doit être fournie au chargement. Une variable non fournie arrive "
                "littéralement dans le contexte du modèle, qui la traite comme du texte",
            location=loc,
        )

    if tool_names:
        cited = {m.group(1) for m in re.finditer(r"`([a-z][a-z0-9_]{2,})`", text)}
        ghosts = sorted(c for c in cited if c.endswith(("_lookup", "_search", "_count", "_get", "_create"))
                        and c not in tool_names)
        if ghosts:
            report.error(
                "PROMPT_TOOL_UNKNOWN",
                f"prompt `{slug}` cite un outil inexistant : {ghosts}",
                fix="un outil nommé dans un prompt et absent du câblage produit un agent qui tente "
                    "de l'appeler puis improvise. Corriger le nom, ou câbler l'outil",
                location=loc,
            )

    # Symétrie contrat <-> prompt sur les champs qui n'existent QUE dans le
    # prompt (skills, rules). Ni l'un ni l'autre n'a de schéma ou d'effet de
    # bord : le seul endroit où ils peuvent exister est le prompt, et le seul
    # moment où l'écart est rattrapable est maintenant — avant `dev-agent`.
    #
    # `declared is None` = l'IR n'existe pas encore, ou ce prompt n'a pas
    # d'agent en face. Se taire alors est la seule option correcte : sans côté
    # contrat, « non déclarée » ne veut rien dire, et un lint qui crie avant la
    # compilation est un lint qu'on désactive.
    for field, (heading, cls_missing, cls_extra, contract_section, why) in SYMMETRIC_FIELDS.items():
        declared = (declared_symmetric or {}).get(field)
        listed = prompt_slugs(text, heading)
        if declared is None or not (declared or listed):
            continue
        summary[field] = sorted(listed or ())

        if declared and listed is None:
            report.error(
                cls_missing,
                f"prompt `{slug}` : aucune section `## {heading}` alors que le contrat "
                f"déclare {sorted(declared)}",
                fix=f"ajouter `## {heading}` et y nommer chaque entrée entre backticks. {why}",
                location=loc,
            )
            continue

        missing = sorted(declared - (listed or set()))
        if missing:
            report.error(
                cls_missing,
                f"prompt `{slug}` : {field} déclaré(es) au contrat mais absente(s) de "
                f"`## {heading}` : {missing}",
                fix="implémenter le comportement et le nommer, ou le retirer du contrat "
                    "d'agent. Le roster de l'architecte fait foi (P7)",
                location=loc,
            )
        extra = sorted((listed or set()) - declared)
        if extra:
            report.error(
                cls_extra,
                f"prompt `{slug}` : entrée(s) nommée(s) dans le prompt et absente(s) du "
                f"contrat : {extra}",
                fix=f"déclarer au `{contract_section}` du contrat d'agent, ou retirer du "
                    "prompt. Ce qui n'existe que dans le prompt échappe à la revue "
                    "et n'a aucune AC en face",
                location=loc,
            )

    lowered = body.lower()
    for never_re, always_re in CONTRADICTION_PAIRS:
        nevers = {m.group(1) for m in never_re.finditer(lowered)}
        always = {m.group(1) for m in always_re.finditer(lowered)}
        for verb in sorted(nevers & always):
            report.warn(
                "PROMPT_CONTRADICTION",
                f"prompt `{slug}` : « ne jamais {verb} » et « toujours {verb} » coexistent",
                fix="une instruction contredite plus bas est une instruction morte — le modèle suit "
                    "l'une des deux, et laquelle dépend du modèle",
                location=loc,
            )
    return summary


# ---------------------------------------------------------------------------
# 2. Le code — c'est ici que la régression se produit
# ---------------------------------------------------------------------------
_DEF_HEADER_RE = re.compile(r"(?:^|\n)\s*(?:async\s+def|def|class)\s+[^\n]*:\s*$")
_PREAMBLE_RE = re.compile(r"^(?:\s*(?:#[^\n]*\n|from\s+__future__\s+import[^\n]*\n|\s*\n))*\s*$")


def _is_docstring(text: str, start: int) -> bool:
    """Le littéral qui commence à `start` est-il une docstring (module, classe, fonction) ?

    Module : rien avant lui que shebang, commentaires, `from __future__` et
    lignes vides. Classe / fonction : la ligne précédente est un en-tête `def`
    ou `class` terminé par `:`. Tout autre littéral, même long et impératif,
    reste suspect — c'est précisément un prompt dans une f-string.
    """
    before = text[:start]
    return bool(_PREAMBLE_RE.match(before)) or bool(_DEF_HEADER_RE.search(before.rstrip(" \t")))


#: Répertoires TIERS sous `src/` : environnement virtuel, dépendances installées,
#: caches et sorties de build. Ce code n'est écrit par aucun agent ; le scanner
#: y trouvait 226 « prompts en dur » (docstrings et messages de langchain,
#: pydantic…) dès que `dev-backend` installait le projet dans `src/{App}/.venv`,
#: et le hook de fin refusait en boucle l'arrêt d'un agent qui n'y était pour rien.
VENDORED_DIRS = frozenset({".venv", "venv", ".tox", "site-packages", "node_modules",
                           "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "bin", "obj",
                           # sorties de build Node et JVM : des copies, pas des sources
                           "dist", "build", ".gradle", "target", "out"})


def _vendored(path: Path, src: Path) -> bool:
    return any(part in VENDORED_DIRS for part in path.relative_to(src).parts[:-1])


#: Les sources des cinq langages. `.kt` manquait : un prompt écrit en dur dans
#: du Kotlin échappait au lint et au hook `postflight_no_inline_prompt`.
INLINE_SCAN_SUFFIXES = (".py", ".cs", ".ts", ".mts", ".java", ".kt", ".kts")


def scan_inline_prompts(root: Path, report: Report) -> int:
    """Un littéral long et impératif hors du module de chargement est un prompt."""
    src = paths.workspace(root) / "src"
    if not src.is_dir():
        return 0

    literal_re = re.compile(r'("""(?:[^"\\]|\\.|"(?!""))*"""|\'\'\'(?:[^\'\\]|\\.|\'(?!\'\'))*\'\'\''
                            r'|@"(?:[^"]|"")*"|`(?:[^`\\]|\\.)*`)', re.S)
    scanned = 0
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.suffix not in INLINE_SCAN_SUFFIXES:
            continue
        if _vendored(path, src):
            continue
        if path.name in PROMPT_MODULE_NAMES or "/tests/" in path.as_posix().casefold():
            continue
        scanned += 1
        text = markdown_io.read_text(path)
        for match in literal_re.finditer(text):
            literal = match.group(1)
            if len(literal) < INLINE_MIN_CHARS or not INSTRUCTION_RE.search(literal):
                continue
            if _is_docstring(text, match.start()):
                # Une docstring de module, de classe ou de fonction est de la
                # documentation, pas un prompt : le modèle ne la lit jamais.
                # Sans cette exception, le runtime GÉNÉRÉ par le framework
                # (`data/formats/__init__.py`, dont la docstring explique en
                # français impératif pourquoi il ne convertit rien) déclenchait
                # le hook de fin sur l'agent qui venait de s'arrêter — et qui
                # n'avait pas écrit ce fichier.
                continue
            line = text[: match.start()].count("\n") + 1
            report.error(
                "PROMPT_INLINE_FORBIDDEN",
                f"{paths.rel(root, path)}:{line} — littéral de {len(literal)} caractères à l'allure "
                "d'instruction système",
                fix="déplacer le texte dans `workspace/src/{App}/prompts/{slug}.system.md` et le charger au "
                    "démarrage. Un prompt sans fichier n'a pas de hash, donc pas d'épinglage (P10), "
                    "donc aucune eval rejouable",
                location=paths.rel(root, path),
            )
    return scanned


# ---------------------------------------------------------------------------
# Entrée
# ---------------------------------------------------------------------------
def run(root: Path, mission: int | str | None = None, require_code: bool = False) -> Report:
    report = Report(name="PROMPTS", target=str(root))

    files = prompt_files(root)
    agents, ir_loc = ir_agents(root, mission)
    tool_names = {str(t) for a in agents for t in (a.get("tools") or [])}
    tool_names |= contract_tool_names(root)
    report.data["fragmentsMissing"] = check_fragments(root, files, report)

    pinned: dict[str, str] = {}
    if mission is not None and not ir_loc:
        report.error(
            "IR_NOT_FOUND",
            f"`--mission {mission}` : IR absent — aucun prompt attendu n'est connu, donc aucun ne peut être épinglé",
            fix=f"`python .sdda/sdda.py ir-compiler --mission {mission}` avant le lint. Sans IR, la part "
                "`prompts` de G5 serait verte sur une liste vide : un contrôle P10 qui n'a rien contrôlé",
            location="workspace/.sys/.ir/")
    elif mission is not None and not agents:
        report.error(
            "PROMPT_NOT_PINNED",
            f"l'IR `{ir_loc}` ne déclare aucun agent : aucun prompt à épingler",
            fix="un système sans agent n'a rien à évaluer en G5 — corriger la topologie, puis recompiler l'IR",
            location=ir_loc)
    if agents:
        pinned = pin_expected_prompts(root, agents, ir_loc, report)
    elif not files:
        report.warn("PROMPT_MISSING", "aucun prompt sur disque et aucun agent dans l'IR",
                    fix="rien à vérifier — relancer après `/sdda-topology` puis `/sdda-build`")

    # Un dictionnaire par prompt : {champ -> slugs déclarés}. `None` pour un
    # prompt sans agent en face, ce que `check_file` interprète comme « ne rien
    # dire » plutôt que « rien de déclaré ».
    declared_maps = {field: declared_by_prompt(agents, field) for field in SYMMETRIC_FIELDS}
    known = {key for m in declared_maps.values() for key in m}

    def symmetric_for(slug: str) -> dict[str, set[str] | None] | None:
        if slug not in known:
            return None
        return {field: declared_maps[field].get(slug) for field in SYMMETRIC_FIELDS}

    summaries = [check_file(root, path, tool_names,
                            symmetric_for(path.name[: -len(".system.md")]), report)
                 for path in files]
    scanned = scan_inline_prompts(root, report)

    if require_code and scanned == 0:
        report.error("PROMPT_CODE_ABSENT", "`--require-code` demandé mais aucun fichier source scanné",
                     fix="lancer après la génération du code (`/sdda-build`)")

    report.data.update({
        "prompts": summaries,
        "sourceFilesScanned": scanned,
        # Les hashes sont rendus pour l'épinglage P10 : `agents[].promptHash`
        # se recalcule ici, sans que l'IR ait à faire confiance au générateur.
        "promptHashes": {s["slug"]: s["hash"] for s in summaries if s.get("hash")},
        # Ce qui entre dans `pinnedHashes` du rapport de gate : un hash par
        # prompt ATTENDU par l'IR (clé = chemin relatif, que `compute_status`
        # sait recalculer), plus l'identité de l'IR. Les prompts orphelins
        # (sans agent) n'y entrent pas : épingler ce que personne n'exécute
        # ferait périmer la gate pour un fichier sans effet.
        "pinnedHashes": pinned,
    })
    return report


def pin_expected_prompts(root: Path, agents: list[dict[str, Any]], ir_loc: str, report: Report) -> dict[str, str]:
    """Le hash de chaque `prompts/{agent}.system.md` que l'IR attend — ou une ERREUR.

    P10 : un résultat d'eval ne vaut que pour le tuple qu'il épingle, et le
    prompt en est la première composante. La part `prompts` de G5 s'écrivait
    avec `pinnedHashes: {}` : verte, et ne prouvant rien — un prompt réécrit
    après le lint ne périmait aucune gate. Trois cas, et aucun n'est muet :

      - le fichier attendu est absent : `[PROMPT_MISSING]`. L'ancien contrôle se
        taisait dès que l'agent portait un `promptRef`, c'est-à-dire toujours
        après compilation — le cas même où l'on attend le fichier ;
      - le fichier est là mais son hash diffère du `promptHash` que l'IR a
        figé : `[PROMPT_HASH_MISMATCH]`. L'IR décrit un autre prompt que celui
        qui tournera, et les evals épinglées sur l'IR mesurent un fantôme ;
      - sinon : épinglé, sous son chemin relatif.
    """
    pinned: dict[str, str] = {}
    prompts_root = paths.prompts_dir(root, app_name(root))
    for agent in agents:
        agent_id = str(agent.get("id") or "")
        ref = str(agent.get("promptRef") or "")
        slug = _prompt_key(agent)
        if not slug:
            continue
        path = paths.resolve_rel(root, ref) if ref else prompts_root / f"{slug}.system.md"
        loc = paths.rel(root, path)
        if not path.is_file():
            report.error(
                "PROMPT_MISSING",
                f"agent `{agent_id}` : prompt attendu `{loc}` absent — rien à épingler",
                fix=f"écrire `{loc}` (dev-prompt) — un agent sans prompt de fichier est un agent dont le "
                    "comportement n'est ni versionné ni hashé, et G5 ne peut rien épingler pour lui",
                location=ir_loc or "workspace/.sys/.ir/")
            continue
        digest = hashing.sha256_file(path)
        frozen = str(agent.get("promptHash") or "")
        if frozen and not hashing.hashes_match(frozen, digest):
            report.error(
                "PROMPT_HASH_MISMATCH",
                f"agent `{agent_id}` : `{loc}` a changé depuis la compilation de l'IR "
                f"(IR {frozen[:19]}…, disque {digest[:19]}…)",
                fix="recompiler l'IR (`ir-compiler`) puis relancer les evals : ce qu'elles épinglent "
                    "doit être le prompt qui tournera",
                location=loc)
            continue
        # Clé = chemin relatif, la forme qu'épingle déjà `eval_runner` pour
        # G5 et que `compute_status.current_hash` recalcule. (La forme
        # `prompt:{slug}` y a une branche, mais qui lève `NameError` — jamais
        # exercée, puisque personne n'épinglait de prompt.)
        pinned[loc] = digest
    # Pas de clé `ir` ici : la part est écrite sous l'artefact `{n}` (numéro
    # seul), et `compute_status` ne retrouve la mission d'une clé `ir` que
    # depuis un artefact `{n}-{Nom}` — la clé serait donc « périmée » dès
    # l'écriture. Le `promptHash` figé par l'IR est déjà confronté au disque
    # ci-dessus, ce qui couvre le seul lien IR <-> prompt que cette part juge.
    return dict(sorted(pinned.items()))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Invariant `prompts-are-files` (P1) — fichiers et code, 0 token")
    p.add_argument("--mission", default=None, help="numéro de mission ; confronte les prompts aux agents de l'IR")
    p.add_argument("--require-code", action="store_true", help="échouer si aucun fichier source n'a été scanné")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = run(root, mission=args.mission, require_code=args.require_code)

    if not args.no_report:
        try:
            write_gate_report(root, "G5", args.mission or "stack", report,
                              pinned=dict(report.data.get("pinnedHashes") or {}), part="prompts")
        except OSError:
            pass
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
