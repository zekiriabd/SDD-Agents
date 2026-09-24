#!/usr/bin/env python3
"""L'enveloppe d'accès aux données, côté CODE — le pendant de `validate_data_access`.

`validate_data_access.py` vérifie que l'enveloppe est DÉCLARÉE : STACK.md,
manifestes, disque et IR disent la même chose, et chaque borne a une valeur.
Rien ne vérifiait qu'elle était IMPLÉMENTÉE. Un contrat qui promet `readonly`,
5 000 ms et 500 lignes, et un `data/` qui ouvre la connexion applicative
habituelle sans `statement_timeout` ni `LIMIT`, passaient ensemble au vert :
la promesse était vérifiée, pas sa tenue. Ce script lit `workspace/src/{App}/data/`
et cherche, pour chaque entrée `dataAccess[]` de l'IR, la matérialisation de
chaque clé de l'enveloppe.

Analyse STATIQUE (module `ast` pour le Python, lecture des `.sql`), 0 token,
rien n'est importé ni exécuté. Ce qu'elle établit, par stratégie :

    SQL (view-per-agent, repository-tools, text-to-sql…)
      role                rôle lecture seule posé dans le code (`transaction_read_only`,
                          `SET ROLE`, `readonly=True`…)
      statementTimeoutMs  `statement_timeout` (ou équivalent) positionné
      maxRows             `LIMIT`/`TOP`/`FETCH FIRST` ou plafond nommé `max_rows`
      schemas             `search_path` ou chaque schéma de l'allowlist nommé ;
                          aucune vue créée hors allowlist
      forbidden           un PARSER SQL importé (sqlglot, pglast, sqlparse…) ;
                          une regex sur DROP/DELETE est refusée — elle se contourne
      logging             span de requête (`db_query`, `sdda.data.query`)
      identité            `set_config`/`SESSION_CONTEXT` côté session, et le filtre
                          DANS chaque vue (`current_setting(...)`), jamais un
                          paramètre que le modèle remplit
      text-to-sql         `EXPLAIN` préalable
    declared-sources
      l'enveloppe unique (`lookup_record`, `search_records`, `count_records`),
      l'identité tirée de `ctx.identity`, `read_timeout_ms`, `max_records_returned`,
      la résolution par le registre (allowlist), `ctx.emit` — et AUCUNE écriture
      de fichier sous `data/` (lecture seule par construction)

Pour toutes : SQL assemblé par f-string / concaténation / `.format` / `%` dans
un appel d'exécution (`[DATA_ACCESS_SQL_INTERPOLATED]`), `retry` sur un module
qui écrit (`[DATA_ACCESS_RETRY_ON_WRITE]`), secret ou chaîne de connexion en
clair (motifs de `scan_secrets`, `[SECRET_LEAK]` — la valeur n'est jamais
recopiée).

Ce que le script ne prétend pas : trouver `statement_timeout` dans une chaîne
ne prouve pas que la chaîne est exécutée, ni que le rôle base est réellement
restreint. C'est une preuve de PRÉSENCE, pas d'effet ; l'effet se prouve par
les tests L2 de `qa-tests` et le smoke `--ping` de la stack. Ce qui est absent,
en revanche, est prouvé absent.

Usage :
    python .sdda/sdda.py validate-envelope --mission 1
    python .sdda/sdda.py validate-envelope --mission 1 --src workspace/src/SupportAssistant/data --json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import eval_reports, markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.layered_config import app_name  # noqa: E402
from sdda_lib.runtime_io import atomic_write_json, now_iso  # noqa: E402
from sdda_scripts import scan_secrets, validate_data_access  # noqa: E402
from sdda_scripts._common import add_common_args, finish, resolve_root  # noqa: E402

DECLARED = validate_data_access.DECLARED

#: Parsers SQL qui produisent un arbre : c'est sur lui que `forbidden` se vérifie.
AST_PARSERS = frozenset({"sqlglot", "pglast", "sqlparse", "sqlfluff", "sqloxide", "mo_sql_parsing"})
RETRY_LIBS = frozenset({"tenacity", "backoff", "retrying"})

#: Indices de matérialisation, par clé — comparés en minuscules sur les chaînes
#: littérales et les identifiants du code.
ROLE_HINTS = ("transaction_read_only", "read only", "set role", "applicationintent=readonly",
              "default_transaction_read_only", "readonly_role")
TIMEOUT_HINTS = ("statement_timeout", "lock_timeout", "query_timeout", "commandtimeout", "max_execution_time")
LOGGING_HINTS = ("db_query", "sdda.data.query", "data_query", "data.query")
IDENTITY_SESSION_HINTS = ("set_config", "sp_set_session_context", "session_context", "dbms_session", "set_identity")
VIEW_IDENTITY_HINTS = ("current_setting(", "session_context(", "sys_context(")

_LIMIT_RE = re.compile(r"\blimit\b|\bfetch\s+first\b|\btop\s*\(?\s*[\d{%:$]")
_SQL_WORD_RE = re.compile(r"\b(select|insert|update|delete|with|merge)\b", re.I)
_WRITE_SQL_RE = re.compile(r"\binsert\s+into\b|\bupdate\s+\w+\s+set\b|\bdelete\s+from\b|\bmerge\s+into\b", re.I)
_GUARD_WORD_RE = re.compile(r"drop|delete|insert|update|truncate|alter|grant", re.I)
_SELECT_STAR_RE = re.compile(r"\bselect\s+(?:distinct\s+)?\*", re.I)
_CREATE_VIEW_RE = re.compile(r"create\s+(?:or\s+replace\s+)?(?:materialized\s+)?view\s+(?:if\s+not\s+exists\s+)?([\w\"\[\]]+)\.", re.I)
_SQL_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)

EXEC_CALLS = frozenset({"execute", "executemany", "executescript", "exec_driver_sql", "fetch", "fetchrow",
                        "fetchval", "text", "raw", "query", "read_sql", "run_view_query"})
WRITE_CALLS = frozenset({"write_text", "write_bytes", "unlink", "rmdir", "remove", "rmtree", "rename",
                         "replace", "move", "touch", "mkdir", "makedirs", "copyfile", "copy", "copy2"})
WRITE_MODULES = frozenset({"os", "shutil", "path", "p"})


# ---------------------------------------------------------------------------
# Faits du code
# ---------------------------------------------------------------------------
@dataclass
class Facts:
    """Ce que le code de `data/` contient, fichier par fichier — sans l'exécuter."""

    py_files: list[str] = field(default_factory=list)
    sql_files: list[str] = field(default_factory=list)
    strings: list[tuple[str, str]] = field(default_factory=list)      # (texte minuscule, fichier)
    names: dict[str, str] = field(default_factory=dict)               # identifiant minuscule -> 1er fichier
    imports: dict[str, str] = field(default_factory=dict)             # module racine -> fichier
    functions: dict[str, str] = field(default_factory=dict)
    readonly_kwargs: list[str] = field(default_factory=list)
    interpolated: list[str] = field(default_factory=list)             # "fichier:ligne"
    regex_guards: list[str] = field(default_factory=list)
    file_writes: list[str] = field(default_factory=list)
    retry_on_write: list[str] = field(default_factory=list)
    params: list[tuple[str, str, str]] = field(default_factory=list)  # (fichier:ligne, fonction/classe, nom)
    parse_errors: list[str] = field(default_factory=list)

    def find(self, hints: tuple[str, ...]) -> str | None:
        """Le premier fichier dont une chaîne ou un identifiant porte l'un des indices."""
        for text, where in self.strings:
            if any(h in text for h in hints):
                return where
        for name, where in self.names.items():
            if any(h.replace(" ", "_") in name for h in hints):
                return where
        return None

    def find_re(self, pattern: re.Pattern[str]) -> str | None:
        return next((where for text, where in self.strings if pattern.search(text)), None)


