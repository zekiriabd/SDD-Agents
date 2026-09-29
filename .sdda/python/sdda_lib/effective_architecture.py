"""Architecture de référence ≠ architecture générée — la sélection, 0 token.

Ce que ce module défend : *la complexité d'une application générée est une
conséquence de la complexité de ses exigences, pas de la richesse du framework.*

Le framework porte une architecture de RÉFÉRENCE : agents, outils, routeur,
pipeline séquentiel, délégation, mémoire longue, RAG, accès base, sources
déclarées en six formats, comptage, filtre d'identité, fraîcheur, guardrails…
Jusqu'ici, les générateurs la copiaient entière. La première application C#
(1 agent, 2 outils, 1 fichier JSON) a reçu un `Routers.cs`, un
`OrchestrationState.cs`, un `memory/ConversationWindow.cs`, un lecteur CSV/TSV/
JSONL et un `CountRecords` — aucun n'étant exigé par une ligne de la spec.

Ici, chaque capacité du catalogue reçoit une DÉCISION : requise ou non, et, si
elle l'est, **l'exigence qui la justifie** (`justifiedBy`). La règle est unique :

    une capacité sans exigence qui la rend nécessaire n'est pas générée.

« C'est une bonne pratique », « ça servira plus tard », « le framework la
prévoit » ne sont pas des exigences. Une convention OBLIGATOIRE du framework
(bornes en code, traces que l'évaluation mesure) en est une, et elle est nommée
comme telle.

Les exigences sont lues, par ordre de précision :
  1. l'IR (`{n}-system.ir.json`) — ce que la topologie et les contrats ont décidé ;
  2. le roster (`feats/{n}-roster.md`) — les outils que l'architecte accorde ;
  3. la MISSION, `## Architecture Needs` (`sdda_lib/spec_needs.py`) — conversation
     multi-tour, mémoire, sortie structurée, rédaction des PII, entrées non maîtrisées ;
  4. STACK.md — ce que le Tech Lead AUTORISE (pattern, politique de mémoire,
     catalogue de guardrails, sources, surface). Une activation de STACK.md n'est
     PAS une exigence quand la MISSION déclare ses besoins : c'est l'intersection
     qui est générée. Sans besoins déclarés (MISSION antérieure), STACK.md décide
     encore seul — le repli historique.

Les consommateurs :
  - `ir_compiler` écrit la décision dans l'IR (`architecture`) : les agents
    `dev-*` la lisent au lieu de déduire l'architecture des fiches de stack ;
  - `gen_app_skeleton` et `gen_source_tools` rendent leurs gabarits avec
    `features()` (`sdda_lib.feature_template`) ;
  - `validate_effective_architecture` confronte le livrable à la décision et
    refuse un composant sans exigence (`[ARCH_COMPONENT_UNJUSTIFIED]`).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from sdda_lib import markdown_io, paths

#: Version du catalogue : l'IR la porte, un changement de catalogue la change.
REFERENCE_VERSION = "1"


@dataclass(frozen=True)
class Capability:
    """Une capacité de l'architecture de référence."""

    id: str
    layer: str
    summary: str


