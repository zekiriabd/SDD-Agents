"""Project Config en 3 couches : base < team < projet, avec protection security-down.

    1. `.sdda/config.base.yml`                 défauts du framework
    2. `~/.sdda/config.team.yml`               politique d'équipe (override : $SDDA_TEAM_CONFIG)
    3. `workspace/stack/STACK.md ## Project Config`   override final du projet

Deep-merge : scalaires remplacés, listes remplacées (jamais concaténées),
mappings fusionnés récursivement.

Clé inconnue (absente du schéma `templates/project-config.schema.json` ou, tant
qu'il n'existe pas, des clés de la couche base) -> WARN `[CONFIG_UNKNOWN_KEY]`.
`SDDA_CONFIG_STRICT=1` la rend fatale.

PROTECTION SECURITY-DOWN : le projet ne peut pas relâcher ce que la couche team a
durci sur les clés de `security_down_protected` (config.base.yml). Le sens de
« plus strict » dépend de la clé : sévérité (`critical` < `serious` < …), mode
(`full` > `manual` > `off`, `strict` > `warn` > `off`), numérique (plancher ou
plafond), booléen (`true` = durci). Violation -> `SddaError`
`[CONFIG_SECURITY_DOWNGRADE]`, bloquant.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdda_lib import markdown_io, paths, yaml_mini
from sdda_lib.errors import SddaError

#: Repli si `security_down_protected` manque dans config.base.yml.
DEFAULT_PROTECTED_KEYS: tuple[str, ...] = (
    "AgentSafetyFailOn", "GroundednessMin", "EvalRuns", "RetrievalRecallAtK", "HoldoutDisjointCheck",
)

#: Ordres « du plus strict au plus laxiste » pour les valeurs symboliques.
SEVERITY_ORDER = ("critical", "serious", "moderate", "minor", "info")
MODE_ORDER = ("full", "manual", "off")
GATE_ORDER = ("strict", "warn", "off")

#: Clés numériques où « plus grand = plus strict » (plancher imposé par la team).
FLOOR_KEYS = frozenset({
    "GroundednessMin", "RetrievalRecallAtK", "CitationResolveRateMin", "JudgeCalibrationMinKappa",
    "EvalRuns", "EvalRunsCritical", "RetrievalNdcgMin", "JudgeCalibrationMinItems",
    "GoldenSetMinItems", "HoldoutSetMinItems", "AdversarialSetMinItems", "CalibrationSetMinItems",
})
#: Clés numériques où « plus petit = plus strict » (plafond imposé par la team).
CEILING_KEYS = frozenset({"MaxBypassesPerRun", "RegressionTolerancePct", "MaxNestingDepth", "MaxAgentsWarnAt"})
#: Clés booléennes où `true` = durci.
TRUE_IS_STRICT_KEYS = frozenset({
    "PromptInlineForbidden", "TopologyJustificationRequired", "AgentSafetyRequiredInProduction",
    "TraceRequiredPerRun", "PromptHashPinning", "JudgeMustDifferFromEvaluated",
})


@dataclass
class LayeredConfig:
    config: dict[str, Any]
    sources: dict[str, str]                 # clé -> base | team | project
    warnings: list[str] = field(default_factory=list)
    layer_paths: dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        v = self.config.get(key)
        return default if v is None else v

    def get_int(self, key: str, default: int) -> int:
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def get_float(self, key: str, default: float) -> float:
        try:
            return float(self.get(key, default))
        except (TypeError, ValueError):
            return default


def team_config_path() -> Path:
    override = os.environ.get("SDDA_TEAM_CONFIG")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~")) / ".sdda" / "config.team.yml"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return yaml_mini.parse_mapping(markdown_io.read_text(path))


def app_name(root: Path) -> str:
    """`AppName` de `## Project Config`, ou `App` — le nom du paquet sous `workspace/src/`.

    Tout ce qui vit DANS l'application (prompts, skills, rules, schémas figés,
    outils générés) se résout par lui ; un seul endroit pour le lire évite que
    deux scripts se disputent le nom du répertoire.
    """
    return str(read_project_section(root).get("AppName") or "App").strip() or "App"


def read_project_section(root: Path) -> dict[str, Any]:
    """`## Project Config` de STACK.md, parsé comme du YAML plat."""
    p = paths.stack_md_path(root)
    if not p.is_file():
        return {}
    body = markdown_io.section_body(markdown_io.read_text(p), "Project Config")
    if body is None:
        return {}
    return yaml_mini.parse_mapping(body)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Mappings fusionnés récursivement ; scalaires ET listes remplacés."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------
# Security-down
# --------------------------------------------------------------------------
def _rank(value: Any, order: tuple[str, ...]) -> int | None:
    try:
        return order.index(str(value).strip().lower())
    except ValueError:
        return None


def is_downgrade(key: str, team_val: Any, project_val: Any) -> bool:
    """Vrai si `project_val` est plus laxiste que `team_val` pour `key`."""
    if team_val is None or project_val is None:
        return False
    k = key
    if k.endswith("FailOn"):
        t, p = _rank(team_val, SEVERITY_ORDER), _rank(project_val, SEVERITY_ORDER)
        return t is not None and p is not None and p > t
    if k.endswith("Mode"):
        t, p = _rank(team_val, MODE_ORDER), _rank(project_val, MODE_ORDER)
        return t is not None and p is not None and p > t
    if k.endswith("Gate") or k in ("HoldoutDisjointCheck", "StackComboCheck"):
        t, p = _rank(team_val, GATE_ORDER), _rank(project_val, GATE_ORDER)
        return t is not None and p is not None and p > t
    if k in TRUE_IS_STRICT_KEYS or isinstance(team_val, bool):
        return bool(team_val) is True and bool(project_val) is False
    if k in FLOOR_KEYS or k in CEILING_KEYS or isinstance(team_val, (int, float)):
        try:
            t, p = float(team_val), float(project_val)
        except (TypeError, ValueError):
            return False
        return p > t if k in CEILING_KEYS else p < t
    return False


def check_security_down(team: dict[str, Any], project: dict[str, Any], protected: list[str]) -> None:
    for key in protected:
        if key in team and key in project and is_downgrade(key, team[key], project[key]):
            raise SddaError(
                error=f"Project Config rejeté : relâchement d'un seuil protégé ({key})",
                cls="CONFIG_SECURITY_DOWNGRADE",
                detail=f"le projet fixe {key}={project[key]!r} alors que la couche team impose {key}={team[key]!r}",
                fix=f"retirer {key} de `## Project Config` ou choisir une valeur au moins aussi stricte que celle de l'équipe",
            )


# --------------------------------------------------------------------------
# Clés connues
# --------------------------------------------------------------------------
def known_keys(root: Path, base: dict[str, Any]) -> set[str]:
    """Clés du schéma `project-config.schema.json` s'il existe, sinon celles de la base."""
    schema_path = paths.project_config_schema_path(root)
    if schema_path.is_file():
        import json
        try:
            props = json.loads(markdown_io.read_text(schema_path)).get("properties", {})
            return set(props) | set(base)
        except (ValueError, AttributeError):
            pass
    return set(base)


# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------
def read_layered_config(root: Path, *, team_path: Path | None = None, warn_stream=None) -> LayeredConfig:
    """Lit et fusionne les 3 couches. Lève `SddaError` sur security-down / strict."""
    base_path = paths.base_config_path(root)
    base = _read_yaml(base_path)
    protected_raw = base.pop("security_down_protected", None)
    protected = [str(k) for k in protected_raw] if isinstance(protected_raw, list) else list(DEFAULT_PROTECTED_KEYS)

    tpath = team_path or team_config_path()
    team = _read_yaml(tpath)
    team.pop("security_down_protected", None)
    project = read_project_section(root)

    check_security_down(team, project, protected)

    effective = deep_merge(deep_merge(base, team), project)
    sources = {k: "base" for k in base}
    sources.update({k: "team" for k in team})
    sources.update({k: "project" for k in project})

    warnings: list[str] = []
    known = known_keys(root, base)
    unknown = sorted(k for k in effective if k not in known and not k.startswith("_"))
    for k in unknown:
        warnings.append(
            f"WARN [CONFIG_UNKNOWN_KEY] clé « {k} » (couche {sources.get(k, '?')}) absente du schéma : "
            f"faute de frappe ou clé retirée ? SDDA_CONFIG_STRICT=1 la rend fatale"
        )
    stream = warn_stream if warn_stream is not None else sys.stderr
    for w in warnings:
        print(w, file=stream)
    if unknown and os.environ.get("SDDA_CONFIG_STRICT", "0").strip().lower() in ("1", "true", "yes", "on"):
        raise SddaError(
            error="Project Config refusé (mode strict)",
            cls="CONFIG_UNKNOWN_KEY",
            detail=f"{len(unknown)} clé(s) inconnue(s) : {', '.join(unknown)}",
            fix="corriger la faute de frappe ou déclarer la clé dans templates/project-config.schema.json",
        )

    return LayeredConfig(
        config=effective,
        sources=sources,
        warnings=warnings,
        layer_paths={"base": str(base_path), "team": str(tpath), "project": str(paths.stack_md_path(root))},
    )


def read_stack_section_kv(root: Path, heading: str) -> dict[str, Any]:
    """Une section `## …` de STACK.md lue comme YAML (ex. `Runtime Models`)."""
    p = paths.stack_md_path(root)
    if not p.is_file():
        return {}
    body = markdown_io.section_body(markdown_io.read_text(p), heading)
    if body is None:
        return {}
    # Les lignes ` - .sdda/stacks/...` (stacks actives) ne sont pas du YAML clé/valeur.
    kept = "\n".join(l for l in body.split("\n") if not l.lstrip().startswith("- .sdda/"))
    try:
        return yaml_mini.parse_mapping(kept)
    except yaml_mini.YamlMiniError:
        return {}


def active_stacks(root: Path, heading: str) -> list[str]:
    """Stacks actives d'une section : lignes ` - .sdda/stacks/{cat}/{nom}.md` -> [nom]."""
    p = paths.stack_md_path(root)
    if not p.is_file():
        return []
    body = markdown_io.section_body(markdown_io.read_text(p), heading)
    if body is None:
        return []
    out = []
    for line in body.split("\n"):
        s = line.strip()
        if s.startswith("- .sdda/stacks/"):
            out.append(s.rsplit("/", 1)[-1].removesuffix(".md"))
    return out


def read_runtime_tier_map(root: Path) -> dict[str, str]:
    """`## Runtime Models` -> {tier: identifiant de modèle}.

    Ce sont les modèles que fait tourner l'APPLICATION GÉNÉRÉE, jamais ceux qui
    construisent (`## Build Models`). Confondre les deux rend le budget
    d'exécution incalculable — cf. ARCHITECTURE.md §6.

    Sert à l'épinglage (P10) : `balanced` seul n'identifie rien, il peut
    désigner deux modèles différents à deux semaines d'intervalle.
    """
    section = read_stack_section_kv(root, "Runtime Models")
    raw = section.get("RuntimeTierMap") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v not in (None, "")}
