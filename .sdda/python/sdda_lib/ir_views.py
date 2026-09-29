"""Vues d'IR par agent de construction — ce que chaque agent lit de l'IR.

L'IR compilée (`workspace/.sys/.ir/{n}-system.ir.json`) décrit le système
entier : 100 à 175 Ko sur un projet réel, dont l'essentiel en contrats d'outils
(schémas, descriptions, erreurs). Chaque `dev-*`, `qa-*` et reviewer la lisait
EN ENTIER, alors que `dev-api` n'en exploite que le point d'entrée et ses
schémas, et une instance de `dev-agent` que SON agent et SES outils. Le contexte
initial d'un agent étant renvoyé au modèle à chaque tour, ces octets se
repayaient des dizaines de fois par agent — la moitié de la facture d'entrée
d'une MISSION, pour du texte que l'agent ne regardait pas.

Une vue est une PROJECTION déterministe de l'IR, jamais une seconde source :

- elle garde `irVersion`, `missionId` et `compiledFrom` ;
- elle ajoute un bloc `view` qui dit pour qui elle est faite, l'empreinte de
  l'IR dont elle dérive (`sourceIrHash`) et CE QUI A ÉTÉ RETIRÉ — pour qu'un
  agent ne conclue pas d'une section absente qu'elle n'existe pas ;
- elle est régénérée à chaque compilation (`ir-compiler`) et vérifiée par
  `ir-view --check` (`[IR_VIEW_STALE]`).

Les champs retenus par agent sont ceux que sa fiche exploite. Un agent qui a
besoin d'un champ retiré le lit dans l'IR complète : la vue réduit ce qui est
chargé par défaut, elle n'interdit rien.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sdda_lib import hashing

#: Version du format de vue. Change si la forme du bloc `view` change.
VIEW_VERSION = "1"

#: Sous-répertoire de `workspace/.sys/.ir/` où vivent les vues. Hors du motif
#: `*-system.ir.json` que parcourent `check-ir-freshness` et `compute-status` :
#: une vue n'est pas une IR.
VIEWS_DIRNAME = "views"

#: Toujours présents : l'identité de l'IR et sa provenance.
_HEADER = ("irVersion", "missionId", "compiledFrom")

# Réductions d'éléments : les champs gardés d'un agent ou d'un outil quand la
# vue n'a besoin que de son identité et de son interface, pas de son contrat.
AGENT_IDENTITY = ("id", "role", "modelTier", "servesCaps")
AGENT_INTERFACE = AGENT_IDENTITY + ("inputSchema", "outputSchema")
AGENT_WIRING = AGENT_INTERFACE + ("tools", "retrievers", "memoryScopes", "handoffs", "bounds",
                                  "onBoundExceeded", "promptRef", "promptHash")
AGENT_POSTURE = AGENT_IDENTITY + ("tools", "retrievers", "memoryScopes", "handoffs", "trustPosture",
                                  "refusalPolicy", "bounds", "inputSchema")
TOOL_IDENTITY = ("id", "name", "sideEffectClass")
TOOL_SAFETY = TOOL_IDENTITY + ("description", "trust", "retryPolicy", "timeoutSec", "rateLimitRpm",
                               "safetyStrategy", "errors")
#: L'architecture effective vue par un constructeur : ce qu'il a le DROIT de
#: construire, et l'exigence qui le justifie. La liste `omitted` se déduit du
#: catalogue ; un `dev-*` n'en a pas besoin pour ne pas la construire.
ARCH_REQUIRED = ("reference", "required")

#: Pour chaque agent de construction : `section -> champs gardés`.
#:   "*"     la section entière ;
#:   tuple   les éléments de la liste (ou les clés de l'objet), réduits à ces champs.
#: Une section absente de la spec est retirée et DÉCLARÉE dans `view.omitted`.
#: `dev-agent` est absent : sa vue est par instance (`agent_view`).
#: `dev-app` (poc) et `architect-*` sont absents : le premier construit tout le
#: système, les seconds écrivent ce que l'IR compile — ils ne lisent pas de vue.
VIEW_SPECS: dict[str, dict[str, Any]] = {
    "dev-tools": {"tools": "*", "dataAccess": ("id", "exposedTo", "binding"), "schemas": "*",
                  "architecture": ARCH_REQUIRED},
    "dev-retrieval": {"retrievers": "*", "agents": ("id", "role", "retrievers", "trustPosture"), "schemas": "*",
                      "architecture": ARCH_REQUIRED},
    "dev-data": {"dataAccess": "*", "tools": "*data*", "schemas": "*", "architecture": ARCH_REQUIRED},
    "dev-prompt": {
        "agents": "*", "tools": ("id", "name", "description", "inputSchema", "sideEffectClass", "errors"),
        "retrievers": ("id", "pattern", "citationMode"), "traceability": "*", "schemas": "*",
    },
    "dev-orchestration": {
        "orchestration": "*", "agents": AGENT_WIRING, "tools": TOOL_IDENTITY,
        "memory": "*", "budget": "*", "guardrails": "*", "schemas": "*", "architecture": ARCH_REQUIRED,
    },
    "dev-api": {
        "orchestration": "*", "agents": AGENT_INTERFACE, "budget": "*", "guardrails": "*", "schemas": "*",
        "architecture": ARCH_REQUIRED,
    },
    "dev-backend": {
        # `authEnv` : la composition configure chaque outil par NOMS de variables.
        "orchestration": "*", "agents": AGENT_WIRING, "tools": TOOL_IDENTITY + ("authEnv", "timeoutSec"),
        "retrievers": ("id", "pattern", "binding", "indexHash"), "dataAccess": "*",
        "memory": "*", "guardrails": "*", "budget": "*", "schemas": "*", "architecture": ARCH_REQUIRED,
    },
    "qa-evals": {
        "agents": AGENT_INTERFACE + ("tools", "retrievers", "trustPosture", "refusalPolicy"),
        "tools": TOOL_IDENTITY, "retrievers": "*", "evaluation": "*", "traceability": "*",
        "budget": "*", "guardrails": "*", "schemas": "*",
    },
    "qa-tests": {
        "agents": AGENT_INTERFACE + ("tools", "retrievers", "bounds"), "tools": "*", "retrievers": "*",
        "dataAccess": "*", "orchestration": ("maxHops", "entryNode", "edges", "terminalNodes"), "schemas": "*",
        "architecture": ARCH_REQUIRED,
    },
    "review-spec": {
        "agents": AGENT_IDENTITY + ("tools",), "tools": TOOL_IDENTITY, "evaluation": "*",
        "traceability": "*", "budget": "*", "orchestration": "*", "architecture": "*",
    },
    "review-rag": {
        "retrievers": "*", "agents": ("id", "role", "servesCaps", "retrievers"), "evaluation": "*",
    },
    "review-safety": {
        "agents": AGENT_POSTURE, "tools": TOOL_SAFETY, "dataAccess": "*", "memory": "*",
        "guardrails": "*", "orchestration": "*",
    },
    "review-cost": {
        "agents": AGENT_IDENTITY + ("bounds", "tools"), "orchestration": "*", "budget": "*",
        "evaluation": ("suites",),
    },
    "review-orchestration": {
        "agents": AGENT_IDENTITY + ("tools", "handoffs", "bounds", "onBoundExceeded"),
        "orchestration": "*", "budget": "*",
    },
    "review-adversarial": {
        "agents": AGENT_POSTURE, "tools": TOOL_SAFETY, "dataAccess": "*", "memory": "*",
        "guardrails": "*", "orchestration": "*", "budget": "*",
    },
}

#: L'agent dont la vue est par instance : une par agent du produit.
INSTANCE_VIEW_AGENT = "dev-agent"


def view_agents() -> list[str]:
    """Les agents de construction qui ont une vue (instances comprises)."""
    return sorted([*VIEW_SPECS, INSTANCE_VIEW_AGENT])


def views_dir(ir_path: Path) -> Path:
    return ir_path.parent / VIEWS_DIRNAME


def view_path(ir_path: Path, agent: str, instance: str | None = None) -> Path:
    """`workspace/.sys/.ir/views/{n}-{agent}[.{instance}].ir.json`."""
    number = ir_path.name.split("-", 1)[0]
    suffix = f".{instance}" if instance else ""
    return views_dir(ir_path) / f"{number}-{agent}{suffix}.ir.json"


def _reduce(item: Any, fields: tuple[str, ...]) -> Any:
    if not isinstance(item, dict):
        return item
    return {k: item[k] for k in fields if k in item}


def _project_section(value: Any, rule: Any) -> Any:
    if rule == "*":
        return value
    if rule == "*data*":
        # Les outils d'accès aux données : les contrats `{n}-data-*`.
        return [t for t in value or [] if isinstance(t, dict) and "-data-" in f"-{t.get('id', '')}"]
    if isinstance(value, list):
        return [_reduce(item, rule) for item in value]
    if isinstance(value, dict):
        return {k: value[k] for k in rule if k in value}
    return value


def _envelope(ir: dict[str, Any], agent: str, source_hash: str, kept: dict[str, Any],
              reduced: list[str], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    omitted = sorted(k for k in ir if k not in _HEADER and k not in kept)
    out: dict[str, Any] = {k: ir[k] for k in _HEADER if k in ir}
    out["view"] = {
        "viewVersion": VIEW_VERSION,
        "agent": agent,
        "sourceIrHash": source_hash,
        "omitted": omitted,
        "reduced": sorted(reduced),
        "note": "projection de l'IR complète, jamais éditée : un champ retiré se lit dans l'IR source",
        **(extra or {}),
    }
    out.update(kept)
    return out


def project(ir: dict[str, Any], agent: str, *, source_hash: str) -> dict[str, Any]:
    """La vue de `agent` (hors `dev-agent`, cf. `agent_view`)."""
    spec = VIEW_SPECS[agent]
    kept: dict[str, Any] = {}
    reduced: list[str] = []
    for section, rule in spec.items():
        if section not in ir:
            continue
        kept[section] = _project_section(ir[section], rule)
        if rule != "*":
            reduced.append(section)
    return _envelope(ir, agent, source_hash, kept, reduced)


def agent_view(ir: dict[str, Any], agent_id: str, *, source_hash: str) -> dict[str, Any]:
    """La vue d'UNE instance de `dev-agent` : son agent, ses outils, ses retrievers.

    Les autres agents n'y figurent que par leur identité et leur interface — de
    quoi typer un handoff, rien de ce qu'il faudrait implémenter à leur place.
    """
    agents = [a for a in ir.get("agents") or [] if isinstance(a, dict)]
    mine = next((a for a in agents if a.get("id") == agent_id), None)
    if mine is None:
        raise KeyError(agent_id)
    tool_ids = set(mine.get("tools") or [])
    retriever_ids = set(mine.get("retrievers") or [])
    kept: dict[str, Any] = {
        "agents": [mine],
        "peers": [_reduce(a, AGENT_INTERFACE) for a in agents if a.get("id") != agent_id],
        "tools": [t for t in ir.get("tools") or [] if isinstance(t, dict) and t.get("id") in tool_ids],
    }
    if retriever_ids and ir.get("retrievers"):
        kept["retrievers"] = [r for r in ir["retrievers"] if isinstance(r, dict) and r.get("id") in retriever_ids]
    if ir.get("dataAccess"):
        exposed = [d for d in ir["dataAccess"] if isinstance(d, dict) and agent_id in (d.get("exposedTo") or [])]
        if exposed:
            kept["dataAccess"] = exposed
    for section in ("memory", "guardrails", "budget", "schemas"):
        if section in ir:
            kept[section] = ir[section]
    if isinstance(ir.get("architecture"), dict):
        kept["architecture"] = _project_section(ir["architecture"], ARCH_REQUIRED)
    view = _envelope(ir, INSTANCE_VIEW_AGENT, source_hash, kept,
                     reduced=["agents", "tools"] + (["retrievers"] if "retrievers" in kept else []),
                     extra={"instance": agent_id})
    # `peers` n'est pas une section de l'IR : la vue dit ce qu'elle porte.
    view["view"]["peers"] = "autres agents, identité et interface seulement"
    return view


def source_hash_of(ir_path: Path) -> str:
    return hashing.sha256_file(ir_path)


def all_views(ir: dict[str, Any], ir_path: Path, *, source_hash: str) -> dict[Path, dict[str, Any]]:
    """Chemin -> vue, pour tous les agents et toutes les instances de `dev-agent`."""
    out: dict[Path, dict[str, Any]] = {}
    for agent in VIEW_SPECS:
        out[view_path(ir_path, agent)] = project(ir, agent, source_hash=source_hash)
    for a in ir.get("agents") or []:
        if isinstance(a, dict) and a.get("id"):
            out[view_path(ir_path, INSTANCE_VIEW_AGENT, a["id"])] = agent_view(ir, a["id"], source_hash=source_hash)
    return out