def _is_sqlish(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and _SQL_WORD_RE.search(n.value)
               for n in ast.walk(node))


def _interpolation(arg: ast.AST) -> bool:
    """Un argument SQL ASSEMBLÉ : f-string à valeurs, concaténation, `.format`, `%`."""
    if isinstance(arg, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in arg.values) and _is_sqlish(arg)
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Add, ast.Mod)):
        return _is_sqlish(arg)
    if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "format":
        return _is_sqlish(arg.func.value)
    return False


def _call_name(func: ast.AST) -> tuple[str, str]:
    """(objet, méthode) d'un appel : `os.remove` -> ('os', 'remove') ; `open` -> ('', 'open')."""
    if isinstance(func, ast.Attribute):
        owner = func.value.id if isinstance(func.value, ast.Name) else (func.value.attr if isinstance(func.value, ast.Attribute) else "")
        return owner.lower(), func.attr
    if isinstance(func, ast.Name):
        return "", func.id
    return "", ""


def _open_writes(node: ast.Call) -> bool:
    mode: ast.AST | None = node.args[1] if len(node.args) > 1 else None
    for kw in node.keywords:
        if kw.arg == "mode":
            mode = kw.value
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and bool(set(mode.value) & set("wax+"))


def scan_python(facts: Facts, rel: str, text: str) -> None:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        facts.parse_errors.append(f"{rel}:{exc.lineno}")
        return
    facts.py_files.append(rel)
    writes_sql = bool(_WRITE_SQL_RE.search(text))
    retry = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            facts.strings.append((node.value.lower(), rel))
        elif isinstance(node, ast.Name):
            facts.names.setdefault(node.id.lower(), rel)
        elif isinstance(node, ast.Attribute):
            facts.names.setdefault(node.attr.lower(), rel)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for m in mods:
                head = m.split(".", 1)[0]
                facts.imports.setdefault(head, rel)
                retry |= head in RETRY_LIBS
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts.functions.setdefault(node.name, rel)
            retry |= any("retry" in ast.unparse(d).lower() for d in node.decorator_list)
            for a in [*node.args.args, *node.args.kwonlyargs]:
                facts.params.append((f"{rel}:{node.lineno}", node.name, a.arg))
        elif isinstance(node, ast.ClassDef) and node.name.lower().endswith("input"):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    facts.params.append((f"{rel}:{stmt.lineno}", node.name, stmt.target.id))
        elif isinstance(node, ast.keyword) and node.arg in ("readonly", "read_only"):
            if isinstance(node.value, ast.Constant) and node.value.value is True:
                facts.readonly_kwargs.append(rel)
        if isinstance(node, ast.Call):
            owner, method = _call_name(node.func)
            if method in EXEC_CALLS and node.args and _interpolation(node.args[0]):
                facts.interpolated.append(f"{rel}:{node.lineno}")
            if owner == "re" and method in ("compile", "search", "match", "fullmatch", "findall", "sub") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and _GUARD_WORD_RE.search(first.value):
                    facts.regex_guards.append(f"{rel}:{node.lineno}")
            if method == "open" and _open_writes(node):
                facts.file_writes.append(f"{rel}:{node.lineno} open(mode écriture)")
            elif method in WRITE_CALLS and (owner in WRITE_MODULES or method in ("write_text", "write_bytes", "unlink", "rmtree", "touch")):
                facts.file_writes.append(f"{rel}:{node.lineno} {owner + '.' if owner else ''}{method}()")
    if retry and writes_sql:
        facts.retry_on_write.append(rel)