#: Le catalogue — ce que le framework SAIT générer. L'ordre est celui des couches (P5).
REFERENCE: tuple[Capability, ...] = (
    Capability("agent.loop", "agents", "boucle d'agent bornée, prompt chargé par hash"),
    Capability("bounds", "shared", "bornes en code (maxIterations, maxToolCalls, timeout, budget)"),
    Capability("tools.registry", "tools", "registre d'outils, contexte d'appel, résultat typé"),
    Capability("tools.custom", "tools", "outils hors sources déclarées, écrits depuis leur contrat"),
    Capability("data.sources", "data", "accès aux sources déclarées (enveloppe, registre résolu, schéma figé)"),
    Capability("data.format.json", "data", "lecteur JSON (tableau ou objet)"),
    Capability("data.format.jsonl", "data", "lecteur JSON Lines (index par offset)"),
    Capability("data.format.delimited", "data", "lecteur CSV / TSV"),
    Capability("data.format.xlsx", "data", "lecteur XLSX"),
    Capability("data.format.parquet", "data", "lecteur Parquet"),
    Capability("data.tool.lookup", "data", "lecture par clé"),
    Capability("data.tool.search", "data", "recherche filtrée, plafonnée, triée"),
    Capability("data.tool.count", "data", "comptage filtré côté code"),
    Capability("data.ranges", "data", "filtres de plage min/max"),
    Capability("data.identity-filter", "data", "filtre d'identité imposé à la source (required_filter)"),
    Capability("data.staleness", "data", "fraîcheur déclarée (`stale`, `as_of` comparé à un seuil)"),
    Capability("data.untrusted-fields", "data", "champs de texte libre enveloppés (P8)"),
    Capability("data.database", "data", "accès base (vues par agent, repositories, enveloppe SQL)"),
    Capability("retrieval", "retrieval", "RAG : ingestion, chunking, index, retriever"),
    Capability("retrieval.rerank", "retrieval", "reranking des candidats"),
    Capability("orchestration.router", "orchestration", "routeur : classification puis spécialiste"),
    Capability("orchestration.sequential", "orchestration", "pipeline séquentiel d'étapes validées"),
    Capability("orchestration.graph", "orchestration", "graphe multi-agents et son manifeste"),
    Capability("orchestration.shared-state", "orchestration", "état partagé entre agents"),
    Capability("orchestration.delegation", "orchestration", "délégation à des sous-agents (handoffs)"),
    Capability("orchestration.human-in-the-loop", "orchestration", "validation humaine en cours de run"),
    Capability("conversation.session", "serving", "contexte conversationnel multi-tour, en mémoire du processus"),
    Capability("memory.layer", "memory", "couche mémoire `memory/` (au-delà de l'historique de session)"),
    Capability("memory.summarization", "memory", "résumé glissant de l'historique"),
    Capability("memory.long-term", "memory", "mémoire persistante entre les runs"),
    Capability("guardrail.injection-detection", "guardrails", "détection d'injection sur l'entrée"),
    Capability("guardrail.pii-redaction", "guardrails", "rédaction des PII"),
    Capability("guardrail.schema-validation", "guardrails", "validation de la sortie contre son schéma"),
    Capability("tracing", "observability", "traces OTel GenAI (coût, latence, trajectoire)"),
    Capability("serving.cli", "serving", "surface console"),
    Capability("serving.http", "serving", "surface HTTP / SSE"),
    Capability("serving.mcp", "serving", "surface serveur MCP"),
    Capability("serving.batch", "serving", "surface batch"),
)

CATALOG: dict[str, Capability] = {c.id: c for c in REFERENCE}

#: `format:` déclaré d'une source -> capacité de lecture.
FORMAT_CAPABILITY: dict[str, str] = {
    "array": "data.format.json", "object": "data.format.json", "json": "data.format.json",
    "jsonl": "data.format.jsonl", "csv": "data.format.delimited", "tsv": "data.format.delimited",
    "xlsx": "data.format.xlsx", "parquet": "data.format.parquet",
}

#: Politiques de mémoire courte qui demandent un historique de session.
_SESSION_POLICIES = frozenset({"sliding-window", "summarize-over", "hybrid"})
_SUMMARY_POLICIES = frozenset({"summarize-over", "hybrid"})
_SOURCE_KINDS = ("lookup", "search", "count")


@dataclass
class EffectiveArchitecture:
    """La décision : capacité -> justifications (vide = non requise)."""

    justified: dict[str, list[str]] = field(default_factory=dict)
    #: D'où viennent les exigences : `ir`, `roster`, `stack`.
    sources: list[str] = field(default_factory=list)

    def require(self, capability: str, why: str) -> None:
        if capability not in CATALOG:
            raise KeyError(f"capacité inconnue du catalogue : {capability}")
        reasons = self.justified.setdefault(capability, [])
        if why not in reasons:
            reasons.append(why)

    def requires(self, capability: str) -> bool:
        return bool(self.justified.get(capability))

    def features(self) -> frozenset[str]:
        """Les capacités requises — l'ensemble que les gabarits reçoivent."""
        return frozenset(c for c, why in self.justified.items() if why)

    def omitted(self) -> list[str]:
        return [c.id for c in REFERENCE if not self.requires(c.id)]

    def to_ir(self) -> dict[str, Any]:
        """La forme portée par l'IR : compacte, triée, reproductible."""
        return {
            "reference": REFERENCE_VERSION,
            "derivedFrom": sorted(set(self.sources)),
            "required": {c.id: list(self.justified[c.id]) for c in REFERENCE if self.requires(c.id)},
            "omitted": self.omitted(),
        }


