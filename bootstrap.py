#!/usr/bin/env python3
"""
SDD_Agents — bootstrap d'un nouveau projet agentic.

Génère `workspace/STACK.md`, l'arborescence du workspace, et vérifie que
l'installation tient debout.

Usage :
    python bootstrap.py                      # interactif
    python bootstrap.py --combo c1           # combo validée, sans question
    python bootstrap.py --combo c1 --auto    # CI : aucune interaction

Variables d'environnement pour le mode CI :
    SDDA_APP_NAME, SDDA_COMBO

Principe : ce script ne fait AUCUN appel LLM et n'installe rien sans le dire.
Il pose peu de questions, et chacune a une conséquence architecturale réelle.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SDDA = ROOT / ".sdda"
WORKSPACE = ROOT / "workspace"
TEMPLATE = SDDA / "templates" / "STACK.md.template"

# L'outillage du framework tourne dès 3.11 (tomllib, exception groups).
# Le PROJET GÉNÉRÉ cible 3.12+ — c'est déclaré dans stacks/lang/python.md,
# et c'est une contrainte différente qu'il ne faut pas confondre.
MIN_PYTHON = (3, 11)


# ---------------------------------------------------------------------------
# Arborescence du workspace
# ---------------------------------------------------------------------------
WORKSPACE_TREE = [
    "stack",
    # STACK.md est gitignoré ; les manifestes de sources, eux, sont versionnés :
    # sans eux, la surface de données du projet ne serait relue nulle part.
    "stack/sources",
    "contracts/dataaccess/schemas",
    "missions",
    "caps",
    "topology",
    "contracts/agents",
    "contracts/tools",
    "contracts/retrieval",
    "contracts/memory",
    "prompts",
    "datasets/golden",
    "datasets/holdout",
    "datasets/calibration",
    "datasets/adversarial",
    "evals/suites",
    "evals/baselines",
    "evals/reports",
    "evals/calibration",
    "traces/runs",
    "src",
    "docs",
    ".sys/.ir",
    ".sys/.context/adrs",
    ".sys/.context/packs",
    ".sys/.state",
    ".sys/.validation",
    ".sys/.audit",
    ".sys/.routing",
    ".sys/.cache",
]


# ---------------------------------------------------------------------------
# Combos
# ---------------------------------------------------------------------------
@dataclass
class Combo:
    """Une composition de stack.

    `status` est délibérément honnête : rien n'est annoncé « validé » tant
    qu'un run mesuré ne l'a pas établi. C'est exactement le faux vert que ce
    framework existe pour empêcher.
    """

    id: str
    label: str
    status: str
    language: str
    frameworks: list[str]
    orchestration: str
    rag: str
    vectorstore: str
    embedding: str
    dataaccess: str
    database: str
    memory: str
    observability: str
    evalstack: str
    serving: str
    # CE QU'ON LIVRE, distinct de `serving` qui dit PAR OÙ L'ON ENTRE. Porté par
    # la combo et non par le gabarit : une valeur figée dans STACK.md.template
    # contredirait toute combo qui ne livre pas un back-end, et l'incohérence ne
    # se verrait qu'au premier `validate_packaging`.
    # Contrairement à `vectorstore` et `embedding`, `rerank/none.md` EXISTE : une
    # absence de reranking est une décision qui se documente et se mesure (le
    # diagnostic tient en deux chiffres — cf. la fiche), pas un trou. Le défaut
    # est donc une fiche réelle, jamais une ligne vide. Toutes les combos le
    # prennent : aucune n'a encore de mesure justifiant un reranker.
    reranker: str = "none"
    deliverable: str = "cli-exe"
    api_framework: str = "none"
    # `none` n'est tenable que si la combo ne touche pas de données : sinon les
    # vues SQL par agent filtrent sur une identité que personne n'a établie.
    # Vérifié par validate_packaging -> [PACKAGING_IDENTITY_UNESTABLISHED].
    api_auth: str = "none"
    guardrails: list[str] = field(default_factory=lambda: ["injection-detection", "schema-validation"])
    tools: list[str] = field(default_factory=list)


COMBOS: dict[str, Combo] = {
    "c1": Combo(
        id="c1",
        label="Python + LangGraph + RAG hybride (pgvector) + PostgreSQL + MCP + CLI",
        status="design-phase",
        language="python",
        frameworks=["langchain", "langgraph"],
        orchestration="router",
        rag="hybrid",
        vectorstore="pgvector",
        embedding="voyage",
        dataaccess="view-per-agent",
        database="PostgreSql",
        memory="buffer",
        observability="otel-genai",
        evalstack="pytest-eval",
        serving="cli",
        tools=["mcp"],
    ),
    "c1-api": Combo(
        id="c1-api",
        label="Idem C1, exposé en API FastAPI + SSE",
        status="untested",
        language="python",
        frameworks=["langchain", "langgraph"],
        orchestration="router",
        rag="hybrid",
        vectorstore="pgvector",
        embedding="voyage",
        dataaccess="view-per-agent",
        database="PostgreSql",
        memory="buffer",
        observability="otel-genai",
        evalstack="pytest-eval",
        serving="fastapi-sse",
        deliverable="backend-api",
        api_framework="fastapi",
        api_auth="oauth2",
        tools=["mcp"],
    ),
    "sources": Combo(
        id="sources",
        label="Python + LangGraph, sources déclarées (fichiers + API + MCP), sans base",
        status="untested",
        language="python",
        frameworks=["langchain", "langgraph"],
        orchestration="router",
        rag="none",
        vectorstore="none",
        embedding="none",
        dataaccess="declared-sources",
        database="none",
        memory="buffer",
        observability="otel-genai",
        evalstack="pytest-eval",
        serving="cli",
        tools=["mcp"],
    ),
    "batch": Combo(
        id="batch",
        label="Python + LangGraph, traitement par lot (N entrées, reprise, budget par item)",
        status="untested",
        language="python",
        frameworks=["langchain", "langgraph"],
        orchestration="sequential",
        rag="none",
        vectorstore="none",
        embedding="none",
        dataaccess="declared-sources",
        database="none",
        memory="buffer",
        observability="otel-genai",
        evalstack="pytest-eval",
        serving="batch",
        deliverable="batch-job",
        tools=["mcp"],
    ),
    # --- `dotnet-api` RETIRÉE le 2026-09-22 -----------------------------------
    #
    # Elle déclarait `observability="otel-genai"` et `evalstack="pytest-eval"`
    # sur `language="csharp"`. Les deux fiches existent, donc l'ancien
    # `preflight_stack_combo` — qui ne regardait que l'existence du fichier —
    # la laissait passer. Les deux sont écrites en Python et le disent
    # (« Suppose `lang/python.md` ») : un bootstrap `--combo dotnet-api`
    # produisait un STACK.md dont le générateur .NET reçoit du pytest et du
    # psycopg comme référence d'implémentation.
    #
    # `[STACK_LANGUAGE_MISMATCH]` le refuse désormais, et le test
    # `test_every_combo_is_loadable[dotnet-api]` l'a montré à la première
    # exécution. Offrir dans le menu une combinaison que la gate du framework
    # rejette est exactement le faux vert que ce projet existe pour empêcher :
    # elle sort du catalogue plutôt que de mentir.
    #
    # Ce qu'il faut pour la rétablir — rien de plus, rien de moins :
    #   - `.sdda/stacks/eval/dotnet-test.md`        (xunit.v3 est déjà épinglé
    #     dans framework/ms-agent-framework.libs.json)
    #   - une fiche d'observabilité .NET, ou `Languages: python, csharp` sur
    #     `observability/otel-genai.md` une fois qu'elle porte les deux
    #     implémentations — les paquets OTel .NET sont déjà épinglés dans
    #     `serving/aspnet-minimal.libs.json` et `ms-agent-framework.libs.json`.
    # Le RAG .NET (vectorstore, rag, rerank, dataaccess) reste hors périmètre :
    # ROADMAP Lot 7.
    #
    # `lang/csharp.md`, `framework/ms-agent-framework.md` et
    # `serving/aspnet-minimal.md` restent activables à la main — c'est la
    # COMBINAISON préfabriquée qui était incohérente, pas les fiches.
    # --------------------------------------------------------------------------
    "minimal": Combo(
        id="minimal",
        label="Python + LangChain, agent unique, sans RAG ni base",
        status="untested",
        language="python",
        frameworks=["langchain"],
        orchestration="single-agent",
        rag="none",
        vectorstore="none",
        embedding="none",
        dataaccess="none",
        database="none",
        memory="buffer",
        observability="otel-genai",
        evalstack="pytest-eval",
        serving="cli",
    ),
}

STATUS_BADGE = {
    "validated": "🟢 validée de bout en bout",
    "bench-validated": "🟢 runtime mesuré",
    "design-phase": "🟡 cible du MVP — pas encore validée par un run mesuré",
    "untested": "🔴 non testée — le pipeline peut échouer de façon non triviale",
}


# ---------------------------------------------------------------------------
# Sortie
# ---------------------------------------------------------------------------
# La console Windows est en cp1252 par défaut : un `⚠` ou un `✅` y lève un
# UnicodeEncodeError et fait planter le bootstrap au tout dernier moment, après
# avoir écrit les fichiers. On bascule stdout en UTF-8 quand c'est possible, et
# on dégrade proprement sinon — un outil d'installation ne doit jamais tomber
# sur un caractère d'ornement.
_ASCII_FALLBACK = {
    "·": "-", "⚠": "!", "✅": "OK", "❌": "KO", "🟢": "[v]", "🟡": "[~]",
    "🔴": "[x]", "—": "-", "«": '"', "»": '"', "→": "->", "├": "|", "└": "`",
}

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    _UNICODE_OK = True
except Exception:  # console exotique, flux redirigé, Python embarqué
    _UNICODE_OK = False


def say(msg: str = "") -> None:
    if not _UNICODE_OK:
        for fancy, plain in _ASCII_FALLBACK.items():
            msg = msg.replace(fancy, plain)
        msg = msg.encode("ascii", "replace").decode("ascii")
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)


def step(msg: str) -> None:
    say(f"  · {msg}")


def fail(msg: str, cause: str, fix: str) -> None:
    """Bloc ERROR 3 lignes, conforme à rules/output-protocol.md §3."""
    say()
    say(f"ERROR: bootstrap — {msg}")
    say(f"CAUSE: {cause}")
    say(f"FIX: {fix}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Saisie interactive
# ---------------------------------------------------------------------------
def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"  {prompt}{suffix} : ").strip()
    except (EOFError, KeyboardInterrupt):
        say()
        fail(
            "saisie interrompue",
            "[BOOTSTRAP_ABORTED] entrée fermée ou interruption clavier",
            "relancer, ou utiliser --combo c1 --auto pour un mode non interactif",
        )
    return answer or default


def ask_choice(prompt: str, options: dict[str, str], default: str) -> str:
    say()
    say(f"  {prompt}")
    for key, label in options.items():
        marker = "*" if key == default else " "
        say(f"   {marker} {key:<12} {label}")
    while True:
        answer = ask("votre choix", default)
        if answer in options:
            return answer
        say(f"    (« {answer} » n'est pas dans la liste)")


VALID_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,39}$")


def ask_app_name(default: str = "") -> str:
    while True:
        name = ask("Nom du système agentic (PascalCase, sans espace)", default)
        if VALID_NAME.match(name):
            return name
        say("    (lettres et chiffres uniquement, commence par une lettre, 2 à 40 caractères)")


# ---------------------------------------------------------------------------
# Préflight
# ---------------------------------------------------------------------------
def preflight() -> None:
    if sys.version_info < MIN_PYTHON:
        fail(
            "version de Python insuffisante",
            f"[BOOTSTRAP_PYTHON_TOO_OLD] {sys.version_info.major}.{sys.version_info.minor} "
            f"< {MIN_PYTHON[0]}.{MIN_PYTHON[1]} requis",
            f"installer Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} ou supérieur",
        )
    if not SDDA.is_dir():
        fail(
            "framework introuvable",
            f"[BOOTSTRAP_SDDA_MISSING] {SDDA} absent",
            "exécuter bootstrap.py depuis la racine du dépôt SDD-Agents",
        )
    if not TEMPLATE.is_file():
        fail(
            "template STACK.md introuvable",
            f"[BOOTSTRAP_TEMPLATE_MISSING] {TEMPLATE} absent",
            "vérifier l'intégrité du dépôt (git status)",
        )


# ---------------------------------------------------------------------------
# Génération
# ---------------------------------------------------------------------------
def build_stack_md(app_name: str, combo: Combo, secrets: dict[str, str]) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")

    framework_lines = "\n".join(
        f" - .sdda/stacks/framework/{f}.md" for f in combo.frameworks
    )
    guardrail_lines = "\n".join(
        f" - .sdda/stacks/guardrails/{g}.md" for g in combo.guardrails
    )
    # Aucun outil actif est un état légitime (un agent peut n'avoir que du RAG) :
    # la ligne reste vide plutôt que d'inventer une stack que personne n'a choisie.
    tools_lines = "\n".join(f" - .sdda/stacks/tools/{t}.md" for t in combo.tools)
    # Même règle pour le retrieval, et elle a une conséquence concrète :
    # `vectorstore/none.md` et `embedding/none.md` N'EXISTENT PAS. Émettre
    # ` - .sdda/stacks/vectorstore/none.md` pour une combo sans RAG produisait un
    # STACK.md qui active deux fiches absentes — donc qui ne charge rien, sans
    # que rien ne le dise. C'est exactement ce que `preflight_stack_combo`
    # refuse désormais ([STACK_COMBO_UNLOADABLE]).
    retrieval_lines = "\n".join(
        line for line in (
            f" - .sdda/stacks/vectorstore/{combo.vectorstore}.md" if combo.vectorstore != "none" else "",
            f" - .sdda/stacks/embedding/{combo.embedding}.md" if combo.embedding != "none" else "",
        ) if line
    ) or "# (aucun : RAG Pattern = none)"

    has_db = combo.database != "none"
    mapping = {
        "{{AppName}}": app_name,
        "{{SystemName}}": app_name,
        "{{Language}}": combo.language,
        "{{FrameworkActiveLines}}": framework_lines,
        "{{OrchestrationPattern}}": combo.orchestration,
        "{{RagPattern}}": combo.rag,
        "{{RetrievalActiveLines}}": retrieval_lines,
        "{{Reranker}}": combo.reranker,
        "{{DataAccess}}": combo.dataaccess,
        "{{MemoryStrategy}}": combo.memory,
        "{{GuardrailActiveLines}}": guardrail_lines,
        "{{ToolsActiveLines}}": tools_lines,
        "{{Observability}}": combo.observability,
        "{{EvalStack}}": combo.evalstack,
        "{{Serving}}": combo.serving,
        "{{ApiAuthMode}}": combo.api_auth,
        "{{DeliverableType}}": combo.deliverable,
        "{{ApiFramework}}": combo.api_framework,
        "{{ServingPort}}": "8080",
        "{{DatabaseType}}": combo.database,
        "{{DbHost}}": secrets.get("DB_HOST", "localhost" if has_db else "n/a"),
        "{{DbPort}}": secrets.get("DB_PORT", "5432" if has_db else "n/a"),
        "{{DbName}}": secrets.get("DB_NAME", f"{app_name.lower()}_db" if has_db else "n/a"),
        "{{DbUser}}": secrets.get("DB_USER", "<à compléter>" if has_db else "n/a"),
        "{{DbPassword}}": secrets.get("DB_PASSWORD", "<à compléter>" if has_db else "n/a"),
        "{{LlmApiKey}}": secrets.get("LLM_API_KEY", "<à compléter>"),
    }
    for placeholder, value in mapping.items():
        text = text.replace(placeholder, value)

    leftovers = sorted(set(re.findall(r"\{\{[A-Za-z_]+\}\}", text)))
    if leftovers:
        fail(
            "template incomplètement substitué",
            f"[BOOTSTRAP_TEMPLATE_UNRESOLVED] placeholders restants : {', '.join(leftovers)}",
            "signaler ce bug : STACK.md.template et bootstrap.py ont divergé",
        )
    return text


def create_workspace() -> None:
    for rel in WORKSPACE_TREE:
        directory = WORKSPACE / rel
        directory.mkdir(parents=True, exist_ok=True)
        keep = directory / ".gitkeep"
        if not any(directory.iterdir()):
            keep.touch()


def build_context_packs() -> None:
    """Assemble les packs de contexte des agents qui en déclarent.

    Fait ici parce qu'un pack manquant ne se voit qu'au premier spawn, sous la
    forme d'un `[PACK_UNUSABLE]` au milieu d'un pipeline — alors qu'il se
    construit en une seconde au moment où le workspace naît.
    """
    sys.path.insert(0, str(SDDA / "python"))
    try:
        from sdda_lib.errors import Report
        from sdda_scripts import context_pack
    except Exception as exc:  # framework incomplet : ce n'est pas bloquant ici
        step(f"⚠  packs de contexte non construits ({type(exc).__name__}) — `context_pack.py build --agent all`")
        return

    loader = context_pack.load_loader(ROOT)
    report = Report(name="CONTEXT", target=str(ROOT))
    agents = [a for a in context_pack.agent_names(loader) if (loader[a] or {}).get("pack_sources")]
    for agent in agents:
        context_pack.build_pack(ROOT, loader, agent, report=report)
    built = len(report.data.get("packs", []))
    step(f"workspace/.sys/.context/packs/ — {built}/{len(agents)} pack(s)")
    for finding in report.errors:
        step(f"⚠  {finding.message}")


def write_gitignore() -> None:
    """STACK.md contient des secrets en clair. Il ne doit jamais partir en commit."""
    gitignore = ROOT / ".gitignore"
    required = [
        "workspace/stack/STACK.md",
        "workspace/traces/",
        "workspace/src/",
        "workspace/.sys/",
        "workspace/evals/reports/",
        ".env",
        "__pycache__/",
        "*.pyc",
        ".venv/",
    ]
    existing = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.is_file() else []
    missing = [line for line in required if line not in existing]
    if not missing:
        return
    with gitignore.open("a", encoding="utf-8") as handle:
        if existing and existing[-1].strip():
            handle.write("\n")
        handle.write("# SDD_Agents — secrets et artefacts générés\n")
        for line in missing:
            handle.write(f"{line}\n")
    step(f".gitignore complété ({len(missing)} entrées)")


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------
def smoke(stack_path: Path, combo: Combo) -> list[str]:
    """Vérifications immédiates. Renvoie la liste des anomalies.

    Le contrôle de `STACK.md` et de l'arborescence est délégué à
    `sdda_scripts/smoke_check.py` — le script que `/sdda-bootstrap` STEP 5
    rappelle. Deux implémentations rendraient deux verdicts.
    """
    problems: list[str] = []

    if not stack_path.is_file():
        problems.append("workspace/stack/STACK.md n'a pas été écrit")
        return problems

    sys.path.insert(0, str(SDDA / "python"))
    try:
        from sdda_scripts import smoke_check
    except Exception as exc:  # framework incomplet : dire, ne pas planter le bootstrap
        problems.append(f"smoke_check.py non chargeable ({type(exc).__name__}) — lancer "
                        "`python .sdda/python/sdda_scripts/smoke_check.py` à la main")
    else:
        problems.extend(smoke_check.problems(ROOT))

    if combo.status not in ("validated", "bench-validated"):
        problems.append(
            f"combo « {combo.id} » : {STATUS_BADGE.get(combo.status, combo.status)}"
        )

    return problems


# ---------------------------------------------------------------------------
# Flux interactif
# ---------------------------------------------------------------------------
def interactive() -> tuple[str, Combo, dict[str, str]]:
    say()
    say("=" * 74)
    say("  SDD_Agents — Spec Driven Development pour applications Agentic")
    say("=" * 74)
    say()
    say("  Quatre questions. Chacune a une conséquence architecturale réelle ;")
    say("  tout le reste se décide dans la spécification, pas ici.")
    say()

    app_name = ask_app_name()

    combo_options = {
        cid: f"{c.label}  ({STATUS_BADGE.get(c.status, c.status)})"
        for cid, c in COMBOS.items()
    }
    combo_id = ask_choice("Composition de stack", combo_options, "c1")
    combo = COMBOS[combo_id]

    if combo.status not in ("validated", "bench-validated"):
        say()
        say(f"  ⚠  Cette composition est en statut « {combo.status} ».")
        say("     Le framework ne prétend aucune validation qu'il n'a pas mesurée.")
        say("     Vous pouvez continuer — en le sachant.")

    secrets: dict[str, str] = {}
    say()
    say("  Secrets — écrits en clair dans workspace/stack/STACK.md, qui est gitignored.")
    say("  Laisser vide pour compléter plus tard.")
    say()
    key = ask("Clé API du fournisseur de modèles (LLM_API_KEY)", "")
    if key:
        secrets["LLM_API_KEY"] = key

    if combo.database != "none":
        say()
        say(f"  Base de données : {combo.database}")
        secrets["DB_HOST"] = ask("Hôte", "localhost")
        secrets["DB_PORT"] = ask("Port", "5432")
        secrets["DB_NAME"] = ask("Base", f"{app_name.lower()}_db")
        secrets["DB_USER"] = ask("Utilisateur (rôle en LECTURE SEULE recommandé)", "")
        secrets["DB_PASSWORD"] = ask("Mot de passe", "")

    return app_name, combo, secrets


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialise un projet SDD_Agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--combo", choices=sorted(COMBOS), help="composition de stack")
    parser.add_argument("--app-name", help="nom du système (PascalCase)")
    parser.add_argument("--auto", action="store_true", help="aucune interaction (CI)")
    parser.add_argument("--force", action="store_true", help="écraser un STACK.md existant")
    args = parser.parse_args()

    preflight()

    stack_path = WORKSPACE / "stack" / "STACK.md"
    if stack_path.is_file() and not args.force:
        fail(
            "projet déjà initialisé",
            f"[BOOTSTRAP_ALREADY_INITIALIZED] {stack_path} existe déjà",
            "utiliser --force pour l'écraser (une sauvegarde .bak sera créée), "
            "ou éditer le fichier directement",
        )

    if args.auto:
        app_name = args.app_name or os.environ.get("SDDA_APP_NAME", "")
        combo_id = args.combo or os.environ.get("SDDA_COMBO", "c1")
        if not app_name:
            fail(
                "nom du système manquant",
                "[BOOTSTRAP_MISSING_ARG] --auto exige --app-name ou SDDA_APP_NAME",
                "relancer avec --app-name MonSysteme",
            )
        if not VALID_NAME.match(app_name):
            fail(
                "nom du système invalide",
                f"[BOOTSTRAP_INVALID_NAME] « {app_name} » n'est pas en PascalCase alphanumérique",
                "utiliser des lettres et des chiffres, 2 à 40 caractères",
            )
        if combo_id not in COMBOS:
            fail(
                "combo inconnue",
                f"[BOOTSTRAP_UNKNOWN_COMBO] « {combo_id} » absente du catalogue",
                f"choisir parmi : {', '.join(sorted(COMBOS))}",
            )
        combo, secrets = COMBOS[combo_id], {}
    elif args.combo:
        combo = COMBOS[args.combo]
        app_name = args.app_name or ask_app_name()
        secrets = {}
    else:
        app_name, combo, secrets = interactive()

    say()
    say("  Génération")

    create_workspace()
    step(f"workspace/ — {len(WORKSPACE_TREE)} répertoires")

    if stack_path.is_file():
        backup = stack_path.with_suffix(".md.bak")
        shutil.copy2(stack_path, backup)
        step(f"sauvegarde de l'ancien STACK.md -> {backup.name}")

    stack_path.write_text(build_stack_md(app_name, combo, secrets), encoding="utf-8")
    step("workspace/stack/STACK.md")

    write_gitignore()
    build_context_packs()

    say()
    say("  Vérification")
    problems = smoke(stack_path, combo)
    if problems:
        for problem in problems:
            step(f"⚠  {problem}")
    else:
        step("✅ aucune anomalie")

    say()
    say("=" * 74)
    say(f"  {app_name} initialisé — {combo.label}")
    say("=" * 74)
    say()
    say("  Étapes suivantes")
    say()
    say("   1. Compléter workspace/stack/STACK.md")
    say("      — les secrets, et surtout ## Project Config > budget d'exécution :")
    say("        CostPerRunTargetUsd / LatencyP95TargetMs n'ont pas de défaut,")
    say("        et la MISSION GATE les exigera.")
    say()
    say("   2. /sdda-mission « décrivez ce que le système doit accomplir »")
    say("      L'élicitation vous demandera d'où vient la vérité contre laquelle")
    say("      on jugera. Sans réponse à cette question, rien n'est mesurable.")
    say()
    say("   3. /sdda-full 1")
    say()
    say("  Documentation : .sdda/PHILOSOPHY.md · .sdda/ARCHITECTURE.md")
    say()
    return 0


if __name__ == "__main__":
    sys.exit(main())
