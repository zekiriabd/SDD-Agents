#!/usr/bin/env python3
"""DATA ACCESS — enforcer de l'invariant `db-safety-envelope-present` (G3).

Ce que cette gate défend : *une stratégie d'accès aux données se câble quand sa
frontière est déclarée et vérifiable, pas quand elle fonctionne sur le poste du
développeur.* Un agent qui lit un répertoire sans racine verrouillée finit par
lire `../.env` ; un agent qui appelle une API sans allowlist d'egress finit par
appeler l'hôte qu'un champ de donnée lui a soufflé ; un agent qui répond sur un
export de trois jours annonce un retard faux avec assurance. Les trois
défaillances sont silencieuses, et aucune ne se voit dans un test unitaire du
code métier.

Le script confronte **quatre sources** qui doivent dire la même chose :

    1. `STACK.md` — la stack active, l'enveloppe, `Stores[]` et `Sources[]` inline
    2. le **manifeste** optionnel — `SourceManifests[]` : une configuration MCP
       standard (`mcp.json`) importée telle quelle, à côté de STACK.md
    3. le **disque** — les racines existent, les globs résolvent, les schémas
       figés sont là, le fichier `.env` déclare bien les variables citées
    4. l'**IR** (s'il est compilé) — `dataAccess[]` décrit la même stratégie et
       porte la même enveloppe

Un désaccord entre deux d'entre elles est une erreur : c'est exactement le cas
où l'on croit avoir une protection qu'on n'a pas.

Il n'exécute rien, n'ouvre aucun enregistrement, n'appelle aucun réseau, ne lit
**aucune valeur** de secret — seulement des noms de variables. Il tourne donc
avant que le code existe, ce qui est le seul moment où corriger est bon marché.

Usage :
    python .sdda/sdda.py validate-data-access --json
    python .sdda/sdda.py validate-data-access --mission 1
    python .sdda/sdda.py validate-data-access --source order_tracking

Variable d'environnement :
    SDDA_SKIP_STORE_PROBE=1   un store local/réseau absent devient un WARN.
                              Pour une CI qui ne monte pas les partages — jamais
                              pour un preflight de production.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io, paths, source_registry as sr  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import active_stacks, read_project_section, read_stack_section_kv  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

#: Stratégies qui s'appuient sur une base : leur enveloppe est déclarée par les
#: clés `Db*` de `## Active Data Access`.
SQL_STRATEGIES = ("view-per-agent", "repository-tools", "semantic-layer", "text-to-sql", "graphql")

#: La stratégie « sources déclarées » : fichiers, API, MCP, sous une grammaire
#: close et une enveloppe `Source*`.
DECLARED = "declared-sources"

SECTION = "workspace/stack/STACK.md ## Active Data Sources"


# ---------------------------------------------------------------------------
# Lecture de la déclaration
# ---------------------------------------------------------------------------
def active_strategy(root: Path) -> str:
    """La stack active de `## Active Data Access`, ou `""` si indécidable."""
    names = active_stacks(root, "Active Data Access")
    return names[0] if len(names) == 1 else ("" if not names else "|".join(names))


def schema_path(root: Path, source_id: str) -> Path:
    app = str(read_project_section(root).get("AppName") or "App").strip() or "App"
    return root / sr.schema_rel_path(source_id, app)


def _positive_int(value: Any) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


# ---------------------------------------------------------------------------
# Enveloppe
# ---------------------------------------------------------------------------
def check_envelope(section: dict[str, Any], store_ids: list[str], source_ids: list[str],
                   report: Report) -> dict[str, Any]:
    """L'enveloppe `Source*` : les mêmes bornes que l'enveloppe DB, tous connecteurs."""
    role = str(section.get("SourceAgentRole") or "").strip().lower()
    if role != "readonly":
        report.error(
            "DATA_SOURCE_ROLE_INVALID",
            f"`SourceAgentRole: {role or '<absent>'}` — `readonly` est la seule valeur admise sur `{DECLARED}`",
            fix="mettre `SourceAgentRole: readonly` ; un besoin d'écriture relève de "
                "`dataaccess/repository-tools.md`, où l'idempotence et la classe d'effet de bord sont exigées",
            location=SECTION,
        )

    bounds: dict[str, int | None] = {}
    for key in ("SourceReadTimeoutMs", "SourceMaxRecordsReturned", "SourceMaxObjectBytes"):
        bounds[key] = _positive_int(section.get(key))
        if bounds[key] is None:
            report.error(
                "DATA_ACCESS_ENVELOPE_MISSING",
                f"`{key}` absent ou non borné (valeur : {section.get(key)!r})",
                fix=f"déclarer `{key}` avec un entier > 0 — une borne absente est une borne infinie",
                location=SECTION,
            )

    forbidden_raw = section.get("SourceForbiddenOps") or []
    forbidden = {str(o).strip().upper() for o in forbidden_raw} if isinstance(forbidden_raw, list) else set()
    missing_ops = [o for o in sr.REQUIRED_FORBIDDEN_OPS if o not in forbidden]
    if missing_ops:
        report.error(
            "DATA_ACCESS_ENVELOPE_MISSING",
            f"`SourceForbiddenOps` n'interdit pas {missing_ops}",
            fix=f"déclarer `SourceForbiddenOps: {list(sr.REQUIRED_FORBIDDEN_OPS)}` — "
                "`SYMLINK_FOLLOW` et `UNDECLARED_EGRESS` sont les deux contournements de frontière "
                "qui ne ressemblent pas à des écritures",
            location=SECTION,
        )

    allowed_sources = _allowlist(section, "SourceAllowedSources", source_ids, "source",
                                 "DATA_SOURCE_ALLOWLIST_UNKNOWN", "DATA_SOURCE_SHADOWED", report)
    allowed_stores = _allowlist(section, "SourceAllowedStores", store_ids, "store",
                                "DATA_STORE_ALLOWLIST_UNKNOWN", "DATA_STORE_SHADOWED", report)

    egress_raw = section.get("SourceEgressAllowlist")
    egress = [str(h).strip().lower() for h in egress_raw] if isinstance(egress_raw, list) else []

    logging = str(section.get("SourceQueryLogging") or "").strip().lower()
    if logging not in ("full", "sampled"):
        report.error(
            "DATA_ACCESS_ENVELOPE_MISSING",
            f"`SourceQueryLogging: {logging or '<absent>'}` invalide",
            fix="`full` (défaut) ou `sampled` — sans journal, aucun post-mortem n'est possible "
                "sur une réponse fausse",
            location=SECTION,
        )

    return {
        "role": role,
        "statementTimeoutMs": bounds["SourceReadTimeoutMs"],
        "maxRows": bounds["SourceMaxRecordsReturned"],
        "schemas": allowed_sources,
        "stores": allowed_stores,
        "forbidden": sorted(forbidden),
        "egressAllowlist": egress,
        "logging": logging,
    }


def _allowlist(section: dict[str, Any], key: str, declared_ids: list[str], label: str,
               unknown_cls: str, shadow_cls: str, report: Report) -> list[str]:
    raw = section.get(key)
    if not isinstance(raw, list) or not raw:
        return list(declared_ids)
    allowed = [str(a).strip() for a in raw]
    unknown = sorted(set(allowed) - set(declared_ids))
    if unknown:
        report.error(
            unknown_cls,
            f"`{key}` cite des {label}s non déclaré(e)s : {unknown}",
            fix=f"corriger l'allowlist, ou déclarer le {label} dans un manifeste ou dans STACK.md",
            location=SECTION,
        )
    shadowed = sorted(set(declared_ids) - set(allowed))
    if shadowed:
        report.warn(
            shadow_cls,
            f"{label}(s) déclaré(s) mais hors allowlist, donc inexistant(s) pour l'application : {shadowed}",
            fix=f"ajouter à `{key}`, ou retirer la déclaration si c'est un reliquat",
            location=SECTION,
        )
    return allowed


# ---------------------------------------------------------------------------
# Secrets : le fichier `.env`, par les noms uniquement
# ---------------------------------------------------------------------------
def check_secrets(root: Path, section: dict[str, Any], registry: sr.Registry,
                  report: Report) -> dict[str, Any]:
    """Le fichier de secrets existe, est ignoré par git, et déclare ce qui est cité.

    Aucune valeur n'est lue. Le script n'a besoin que des noms, et un script
    qui n'a jamais eu la valeur en mémoire ne peut pas la recopier dans un
    rapport de gate — lequel, lui, n'est pas gitignoré partout.
    """
    # `SourceSecretsFile` est RELATIF AU LIVRABLE (`workspace/src/{App}/`), pas
    # à la racine du dépôt : c'est l'application qui consomme ces valeurs, et
    # c'est de là qu'elle part en exécutable ou en conteneur (paths.env_path).
    declared = str(section.get("SourceSecretsFile") or ".env").strip()
    app = str(read_project_section(root).get("AppName") or "App").strip() or "App"
    candidate = Path(declared)
    env_path = candidate if candidate.is_absolute() else (paths.app_dir(root, app) / candidate)
    shown = declared if candidate.is_absolute() else f"workspace/src/{app}/{declared}"

    referenced: dict[str, list[str]] = {}
    for sid, store in registry.stores.items():
        for name in sr.auth_env_refs(store):
            referenced.setdefault(name, []).append(sid)

    summary = {"file": shown, "referenced": sorted(referenced), "present": False}

    if not env_path.is_file():
        if referenced:
            report.error(
                "DATA_SECRET_FILE_MISSING",
                f"`SourceSecretsFile: {declared}` introuvable ({shown}) alors que {len(referenced)} variable(s) "
                f"y sont référencées ({', '.join(sorted(referenced)[:5])})",
                fix=f"créer `{shown}` — avec l'application, pas à la racine du dépôt — avec une ligne `NOM=` "
                    "par variable citée, et vérifier qu'il est bien dans `.gitignore`",
                location=SECTION,
            )
        return summary

    summary["present"] = True
    names = sr.env_names(env_path)
    summary["declaredCount"] = len(names)

    missing = sorted(n for n in referenced if n not in names)
    if missing:
        report.error(
            "DATA_SECRET_VAR_UNDECLARED",
            f"variable(s) citée(s) par un store mais absente(s) de `{shown}` : "
            + ", ".join(f"{n} (store `{referenced[n][0]}`)" for n in missing[:5]),
            fix=f"ajouter `NOM=` dans `{shown}` — une variable absente ne produit pas une erreur "
                "d'authentification claire, elle produit un appel anonyme qui renvoie 200 et zéro ligne",
            location=SECTION,
        )

    if not _is_git_ignored(root, env_path):
        report.error(
            "DATA_SECRET_FILE_UNIGNORED",
            f"`{shown}` n'apparaît pas dans le `.gitignore` du projet",
            fix=f"ajouter `workspace/src/*/.env` (ou `{shown}`) à `.gitignore` avant d'y écrire la moindre clé — "
                "un secret commité reste dans l'historique après sa suppression",
            location=".gitignore",
        )
    return summary


def _is_git_ignored(root: Path, target: Path) -> bool:
    """Le fichier de secrets est-il couvert par le `.gitignore` du projet ?

    Comparaison sur les motifs, pas d'appel à git : le validateur doit tourner
    sur un dossier qui n'est pas encore un dépôt. On honore les formes que les
    projets écrivent vraiment — le chemin exact, le nom seul (`.env`, qui
    s'applique à tout niveau), et un glob (`workspace/src/*/.env`,
    `workspace/**/.env`) — sans prétendre réimplémenter gitignore.
    """
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return False
    try:
        rel = target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = target.as_posix()
    name = target.name
    for line in markdown_io.read_text(gitignore).split("\n"):
        pattern = line.strip()
        if not pattern or pattern.startswith("#") or pattern.startswith("!"):
            continue
        pattern = pattern.removeprefix("/").removesuffix("/")
        if pattern in (rel, name, "*" + target.suffix):
            return True
        if "*" in pattern and (fnmatch.fnmatchcase(rel, pattern)
                               or fnmatch.fnmatchcase(rel, pattern.replace("**/", "*/"))
                               or fnmatch.fnmatchcase(rel, pattern.replace("**/", ""))):
            return True
    return False


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------
def check_store(root: Path, sid: str, store: dict[str, Any], envelope: dict[str, Any],
                registry: sr.Registry, report: Report) -> dict[str, Any]:
    loc = registry.origin.get(f"store:{sid}", SECTION)
    summary: dict[str, Any] = {"id": sid}

    if not sr.is_valid_id(sid):
        report.error("DATA_STORE_INCOMPLETE", f"store `{sid}` : identifiant invalide",
                     fix="snake_case, commence par une lettre, 2 à 64 caractères", location=loc)

    kind = str(store.get("kind") or "").strip()
    summary["kind"] = kind
    if kind not in sr.STORE_KINDS:
        report.error("DATA_STORE_KIND_UNKNOWN", f"store `{sid}` : `kind: {kind or '<absent>'}` inconnu",
                     fix=f"choisir parmi {list(sr.STORE_KINDS)}", location=loc)
        return summary

    allowed = sr.STORE_COMMON_KEYS | sr.STORE_KIND_KEYS[kind] | {"_env_refs"}
    unknown = sorted(k for k in set(store) - allowed if not k.startswith("_"))
    if unknown:
        report.error(
            "DATA_STORE_UNKNOWN_KEY",
            f"store `{sid}` ({kind}) : clé(s) inconnue(s) {unknown}",
            fix=f"grammaire close — clés admises pour `{kind}` : "
                f"{', '.join(sorted(allowed - {'_env_refs'}))}",
            location=loc,
        )
    missing = [k for k in sr.STORE_KIND_REQUIRED[kind] if not str(store.get(k) or "").strip()]
    if missing:
        report.error("DATA_STORE_INCOMPLETE", f"store `{sid}` ({kind}) : clé(s) obligatoire(s) absente(s) — {missing}",
                     fix=f"compléter : {', '.join(sr.STORE_KIND_REQUIRED[kind])}", location=loc)

    if store.get("read_only") is False:
        report.error(
            "DATA_SOURCE_ROLE_INVALID",
            f"store `{sid}` : `read_only: false`",
            fix="`declared-sources` est en lecture seule par construction ; un besoin d'écriture est "
                "`dataaccess/repository-tools.md`",
            location=loc,
        )

    _check_auth(sid, store, loc, report)
    _check_egress(sid, store, kind, envelope, loc, report)

    if kind in sr.LOCAL_KINDS:
        summary["root"] = _probe_root(root, sid, store, kind, loc, report)
    if kind == "mcp":
        summary["server"] = str(store.get("server") or "")
    return summary


def _check_auth(sid: str, store: dict[str, Any], loc: str, report: Report) -> None:
    auth = store.get("auth")
    if auth is None:
        report.error(
            "DATA_AUTH_INCOMPLETE",
            f"store `{sid}` : aucun bloc `auth`",
            fix="déclarer `auth: { mode: none }` pour un store public — un store sans authentification "
                "se déclare, il ne s'obtient pas en oubliant la clé",
            location=loc,
        )
        return
    if not isinstance(auth, dict):
        report.error("DATA_AUTH_INCOMPLETE", f"store `{sid}` : `auth` n'est pas un mapping",
                     fix="`auth: { mode: api-key, header: X-API-Key, key_env: CRM_API_KEY }`", location=loc)
        return

    mode = str(auth.get("mode") or "").strip()
    if mode not in sr.AUTH_MODES:
        report.error("DATA_AUTH_MODE_UNKNOWN", f"store `{sid}` : `auth.mode: {mode or '<absent>'}` inconnu",
                     fix=f"choisir parmi {list(sr.AUTH_MODES)}", location=loc)
        return

    required, optional = sr.AUTH_KEYS[mode]
    present = set(auth) - {"mode"}
    absent = sorted(required - present)
    if absent:
        report.error("DATA_AUTH_INCOMPLETE", f"store `{sid}` (`{mode}`) : clé(s) absente(s) — {absent}",
                     fix=f"`{mode}` exige : {', '.join(sorted(required))}", location=loc)
    extra = sorted(present - required - optional)
    if extra:
        report.error(
            "DATA_AUTH_INCOMPLETE",
            f"store `{sid}` (`{mode}`) : clé(s) d'authentification inconnue(s) — {extra}",
            fix=f"grammaire close — `{mode}` admet : {', '.join(sorted(required | optional)) or 'aucune clé'}",
            location=loc,
        )

    # Le cœur : une clé `*_env` porte un NOM, tout le reste ne porte jamais un secret.
    for key, value in auth.items():
        if key == "mode":
            continue
        if key.endswith("_env"):
            name = str(value or "").strip()
            if not name:
                report.error("DATA_AUTH_INCOMPLETE", f"store `{sid}` : `auth.{key}` est vide",
                             fix="donner le NOM de la variable définie dans le fichier de secrets", location=loc)
            elif sr.looks_like_secret(name):
                report.error(
                    "DATA_SECRET_INLINE",
                    f"store `{sid}` : `auth.{key}` ressemble à une valeur de secret, pas à un nom de variable",
                    fix=f"écrire `{key}: NOM_DE_LA_VARIABLE` et mettre la valeur dans le fichier de secrets — "
                        "STACK.md et les manifestes sont relus en revue, pas le `.env`",
                    location=loc,
                )
        elif sr.looks_like_secret(value):
            report.error(
                "DATA_SECRET_INLINE",
                f"store `{sid}` : `auth.{key}` porte une valeur qui ressemble à un secret",
                fix=f"remplacer par une clé `{key}_env` portant le nom d'une variable du fichier de secrets",
                location=loc,
            )


def _check_egress(sid: str, store: dict[str, Any], kind: str, envelope: dict[str, Any],
                  loc: str, report: Report) -> None:
    """Un store distant n'existe que si son hôte est dans l'allowlist d'egress."""
    if kind not in sr.REMOTE_KINDS:
        return
    scheme = sr.store_scheme(store)
    if scheme in ("http",):
        host = sr.store_host(store) or ""
        if host not in ("localhost", "127.0.0.1", "::1"):
            report.error(
                "DATA_TLS_INSECURE",
                f"store `{sid}` : URL en `http://` vers `{host or '?'}`",
                fix="TLS obligatoire hors localhost — une clé d'API en clair sur le réseau est une clé publiée",
                location=loc,
            )
    if store.get("verify_tls") is False:
        report.error(
            "DATA_TLS_INSECURE",
            f"store `{sid}` : `verify_tls: false`",
            fix="vérifier le certificat, ou déclarer l'autorité interne via `auth.mode: mtls` — "
                "désactiver la vérification annule l'authentification du serveur",
            location=loc,
        )

    host = sr.store_host(store)
    if host is None:
        return                                    # stdio MCP : aucun egress réseau
    allowlist = envelope.get("egressAllowlist") or []
    if host.lower() not in [h.lower() for h in allowlist]:
        report.error(
            "DATA_EGRESS_UNDECLARED",
            f"store `{sid}` : l'hôte `{host}` n'est pas dans `SourceEgressAllowlist`",
            fix=f"ajouter `{host}` à `SourceEgressAllowlist`, ou retirer le store — "
                "`UNDECLARED_EGRESS` est interdit par l'enveloppe, et une allowlist vide vaut « aucune sortie »",
            location=loc,
        )


