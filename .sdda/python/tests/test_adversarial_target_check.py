"""adversarial-target-check : une garde qui échoue FERMÉE.

Défendu ici : un outil à effet de bord non mocké bloque ; un dry-run n'est
admis que sur demande, et reste signalé ; une surface réseau sans endpoint de
bouclage bloque ; un nom de variable de production — dans STACK.md ou dans un
`.env` — bloque sans que sa valeur soit lue ; une base SQL sans connexion de
test nommée bloque ; un store distant bloque ; une garde qui plante refuse.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main  # type: ignore
from sdda_scripts import adversarial_target_check as atc
from sdda_scripts import ir_compiler

STACK = "workspace/stack/STACK.md"
FIXTURE = "workspace/pipeline/fixtures/tools/zendesk.jsonl"
PROOF = "workspace/.sys/.validation/adversarial-target-1.json"


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _edit(root: Path, old: str, new: str) -> None:
    p = root / STACK
    text = p.read_text(encoding="utf-8")
    assert old in text
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def _compiled(root: Path) -> Path:
    ir_compiler.main(["--root", str(root), "--mission", "1", "--no-report"])
    return root


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = _compiled(make_project(tmp_path))
    _write(r, FIXTURE, json.dumps({"tool": "zendesk_create_ticket", "result": {"ticket_id": "T-1"}}) + "\n")
    return r


def _check(root: Path, *extra: str) -> tuple[int, dict]:
    code, out = run_main(atc.main, ["--root", str(root), "--mission", "1", "--json", *extra])
    return code, json.loads(out)


def _classes(result: dict) -> set[str]:
    return {f["class"] for f in result["errors"]} | {f["class"] for f in result["warnings"]}


def test_an_isolated_cli_target_with_mocked_tools_is_safe(root: Path) -> None:
    code, result = _check(root)
    assert code == 0, result
    proof = json.loads((root / PROOF).read_text(encoding="utf-8"))
    assert proof["safe"] is True and proof["tools"] == [{"tool": "1-zendesk-create-ticket",
                                                          "sideEffectClass": "external-side-effect", "verdict": "mocked"}]


def test_a_side_effect_tool_without_mock_is_refused(tmp_path: Path) -> None:
    r = _compiled(make_project(tmp_path))
    code, result = _check(r)
    assert code == 1 and atc.CLS_UNSAFE in _classes(result)


def test_dry_run_is_admitted_only_on_request_and_stays_visible(tmp_path: Path) -> None:
    r = _compiled(make_project(tmp_path))
    code, result = _check(r, "--allow-dry-run")
    assert code == 0 and atc.CLS_DRY_RUN in _classes(result)


@pytest.mark.parametrize("endpoint,ok", [("http://127.0.0.1:8000", True), ("http://localhost:9000/run", True),
                                         ("http://10.0.0.5:8000", False), ("https://support.acme.com", False),
                                         ("http://0.0.0.0:8000", False)])
def test_the_endpoint_must_be_loopback(root: Path, endpoint: str, ok: bool) -> None:
    code, _ = _check(root, "--endpoint", endpoint)
    assert (code == 0) is ok


def test_a_network_surface_without_endpoint_is_refused(root: Path) -> None:
    _edit(root, " - .sdda/stacks/serving/cli.md", " - .sdda/stacks/serving/fastapi-sse.md")
    code, result = _check(root)
    assert code == 1 and atc.CLS_UNSAFE in _classes(result)
    assert _check(root, "--endpoint", "http://127.0.0.1:8000")[0] == 0


def test_a_production_variable_name_is_refused_without_reading_its_value(root: Path) -> None:
    _write(root, "workspace/assets/.env", "CRM_TOKEN_PROD=valeur-secrete-de-prod\n")
    code, result = _check(root)
    assert code == 1 and atc.CLS_UNSAFE in _classes(result)
    assert "valeur-secrete-de-prod" not in json.dumps(result)
    assert "valeur-secrete-de-prod" not in (root / PROOF).read_text(encoding="utf-8")


def test_a_sql_strategy_needs_a_test_connection_by_name(root: Path) -> None:
    _edit(root, " - .sdda/stacks/dataaccess/none.md\nDatabaseType: none",
          " - .sdda/stacks/dataaccess/view-per-agent.md\nDatabaseType: PostgreSql\n - DB_HOST: ${DB_HOST}\n - DB_NAME: ${DB_NAME}")
    code, result = _check(root)
    assert code == 1 and atc.CLS_UNSAFE in _classes(result)
    _edit(root, " - DB_HOST: ${DB_HOST}\n - DB_NAME: ${DB_NAME}", " - DB_HOST: ${DB_HOST_TEST}\n - DB_NAME: ${DB_NAME_TEST}")
    assert _check(root)[0] == 0


def test_a_remote_store_is_refused(tmp_path: Path) -> None:
    r = _compiled(make_project(tmp_path, "project_declared_sources"))
    _write(r, FIXTURE, json.dumps({"tool": "zendesk_create_ticket", "result": {}}) + "\n")
    code, result = _check(r)
    assert code == 1
    assert any("store" in f["message"] and f["class"] == atc.CLS_UNSAFE for f in result["errors"])


def test_a_crashing_check_refuses(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("déclaration illisible")
    monkeypatch.setattr(atc, "check_sources", boom)
    code, result = _check(root)
    assert code == 1 and atc.CLS_UNSAFE in _classes(result)


def test_without_ir_nothing_is_attacked(project: Path) -> None:
    code, out = run_main(atc.main, ["--root", str(project), "--mission", "1"])
    assert code == 1 and "IR_NOT_FOUND" in out
