"""Audit du 2026-09-25 — générateurs, couche données, secrets et migration (M7-M11, M15, M16, mineurs).

Chaque test échouait avant les correctifs de l'audit.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_lib import paths
from sdda_lib import workspace as ws
from sdda_scripts import gen_app_skeleton, gen_source_tools as gst, install_env, migrate_workspace
from sdda_scripts import preflight_force_cumul
from test_runtime_envelope import APP, TRACKING, Runtime, patch, runtime  # noqa: F401 - fixture réutilisée
from test_workspace_v6 import v5_workspace  # noqa: F401 - fixture réutilisée

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bootstrap as bs  # noqa: E402

POSIX = os.name != "nt"


# ---------------------------------------------------------------------------
# M7 / M8 — l'enveloppe : tri avant plafond, plages typées
# ---------------------------------------------------------------------------
def test_m7_search_sorts_the_whole_source_before_capping(runtime: Runtime) -> None:
    lines = [json.dumps({"order_id": f"ORD-{i:06d}", "customer_id": "CUS-1", "carrier": "DPD",
                         "status": "in_transit", "last_scan_at": "2026-09-20T08:10:00+00:00",
                         "recipient_name": "A. Dupont", "carrier_message": "en transit"})
             for i in reversed(range(40))]                       # l'ordre du FICHIER est décroissant
    (runtime.project / TRACKING).write_text("\n".join(lines) + "\n", encoding="utf-8")
    patch(runtime.project, "workspace/stack/STACK.md", "SourceMaxRecordsReturned: 200",
          "SourceMaxRecordsReturned: 5")
    gst.run(runtime.project, mode="write")
    fresh = Runtime(runtime.project)
    try:
        result = fresh.run(fresh.envelope.search_records(source="order_tracking", ctx=fresh.ctx()))
    finally:
        fresh.close()
    assert [r["order_id"] for r in result.records] == [f"ORD-{i:06d}" for i in range(5)]
    assert result.truncated is True


def test_m8_a_numeric_range_compares_numbers_not_strings(runtime: Runtime) -> None:
    matches = runtime.envelope._matches
    assert matches({"qty": "9"}, {"qty_min": "10"}, frozenset({"qty"})) is False
    assert matches({"qty": "12"}, {"qty_min": "10"}, frozenset({"qty"})) is True


# ---------------------------------------------------------------------------
# M9 — schema_guard : câblé, et typé comme l'inférence
# ---------------------------------------------------------------------------
def test_m9_a_csv_numeric_column_is_not_a_drift(runtime: Runtime, tmp_path: Path) -> None:
    registry = runtime.registry
    source = registry.Source(id="orders_csv", connector="file", store="s", key="id", description="d",
                             format="csv")
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "orders_csv.schema.json").write_text(json.dumps(
        {"type": "object", "properties": {"id": {"type": "string"}, "qty": {"type": "integer"}},
         "required": ["id", "qty"]}), encoding="utf-8")
    report = runtime.guard.check_source(tmp_path, source, None, 10, [{"id": "A1", "qty": "12"}])
    assert report.ok, report.drift


def test_m9_a_drifted_source_is_not_served(runtime: Runtime) -> None:
    frozen = runtime.data / "schemas" / "order_tracking.schema.json"
    schema = json.loads(frozen.read_text(encoding="utf-8"))
    schema["properties"]["carrier"]["type"] = "integer"
    frozen.write_text(json.dumps(schema), encoding="utf-8")
    with pytest.raises(runtime.guard.SchemaDrift):
        runtime.run(runtime.envelope.lookup_record(source="order_tracking", key="ORD-000101",
                                                   ctx=runtime.ctx()))


# ---------------------------------------------------------------------------
# M10 — BOM
# ---------------------------------------------------------------------------
def test_m10_a_bom_prefixed_csv_keeps_its_first_header(runtime: Runtime, tmp_path: Path) -> None:
    reader = importlib.import_module(f"{APP}.data.formats.csv_reader")
    path = tmp_path / "bom.csv"
    path.write_bytes("\ufeffid,qty\r\nA1,12\r\n".encode("utf-8"))
    source = runtime.registry.Source(id="o", connector="file", store="s", key="id", description="d",
                                     format="csv")
    assert list(reader.read_csv(path, source)) == [{"id": "A1", "qty": "12"}]
    assert gst._bom_tolerant("utf-8") == "utf-8-sig"


# ---------------------------------------------------------------------------
# Mineurs de la couche données
# ---------------------------------------------------------------------------
def test_minor_a_pii_key_is_redacted_in_the_span(runtime: Runtime) -> None:
    source = runtime.registry.Source(id="c", connector="file", store="s", key="email", description="d",
                                     pii=("email",))
    assert runtime.envelope._redact(source, {"key": "a@b.fr", "source": "c"})["key"] == "[PII]"


def test_minor_a_jsonl_lookup_seeks_to_its_line(runtime: Runtime) -> None:
    reg = runtime.registry.load_registry(str(runtime.data / "sources.json"))
    source = reg.source("order_tracking")
    index = runtime.index.build_index(reg, source, runtime.project)
    location = index.by_key["ORD-000101"]
    assert location.byte_offset is not None
    assert runtime.index.record_at(location, source)["order_id"] == "ORD-000101"


# ---------------------------------------------------------------------------
# M11 et mineurs — gen_source_tools
# ---------------------------------------------------------------------------
def test_m11_a_field_name_that_is_not_an_identifier_is_refused(runtime: Runtime) -> None:
    frozen = runtime.data / "schemas" / "order_tracking.schema.json"
    schema = json.loads(frozen.read_text(encoding="utf-8"))
    schema["properties"]["1st scan"] = {"type": "string"}
    frozen.write_text(json.dumps(schema), encoding="utf-8")
    report = gst.run(runtime.project, mode="write", scope="code")
    assert any(f.cls == "DATA_SOURCE_FIELD_NAME_INVALID" for f in report.findings), report.render_text()


def test_minor_a_description_ending_with_a_backslash_still_compiles() -> None:
    import ast

    class Ctx:
        root = Path(".")
        app = "App"

        def contract_id(self, s: str, k: str) -> str:
            return f"1-{s}-{k}"

        def schema_path(self, s: str) -> Path:
            return Path(f"x/{s}.schema.json")

    src = {"key": "order_id", "connector": "file", "store": "s", "description": "chemin C:\\"}
    code = gst.render_wrapper(Ctx(), "orders", src, {"properties": {"order_id": {"type": "string"}}}, "lookup")
    ast.parse(code)


def test_minor_gen_source_tools_refuses_a_non_python_project(runtime: Runtime) -> None:
    patch(runtime.project, "workspace/stack/STACK.md", ".sdda/stacks/lang/python.md", ".sdda/stacks/lang/csharp.md")
    report = gst.run(runtime.project, mode="write")
    assert any(f.cls == "STACK_LANGUAGE_MISMATCH" for f in report.findings), report.render_text()


def test_minor_an_orphan_wrapper_is_reported_then_removed(runtime: Runtime) -> None:
    tools_dir = runtime.data / "tools"
    orphan = tools_dir / "old_source_lookup.py"
    orphan.write_text(f"# {gst.BANNER}\nx = 1\n", encoding="utf-8")
    checked = gst.run(runtime.project, mode="check")
    assert any(f.cls == "DATA_TOOL_ORPHAN" for f in checked.findings)
    gst.run(runtime.project, mode="write")
    assert not orphan.exists()


def test_minor_the_mission_number_is_sanitized(runtime: Runtime) -> None:
    report = gst.run(runtime.project, mode="write", mission="../../x")
    assert any(f.cls == "INVALID_ARG" for f in report.findings)


def test_minor_generated_files_are_lf_on_every_platform(runtime: Runtime) -> None:
    for path in (runtime.data / "envelope.py", runtime.data / "sources.json"):
        assert b"\r\n" not in path.read_bytes(), path


# ---------------------------------------------------------------------------
# M15 / mineurs — gen_app_skeleton
# ---------------------------------------------------------------------------
def _set_app_name(project: Path, name: str) -> None:
    stack = paths.stack_md_path(project)
    text = stack.read_text(encoding="utf-8").replace("AppName: SupportAssistant", f"AppName: {name}", 1)
    stack.write_text(text, encoding="utf-8")


def test_m15_an_app_name_that_escapes_workspace_src_is_refused(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _set_app_name(project, "../../escaped")
    report = gen_app_skeleton.run(project, mode="write")
    assert not report.ok
    assert not (tmp_path / "escaped").exists() and not (project / "escaped").exists()


def test_minor_several_missions_require_mission(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    first = next(paths.missions_dir(project).glob("*-*.md"))
    (first.parent / ("9-" + first.name.split("-", 1)[1])).write_text(first.read_text(encoding="utf-8"),
                                                                      encoding="utf-8")
    ambiguous = gen_app_skeleton.run(project, mode="check")
    assert any(f.cls == "MISSION_AMBIGUOUS" for f in ambiguous.findings)
    chosen = gen_app_skeleton.run(project, mode="write", mission=first.name.split("-", 1)[0])
    assert chosen.ok, chosen.render_text()
    config = json.loads((paths.app_dir(project, "SupportAssistant") / "app_config.json").read_text(encoding="utf-8"))
    assert config["missionId"] == first.name.split("-", 1)[0]


def test_minor_the_skeleton_is_written_in_lf(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    assert gen_app_skeleton.run(project, mode="write").ok
    assert b"\r\n" not in (paths.app_dir(project, "SupportAssistant") / "config.py").read_bytes()


# ---------------------------------------------------------------------------
# M16 — fichiers de secrets
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not POSIX, reason="les permissions POSIX ne s'appliquent pas sous Windows (ACL)")
def test_m16_secret_files_are_created_0600(tmp_path: Path) -> None:
    target = tmp_path / "app" / "secrets.env"
    install_env.write_secret_file(target, b"K=v\n")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_m16_a_secret_file_is_replaced_atomically_and_leaves_no_temporary(tmp_path: Path) -> None:
    target = tmp_path / "secrets.env"
    target.write_text("OLD=1\n", encoding="utf-8")
    install_env.write_secret_file(target, b"NEW=2\n")
    assert target.read_bytes() == b"NEW=2\n"
    assert [p.name for p in tmp_path.iterdir()] == ["secrets.env"]


def test_m16_the_bootstrap_asks_secrets_without_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bs.getpass, "getpass", lambda prompt="": "sk-secret-value")
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("un secret saisi par input() s'affiche"))
    assert bs.ask_secret("Clé") == "sk-secret-value"


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------
def test_minor_a_migration_collision_blocks_the_version(v5_workspace: Path) -> None:  # noqa: F811
    w = v5_workspace / "workspace"
    (w / "feats/1-roster.md").write_text("# autre roster\n", encoding="utf-8")   # la cible existe déjà
    code, _ = run_main(migrate_workspace.main, ["--root", str(v5_workspace), "--json"])
    assert code != 0
    assert ws.read_workspace_version(v5_workspace) == 5, "une collision ne doit pas dater le workspace en v6"


def test_minor_dry_run_announces_the_path_rewrites(v5_workspace: Path) -> None:  # noqa: F811
    code, out = run_main(migrate_workspace.main, ["--root", str(v5_workspace), "--dry-run", "--json"])
    actions = json.loads(out)["data"]["actions"]
    assert any(a["op"] == "edit" and "chemins v6" in a.get("detail", "") for a in actions), actions


def test_minor_ground_truth_is_never_rewritten(v5_workspace: Path) -> None:  # noqa: F811
    seed = v5_workspace / "workspace/proof/seed/labels.jsonl"
    seed.write_text('{"note": "voir proof/seed dans le libellé client"}\n', encoding="utf-8")
    run_main(migrate_workspace.main, ["--root", str(v5_workspace)])
    moved = v5_workspace / "workspace/seed/labels.jsonl"
    assert "proof/seed" in moved.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# preflight_force_cumul
# ---------------------------------------------------------------------------
def test_minor_a_bypass_passed_as_true_is_counted() -> None:
    assert preflight_force_cumul.parse_env_bypasses("SDDA_BYPASS_TOOL_GATE=true") == ["SDDA_BYPASS_TOOL_GATE"]


# ---------------------------------------------------------------------------
# Identité de l'appelant jusqu'à la source, bornes de contrat d'outil appliquées
# ---------------------------------------------------------------------------
def test_minor_the_transport_identity_reaches_a_required_filter(runtime: Runtime) -> None:
    assert gen_app_skeleton.run(runtime.project, mode="write").ok
    identity = importlib.import_module(f"{APP}.identity")
    source = runtime.registry.Source(id="c", connector="file", store="s", key="k", description="d",
                                     required_filter=("customer_id",))
    with identity.caller("CUS-7"):
        assert runtime.envelope._identity_filters(runtime.ctx(), source) == {"customer_id": "CUS-7"}


def test_minor_tool_contract_limits_are_applied(runtime: Runtime) -> None:
    assert gen_app_skeleton.run(runtime.project, mode="write").ok
    spec_mod = importlib.import_module(f"{APP}.tools.spec")
    reg_mod = importlib.import_module(f"{APP}.tools.registry")
    registry = reg_mod.ToolRegistry()
    registry.register(spec_mod.ToolSpec(id="1-big", name="big", description="d", rate_limit_rpm=1,
                                        max_response_bytes=10), lambda: "x" * 100)
    registry.grant("agent", ["big"])
    toolset = registry.to_toolset("agent")
    too_large = asyncio.run(toolset.call("big", {}))
    assert too_large.ok is False and too_large.error_code == "TOOL_RESPONSE_TOO_LARGE"
    limited = asyncio.run(toolset.call("big", {}))
    assert limited.ok is False and limited.error_code == "TOOL_RATE_LIMITED"
