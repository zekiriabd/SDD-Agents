"""Project Config en couches : base < profil < team < projet, avec protection security-down.

    1. `.sdda/config.base.yml`                 défauts du framework
    1b. `.sdda/profiles/{Profile}.yml`          défauts du profil (`poc` : jeux plus petits…)
    2. `~/.sdda/config.team.yml`               politique d'équipe (override : $SDDA_TEAM_CONFIG)
    3. `workspace/stack/STACK.md ## Project Config`   override final du projet

Deep-merge : scalaires remplacés, listes remplacées (jamais concaténées),
mappings fusionnés récursivement.

Clé inconnue (absente du schéma `templates/project-config.schema.json` ou, tant
qu'il n'existe pas, des clés de la couche base) -> WARN `[CONFIG_UNKNOWN_KEY]`.
`SDDA_CONFIG_STRICT=1` la rend fatale.

VALEURS : `validate_config` confronte chaque valeur (Project Config effectif
et sections `## Active *`, schéma `x-stackSections`) à son type, son
énumération et ses bornes -> `[CONFIG_VALUE_INVALID]`, que `smoke-check` et
`preflight_stack_combo` rendent bloquant. Les clés `x-section` (TraceLevel,
GoldenSetMinItems…) se lisent aussi dans leur section, comme couche projet.

PROTECTION SECURITY-DOWN : le projet ne peut pas relâcher ce que la couche team a
durci sur les clés de `security_down_protected` (config.base.yml). Le sens de
« plus strict » dépend de la clé : sévérité (`critical` < `serious` < …), mode
(`full` > `manual` > `off`, `strict` > `warn` > `off`), identité d'appelant
(`none` < `api-key` < `oauth2` = `azure-ad` = `mtls`), numérique (plancher ou
plafond), booléen (`true` = durci). Violation -> `SddaError`
`[CONFIG_SECURITY_DOWNGRADE]`, bloquant.
"""
from __future__ import annotations

import io
import os
import re
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
#: Un `*FailOn` bloque tout finding de sévérité >= au seuil
#: (validate_safety_gate) : `info` bloque tout, `critical` presque rien.
SEVERITY_ORDER = ("info", "minor", "moderate", "serious", "critical")
MODE_ORDER = ("full", "manual", "off")
GATE_ORDER = ("strict", "warn", "off")

#: Clés numériques où « plus grand = plus strict » (plancher imposé par la team).
FLOOR_KEYS = frozenset({
    "GroundednessMin", "RetrievalRecallAtK", "CitationResolveRateMin", "JudgeCalibrationMinKappa",
    "EvalRuns", "EvalRunsCritical", "RetrievalNdcgMin", "JudgeCalibrationMinItems",
    "GoldenSetMinItems", "HoldoutSetMinItems", "AdversarialSetMinItems", "CalibrationSetMinItems",
})
#: Clés numériques où « plus petit = plus strict » (plafond imposé par la team).
CEILING_KEYS = frozenset({"MaxBypassesPerRun", "RegressionTolerancePct", "MaxAgentsWarnAt"})
#: Clés booléennes où `true` = durci.
TRUE_IS_STRICT_KEYS = frozenset({
    "AgentSafetyRequiredInProduction", "TraceRequiredPerRun", "JudgeMustDifferFromEvaluated",
    "ApiContractFirst",
})
#: `ApiAuthMode` : par où entre l'identité de l'appelant. Ce n'est pas un mode
#: `full > manual > off` — le suffixe `Mode` le faisait ranger là, où aucune de
#: ses valeurs n'existe, donc aucun relâchement n'était jamais vu et la
#: protection déclarée dans config.base.yml était inerte. Rang croissant =
#: plus dur ; les trois mécanismes forts sont équivalents entre eux.
AUTH_RANK: dict[str, int] = {"none": 0, "api-key": 1, "oauth2": 2, "azure-ad": 2, "mtls": 2}


