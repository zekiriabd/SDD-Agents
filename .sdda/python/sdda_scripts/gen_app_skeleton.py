#!/usr/bin/env python3
"""Génère le squelette applicatif Python — 0 token, déterministe.

Ce que ce script défend : *la plomberie d'une application agentic est la même à
chaque projet, elle doit donc être écrite une fois et régénérée, pas
réinterprétée.* Avant lui, `config.py`, `models.py`, `bounds.py`, `tracing.py`,
la boucle bornée, la CLI et l'exécuteur d'eval n'existaient qu'en prose dans
sept fiches de stack — et six agents `dev-*` en tiraient six architectures
différentes. La prose dit *quoi* ; elle ne peut pas empêcher la CLI de borner le
budget quand l'API ne le borne pas, ni un `run` d'avoir une sémantique que le
runner d'eval n'a pas. La conséquence se voyait à la fin, en G5/G6 : rien à
charger avec `--executor`, donc rien à mesurer.

Trois modes, les mêmes que `gen_source_tools.py` :

    --check    le défaut : régénère en mémoire et compare. Exit 1 si dérive.
    --write    écrit les fichiers absents ou divergents.
    --json     sortie machine.

**Le squelette est du code INVARIANT.** Une retouche locale se perd à la
régénération suivante et diverge en silence d'ici là : c'est exactement ce que
`--check` existe pour dire. Ce qui est PROPRE au projet — les agents, les
outils, le graphe réel, les prompts — vit ailleurs et n'est jamais écrasé.

Ce que le script refuse, et pourquoi il refuse plutôt qu'il n'adapte :
  - une stack de langage qui n'est pas `python` : ce squelette est en Python, et
    l'écrire dans un projet C# produirait du code qui ne compile pas trois
    phases plus loin ([STACK_LANGUAGE_MISMATCH]) ;
  - un couple livrable/surface incohérent : le jugement appartient à
    `validate_packaging.py`, qui est l'enforcer nommé par INVARIANTS. Le
    dupliquer ici ferait deux vérités sur la même question, et c'est celle qu'on
    ne lit pas qui gagnerait.

Usage :
    python .sdda/sdda.py gen-app-skeleton --check --json
    python .sdda/sdda.py gen-app-skeleton --write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.layered_config import (  # noqa: E402
    active_stacks, read_project_section, read_runtime_tier_map, read_stack_section_kv,
)
from sdda_scripts import validate_packaging  # noqa: E402
from sdda_scripts._common import (  # noqa: E402
    add_common_args, ensure_utf8_stdout, finish, load_config, resolve_root,
)

#: Le sous-arbre des templates qui devient le paquet applicatif. `data/` et
#: `tools/` sont voisins et appartiennent à `gen_source_tools.py` : deux
#: générateurs, deux déclencheurs, et un projet sans sources déclarées ne doit
#: pas recevoir une couche d'accès aux données qu'il n'a pas demandée.
RUNTIME_ROOT = "templates/runtime/python"
APP_DIR = "app"

#: Rendus, pas copiés : leur contenu dépend de STACK.md. Cible relative à la
#: racine du PROJET applicatif pour le premier, au paquet pour le second.
PYPROJECT_TMPL = "pyproject.toml.tmpl"
CONFIG_TMPL = "app_config.json.tmpl"

#: Le langage que ce squelette sait écrire. Un seul, et c'est dit ici plutôt
#: que déduit : le jour où `csharp` aura son squelette, il aura son générateur.
LANGUAGE = "python"

#: Deux catalogues actifs épinglent le même paquet à deux versions différentes.
CLS_PIN_CONFLICT = "STACK_LIBRARY_PIN_CONFLICT"

#: Nom logique -> variable d'environnement, par fournisseur. Le NOM voyage
#: (dans une trace, dans un message d'erreur, c'est même ce qu'on veut y lire) ;
#: la VALEUR ne quitte jamais `config.py`.
SECRET_ENV: dict[str, dict[str, str]] = {
    "anthropic": {"llmApiKey": "ANTHROPIC_API_KEY"},
    "openai": {"llmApiKey": "OPENAI_API_KEY"},
    "azure": {"llmApiKey": "AZURE_OPENAI_API_KEY"},
    "google": {"llmApiKey": "GOOGLE_API_KEY"},
    "local": {},
    "none": {},
}

#: Bornes de départ écrites dans `app_config.json` tant que l'IR n'en fournit
#: pas. Volontairement serrées : un oubli doit coûter peu, pas passer inaperçu.
STARTER_BOUNDS: dict[str, Any] = {
    "max_iterations": 4, "max_tool_calls": 8, "max_delegation_depth": 0,
    "timeout_s": 60.0, "budget_usd": 0.50, "on_bound_exceeded": "fail-explicit",
}


#: Systèmes de build dont un `.libs.json` alimente `pyproject.toml`. Les
#: catalogues npm, gradle ou dotnet d'une stack active ne concernent pas ce
#: squelette — `preflight_stack_combo` refuse de toute façon leur coexistence.
PYTHON_BUILD_SYSTEMS = frozenset({"uv", "pip", "poetry"})

#: Outils d'atelier que les catalogues répètent en `core` pour que chaque stack
#: soit installable seule (`lang/python.md §1`). Ils vont au groupe `dev` : un
#: `mypy` dans les dépendances d'exécution partirait dans la roue, et dans
#: l'image, de chaque application.
DEV_TOOL_MODULES = frozenset({"ruff", "mypy", "pytest", "pytest-asyncio", "pytest-cov", "pytest-timeout"})

#: Le groupe `dev` minimal, présent même sans catalogue : sans pytest-asyncio,
#: chaque test `async def` échoue en « async def functions are not natively
#: supported », ce qui ressemble à une panne du code et n'en est pas une.
BASE_DEV_DEPENDENCIES: tuple[str, ...] = ("pytest>=8.3", "pytest-asyncio>=0.24")

#: `RuntimeProvider` (STACK.md) -> fiche `.sdda/providers/` qui porte le SDK
#: importé par `models.provider_client`.
PROVIDER_SHEETS: dict[str, str] = {
    "anthropic": "anthropic", "openai": "openai", "azure": "azure-openai",
    "google": "google", "gemini": "google", "local": "local-ollama",
}

_STACK_LINE_RE = re.compile(r"^\s*-\s*\.sdda/stacks/([a-z0-9-]+)/([A-Za-z0-9._-]+)\.md\b", re.M)


def runtime_dir(root: Path) -> Path:
    """Les templates du projet s'il en vendore, ceux du framework sinon."""
    local = root / ".sdda" / RUNTIME_ROOT
    return local if local.is_dir() else paths.FRAMEWORK_SDDA_DIR / RUNTIME_ROOT


