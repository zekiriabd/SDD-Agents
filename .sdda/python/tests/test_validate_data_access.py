"""DATA ACCESS — la gate G3 sur les sources déclarées.

Chaque test casse **une** chose dans un projet vert et vérifie la classe exacte
émise. C'est le seul protocole qui garantit qu'une protection existe : un
validateur qui passe au vert sur un projet cassé est pire qu'aucun validateur,
parce qu'on cesse de regarder.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_lib import source_registry as sr
from sdda_scripts import validate_data_access as vda

STACK = "workspace/stack/STACK.md"
#: La surface de données se déclare INLINE dans STACK.md (v3). Le seul manifeste
#: qui subsiste est un `mcp.json` au format standard, à côté de STACK.md.
MANIFEST = STACK
MCP_CONFIG = "workspace/stack/mcp.json"
SCHEMAS = "workspace/src/SupportAssistant/src/SupportAssistant/data/schemas"


@pytest.fixture
def sources_project(tmp_path: Path) -> Path:
    return make_project(tmp_path, "project_declared_sources")


def patch(project: Path, rel: str, old: str, new: str) -> None:
    path = project / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"motif absent de {rel} : {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def classes(project: Path, *, source: str | None = None) -> set[str]:
    report = vda.run(project, only_source=source)
    return {f.cls for f in report.findings}


def errors(project: Path) -> set[str]:
    report = vda.run(project)
    return {f.cls for f in report.errors}


# ---------------------------------------------------------------------------
# Le cas vert
# ---------------------------------------------------------------------------
def test_declared_sources_project_is_green(sources_project: Path) -> None:
    report = vda.run(sources_project)
    assert report.ok, report.render_text()
    assert not report.warnings, report.render_text()
    assert report.data["strategy"] == "declared-sources"
    assert sorted(report.data["envelope"]["schemas"]) == ["crm_contract", "crm_customer", "order_tracking"]


def test_three_connectors_are_resolved(sources_project: Path) -> None:
    """Fichier, API et MCP vivent dans le même registre, avec le même contrôle."""
    data = vda.run(sources_project).data
    by_id = {s["id"]: s for s in data["sources"]}
    assert by_id["order_tracking"]["connector"] == "file"
    assert by_id["crm_customer"]["connector"] == "http-api"
    assert by_id["crm_contract"]["connector"] == "mcp"
    assert by_id["order_tracking"]["files"] == 1


def test_manifests_and_inline_are_merged(sources_project: Path) -> None:
    data = vda.run(sources_project).data
    assert data["manifests"] == ["mcp.json"]
    # `internal_crm` ne vient d'aucune déclaration SDD_Agents : il est importé
    # du fichier MCP standard, et n'existe que parce que l'allowlist le garde.
    assert "internal_crm" in data["envelope"]["stores"]


def test_cli_json_mode_exits_zero(sources_project: Path) -> None:
    code, out = run_main(vda.main, ["--root", str(sources_project), "--json", "--no-report"])
    assert code == 0, out
    assert json.loads(out)["ok"] is True


def test_single_source_filter(sources_project: Path) -> None:
    report = vda.run(sources_project, only_source="order_tracking")
    assert [s["id"] for s in report.data["sources"]] == ["order_tracking"]

    report = vda.run(sources_project, only_source="inconnue")
    assert report.has("DATA_SOURCE_UNKNOWN")


# ---------------------------------------------------------------------------
# Secrets — la raison d'être du fichier `.env`
# ---------------------------------------------------------------------------
def test_inline_secret_in_store_is_refused(sources_project: Path) -> None:
    patch(sources_project, STACK,
          "auth: { mode: api-key, header: X-API-Key, key_env: CRM_API_KEY }",
          "auth: { mode: api-key, header: X-API-Key, key_env: sk-live-4f8a2b91c7de0356 }")
    assert "DATA_SECRET_INLINE" in errors(sources_project)


def test_secret_variable_absent_from_env_file(sources_project: Path) -> None:
    patch(sources_project, ".env", "CRM_API_KEY=\n", "")
    found = errors(sources_project)
    assert "DATA_SECRET_VAR_UNDECLARED" in found


def test_env_file_missing(sources_project: Path) -> None:
    (sources_project / ".env").unlink()
    assert "DATA_SECRET_FILE_MISSING" in errors(sources_project)


def test_env_file_not_gitignored(sources_project: Path) -> None:
    patch(sources_project, ".gitignore", ".env\n", "")
    assert "DATA_SECRET_FILE_UNIGNORED" in errors(sources_project)


def test_literal_secret_in_imported_mcp_config(sources_project: Path) -> None:
    """Le piège n°1 des `.mcp.json` : le format autorise la valeur en clair."""
    patch(sources_project, MCP_CONFIG, '"${CRM_MCP_TOKEN}"', '"sk-live-4f8a2b91c7de0356"')
    assert "DATA_SECRET_INLINE" in errors(sources_project)


def test_env_names_never_yields_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# commentaire\nexport CRM_API_KEY=sk-live-secret\nVIDE=\n", encoding="utf-8")
    names = sr.env_names(env)
    assert names == {"CRM_API_KEY", "VIDE"}
    assert not any("secret" in n for n in names)


@pytest.mark.parametrize("value,expected", [
    ("CRM_API_KEY", False),
    ("${CRM_API_KEY}", False),
    ("$CRM_API_KEY", False),
    ("X-API-Key", False),
    ("sk-live-4f8a2b91c7de0356", True),
    ("ghp_16C7e42F292c6912E7710c838347Ae178B4a", True),
    ("AKIAIOSFODNN7EXAMPLE", True),
])
def test_looks_like_secret(value: str, expected: bool) -> None:
    assert sr.looks_like_secret(value) is expected


# ---------------------------------------------------------------------------
# Frontière réseau
# ---------------------------------------------------------------------------
def test_host_outside_egress_allowlist(sources_project: Path) -> None:
    patch(sources_project, STACK, "SourceEgressAllowlist: [crm.example.com]",
          "SourceEgressAllowlist: [autre.example.com]")
    assert "DATA_EGRESS_UNDECLARED" in errors(sources_project)


def test_empty_egress_allowlist_means_no_egress(sources_project: Path) -> None:
    """Une allowlist vide vaut « aucune sortie », jamais « tout permis »."""
    patch(sources_project, STACK, "SourceEgressAllowlist: [crm.example.com]",
          "SourceEgressAllowlist: []")
    assert "DATA_EGRESS_UNDECLARED" in errors(sources_project)


def test_plain_http_is_refused(sources_project: Path) -> None:
    patch(sources_project, STACK, "base_url: https://crm.example.com/api/v2",
          "base_url: http://crm.example.com/api/v2")
    assert "DATA_TLS_INSECURE" in errors(sources_project)


def test_verify_tls_false_is_refused(sources_project: Path) -> None:
    patch(sources_project, STACK, "base_url: https://crm.example.com/api/v2",
          "base_url: https://crm.example.com/api/v2\n    verify_tls: false")
    assert "DATA_TLS_INSECURE" in errors(sources_project)


# ---------------------------------------------------------------------------
# Manifestes
# ---------------------------------------------------------------------------
def test_duplicate_id_across_manifest_and_stack(sources_project: Path) -> None:
    patch(sources_project, STACK, "  - id: crm_customer\n    connector: http-api",
          "  - id: order_tracking\n    connector: http-api")
    assert "DATA_MANIFEST_DUPLICATE_ID" in errors(sources_project)


def test_manifest_outside_root_is_refused(sources_project: Path) -> None:
    patch(sources_project, STACK, "  - { path: mcp.json, kind: mcp-config }",
          "  - { path: ../../../../../etc/mcp.json, kind: mcp-config }")
    assert "DATA_MANIFEST_OUTSIDE_ROOT" in errors(sources_project)


def test_manifest_missing(sources_project: Path) -> None:
    (sources_project / MCP_CONFIG).unlink()
    assert "DATA_MANIFEST_MISSING" in errors(sources_project)


def test_manifest_malformed(sources_project: Path) -> None:
    (sources_project / MCP_CONFIG).write_text("{ pas du json", encoding="utf-8")
    assert "DATA_MANIFEST_MALFORMED" in errors(sources_project)


# ---------------------------------------------------------------------------
# Grammaire close
# ---------------------------------------------------------------------------
def test_unknown_source_key_is_refused(sources_project: Path) -> None:
    """`pii_fields` au lieu de `pii` désactiverait la redaction sans rien dire."""
    patch(sources_project, MANIFEST, "    pii: [recipient_name]", "    pii_fields: [recipient_name]")
    assert "DATA_SOURCE_UNKNOWN_KEY" in errors(sources_project)


def test_unknown_store_key_is_refused(sources_project: Path) -> None:
    patch(sources_project, MANIFEST, "    root: workspace/data/exports",
          "    root: workspace/data/exports\n    bucket: acme")
    assert "DATA_STORE_UNKNOWN_KEY" in errors(sources_project)


def test_unknown_connector(sources_project: Path) -> None:
    patch(sources_project, MANIFEST, "    connector: file", "    connector: ftp")
    assert "DATA_SOURCE_CONNECTOR_UNKNOWN" in errors(sources_project)


def test_unknown_format(sources_project: Path) -> None:
    patch(sources_project, MANIFEST, "    format: jsonl", "    format: yaml")
    assert "DATA_SOURCE_FORMAT_UNKNOWN" in errors(sources_project)


def test_xlsx_warns_about_pinned_library(sources_project: Path) -> None:
    patch(sources_project, MANIFEST, "    format: jsonl", "    format: xlsx")
    assert "DATA_SOURCE_FORMAT_NEEDS_LIB" in classes(sources_project)


def test_unknown_auth_mode(sources_project: Path) -> None:
    patch(sources_project, STACK, "auth: { mode: api-key, header: X-API-Key, key_env: CRM_API_KEY }",
          "auth: { mode: magic, key_env: CRM_API_KEY }")
    assert "DATA_AUTH_MODE_UNKNOWN" in errors(sources_project)


def test_auth_block_is_mandatory(sources_project: Path) -> None:
    patch(sources_project, MANIFEST, "    auth: { mode: none }\n", "")
    assert "DATA_AUTH_INCOMPLETE" in errors(sources_project)


def test_unknown_store_reference(sources_project: Path) -> None:
    patch(sources_project, MANIFEST, "    store: exports_local", "    store: nulle_part")
    assert "DATA_STORE_UNKNOWN" in errors(sources_project)


def test_description_too_short(sources_project: Path) -> None:
    """La description EST le prompt de l'outil : trop courte, le modèle appelle à tort."""
    path = sources_project / MANIFEST
    text = path.read_text(encoding="utf-8")
    head, _, tail = text.partition("    description: |\n      Suivi transporteur")
    kept = tail.split("\n\n", 1)[1]
    path.write_text(head + "    description: Suivi transporteur.\n\n" + kept, encoding="utf-8")
    assert "DATA_SOURCE_DESCRIPTION_TOO_SHORT" in errors(sources_project)