def _probe_root(root: Path, sid: str, store: dict[str, Any], kind: str,
                loc: str, report: Report) -> str | None:
    declared = str(store.get("root") or "").strip()
    if not declared:
        return None
    candidate = Path(declared)
    base = candidate if (candidate.is_absolute() or declared.startswith(("//", "\\\\"))) else (root / candidate)
    try:
        resolved = base.resolve()
    except OSError as exc:
        report.error("DATA_STORE_UNREACHABLE", f"store `{sid}` : `root: {declared}` non résolvable ({exc.__class__.__name__})",
                     fix="vérifier le chemin", location=loc)
        return None

    if not resolved.is_dir():
        skip = os.environ.get("SDDA_SKIP_STORE_PROBE", "0").strip().lower() in ("1", "true", "yes", "on")
        emit = report.warn if skip else report.error
        emit(
            "DATA_STORE_UNREACHABLE",
            f"store `{sid}` ({kind}) : `root: {declared}` n'existe pas ou n'est pas un répertoire ({resolved})",
            fix="monter le partage, créer le répertoire, ou corriger `root`. "
                "Sur une CI qui ne monte pas les partages : `SDDA_SKIP_STORE_PROBE=1` — jamais en preflight de production",
            location=loc,
        )
        return None
    return paths.rel(root, resolved)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
