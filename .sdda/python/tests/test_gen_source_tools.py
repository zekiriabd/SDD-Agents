"""Générateur d'outils de sources — déterminisme, refus, et sortie réellement utilisable.

Deux propriétés valent d'être tenues par des tests plutôt que par la discipline :

1. **Le générateur est idempotent.** Deux exécutions produisent les mêmes
   octets. Sans cela, `--check` crie en CI à chaque run et on cesse de le lire.
2. **Sa sortie compile.** Les wrappers passent l'AST, et les contrats générés
   sont lus par `ir_compiler` et validés par le méta-schéma de la TOOL GATE.
   Un générateur dont la sortie ne franchit pas la gate suivante n'est pas un
   générateur, c'est une source de travail manuel.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_lib import markdown_io, schema_infer
from sdda_lib.errors import Report
from sdda_lib.jsonschema_mini import SchemaValidator
from sdda_scripts import gen_source_tools as gst
from sdda_scripts import ir_compiler

#: La déclaration des sources vit INLINE dans STACK.md (v3 du workspace) ; les
#: schémas figés partent avec le code, à côté des wrappers qu'ils gardent.
MANIFEST = "workspace/stack/STACK.md"
SCHEMAS = "workspace/src/SupportAssistant/data/schemas"
TOOLS = "workspace/src/SupportAssistant/data/tools"
CONTRACTS = "workspace/feats/contracts/tools"
META = Path(__file__).resolve().parents[2] / "templates" / "tool-schema.schema.json"


@pytest.fixture
def sources_project(tmp_path: Path) -> Path:
    return make_project(tmp_path, "project_declared_sources")


def classes(report: Report) -> set[str]:
    return {f.cls for f in report.findings}


def add_source(project: Path, block: str) -> None:
    """Ajoute une source sous `Sources:` de `## Active Data Sources` — là où elle se déclare."""
    path = project / MANIFEST
    text = path.read_text(encoding="utf-8")
    assert "\nSources:\n" in text
    path.write_text(text.replace("\nSources:\n", "\nSources:\n" + block.strip("\n") + "\n", 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# Génération
# ---------------------------------------------------------------------------
def test_check_before_generation_reports_missing(sources_project: Path) -> None:
    report = gst.run(sources_project, mode="check")
    assert "DATA_TOOL_MISSING" in classes(report)
    assert not report.ok


def test_write_then_check_is_green(sources_project: Path) -> None:
    written = gst.run(sources_project, mode="write")
    assert written.ok, written.render_text()
    # 3 sources × (lookup + search + count)
    assert len(written.data["wrappersWritten"]) == 9
    assert len(written.data["contractsWritten"]) == 9

    again = gst.run(sources_project, mode="check")
    assert again.ok, again.render_text()


def test_generation_is_byte_identical_on_rerun(sources_project: Path) -> None:
    gst.run(sources_project, mode="write")
    before = {p.name: p.read_bytes() for p in (sources_project / TOOLS).glob("*.py")}
    second = gst.run(sources_project, mode="write")
    after = {p.name: p.read_bytes() for p in (sources_project / TOOLS).glob("*.py")}
    assert before == after
    assert second.data["wrappersWritten"] == []


def test_every_wrapper_parses(sources_project: Path) -> None:
    gst.run(sources_project, mode="write")
    files = sorted((sources_project / TOOLS).glob("*.py"))
    assert len(files) == 9
    for path in files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_wrapper_carries_pii_and_untrusted_fields(sources_project: Path) -> None:
    gst.run(sources_project, mode="write")
    text = (sources_project / TOOLS / "order_tracking_lookup.py").read_text(encoding="utf-8")
    assert 'PII_FIELDS = frozenset({"recipient_name"})' in text
    assert 'UNTRUSTED_FIELDS = frozenset({"carrier_message"})' in text
    assert "as_of" in text and "stale" in text


def _require_customer(project: Path) -> None:
    path = project / MANIFEST
    text = path.read_text(encoding="utf-8")
    old = "    filters: [order_id, customer_id, carrier, status]\n"
    assert old in text
    path.write_text(text.replace(old, old + "    required_filter: [customer_id]\n", 1), encoding="utf-8")


def _input_block(text: str) -> str:
    return text.split("class Input(BaseModel):", 1)[1].split("class Record(BaseModel):", 1)[0]


def test_the_identity_field_is_never_a_parameter_the_model_fills(sources_project: Path) -> None:
    """`customer_id` vient de l'appelant (`ToolContext.identity`), pas du modèle.

    Le proposer en paramètre OBLIGATOIRE demandait au modèle d'inventer une
    valeur que le runtime jette — l'invitation exacte qu'un message hostile
    attend. Il disparaît donc du wrapper ET du schéma d'entrée du contrat.
    """
    _require_customer(sources_project)
    assert gst.run(sources_project, mode="write").ok
    for kind in ("search", "count"):
        block = _input_block((sources_project / TOOLS / f"order_tracking_{kind}.py").read_text(encoding="utf-8"))
        assert "customer_id" not in block, kind
        assert "carrier:" in block, kind
    src = {"key": "order_id", "filters": ["order_id", "customer_id", "carrier"], "required_filter": ["customer_id"]}
    schema = {"properties": {"customer_id": {"type": "string"}, "carrier": {"type": "string"}}}
    inputs, _ = gst._tool_schemas(src, schema, "search", 50)
    assert "customer_id" not in inputs["properties"] and inputs["required"] == []


def test_a_closed_filter_is_typed_with_its_admitted_values(sources_project: Path) -> None:
    """Le modèle voit `DPD | UPS` dans le schéma, au lieu de deviner `FedEx`."""
    assert gst.run(sources_project, mode="write").ok
    text = (sources_project / TOOLS / "order_tracking_search.py").read_text(encoding="utf-8")
    block = _input_block(text)
    assert 'carrier: Literal["DPD", "UPS"] | None' in block
    assert "from typing import" in text and "Literal" in text.split("from pydantic", 1)[0]
    ast.parse(text)
    # En SORTIE, la donnée reste typée large : une valeur hors enum est signalée, pas refusée.
    record = text.split("class Record(BaseModel):", 1)[1].split("class Output(BaseModel):", 1)[0]
    assert "Literal" not in record


def test_a_contract_declares_only_the_errors_the_runtime_raises(sources_project: Path) -> None:
    """NOT_FOUND, SOURCE_STALE, TOO_MANY_RECORDS sont RENDUS, pas levés.

    Les déclarer en erreurs faisait écrire à qa-evals des cas impossibles, que
    qa-tests devait marquer `xfail` — et G3 les lisait comme des défauts.
    """
    import re as _re

    runtime_errors = Path(__file__).resolve().parents[2] / "templates/runtime/python/data"
    raised = set()
    for path in runtime_errors.glob("*.py"):
        raised |= set(_re.findall(r"raise (\w+)\(", path.read_text(encoding="utf-8")))
    codes = {"InvalidFilter": "INVALID_FILTER", "SourceUnavailable": "SOURCE_UNAVAILABLE", "Timeout": "TIMEOUT"}
    raisable = {codes[c] for c in raised if c in codes}
    for kind in ("lookup", "search", "count"):
        for src in ({}, {"required_filter": ["customer_id"]}):
            assert set(gst.declared_errors(kind, src)) <= raisable, (kind, src)
    assert "INVALID_FILTER" in gst.declared_errors("lookup", {"required_filter": ["customer_id"]})
    assert "INVALID_FILTER" not in gst.declared_errors("lookup", {})

    assert gst.run(sources_project, mode="write").ok
    contract = (sources_project / CONTRACTS / "1-order-tracking-search.tool.md").read_text(encoding="utf-8")
    errors_table = contract.split("## 4. Erreurs déclarées", 1)[1].split("### 4.1", 1)[0]
    for impossible in ("NOT_FOUND", "SOURCE_STALE", "TOO_MANY_RECORDS"):
        assert impossible not in errors_table
    states = contract.split("### 4.1", 1)[1].split("## 5.", 1)[0]
    assert "`truncated: true`" in states and "`stale: true`" in states and "`records: []`" in states


def test_count_tool_exists_so_the_model_never_counts(sources_project: Path) -> None:
    """« Combien ? » est un outil, pas 200 lignes tronquées que le modèle additionne."""
    gst.run(sources_project, mode="write")
    text = (sources_project / TOOLS / "order_tracking_count.py").read_text(encoding="utf-8")
    assert "count_records(" in text
    assert "count: int" in text


def test_search_declares_truncation(sources_project: Path) -> None:
    gst.run(sources_project, mode="write")
    text = (sources_project / TOOLS / "order_tracking_search.py").read_text(encoding="utf-8")
    assert "truncated: bool" in text
    contract = (sources_project / CONTRACTS / "1-order-tracking-search.tool.md").read_text(encoding="utf-8")
    assert "truncated" in contract


def test_range_fields_become_min_max_parameters(sources_project: Path) -> None:
    gst.run(sources_project, mode="write")
    text = (sources_project / TOOLS / "order_tracking_search.py").read_text(encoding="utf-8")
    assert "last_scan_at_min:" in text and "last_scan_at_max:" in text


def test_no_unused_typing_import(sources_project: Path) -> None:
    """Un import inutilisé fait échouer le lint du projet généré."""
    gst.run(sources_project, mode="write")
    for path in sorted((sources_project / TOOLS).glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "from typing import Any" in text:
            assert text.count("Any") > 1, path.name


# ---------------------------------------------------------------------------
# Les contrats générés franchissent la porte suivante
# ---------------------------------------------------------------------------
def test_generated_contracts_compile_and_validate(sources_project: Path) -> None:
    gst.run(sources_project, mode="write")
    report = Report(name="T")
    ctx = ir_compiler.CompileContext(root=sources_project, number="1", report=report)
    validator = SchemaValidator(json.loads(META.read_text(encoding="utf-8")))

    generated = sorted(
        (sources_project / CONTRACTS) / f"1-{slug}-{kind}.tool.md"
        for slug in ("order-tracking", "crm-customer", "crm-contract")
        for kind in ("lookup", "search", "count")
    )
    assert len(generated) == 9
    assert all(p.is_file() for p in generated)

    for path in generated:
        tool = ir_compiler.compile_tool(ctx, path)
        for key in ("name", "description", "sideEffectClass", "trust",
                    "inputSchema", "outputSchema", "timeoutSec", "retryPolicy", "contractTestsRef"):
            assert key in tool, f"{path.name} : `{key}` absent"
        for key in ("inputSchema", "outputSchema"):
            assert validator.validate(tool[key]) == [], f"{path.name} : {key}"
        assert tool["sideEffectClass"] == "read-only"
    assert report.ok, report.render_text()


def test_remote_sources_are_untrusted_in_the_contract(sources_project: Path) -> None:
    gst.run(sources_project, mode="write")
    text = (sources_project / CONTRACTS / "1-crm-customer-lookup.tool.md").read_text(encoding="utf-8")
    assert "Trust: untrusted" in text
    assert "## 9." in text


def test_contract_carries_the_env_var_name_never_the_value(sources_project: Path) -> None:
    gst.run(sources_project, mode="write")
    text = (sources_project / CONTRACTS / "1-crm-customer-search.tool.md").read_text(encoding="utf-8")
    assert "`CRM_API_KEY`" in text
    assert "gitignoré" in text


def test_existing_contract_is_never_overwritten(sources_project: Path) -> None:
    """Le contrat est complété par architect-tools : le réécrire annulerait son travail."""
    gst.run(sources_project, mode="write")
    path = sources_project / CONTRACTS / "1-order-tracking-lookup.tool.md"
    marked = path.read_text(encoding="utf-8").replace(
        "| `{n}-<agent>` | `{n}-{m}-<CAP>` |", "| `1-billing-specialist` | `1-2-ExplainInvoiceLine` |")
    path.write_text(marked, encoding="utf-8")

    gst.run(sources_project, mode="write")
    assert "1-billing-specialist" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Dérive
# ---------------------------------------------------------------------------
def test_hand_edited_wrapper_is_caught(sources_project: Path) -> None:
    gst.run(sources_project, mode="write")
    path = sources_project / TOOLS / "order_tracking_lookup.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n# retouche manuelle\n", encoding="utf-8")
    assert "DATA_TOOL_HAND_EDITED" in classes(gst.run(sources_project, mode="check"))


def test_declaration_change_makes_the_code_stale(sources_project: Path) -> None:
    gst.run(sources_project, mode="write")
    manifest = sources_project / MANIFEST
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("    pii: [recipient_name]", "    pii: []"),
        encoding="utf-8")
    assert "DATA_TOOL_HAND_EDITED" in classes(gst.run(sources_project, mode="check"))


def test_contract_description_drift_is_reported(sources_project: Path) -> None:
    gst.run(sources_project, mode="write")
    path = sources_project / CONTRACTS / "1-order-tracking-lookup.tool.md"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "Suivi transporteur des commandes expediees", "Tout autre chose"), encoding="utf-8")
    assert "DATA_TOOL_DESCRIPTION_DRIFT" in classes(gst.run(sources_project, mode="check"))


def test_contract_cannot_be_less_suspicious_than_the_source(sources_project: Path) -> None:
    gst.run(sources_project, mode="write")
    path = sources_project / CONTRACTS / "1-order-tracking-lookup.tool.md"
    path.write_text(path.read_text(encoding="utf-8").replace("Trust: untrusted", "Trust: trusted"),
                    encoding="utf-8")
    assert "DATA_TOOL_CONTRACT_DRIFT" in classes(gst.run(sources_project, mode="check"))


def test_missing_frozen_schema_blocks_generation(sources_project: Path) -> None:
    (sources_project / SCHEMAS / "order_tracking.schema.json").unlink()
    assert "DATA_SOURCE_SCHEMA_MISSING" in classes(gst.run(sources_project, mode="write"))


def test_refuses_when_another_dataaccess_stack_is_active(sources_project: Path) -> None:
    stack = sources_project / "workspace/stack/STACK.md"
    stack.write_text(stack.read_text(encoding="utf-8").replace(
        " - .sdda/stacks/dataaccess/declared-sources.md", " - .sdda/stacks/dataaccess/view-per-agent.md"),
        encoding="utf-8")
    assert "DATA_ACCESS_INCONSISTENT" in classes(gst.run(sources_project, mode="check"))


# ---------------------------------------------------------------------------
# Inférence
# ---------------------------------------------------------------------------
def test_infer_writes_a_schema_from_real_records(sources_project: Path) -> None:
    (sources_project / SCHEMAS / "order_tracking.schema.json").unlink()
    report = gst.run(sources_project, mode="infer", source="order_tracking")
    assert report.ok, report.render_text()
    assert "DATA_SCHEMA_REVIEW_REQUIRED" in classes(report)

    schema = json.loads((sources_project / SCHEMAS / "order_tracking.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["last_scan_at"]["format"] == "date-time"
    assert sorted(schema["required"]) == ["carrier", "carrier_message", "customer_id",
                                          "last_scan_at", "order_id", "recipient_name", "status"]
    assert schema["x-sdda"]["reviewed"] is False


def test_infer_never_overwrites_a_frozen_schema_without_force(sources_project: Path) -> None:
    report = gst.run(sources_project, mode="infer", source="order_tracking")
    assert "DATA_SCHEMA_ALREADY_FROZEN" in classes(report)
    assert not report.ok

    forced = gst.run(sources_project, mode="infer", source="order_tracking", force=True)
    assert forced.ok, forced.render_text()


def test_infer_on_a_remote_source_refuses_instead_of_calling_out(sources_project: Path) -> None:
    (sources_project / SCHEMAS / "crm_customer.schema.json").unlink()
    report = gst.run(sources_project, mode="infer", source="crm_customer")
    assert "DATA_SCHEMA_SAMPLE_REQUIRED" in classes(report)


def test_infer_from_sample_for_a_remote_source(sources_project: Path, tmp_path: Path) -> None:
    (sources_project / SCHEMAS / "crm_customer.schema.json").unlink()
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({"data": {"items": [
        {"customer_id": "CUS-1", "email": "a@example.com", "segment": "pro", "updated_at": "2026-09-01T10:00:00+00:00"},
        {"customer_id": "CUS-2", "email": "b@example.com", "segment": "pro", "updated_at": "2026-09-02T10:00:00+00:00"},
        {"customer_id": "CUS-3", "email": "c@example.com", "segment": "pro", "updated_at": "2026-09-03T10:00:00+00:00"},
    ]}}), encoding="utf-8")

    report = gst.run(sources_project, mode="infer", source="crm_customer", sample=sample)
    assert report.ok, report.render_text()
    schema = json.loads((sources_project / SCHEMAS / "crm_customer.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["segment"]["enum"] == ["pro"]
    # `email` est déclaré `pii` : jamais d'enum, sinon des données réelles
    # atterriraient dans un fichier versionné.
    assert "enum" not in schema["properties"]["email"]


def test_unknown_source(sources_project: Path) -> None:
    assert "DATA_SOURCE_UNKNOWN" in classes(gst.run(sources_project, mode="infer", source="fantome"))


def test_infer_requires_a_source(sources_project: Path) -> None:
    assert "INVALID_ARG" in classes(gst.run(sources_project, mode="infer"))


def test_cli_check_exits_nonzero_then_zero(sources_project: Path) -> None:
    code, _ = run_main(gst.main, ["--root", str(sources_project), "--check", "--json", "--no-report"])
    assert code == 1
    code, out = run_main(gst.main, ["--root", str(sources_project), "--write", "--json", "--no-report"])
    assert code == 0, out
    code, out = run_main(gst.main, ["--root", str(sources_project), "--json", "--no-report"])
    assert code == 0, out                     # --check est le mode par défaut
    assert json.loads(out)["ok"] is True


# ---------------------------------------------------------------------------
# Inférence — les décisions qui se voient en production
# ---------------------------------------------------------------------------
def test_leading_zero_stays_a_string(tmp_path: Path) -> None:
    """`0012345` typé integer devient `12345`, et la jointure ne trouve plus rien."""
    rows = [{"code_postal": "01000", "n": "12"}, {"code_postal": "75001", "n": "13"}]
    schema = schema_infer.infer(rows, source_id="s", coerce=True).schema
    assert schema["properties"]["code_postal"]["type"] == "string"
    assert schema["properties"]["n"]["type"] == "integer"


def test_key_field_is_never_coerced(tmp_path: Path) -> None:
    rows = [{"order_id": "123456"}, {"order_id": "123457"}]
    schema = schema_infer.infer(rows, source_id="s", coerce=True, string_only={"order_id"}).schema
    assert schema["properties"]["order_id"]["type"] == "string"


def test_required_means_present_in_every_record() -> None:
    rows = [{"a": 1, "b": 2}, {"a": 3}, {"a": 4, "b": None}]
    schema = schema_infer.infer(rows, source_id="s").schema
    assert schema["required"] == ["a"]


def test_enum_is_not_inferred_from_a_thin_sample() -> None:
    thin = [{"status": "a"}, {"status": "b"}]
    assert "enum" not in schema_infer.infer(thin, source_id="s").schema["properties"]["status"]
    thick = [{"status": "a"}] * 5 + [{"status": "b"}] * 5
    assert schema_infer.infer(thick, source_id="s").schema["properties"]["status"]["enum"] == ["a", "b"]


def test_no_maxlength_is_inferred() -> None:
    """La plus longue valeur d'un échantillon est une observation, pas une borne."""
    rows = [{"carrier": "DPD"}, {"carrier": "UPS"}]
    assert "maxLength" not in schema_infer.infer(rows, source_id="s").schema["properties"]["carrier"]