def collect(root: Path, data_dir: Path) -> tuple[Facts, dict[str, str]]:
    """(faits Python, {fichier .sql: texte sans commentaires}). Les tests ne sont pas du code servi."""
    facts = Facts()
    sql: dict[str, str] = {}
    for path in sorted(p for p in data_dir.rglob("*") if p.is_file()):
        parts = set(path.relative_to(data_dir).parts)
        if parts & {"tests", "__pycache__"} or path.name.startswith("test_"):
            continue
        rel = paths.rel(root, path)
        if path.suffix == ".py":
            scan_python(facts, rel, markdown_io.read_text(path))
        elif path.suffix == ".sql":
            facts.sql_files.append(rel)
            sql[rel] = _SQL_COMMENT_RE.sub(" ", markdown_io.read_text(path)).lower()
    return facts, sql


# ---------------------------------------------------------------------------
# Contrôles
# ---------------------------------------------------------------------------
@dataclass
class Check:
    key: str
    ok: bool
    evidence: str | None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "ok": self.ok, "evidence": self.evidence, **({"note": self.note} if self.note else {})}


def _missing(report: Report, eid: str, key: str, what: str, fix: str, loc: str) -> None:
    report.error("DATA_ACCESS_ENVELOPE_MISSING", f"accès `{eid}` : `{key}` déclaré, non matérialisé dans le code — {what}", fix, loc)