def check_source(root: Path, sid: str, src: dict[str, Any], registry: sr.Registry,
                 section: dict[str, Any], report: Report) -> dict[str, Any]:
    loc = registry.origin.get(f"source:{sid}", SECTION)
    summary: dict[str, Any] = {"id": sid, "files": 0, "bytes": 0}

    if not sr.is_valid_id(sid):
        report.error("DATA_SOURCE_INCOMPLETE", f"source `{sid}` : identifiant invalide",
                     fix="snake_case, commence par une lettre — l'`id` nomme l'outil que voit le modèle",
                     location=loc)

    connector = str(src.get("connector") or "").strip()
    summary["connector"] = connector
    if connector not in sr.CONNECTORS:
        report.error("DATA_SOURCE_CONNECTOR_UNKNOWN", f"source `{sid}` : `connector: {connector or '<absent>'}` inconnu",
                     fix=f"choisir parmi {list(sr.CONNECTORS)}", location=loc)
        return summary

    required = sr.SOURCE_COMMON_REQUIRED + sr.SOURCE_CONNECTOR_REQUIRED[connector]
    missing = [k for k in required if not str(src.get(k) or "").strip()]
    if missing:
        report.error("DATA_SOURCE_INCOMPLETE", f"source `{sid}` ({connector}) : clé(s) obligatoire(s) absente(s) — {missing}",
                     fix=f"compléter : {', '.join(required)}", location=loc)

    unknown = sorted(set(src) - sr.allowed_keys(src))
    if unknown:
        report.error(
            "DATA_SOURCE_UNKNOWN_KEY",
            f"source `{sid}` ({connector}) : clé(s) inconnue(s) {unknown}",
            fix=f"grammaire close — clés admises : {', '.join(sorted(sr.allowed_keys(src)))}. "
                "Une faute de frappe sur `pii` ou `free_text` désactive un tag de sûreté sans rien signaler",
            location=loc,
        )

    description = str(src.get("description") or "").strip()
    if len(description) < sr.MIN_DESCRIPTION_CHARS:
        report.error(
            "DATA_SOURCE_DESCRIPTION_TOO_SHORT",
            f"source `{sid}` : description de {len(description)} caractères (minimum {sr.MIN_DESCRIPTION_CHARS})",
            fix="la description EST la description de l'outil vue par le modèle : dire quand utiliser, "
                "quand ne pas utiliser, ce qui est retourné, les unités, le fuseau et la fraîcheur",
            location=loc,
        )

    store_id = str(src.get("store") or "").strip()
    store = registry.stores.get(store_id)
    if store_id and store is None:
        report.error("DATA_STORE_UNKNOWN", f"source `{sid}` : `store: {store_id}` non déclaré",
                     fix="déclarer le store dans `Stores[]` ou dans un manifeste — "
                         "l'adresse et les identifiants vivent dans le store, jamais dans la source",
                     location=loc)

    tagged = _check_tags(sid, src, loc, report)
    summary["trust"] = sr.source_trust(src)
    if summary["trust"] == "trusted" and connector in sr.UNTRUSTED_BY_DEFAULT:
        report.warn(
            "DATA_SOURCE_TRUST_OPTIMISTIC",
            f"source `{sid}` ({connector}) : `trust: trusted` déclaré explicitement sur une source distante",
            fix="une réponse d'API ou d'outil MCP est écrite par un tiers ; la traiter comme `untrusted` "
                "(défaut) sauf justification écrite dans la description — cf. P8",
            location=loc,
        )

    if connector == "file" and store is not None:
        summary.update(_check_file_source(root, sid, src, store, section, loc, report))
    elif connector == "mcp" and store is not None:
        _check_mcp_source(root, sid, src, store, registry, loc, report)

    _check_frozen_schema(root, sid, src, tagged, loc, report, summary)
    return summary