# ---------------------------------------------------------------------------
# Contexte
# ---------------------------------------------------------------------------
@dataclass
class Context:
    """Ce qu'il faut savoir du projet pour générer, résolu une seule fois."""

    root: Path
    app: str = "App"
    mission: str = ""
    language: str = ""
    surfaces: list[str] = field(default_factory=list)
    deliverable: str = "cli-exe"
    provider: str = "none"
    default_tier: str = "balanced"
    tier_map: dict[str, str] = field(default_factory=dict)
    streaming: bool = False
    src_root: Path = field(default_factory=Path)
    project_dir: Path = field(default_factory=Path)

    @classmethod
    def resolve(cls, root: Path, report: Report, *, src_root: Path | None = None) -> "Context":
        project = read_project_section(root)
        config = load_config(root, report)
        languages = active_stacks(root, "Active Language & Runtime")
        surfaces = active_stacks(root, "Active Serving Surface")
        runtime = {}
        try:
            runtime = read_runtime_tier_map(root)
        except Exception:  # une section absente n'est pas une raison de ne rien rendre
            runtime = {}

        ctx = cls(
            root=root,
            app=str(project.get("AppName") or "").strip(),
            language=languages[0] if languages else "",
            surfaces=surfaces,
            deliverable=str(config.get("DeliverableType", "cli-exe")),
            provider=str(_section(root, "Runtime Models").get("RuntimeProvider") or "none"),
            default_tier=str(config.get("DefaultTier", "balanced")),
            tier_map={str(k): str(v) for k, v in runtime.items()},
            streaming=str(_section(root, "Active Serving Surface").get(
                "StreamingEnabled", "false")).strip().lower() in ("true", "yes", "1"),
        )
        ctx.mission = _detect_mission(root)
        # Layout plat (SDD_Pro) : le projet EST le paquet — cf. paths.app_src_root.
        ctx.project_dir = paths.app_dir(root, ctx.app or "App")
        ctx.src_root = src_root or paths.app_src_root(root, ctx.app or "App")
        return ctx


