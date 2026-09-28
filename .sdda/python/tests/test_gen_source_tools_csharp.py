"""Générateur d'outils de sources en C# — même chaîne que Python, et une sortie qui compile ET se comporte.

Ce que ces tests tiennent, dans cet ordre :

1. **La génération C# est déterministe et complète** : un wrapper `.cs` par outil,
   le runtime `data/` + `tools/` copié, `{AppName}` substitué partout, aucun
   `.py` émis dans un projet C#, `--check` vert après `--write`.
2. **Le contrat est la seule source du schéma d'entrée** : le wrapper porte le
   `## 2` du contrat octet pour octet, sans le champ d'identité.
3. **Les refus** : format sans lecteur C# (`xlsx`), langage sans runtime (Kotlin),
   wrapper orphelin.
4. **La sortie compile sous les réglages de `lang/csharp.md`** et se COMPORTE
   comme l'enveloppe Python (lookup, identité, états rendus, texte libre
   enveloppé, filtres refusés) — si le SDK .NET 10 est présent ; sinon ignoré,
   jamais simulé.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import make_project
from sdda_lib.errors import Report
from sdda_scripts import gen_source_tools as gst

APP = "SupportAssistant"
MANIFEST = "workspace/stack/STACK.md"
APP_DIR = f"workspace/src/{APP}"
TOOLS = f"{APP_DIR}/data/tools"


@pytest.fixture
def cs_project(tmp_path: Path) -> Path:
    project = make_project(tmp_path, "project_declared_sources")
    _patch(project, ".sdda/stacks/lang/python.md", ".sdda/stacks/lang/csharp.md")
    return project


def _patch(project: Path, old: str, new: str) -> None:
    path = project / MANIFEST
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def classes(report: Report) -> set[str]:
    return {f.cls for f in report.findings}


# ---------------------------------------------------------------------------
# 1. Génération
# ---------------------------------------------------------------------------
def test_write_emits_csharp_wrappers_and_runtime_then_check_is_green(cs_project: Path) -> None:
    written = gst.run(cs_project, mode="write")
    assert written.ok, written.render_text()
    names = sorted(p.name for p in (cs_project / TOOLS).glob("*.cs"))
    assert names == sorted(f"{s}{k}.cs" for s in ("CrmContract", "CrmCustomer", "OrderTracking")
                           for k in ("Count", "Lookup", "Search"))
    app = cs_project / APP_DIR
    assert (app / "data" / "DataEnvelope.cs").is_file()
    assert (app / "tools" / "ToolRegistry.cs").is_file()
    assert not list(app.rglob("*.py")), "un projet C# ne reçoit aucun .py"
    for path in app.rglob("*.cs"):
        text = path.read_text(encoding="utf-8")
        assert gst.APP_TOKEN not in text, path
        assert re.search(rf"^namespace {APP}\.(Data|Data\.Tools|Tools);$", text, re.M), path
    assert gst.run(cs_project, mode="check").ok


def test_csharp_generation_is_byte_identical_on_rerun(cs_project: Path) -> None:
    gst.run(cs_project, mode="write")
    app = cs_project / APP_DIR
    before = {p: p.read_bytes() for p in app.rglob("*.cs")}
    second = gst.run(cs_project, mode="write")
    assert second.data["wrappersWritten"] == [] and second.data["runtimeWritten"] == []
    assert {p: p.read_bytes() for p in app.rglob("*.cs")} == before


def test_a_hand_edited_runtime_file_is_reported(cs_project: Path) -> None:
    gst.run(cs_project, mode="write")
    envelope = cs_project / APP_DIR / "data" / "DataEnvelope.cs"
    envelope.write_text(envelope.read_text(encoding="utf-8").replace("MaxInValues = 20", "MaxInValues = 2000"),
                        encoding="utf-8")
    assert "DATA_RUNTIME_STALE" in classes(gst.run(cs_project, mode="check"))


# ---------------------------------------------------------------------------
# 2. Le contrat, seule source du schéma d'entrée
# ---------------------------------------------------------------------------
def _embedded_schema(text: str) -> dict:
    body = text.split('InputSchemaJson = """', 1)[1].split('""",', 1)[0]
    return json.loads(body)


def test_the_wrapper_carries_the_contract_input_schema_without_the_identity_field(cs_project: Path) -> None:
    text = (cs_project / MANIFEST).read_text(encoding="utf-8")
    old = "    filters: [order_id, customer_id, carrier, status]\n"
    (cs_project / MANIFEST).write_text(text.replace(old, old + "    required_filter: [customer_id]\n", 1), encoding="utf-8")
    assert gst.run(cs_project, mode="write").ok

    wrapper = (cs_project / TOOLS / "OrderTrackingSearch.cs").read_text(encoding="utf-8")
    embedded = _embedded_schema(wrapper)
    assert "customer_id" not in embedded["properties"]
    assert embedded["additionalProperties"] is False

    contract = (cs_project / "workspace/pipeline/contracts/tools/1-order-tracking-search.tool.md").read_text(encoding="utf-8")
    declared = json.loads(contract.split("- **Entrée** :\n```json\n", 1)[1].split("\n```", 1)[0])
    assert embedded == declared
    assert 'PiiFields = new HashSet<string>(StringComparer.Ordinal) { "recipient_name" }' in wrapper
    assert 'UntrustedFields = new HashSet<string>(StringComparer.Ordinal) { "carrier_message" }' in wrapper


def test_a_description_with_quotes_and_xml_stays_a_valid_literal() -> None:
    assert gst._cs_string('a "b" \\ c\n') == '"a \\"b\\" \\\\ c\\u000a"'
    raw = gst._cs_raw('{"x": """y"""}', "  ")
    assert raw.startswith('""""\n') and raw.endswith('\n  """"')
    assert gst._xml_text("<untrusted> & co") == "&lt;untrusted&gt; &amp; co"
    assert gst.csharp_class_name("order_tracking", "lookup") == "OrderTrackingLookup"


# ---------------------------------------------------------------------------
# 3. Refus
# ---------------------------------------------------------------------------
def test_a_format_without_csharp_reader_is_refused_before_the_build(cs_project: Path) -> None:
    _patch(cs_project, "    format: jsonl\n", "    format: xlsx\n")
    report = gst.run(cs_project, mode="write", scope="code")
    assert "STACK_VALUE_UNIMPLEMENTED" in classes(report), report.render_text()


def test_a_language_without_runtime_is_refused_for_code_but_not_for_contracts(tmp_path: Path) -> None:
    project = make_project(tmp_path, "project_declared_sources")
    _patch(project, ".sdda/stacks/lang/python.md", ".sdda/stacks/lang/kotlin.md")
    assert "STACK_LANGUAGE_MISMATCH" in classes(gst.run(project, mode="write"))
    contracts = gst.run(project, mode="write", scope="contracts")
    assert contracts.ok, contracts.render_text()
    assert contracts.data["contractsWritten"]


def test_an_orphan_csharp_wrapper_is_reported_then_removed(cs_project: Path) -> None:
    gst.run(cs_project, mode="write")
    orphan = cs_project / TOOLS / "OldSourceLookup.cs"
    orphan.write_text(f"// <auto-generated>\n// {gst.BANNER}\n", encoding="utf-8")
    assert "DATA_TOOL_ORPHAN" in classes(gst.run(cs_project, mode="check"))
    gst.run(cs_project, mode="write")
    assert not orphan.exists()


# ---------------------------------------------------------------------------
# 4. Compilation et comportement réels (SDK .NET 10)
# ---------------------------------------------------------------------------
def _dotnet10() -> str | None:
    exe = shutil.which("dotnet")
    if not exe:
        return None
    try:
        sdks = subprocess.run([exe, "--list-sdks"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return exe if re.search(r"^10\.", sdks, re.M) else None


PROPS = """<Project>
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <LangVersion>14.0</LangVersion>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
    <AnalysisLevel>latest-all</AnalysisLevel>
    <EnforceCodeStyleInBuild>true</EnforceCodeStyleInBuild>
  </PropertyGroup>
