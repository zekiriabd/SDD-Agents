"""L'enveloppe d'accès base lue hors Python — rôle, timeout, plafond, identité, SQL interpolé.

`data/` n'était analysé qu'en Python (`ast`) : l'enveloppe d'une application
C#, TypeScript ou JVM n'était pas regardée, et ses constats sortaient faux.
"""
from __future__ import annotations

import pytest

from sdda_scripts import validate_envelope as ve

SAFE = {
    "csharp": ('using Npgsql;\nusing Microsoft.SqlServer.TransactSql.ScriptDom;\nclass Db {\n'
               '  const string Role = "SET TRANSACTION READ ONLY; SET LOCAL statement_timeout = 2000";\n'
               '  const string Q = "SELECT id FROM agent_views.orders WHERE id = @id LIMIT 51";\n'
               '  void Log() { Span("sdda.data.query"); Session("set_config"); }\n}\n'),
    "typescript": ('import { Pool } from "pg";\nimport { Parser } from "node-sql-parser";\n'
                   'const role = "SET TRANSACTION READ ONLY; SET LOCAL statement_timeout = 2000";\n'
                   'const q = "SELECT id FROM agent_views.orders WHERE id = $1 LIMIT 51";\n'
                   'span("sdda.data.query"); session("set_config");\n'),
    "java": ('import net.sf.jsqlparser.parser.CCJSqlParserUtil;\nclass Db {\n'
             '  String role = "SET TRANSACTION READ ONLY; SET LOCAL statement_timeout = 2000";\n'
             '  String q = "SELECT id FROM agent_views.orders WHERE id = ? LIMIT 51";\n'
             '  void log() { span("sdda.data.query"); session("set_config"); }\n}\n'),
}
UNSAFE = {
    "csharp": 'var q = $"SELECT * FROM orders WHERE id = {id}";\n',
    "typescript": "const q = `SELECT * FROM orders WHERE id = ${id}`;\n",
    "kotlin": 'val q = "SELECT * FROM orders WHERE id = $id"\n',
    "java": 'String q = "SELECT * FROM orders WHERE id = " + id;\n',
}


@pytest.mark.parametrize("language", sorted(SAFE))
def test_the_envelope_facts_are_found_in_native_code(language: str) -> None:
    facts = ve.Facts()
    ve.scan_native(facts, f"data/Db.{language}", SAFE[language], language)
    assert facts.find(ve.ROLE_HINTS) and facts.find(ve.TIMEOUT_HINTS)
    assert facts.find(ve.LOGGING_HINTS) and facts.find(ve.IDENTITY_SESSION_HINTS)
    assert facts.find_re(ve._LIMIT_RE)
    assert set(facts.imports) & ve.AST_PARSERS, "le parser SQL importé est reconnu"
    assert facts.interpolated == []


@pytest.mark.parametrize("language", sorted(UNSAFE))
def test_interpolated_sql_is_caught_in_every_language(language: str) -> None:
    facts = ve.Facts()
    ve.scan_native(facts, "data/Db.x", UNSAFE[language], language)
    assert facts.interpolated == ["data/Db.x:1"]