def _section(root: Path, heading: str) -> dict[str, Any]:
    return read_stack_section_kv(root, heading)


def _detect_mission(root: Path) -> str:
    found = sorted({p.name.split("-", 1)[0] for p in paths.missions_dir(root).glob("*-*.md")})
    return found[0] if len(found) == 1 else ""


# ---------------------------------------------------------------------------
# Rendu
# ---------------------------------------------------------------------------
def _pricing(root: Path) -> dict[str, dict[str, float]]:
    """La table de tarifs, restreinte aux modèles que le projet fait tourner.

    Restreinte, et non copiée en entier : le fichier est versionné avec
    l'application, et un tarif qui change rétroactivement fausserait la
    comparaison de deux baselines. Un modèle absent de la table ne rend pas un
    coût nul — `models.cost_usd` rend un problème nommé, parce qu'un zéro
    passerait sous n'importe quel plafond sans rien dire.
    """
    try:
        from sdda_lib import pricing as _pricing_table  # noqa: PLC0415
    except Exception:  # pragma: no cover - le paquet est une dépendance dure
        return {}
    out: dict[str, dict[str, float]] = {}
    for model in sorted(set(read_runtime_tier_map(root).values())):
        try:
            rates = _pricing_table.get_pricing(model, strict=True)
        except Exception:
            continue
        out[str(model)] = {k: float(v) for k, v in rates.items()}
    return out


# ---------------------------------------------------------------------------
# Dépendances — dérivées des `.libs.json` des fiches ACTIVES, épinglées
# ---------------------------------------------------------------------------
@dataclass
class Dependencies:
    """Ce que `pyproject.toml` déclare, et d'où vient chaque ligne."""

    runtime: list[str] = field(default_factory=list)
    dev: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"runtime": list(self.runtime), "dev": list(self.dev), "sources": dict(self.sources)}