# ---------------------------------------------------------------------------
# Lecture des exigences
# ---------------------------------------------------------------------------
def _stack_values(root: Path | None, heading: str) -> dict[str, Any]:
    if root is None:
        return {}
    from sdda_lib.layered_config import read_stack_section_kv  # noqa: PLC0415

    try:
        return read_stack_section_kv(root, heading)
    except Exception:  # une section illisible est jugée par smoke-check, pas ici
        return {}


def _active(root: Path | None, heading: str) -> list[str]:
    if root is None:
        return []
    from sdda_lib.layered_config import active_stacks  # noqa: PLC0415

    try:
        return active_stacks(root, heading)
    except Exception:
        return []


def roster_tools(root: Path | None, mission: str | int | None) -> set[str] | None:
    """Les outils que le roster accorde, None sans roster lisible (P7 : décision de l'architecte)."""
    if root is None or not mission:
        return None
    path = paths.roster_path(root, mission)
    if not path.is_file():
        return None
    from sdda_scripts.validate_architecture import read_roster_yaml  # noqa: PLC0415

    try:
        roster = read_roster_yaml(path)
    except Exception:  # un roster illisible est jugé par `roster validate`
        return None
    members = [roster.get("orchestrator") or {}] + list(roster.get("subagents") or [])
    return {str(t) for m in members if isinstance(m, dict) for t in (m.get("tools") or [])}


def _roster(root: Path | None, mission: str | int | None) -> dict[str, Any] | None:
    if root is None or not mission:
        return None
    path = paths.roster_path(root, mission)
    if not path.is_file():
        return None
    from sdda_scripts.validate_architecture import read_roster_yaml  # noqa: PLC0415

    try:
        return read_roster_yaml(path)
    except Exception:
        return None


def declared_sources(root: Path | None) -> dict[str, dict[str, Any]]:
    """Les sources de `## Active Data Sources`, si `declared-sources` est la stack active."""
    if root is None or _active(root, "Active Data Access") != ["declared-sources"]:
        return {}
    from sdda_lib import source_registry as sr  # noqa: PLC0415

    try:
        registry = sr.load_registry(root, _stack_values(root, "Active Data Sources"))
    except Exception:
        return {}
    return {sid: src for sid, src in registry.sources.items() if str(src.get("connector") or "") in sr.CONNECTORS}


def source_kinds(src: dict[str, Any]) -> list[str]:
    """Les outils qu'une DÉCLARATION de source rend possibles (référence, pas décision)."""
    kinds = ["lookup"] if src.get("key") else []
    if src.get("filters") or src.get("ranges") or src.get("required_filter"):
        kinds.extend(["search", "count"])
    return kinds


def wired_source_tools(sources: dict[str, dict[str, Any]], *, ir_tool_names: set[str] | None,
                       granted: set[str] | None) -> dict[str, set[str]]:
    """source -> kinds EFFECTIVEMENT exigés.

    L'IR fait foi s'il porte des outils ; sinon le roster ; sinon la déclaration
    seule (usage manuel, avant tout roster). Un outil que ni l'IR ni le roster
    ne câble n'est pas exigé — c'est le cas de `count` dans la première MISSION.
    """
    wanted = ir_tool_names if ir_tool_names else granted
    out: dict[str, set[str]] = {}
    for sid, src in sorted(sources.items()):
        kinds = source_kinds(src)
        if wanted is not None:
            kinds = [k for k in kinds if f"{sid}_{k}" in wanted]
        if kinds:
            out[sid] = set(kinds)
    return out