def check_sql(facts: Facts, sql: dict[str, str], entry: dict[str, Any], strategy: str, loc: str,
              report: Report) -> list[Check]:
    eid = str(entry.get("id") or strategy)
    env = entry.get("envelope") or {}
    checks: list[Check] = []

    role_ev = facts.find(ROLE_HINTS) or (facts.readonly_kwargs[0] if facts.readonly_kwargs else None)
    checks.append(Check("role", bool(role_ev), role_ev))
    if not role_ev:
        _missing(report, eid, "role", "aucun rôle lecture seule posé (`transaction_read_only`, `SET ROLE`, `readonly=True`)",
                 "ouvrir la session sur le rôle déclaré et le vérifier au démarrage (`SHOW transaction_read_only`) — un rôle promis n'est pas un rôle", loc)

    timeout_ev = facts.find(TIMEOUT_HINTS)
    checks.append(Check("statementTimeoutMs", bool(timeout_ev), timeout_ev))
    if not timeout_ev:
        _missing(report, eid, "statementTimeoutMs", "aucun `statement_timeout` positionné",
                 "`SET LOCAL statement_timeout` à l'ouverture de chaque transaction : c'est le SERVEUR qui coupe, pas le client", loc)

    rows_ev = facts.find_re(_LIMIT_RE) or facts.find(("max_rows", "maxrows"))
    checks.append(Check("maxRows", bool(rows_ev), rows_ev))
    if not rows_ev:
        _missing(report, eid, "maxRows", "aucun `LIMIT` ni plafond `max_rows`",
                 "réécrire chaque requête en `LIMIT maxRows + 1` et marquer `truncated` : un résultat partiel muet est une réponse fausse", loc)

    schemas = [str(s).lower() for s in env.get("schemas") or []]
    named = [s for s in schemas if any(s in text for text, _ in facts.strings) or any(s in body for body in sql.values())]
    schema_ev = facts.find(("search_path",)) or (named[0] if schemas and len(named) == len(schemas) else None)
    checks.append(Check("schemas", bool(schema_ev) or not schemas, schema_ev,
                        "" if schemas else "aucune allowlist dans l'IR — rien à confronter"))
    if schemas and not schema_ev:
        _missing(report, eid, "schemas", f"ni `search_path` ni l'allowlist {schemas} dans le code",
                 "fixer `search_path` sur l'allowlist à l'ouverture de session, et refuser toute requête qui en sort AVANT exécution", loc)
    for rel, body in sql.items():
        for schema in _CREATE_VIEW_RE.findall(body):
            if schemas and schema.strip('"[]') not in schemas:
                report.error("DATA_ACCESS_SCHEMA_OUTSIDE_ALLOWLIST", f"accès `{eid}` : `{rel}` crée une vue dans `{schema}`, hors allowlist {schemas}",
                             "créer la vue dans un schéma de l'allowlist (ex. `agent_views`), ou élargir l'allowlist par un ADR", rel)

    parser = sorted(set(facts.imports) & AST_PARSERS)
    checks.append(Check("forbidden", bool(parser), facts.imports[parser[0]] if parser else None,
                        f"parser : {parser[0]}" if parser else ""))
    if not parser:
        report.error("DATA_ACCESS_AST_MISSING", f"accès `{eid}` : aucun parser SQL importé sous data/ — `forbidden` {env.get('forbidden') or []} n'est vérifié sur aucun arbre",
                     "parser chaque requête (sqlglot, pglast…) et refuser si un nœud interdit apparaît ; pas de regex", loc)
    for where in facts.regex_guards:
        report.error("DATA_ACCESS_REGEX_GUARD", f"accès `{eid}` : `{where}` filtre des instructions interdites par regex",
                     "une regex se contourne (commentaire, casse, encodage) : remplacer par la vérification sur l'AST", where)

    log_ev = facts.find(LOGGING_HINTS)
    checks.append(Check("logging", bool(log_ev), log_ev))
    if not log_ev:
        _missing(report, eid, "logging", "aucun span de requête (`sdda.data.query`, `db_query`)",
                 "tracer chaque requête (span `sdda.data.query`, paramètres redigés) — sans journal, aucun post-mortem", loc)

    identity = str(env.get("identityFilter") or "").strip().lower()
    session_ev = facts.find(IDENTITY_SESSION_HINTS)
    views = {rel: body for rel, body in sql.items() if "/views/" in f"/{rel}"}
    unfiltered = [rel for rel, body in views.items()
                  if not any(h in body for h in VIEW_IDENTITY_HINTS) or (identity and identity not in body)]
    checks.append(Check("identity", bool(session_ev) and not unfiltered or not (identity or views), session_ev,
                        f"filtre `{identity}`" if identity else ""))
    if identity and not session_ev:
        report.error("DATA_ACCESS_FILTER_POST_GENERATION", f"accès `{eid}` : aucune identité posée en session (`set_config`, `SESSION_CONTEXT`) pour `{identity}`",
                     "poser l'identité de l'APPELANT dans la session depuis le contexte d'exécution, jamais depuis un argument du modèle", loc)
    for rel in unfiltered:
        emit = report.error if identity else report.warn
        emit("DATA_ACCESS_FILTER_POST_GENERATION", f"accès `{eid}` : la vue `{rel}` ne filtre pas sur l'identité de session"
             + (f" (`{identity}` = current_setting(...))" if identity else ""),
             "ajouter `WHERE <col> = current_setting('app.tenant_id', true)` DANS la vue — même en mono-tenant, c'est ce qui la rend fail-closed", rel)
    for rel, body in views.items():
        if _SELECT_STAR_RE.search(body):
            report.error("DATA_VIEW_SELECT_STAR", f"accès `{eid}` : `{rel}` contient `SELECT *`",
                         "lister les colonnes : un ajout de colonne en base changerait la surface exposée sans revue", rel)

    if strategy == "text-to-sql":
        explain_ev = facts.find(("explain",))
        checks.append(Check("explain", bool(explain_ev), explain_ev))
        if not explain_ev:
            _missing(report, eid, "text-to-sql", "aucun `EXPLAIN` préalable avec seuil de coût",
                     "DATA-ACCESS.md §1 : les neuf conditions en code, dont l'EXPLAIN avant exécution", loc)

    for where in facts.interpolated:
        report.error("DATA_ACCESS_SQL_INTERPOLATED", f"accès `{eid}` : `{where}` exécute un SQL assemblé (f-string, concaténation, format)",
                     "SQL figé et paramètres LIÉS ; le modèle fournit des valeurs, il n'assemble jamais une clause", where)
    return checks


