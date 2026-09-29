"""Architecture de référence ≠ architecture générée — le livrable proportionné à la spec.

Ce que ces tests tiennent :

1. **Agent seul** : un agent, pas de routeur, pas d'état d'orchestration, pas de
   sous-agent — dans la décision (IR `architecture`) comme dans le squelette.
2. **Pas de mémoire** : ni `memory/`, ni store, ni implémentation de mémoire ; une
   conversation multi-tour est une SESSION, pas une couche mémoire.
3. **Pas de RAG** : ni retriever, ni vector store, ni pipeline — un `retrieval/`
   écrit quand même est refusé.
4. **JSON seul** : lecteur JSON présent ; lecteurs CSV, TSV, JSONL absents
   (Python et C#, compilé si le SDK .NET 10 est là).
5. **Outils** : l'outil que le roster accorde est généré ; celui qu'il n'accorde
   pas (`count`) n'apparaît nulle part — ni contrat, ni wrapper, ni porte.
6. **Dépendances** : le `.csproj` ne porte que des paquets utilisés.
7. **Architecture complexe** : multi-agents, routage, mémoire, RAG sont bien
   générés — le changement rend les capacités conditionnelles, il ne les retire pas.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from conftest import make_project
from sdda_lib import effective_architecture as ea
from sdda_lib import feature_template as ft
from sdda_lib.errors import Report
from sdda_scripts import gen_app_skeleton, gen_source_tools as gst, ir_compiler
from sdda_scripts import validate_effective_architecture as vea
from test_gen_source_tools_csharp import PROPS, _dotnet10

APP = "SupportAssistant"
APP_DIR = f"workspace/src/{APP}"
MANIFEST = "workspace/stack/STACK.md"
CONTRACTS = "workspace/pipeline/contracts/tools"

ORDER_LOOKUP_SOURCES = """## Active Data Sources
SourceSecretsFile: .env
Stores:
  - id: order_data
    kind: local
    root: workspace/assets
    read_only: true
    auth: { mode: none }
    description: Jeu de test local, lecture seule.
Sources:
  - id: orders
    connector: file
    store: order_data
    glob: orders.json
    format: array
    encoding: utf-8
    key: order_id
    filters: [order_id, status, customer_name]
    pii: [customer_name]
    description: |
      Commandes, un enregistrement par commande (cle order_id, forme CMD-1001).
      Utiliser pour retrouver une commande par son numero ou par nom de client et
      donner son statut et son montant. Ne pas utiliser pour une information absente
      du fichier : la dire absente, ne jamais l'inventer. Montants en EUR.
SourceAgentRole: readonly
SourceReadTimeoutMs: 1000
SourceMaxRecordsReturned: 25
SourceMaxObjectBytes: 5242880
SourceSchemaCheckSample: 500
SourceMaxStalenessHours: 24
SourceForbiddenOps: [WRITE, DELETE, EXEC, SYMLINK_FOLLOW, UNDECLARED_EGRESS]
SourceEgressAllowlist: []
SourceQueryLogging: full

"""

ROSTER = """# ROSTER: 1