def test_naive_datetime_is_reported() -> None:
    rows = [{"t": "2026-09-11T22:40:00"}]
    result = schema_infer.infer(rows, source_id="s")
    assert any("fuseau" in note for note in result.notes)


def test_mixed_types_are_declared_as_a_union() -> None:
    rows = [{"v": 1}, {"v": "x"}]
    assert schema_infer.infer(rows, source_id="s").schema["properties"]["v"]["type"] == ["integer", "string"]


def test_booleans_are_not_integers() -> None:
    rows = [{"flag": True}, {"flag": False}]
    assert schema_infer.infer(rows, source_id="s").schema["properties"]["flag"]["type"] == "boolean"


# ---------------------------------------------------------------------------
# Lecture des formats
# ---------------------------------------------------------------------------
def test_csv_source_is_read_and_inferred(sources_project: Path) -> None:
    exports = sources_project / "workspace/assets/exports/catalog"
    exports.mkdir(parents=True)
    (exports / "2026-09.csv").write_text(
        "sku;label;price_eur;stock\n"
        "0012;Cable USB;9.90;14\n"
        "0013;Chargeur;19.90;3\n"
        "0014;Housse;12.50;41\n",
        encoding="utf-8")
    add_source(sources_project, """
  - id: catalog
    connector: file
    store: exports_local
    glob: catalog/*.csv
    format: csv
    delimiter: ";"
    key: sku
    filters: [sku, label]
    ranges: [price_eur]
    description: |
      Catalogue produit exporte mensuellement par le marketing, une ligne par reference.
      Utiliser pour connaitre le libelle, le prix affiche et le stock theorique d'une
      reference. Ne pas utiliser pour la disponibilite reelle en entrepot ni pour les
      tarifs negocies. Les prix sont en euros TTC. as_of porte la date de l'export
      mensuel : le stock a certainement bouge depuis.
""")

    report = gst.run(sources_project, mode="infer", source="catalog")
    assert report.ok, report.render_text()
    schema = json.loads((sources_project / SCHEMAS / "catalog.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["sku"]["type"] == "string"      # `key` -> jamais coercé
    assert schema["properties"]["price_eur"]["type"] == "number"
    assert schema["properties"]["stock"]["type"] == "integer"


def test_json_array_with_records_path(sources_project: Path) -> None:
    exports = sources_project / "workspace/assets/exports/stock"
    exports.mkdir(parents=True)
    (exports / "snapshot.json").write_text(
        json.dumps({"meta": {"v": 1}, "rows": [{"sku": "A1", "qty": 3}, {"sku": "A2", "qty": 0}]}),
        encoding="utf-8")
    add_source(sources_project, """
  - id: stock_levels
    connector: file
    store: exports_local
    glob: stock/*.json
    format: object
    records_path: rows
    key: sku
    filters: [sku]
    ranges: [qty]
    description: |
      Niveaux de stock theoriques par reference, un enregistrement par SKU, issus du
      snapshot nocturne du WMS. Utiliser pour savoir si une reference est annoncee en
      stock. Ne pas utiliser pour promettre une disponibilite : le snapshot date de la
      nuit et as_of le dit. Les quantites sont des unites entieres, jamais des colis.
""")

    report = gst.run(sources_project, mode="infer", source="stock_levels")
    assert report.ok, report.render_text()
    schema = json.loads((sources_project / SCHEMAS / "stock_levels.schema.json").read_text(encoding="utf-8"))
    assert sorted(schema["properties"]) == ["qty", "sku"]


def test_empty_glob_refuses_to_infer(sources_project: Path) -> None:
    (sources_project / "workspace/assets/exports/tracking/2026-09-20.jsonl").unlink()
    (sources_project / SCHEMAS / "order_tracking.schema.json").unlink()
    assert "DATA_SOURCE_EMPTY" in classes(gst.run(sources_project, mode="infer", source="order_tracking"))


def test_bad_encoding_is_an_error_not_a_silent_replacement(sources_project: Path) -> None:
    """`errors=strict` est délibéré : un remplacement produit des données fausses."""
    (sources_project / "workspace/assets/exports/tracking/2026-09-21.jsonl").write_bytes(
        b'{"order_id":"ORD-9","carrier_message":"caf\xe9"}\n')
    (sources_project / SCHEMAS / "order_tracking.schema.json").unlink()
    assert "DATA_SOURCE_UNREADABLE" in classes(gst.run(sources_project, mode="infer", source="order_tracking"))


def test_mission_must_be_decidable(sources_project: Path) -> None:
    for path in (sources_project / "workspace/feats/missions").glob("*.md"):
        path.unlink()
    assert "MISSION_AMBIGUOUS" in classes(gst.run(sources_project, mode="check"))