def check_declared(facts: Facts, entry: dict[str, Any], loc: str, report: Report) -> list[Check]:
    eid = str(entry.get("id") or DECLARED)
    checks: list[Check] = []
    ops = [f for f in ("lookup_record", "search_records", "count_records") if f in facts.functions]
    checks.append(Check("envelope", len(ops) == 3, facts.functions.get("search_records"), f"opérations : {ops}"))
    if len(ops) < 3:
        _missing(report, eid, "envelope", f"opérations de l'enveloppe unique présentes : {ops}",
                 "régénérer le runtime (`gen-source-tools --write`) : tous les outils passent par la même porte", loc)
    for key, hints, what in (
        ("identity", ("identity",), "l'identité n'est pas lue depuis le contexte d'appel (`ctx.identity`)"),
        ("statementTimeoutMs", ("read_timeout_ms",), "aucun budget de lecture (`read_timeout_ms`)"),
        ("maxRows", ("max_records_returned",), "aucun plafond (`max_records_returned`)"),
        ("schemas", ("load_registry",), "aucune résolution par le registre — l'allowlist n'est pas appliquée"),
        ("logging", ("emit",), "aucun événement émis par appel (`ctx.emit`)"),
    ):
        ev = facts.find(hints)
        checks.append(Check(key, bool(ev), ev))
        if not ev:
            _missing(report, eid, key, what, "le runtime `data/` généré porte ces bornes : ne pas le réécrire à la main", loc)
    checks.append(Check("role", not facts.file_writes, None if facts.file_writes else "aucune écriture de fichier"))
    for where in facts.file_writes:
        report.error("DATA_ACCESS_WRITE_IN_READONLY", f"accès `{eid}` : `{where}` écrit sur disque sous data/ alors que `{DECLARED}` est en lecture seule",
                     "retirer l'écriture : un besoin d'écriture relève de `repository-tools`, avec idempotence et classe d'effet de bord", where)
    return checks