@dataclass
class LayeredConfig:
    config: dict[str, Any]
    sources: dict[str, str]                 # clé -> base | team | project
    warnings: list[str] = field(default_factory=list)
    layer_paths: dict[str, str] = field(default_factory=dict)
    #: (clé, section, valeur de `## Project Config`, valeur de la section) —
    #: une clé écrite aux deux endroits avec deux valeurs. Cf. `x-section`.
    conflicts: list[tuple[str, str, Any, Any]] = field(default_factory=list)

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
    name = str(read_project_section(root).get("AppName") or "App").strip() or "App"
    if not paths.APP_NAME_RE.match(name):
        # Le nom devient un répertoire : `../../../x` écrivait l'application —
        # `.env` compris — hors du workspace, et le générateur rendait « OK ».
        # Le motif du schéma n'était appliqué qu'au preflight ; tout script qui
        # résout un chemin d'application passe par ici.
        raise SddaError(f"`AppName: {name}` n'est pas un nom d'application valide", "CONFIG_VALUE_INVALID",
                        "un identifiant : une lettre puis lettres, chiffres, `_` ou `-` (motif "
                        f"`{paths.APP_NAME_RE.pattern}`) — il devient `workspace/src/{{AppName}}/`",
                        location=f"{STACK_LOC} ## Project Config")
    return name


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
    if k == "ApiAuthMode":
        t, p = AUTH_RANK.get(str(team_val).strip().lower()), AUTH_RANK.get(str(project_val).strip().lower())
        return t is not None and p is not None and p < t
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
def load_schema(root: Path) -> dict[str, Any]:
    """`templates/project-config.schema.json`, ou `{}` s'il est absent ou illisible."""
    import json

    schema_path = paths.project_config_schema_path(root)
    if not schema_path.is_file():
        return {}
    try:
        schema = json.loads(markdown_io.read_text(schema_path))
    except ValueError:
        return {}
    return schema if isinstance(schema, dict) else {}


def known_keys(root: Path, base: dict[str, Any]) -> set[str]:
    """Clés du schéma `project-config.schema.json` s'il existe, sinon celles de la base."""
    props = load_schema(root).get("properties")
    return (set(props) | set(base)) if isinstance(props, dict) else set(base)


def section_resident_keys(schema: dict[str, Any]) -> dict[str, str]:
    """{clé de Project Config: section} pour les clés qui portent `x-section`.

    `TraceLevel` ou `GoldenSetMinItems` s'écrivent, dans le gabarit, sous
    `## Active Observability` et `## Active Eval Stack` — là où l'humain les
    cherche. Mais tous les scripts les lisent dans la config en 3 couches, qui
    ne regardait que `## Project Config` : la valeur écrite dans la section
    n'était lue par PERSONNE, et `GoldenSetMinItems: 100` y laissait le
    framework exiger 50. La section devient donc, pour ces clés, une autre
    écriture de la couche projet.
    """
    props = schema.get("properties") or {}
    return {k: str(v["x-section"]) for k, v in props.items() if isinstance(v, dict) and v.get("x-section")}


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
    conflicts: list[tuple[str, str, Any, Any]] = []
    for key, heading in section_resident_keys(load_schema(root)).items():
        section = read_stack_section_kv(root, heading)
        if key not in section:
            continue
        if key in project and project[key] != section[key]:
            conflicts.append((key, heading, project[key], section[key]))
        else:
            project[key] = section[key]

    check_security_down(team, project, protected)

    # Le profil (`Profile: poc | standard | production`) est un JEU DE DÉFAUTS,
    # rangé entre la base et l'équipe : `.sdda/profiles/{profil}.yml`. Il change
    # ce que le framework suppose quand STACK.md ne dit rien — des jeux plus
    # petits pour un poc — sans jamais passer au-dessus de la politique d'équipe
    # (security-down reste jugé team contre projet) ni de ce que le projet écrit.
    profile_name = str(project.get("Profile") or team.get("Profile") or base.get("Profile") or "standard").strip()
    profile_path = profile_config_path(root, profile_name)
    profile = _read_yaml(profile_path)
    profile.pop("security_down_protected", None)

    effective = deep_merge(deep_merge(deep_merge(base, profile), team), project)
    sources = {k: "base" for k in base}
    sources.update({k: "profile" for k in profile})
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
        layer_paths={"base": str(base_path), "profile": str(profile_path), "team": str(tpath),
                     "project": str(paths.stack_md_path(root))},
        conflicts=conflicts,
    )