def package_name(requirement: str) -> str:
    """`psycopg[binary,pool]==3.2.1` -> `psycopg` (normalisé PEP 503)."""
    head = re.split(r"[\[=<>!~;\s]", requirement.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", head).lower()


def _extras(module: str) -> set[str]:
    m = re.search(r"\[([^\]]*)\]", module)
    return {e.strip() for e in m.group(1).split(",") if e.strip()} if m else set()


def _stack_text(root: Path) -> str:
    """STACK.md sans ses commentaires : un `# gpt-4` en marge ne doit pas installer un SDK."""
    stack = paths.stack_md_path(root)
    if not stack.is_file():
        return ""
    lines = []
    for line in markdown_io.read_text(stack).split("\n"):
        if line.lstrip().startswith("#") and not line.lstrip().startswith("## "):
            continue
        lines.append(re.split(r"\s#\s", line, maxsplit=1)[0])
    return "\n".join(lines)


def active_libs_catalogs(root: Path) -> list[Path]:
    """Les `.libs.json` des fiches ACTIVES de STACK.md (toutes sections), dans l'ordre du fichier."""
    sdda = root / ".sdda" if (root / ".sdda" / "stacks").is_dir() else paths.FRAMEWORK_SDDA_DIR
    out: list[Path] = []
    for category, name in _STACK_LINE_RE.findall(_stack_text(root)):
        catalog = sdda / "stacks" / category / f"{name}.libs.json"
        if catalog.is_file() and catalog not in out:
            out.append(catalog)
    return out


def _provider_sdk(root: Path, provider: str) -> tuple[str, str, str] | None:
    """(paquet, version, fiche) du SDK que `models.provider_client` importe pour ce fournisseur."""
    sheet_name = PROVIDER_SHEETS.get(provider.lower())
    if not sheet_name:
        return None
    sdda = root / ".sdda" if (root / ".sdda" / "providers").is_dir() else paths.FRAMEWORK_SDDA_DIR
    sheet = sdda / "providers" / f"{sheet_name}.yaml"
    if not sheet.is_file():
        return None
    from sdda_lib import yaml_mini  # noqa: PLC0415

    sdk = yaml_mini.parse_mapping(markdown_io.read_text(sheet)).get("runtime_sdk")
    if not isinstance(sdk, dict) or not sdk.get("package") or not sdk.get("version"):
        return None
    return str(sdk["package"]), str(sdk["version"]), f"providers/{sheet.name}"


def resolve_dependencies(ctx: Context, report: Report) -> Dependencies:
    """Les dépendances du projet, DÉRIVÉES : aucune n'est écrite à la main.

    Sources, dans cet ordre : `core` puis `dev` puis les `onDemand` dont un
    déclencheur figure dans STACK.md (hors commentaires), de chaque
    `.libs.json` actif ; puis le SDK du fournisseur runtime, lu dans sa fiche
    (`runtime_sdk`). Tout est épinglé `==` sur la version du catalogue — qui
    est la seule à faire foi (`libs-catalog.schema.json`).

    Deux catalogues peuvent citer le même paquet (le socle `pydantic`,
    `structlog`… est répété pour que chaque stack s'installe seule) : c'est
    légitime à version égale, et les extras s'unissent. À versions
    différentes, c'est `[STACK_LIBRARY_PIN_CONFLICT]` — choisir l'une en
    silence, c'est livrer une combinaison que personne n'a résolue.

    Le résultat est TRIÉ par nom : deux régénérations rendent les mêmes octets.
    """
    deps = Dependencies()
    pinned: dict[str, tuple[str, set[str], str, str]] = {}   # nom -> (version, extras, groupe, source)
    stack = _stack_text(ctx.root)

    def add(module: str, version: str, group: str, source: str) -> None:
        name = package_name(module)
        group = "dev" if name in DEV_TOOL_MODULES or name.startswith("pytest") else group
        if name in pinned:
            prev_version, extras, prev_group, prev_source = pinned[name]
            if prev_version != version:
                report.error(
                    CLS_PIN_CONFLICT,
                    f"`{name}` épinglé {prev_version} par {prev_source} et {version} par {source}",
                    fix="aligner les deux catalogues sur une version résolue CONJOINTEMENT "
                        "(`uv pip compile` sur l'union) — le squelette ne choisit pas à leur place",
                    location=source)
                return
            extras |= _extras(module)
            pinned[name] = (version, extras, "runtime" if "runtime" in (prev_group, group) else "dev", prev_source)
            return
        pinned[name] = (version, _extras(module), group, source)

    for catalog_path in active_libs_catalogs(ctx.root):
        rel = paths.rel(ctx.root, catalog_path) if catalog_path.is_relative_to(ctx.root) else \
            f".sdda/stacks/{catalog_path.parent.name}/{catalog_path.name}"
        try:
            catalog = json.loads(markdown_io.read_text(catalog_path))
        except ValueError as exc:
            report.error("STACK_LIBRARY_MISSING", f"`{rel}` illisible ({exc})",
                         fix="corriger le JSON du catalogue", location=rel)
            continue
        if str(catalog.get("buildSystem") or "uv") not in PYTHON_BUILD_SYSTEMS:
            continue
        versions = catalog.get("versions") or {}
        entries = [(e, "runtime") for e in catalog.get("core") or []] + \
                  [(e, "dev") for e in catalog.get("dev") or []]
        for entry in catalog.get("onDemand") or []:
            triggers = [str(t) for t in entry.get("triggers") or []]
            if any(re.search(t, stack) for t in triggers):
                entries.append((entry, "runtime"))
        for entry, group in entries:
            module, ref = str(entry.get("module") or ""), str(entry.get("ref") or entry.get("module") or "")
            version = versions.get(ref)
            if not module or not version:
                report.error("STACK_LIBRARY_MISSING",
                             f"`{rel}` : `{module or '?'}` sans version épinglée (`versions.{ref}`)",
                             fix="toute `ref` de `core`/`dev`/`onDemand` doit exister dans `versions`",
                             location=rel)
                continue
            add(module, str(version), group, rel)

    sdk = _provider_sdk(ctx.root, ctx.provider)
    if sdk is not None:
        add(sdk[0], sdk[1], "runtime", sdk[2])

    for name in sorted(pinned):
        version, extras, group, source = pinned[name]
        requirement = f"{name}{'[' + ','.join(sorted(extras)) + ']' if extras else ''}=={version}"
        (deps.runtime if group == "runtime" else deps.dev).append(requirement)
        deps.sources[name] = source
    base_dev = [r for r in BASE_DEV_DEPENDENCIES if package_name(r) not in pinned]
    deps.dev = sorted(deps.dev + base_dev, key=package_name)
    return deps


def _toml_list(items: list[str], *, multiline: bool) -> str:
    if not items:
        return "[]"
    if not multiline:
        return "[" + ", ".join(json.dumps(i) for i in items) + "]"
    return "[\n" + "".join(f"  {json.dumps(i)},\n" for i in items) + "]"


def render_pyproject(template: str, deps: Dependencies) -> str:
    return (template.replace("{Dependencies}", _toml_list(deps.runtime, multiline=True))
                    .replace("{DevDependencies}", _toml_list(deps.dev, multiline=False)))


def _guardrails(ctx: Context) -> dict[str, Any]:
    """`guardrails` d'`app_config.json` : les fiches ACTIVES et ce que l'IR dit des sorties.

    Rien n'est actif par défaut : un guardrail que STACK.md ne déclare pas
    n'a été mesuré par aucune eval. Les schémas de sortie viennent de l'IR
    (`agents[].outputSchema`), jamais d'une recopie : le contrat est la source,
    le schéma validé au runtime en est la projection.
    """
    section = _section(ctx.root, "Active Guardrails")
    active = sorted(active_stacks(ctx.root, "Active Guardrails"))

    def names(key: str) -> list[str]:
        raw = section.get(key)
        return [str(v) for v in raw] if isinstance(raw, list) else ([str(raw)] if raw else [])

    config: dict[str, Any] = {
        "active": active,
        "input": names("InputGuardrails"),
        "output": names("OutputGuardrails"),
        "onTrip": str(section.get("OnGuardrailTrip") or "block-and-log"),
        "injection": {"threshold": float(section.get("InjectionThreshold") or 0.5)},
        "pii": {},
        "outputSchema": None,
        "outputSchemas": {},
    }
    ir_file = paths.ir_path(ctx.root, ctx.mission) if ctx.mission else None
    if ir_file is not None and ir_file.is_file():
        try:
            ir = json.loads(markdown_io.read_text(ir_file))
        except ValueError:
            ir = {}
        agents = {str(a.get("id")): a for a in ir.get("agents") or [] if isinstance(a, dict)}
        schemas: dict[str, Any] = {}
        for agent_id, agent in sorted(agents.items()):
            if isinstance(agent.get("outputSchema"), dict) and agent["outputSchema"]:
                schemas[agent_id] = agent["outputSchema"]
                schemas.setdefault(re.sub(r"^\d+-", "", agent_id), agent["outputSchema"])
        config["outputSchemas"] = schemas
        orchestration = ir.get("orchestration") or {}
        nodes = {str(n.get("id")): n for n in orchestration.get("nodes") or [] if isinstance(n, dict)}
        terminals = [nodes.get(str(t)) or {"ref": str(t)} for t in orchestration.get("terminalNodes") or []]
        finals = {str(t.get("ref") or t.get("id") or "") for t in terminals}
        if len(finals) == 1:
            config["outputSchema"] = schemas.get(finals.pop())
    return config


def render_app_config(ctx: Context, template: str) -> str:
    """`app_config.json` — ce que l'application lit au démarrage.

    STACK.md n'est jamais lu au runtime : c'est une déclaration de projet, pas
    une configuration d'exécution, et surtout **ce qui est résolu est épinglable**. Deux runs sur deux
    configurations différentes ne sont pas comparables, et le pipeline doit
    pouvoir le dire.
    """
    secrets = dict(SECRET_ENV.get(ctx.provider.lower(), SECRET_ENV["none"]))
    # Le NOM de la variable est celui que STACK.md déclare (`## Active Secrets`,
    # ex. `LLM_API_KEY`), pas celui que le SDK du fournisseur suppose : sinon
    # `.env` porte `LLM_API_KEY`, l'application lit `ANTHROPIC_API_KEY`, et la
    # clé « manque » alors qu'elle est là. Le défaut par fournisseur ne sert
    # que si STACK.md ne déclare aucun secret de modèle.
    declared = _declared_secret_names(ctx.root)
    if "LLM_API_KEY" in declared and "llmApiKey" in secrets:
        secrets["llmApiKey"] = "LLM_API_KEY"
    substitutions = {
        "{MissionId}": ctx.mission,
        "{RuntimeProvider}": ctx.provider,
        "{DefaultTier}": ctx.default_tier,
        "{TierMap}": _json(ctx.tier_map),
        "{Pricing}": _json(_pricing(ctx.root)),
        "{Bounds}": _json(STARTER_BOUNDS),
        "{DeliverableType}": ctx.deliverable,
        "{ServingSurface}": ctx.surfaces[0] if ctx.surfaces else "cli",
        "{Streaming}": "true" if ctx.streaming else "false",
        "{SecretEnv}": _json(secrets),
        "{Guardrails}": _json(_guardrails(ctx)),
    }
    text = template
    for needle, value in substitutions.items():
        text = text.replace(needle, value)
    return text


def _json(payload: Any) -> str:
    """JSON indenté de 2, réindenté pour tenir dans le gabarit.

    Le fichier est versionné et relu par un humain : un objet sur une ligne
    ferait d'un changement de tarif un diff illisible.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return text.replace("\n", "\n  ")


def plan(ctx: Context, report: Report, deps: Dependencies | None = None) -> list[tuple[Path, str]]:
    """(cible, contenu) pour chaque fichier du squelette. Trié, donc reproductible."""
    deps = deps if deps is not None else resolve_dependencies(ctx, report)
    source = runtime_dir(ctx.root) / APP_DIR
    if not source.is_dir():
        report.error(
            "APP_RUNTIME_MISSING", f"squelette applicatif introuvable ({source})",
            fix="restaurer `.sdda/templates/runtime/python/app/` — sans lui, chaque agent "
                "`dev-*` réinvente la plomberie, et l'exécuteur d'eval n'existe pas")
        return []

    targets: list[tuple[Path, str]] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(source)
        text = markdown_io.read_text(path)
        if path.name == PYPROJECT_TMPL:
            targets.append((ctx.project_dir / "pyproject.toml", _subst(render_pyproject(text, deps), ctx)))
        elif path.name == CONFIG_TMPL:
            targets.append((ctx.src_root / "app_config.json",
                            _subst(render_app_config(ctx, text), ctx)))
        else:
            targets.append((ctx.src_root / relative, _subst(text, ctx)))
    return targets


def _subst(text: str, ctx: Context) -> str:
    """`{AppName}` -> le nom du projet. Remplacement LITTÉRAL, jamais `format`.

    `format` casserait sur la première accolade d'un littéral Python ou d'un
    JSON — et le squelette en est plein.
    """
    return text.replace("{AppName}", ctx.app)


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------
#: La liste `dependencies = [...]` d'un pyproject, entrées entre guillemets —
#: un extra (`psycopg[binary,pool]`) contient `]`, qu'un `[^\]]*` couperait.
_DEPENDENCIES_RE = re.compile(r'^dependencies = \[(?:\s*"[^"]*",?)*\s*\]$', re.M)
_QUOTED_RE = re.compile(r'"([^"]*)"')


def _declared_dependencies(text: str) -> list[str]:
    match = _DEPENDENCIES_RE.search(text)
    return _QUOTED_RE.findall(match.group(0)) if match else []


def merge_dependencies(generated: list[str], existing: list[str]) -> list[str]:
    """Les épinglages DÉRIVÉS, plus ce que le projet a ajouté lui-même (`uv add`).

    Un paquet que le squelette dérive est réécrit à la version du catalogue ;
    un paquet que le squelette ne connaît pas est conservé tel quel. Trier par
    nom rend la liste reproductible quel que soit l'ordre des ajouts.
    """
    names = {package_name(r) for r in generated}
    kept = [r for r in existing if package_name(r) not in names]
    return sorted(generated + kept, key=package_name)


def _comparable(target: Path, text: str) -> str:
    """Ce que `--check` compare à l'octet. Pour `pyproject.toml`, tout SAUF `dependencies`.

    La liste de dépendances est jugée à part (`_missing_pins`) : elle doit
    CONTENIR chaque épinglage dérivé des `.libs.json` actifs, et peut porter
    en plus ce que le projet a ajouté par `uv add`. L'exiger égale à l'octet
    ferait de chaque ajout légitime une « dérive » ; ne pas la juger du tout
    laissait `dependencies = []` passer au vert — un projet qui ne s'installe pas.
    """
    if target.name != "pyproject.toml":
        return text
    return _DEPENDENCIES_RE.sub("dependencies = []", text)


def _missing_pins(target: Path, current: str, rendered: str) -> list[str]:
    if target.name != "pyproject.toml":
        return []
    have = set(_declared_dependencies(current))
    return [r for r in _declared_dependencies(rendered) if r not in have]


def _declared_secret_names(root: Path) -> set[str]:
    """Les NOMS déclarés sous `## Active Secrets` de STACK.md (`- LLM_API_KEY: ${LLM_API_KEY}`)."""
    stack = paths.stack_md_path(root)
    if not stack.is_file():
        return set()
    body = markdown_io.section_body(markdown_io.read_text(stack), "Active Secrets") or ""
    return set(re.findall(r"^\s*-\s*([A-Z][A-Z0-9_]*)\s*:", body, re.M))


def generate(ctx: Context, report: Report, *, write: bool) -> dict[str, Any]:
    deps = resolve_dependencies(ctx, report)
    targets = plan(ctx, report, deps)
    written: list[str] = []
    drifted: list[str] = []
    missing: list[str] = []
    missing_pins: list[str] = []

    for target, content in targets:
        rel = paths.rel(ctx.root, target)
        exists = target.is_file()
        current = markdown_io.read_text(target) if exists else ""
        pins_absent = _missing_pins(target, current, content) if exists else []
        same = exists and _comparable(target, current) == _comparable(target, content) and not pins_absent
        if exists and target.name == "pyproject.toml":
            # Les ajouts du projet (`uv add`) survivent à la régénération ;
            # les épinglages dérivés sont réécrits à la version du catalogue.
            merged = merge_dependencies(deps.runtime, _declared_dependencies(current))
            content = _DEPENDENCIES_RE.sub(lambda _m: "dependencies = " + _toml_list(merged, multiline=True),
                                           content, count=1)
        missing_pins.extend(pins_absent)
        if write:
            if not same:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                written.append(rel)
        elif not exists:
            missing.append(rel)
        elif not same:
            drifted.append(rel)

    if missing or drifted:
        detail = ", ".join((missing + drifted)[:4]) + (" …" if len(missing) + len(drifted) > 4 else "")
        if missing_pins and not write:
            detail += f" ; pyproject sans les épinglages {missing_pins[:4]}{' …' if len(missing_pins) > 4 else ''}"
        report.error(
            "APP_SKELETON_STALE",
            f"{len(missing)} fichier(s) absent(s) et {len(drifted)} divergent(s) : {detail}",
            fix="`python .sdda/sdda.py gen-app-skeleton --write`. Ce squelette est du code "
                "INVARIANT : une retouche locale se perd à la régénération suivante, et "
                "diverge en silence d'ici là",
            location=paths.rel(ctx.root, ctx.src_root))

    return {
        "app": ctx.app, "mission": ctx.mission, "language": ctx.language,
        "deliverableType": ctx.deliverable, "servingSurfaces": ctx.surfaces,
        "srcRoot": paths.rel(ctx.root, ctx.src_root),
        "planned": [paths.rel(ctx.root, t) for t, _ in targets],
        "written": written, "drifted": drifted, "missing": missing,
        "dependencies": deps.to_dict(), "missingPins": [] if write else missing_pins,
    }


def run(root: Path, *, mode: str = "check", src_root: Path | None = None) -> Report:
    report = Report(name="GEN-APP-SKELETON", target=str(root))

    if not paths.stack_md_path(root).is_file():
        report.error("STACK_MISSING", "STACK.md introuvable", fix="lancer `python bootstrap.py`")
        return report

    ctx = Context.resolve(root, report, src_root=src_root)

    if ctx.language != LANGUAGE:
        report.error(
            "STACK_LANGUAGE_MISMATCH",
            f"`## Active Language & Runtime` active `{ctx.language or '<aucune>'}`, "
            f"ce squelette est écrit en `{LANGUAGE}`",
            fix=f"activer `.sdda/stacks/lang/{LANGUAGE}.md`, ou attendre le squelette du "
                "langage actif — écrire du Python dans un projet qui n'en est pas n'échoue "
                "pas ici, mais à la compilation, trois phases plus loin",
            location="workspace/stack/STACK.md ## Active Language & Runtime")
        return report

    if not ctx.app or ctx.app.startswith("<"):
        report.error(
            "STACK_PLACEHOLDER_UNRESOLVED",
            f"`AppName` absent ou non résolu ({ctx.app or '<vide>'})",
            fix="renseigner `AppName` dans `## Project Config` — il nomme le paquet, le "
                "point d'entrée console et le dossier du projet généré",
            location="workspace/stack/STACK.md ## Project Config")
        return report

    # La cohérence livrable x langage x surface appartient à `validate_packaging`,
    # l'enforcer nommé par INVARIANTS pour la part `packaging` de G2. On lui
    # délègue et on reprend ses findings tels quels : redire ses règles ici
    # créerait deux vérités sur la même question.
    packaging = validate_packaging.run(root, Report(name="PACKAGING", target=str(root)))
    report.extend(packaging)
    if not packaging.ok:
        report.data.update({"packaging": packaging.data})
        return report

    report.data.update(generate(ctx, report, write=(mode == "write")))
    report.data["packaging"] = packaging.data
    return report


# ---------------------------------------------------------------------------
# Entrée
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Génère le squelette applicatif Python (0 token, aucun réseau)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="défaut : comparer sans rien écrire, exit 1 si dérive")
    mode.add_argument("--write", action="store_true", help="écrire les fichiers manquants ou divergents")
    p.add_argument("--src-root", type=Path, default=None, help="racine du paquet applicatif généré")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    report = run(resolve_root(args), mode="write" if args.write else "check",
                 src_root=args.src_root)
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