def identity_fields(root: Path, data_dir: Path, entry: dict[str, Any]) -> set[str]:
    """Champs d'identité : `identityFilter` de l'IR, et les `required_filter` du registre généré."""
    out = {str((entry.get("envelope") or {}).get("identityFilter") or "").strip()} - {""}
    registry = data_dir / "sources.json"
    if registry.is_file():
        try:
            data = json.loads(markdown_io.read_text(registry))
        except ValueError:
            data = {}
        sources = data.get("sources") if isinstance(data, dict) else None
        items = sources.values() if isinstance(sources, dict) else (sources or [])
        for src in items:
            if isinstance(src, dict):
                out |= {str(f) for f in src.get("required_filter") or []}
    return out


def check_common(root: Path, data_dir: Path, facts: Facts, entry: dict[str, Any], report: Report) -> None:
    eid = str(entry.get("id") or "data")
    idents = identity_fields(root, data_dir, entry)
    for where, owner, name in facts.params:
        exposed = "/tools/" in where or "/repositories/" in where
        if name in idents and exposed:
            report.error("DATA_ACCESS_FILTER_POST_GENERATION", f"accès `{eid}` : `{name}` est un paramètre de `{owner}` ({where}) — le modèle peut le remplir",
                         "retirer le paramètre : l'identité vient du contexte d'appel (session), jamais d'un argument d'outil", where)
    for rel in facts.retry_on_write:
        report.error("DATA_ACCESS_RETRY_ON_WRITE", f"accès `{eid}` : `{rel}` écrit en base et porte une politique de retry",
                     "`retry_policy: none` sur toute écriture non idempotente, jusque dans le driver ; sinon clé d'idempotence matérialisée", rel)
    for rel in facts.parse_errors:
        report.error("DATA_ACCESS_CODE_UNPARSABLE", f"`{rel}` ne s'analyse pas (SyntaxError) — rien n'y est vérifié",
                     "corriger la syntaxe : un fichier illisible par l'AST est un fichier dont l'enveloppe n'est pas prouvée", rel)


def scan_code_secrets(root: Path, data_dir: Path, report: Report) -> int:
    hits = 0
    for path in sorted(p for p in data_dir.rglob("*") if p.is_file() and p.suffix in (".py", ".sql", ".json", ".toml", ".ini", ".cfg")):
        rel = paths.rel(root, path)
        for number, line in enumerate(markdown_io.read_text(path).split("\n"), start=1):
            for label, pattern in scan_secrets.COMPILED:
                if pattern.search(line):
                    hits += 1
                    report.error("SECRET_LEAK", f"{rel}:{number} — {label} en clair dans le code d'accès aux données",
                                 "la connexion se construit depuis des NOMS de variables (`.env`), jamais une valeur commitée", rel)
    return hits


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def entries_of(root: Path, ir: dict[str, Any], report: Report) -> list[dict[str, Any]]:
    entries = [e for e in ir.get("dataAccess") or [] if isinstance(e, dict)]
    if entries:
        return entries
    strategy = validate_data_access.active_strategy(root)
    if strategy and strategy != "none" and "|" not in strategy:
        report.warn("DATA_ACCESS_INCONSISTENT", f"STACK.md active `dataaccess/{strategy}` mais l'IR ne porte aucune entrée `dataAccess[]` — "
                    "le code est vérifié sur la stratégie de STACK.md, l'enveloppe n'est confrontée à aucun contrat",
                    "écrire les contrats `{n}-data-*` (architect-data) puis recompiler l'IR", "workspace/stack/STACK.md")
        return [{"id": f"stack-{strategy}", "binding": {"strategy": strategy}, "envelope": {}}]
    return []


