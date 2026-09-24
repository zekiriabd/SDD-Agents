"""validate-envelope : l'enveloppe promise par le contrat est-elle dans le CODE ?

`validate_data_access` prouve que l'enveloppe est déclarée ; ce script prouve
qu'elle est écrite. Défendu ici : le runtime `declared-sources` généré passe ;
une écriture de fichier sous `data/` le fait tomber ; une implémentation SQL
complète passe, et chaque manque d'une implémentation SQL naïve est nommé par
sa classe — timeout absent, regex au lieu d'AST, SQL assemblé, identité en
paramètre, vue sans filtre de session, `SELECT *`, secret en clair.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main  # type: ignore
from sdda_lib import paths
from sdda_scripts import gen_source_tools as gst
from sdda_scripts import ir_compiler, validate_envelope

DATA = "workspace/src/SupportAssistant/data"

GOOD_ENVELOPE = '''
import sqlglot

FORBIDDEN = ("DROP", "DELETE")
MAX_ROWS = 500


async def run_view_query(*, view, filters, ctx, conn):
    await conn.execute("SET TRANSACTION READ ONLY")
    await conn.execute("SET LOCAL statement_timeout = 5000")
    await conn.execute("SET LOCAL search_path = agent_views")
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx.tenant_id)
    sql = "SELECT invoice_id, amount FROM agent_views.v_billing__invoices WHERE invoice_id = $1 ORDER BY invoice_id LIMIT $2"
    tree = sqlglot.parse_one(sql)
    with ctx.span("sdda.data.query"):
        return await conn.fetch(sql, filters["invoice_id"], MAX_ROWS + 1)
'''

GOOD_VIEW = """-- description de l'outil
CREATE OR REPLACE VIEW agent_views.v_billing__invoices AS
SELECT i.invoice_id, i.amount
FROM billing.invoices i
WHERE i.tenant_id = current_setting('app.tenant_id', true)::uuid;
"""

GOOD_TOOL = '''
from pydantic import BaseModel


class Input(BaseModel):
    invoice_id: str
'''


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _ir_with(root: Path, entries: list[dict]) -> None:
    ir_compiler.main(["--root", str(root), "--mission", "1", "--no-report"])
    path = paths.ir_path(root, 1)
    ir = json.loads(path.read_text(encoding="utf-8"))
    ir["dataAccess"] = entries
    path.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")


VIEW_ENTRY = {"id": "1-billing-views", "binding": {"strategy": "view-per-agent"}, "exposedTo": ["1-billing-specialist"],
              "envelope": {"role": "readonly", "statementTimeoutMs": 5000, "maxRows": 500, "schemas": ["agent_views"],
                           "forbidden": ["DROP", "DELETE"], "identityFilter": "tenant_id"}}
DECLARED_ENTRY = {"id": "1-sources", "binding": {"strategy": "declared-sources"}, "exposedTo": ["1-billing-specialist"],
                  "envelope": {"role": "readonly", "statementTimeoutMs": 5000, "maxRows": 200, "schemas": ["order_tracking"],
                               "forbidden": ["WRITE", "DELETE", "EXEC", "SYMLINK_FOLLOW", "UNDECLARED_EGRESS"]}}


def _run(root: Path) -> tuple[int, str, dict]:
    code, out = run_main(validate_envelope.main, ["--root", str(root), "--mission", "1", "--json"])
    return code, out, json.loads(out)


def _classes(result: dict) -> set[str]:
    return {f["class"] for f in result["errors"]} | {f["class"] for f in result["warnings"]}


@pytest.fixture
def view_project(tmp_path: Path) -> Path:
    root = make_project(tmp_path)
    _write(root, f"{DATA}/envelope.py", GOOD_ENVELOPE)
    _write(root, f"{DATA}/views/v_billing__invoices.sql", GOOD_VIEW)
    _write(root, f"{DATA}/tools/billing_invoices.py", GOOD_TOOL)
    _ir_with(root, [VIEW_ENTRY])
    return root


def test_without_ir_the_check_refuses(project: Path) -> None:
    code, out = run_main(validate_envelope.main, ["--root", str(project), "--mission", "1"])
    assert code == 1 and "IR_NOT_FOUND" in out


def test_no_data_access_is_not_applicable(project: Path) -> None:
    _ir_with(project, [])
    code, _, result = _run(project)
    assert code == 0 and result["ok"]
    assert not (project / "workspace/.sys/.validation/envelope-1.json").exists()


def test_declared_access_without_code_is_missing(project: Path) -> None:
    _ir_with(project, [VIEW_ENTRY])
    code, _, result = _run(project)
    assert code == 1 and "DATA_ACCESS_ENVELOPE_MISSING" in _classes(result)


def test_a_complete_sql_envelope_passes(view_project: Path) -> None:
    code, _, result = _run(view_project)
    assert code == 0, result
    written = json.loads((view_project / "workspace/.sys/.validation/envelope-1.json").read_text(encoding="utf-8"))
    checks = {c["key"]: c for c in written["entries"][0]["checks"]}
    assert all(c["ok"] for c in checks.values()), checks
    assert checks["forbidden"]["note"] == "parser : sqlglot"


def test_a_naive_sql_implementation_is_named_key_by_key(view_project: Path) -> None:
    _write(view_project, f"{DATA}/envelope.py", '''
import re

GUARD = re.compile(r"\\b(DROP|DELETE)\\b", re.I)


def run_query(conn, view, invoice_id, tenant_id):
    conn.execute(f"SELECT * FROM {view} WHERE invoice_id = '{invoice_id}'")

DSN = "postgresql://agent:hunter2secret@db.prod.internal:5432/billing"
''')
    _write(view_project, f"{DATA}/views/v_billing__invoices.sql",
           "CREATE VIEW billing.v_billing__invoices AS SELECT * FROM billing.invoices;")
    _write(view_project, f"{DATA}/tools/billing_invoices.py", GOOD_TOOL + "    tenant_id: str\n")
    code, _, result = _run(view_project)
    assert code == 1
    found = _classes(result)
    for cls in ("DATA_ACCESS_ENVELOPE_MISSING", "DATA_ACCESS_AST_MISSING", "DATA_ACCESS_REGEX_GUARD",
                "DATA_ACCESS_SQL_INTERPOLATED", "DATA_ACCESS_FILTER_POST_GENERATION", "DATA_VIEW_SELECT_STAR",
                "DATA_ACCESS_SCHEMA_OUTSIDE_ALLOWLIST", "SECRET_LEAK"):
        assert cls in found, cls
    assert "hunter2secret" not in json.dumps(result)          # la valeur n'est jamais recopiée
    missing = " ".join(f["message"] for f in result["errors"] if f["class"] == "DATA_ACCESS_ENVELOPE_MISSING")
    assert "statementTimeoutMs" in missing and "role" in missing and "logging" in missing


def test_retry_on_a_writing_module_is_refused(view_project: Path) -> None:
    _write(view_project, f"{DATA}/repositories/tickets/repo.py", '''
import tenacity


@tenacity.retry
def create(conn, ticket):
    conn.execute("INSERT INTO agent_views.tickets (id) VALUES ($1)", ticket)
''')
    code, _, result = _run(view_project)
    assert code == 1 and "DATA_ACCESS_RETRY_ON_WRITE" in _classes(result)


def test_the_generated_declared_sources_runtime_passes(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_declared_sources")
    assert gst.run(root, mode="write").ok
    _ir_with(root, [DECLARED_ENTRY])
    code, _, result = _run(root)
    assert code == 0, result


def test_a_file_write_under_declared_sources_is_refused(tmp_path: Path) -> None:
    root = make_project(tmp_path, "project_declared_sources")
    assert gst.run(root, mode="write").ok
    _write(root, f"{DATA}/cache.py", "def keep(path, text):\n    with open(path, 'w') as fh:\n        fh.write(text)\n")
    _ir_with(root, [DECLARED_ENTRY])
    code, _, result = _run(root)
    assert code == 1 and "DATA_ACCESS_WRITE_IN_READONLY" in _classes(result)