def _check_tags(sid: str, src: dict[str, Any], loc: str, report: Report) -> dict[str, list[str]]:
    tagged: dict[str, list[str]] = {}
    for key in sr.FIELD_TAGS:
        value = src.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            report.error("DATA_SOURCE_UNKNOWN_KEY", f"source `{sid}` : `{key}` doit être une liste de champs",
                         fix=f"`{key}: [champ1, champ2]`", location=loc)
            continue
        tagged[key] = [str(v).strip() for v in value]

    date_field = str(src.get("date_field") or "").strip()
    if date_field:
        known = set(tagged.get("ranges", [])) | set(tagged.get("filters", []))
        if date_field not in known:
            report.warn(
                "DATA_SOURCE_DATE_FIELD_UNQUERYABLE",
                f"source `{sid}` : `date_field: {date_field}` n'est ni dans `ranges` ni dans `filters`",
                fix=f"ajouter `{date_field}` à `ranges` — sinon aucune question « depuis N jours » n'est filtrable",
                location=loc,
            )
    else:
        report.warn(
            "DATA_SOURCE_DATE_FIELD_MISSING",
            f"source `{sid}` : aucun `date_field`",
            fix="déclarer le champ qui fait foi pour la fraîcheur et les questions temporelles ; "
                "sans lui, `as_of` ne peut pas être calculé et l'agent répond sur un instantané qu'il ignore",
            location=loc,
        )

    if not src.get("free_text"):
        report.warn(
            "DATA_TEXT_FIELD_UNTAGGED",
            f"source `{sid}` : aucun champ `free_text`",
            fix="si la source contient un champ rempli par un tiers (commentaire client, libellé transporteur, "
                "corps de ticket), le déclarer : il rend l'outil `untrusted` et déclenche la suite d'injection (P8)",
            location=loc,
        )
    return tagged