</Project>
"""

CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType></PropertyGroup>
  <ItemGroup><None Include="data/**/*.json" CopyToOutputDirectory="PreserveNewest" /></ItemGroup>
</Project>
"""

PROBE = """#pragma warning disable CA1303, CA1515, CA2007, CA1849, CA1062
using System.Text.Json;
using {app}.Data.Tools;
using {app}.Tools;

namespace {app}.Probe;

internal static class Program
{{
    private static async Task<int> Main(string[] args)
    {{
        var spans = new List<string>();
        var ctx = new ToolContext(args[0]) {{ Identity = new Dictionary<string, string> {{ ["customer_id"] = "CUS-1" }}, Span = (n, p) => spans.Add(n + ":" + (p.TryGetValue("key", out var k) ? k : "")) }};
        var anonymous = new ToolContext(args[0]);
        var results = new Dictionary<string, ToolResult>
        {{
            ["own"] = await OrderTrackingLookup.InvokeAsync(Json("{{\\"order_id\\":\\"ORD-000101\\"}}"), ctx, CancellationToken.None),
            ["other"] = await OrderTrackingLookup.InvokeAsync(Json("{{\\"order_id\\":\\"ORD-000102\\"}}"), ctx, CancellationToken.None),
            ["anonymous"] = await OrderTrackingLookup.InvokeAsync(Json("{{\\"order_id\\":\\"ORD-000101\\"}}"), anonymous, CancellationToken.None),
            ["search"] = await OrderTrackingSearch.InvokeAsync(Json("{{}}"), ctx, CancellationToken.None),
            ["badfilter"] = await OrderTrackingSearch.InvokeAsync(Json("{{\\"recipient_name\\":\\"x\\"}}"), ctx, CancellationToken.None),
            ["count"] = await OrderTrackingCount.InvokeAsync(Json("{{\\"carrier\\":\\"DPD\\"}}"), ctx, CancellationToken.None),
        }};
        Console.WriteLine(JsonSerializer.Serialize(new {{ results, spans }}));
        return 0;
    }}

    private static JsonElement Json(string text) => JsonDocument.Parse(text).RootElement.Clone();
}}
"""