def profile_config_path(root: Path, profile: str) -> Path:
    """`.sdda/profiles/{profil}.yml` du projet s'il vendore le framework, du framework sinon."""
    sdda = root / ".sdda" if (root / ".sdda" / "profiles").is_dir() else paths.FRAMEWORK_SDDA_DIR
    return sdda / "profiles" / f"{profile}.yml"


def active_profile(root: Path) -> str:
    """`Profile` effectif (`standard` à défaut) — ce que les commandes lisent pour choisir leur chemin."""
    try:
        return str(read_layered_config(root, warn_stream=io.StringIO()).get("Profile", "standard")).strip()
    except SddaError:
        return str(read_project_section(root).get("Profile") or "standard").strip()


#: ` - DB_HOST: ${DB_HOST}` — une DÉCLARATION de variable (nom -> référence),
#: pas une clé de configuration. Au milieu de clés YAML, ces lignes rendaient
#: la section entière illisible : `## Active Data Access` se lisait `{}`, donc
#: `DbAgentRole: full` n'exigeait plus d'ADR et aucune valeur n'était vue.
_ENV_DECLARATION_RE = re.compile(r"^\s?-\s+[A-Z][A-Z0-9_]*\s*:")


def section_kv(root: Path, heading: str) -> tuple[dict[str, Any] | None, str | None]:
    """`(valeurs, erreur)` d'une section : `(None, None)` si elle est absente.

    La forme qui DIT l'échec de lecture. `read_stack_section_kv` rend `{}` sur
    une section illisible — commode pour un lecteur qui a un défaut, fatal pour
    un validateur : une section qu'on ne sait pas lire passerait pour conforme.
    """
    p = paths.stack_md_path(root)
    if not p.is_file():
        return None, None
    body = markdown_io.section_body(markdown_io.read_text(p), heading)
    if body is None:
        return None, None
    # Les lignes ` - .sdda/stacks/...` (stacks actives) et ` - NOM: ${NOM}`
    # (déclarations de variables) ne sont pas du YAML clé/valeur.
    kept = "\n".join(l for l in body.split("\n")
                     if not l.lstrip().startswith("- .sdda/") and not _ENV_DECLARATION_RE.match(l))
    try:
        return yaml_mini.parse_mapping(kept), None
    except yaml_mini.YamlMiniError as exc:
        return {}, str(exc)


def read_stack_section_kv(root: Path, heading: str) -> dict[str, Any]:
    """Une section `## …` de STACK.md lue comme YAML (ex. `Runtime Models`)."""
    values, _ = section_kv(root, heading)
    return values or {}


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


#: Le harnais supporté, et le fichier mémoire qu'il charge : repli quand STACK.md
#: ou la matrice ne disent rien.
DEFAULT_HARNESS = "claude-code"
DEFAULT_MEMORY_FILE = "CLAUDE.md"


def active_harness(root: Path) -> str:
    """`## Active Harness` -> `Harness:` (`claude-code` à défaut)."""
    return str(read_stack_section_kv(root, "Active Harness").get("Harness") or DEFAULT_HARNESS).strip()


def harness_memory_file(root: Path) -> str:
    """`memory_file` du harnais actif dans `capability-matrix.yml` (`CLAUDE.md` à défaut).

    Lu, pas codé en dur : c'est la clé que `harness_build` utilise déjà pour les
    façades du framework. Le fichier de contexte d'une application générée doit
    être celui que le MÊME harnais charge seul en entrant dans son répertoire.
    """
    sdda = root / ".sdda" if (root / ".sdda" / "capability-matrix.yml").is_file() else paths.FRAMEWORK_SDDA_DIR
    matrix_path = sdda / "capability-matrix.yml"
    if not matrix_path.is_file():
        return DEFAULT_MEMORY_FILE
    matrix = yaml_mini.parse_mapping(markdown_io.read_text(matrix_path))
    entry = (matrix.get("harnesses") or {}).get(active_harness(root)) or {}
    return str(entry.get("memory_file") or DEFAULT_MEMORY_FILE)


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


# --------------------------------------------------------------------------
# Valeurs — le schéma validait les NOMS, jamais ce qu'on y écrivait
# --------------------------------------------------------------------------
@dataclass
class ConfigIssue:
    """Un constat sur la configuration, prêt à devenir finding ou refus de hook."""
    cls: str
    message: str
    fix: str
    location: str
    blocking: bool = True