```yaml
mission: 1
pattern: single-agent
orchestrator:
  id: order-assistant
  tools: [{tools}]
subagents: []
```
"""


def classes(report: Report) -> set[str]:
    return {f.cls for f in report.findings}


def _replace_section(project: Path, heading: str, next_heading: str, body: str) -> None:
    path = project / MANIFEST
    text = path.read_text(encoding="utf-8")
    start = text.index(f"## {heading}")
    end = text.index(f"## {next_heading}", start)
    path.write_text(text[:start] + body + text[end:], encoding="utf-8")


def _patch(project: Path, old: str, new: str) -> None:
    path = project / MANIFEST
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def order_lookup(tmp_path: Path, *, language: str = "python", tools: str = "orders_lookup, orders_search") -> Path:
    """La spec simple : 1 agent, 2 outils, 1 fichier JSON de 25 commandes, conversation console."""
    project = make_project(tmp_path, "project_declared_sources")
    _replace_section(project, "Active Data Sources", "Active Memory Strategy", ORDER_LOOKUP_SOURCES)
    if language != "python":
        _patch(project, ".sdda/stacks/lang/python.md", f".sdda/stacks/lang/{language}.md")
    orders = [{"order_id": f"CMD-{1000 + i}", "status": ("shipped", "paid", "delivered")[i % 3],
               "customer_name": ("Alice Martin", "Bruno Diaz", "Chloe Petit")[i % 3], "total": f"{10 * i}.00"}
              for i in range(1, 26)]
    (project / "workspace/assets/orders.json").write_text(json.dumps(orders), encoding="utf-8")
    roster = project / "workspace/feats/1-roster.md"
    roster.parent.mkdir(parents=True, exist_ok=True)
    roster.write_text(ROSTER.format(tools=tools), encoding="utf-8")
    inferred = gst.run(project, mode="infer", source="orders")
    assert inferred.ok, inferred.render_text()
    return project


# ---------------------------------------------------------------------------
# Gabarits conditionnels
# ---------------------------------------------------------------------------
def test_a_template_renders_only_the_required_blocks_and_erases_its_markers() -> None:
    text = "a\n# @sdda-if x\nb\n# @sdda-else\nc\n# @sdda-endif\n  // @sdda-if !y|z\nd\n  // @sdda-endif\ne"
    assert ft.render(text, {"x"}) == "a\nb\nd\ne"
    assert ft.render(text, {"y"}) == "a\nc\ne"
    assert ft.render(text, {"y", "z"}) == "a\nc\nd\ne"
    assert ft.render(text, ft.ALL) == "a\nb\nd\ne", "la référence : toute capacité présente"
    assert ft.wanted("# @sdda-file-if data.format.delimited\nx", {"data.format.json"}) is False
    assert ft.wanted("# @sdda-file-if data.format.delimited\nx", ft.ALL) is True
    with pytest.raises(ft.TemplateError):
        ft.render("# @sdda-if x\nb", {"x"})


# ---------------------------------------------------------------------------
# 1. Agent seul
# ---------------------------------------------------------------------------
SINGLE_AGENT_IR = {
    "missionId": "1-OrderLookup",
    "agents": [{"id": "1-order-assistant", "tools": ["1-orders-lookup"],
                "bounds": {"maxIterations": 4, "maxToolCalls": 4, "maxDelegationDepth": 0,
                           "timeoutSec": 60, "budgetUsd": 0.1}}],
    "tools": [{"id": "1-orders-lookup", "name": "orders_lookup"}],
    "orchestration": {"rootPattern": "single-agent", "entryNode": "agent",
                      "nodes": [{"id": "agent", "kind": "agent", "ref": "1-order-assistant"}],
                      "edges": [], "terminalNodes": ["agent"]},
    "memory": {"shortTermPolicy": "sliding-window", "shortTermMaxTurns": 12, "longTermEnabled": False,
               "crossAgentSharedState": "scoped"},
}


def test_single_agent_decision_has_an_agent_and_no_orchestration_machinery() -> None:
    arch = ea.derive(SINGLE_AGENT_IR)
    assert arch.requires("agent.loop") and arch.requires("bounds")
    for absent in ("orchestration.router", "orchestration.graph", "orchestration.shared-state",
                   "orchestration.sequential", "orchestration.delegation"):
        assert not arch.requires(absent), absent
    assert all(why for why in arch.justified.values()), "chaque capacité requise porte sa justification"


def test_single_agent_skeleton_has_no_router_no_state_no_subagent(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _patch(project, ".sdda/stacks/orchestration/router.md", ".sdda/stacks/orchestration/single-agent.md")
    report = gen_app_skeleton.run(project, mode="write")
    assert report.ok, report.render_text()
    app = project / APP_DIR
    assert (app / "orchestration/base.py").is_file(), "la boucle bornée, elle, est exigée"
    assert not (app / "orchestration/router.py").exists()
    assert not (app / "orchestration/sequential.py").exists()
    assert gen_app_skeleton.run(project, mode="check").ok


# ---------------------------------------------------------------------------
# 2. Pas de mémoire
# ---------------------------------------------------------------------------
def test_no_memory_requirement_means_no_memory_layer_and_no_session() -> None:
    ir = {**SINGLE_AGENT_IR, "memory": {"shortTermPolicy": "none", "longTermEnabled": False}}
    arch = ea.derive(ir)
    for absent in ("memory.layer", "memory.long-term", "memory.summarization", "conversation.session"):
        assert not arch.requires(absent), absent


def test_a_multi_turn_conversation_is_a_session_not_a_memory_layer(tmp_path: Path) -> None:
    arch = ea.derive(SINGLE_AGENT_IR)
    assert arch.requires("conversation.session")
    assert not arch.requires("memory.layer") and not arch.requires("memory.long-term")

    project = make_project(tmp_path)
    _patch(project, ".sdda/stacks/orchestration/router.md", ".sdda/stacks/orchestration/single-agent.md")
    assert gen_app_skeleton.run(project, mode="write").ok
    assert not (project / APP_DIR / "memory").exists(), "fenêtre glissante d'un agent seul : aucun memory/"


def test_a_leftover_memory_module_is_reported_then_removed(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _patch(project, ".sdda/stacks/orchestration/router.md", ".sdda/stacks/orchestration/single-agent.md")
    assert gen_app_skeleton.run(project, mode="write", features=ft.ALL).ok      # une génération « complète » d'avant
    leftover = project / APP_DIR / "memory/__init__.py"
    assert leftover.is_file()
    assert "ARCH_COMPONENT_UNJUSTIFIED" in classes(gen_app_skeleton.run(project, mode="check"))
    assert gen_app_skeleton.run(project, mode="write").ok
    assert not leftover.exists() and not (project / APP_DIR / "orchestration/router.py").exists()


# ---------------------------------------------------------------------------
# 3. Pas de RAG
# ---------------------------------------------------------------------------
def test_no_rag_requirement_means_no_retrieval_and_a_written_retriever_is_refused(tmp_path: Path) -> None:
    arch = ea.derive(SINGLE_AGENT_IR)
    assert not arch.requires("retrieval") and not arch.requires("retrieval.rerank")

    project = make_project(tmp_path)
    app = project / APP_DIR
    (app / "retrieval").mkdir(parents=True)
    (app / "retrieval" / "index.py").write_text("class VectorStore:\n    pass\n", encoding="utf-8")
    report = vea.run(project, Report(name="P", target="t"), ir={**SINGLE_AGENT_IR, "architecture": arch.to_ir()})
    assert "ARCH_COMPONENT_UNJUSTIFIED" in classes(report)
    assert any("retrieval/index.py" in f.message for f in report.errors)


# ---------------------------------------------------------------------------
# 4 + 5. JSON seul, outils accordés seulement
# ---------------------------------------------------------------------------
def test_json_only_python_runtime_has_the_json_reader_and_nothing_else(tmp_path: Path) -> None:
    project = order_lookup(tmp_path)
    report = gst.run(project, mode="write")
    assert report.ok, report.render_text()
    data = project / APP_DIR / "data"
    assert (data / "formats/json_reader.py").is_file()
    assert not (data / "formats/csv_reader.py").exists(), "aucune source CSV/TSV"
    assert not (data / "formats/tabular.py").exists(), "aucune source XLSX/Parquet"
    index = (data / "index.py").read_text(encoding="utf-8")
    assert "_jsonl_rows" not in index, "aucune source JSONL"
    envelope = (data / "envelope.py").read_text(encoding="utf-8")
    assert "async def lookup_record" in envelope and "async def search_records" in envelope
    for absent in ("count_records", "_identity_filters", "_check_freshness", "@sdda-"):
        assert absent not in envelope, absent
    registry = json.loads((data / "sources.json").read_text(encoding="utf-8"))
    assert "max_staleness_hours" not in registry["envelope"], "fraîcheur non déclarée par la source"
    assert all("required_filter" not in s and "max_staleness_hours" not in s for s in registry["sources"])
    assert gst.run(project, mode="check").ok


def test_only_the_granted_tools_exist_and_count_appears_nowhere(tmp_path: Path) -> None:
    project = order_lookup(tmp_path)
    assert gst.run(project, mode="write").ok
    contracts = sorted(p.name for p in (project / CONTRACTS).glob("1-orders-*.tool.md"))
    assert contracts == ["1-orders-lookup.tool.md", "1-orders-search.tool.md"]
    wrappers = sorted(p.name for p in (project / APP_DIR / "data/tools").glob("orders_*.py"))
    assert wrappers == ["orders_lookup.py", "orders_search.py"]
    specs = json.loads((project / APP_DIR / "data/tool_specs.json").read_text(encoding="utf-8"))
    assert sorted(t["name"] for t in specs["tools"]) == ["orders_lookup", "orders_search"]
    search = (project / CONTRACTS / "1-orders-search.tool.md").read_text(encoding="utf-8")
    assert "outil `count`" not in search
    assert '"stale"' not in search and "`stale: true`" not in search, "aucune fraîcheur promise"


def test_a_declared_freshness_makes_staleness_required(tmp_path: Path) -> None:
    project = order_lookup(tmp_path)
    _patch(project, "    pii: [customer_name]\n", "    pii: [customer_name]\n    max_staleness_hours: 48\n")
    assert gst.run(project, mode="write").ok
    envelope = (project / APP_DIR / "data/envelope.py").read_text(encoding="utf-8")
    assert "_check_freshness" in envelope
    assert "`stale: true`" in (project / CONTRACTS / "1-orders-lookup.tool.md").read_text(encoding="utf-8")


def test_a_leftover_reference_runtime_file_is_reported_then_removed(tmp_path: Path) -> None:
    project = order_lookup(tmp_path)
    assert gst.run(project, mode="write", features=ft.ALL).ok
    csv_reader = project / APP_DIR / "data/formats/csv_reader.py"
    assert csv_reader.is_file()
    assert "ARCH_COMPONENT_UNJUSTIFIED" in classes(gst.run(project, mode="check"))
    assert gst.run(project, mode="write").ok
    assert not csv_reader.exists()


PROBE = """#pragma warning disable CA1303, CA1515, CA2007, CA1849, CA1062
using System.Text.Json;
using {app}.Data.Tools;
using {app}.Tools;