def load_ir(root: Path | None, mission: str | int | None) -> dict[str, Any]:
    if root is None or not mission:
        return {}
    path = paths.ir_path(root, mission)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(markdown_io.read_text(path))
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# Dérivation
# ---------------------------------------------------------------------------
def derive(ir: dict[str, Any] | None = None, *, root: Path | None = None,
           mission: str | int | None = None) -> EffectiveArchitecture:
    """La décision pour un projet. `ir` absent : lu sur disque si `root` et `mission` le permettent."""
    if ir is None:
        ir = load_ir(root, mission)
    mission = mission or (str(ir.get("missionId") or "").split("-", 1)[0] if ir else None)
    from sdda_lib.spec_needs import load_needs  # noqa: PLC0415

    needs = load_needs(root, mission) if root is not None else None
    arch = EffectiveArchitecture()
    if needs is not None and needs.declared:
        arch.sources.append("mission")
    if ir:
        arch.sources.append("ir")
    roster = _roster(root, mission)
    if roster is not None:
        arch.sources.append("roster")
    if root is not None:
        arch.sources.append("stack")

    agents = [a for a in ir.get("agents") or [] if isinstance(a, dict)]
    orchestration = ir.get("orchestration") if isinstance(ir.get("orchestration"), dict) else {}
    pattern = str(orchestration.get("rootPattern") or "") or next(
        iter(_active(root, "Active Orchestration Pattern")), "")

    _derive_agents(arch, agents, roster, pattern)
    _derive_orchestration(arch, ir, agents, orchestration, roster, pattern, root)
    _derive_memory(arch, ir, agents, roster, pattern, root, needs)
    _derive_tools_and_data(arch, ir, roster, root, mission)
    _derive_retrieval(arch, ir, root)
    _derive_guardrails(arch, ir, root, needs)
    _derive_platform(arch, ir, root)
    return arch


def _derive_agents(arch: EffectiveArchitecture, agents: list[dict[str, Any]],
                   roster: dict[str, Any] | None, pattern: str) -> None:
    if agents:
        arch.require("agent.loop", f"IR agents[] : {', '.join(str(a.get('id')) for a in agents)}")
    elif roster and isinstance(roster.get("orchestrator"), dict):
        arch.require("agent.loop", f"roster : agent `{roster['orchestrator'].get('id')}`")
    elif pattern:
        arch.require("agent.loop", f"STACK.md ## Active Orchestration Pattern : {pattern}")
    if arch.requires("agent.loop"):
        arch.require("bounds", "convention obligatoire du framework : bornes en code (règle 10, P12)")


def _multi_agent(agents: list[dict[str, Any]], roster: dict[str, Any] | None, pattern: str) -> str | None:
    """Pourquoi le système est multi-agents, ou None."""
    if len(agents) > 1:
        return f"IR : {len(agents)} agents"
    if not agents and roster and roster.get("subagents"):
        return f"roster : {len(roster['subagents'])} sous-agent(s)"
    if not agents and not roster and pattern in ("router", "sequential", "supervisor", "hierarchical"):
        return f"STACK.md ## Active Orchestration Pattern : {pattern}"
    return None


def _derive_orchestration(arch: EffectiveArchitecture, ir: dict[str, Any], agents: list[dict[str, Any]],
                          orchestration: dict[str, Any], roster: dict[str, Any] | None, pattern: str,
                          root: Path | None) -> None:
    nodes = [n for n in orchestration.get("nodes") or [] if isinstance(n, dict)]
    if pattern == "router" or any(n.get("kind") == "router" for n in nodes):
        arch.require("orchestration.router", f"pattern `router` ({'IR orchestration' if orchestration else 'STACK.md'})")
    if pattern == "sequential":
        arch.require("orchestration.sequential", f"pattern `sequential` ({'IR orchestration' if orchestration else 'STACK.md'})")
    multi = _multi_agent(agents, roster, pattern)
    if multi:
        arch.require("orchestration.graph", multi)
        shared = str((ir.get("memory") or {}).get("crossAgentSharedState")
                     or _stack_values(root, "Active Memory Strategy").get("CrossAgentSharedState") or "none")
        if shared.lower() != "none":
            arch.require("orchestration.shared-state", f"{multi} et CrossAgentSharedState: {shared}")
    handoffs = [f"{a.get('id')} -> {h.get('to')}" for a in agents for h in a.get("handoffs") or [] if isinstance(h, dict)]
    if handoffs:
        arch.require("orchestration.delegation", f"IR handoffs : {', '.join(handoffs)}")
    depth = [str(a.get("id")) for a in agents
             if isinstance(a.get("bounds"), dict) and int(a["bounds"].get("maxDelegationDepth") or 0) > 0]
    if depth:
        arch.require("orchestration.delegation", f"IR maxDelegationDepth > 0 : {', '.join(depth)}")
    if not agents and roster and roster.get("subagents"):
        arch.require("orchestration.delegation", "roster : subagents[] non vide")
    if orchestration.get("humanInTheLoop") is True:
        arch.require("orchestration.human-in-the-loop", "IR orchestration.humanInTheLoop: true")