STACK_LOC = "workspace/stack/STACK.md"


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _cross_key_issues(config: dict[str, Any]) -> list[ConfigIssue]:
    """Les contraintes entre clés que JSON Schema n'exprime pas (le schéma les annonçait)."""
    out: list[ConfigIssue] = []
    loc = f"{STACK_LOC} ## Project Config"
    pairs = (
        ("CostPerRunTargetUsd", "CostPerRunHardCapUsd", "le plafond dur est au moins égal à la cible"),
        ("EvalRuns", "EvalRunsCritical", "une CAP critique se mesure sur au moins autant de runs qu'une autre"),
        ("CapGranularityTarget", "CapGranularityWarnAt", "l'avertissement tombe au-delà de la cible"),
        ("CapGranularityWarnAt", "CapGranularityHardCap", "le plafond tombe au-delà de l'avertissement"),
    )
    for low, high, why in pairs:
        a, b = _number(config.get(low)), _number(config.get(high))
        if a is not None and b is not None and b < a:
            out.append(ConfigIssue("CONFIG_VALUE_INVALID", f"`{high}: {config.get(high)}` < `{low}: {config.get(low)}`",
                                   f"{why} : relever `{high}` ou baisser `{low}`", loc))

    # Observabilité : quatre clés que le gabarit déclarait et qu'aucun script
    # ne lisait. Leur sens se lit ici, contre les invariants qu'elles touchent.
    oloc = f"{STACK_LOC} ## Active Observability"
    level = str(config.get("TraceLevel") or "full").strip().lower()
    if level == "off" and config.get("TraceRequiredPerRun") is not False:
        out.append(ConfigIssue("CONFIG_VALUE_INVALID", "`TraceLevel: off` alors que `TraceRequiredPerRun: true`",
                               "un run sans trace est un échec (invariant trace-emitted-per-run) : `off` n'est "
                               "tenable qu'en POC jetable, avec `TraceRequiredPerRun: false` assumé", oloc))
    rate = _number(config.get("TraceSampleRate"))
    if rate is not None and rate < 1.0:
        if level != "sampled":
            out.append(ConfigIssue("CONFIG_VALUE_INVALID", f"`TraceSampleRate: {rate}` ignoré : `TraceLevel: {level}`",
                                   "le taux ne s'applique qu'à `TraceLevel: sampled`", oloc, blocking=False))
        else:
            out.append(ConfigIssue("CONFIG_VALUE_INVALID", f"`TraceLevel: sampled` à {rate} : des runs n'auront pas de trace",
                                   "les graders de trajectoire rendront `[EVAL_OUTPUT_UNGRADABLE]` sur les runs non "
                                   "tracés — garder 1.0 pendant les évaluations", oloc, blocking=False))
    if config.get("CostTrackingEnabled") is False and _number(config.get("CostPerRunHardCapUsd")) is not None:
        out.append(ConfigIssue("CONFIG_VALUE_INVALID",
                               "`CostTrackingEnabled: false` avec un `CostPerRunHardCapUsd` déclaré",
                               "sans coût par appel dans les traces, G6 ne peut pas mesurer le plafond "
                               "([BUDGET_EXCEEDED_MEASURED] devient invérifiable) : réactiver le suivi", oloc))
    return out