def run(root: Path, ir: dict[str, Any], data_dir: Path) -> tuple[Report, dict[str, Any]]:
    report = Report(name="ENVELOPE", target=str(ir.get("missionId") or root))
    payload: dict[str, Any] = {"missionId": ir.get("missionId"), "generatedAt": now_iso(),
                               "dataDir": paths.rel(root, data_dir), "entries": []}
    entries = entries_of(root, ir, report)
    if not entries:
        payload["applicable"] = False
        report.data["lines"] = ["enveloppe sans objet : aucune entrée `dataAccess[]`, `dataaccess/none`"]
        return report, payload
    payload["applicable"] = True
    loc = paths.rel(root, data_dir)
    if not data_dir.is_dir():
        report.error("DATA_ACCESS_ENVELOPE_MISSING", f"{len(entries)} accès déclaré(s) et aucun code sous `{loc}`",
                     "dev-data matérialise vues, repositories et enveloppe sous workspace/src/{App}/data/", loc)
        return report, payload

    facts, sql = collect(root, data_dir)
    payload["files"] = {"python": len(facts.py_files), "sql": len(facts.sql_files)}
    for entry in entries:
        strategy = str((entry.get("binding") or {}).get("strategy") or "")
        if strategy == DECLARED:
            checks = check_declared(facts, entry, loc, report)
        elif strategy in validate_data_access.SQL_STRATEGIES:
            checks = check_sql(facts, sql, entry, strategy, loc, report)
        else:
            report.warn("DATA_ACCESS_STRATEGY_UNKNOWN", f"accès `{entry.get('id')}` : stratégie `{strategy or '?'}` inconnue de ce contrôle",
                        "ajouter son contrôle ici : une stratégie non vérifiée n'est pas une stratégie sûre", loc)
            checks = []
        check_common(root, data_dir, facts, entry, report)
        payload["entries"].append({"id": entry.get("id"), "strategy": strategy, "checks": [c.to_dict() for c in checks]})
    payload["secretHits"] = scan_code_secrets(root, data_dir, report)
    return report, payload


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Enveloppe d'accès aux données, côté CODE : analyse statique de src/{App}/data/ contre dataAccess[] (0 token)")
    p.add_argument("--mission", type=int, default=None, help="numéro de MISSION ; défaut : l'unique IR compilé")
    p.add_argument("--src", type=Path, default=None, help="répertoire `data/` du livrable ; défaut : workspace/src/{App}/data")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="ENVELOPE", target=str(root))
    ir_file, why = eval_reports.find_ir_file(root, args.mission)
    if ir_file is None:
        report.error("IR_NOT_FOUND", why, "compiler l'IR : python .sdda/sdda.py ir-compiler --mission {n}", str(paths.ir_dir(root)))
        return finish(report, args)
    try:
        ir = json.loads(markdown_io.read_text(ir_file))
    except ValueError:
        report.error("IR_SCHEMA_INVALID", f"IR illisible : {paths.rel(root, ir_file)}", "recompiler l'IR", paths.rel(root, ir_file))
        return finish(report, args)
    data_dir = args.src if args.src else paths.app_src_root(root, app_name(root)) / "data"
    data_dir = data_dir if data_dir.is_absolute() else root / data_dir
    sub, payload = run(root, ir, data_dir)
    report.extend(sub)
    report.target = sub.target
    report.data.update(sub.data)
    if payload.get("applicable") and not args.no_report:
        out = paths.validation_dir(root) / f"envelope-{eval_reports.mission_number(ir) or 'system'}.json"
        payload["findings"] = {"errors": [f.to_dict() for f in report.errors], "warnings": [f.to_dict() for f in report.warnings]}
        atomic_write_json(out, payload)
        report.data["written"] = paths.rel(root, out)
    report.data["entries"] = payload.get("entries", [])
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