def _derive_memory(arch: EffectiveArchitecture, ir: dict[str, Any], agents: list[dict[str, Any]],
                   roster: dict[str, Any] | None, pattern: str, root: Path | None, needs: Any = None) -> None:
    if needs is not None and needs.declared:
        # La SPEC décide s'il y a une conversation et une mémoire ; STACK.md ne dit
        # que COMMENT (taille de fenêtre, résumé) quand il y en a une.
        stack = _stack_values(root, "Active Memory Strategy")
        policy = str((ir.get("memory") or {}).get("shortTermPolicy") or stack.get("ShortTermPolicy") or "sliding-window").lower()
        if needs.multi_turn:
            arch.require("conversation.session", "MISSION Architecture Needs : conversation multi-tour "
                                                 f"(Conversation: {needs.get('Conversation')}, Memory: {needs.get('Memory')})")
            if policy in _SUMMARY_POLICIES:
                arch.require("memory.summarization", f"conversation multi-tour et STACK.md ShortTermPolicy: {policy}")
        if needs.long_term_memory:
            arch.require("memory.long-term", "MISSION Architecture Needs : Memory: long-term")
        for trigger in ("memory.summarization", "memory.long-term", "orchestration.shared-state"):
            if arch.requires(trigger):
                arch.require("memory.layer", f"exigé par `{trigger}`")
        return
    memory = ir.get("memory") if isinstance(ir.get("memory"), dict) else None
    if memory is None and not ir:
        stack = _stack_values(root, "Active Memory Strategy")
        memory = {"shortTermPolicy": stack.get("ShortTermPolicy"), "longTermEnabled": stack.get("LongTermEnabled"),
                  "longTermStore": stack.get("LongTermStore")}
    memory = memory or {}
    where = "IR memory" if ir else "STACK.md ## Active Memory Strategy"
    policy = str(memory.get("shortTermPolicy") or "none").lower()
    if policy in _SESSION_POLICIES:
        # La conversation multi-tour : l'historique de SESSION, tenu par le
        # framework (thread / historique de messages) et borné en tours. Ce
        # n'est pas une couche `memory/` : une fenêtre glissante est une borne
        # de la session, pas un composant de plus.
        arch.require("conversation.session", f"{where} : shortTermPolicy: {policy} (conversation multi-tour)")
    if policy in _SUMMARY_POLICIES:
        arch.require("memory.summarization", f"{where} : shortTermPolicy: {policy}")
    if str(memory.get("longTermEnabled") or "").strip().lower() in ("true", "yes", "1"):
        arch.require("memory.long-term", f"{where} : longTermEnabled: true (store {memory.get('longTermStore') or '?'})")
    for trigger in ("memory.summarization", "memory.long-term", "orchestration.shared-state"):
        if arch.requires(trigger):
            arch.require("memory.layer", f"exigé par `{trigger}`")