def _check_file_source(root: Path, sid: str, src: dict[str, Any], store: dict[str, Any],
                       section: dict[str, Any], loc: str, report: Report) -> dict[str, Any]:
    out: dict[str, Any] = {}
    fmt = str(src.get("format") or "").strip().lower()
    out["format"] = fmt
    if fmt not in sr.FILE_FORMATS:
        report.error("DATA_SOURCE_FORMAT_UNKNOWN", f"source `{sid}` : `format: {fmt or '<absent>'}` inconnu",
                     fix=f"choisir parmi {list(sr.FILE_FORMATS)}", location=loc)
    elif fmt in sr.FORMATS_NEEDING_LIB:
        report.warn(
            "DATA_SOURCE_FORMAT_NEEDS_LIB",
            f"source `{sid}` : `format: {fmt}` exige `{sr.FORMATS_NEEDING_LIB[fmt]}`",
            fix=f"épingler `{sr.FORMATS_NEEDING_LIB[fmt]}` dans le `.libs.json` du framework actif — "
                "le parseur fait partie du comportement observable, donc de l'épinglage des evals",
            location=loc,
        )

    glob = str(src.get("glob") or "").strip()
    out["glob"] = glob
    if not glob:
        return out
    if glob.startswith(("/", "\\")) or ".." in Path(glob).parts:
        report.error(
            "DATA_SOURCE_PATH_ESCAPE",
            f"source `{sid}` : `glob: {glob}` sort de la racine du store (absolu ou `..`)",
            fix="un glob est toujours relatif à la racine du store — la racine est la frontière",
            location=loc,
        )
        return out

    kind = str(store.get("kind") or "")
    if kind not in sr.LOCAL_KINDS:
        return out                                  # store distant : pas de sonde disque

    declared_root = str(store.get("root") or "").strip()
    candidate = Path(declared_root)
    base = candidate if (candidate.is_absolute() or declared_root.startswith(("//", "\\\\"))) else (root / candidate)
    try:
        store_root = base.resolve()
    except OSError:
        return out
    if not store_root.is_dir():
        return out                                  # déjà signalé par `_probe_root`

    try:
        matches = sorted(p for p in store_root.glob(glob) if p.is_file())
    except (OSError, ValueError) as exc:
        report.error("DATA_SOURCE_GLOB_INVALID", f"source `{sid}` : glob `{glob}` illisible ({exc.__class__.__name__})",
                     fix="corriger le motif", location=loc)
        return out

    if not matches:
        report.error(
            "DATA_SOURCE_EMPTY",
            f"source `{sid}` : `{glob}` ne résout aucun fichier sous {paths.rel(root, store_root)}",
            fix="déposer les exports, ou corriger le glob — une source vide produit un agent qui répond "
                "« je ne trouve pas » à tout, sans jamais dire que la source est vide",
            location=loc,
        )
        return out

    total = largest = 0
    symlinks: list[str] = []
    escaped: list[str] = []
    for path in matches:
        if path.is_symlink():
            symlinks.append(path.name)
            continue
        try:
            if not path.resolve().is_relative_to(store_root):
                escaped.append(path.name)
                continue
            size = path.stat().st_size
        except OSError:
            continue
        total += size
        largest = max(largest, size)

    if symlinks:
        report.error(
            "DATA_SOURCE_PATH_ESCAPE",
            f"source `{sid}` : {len(symlinks)} lien(s) symbolique(s) dans la sélection ({symlinks[:3]})",
            fix="`SYMLINK_FOLLOW` est interdit par l'enveloppe : retirer les liens de la racine du store",
            location=paths.rel(root, store_root),
        )
    if escaped:
        report.error(
            "DATA_SOURCE_PATH_ESCAPE",
            f"source `{sid}` : {len(escaped)} fichier(s) résolvent hors de la racine du store ({escaped[:3]})",
            fix="la racine est la frontière ; déplacer les fichiers sous `root`",
            location=paths.rel(root, store_root),
        )

    cap = _positive_int(section.get("SourceMaxObjectBytes"))
    if cap and largest > cap:
        report.error(
            "DATA_SOURCE_FILE_TOO_LARGE",
            f"source `{sid}` : un fichier de {largest} octets dépasse `SourceMaxObjectBytes` ({cap})",
            fix="découper l'export, ou passer en base — l'index est construit en mémoire au démarrage",
            location=loc,
        )

    out.update({"files": len(matches), "bytes": total, "largestFileBytes": largest})
    return out


