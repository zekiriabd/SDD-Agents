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


def plan(ctx: Context, report: Report) -> list[tuple[Path, str]]:
    """(cible, contenu) pour chaque fichier du squelette. Trié, donc reproductible."""
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
            targets.append((ctx.project_dir / "pyproject.toml", _subst(text, ctx)))
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
_DEPENDENCIES_RE = re.compile(r"^dependencies = \[(?:[^\]]|\n)*?\]$", re.M)


def _comparable(target: Path, text: str) -> str:
    """Ce que `--check` compare. Pour `pyproject.toml`, tout SAUF `dependencies`.

    La liste de dépendances est remplie par `uv add` depuis les `.libs.json`
    des stacks actives (`lang/python.md §2.1`) : le squelette l'écrit vide, et
    l'exiger vide faisait de chaque projet correctement installé un projet
    « dérivé ». Le reste du fichier (identité, point d'entrée, layout) reste
    comparé à l'octet.
    """
    if target.name != "pyproject.toml":
        return text
    return _DEPENDENCIES_RE.sub("dependencies = []", text)


def _declared_secret_names(root: Path) -> set[str]:
    """Les NOMS déclarés sous `## Active Secrets` de STACK.md (`- LLM_API_KEY: ${LLM_API_KEY}`)."""
    stack = paths.stack_md_path(root)
    if not stack.is_file():
        return set()
    body = markdown_io.section_body(markdown_io.read_text(stack), "Active Secrets") or ""
    return set(re.findall(r"^\s*-\s*([A-Z][A-Z0-9_]*)\s*:", body, re.M))


def generate(ctx: Context, report: Report, *, write: bool) -> dict[str, Any]:
    targets = plan(ctx, report)
    written: list[str] = []
    drifted: list[str] = []
    missing: list[str] = []

    for target, content in targets:
        rel = paths.rel(ctx.root, target)
        exists = target.is_file()
        same = exists and _comparable(target, markdown_io.read_text(target)) == _comparable(target, content)
        if write:
            if not same:
                if exists and target.name == "pyproject.toml":
                    # La liste `dependencies` appartient à `uv add` : la
                    # régénération réécrit tout le reste, jamais elle.
                    current = _DEPENDENCIES_RE.search(markdown_io.read_text(target))
                    if current:
                        content = _DEPENDENCIES_RE.sub(lambda _m: current.group(0), content, count=1)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                written.append(rel)
        elif not exists:
            missing.append(rel)
        elif not same:
            drifted.append(rel)

    if missing or drifted:
        detail = ", ".join((missing + drifted)[:4]) + (" …" if len(missing) + len(drifted) > 4 else "")
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