# ---------------------------------------------------------------------------
# Frontière de fichiers
# ---------------------------------------------------------------------------
def test_glob_escaping_the_store_root(sources_project: Path) -> None:
    patch(sources_project, MANIFEST, "    glob: tracking/*.jsonl", "    glob: ../../stack/*.md")
    assert "DATA_SOURCE_PATH_ESCAPE" in errors(sources_project)


def test_glob_matching_nothing(sources_project: Path) -> None:
    patch(sources_project, MANIFEST, "    glob: tracking/*.jsonl", "    glob: absent/*.jsonl")
    assert "DATA_SOURCE_EMPTY" in errors(sources_project)


def test_store_root_unreachable(sources_project: Path) -> None:
    patch(sources_project, MANIFEST, "    root: workspace/data/exports", "    root: workspace/data/absent")
    assert "DATA_STORE_UNREACHABLE" in errors(sources_project)


def test_store_probe_can_be_downgraded_on_ci(sources_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDDA_SKIP_STORE_PROBE", "1")
    patch(sources_project, MANIFEST, "    root: workspace/data/exports", "    root: workspace/data/absent")
    report = vda.run(sources_project)
    assert "DATA_STORE_UNREACHABLE" not in {f.cls for f in report.errors}
    assert "DATA_STORE_UNREACHABLE" in {f.cls for f in report.warnings}


def test_file_larger_than_cap(sources_project: Path) -> None:
    patch(sources_project, STACK, "SourceMaxObjectBytes: 52428800", "SourceMaxObjectBytes: 10")
    assert "DATA_SOURCE_FILE_TOO_LARGE" in errors(sources_project)


# ---------------------------------------------------------------------------
# Schéma figé
# ---------------------------------------------------------------------------
def test_frozen_schema_missing(sources_project: Path) -> None:
    (sources_project / SCHEMAS / "order_tracking.schema.json").unlink()
    assert "DATA_SOURCE_SCHEMA_MISSING" in errors(sources_project)


def test_declared_field_absent_from_frozen_schema(sources_project: Path) -> None:
    patch(sources_project, MANIFEST, "    pii: [recipient_name]", "    pii: [numero_tva]")
    assert "DATA_SOURCE_FIELD_UNDECLARED" in errors(sources_project)


def test_frozen_schema_malformed(sources_project: Path) -> None:
    (sources_project / SCHEMAS / "crm_customer.schema.json").write_text(
        "{ pas du json", encoding="utf-8")
    assert "DATA_SOURCE_SCHEMA_MALFORMED" in errors(sources_project)


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------
def test_mcp_tool_outside_server_allowlist(sources_project: Path) -> None:
    patch(sources_project, MANIFEST, "    tool: crm_get_contract", "    tool: crm_delete_contract")
    assert "DATA_MCP_TOOL_NOT_ALLOWLISTED" in errors(sources_project)


def test_mcp_server_unknown(sources_project: Path) -> None:
    patch(sources_project, MCP_CONFIG, '"internal-crm"', '"autre-serveur"')
    assert "DATA_STORE_UNKNOWN" in errors(sources_project)


def test_source_cannot_be_more_trusted_than_its_mcp_server(sources_project: Path) -> None:
    patch(sources_project, MANIFEST, "    tool: crm_get_contract",
          "    tool: crm_get_contract\n    trust: trusted")
    assert "DATA_SOURCE_TRUST_OPTIMISTIC" in errors(sources_project)


def test_remote_source_declared_trusted_warns(sources_project: Path) -> None:
    patch(sources_project, STACK, "    records_path: data.items",
          "    records_path: data.items\n    trust: trusted")
    assert "DATA_SOURCE_TRUST_OPTIMISTIC" in classes(sources_project)


# ---------------------------------------------------------------------------
# Enveloppe et cohérence de stack
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["SourceReadTimeoutMs", "SourceMaxRecordsReturned", "SourceMaxObjectBytes"])
def test_missing_bound_is_an_infinite_bound(sources_project: Path, key: str) -> None:
    path = sources_project / STACK
    text = "\n".join(l for l in path.read_text(encoding="utf-8").split("\n") if not l.startswith(key + ":"))
    path.write_text(text, encoding="utf-8")
    assert "DATA_ACCESS_ENVELOPE_MISSING" in errors(sources_project)