def _check_mcp_source(root: Path, sid: str, src: dict[str, Any], store: dict[str, Any],
                      registry: sr.Registry, loc: str, report: Report) -> None:
    """Un serveur MCP propose, le contrat dispose — et l'allowlist tranche."""
    server = str(store.get("server") or "").strip()
    tool = str(src.get("tool") or "").strip()

    declared = read_stack_section_kv(root, "Active Tools & Integrations").get("MCPServers") or []
    by_name = {str(s.get("name") or "").strip(): s for s in declared if isinstance(s, dict)}

    if server and server not in by_name and server not in registry.mcp_servers:
        report.error(
            "DATA_MCP_SERVER_UNKNOWN",
            f"source `{sid}` : le store cite le serveur MCP `{server}`, introuvable dans `MCPServers[]` "
            "ni dans un manifeste `mcp-config`",
            fix="déclarer le serveur dans `## Active Tools & Integrations → MCPServers[]`, "
                "ou ajouter sa configuration à `SourceManifests` avec `kind: mcp-config`",
            location=loc,
        )
        return

    conf = by_name.get(server)
    if conf:
        allowlist = conf.get("tools_allowlist")
        if isinstance(allowlist, list) and allowlist and tool not in [str(t).strip() for t in allowlist]:
            report.error(
                "DATA_MCP_TOOL_NOT_ALLOWLISTED",
                f"source `{sid}` : l'outil `{tool}` n'est pas dans `tools_allowlist` du serveur `{server}`",
                fix=f"ajouter `{tool}` à l'allowlist du serveur, ou retirer la source — "
                    "un serveur annonce N outils, l'application n'en câble que ceux qu'elle a nommés",
                location=loc,
            )
        if str(conf.get("trust") or "").strip().lower() == "untrusted" and sr.source_trust(src) == "trusted":
            report.error(
                "DATA_SOURCE_TRUST_OPTIMISTIC",
                f"source `{sid}` : `trust: trusted` alors que le serveur `{server}` est déclaré `untrusted`",
                fix="une source ne peut pas être plus confiante que son serveur : retirer `trust` "
                    "pour hériter du défaut `untrusted`",
                location=loc,
            )


def _check_frozen_schema(root: Path, sid: str, src: dict[str, Any], tagged: dict[str, list[str]],
                         loc: str, report: Report, summary: dict[str, Any]) -> None:
    """Le schéma figé fait foi — pour un CSV, un XLSX ou une réponse d'API autant que pour un JSON."""
    declared = str(src.get("schema") or "").strip()
    spath = (root / declared) if declared else schema_path(root, sid)
    if not spath.is_file():
        report.error(
            "DATA_SOURCE_SCHEMA_MISSING",
            f"source `{sid}` : aucun schéma figé ({paths.rel(root, spath)})",
            fix=f"générer puis relire : `gen_source_tools.py --infer --source {sid}` — "
                "ni un CSV, ni un XLSX, ni une réponse d'API n'ont de schéma ; sans épinglage, "
                "une colonne qui change de type change l'agent en silence",
            location=loc,
        )
        return

    try:
        schema = json.loads(markdown_io.read_text(spath))
    except ValueError as exc:
        report.error("DATA_SOURCE_SCHEMA_MALFORMED", f"source `{sid}` : schéma figé illisible ({exc.__class__.__name__})",
                     fix="régénérer le schéma", location=paths.rel(root, spath))
        return

    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        report.error("DATA_SOURCE_SCHEMA_MALFORMED", f"source `{sid}` : le schéma figé n'a pas d'objet `properties`",
                     fix="un schéma de source est un JSON Schema d'objet", location=paths.rel(root, spath))
        return

    summary["schemaFields"] = len(props)
    declared_fields: set[str] = set()
    for names in tagged.values():
        declared_fields.update(names)
    date_field = str(src.get("date_field") or "").strip()
    if date_field:
        declared_fields.add(date_field)
    key = str(src.get("key") or "").strip()
    if key:
        declared_fields.add(key)

    absent = sorted(f for f in declared_fields if f and f not in props)
    if absent:
        report.error(
            "DATA_SOURCE_FIELD_UNDECLARED",
            f"source `{sid}` : champ(s) déclaré(s) dans la source mais absent(s) du schéma figé — {absent}",
            fix="le schéma figé fait foi : corriger la déclaration, ou ré-inférer le schéma si la donnée a changé",
            location=loc,
        )