def validate_config(root: Path, lc: LayeredConfig | None = None) -> list[ConfigIssue]:
    """Types, énumérations et bornes des VALEURS — Project Config et sections `## Active *`.

    `OnBoundExceeded: foo` passait : `known_keys` ne regardait que le nom de la
    clé, et le schéma portait ~35 énumérations que personne n'appliquait. Une
    valeur hors domaine n'échoue pas ici, elle échoue plus tard et ailleurs —
    ou pas du tout : un script qui compare `== "strict"` traite `stirct` comme
    `warn`, c'est-à-dire qu'il desserre une gate sur une faute de frappe.

    Rend des constats, ne lève rien : c'est `smoke-check` et
    `preflight_stack_combo` qui en font des refus. `read_layered_config` reste
    tolérant, pour que chaque script ne rougisse pas sur une clé qui ne le
    concerne pas.
    """
    from sdda_lib.jsonschema_mini import SchemaValidator

    schema = load_schema(root)
    if not schema:
        return []
    if lc is None:
        try:
            lc = read_layered_config(root, warn_stream=io.StringIO())
        except SddaError:
            return []  # security-down : déjà un refus, porté par qui lit la config
    validator = SchemaValidator(schema)
    props: dict[str, Any] = schema.get("properties") or {}
    issues: list[ConfigIssue] = []
    loc = f"{STACK_LOC} ## Project Config"

    for key, value in sorted(lc.config.items()):
        if key == "security_down_protected" or not isinstance(props.get(key), dict):
            continue
        for err in validator.validate(value, props[key], path=key):
            issues.append(ConfigIssue(
                "CONFIG_VALUE_INVALID", f"{err} (couche {lc.sources.get(key, '?')})",
                "corriger la valeur — domaine admis : templates/project-config.schema.json", loc))
    issues += _cross_key_issues(lc.config)
    for key, heading, in_config, in_section in lc.conflicts:
        issues.append(ConfigIssue(
            "CONFIG_KEY_CONFLICT",
            f"`{key}` vaut {in_config!r} dans `## Project Config` et {in_section!r} dans `## {heading}`",
            "n'écrire la clé qu'à un endroit : deux valeurs pour une clé, c'est celle que personne ne relit qui gouverne",
            f"{STACK_LOC} ## {heading}"))

    resident = section_resident_keys(schema)
    sections: dict[str, Any] = schema.get("x-stackSections") or {}
    for heading, section_schema in sections.items():
        if not isinstance(section_schema, dict):
            continue                                    # `description`
        values, error = section_kv(root, heading)
        sloc = f"{STACK_LOC} ## {heading}"
        if error:
            issues.append(ConfigIssue(
                "CONFIG_VALUE_INVALID", f"`## {heading}` illisible ({error}) : aucune de ses valeurs n'est lue",
                "réaligner la section sur .sdda/templates/STACK.md.template (une clé par ligne, `NOM: ${NOM}` pour une variable)",
                sloc))
            continue
        section_props = section_schema.get("properties") or {}
        for key, value in sorted((values or {}).items()):
            if key in section_props:
                for err in validator.validate(value, section_props[key], path=key):
                    issues.append(ConfigIssue("CONFIG_VALUE_INVALID", err,
                                              "corriger la valeur — domaine admis : x-stackSections de "
                                              "templates/project-config.schema.json", sloc))
            elif resident.get(key) == heading:
                continue                                # validée plus haut, dans la config effective
            elif key in props:
                # Une clé de Project Config écrite dans une section qui ne la
                # porte pas : aucun script ne l'y lit. Rouge si elle dit autre
                # chose que la valeur effective — c'est alors un réglage
                # silencieusement ignoré.
                effective = lc.config.get(key)
                ignored = value != effective
                issues.append(ConfigIssue(
                    "CONFIG_KEY_MISPLACED",
                    f"`{key}: {value!r}` écrit sous `## {heading}` n'est lu par personne"
                    + (f" — la valeur appliquée est {effective!r}" if ignored else ""),
                    f"déplacer `{key}` dans `## Project Config`", sloc, blocking=ignored))
            elif not key.startswith("_"):
                issues.append(ConfigIssue(
                    "CONFIG_UNKNOWN_KEY", f"clé « {key} » inconnue de `## {heading}` : faute de frappe ou clé retirée ?",
                    "la retirer, ou la déclarer dans x-stackSections de templates/project-config.schema.json",
                    sloc, blocking=False))
    return issues


# --------------------------------------------------------------------------
# Juge ≠ évalué (JudgeMustDifferFromEvaluated)
# --------------------------------------------------------------------------
def _ir_tiers(root: Path) -> set[str] | None:
    """Tiers des agents du PRODUIT, lus dans les IR compilés ; `None` si aucun IR.

    C'est l'IR qui sait quel modèle est évalué : un agent déclare un tier, et
    seuls les tiers réellement portés par des agents font tourner un modèle
    que le juge notera. Avant l'IR, la question n'a pas de réponse complète.
    """
    import json

    irs = sorted(paths.ir_dir(root).glob("*-system.ir.json")) if paths.ir_dir(root).is_dir() else []
    if not irs:
        return None
    tiers: set[str] = set()
    for p in irs:
        try:
            ir = json.loads(markdown_io.read_text(p))
        except (OSError, ValueError):
            continue
        for agent in ir.get("agents") or []:
            if isinstance(agent, dict) and agent.get("modelTier"):
                tiers.add(str(agent["modelTier"]))
    return tiers