namespace {app}.Probe;

internal static class Program
{{
    private static async Task<int> Main(string[] args)
    {{
        var ctx = new ToolContext(args[0]);
        var found = await OrdersLookup.InvokeAsync(Json("{{\\"order_id\\":\\"CMD-1003\\"}}"), ctx, CancellationToken.None);
        var byName = await OrdersSearch.InvokeAsync(Json("{{\\"customer_name\\":\\"Alice Martin\\"}}"), ctx, CancellationToken.None);
        Console.WriteLine(JsonSerializer.Serialize(new {{ found, byName }}));
        return 0;
    }}

    private static JsonElement Json(string text) => JsonDocument.Parse(text).RootElement.Clone();
}}
"""

CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType></PropertyGroup>
  <ItemGroup><None Include="data/**/*.json" CopyToOutputDirectory="PreserveNewest" /></ItemGroup>
</Project>
"""


def test_json_only_csharp_runtime_has_no_csv_jsonl_count_identity_or_staleness(tmp_path: Path) -> None:
    project = order_lookup(tmp_path, language="csharp")
    report = gst.run(project, mode="write")
    assert report.ok, report.render_text()
    data = project / APP_DIR / "data"
    readers = (data / "RecordReaders.cs").read_text(encoding="utf-8")
    assert "ReadJson" in readers
    for absent in ("ReadCsv", "CsvRows", "JsonLinesWithOffsets", "\"tsv\"", "\"jsonl\""):
        assert absent not in readers, absent
    envelope = (data / "DataEnvelope.cs").read_text(encoding="utf-8")
    assert "LookupRecord(" in envelope and "SearchRecords(" in envelope
    for absent in ("CountRecords", "IdentityFilters", "IsStale", "RequiredFilter", "@sdda-"):
        assert absent not in envelope, absent
    assert sorted(p.name for p in (data / "tools").glob("*.cs")) == ["OrdersLookup.cs", "OrdersSearch.cs"]
    assert gst.run(project, mode="check").ok

    dotnet = _dotnet10()
    if dotnet is None:
        pytest.skip("SDK .NET 10 absent : la compilation C# n'est pas simulée")
    app = project / APP_DIR
    (app / "Directory.Build.props").write_text(PROPS, encoding="utf-8")
    (app / f"{APP}.csproj").write_text(CSPROJ, encoding="utf-8")
    (app / "probe").mkdir()
    (app / "probe" / "Program.cs").write_text(PROBE.format(app=APP), encoding="utf-8")
    build = subprocess.run([dotnet, "build", "-nologo", "-v", "q"], cwd=app, capture_output=True, text=True, timeout=600)
    assert build.returncode == 0, build.stdout[-4000:] + build.stderr[-2000:]
    run = subprocess.run([dotnet, "run", "--no-build", "--", str(project)], cwd=app, capture_output=True,
                         text=True, timeout=300, encoding="utf-8")
    assert run.returncode == 0, run.stdout + run.stderr
    out = json.loads(run.stdout.strip().splitlines()[-1])
    found = json.loads(out["found"]["Content"])
    assert out["found"]["Ok"] and found["record"]["order_id"] == "CMD-1003" and "stale" not in found
    by_name = json.loads(out["byName"]["Content"])
    assert {r["customer_name"] for r in by_name["records"]} == {"Alice Martin"} and by_name["truncated"] is False


