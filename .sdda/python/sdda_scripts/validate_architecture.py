#!/usr/bin/env python3
"""ARCHITECTURE GATE (G2) — enforcer de l'invariant `architecture-declared-by-architect`.

Ce que cette gate défend : *l'architecture est une décision de l'architecte,
explicite et vérifiable — jamais une décision émergente prise par un LLM pendant
la génération* (PHILOSOPHY P7).

Le mécanisme tient en une phrase : **chaque choix de `STACK.md` impose des champs
de spécification**, et ce script vérifie qu'ils sont là et remplis avant qu'une
seule ligne de code soit générée.

    STACK.md dit          `Active Orchestration Pattern: supervisor`
    -> la spec DOIT dire  qui est l'orchestrateur, quels sont les subagents,
                          le rôle et les responsabilités de chacun, ses outils,
                          son modèle, les relations et la borne de boucle.

Un champ manquant est `[ARCH_SPEC_INCOMPLETE]`, bloquant. Ce n'est pas du zèle :
un champ absent ne reste pas absent. Il est comblé au moment de la génération par
un modèle qui inventera un rôle plausible, et personne n'aura décidé ni relu
l'architecture qui en sort. Une architecture partiellement spécifiée est une
architecture partiellement émergente.

Ce que cette gate ne fait PAS : juger l'architecture. Sept subagents là où deux
suffiraient est une erreur fréquente et coûteuse — mais c'est l'erreur de
l'architecte, et il a le droit de la commettre en connaissance de cause. Le
framework la **signale** (`[TOPOLOGY_SIMPLICITY_ADVISORY]`, dans
`validate_topology.py`) et n'oppose aucun veto.

La matrice des exigences est déclarative : `.sdda/registry/architecture-requirements.yml`.
Ajouter un pattern d'orchestration, c'est ajouter un bloc — pas modifier ce script.

Usage :
    python .sdda/sdda.py validate-architecture --mission 1
    python .sdda/sdda.py validate-architecture --mission 1 --json
    python .sdda/sdda.py validate-architecture --explain          # ce que la stack active exige
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, paths, yaml_mini  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import active_stacks, read_stack_section_kv  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402
from sdda_scripts.validate_mission import mission_artifact  # noqa: E402

REGISTRY = "registry/architecture-requirements.yml"

#: Axes lus dans le registre, dans l'ordre où ils sont rapportés.
AXES = ("orchestration", "rag", "dataaccess", "tools")

#: Clés d'exigence que ce script sait appliquer. Une clé du registre absente
#: d'ici est `[ARCH_REQUIREMENT_UNKNOWN]` : une faute de frappe qui
#: désactiverait une exigence en silence est pire que pas d'exigence du tout.
KNOWN_REQUIREMENTS = frozenset({
    "orchestrator", "subagents_min", "subagents_max", "relations", "conditions",
    "fallback", "tools_per_agent", "model_per_agent", "merge_strategy",
    "loop_bound", "stack_keys", "stack_section",
})

#: Champs obligatoires de l'orchestrateur (`### 2.1`), en clés normalisées.
ORCHESTRATOR_FIELDS = ("id", "role", "responsabilites")

#: Colonnes attendues de la table des subagents (`### 2.2`).
SUBAGENT_COLUMNS = {
    "id": ("id",),
    "role": ("rôle", "role"),
    "responsabilites": ("responsabilités", "responsabilites"),
    "tools": ("outils / skills", "outils", "outils/skills", "skills"),
    "tier": ("tier",),
    "model": ("modèle", "modele", "model"),
}

_TIERS = ("fast", "balanced", "deep")
_FALLBACK_RE = re.compile(r"repli|fallback|aucune classe|par d[ée]faut|clarif", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Lecture du registre et de la spécification
# ---------------------------------------------------------------------------
def registry_path(root: Path) -> Path:
    local = root / ".sdda" / REGISTRY
    return local if local.is_file() else paths.FRAMEWORK_SDDA_DIR / REGISTRY


def load_registry(root: Path, report: Report) -> dict[str, Any]:
    path = registry_path(root)
    if not path.is_file():
        report.error("ARCH_REGISTRY_MISSING", f"registre d'exigences introuvable ({path})",
                     fix="restaurer .sdda/registry/architecture-requirements.yml")
        return {}
    try:
        return yaml_mini.parse_mapping(markdown_io.read_text(path))
    except (yaml_mini.YamlMiniError, OSError) as exc:
        report.error("ARCH_REGISTRY_MISSING", f"registre illisible : {exc}",
                     fix="corriger la syntaxe du registre")
        return {}


def _norm(text: str) -> str:
    out = markdown_io.strip_code(str(text or "")).strip().lower()
    for src, dst in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("ô", "o"), ("î", "i"), ("ç", "c")):
        out = out.replace(src, dst)
    return out


def _subsection(body: str, title: str) -> str:
    """Le corps d'un `### {title}` à l'intérieur d'une section `##`."""
    pattern = re.compile(rf"^###\s+(?:\d+(?:\.\d+)*\.?\s+)?{re.escape(title)}\s*$", re.MULTILINE)
    m = pattern.search(body)
    if not m:
        return ""
    rest = body[m.end():]
    nxt = re.search(r"^###?\s", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def _cell(row: dict[str, str], field: str) -> str:
    for name in SUBAGENT_COLUMNS[field]:
        for key, value in row.items():
            if _norm(key) == _norm(name):
                return markdown_io.strip_code(value).strip()
    return ""


def _condition_of(row: dict[str, str]) -> str:
    """La condition d'une relation — 3e colonne du Markdown, clé `Condition` du manifeste."""
    for key, value in row.items():
        if _norm(key) == "condition":
            return markdown_io.strip_code(value)
    values = list(row.values())
    return markdown_io.strip_code(values[2]) if len(values) > 2 else ""


def _filled(value: str) -> bool:
    return bool(value.strip()) and not markdown_io.is_placeholder(value)


def _joined(value: Any) -> str:
    """Une valeur de manifeste -> texte. `[]` devient `aucun`, explicitement.

    La distinction compte : une liste d'outils VIDE est une décision de
    l'architecte (« cet agent n'a aucun outil »), une clé ABSENTE est un oubli.
    Les confondre laisserait le moindre privilège à la discrétion du générateur.
    """
    if isinstance(value, list):
        return ", ".join(str(v).strip() for v in value if str(v).strip()) or "aucun"
    return str(value if value is not None else "").strip()


class Roster:
    """Le roster déclaré par l'architecte — manifeste, ou section Markdown.

    Deux sources possibles, **jamais les deux à la fois** :

      1. `workspace/feats/{n}-roster.md` — un Markdown dont le premier
         bloc ```yaml est la déclaration ; écrit par l'HUMAIN, lu par
         `architect-topology`. C'est la forme recommandée : la décision
         d'architecture existe alors AVANT l'artefact de topologie, et se relit
         sans ouvrir un document de 200 lignes.
      2. `## 2. Roster déclaré` de `topology/{n}-topology.md` — le repli, pour
         un projet à un seul agent où le fichier séparé est une cérémonie.

    Deux sources divergentes, c'est celle que personne ne relit qui gagne :
    la cohabitation est `[ARCH_ROSTER_DUPLICATE_SOURCE]`.
    """

    def __init__(self) -> None:
        self.present = False
        self.source = ""
        self.orchestrator: dict[str, str] = {}
        self.subagents: list[dict[str, str]] = []
        self.relations: list[dict[str, str]] = []
        self.loops: list[dict[str, str]] = []
        self.merge = ""

    # -- depuis le manifeste -------------------------------------------------
    @classmethod
    def from_manifest(cls, data: dict[str, Any], source: str) -> "Roster":
        self = cls()
        self.present = True
        self.source = source
        orch = data.get("orchestrator") if isinstance(data.get("orchestrator"), dict) else {}
        self.orchestrator = {
            "id": _joined(orch.get("id")),
            "role": _joined(orch.get("role")),
            "responsabilites": _joined(orch.get("responsibilities")),
            "tier": _joined(orch.get("tier")),
            "modele": _joined(orch.get("model")),
            "outils": _joined(orch.get("tools")) if "tools" in orch else "",
        }
        for entry in data.get("subagents") or []:
            if not isinstance(entry, dict) or not _joined(entry.get("id")):
                continue
            # `skills` ne vaut PAS `tools` : le premier dit ce que l'agent sait
            # faire, le second ce qu'il a le droit d'appeler. Une clé `tools`
            # absente reste absente, même si des skills sont déclarés — sinon le
            # moindre privilège redevient implicite.
            tools = ([_joined(entry.get("tools"))] if "tools" in entry else [])
            if tools and "skills" in entry:
                tools.append(_joined(entry.get("skills")))
            self.subagents.append({
                "id": _joined(entry.get("id")),
                "role": _joined(entry.get("role")),
                "responsabilites": _joined(entry.get("responsibilities")),
                "tools": " / ".join(t for t in tools if t),
                "tier": _joined(entry.get("tier")),
                "model": _joined(entry.get("model")),
            })
        for entry in data.get("relations") or []:
            if isinstance(entry, dict):
                self.relations.append({
                    "De": _joined(entry.get("from")),
                    "Vers": _joined(entry.get("to")),
                    "Condition": _joined(entry.get("condition")),
                    "Compte comme hop": _joined(entry.get("counts_as_hop")),
                })
        for entry in data.get("loop_bounds") or []:
            if isinstance(entry, dict):
                self.loops.append({
                    "Boucle": _joined(entry.get("loop")),
                    "Borne": _joined(entry.get("bound")),
                    "Comportement": _joined(entry.get("on_exceeded")),
                })
        self.merge = _joined(data.get("merge_strategy"))
        return self

    # -- depuis la section Markdown -----------------------------------------
    @classmethod
    def from_markdown(cls, text: str, source: str) -> "Roster":
        self = cls()
        body = markdown_io.section_body(text, "Roster déclaré") or ""
        self.present = bool(body.strip())
        self.source = source
        self.orchestrator = {
            _norm(k): v for k, v in markdown_io.parse_kv_list(_subsection(body, "Orchestrateur")).items()
        }
        self.subagents = [
            {f: _cell(r, f) for f in SUBAGENT_COLUMNS}
            for r in markdown_io.parse_table(_subsection(body, "Subagents")) if _filled(_cell(r, "id"))
        ]
        self.relations = [r for r in markdown_io.parse_table(_subsection(body, "Relations entre agents"))
                          if _filled(markdown_io.strip_code(next(iter(r.values()), "")))]
        self.loops = [r for r in markdown_io.parse_table(_subsection(body, "Bornes de boucle"))
                      if _filled(markdown_io.strip_code(next(iter(r.values()), "")))]
        self.merge = _subsection(body, "Stratégie de fusion").strip()
        return self

    # -- lecture -------------------------------------------------------------
    @property
    def orchestrator_id(self) -> str:
        return markdown_io.strip_code(self.orchestrator.get("id", "")).strip()

    def agents(self) -> list[tuple[str, dict[str, str]]]:
        out: list[tuple[str, dict[str, str]]] = []
        if _filled(self.orchestrator_id):
            out.append((self.orchestrator_id, {
                "role": self.orchestrator.get("role", ""),
                "responsabilites": self.orchestrator.get("responsabilites", ""),
                "tools": self.orchestrator.get("outils", ""),
                "tier": self.orchestrator.get("tier", ""),
                "model": self.orchestrator.get("modele", ""),
            }))
        out.extend((row["id"], row) for row in self.subagents)
        return out


def load_roster(root: Path, mission: int | str | None, topology: Path | None,
                report: Report) -> Roster:
    """Résout la source du roster. Manifeste prioritaire, Markdown en repli."""
    manifest = _roster_manifest(root, mission, report)
    markdown = Roster.from_markdown(markdown_io.read_text(topology), paths.rel(root, topology)) \
        if topology is not None else Roster()

    if manifest is not None and markdown.present:
        report.error(
            "ARCH_ROSTER_DUPLICATE_SOURCE",
            f"le roster est déclaré deux fois — `{manifest.source}` et `{markdown.source} ## 2. Roster déclaré`",
            fix="une seule source de vérité : garder `{n}-roster.md` et retirer la section de la topologie. "
                "Deux déclarations divergentes, et c'est celle que personne ne relit qui gouverne le code",
            location=manifest.source,
        )
    return manifest if manifest is not None else markdown


def read_roster_yaml(path: Path) -> dict[str, Any]:
    """La déclaration d'un `{n}-roster.md` : son premier bloc ```yaml, parsé.

    Un `.yml` nu (forme d'avant la v3 du workspace) reste lisible ; la forme
    écrite est toujours le Markdown. Partagé avec `roster.py`, qui l'importe.
    """
    text = markdown_io.read_text(path)
    if path.suffix.lower() in (".md", ".markdown"):
        block = markdown_io.first_yaml_block(text)
        if block is None:
            raise yaml_mini.YamlMiniError("aucun bloc ```yaml dans le roster Markdown")
        return yaml_mini.parse_mapping(block)
    return yaml_mini.parse_mapping(text)


def _roster_manifest(root: Path, mission: int | str | None, report: Report) -> Roster | None:
    """`feats/{n}-roster.md` s'il existe. Une convention, aucune clé de STACK.md.

    Sans numéro de mission, un roster unique dans le répertoire est pris ; deux
    ou plus, aucun — deviner lequel serait décider à la place de l'architecte.
    """
    if mission is not None:
        candidates = [paths.roster_path(root, mission)]
    else:
        # Le roster vit dans `feats/` depuis le workspace v6 ; cette branche
        # le cherchait encore sous `topology/`, et ne le trouvait donc jamais.
        candidates = sorted(paths.feats_dir(root).glob("*-roster.md"))
        if len(candidates) > 1:
            candidates = []

    for path in candidates:
        if not path.is_file():
            return None
        try:
            data = read_roster_yaml(path)
        except (yaml_mini.YamlMiniError, OSError) as exc:
            report.error("ARCH_ROSTER_MANIFEST_MALFORMED", f"roster `{paths.rel(root, path)}` illisible : {exc}",
                         fix="corriger le bloc ```yaml du roster (`.sdda/templates/roster.template.md`)",
                         location=paths.rel(root, path))
            return None
        return Roster.from_manifest(data, paths.rel(root, path))
    return None


# ---------------------------------------------------------------------------
# Application des exigences
# ---------------------------------------------------------------------------
def requirements_for(registry: dict[str, Any], axis: str, choice: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """`(exigences, bloc)` pour un choix — avec repli sur `_default`."""
    block = (registry.get(axis) or {}).get("choices") or {}
    entry = block.get(choice)
    if entry is None:
        entry = block.get("_default")
    if not isinstance(entry, dict):
        return {}, {}
    return (entry.get("requires") or {}), entry


def check_requirements(root: Path, axis: str, choice: str, requires: dict[str, Any], entry: dict[str, Any],
                       roster: Roster, report: Report, loc: str) -> None:
    unknown = sorted(set(requires) - KNOWN_REQUIREMENTS)
    if unknown:
        report.error(
            "ARCH_REQUIREMENT_UNKNOWN",
            f"`{axis}/{choice}` : clé(s) d'exigence inconnue(s) {unknown}",
            fix=f"grammaire close — clés admises : {', '.join(sorted(KNOWN_REQUIREMENTS))}. "
                "Une clé mal orthographiée désactive l'exigence sans rien signaler",
            location=".sdda/registry/architecture-requirements.yml",
        )

    why = str(entry.get("why") or "").strip()
    hint = f" Pourquoi : {' '.join(why.split())}" if why else ""

    # -- l'orchestrateur -----------------------------------------------------
    if requires.get("orchestrator"):
        missing = [f for f in ORCHESTRATOR_FIELDS if not _filled(roster.orchestrator.get(f, ""))]
        if missing:
            report.error(
                "ARCH_ORCHESTRATOR_INCOMPLETE",
                f"`{choice}` : `### Orchestrateur` incomplet — {missing} absent(s) ou non renseigné(s)",
                fix="remplir `- **id** :`, `- **rôle** :` et `- **responsabilités** :` dans "
                    "`## 2. Roster déclaré`. C'est l'architecte qui nomme l'orchestrateur, pas le générateur."
                    + hint,
                location=loc,
            )

    # -- le nombre de subagents ---------------------------------------------
    count = len(roster.subagents)
    lo = requires.get("subagents_min")
    if isinstance(lo, int) and count < lo:
        report.error(
            "ARCH_ROSTER_INCOMPLETE",
            f"`{choice}` exige au moins {lo} subagent(s) déclaré(s), {count} trouvé(s)",
            fix="compléter la table `### Subagents` — une ligne par agent, avec son rôle et ses "
                "responsabilités. Le framework ne décide pas combien d'agents il faut." + hint,
            location=loc,
        )
    hi = requires.get("subagents_max")
    if isinstance(hi, int) and count > hi:
        report.error(
            "ARCH_ROSTER_INCOHERENT",
            f"`{choice}` n'admet pas de subagent ({count} déclaré(s))",
            fix=f"le pattern `{choice}` est mono-agent : retirer les subagents, ou changer "
                "`## Active Orchestration Pattern` dans STACK.md",
            location=loc,
        )

    # -- les relations -------------------------------------------------------
    if requires.get("relations") and not roster.relations:
        report.error(
            "ARCH_RELATIONS_MISSING",
            f"`{choice}` : `### Relations entre agents` vide",
            fix="déclarer qui appelle qui. Une relation non écrite est une relation que le "
                "générateur inventera." + hint,
            location=loc,
        )
    if requires.get("conditions"):
        for row in roster.relations:
            values = list(row.values())
            if not _filled(_condition_of(row)):
                src = markdown_io.strip_code(values[0]) if values else "?"
                dst = markdown_io.strip_code(values[1]) if len(values) > 1 else "?"
                report.error(
                    "ARCH_CONDITION_MISSING",
                    f"`{choice}` : relation `{src}` -> `{dst}` sans condition explicite",
                    fix="écrire la condition de déclenchement. « le contexte suit » n'est pas une condition",
                    location=loc,
                )

    if requires.get("fallback"):
        has_fallback = any(_FALLBACK_RE.search(_condition_of(row)) for row in roster.relations)
        if not has_fallback:
            report.error(
                "ARCH_FALLBACK_MISSING",
                f"`{choice}` : aucun chemin de repli déclaré dans `### Relations entre agents`",
                fix="déclarer la branche « aucune classe ne correspond ». C'est celle que personne "
                    "n'écrit et celle qui arrive en production." + hint,
                location=loc,
            )

    # -- ce que chaque agent doit porter ------------------------------------
    if requires.get("tools_per_agent"):
        for agent_id, fields in roster.agents():
            if not _filled(fields.get("tools", "")):
                report.error(
                    "ARCH_AGENT_TOOLS_UNDECLARED",
                    f"agent `{agent_id}` : aucun outil/skill déclaré",
                    fix="lister ses outils, ou écrire `aucun` explicitement. Un champ vide laisse le "
                        "moindre privilège à la discrétion du générateur",
                    location=loc,
                )
    if requires.get("model_per_agent"):
        for agent_id, fields in roster.agents():
            tier = _norm(fields.get("tier", ""))
            model = fields.get("model", "")
            if tier not in _TIERS and not _filled(model):
                report.error(
                    "ARCH_AGENT_MODEL_UNDECLARED",
                    f"agent `{agent_id}` : ni `tier` ({'|'.join(_TIERS)}) ni `modèle` déclaré",
                    fix="déclarer le tier, ou un modèle nominatif. Sans cela le générateur applique "
                        "un défaut, et l'économie que le multi-modèle devait produire disparaît",
                    location=loc,
                )

    # -- bornes et fusion ----------------------------------------------------
    if requires.get("loop_bound") and not roster.loops:
        report.error(
            "ARCH_LOOP_BOUND_MISSING",
            f"`{choice}` autorise un cycle mais `### Bornes de boucle` est vide",
            fix="déclarer chaque boucle, sa borne, et le comportement à l'atteinte. Une boucle non "
                "bornée est un bug, pas une propriété émergente (P12)",
            location=loc,
        )
    if requires.get("merge_strategy") and (not _filled(roster.merge) or "|" in roster.merge):
        report.error(
            "ARCH_MERGE_STRATEGY_MISSING",
            f"`{choice}` : `### Stratégie de fusion` non renseignée",
            fix="choisir : vote majoritaire, priorité déclarée, synthèse par un agent dédié, ou "
                "échec si divergence." + hint,
            location=loc,
        )

    # -- les clés de STACK.md ------------------------------------------------
    section = requires.get("stack_section")
    keys = requires.get("stack_keys") or []
    if section and keys:
        declared = read_stack_section_kv(root, str(section))
        absent = [k for k in keys if not _filled(str(declared.get(k, "") or ""))]
        if absent:
            report.error(
                "ARCH_SPEC_INCOMPLETE",
                f"`{axis}/{choice}` : `## {section}` ne déclare pas {absent}",
                fix=f"renseigner ces clés dans STACK.md. Le choix `{choice}` les exige — "
                    "les laisser vides revient à déléguer la décision au générateur." + hint,
                location="workspace/stack/STACK.md",
            )


def check_mcp_servers(root: Path, entry: dict[str, Any], report: Report) -> None:
    """Un serveur MCP propose, le contrat dispose — encore faut-il l'avoir écrit."""
    if not entry.get("mcp_servers_complete"):
        return
    servers = read_stack_section_kv(root, "Active Tools & Integrations").get("MCPServers") or []
    servers = [s for s in servers if isinstance(s, dict)]
    if not servers:
        report.error(
            "ARCH_MCP_UNDECLARED",
            "`tools/mcp` est actif mais `MCPServers[]` est vide",
            fix="déclarer chaque serveur : `name`, `transport`, `trust`, `tools_allowlist`",
            location="workspace/stack/STACK.md",
        )
        return
    for server in servers:
        name = str(server.get("name") or "?")
        for key in ("transport", "trust"):
            if not _filled(str(server.get(key, "") or "")):
                report.error("ARCH_MCP_UNDECLARED", f"serveur MCP `{name}` : `{key}` absent",
                             fix=f"déclarer `{key}` — `trust` gouverne le traitement de ses sorties (P8)",
                             location="workspace/stack/STACK.md")
        allowlist = server.get("tools_allowlist")
        if not isinstance(allowlist, list) or not allowlist:
            report.error(
                "ARCH_MCP_UNDECLARED",
                f"serveur MCP `{name}` : `tools_allowlist` absente ou vide",
                fix="nommer les outils câblés. Sans allowlist, c'est le serveur qui décide de la "
                    "surface d'attaque de l'application",
                location="workspace/stack/STACK.md",
            )


def check_multi_model(root: Path, registry: dict[str, Any], roster: Roster, report: Report, loc: str) -> None:
    """Deux tiers vers deux modèles : « qui utilise quoi » devient une décision."""
    block = registry.get("runtime_models") or {}
    always = (block.get("always") or {}).get("requires") or {}
    section = str(always.get("stack_section") or "Runtime Models")
    declared = read_stack_section_kv(root, section)
    for key in always.get("stack_keys") or []:
        if not _filled(str(declared.get(key, "") or "")):
            report.error("ARCH_SPEC_INCOMPLETE", f"`## {section}` ne déclare pas `{key}`",
                         fix="renseigner la clé dans STACK.md", location="workspace/stack/STACK.md")

    tier_map = declared.get("RuntimeTierMap")
    models = {str(v).strip() for v in tier_map.values() if str(v).strip()} if isinstance(tier_map, dict) else set()
    if len(models) < 2:
        return
    requires = (block.get("multi_model") or {}).get("requires") or {}
    if not requires.get("model_per_agent"):
        return
    for agent_id, fields in roster.agents():
        if _norm(fields.get("tier", "")) not in _TIERS and not _filled(fields.get("model", "")):
            report.error(
                "ARCH_AGENT_MODEL_UNDECLARED",
                f"{len(models)} modèles distincts déclarés, mais l'agent `{agent_id}` n'en nomme aucun",
                fix="préciser quel modèle (ou quel tier) sert cet agent, et pour quel objectif",
                location=loc,
            )


# ---------------------------------------------------------------------------
# Entrée
# ---------------------------------------------------------------------------
def run(root: Path, mission: int | str | None = None, explain: bool = False) -> Report:
    report = Report(name="ARCHITECTURE", target=str(root))

    if not paths.stack_md_path(root).is_file():
        report.error("STACK_MISSING", "STACK.md introuvable", fix="lancer `python bootstrap.py`")
        return report

    registry = load_registry(root, report)
    if not registry:
        return report

    active: dict[str, str] = {}
    for axis in AXES:
        heading = str((registry.get(axis) or {}).get("section") or "")
        names = active_stacks(root, heading) if heading else []
        if axis == "tools":
            active[axis] = ",".join(names)
        elif len(names) == 1:
            active[axis] = names[0]
        elif names:
            report.error("STACK_MALFORMED", f"`## {heading}` active plusieurs stacks : {names}",
                         fix="exactement une", location="workspace/stack/STACK.md")
    report.data["active"] = active

    if explain:
        report.data["requires"] = {
            f"{axis}/{choice}": requirements_for(registry, axis, choice)[0]
            for axis, choice in active.items() if choice
        }
        return report

    topology = _topology_path(root, mission)
    roster = load_roster(root, mission, topology, report)
    loc = roster.source or (paths.rel(root, topology) if topology else "workspace/stack/STACK.md")
    if not roster.present:
        report.error(
            "ARCH_ROSTER_MISSING",
            "aucun roster déclaré — ni `feats/{n}-roster.md`, ni `## 2. Roster déclaré` dans la topologie",
            fix="écrire `workspace/feats/{n}-roster.md` — `python .sdda/sdda.py roster scaffold --mission {n}` "
                "le pré-remplit (recommandé), ou remplir la "
                "section `## 2. Roster déclaré` du template de topologie. C'est l'architecte qui déclare "
                "les agents, leurs rôles et leurs outils — le framework les vérifie, il ne les invente "
                "pas (P7)",
            location=loc,
        )
        return report

    for axis, choice in active.items():
        if not choice:
            continue
        for one in (choice.split(",") if axis == "tools" else [choice]):
            if not one:
                continue
            requires, entry = requirements_for(registry, axis, one)
            check_requirements(root, axis, one, requires, entry, roster, report, loc)
            if entry.get("mcp_servers_complete"):
                check_mcp_servers(root, entry, report)
            if entry.get("adr_required"):
                report.warn(
                    "ARCH_ADR_REQUIRED",
                    f"`{axis}/{one}` exige un ADR nominatif en plus de la déclaration",
                    fix="écrire l'ADR dans workspace/pipeline/decisions/ et le citer dans `## 9. Décisions à ADR`",
                    location=loc,
                )

    check_multi_model(root, registry, roster, report, loc)

    report.data.update({
        "orchestrator": roster.orchestrator_id,
        "rosterSource": roster.source,
        "subagents": [r["id"] for r in roster.subagents],
        "relations": len(roster.relations),
        "loopBounds": len(roster.loops),
    })
    return report


def _topology_path(root: Path, mission: int | str | None) -> Path | None:
    directory = paths.topology_dir(root)
    if mission is not None:
        found = sorted(directory.glob(f"{mission}-topology.md"))
        return found[0] if found else None
    found = sorted(directory.glob("*-topology.md"))
    return found[0] if len(found) == 1 else None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="ARCHITECTURE GATE — la spécification est-elle complète pour l'architecture choisie ? (G2)")
    p.add_argument("--mission", default=None, help="numéro de mission ; défaut : l'unique topologie présente")
    p.add_argument("--explain", action="store_true", help="afficher ce que la stack active exige, sans valider")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = run(root, mission=args.mission, explain=args.explain)

    if not args.no_report and not args.explain:
        try:
            write_gate_report(root, "G2", mission_artifact(root, args.mission), report,
                              pinned=source_pins(root, args.mission, report), part="architecture")
        except OSError:
            pass
    return finish(report, args)


def source_pins(root: Path, mission: int | str | None, report: Report) -> dict[str, str]:
    """STACK.md, la topologie et le roster LU : ce sur quoi la déclaration a été jugée.

    Épinglée à vide, la part restait verte après une édition du roster ou un
    changement de pattern dans STACK.md — et `/sdda-topology --recompile-only`
    ne la rejouait pas. Un vert qui ne peut pas se périmer ne prouve rien sur
    l'état présent.
    """
    pins: dict[str, str] = {}
    stack = paths.stack_md_path(root)
    if stack.is_file():
        pins["stack"] = hashing.sha256_file(stack)
    topology = _topology_path(root, mission)
    if topology is not None and mission is not None:
        pins["topology"] = hashing.sha256_spec_file(topology)
    source = str(report.data.get("rosterSource") or "")
    if source.endswith("-roster.md") and (root / source).is_file():
        pins[source] = hashing.sha256_file(root / source)
    return pins


if __name__ == "__main__":
    sys.exit(main())