def _derive_tools_and_data(arch: EffectiveArchitecture, ir: dict[str, Any], roster: dict[str, Any] | None,
                           root: Path | None, mission: str | int | None) -> None:
    ir_tools = [t for t in ir.get("tools") or [] if isinstance(t, dict)]
    ir_names = {str(t.get("name") or t.get("id")) for t in ir_tools}
    granted = roster_tools(root, mission)
    sources = declared_sources(root)
    wired = wired_source_tools(sources, ir_tool_names=ir_names or None, granted=granted)
    source_tool_names = {f"{s}_{k}" for s, kinds in wired.items() for k in kinds}
    where = "IR tools[]" if ir_names else ("roster" if granted is not None else "STACK.md ## Active Data Sources")

    custom = sorted(n for n in (ir_names or granted or set()) if n not in source_tool_names
                    and not any(n == f"{s}_{k}" for s in sources for k in _SOURCE_KINDS))
    if custom:
        arch.require("tools.custom", f"{where} : {', '.join(custom)}")
    if custom or wired:
        arch.require("tools.registry", f"{where} : {len(custom) + len(source_tool_names)} outil(s) câblé(s)")

    for sid, kinds in sorted(wired.items()):
        src = sources[sid]
        arch.require("data.sources", f"{where} : outils de la source `{sid}`")
        fmt = str(src.get("format") or "").strip().lower()
        capability = FORMAT_CAPABILITY.get(fmt)
        if capability:
            arch.require(capability, f"source `{sid}` : format: {fmt}")
        for kind in sorted(kinds):
            arch.require(f"data.tool.{kind}", f"{where} : `{sid}_{kind}`")
        if "search" in kinds and src.get("ranges"):
            arch.require("data.ranges", f"source `{sid}` : ranges: {list(src['ranges'])}")
        if src.get("required_filter"):
            arch.require("data.identity-filter", f"source `{sid}` : required_filter: {list(src['required_filter'])}")
        if src.get("max_staleness_hours") not in (None, ""):
            arch.require("data.staleness", f"source `{sid}` : max_staleness_hours: {src['max_staleness_hours']}")
        if src.get("free_text") or str(src.get("connector") or "") in ("http-api", "mcp"):
            arch.require("data.untrusted-fields",
                         f"source `{sid}` : " + (f"free_text: {list(src['free_text'])}" if src.get("free_text")
                                                 else f"connector: {src.get('connector')} (tiers)"))

    access = [d for d in ir.get("dataAccess") or [] if isinstance(d, dict)]
    if access:
        arch.require("data.database", f"IR dataAccess[] : {', '.join(str(d.get('id')) for d in access)}")
    elif not ir and any(s.startswith("view-per-agent") for s in _active(root, "Active Data Access")):
        arch.require("data.database", "STACK.md ## Active Data Access : view-per-agent")


def _derive_retrieval(arch: EffectiveArchitecture, ir: dict[str, Any], root: Path | None) -> None:
    retrievers = [r for r in ir.get("retrievers") or [] if isinstance(r, dict)]
    if retrievers:
        arch.require("retrieval", f"IR retrievers[] : {', '.join(str(r.get('id')) for r in retrievers)}")
    elif not ir:
        rag = [s for s in _active(root, "Active RAG Pattern") if s != "none"]
        if rag:
            arch.require("retrieval", f"STACK.md ## Active RAG Pattern : {rag[0]}")
    if arch.requires("retrieval"):
        rerank = [s for s in _active(root, "Active Reranker") if s != "none"]
        if rerank:
            arch.require("retrieval.rerank", f"STACK.md ## Active Reranker : {rerank[0]}")


#: Guardrail -> (besoin de la spec qui l'exige, libellé).
def guardrail_needed(gid: str, needs: Any, arch: EffectiveArchitecture | None = None) -> str | None:
    """Pourquoi la SPEC exige ce guardrail, ou None. STACK.md ne fait que l'AUTORISER."""
    if gid == "injection-detection":
        if needs.untrusted:
            return f"MISSION Trust Boundaries : entrée non maîtrisée ({', '.join(needs.untrusted_inputs)})"
        if arch is not None and arch.requires("data.untrusted-fields"):
            return "une source déclarée porte du texte de tiers (data.untrusted-fields)"
        return None
    if gid == "schema-validation":
        return "MISSION Architecture Needs : StructuredOutput: yes" if needs.structured_output else None
    if gid == "pii-redaction":
        return "MISSION Architecture Needs : PersonalDataRedaction: yes" if needs.pii_redaction else None
    return None


def _derive_guardrails(arch: EffectiveArchitecture, ir: dict[str, Any], root: Path | None, needs: Any = None) -> None:
    if needs is not None and needs.declared:
        allowed = set(_active(root, "Active Guardrails"))
        if isinstance(ir.get("guardrails"), dict):
            allowed |= {str(g.get("id")) for point in ("input", "output") for g in ir["guardrails"].get(point) or []
                        if isinstance(g, dict)}
        for gid in sorted(allowed):
            why = guardrail_needed(gid, needs, arch)
            if why and f"guardrail.{gid}" in CATALOG:
                arch.require(f"guardrail.{gid}", f"{why} (autorisé par STACK.md)")
        return
    guardrails = ir.get("guardrails") if isinstance(ir.get("guardrails"), dict) else None
    ids: list[tuple[str, str]] = []
    if guardrails:
        for point in ("input", "output"):
            ids.extend((str(g.get("id")), f"IR guardrails.{point}") for g in guardrails.get(point) or []
                       if isinstance(g, dict))
    elif not ir:
        ids.extend((g, "STACK.md ## Active Guardrails") for g in _active(root, "Active Guardrails"))
    for gid, where in ids:
        capability = f"guardrail.{gid}"
        if capability in CATALOG:
            arch.require(capability, f"{where} : {gid}")