# ---------------------------------------------------------------------------
# 6. Dépendances
# ---------------------------------------------------------------------------
def _cs_app(project: Path, files: dict[str, str]) -> None:
    app = project / APP_DIR
    for rel, text in files.items():
        target = app / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def test_the_csproj_carries_only_packages_the_code_uses(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    csproj = """<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>
    <PackageReference Include="Microsoft.Agents.AI" />
    <PackageReference Include="Microsoft.Agents.AI.Abstractions" />
    <PackageReference Include="Microsoft.Extensions.Configuration.Json" />
    <PackageReference Include="System.CommandLine" />
    <PackageReference Include="Spectre.Console" />
  </ItemGroup></Project>"""
    _cs_app(project, {
        f"{APP}.csproj": csproj,
        "tests/SupportAssistant.Tests.csproj": '<Project><ItemGroup><PackageReference Include="xunit.v3" /></ItemGroup></Project>',
        "agents/order-assistant/Agent.cs": "using Microsoft.Agents.AI;\nnamespace X;\npublic static class A { }\n",
        "app/Composition.cs": "namespace X;\npublic static class C { public static void B(Microsoft.Extensions.Configuration.IConfigurationBuilder b) => b.AddJsonFile(\"a.json\"); }\n",
        "serving/Program.cs": "using System.CommandLine;\nnamespace X;\npublic static class P { }\n",
    })
    report = vea.run(project, Report(name="P", target="t"), ir={**SINGLE_AGENT_IR, "architecture": ea.derive(SINGLE_AGENT_IR).to_ir()})
    flagged = {m.group(1) for f in report.errors if f.cls == "ARCH_DEPENDENCY_UNUSED"
               for m in [re.search(r"référence `([^`]+)`", f.message)] if m}
    assert flagged == {"Spectre.Console", "Microsoft.Agents.AI.Abstractions"}, report.render_text()
    assert not any("xunit" in f.message for f in report.findings), "le projet de test n'est pas jugé ici"


# ---------------------------------------------------------------------------
# Le livrable C# de la première MISSION, tel qu'il a été sur-généré
# ---------------------------------------------------------------------------
def test_the_over_generated_components_of_the_first_csharp_mission_are_refused(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _cs_app(project, {
        "memory/ConversationWindow.cs": "namespace X.Memory;\npublic sealed class ConversationWindow { }\n",
        "orchestration/Routers.cs": "namespace X.Orchestration;\npublic static class Routers { }\n",
        "orchestration/OrchestrationState.cs": "namespace X.Orchestration;\npublic sealed record OrchestrationState;\n",
        "orchestration/BoundedAgentRunner.cs": "namespace X.Orchestration;\npublic static class BoundedAgentRunner { }\n",
        "data/DataEnvelope.cs": "namespace X.Data;\npublic static class E { public static int CountRecords() => 0; }\n",
    })
    report = vea.run(project, Report(name="P", target="t"), ir={**SINGLE_AGENT_IR, "architecture": ea.derive(SINGLE_AGENT_IR).to_ir()})
    messages = " ".join(f.message for f in report.errors)
    for culprit in ("memory/ConversationWindow.cs", "orchestration/Routers.cs",
                    "orchestration/OrchestrationState.cs", "CountRecords"):
        assert culprit in messages, culprit
    assert "BoundedAgentRunner" not in messages, "les bornes sont une convention obligatoire, pas un excès"


# ---------------------------------------------------------------------------
# 7. Architecture complexe : les capacités restent, conditionnelles
# ---------------------------------------------------------------------------
COMPLEX_IR = {
    "missionId": "2-Support",
    "agents": [
        {"id": "2-router", "tools": [], "handoffs": [{"to": "2-billing"}, {"to": "2-sales"}]},
        {"id": "2-billing", "tools": ["2-invoice"], "retrievers": ["2-kb"]},
        {"id": "2-sales", "tools": []},
    ],
    "tools": [{"id": "2-invoice", "name": "invoice_lookup"}],
    "retrievers": [{"id": "2-kb"}],
    "orchestration": {"rootPattern": "router", "entryNode": "route",
                      "nodes": [{"id": "route", "kind": "router"}, {"id": "billing", "kind": "agent", "ref": "2-billing"},
                                {"id": "sales", "kind": "agent", "ref": "2-sales"}],
                      "edges": [{"from": "route", "to": "billing"}, {"from": "route", "to": "sales"}]},
    "memory": {"shortTermPolicy": "summarize-over", "longTermEnabled": True, "longTermStore": "store-backed",
               "crossAgentSharedState": "scoped"},
    "guardrails": {"input": [{"id": "injection-detection"}, {"id": "pii-redaction"}],
                   "output": [{"id": "schema-validation"}]},
}


def test_a_complex_spec_requires_every_advanced_capability_with_its_reason() -> None:
    arch = ea.derive(COMPLEX_IR)
    for present in ("orchestration.router", "orchestration.graph", "orchestration.delegation",
                    "orchestration.shared-state", "memory.layer", "memory.summarization", "memory.long-term",
                    "conversation.session", "retrieval", "tools.custom", "guardrail.pii-redaction"):
        assert arch.requires(present), present
    assert not arch.requires("orchestration.sequential")
    assert "2-router -> 2-billing" in " ".join(arch.justified["orchestration.delegation"])


def test_a_complex_project_still_receives_router_and_memory_and_its_ir_says_why(tmp_path: Path) -> None:
    project = make_project(tmp_path)                                  # router, 2 agents, handoffs, RAG
    path, compiled = ir_compiler.compile_to_file(project, 1)
    assert compiled.ok, compiled.render_text()
    architecture = json.loads(path.read_text(encoding="utf-8"))["architecture"]
    for present in ("orchestration.router", "orchestration.graph", "orchestration.shared-state",
                    "memory.layer", "retrieval"):
        assert architecture["required"][present], present
    assert "data.format.delimited" in architecture["omitted"]

    assert gen_app_skeleton.run(project, mode="write", mission="1").ok
    app = project / APP_DIR
    assert (app / "orchestration/router.py").is_file()
    assert (app / "memory/__init__.py").is_file()
    assert not (app / "orchestration/sequential.py").exists(), "pattern router, pas sequential"
    assert not (app / "guardrails/pii.py").exists(), "pii-redaction non déclaré dans cette fixture"


def test_the_ir_schema_accepts_the_effective_architecture(tmp_path: Path) -> None:
    from sdda_scripts import validate_ir

    project = make_project(tmp_path)
    ir_compiler.compile_to_file(project, 1)
    code = validate_ir.main(["--root", str(project), "--mission", "1", "--no-report"])
    assert code == 0
