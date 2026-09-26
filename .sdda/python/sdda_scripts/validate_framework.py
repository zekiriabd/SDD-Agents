#!/usr/bin/env python3
"""FRAMEWORK — le code généré utilise le framework déclaré, et lui seul (0 token).

Part `framework` de G6. `## Active Agent Framework` est une déclaration ; le
squelette généré n'importe ni LangChain ni LangGraph (il n'impose que la
coquille), et ce sont les `dev-*` qui écrivent agents et orchestration. Rien ne
vérifiait qu'ils écrivaient avec la stack choisie : un `dev-orchestration` qui
code son routeur en `if/else` sur le SDK brut, ou un `dev-agent` qui importe
CrewAI « parce que c'est plus court », livre un système dont la fiche de stack,
les pins `.libs.json` et les idiomes relus en revue ne décrivent plus rien.

Deux contrôles, sur les imports Python (`ast`, aucun import exécuté) de
`workspace/src/{AppName}/` :

1. **Le framework déclaré est importé là où sa fiche le place** —
   `langgraph` dans `orchestration/` (fiche langgraph.md : « un fichier nomme le
   framework »), `langchain*` dans `agents/` (fiche langchain.md §3.3 : la
   boucle d'agent). Absent -> `[FRAMEWORK_DRIFT]`.
2. **Aucun framework concurrent non déclaré n'est importé**, tests compris —
   `crewai`, `llama_index`, `autogen`, `pydantic_ai`… ; et `langgraph` (ou
   `langchain.agents.create_agent`, un graphe LangGraph sous le capot) sans
   `framework/langgraph.md` actif. -> `[FRAMEWORK_DRIFT]`.

Hors Python (C#, TypeScript, Kotlin, Java), les imports sont lus par motif —
`using X;`, `import … from "x"`, `import x.y` — sans parseur par langage : les
formes d'import de ces langages sont régulières, et c'est tout ce que le
contrôle lit. Le framework déclaré doit apparaître dans l'une des couches que
sa fiche nomme (`NATIVE_DECLARED`) ; ses concurrents connus
(`NATIVE_COMPETITORS`) nulle part. Un langage sans table : non vérifiable, dit
(`WARN`), et la part n'est pas écrite — jamais verte par défaut silencieux.

Usage :
    python .sdda/sdda.py validate-framework --mission 1 [--json] [--no-report]
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import active_stacks, app_name  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402
from sdda_scripts.validate_mission import mission_artifact  # noqa: E402

#: Fiche de framework -> (préfixes de modules, répertoires où l'import est EXIGÉ).
#: Les répertoires sont ceux que la fiche nomme ; un framework de fiche
#: non Python (ms-agent-framework, langgraph-js, spring-ai) n'est pas ici.
DECLARED: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "langchain": (("langchain", "langchain_core", "langchain_anthropic", "langchain_openai",
                   "langchain_google_genai", "langchain_mcp_adapters", "langchain_text_splitters",
                   "langchain_postgres", "langchain_community"), ("agents",)),
    "langgraph": (("langgraph",), ("orchestration",)),
}

#: Frameworks agentiques Python qu'aucune fiche active ne déclare : leur import
#: est une architecture parallèle. Le SDK du fournisseur (anthropic, openai)
#: n'en fait pas partie — c'est la couche modèle, pas un framework.
COMPETITORS: dict[str, str] = {
    "crewai": "crewai", "llama_index": "llamaindex", "autogen": "autogen", "autogen_agentchat": "autogen",
    "autogen_core": "autogen", "pydantic_ai": "pydantic-ai", "agno": "agno", "google.adk": "google-adk",
    "semantic_kernel": "semantic-kernel", "smolagents": "smolagents", "haystack": "haystack", "dspy": "dspy",
    "langroid": "langroid", "swarm": "swarm",
}

#: `langchain.agents.create_agent` (1.x) est un graphe LangGraph : l'utiliser
#: sans langgraph.md, c'est activer LangGraph sans le déclarer (fiche langchain.md).
HIDDEN_LANGGRAPH = ("langchain.agents",)

#: Hors Python — langage -> (suffixes de fichiers, motif d'import).
NATIVE_IMPORTS: dict[str, tuple[tuple[str, ...], re.Pattern[str]]] = {
    "csharp": ((".cs",), re.compile(r"^\s*(?:global\s+)?using\s+(?:static\s+)?(?:\w+\s*=\s*)?([A-Za-z_][\w.]*)\s*;", re.M)),
    "typescript": ((".ts", ".tsx", ".mts"),
                   re.compile(r"""(?:\bfrom\s+|\bimport\s*\(?\s*|\brequire\s*\(\s*)["']([^"']+)["']""")),
    "kotlin": ((".kt", ".kts"), re.compile(r"^\s*import\s+([\w.]+)", re.M)),
    "java": ((".java",), re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)", re.M)),
}

#: Fiche de framework non Python -> (préfixes, couches dont UNE au moins l'importe).
#: « Au moins une » et non « chacune » : ces fiches placent le framework dans
#: l'orchestration OU la boucle d'agent selon le pattern (single-agent n'a pas
#: d'orchestration propre).
NATIVE_DECLARED: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "ms-agent-framework": (("Microsoft.Agents.AI",), ("orchestration", "agents")),
    "langgraph-js": (("@langchain/langgraph",), ("orchestration", "agents")),
    "spring-ai": (("org.springframework.ai",), ("agents", "orchestration")),
}

#: Concurrents non Python : un framework agentique qu'aucune fiche active ne déclare.
NATIVE_COMPETITORS: dict[str, dict[str, str]] = {
    "csharp": {"Microsoft.SemanticKernel": "semantic-kernel", "LangChain": "langchain-dotnet",
               "AutoGen": "autogen"},
    "typescript": {"ai": "vercel-ai-sdk", "@mastra": "mastra", "llamaindex": "llamaindex",
                   "@openai/agents": "openai-agents-js", "langchain/agents": "langchain-js-agents"},
    "kotlin": {"dev.langchain4j": "langchain4j", "com.embabel": "embabel"},
    "java": {"dev.langchain4j": "langchain4j", "com.embabel": "embabel"},
}


def _imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.append(node.module)
    return out


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def scan(app_root: Path) -> dict[str, list[str]]:
    """{chemin relatif POSIX: [modules importés]} pour chaque `.py` de l'application."""
    out: dict[str, list[str]] = {}
    for p in sorted(app_root.rglob("*.py")):
        if any(part in (".venv", "__pycache__", "node_modules") for part in p.parts):
            continue
        out[p.relative_to(app_root).as_posix()] = _imports(p)
    return out


def run(root: Path, report: Report) -> Report:
    frameworks = active_stacks(root, "Active Agent Framework")
    languages = active_stacks(root, "Active Language & Runtime")
    app = app_name(root)
    app_root = paths.app_src_root(root, app)
    loc = f"workspace/src/{app}/"
    report.data.update({"declared": frameworks, "app": loc})

    if len(languages) == 1 and languages[0] in NATIVE_IMPORTS:
        return _run_native(root, report, languages[0], frameworks, app_root, loc)
    if languages != ["python"]:
        report.warn("FRAMEWORK_DRIFT", f"langage `{'/'.join(languages) or '?'}` : imports non vérifiables par ce script",
                    "le contrôle lit Python, C#, TypeScript, Kotlin et Java ; la revue (review-spec) doit confronter "
                    "le code à la fiche", loc)
        # NON APPLICABLE, et dit comme tel : `main` n'écrit alors aucun
        # rapport. Un avertissement laissait `ok: true`, donc une part
        # `framework` VERTE sur un code que personne n'avait lu — le « jamais
        # vert par défaut silencieux » du docstring, contredit par le verdict.
        report.data["applicable"] = False
        return report
    if not app_root.is_dir():
        report.error("FRAMEWORK_DRIFT", f"`{loc}` absent : rien ne prouve que `{', '.join(frameworks)}` est utilisé",
                     "construire l'application (/sdda-build) avant l'ORCH GATE", loc)
        return report

    files = scan(app_root)
    report.data["files"] = len(files)
    declared = set(frameworks)

    # 1. Le framework déclaré, là où sa fiche le met.
    for fw in sorted(declared & set(DECLARED)):
        prefixes, required_in = DECLARED[fw]
        for directory in required_in:
            hits = [f for f, mods in files.items()
                    if f.startswith(directory + "/") and any(_matches(m, p) for m in mods for p in prefixes)]
            if not hits:
                report.error("FRAMEWORK_DRIFT",
                             f"`framework/{fw}.md` est déclaré, mais aucun module de `{loc}{directory}/` n'importe "
                             f"`{prefixes[0]}`",
                             f"implémenter `{directory}/` avec {fw} comme la fiche le décrit, ou retirer "
                             f"`framework/{fw}.md` de `## Active Agent Framework` : une déclaration que le code "
                             "ne suit pas fait relire en revue une architecture qui n'existe pas", f"{loc}{directory}/")

    # 2. Rien d'autre.
    for rel, mods in sorted(files.items()):
        for mod in mods:
            for prefix, name in COMPETITORS.items():
                if _matches(mod, prefix):
                    report.error("FRAMEWORK_DRIFT", f"`{rel}` importe `{mod}` ({name}), framework non déclaré",
                                 f"réécrire avec {', '.join(frameworks) or 'le framework déclaré'}, ou déclarer "
                                 f"`framework/{name}.md` (fiche absente : elle refusera au preflight)", f"{loc}{rel}")
            if "langgraph" not in declared and (_matches(mod, "langgraph")
                                                or any(_matches(mod, h) for h in HIDDEN_LANGGRAPH)):
                report.error("FRAMEWORK_DRIFT", f"`{rel}` importe `{mod}` sans `framework/langgraph.md` actif",
                             "activer langgraph.md (et en assumer les bornes) ou écrire la boucle de la fiche "
                             "langchain.md §3.3 — `create_agent` est un graphe LangGraph sous le capot", f"{loc}{rel}")
    return report


_NATIVE_SKIP = {"bin", "obj", "node_modules", "dist", "build", ".gradle", "target", "out"}


def scan_native(app_root: Path, language: str) -> dict[str, list[str]]:
    """{chemin relatif POSIX: [imports]} des sources d'un langage non Python."""
    suffixes, pattern = NATIVE_IMPORTS[language]
    out: dict[str, list[str]] = {}
    for p in sorted(app_root.rglob("*")):
        if p.suffix not in suffixes or not p.is_file():
            continue
        rel = p.relative_to(app_root)
        if any(part in _NATIVE_SKIP for part in rel.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        out[rel.as_posix()] = pattern.findall(text)
    return out


def _native_match(module: str, prefix: str, language: str) -> bool:
    if language == "typescript":
        return module == prefix or module.startswith(prefix + "/")
    return module == prefix or module.startswith(prefix + ".")


def _run_native(root: Path, report: Report, language: str, frameworks: list[str], app_root: Path,
                loc: str) -> Report:
    if not app_root.is_dir():
        report.error("FRAMEWORK_DRIFT", f"`{loc}` absent : rien ne prouve que `{', '.join(frameworks)}` est utilisé",
                     "construire l'application (/sdda-build) avant l'ORCH GATE", loc)
        return report
    files = scan_native(app_root, language)
    report.data.update({"files": len(files), "language": language})
    declared = set(frameworks)
    for fw in sorted(declared - set(NATIVE_DECLARED)):
        # Un framework d'un autre langage (`langchain` sur un projet C#) ne se
        # vérifie pas ici : laissé passer, il rendait une part VERTE sur un
        # code que rien n'avait confronté à sa fiche.
        report.error("FRAMEWORK_DRIFT", f"`framework/{fw}.md` n'a pas de voie {language} connue de ce contrôle",
                     f"déclarer le framework de la fiche `lang/{language}.md` (ms-agent-framework, langgraph-js, "
                     "spring-ai) — un framework d'un autre langage est refusé au preflight "
                     "([STACK_LANGUAGE_MISMATCH])", loc)
    for fw in sorted(declared & set(NATIVE_DECLARED)):
        prefixes, layers = NATIVE_DECLARED[fw]
        hits = [f for f, mods in files.items()
                if f.split("/", 1)[0].casefold() in layers
                and any(_native_match(m, p, language) for m in mods for p in prefixes)]
        if not hits:
            report.error("FRAMEWORK_DRIFT",
                         f"`framework/{fw}.md` est déclaré, mais aucun fichier de `{loc}{{{','.join(layers)}}}/` "
                         f"n'importe `{prefixes[0]}`",
                         f"implémenter la boucle ou le graphe avec {fw} comme la fiche le décrit, ou retirer "
                         f"`framework/{fw}.md` de `## Active Agent Framework`", loc)
    for rel, mods in sorted(files.items()):
        for mod in mods:
            for prefix, name in NATIVE_COMPETITORS.get(language, {}).items():
                if _native_match(mod, prefix, language):
                    report.error("FRAMEWORK_DRIFT", f"`{rel}` importe `{mod}` ({name}), framework non déclaré",
                                 f"réécrire avec {', '.join(frameworks) or 'le framework déclaré'}, ou déclarer "
                                 f"`framework/{name}.md` (fiche absente : elle refusera au preflight)", f"{loc}{rel}")
    return report


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="Le code généré utilise le framework déclaré, et lui seul.")
    add_common_args(parser)
    parser.add_argument("--mission", default=None, help="numéro de MISSION (artefact du rapport de gate) ; défaut : `stack`")
    args = parser.parse_args(argv)
    root = resolve_root(args)
    report = run(root, Report(name="FRAMEWORK", target=str(root)))
    if not args.no_report and report.data.get("applicable", True):
        # Épinglé sur STACK.md (le framework déclaré) : vide, une part verte
        # survivait à un changement de `## Active Agent Framework`.
        stack = paths.stack_md_path(root)
        pins = {"stack": hashing.sha256_file(stack)} if stack.is_file() else {}
        write_gate_report(root, "G6", mission_artifact(root, args.mission), report, pins, part="framework")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
