#!/usr/bin/env python3
"""PROPORTIONALITÉ — le livrable ne porte que ce que la spec exige (0 token).

Part `proportionality` de G6 (contributive : absente, elle ne bloque pas ;
rouge, elle bloque). Ce script tient la règle de l'architecture effective :

    architecture possible ≠ architecture générée.

L'IR porte la décision (`architecture.required`, chaque capacité avec
l'exigence qui la justifie — `sdda_lib/effective_architecture.py`). Les
générateurs la suivent ; les agents `dev-*`, eux, écrivent du code, et rien ne
vérifiait qu'ils n'ajoutaient pas « au cas où » un routeur à un agent seul, une
couche `memory/` que personne n'appelle, un lecteur CSV pour une source JSON
ou un paquet NuGet sans un seul `using`. C'est ce qu'a livré la première
application C# : `Routers.cs`, `OrchestrationState.cs`,
`memory/ConversationWindow.cs`, `Spectre.Console` — sans exigence.

Trois contrôles, sur `workspace/src/{AppName}/` (tests exclus : un test suit le
composant qu'il teste) :

1. **Composants** — un fichier que `COMPONENT_RULES` rattache à une capacité
   (`memory/`, `orchestration/Routers.cs`, `retrieval/`, `formats/csv_reader.py`…)
   exige cette capacité. -> `[ARCH_COMPONENT_UNJUSTIFIED]`.
2. **Symboles** — un symbole qui n'existe que pour une capacité (`CountRecords`,
   `IsStale`, `IdentityFilters`, `ReadCsv`, `RouterGraph`, `OrchestrationState`…)
   exige cette capacité, où qu'il soit écrit. -> `[ARCH_COMPONENT_UNJUSTIFIED]`.
3. **Dépendances (.NET)** — chaque `PackageReference` du projet principal doit
   être UTILISÉ par le code : un `using`, un nom qualifié, ou la preuve d'usage
   que le paquet laisse (`AddJsonFile(` pour `Configuration.Json`…). Un paquet
   déjà apporté par un autre paquet référencé est redondant. ->
   `[ARCH_DEPENDENCY_UNUSED]`.

Ce que le script ne fait pas : juger qu'une capacité EXIGÉE est bien présente.
C'est le travail des gates de couche (G3 à G6) et des evals — ici, on ne
refuse que l'excès.

Usage :
    python .sdda/sdda.py validate-effective-architecture --mission 1 [--json] [--no-report]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import effective_architecture as ea  # noqa: E402
from sdda_lib import hashing, markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import app_name  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402
from sdda_scripts.validate_mission import mission_artifact  # noqa: E402

#: Suffixes de code lus. Les données (`*.json`) et les prompts ne sont pas des composants.
CODE_SUFFIXES = frozenset({".py", ".cs", ".ts", ".js", ".kt", ".java"})
#: Répertoires jamais lus : sorties de build, dépendances, caches.
SKIP_DIRS = frozenset({"bin", "obj", "node_modules", "dist", "build", ".gradle", "target", "out",
                       "__pycache__", ".venv", "venv", ".mypy_cache", ".ruff_cache", ".pytest_cache"})

#: Symboles qui n'existent que pour une capacité : motif -> capacité (ou disjonction).
#: Mots entiers, sensibles à la casse — un symbole est un nom, pas une idée.
SYMBOL_RULES: tuple[tuple[str, str], ...] = (
    (r"\b(ReadCsv|read_csv|CsvRows)\b", "data.format.delimited"),
    (r"\b(JsonLinesWithOffsets|JsonLineAt|_jsonl_rows)\b", "data.format.jsonl"),
    (r"\b(CountRecords|count_records)\b", "data.tool.count"),
    (r"\b(SearchRecords|search_records)\b", "data.tool.search"),
    (r"\b(LookupRecord|lookup_record)\b", "data.tool.lookup"),
    (r"\b(IsStale|_check_freshness)\b", "data.staleness"),
    (r"\b(IdentityFilters|_identity_filters)\b", "data.identity-filter"),
    (r"\b(RouterGraph|IntentRouter|RouteDecision)\b", "orchestration.router"),
    (r"\b(SequentialGraph|SequentialPipeline)\b", "orchestration.sequential"),
    (r"\b(OrchestrationState|SharedState)\b", "orchestration.shared-state"),
    (r"\b(ConversationWindow|ConversationSummarizer|LongTermMemory|MemoryStore)\b", "memory.layer"),
    (r"\b(VectorStore|IVectorStore|Retriever|IRetriever)\b", "retrieval"),
)

#: Preuve d'usage des paquets dont le NOM n'est pas l'espace de noms importé
#: (méthodes d'extension, types d'un autre espace). Un paquet absent d'ici est
#: prouvé par `using {Paquet}` ou `{Paquet}.` qualifié.
PACKAGE_EVIDENCE: dict[str, str] = {
    "Microsoft.Extensions.Hosting": r"\bHost\.Create|\bIHost\b|\bHostApplicationBuilder\b|using Microsoft\.Extensions\.Hosting\b",
    "Microsoft.Extensions.Configuration": r"\bConfigurationBuilder\b|\bIConfiguration(Root)?\b|using Microsoft\.Extensions\.Configuration\b",
    "Microsoft.Extensions.Configuration.Json": r"\.AddJsonFile\(|\.AddJsonStream\(",
    "Microsoft.Extensions.Configuration.EnvironmentVariables": r"\.AddEnvironmentVariables\(",
    "Microsoft.Extensions.Configuration.FileExtensions": r"\.SetBasePath\(|\.SetFileProvider\(",
    "Microsoft.Extensions.Configuration.UserSecrets": r"\.AddUserSecrets",
    "Microsoft.Extensions.Logging.Console": r"\.AddConsole\(|\.AddSimpleConsole\(|\.AddJsonConsole\(",
    "Microsoft.Extensions.Http.Resilience": r"\.AddResilienceHandler\(|\.AddStandardResilienceHandler\(",
    "Microsoft.Extensions.AI.OpenAI": r"\.AsIChatClient\(|\.AsChatClient\(|\.AsEmbeddingGenerator\(",
    "Microsoft.Agents.AI.OpenAI": r"\.AsAIAgent\(|\.CreateAIAgent\(|using Microsoft\.Agents\.AI\.OpenAI\b",
    "Microsoft.Agents.AI.Workflows": r"using Microsoft\.Agents\.AI\.Workflows\b|\bWorkflowBuilder\b",
    "OpenTelemetry.Extensions.Hosting": r"\.AddOpenTelemetry\(",
    "OpenTelemetry.Exporter.OpenTelemetryProtocol": r"\.AddOtlpExporter\(|\bOtlpExporter",
    "OpenTelemetry.Exporter.InMemory": r"\.AddInMemoryExporter\(",
    "OpenTelemetry.Exporter.Console": r"\.AddConsoleExporter\(",
    "System.ClientModel": r"using System\.ClientModel\b|\bApiKeyCredential\b",
    "Spectre.Console": r"using Spectre\.Console\b|\bAnsiConsole\b",
    "ModelContextProtocol": r"using ModelContextProtocol\b|\bMcpClient",
}

#: Paquet -> paquet référencé qui l'apporte déjà (dépendance transitive
#: déclarée) : le référencer en plus n'ajoute rien, sinon une ligne à maintenir.
PACKAGE_REDUNDANT_WITH: dict[str, str] = {
    "Microsoft.Agents.AI.Abstractions": "Microsoft.Agents.AI",
    "Microsoft.Extensions.AI.Abstractions": "Microsoft.Extensions.AI",
    "Microsoft.Extensions.Configuration.FileExtensions": "Microsoft.Extensions.Configuration.Json",
}

_PACKAGE_RE = re.compile(r'<PackageReference\s+Include="([^"]+)"', re.I)


def _is_test(rel: str) -> bool:
    return bool(re.search(r"(^|/)tests?/", rel, re.I)) or bool(re.search(r"(^|/)test_[^/]+$|Tests?\.cs$", rel))


def source_files(app_root: Path) -> dict[str, str]:
    """{chemin relatif POSIX: texte} du code applicatif, tests exclus."""
    out: dict[str, str] = {}
    if not app_root.is_dir():
        return out
    for path in sorted(app_root.rglob("*")):
        if not path.is_file() or path.suffix not in CODE_SUFFIXES:
            continue
        rel = path.relative_to(app_root).as_posix()
        if any(part in SKIP_DIRS for part in rel.split("/")[:-1]) or _is_test(rel):
            continue
        out[rel] = markdown_io.read_text(path)
    return out


def check_components(files: dict[str, str], features: frozenset[str], loc: str, report: Report) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for rel in files:
        capability = ea.component_capability(rel)
        if capability and not ea.satisfied(capability, features):
            findings.append({"file": rel, "capability": capability, "kind": "component"})
            report.error(
                "ARCH_COMPONENT_UNJUSTIFIED",
                f"`{rel}` est un composant `{capability}`, que la spec n'exige pas",
                "retirer le composant ; si la capacité est voulue, c'est la SPEC qui la déclare (roster, "
                "pattern, mémoire, source, guardrail) — l'IR `architecture.required` dit ce qui est exigé et pourquoi",
                f"{loc}{rel}")
    return findings


def check_symbols(files: dict[str, str], features: frozenset[str], loc: str, report: Report) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    compiled = [(re.compile(pattern), capability) for pattern, capability in SYMBOL_RULES]
    for rel, text in files.items():
        for pattern, capability in compiled:
            if ea.satisfied(capability, features):
                continue
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                findings.append({"file": f"{rel}:{line}", "capability": capability, "symbol": match.group(0),
                                 "kind": "symbol"})
                report.error(
                    "ARCH_COMPONENT_UNJUSTIFIED",
                    f"`{rel}:{line}` définit ou appelle `{match.group(0)}` (capacité `{capability}`), "
                    "que la spec n'exige pas",
                    "retirer le code : une porte, un lecteur ou un moteur sans exigence n'est pas « gratuit » — il se "
                    "compile, se teste, se maintient et élargit la surface d'attaque",
                    f"{loc}{rel}")
    return findings


def main_projects(app_root: Path) -> list[Path]:
    """Les `.csproj` du livrable, projets de test exclus."""
    if not app_root.is_dir():
        return []
    return [p for p in sorted(app_root.rglob("*.csproj"))
            if not any(part in SKIP_DIRS for part in p.relative_to(app_root).parts[:-1])
            and not _is_test(p.relative_to(app_root).as_posix())]


def package_used(package: str, code: str) -> bool:
    evidence = PACKAGE_EVIDENCE.get(package)
    if evidence and re.search(evidence, code):
        return True
    escaped = re.escape(package)
    return bool(re.search(rf"using\s+(static\s+)?{escaped}(\.|;|\s)", code) or re.search(rf"\b{escaped}\.[A-Z]", code))


def check_packages(app_root: Path, files: dict[str, str], root: Path, report: Report) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    code = "\n".join(text for rel, text in files.items() if rel.endswith(".cs"))
    for project in main_projects(app_root):
        rel_project = paths.rel(root, project)
        referenced = _PACKAGE_RE.findall(markdown_io.read_text(project))
        for package in referenced:
            provider = PACKAGE_REDUNDANT_WITH.get(package)
            if provider and provider in referenced:
                findings.append({"project": rel_project, "package": package, "reason": f"apporté par {provider}"})
                report.error("ARCH_DEPENDENCY_UNUSED",
                             f"`{rel_project}` référence `{package}`, déjà apporté par `{provider}`",
                             f"retirer la référence : `{provider}` en dépend, la déclarer en plus n'ajoute qu'une "
                             "ligne à maintenir", rel_project)
                continue
            if not package_used(package, code):
                findings.append({"project": rel_project, "package": package, "reason": "aucun usage"})
                report.error("ARCH_DEPENDENCY_UNUSED",
                             f"`{rel_project}` référence `{package}`, qu'aucun fichier du livrable n'utilise",
                             "retirer la référence (et sa `PackageVersion` si plus rien ne l'utilise) : le catalogue "
                             "`.libs.json` dit la version À ÉPINGLER SI le code s'en sert, pas ce qu'il faut installer",
                             rel_project)
    return findings


def run(root: Path, report: Report, *, mission: str | None = None, ir: dict[str, Any] | None = None) -> Report:
    app = app_name(root)
    app_root = paths.app_src_root(root, app)
    loc = f"workspace/src/{app}/"
    arch_ir = (ir or ea.load_ir(root, mission)).get("architecture")
    # L'IR fait foi s'il porte la décision ; sinon elle est redérivée à
    # l'identique (même fonction), et le rapport le dit.
    if isinstance(arch_ir, dict) and isinstance(arch_ir.get("required"), dict):
        features = frozenset(arch_ir["required"])
        source = "ir"
    else:
        features = ea.derive(ir, root=root, mission=mission).features()
        source = "derived"
    report.data.update({"app": loc, "architectureFrom": source, "required": sorted(features)})
    if not app_root.is_dir():
        report.data["applicable"] = False
        report.warn("ARCH_COMPONENT_UNJUSTIFIED", f"`{loc}` absent : rien à confronter",
                    "construire l'application (/sdda-build) avant l'ORCH GATE", loc)
        return report
    files = source_files(app_root)
    report.data["files"] = len(files)
    findings = check_components(files, features, loc, report)
    findings += check_symbols(files, features, loc, report)
    findings += check_packages(app_root, files, root, report)
    report.data["findings"] = findings
    return report


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="Le livrable ne porte que ce que la spec exige.")
    add_common_args(parser)
    parser.add_argument("--mission", default=None, help="numéro de MISSION (IR lue, artefact du rapport)")
    args = parser.parse_args(argv)
    root = resolve_root(args)
    report = run(root, Report(name="PROPORTIONALITY", target=str(root)), mission=args.mission)
    if not args.no_report and report.data.get("applicable", True):
        ir_file = paths.ir_path(root, args.mission) if args.mission else None
        pins = {"ir": hashing.sha256_file(ir_file)} if ir_file is not None and ir_file.is_file() else {}
        write_gate_report(root, "G6", mission_artifact(root, args.mission), report, pins, part="proportionality")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