# ---------------------------------------------------------------------------
# `declared-sources` : l'assemblage
# ---------------------------------------------------------------------------
def check_declared_sources(root: Path, report: Report, only_source: str | None = None) -> dict[str, Any]:
    section = read_stack_section_kv(root, "Active Data Sources")
    if not section:
        report.error(
            "DATA_SOURCES_MISSING",
            f"`dataaccess/{DECLARED}` est actif mais `## Active Data Sources` est absent de STACK.md",
            fix="déclarer `Stores[]`, `Sources[]` (ou `SourceManifests[]`) et l'enveloppe `Source*` — "
                f"cf. .sdda/stacks/dataaccess/{DECLARED}.md §3",
            location="workspace/stack/STACK.md",
        )
        return {}

    db_type = str(read_stack_section_kv(root, "Active Data Access").get("DatabaseType") or "none").strip().lower()
    if db_type not in ("none", ""):
        report.error(
            "DATA_SOURCE_DB_CONFLICT",
            f"`DatabaseType: {db_type}` avec `dataaccess/{DECLARED}` actif",
            fix=f"`{DECLARED}` n'est pas une base : mettre `DatabaseType: none`, ou activer une stack SQL. "
                "Les deux à la fois produisent deux surfaces de données dont une seule est vérifiée",
            location="workspace/stack/STACK.md",
        )

    registry = sr.load_registry(root, section)
    for problem in registry.problems:
        emit = report.error if problem.severity == "error" else report.warn
        emit(problem.cls, problem.message, problem.fix, problem.location or SECTION)

    if not registry.sources:
        report.error(
            "DATA_SOURCES_MISSING",
            "aucune source déclarée — ni dans `Sources[]`, ni dans les manifestes",
            fix="ajouter au moins une source ; une stack d'accès sans surface n'a pas de raison d'être active",
            location=SECTION,
        )

    envelope = check_envelope(section, sorted(registry.stores), sorted(registry.sources), report)
    secrets = check_secrets(root, section, registry, report)

    store_summaries = [check_store(root, sid, store, envelope, registry, report)
                       for sid, store in sorted(registry.stores.items())]

    wanted = sorted(registry.sources)
    if only_source:
        if only_source not in registry.sources:
            report.error("DATA_SOURCE_UNKNOWN", f"source `{only_source}` non déclarée",
                         fix="vérifier l'`id` dans `## Active Data Sources` ou dans les manifestes",
                         location=SECTION)
            return {}
        wanted = [only_source]

    source_summaries = [check_source(root, sid, registry.sources[sid], registry, section, report)
                        for sid in wanted]

    return {
        "strategy": DECLARED,
        "manifests": registry.manifests,
        "secrets": secrets,
        "envelope": envelope,
        "stores": store_summaries,
        "sources": source_summaries,
        "totalFiles": sum(s.get("files", 0) for s in source_summaries),
        "totalBytes": sum(s.get("bytes", 0) for s in source_summaries),
    }


# ---------------------------------------------------------------------------
# Stratégies SQL et `none`
# ---------------------------------------------------------------------------
def check_sql(root: Path, strategy: str, report: Report) -> dict[str, Any]:
    section = read_stack_section_kv(root, "Active Data Access")
    loc = "workspace/stack/STACK.md ## Active Data Access"

    db_type = str(section.get("DatabaseType") or "none").strip().lower()
    if db_type in ("none", ""):
        report.error(
            "DATA_ACCESS_INCONSISTENT",
            f"`dataaccess/{strategy}` est actif mais `DatabaseType: none`",
            fix=f"déclarer la base, ou activer `dataaccess/none.md` / `dataaccess/{DECLARED}.md`",
            location=loc,
        )

    role = str(section.get("DbAgentRole") or "").strip().lower()
    if role not in ("readonly", "scoped-write", "full"):
        report.error("DATA_ACCESS_ENVELOPE_MISSING", f"`DbAgentRole: {role or '<absent>'}` invalide",
                     fix="readonly | scoped-write | full", location=loc)
    elif role == "full":
        report.warn("DATA_ACCESS_ADR_REQUIRED", "`DbAgentRole: full` exige un ADR explicite",
                    fix="écrire l'ADR dans workspace/feats/decisions/, ou restreindre le rôle", location=loc)

    for key in ("DbStatementTimeoutMs", "DbMaxRowsReturned"):
        if _positive_int(section.get(key)) is None:
            report.error("DATA_ACCESS_ENVELOPE_MISSING", f"`{key}` absent ou non borné",
                         fix=f"déclarer `{key}` avec un entier > 0", location=loc)

    if not section.get("DbAllowedSchemas"):
        report.error("DATA_ACCESS_ENVELOPE_MISSING", "`DbAllowedSchemas` absent",
                     fix="allowlist de schémas, jamais une denylist", location=loc)
    if role != "readonly" and not section.get("DbForbiddenStatements"):
        report.error("DATA_ACCESS_ENVELOPE_MISSING",
                     f"`DbForbiddenStatements` absent alors que le rôle est `{role}`",
                     fix="lister les instructions interdites ; elles sont vérifiées sur l'AST", location=loc)

    return {"strategy": strategy, "databaseType": db_type, "envelope": {"role": role}}


def check_none(root: Path, report: Report) -> dict[str, Any]:
    section = read_stack_section_kv(root, "Active Data Access")
    db_type = str(section.get("DatabaseType") or "none").strip().lower()
    if db_type not in ("none", ""):
        report.error(
            "DATA_ACCESS_INCONSISTENT",
            f"`dataaccess/none` est actif mais `DatabaseType: {db_type}`",
            fix="mettre `DatabaseType: none`, ou activer la stack d'accès qui correspond",
            location="workspace/stack/STACK.md",
        )
    sources_section = read_stack_section_kv(root, "Active Data Sources")
    declared = (sources_section.get("Sources") or []) + (sources_section.get("SourceManifests") or [])
    if declared:
        report.error(
            "DATA_ACCESS_INCONSISTENT",
            f"`dataaccess/none` est actif mais `## Active Data Sources` déclare {len(declared)} entrée(s)",
            fix=f"activer `dataaccess/{DECLARED}.md`, ou retirer les sources",
            location="workspace/stack/STACK.md",
        )
    return {"strategy": "none"}