def test_forbidden_ops_must_cover_symlink_and_egress(sources_project: Path) -> None:
    patch(sources_project, STACK,
          "SourceForbiddenOps: [WRITE, DELETE, EXEC, SYMLINK_FOLLOW, UNDECLARED_EGRESS]",
          "SourceForbiddenOps: [WRITE, DELETE]")
    assert "DATA_ACCESS_ENVELOPE_MISSING" in errors(sources_project)


def test_role_must_be_readonly(sources_project: Path) -> None:
    patch(sources_project, STACK, "SourceAgentRole: readonly", "SourceAgentRole: scoped-write")
    assert "DATA_SOURCE_ROLE_INVALID" in errors(sources_project)


def test_allowlist_citing_an_undeclared_source(sources_project: Path) -> None:
    patch(sources_project, STACK, "SourceAgentRole: readonly",
          "SourceAllowedSources: [order_tracking, fantome]\nSourceAgentRole: readonly")
    assert "DATA_SOURCE_ALLOWLIST_UNKNOWN" in errors(sources_project)


def test_source_outside_allowlist_is_shadowed(sources_project: Path) -> None:
    patch(sources_project, STACK, "SourceAgentRole: readonly",
          "SourceAllowedSources: [order_tracking]\nSourceAgentRole: readonly")
    assert "DATA_SOURCE_SHADOWED" in classes(sources_project)


def test_database_and_declared_sources_conflict(sources_project: Path) -> None:
    patch(sources_project, STACK, "DatabaseType: none", "DatabaseType: PostgreSql")
    assert "DATA_SOURCE_DB_CONFLICT" in errors(sources_project)


def test_none_strategy_with_sources_declared(sources_project: Path) -> None:
    patch(sources_project, STACK, " - .sdda/stacks/dataaccess/declared-sources.md",
          " - .sdda/stacks/dataaccess/none.md")
    assert "DATA_ACCESS_INCONSISTENT" in errors(sources_project)


def test_declared_sources_without_section(sources_project: Path) -> None:
    path = sources_project / STACK
    text = path.read_text(encoding="utf-8")
    start = text.index("## Active Data Sources")
    end = text.index("## Active Memory Strategy")
    path.write_text(text[:start] + text[end:], encoding="utf-8")
    assert "DATA_SOURCES_MISSING" in errors(sources_project)


def test_base_project_stays_green(project: Path) -> None:
    """`dataaccess/none` sans source déclarée : la stack par défaut reste verte."""
    report = vda.run(project)
    assert report.ok, report.render_text()
    assert report.data["strategy"] == "none"