#: Fiche de surface -> capacité de serving.
_SURFACES = {"cli": "serving.cli", "cli-dotnet": "serving.cli", "cli-node": "serving.cli", "cli-jvm": "serving.cli",
             "sse": "serving.http", "http": "serving.http", "mcp-server": "serving.mcp", "batch": "serving.batch"}


def _derive_platform(arch: EffectiveArchitecture, ir: dict[str, Any], root: Path | None) -> None:
    observability = _active(root, "Active Observability")
    if observability:
        arch.require("tracing", f"STACK.md ## Active Observability : {observability[0]} "
                                "(coût, latence et trajectoire de la MISSION se mesurent sur les traces)")
    for surface in _active(root, "Active Serving Surface"):
        capability = _SURFACES.get(surface) or next(
            (c for key, c in _SURFACES.items() if surface.startswith(key)), None)
        if capability:
            arch.require(capability, f"STACK.md ## Active Serving Surface : {surface}")


# ---------------------------------------------------------------------------
# Composants : quel fichier exige quelle capacité
# ---------------------------------------------------------------------------
#: Composants REPÉRABLES dans un livrable, quel que soit le langage : motif de
#: chemin (relatif à `src/{App}/`, `/` comme séparateur, insensible à la casse)
#: -> capacité qui doit être requise pour qu'il existe. C'est ce que
#: `validate_effective_architecture` confronte au livrable. Les tests
#: (`tests/`) sont exclus : un test suit le composant qu'il teste.
COMPONENT_RULES: tuple[tuple[str, str], ...] = (
    (r"(^|/)memory/", "memory.layer"),
    (r"(^|/)orchestration/(routers?|router_\w+)\.\w+$", "orchestration.router"),
    (r"(^|/)orchestration/sequential\w*\.\w+$", "orchestration.sequential"),
    (r"(^|/)orchestration/(orchestration_?state|shared_?state)\.\w+$", "orchestration.shared-state"),
    (r"(^|/)orchestration/(subagents?|delegation|handoffs?)\w*\.\w+$", "orchestration.delegation"),
    (r"(^|/)retrieval/", "retrieval"),
    (r"(^|/)(vector_?store|retriever)s?\w*\.\w+$", "retrieval"),
    (r"(^|/)data/formats/csv_reader\.py$", "data.format.delimited"),
    (r"(^|/)data/formats/tabular\.py$", "data.format.delimited"),
    (r"(^|/)data/formats/json_reader\.py$", "data.format.json|data.format.jsonl"),
    (r"(^|/)data/RecordReaders\.(Csv|Delimited)\.cs$", "data.format.delimited"),
    (r"(^|/)data/RecordReaders\.Jsonl\.cs$", "data.format.jsonl"),
    (r"(^|/)data/(views|repositories)/", "data.database"),
    (r"(^|/)guardrails?/(injection\w*)\.\w+$", "guardrail.injection-detection"),
    (r"(^|/)guardrails?/(pii\w*)\.\w+$", "guardrail.pii-redaction"),
    (r"(^|/)guardrails?/(schema\w*)\.\w+$", "guardrail.schema-validation"),
)


def component_capability(relative: str) -> str | None:
    """La capacité (ou disjonction `a|b`) qu'exige ce fichier, None s'il n'est pas un composant repéré."""
    rel = relative.replace("\\", "/")
    if re.search(r"(^|/)tests?/", rel, re.I):
        return None
    for pattern, capability in COMPONENT_RULES:
        if re.search(pattern, rel, re.I):
            return capability
    return None


def satisfied(expr: str, features: Iterable[str]) -> bool:
    have = set(features)
    return any(term.strip() in have for term in expr.split("|"))