# ---------------------------------------------------------------------------
# Confrontation à l'IR
# ---------------------------------------------------------------------------
def check_against_ir(root: Path, strategy: str, mission: int | str | None, report: Report) -> list[dict[str, Any]]:
    """L'IR décrit-il la même stratégie que STACK.md, avec une enveloppe bornée ?"""
    ir_files = sorted(paths.ir_dir(root).glob("*-system.ir.json"))
    if mission is not None:
        target = paths.ir_path(root, mission)
        ir_files = [target] if target.is_file() else []
    if not ir_files:
        return []

    out: list[dict[str, Any]] = []
    for ir_file in ir_files:
        try:
            ir = json.loads(markdown_io.read_text(ir_file))
        except ValueError:
            report.warn("IR_MALFORMED", f"IR illisible : {paths.rel(root, ir_file)}",
                        fix="recompiler l'IR (`/sdda-topology --recompile-only`)")
            continue
        entries = ir.get("dataAccess") or []
        loc = paths.rel(root, ir_file)

        if strategy == "none" and entries:
            report.error(
                "DATA_ACCESS_INCONSISTENT",
                f"STACK.md déclare `dataaccess/none` mais l'IR porte {len(entries)} entrée(s) `dataAccess[]`",
                fix="recompiler l'IR après changement de stack, ou corriger STACK.md",
                location=loc,
            )
        for entry in entries:
            eid = entry.get("id", "?")
            # La stratégie est un COMPOSANT : depuis la scission intent/binding
            # elle vit dans `binding`, avec les autres choix de réalisation.
            # Hors de `binding`, l'entrée ne dit plus que ce qui s'exige et se
            # mesure — qui est exposé, et sous quelle enveloppe.
            entry_strategy = (entry.get("binding") or {}).get("strategy")
            if strategy and strategy != "none" and entry_strategy != strategy:
                report.error(
                    "DATA_ACCESS_INCONSISTENT",
                    f"accès `{eid}` : l'IR déclare `{entry_strategy}`, STACK.md `{strategy}`",
                    fix="recompiler l'IR depuis les contrats à jour",
                    location=loc,
                )
            envelope = entry.get("envelope") or {}
            missing = [k for k in ("role", "statementTimeoutMs", "maxRows", "schemas", "forbidden") if not envelope.get(k)]
            if missing:
                report.error(
                    "DATA_ACCESS_ENVELOPE_MISSING",
                    f"accès `{eid}` : enveloppe incomplète dans l'IR — {missing} absent(s)",
                    fix="une borne absente est une borne infinie ; compléter l'enveloppe du contrat puis recompiler",
                    location=loc,
                )
            if entry_strategy == DECLARED and not envelope.get("egressAllowlist"):
                remote = [s for s in (entry.get("stores") or [])]
                if remote:
                    report.warn(
                        "DATA_EGRESS_UNDECLARED",
                        f"accès `{eid}` : aucune `egressAllowlist` dans l'IR alors que des stores sont déclarés",
                        fix="projeter `SourceEgressAllowlist` dans l'enveloppe du contrat puis recompiler",
                        location=loc,
                    )
            out.append({"id": eid, "strategy": entry_strategy, "irFile": loc})
    return out


# ---------------------------------------------------------------------------
# Entrée
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="DATA ACCESS — enveloppe de sûreté et cohérence STACK.md / manifestes / disque / IR (G3)")
    p.add_argument("--mission", default=None, help="numéro de mission ; défaut : tous les IR compilés")
    p.add_argument("--source", default=None, help="ne vérifier qu'une source déclarée (par son `id`)")
    add_common_args(p)
    return p


def run(root: Path, mission: int | str | None = None, only_source: str | None = None) -> Report:
    report = Report(name="DATA-ACCESS", target=str(root))

    stack_path = paths.stack_md_path(root)
    if not stack_path.is_file():
        report.error("STACK_MISSING", f"STACK.md introuvable ({paths.rel(root, stack_path)})",
                     fix="lancer `python bootstrap.py`")
        return report

    strategy = active_strategy(root)
    if not strategy:
        report.error(
            "STACK_MALFORMED",
            "`## Active Data Access` ne désigne pas exactement une stack",
            fix="activer exactement une ligne ` - .sdda/stacks/dataaccess/<nom>.md`",
            location="workspace/stack/STACK.md",
        )
        return report
    if "|" in strategy:
        report.error("STACK_MALFORMED", f"`## Active Data Access` active plusieurs stacks : {strategy}",
                     fix="exactement une", location="workspace/stack/STACK.md")
        return report

    report.data["strategy"] = strategy

    if strategy == DECLARED:
        report.data.update(check_declared_sources(root, report, only_source=only_source))
    elif strategy == "none":
        report.data.update(check_none(root, report))
    elif strategy in SQL_STRATEGIES:
        report.data.update(check_sql(root, strategy, report))
    else:
        report.warn(
            "DATA_ACCESS_STRATEGY_UNKNOWN",
            f"stack `dataaccess/{strategy}` inconnue de ce validateur — enveloppe non vérifiée",
            fix="ajouter la stratégie à SQL_STRATEGIES, ou lui écrire son contrôle",
            location="workspace/stack/STACK.md",
        )

    report.data["ir"] = check_against_ir(root, strategy, mission, report)
    return report


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = run(root, mission=args.mission, only_source=args.source)

    if not args.no_report:
        try:
            write_gate_report(root, "G3", args.mission or "stack", report, pinned={}, part="dataaccess")
        except OSError:
            pass
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