def test_generated_csharp_compiles_strict_and_behaves_like_the_python_envelope(cs_project: Path) -> None:
    dotnet = _dotnet10()
    if dotnet is None:
        pytest.skip("SDK .NET 10 absent : la compilation C# n'est pas simulée")
    text = (cs_project / MANIFEST).read_text(encoding="utf-8")
    old = "    filters: [order_id, customer_id, carrier, status]\n"
    (cs_project / MANIFEST).write_text(text.replace(old, old + "    required_filter: [customer_id]\n", 1), encoding="utf-8")
    assert gst.run(cs_project, mode="write").ok

    # `copytree` garde la date du fichier de la fixture, plus vieille que
    # `max_staleness_hours: 24` : l'instantané serait (à juste titre) `stale`.
    export = cs_project / "workspace/assets/exports/tracking/2026-09-20.jsonl"
    os.utime(export, None)

    app = cs_project / APP_DIR
    # Seuls les outils d'une source fichier ont un client runtime : les autres
    # (mcp, http-api) sont refusés au preflight, pas compilés ici.
    for path in (app / "data" / "tools").glob("Crm*.cs"):
        path.unlink()
    (app / "Directory.Build.props").write_text(PROPS, encoding="utf-8")
    (app / f"{APP}.csproj").write_text(CSPROJ, encoding="utf-8")
    (app / "probe").mkdir()
    (app / "probe" / "Program.cs").write_text(PROBE.format(app=APP), encoding="utf-8")

    build = subprocess.run([dotnet, "build", "-nologo", "-v", "q"], cwd=app, capture_output=True, text=True, timeout=600)
    assert build.returncode == 0, build.stdout[-4000:] + build.stderr[-2000:]
    run = subprocess.run([dotnet, "run", "--no-build", "--", str(cs_project)], cwd=app, capture_output=True,
                         text=True, timeout=300, encoding="utf-8")
    assert run.returncode == 0, run.stdout + run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])
    results = {k: {**v, "Content": json.loads(v["Content"])} for k, v in out["results"].items()}

    own = results["own"]["Content"]
    assert results["own"]["Ok"] and own["record"]["order_id"] == "ORD-000101" and own["stale"] is False and own["as_of"]
    assert own["record"]["carrier_message"].startswith('<untrusted source="order_tracking" field="carrier_message">')
    assert results["other"]["Ok"] and results["other"]["Content"]["record"] is None       # un autre appelant : indiscernable d'une clé absente
    assert results["anonymous"]["ErrorCode"] == "INVALID_FILTER"                             # sans identité : refus, pas une lecture non filtrée
    search = results["search"]["Content"]
    assert [r["order_id"] for r in search["records"]] == ["ORD-000101"] and search["truncated"] is False
    assert results["badfilter"]["ErrorCode"] == "INVALID_FILTER"
    assert results["count"]["Content"]["count"] == 1
    assert "data.lookup:ORD-000101" in out["spans"]