def judge_issues(root: Path, lc: LayeredConfig | None = None) -> list[ConfigIssue]:
    """`JudgeMustDifferFromEvaluated: true` appliqué — il n'était que noté.

    `llm_judge.py` enregistrait `judge_equals_evaluated` dans son rapport, et
    rien ne le refusait : un Gemini qui note Gemini rendait un verdict
    bloquant comme un autre. Le contrôle se fait ici, sur la configuration,
    parce que c'est là que la décision se prend — avant que 50 items × k runs
    aient été payés pour mesurer une complaisance.

    - Avec un IR : les modèles évalués sont ceux des tiers que portent les
      agents. `JudgeModel` parmi eux -> `[JUDGE_SAME_AS_EVALUATED]`, bloquant.
    - Sans IR : on ne sait pas encore quels tiers seront portés. Si TOUS les
      tiers résolvent vers le modèle juge, la réponse est déjà connue —
      bloquant. Sinon, un juge présent dans la table est un avertissement.
    """
    if lc is None:
        try:
            lc = read_layered_config(root, warn_stream=io.StringIO())
        except SddaError:
            return []
    flag = lc.config.get("JudgeMustDifferFromEvaluated", True)
    if flag is False or str(flag).strip().lower() in ("false", "no", "off", "0"):
        return []
    judge = str(read_stack_section_kv(root, "Runtime Models").get("JudgeModel") or "").strip()
    if not judge or judge.lower() == "none":
        return []
    from sdda_lib import pricing  # noqa: PLC0415 — évite un cycle d'import

    raw_map = read_runtime_tier_map(root)
    # Un tier absent de `RuntimeTierMap` tourne sur le repli documenté, et
    # `claude-opus-5[1m]` EST `claude-opus-5` : comparer les chaînes brutes
    # laissait le juge noter sa propre sortie sous une autre graphie.
    tier_map = {t: pricing.base_model_id(pricing.resolve_model(t, raw_map))
                for t in {*raw_map, *pricing.DEFAULT_TIER_MAP}}
    judge_model = pricing.base_model_id(tier_map.get(judge, judge))
    loc = f"{STACK_LOC} ## Runtime Models"
    fix = ("choisir un `JudgeModel` qu'aucun tier porté par un agent ne résout (un modèle plus fort, ou d'un autre "
           "fournisseur) ; ou, si c'est impossible, `JudgeMustDifferFromEvaluated: false` dans `## Project Config`, "
           "et le juge ne rendra que des verdicts à assumer")
    used = _ir_tiers(root)
    if used is not None:
        same = sorted(t for t in used if tier_map.get(t, t) == judge_model)
        if same:
            return [ConfigIssue("JUDGE_SAME_AS_EVALUATED",
                                f"`JudgeModel: {judge}` résout vers `{judge_model}`, le modèle des agents en tier "
                                f"{', '.join(f'`{t}`' for t in same)} (IR) : le juge noterait sa propre sortie",
                                fix, loc)]
        return []
    hit = sorted(t for t, m in tier_map.items() if m == judge_model)
    if not hit:
        return []
    if set(tier_map.values()) == {judge_model}:
        return [ConfigIssue("JUDGE_SAME_AS_EVALUATED",
                            f"`JudgeModel: {judge}` : TOUS les tiers de `RuntimeTierMap` résolvent vers `{judge_model}` — "
                            "quel que soit l'agent, le juge se notera lui-même", fix, loc)]
    return [ConfigIssue("JUDGE_SAME_AS_EVALUATED",
                        f"`JudgeModel: {judge}` = `RuntimeTierMap.{hit[0]}` : bloquant dès que l'IR affecte ce tier à un agent",
                        fix, loc, blocking=False)]
