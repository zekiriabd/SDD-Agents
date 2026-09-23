#!/usr/bin/env python3
"""G2 (côté Markdown) — TOPOLOGY GATE sur `workspace/feats/topology/{n}-topology.md`.

Le contrôle du GRAPHE (atteignabilité, bornes, références) se fait sur l'IR
(`validate_ir.py`). La COMPLÉTUDE de la déclaration d'architecture — roster,
rôles, outils et modèles par agent, relations — est vérifiée par
`validate_architecture.py`. Ici, on vérifie ce que seul le Markdown porte :
  - handoffs contractés (`[HANDOFF_UNCONTRACTED]`), chemin de repli du routeur
    (`[ROUTER_NO_FALLBACK]`), contrats présents sur disque, hash MISSION à jour ;
  - la cohérence CAP <-> allocation ;
  - **en avertissement seulement** : un agent au-delà du premier dont la raison
    ne figure pas dans la liste close de P7 (isolation de scope d'outils, tier
    distinct, pression de contexte, fonction objectif différente, parallélisme
    requis) -> `[TOPOLOGY_SIMPLICITY_ADVISORY]`.

**Pourquoi un avertissement et non un refus.** L'architecture appartient à
l'architecte (P7) : sept subagents là où deux suffiraient reste une erreur
fréquente et coûteuse, mais c'est la sienne, et il a le droit de la commettre en
connaissance de cause. Ce que le framework refuse, c'est qu'elle soit commise
**par défaut, par un LLM, sans que personne l'ait écrite** — et cela, c'est
`validate_architecture.py` qui le garantit, en exigeant que la déclaration soit
complète avant toute génération.

Rapport : `G2-{missionId}.topology.json`.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, mermaid, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.graph import Graph  # noqa: E402
from sdda_lib.layered_config import LayeredConfig  # noqa: E402
from sdda_scripts._common import add_common_args, finish, load_config, resolve_root  # noqa: E402
from sdda_scripts.validate_mission import load_mission  # noqa: E402

PATTERNS = ("single-agent", "router", "sequential", "parallel", "supervisor", "graph", "plan-execute", "reflection", "blackboard")
#: Liste close de P7 : mot-clés (sans accents, minuscules) -> raison canonique.
REASONS: dict[str, tuple[str, ...]] = {
    "tool-scope-isolation": ("isolation",),
    "distinct-tier": ("tier",),
    "context-pressure": ("pression", "contexte"),
    "different-objective": ("objectif",),
    "parallelism": ("parallel",),
}
_REFUSED_REASON = "separation des responsabilites"
_CARRIER_RE = re.compile(r"\b(agent|outil|tool|retriever)\b\s*`([^`]+)`", re.IGNORECASE)


@dataclass
class TopologySpec:
    mission_id: str
    mission_hash: str
    root_pattern: str
    header: dict[str, str]
    allocation: list[dict[str, str]]
    justifications: list[dict[str, str]]
    graph_meta: dict[str, str]
    mermaid_text: str
    handoffs: list[dict[str, str]]
    contracts: list[dict[str, str]]
    alternative_body: str | None
    hash: str
    path: Path | None = None
    text: str = ""
    agents: list[str] = field(default_factory=list)      # slugs tels qu'écrits
    tools: list[str] = field(default_factory=list)
    retrievers: list[str] = field(default_factory=list)

    @property
    def mission_number(self) -> int:
        m = re.match(r"^(\d+)-", self.mission_id)
        return int(m.group(1)) if m else 0

    def qualified(self, slug: str) -> str:
        """`billing-specialist` -> `1-billing-specialist` (id d'agent du domaine)."""
        return slug if re.match(r"^\d+-", slug) else f"{self.mission_number}-{slug}"


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def _col(row: dict[str, str], *names: str) -> str:
    """Cellule par nom de colonne, insensible aux accents/casse."""
    wanted = {_norm(n) for n in names}
    for k, v in row.items():
        if _norm(k).strip() in wanted:
            return v
    return ""


def classify_reason(text: str) -> str | None:
    """Raison canonique de P7, ou None si hors liste close."""
    n = _norm(text)
    if _REFUSED_REASON in n:
        return None
    for reason, keys in REASONS.items():
        if any(k in n for k in keys):
            return reason
    return None


def parse_topology(text: str, path: Path | None = None) -> TopologySpec:
    header = markdown_io.parse_header_fields(text)

    def sec(title: str) -> str | None:
        return markdown_io.section_body(text, title)

    allocation = markdown_io.parse_table(sec("Allocation des capabilities") or "")
    # Le gabarit, la fixture, la fiche de l'architecte et le message d'erreur
    # de ce même script disent « considérée » ; seul cet appel disait
    # « écartée ». La section était donc lue comme ABSENTE quoi que l'architecte
    # écrive, la table de justification jamais parsée, et l'avertissement
    # « agent sans raison nommée » tombait sur chaque topologie. On accepte les
    # deux graphies : un workspace existant a pu adopter l'une ou l'autre.
    alt = sec("Alternative plus simple considérée") or sec("Alternative plus simple écartée")
    justifications = [r for r in markdown_io.parse_table(alt or "") if not markdown_io.is_placeholder(markdown_io.strip_code(_col(r, "Agent")))]
    graph_sec = sec("Le graphe") or ""
    meta = {_norm(k).replace("œ", "oe"): markdown_io.strip_code(v) for k, v in markdown_io.parse_kv_list(graph_sec).items()}
    mm = markdown_io.fenced_blocks(graph_sec, "mermaid")
    spec = TopologySpec(
        mission_id=header.get("MISSION", "").strip(),
        mission_hash=header.get("MISSION hash", "").strip(),
        root_pattern=header.get("Root Pattern", "").strip().lower(),
        header=header, allocation=allocation, justifications=justifications,
        graph_meta=meta, mermaid_text=mm[0] if mm else "",
        handoffs=markdown_io.parse_table(sec("Handoffs") or ""),
        contracts=markdown_io.parse_table(sec("Contrats produits") or ""),
        alternative_body=alt, hash=hashing.sha256_spec_text(text), path=path, text=text,
    )
    agents, tools, retrievers = [], [], []
    for row in allocation:
        for kind, slug in _CARRIER_RE.findall(_col(row, "Portée par")):
            target = {"agent": agents, "outil": tools, "tool": tools, "retriever": retrievers}[kind.lower()]
            if slug not in target and not markdown_io.is_placeholder(slug):
                target.append(slug)
    for row in justifications:
        slug = markdown_io.strip_code(_col(row, "Agent"))
        if slug and slug not in agents:
            agents.append(slug)
    spec.agents, spec.tools, spec.retrievers = agents, tools, retrievers
    return spec


def load_mermaid(root: Path | None, spec: TopologySpec) -> str:
    """Le graphe : le bloc ```mermaid de `## 4. Le graphe` dans `{n}-topology.md`.

    Il a eu un fichier à côté, `{n}-topology.mmd`, qui « faisait foi » quand il
    existait. Deux artefacts pour un graphe, c'est celui que personne ne relit
    qui gouverne l'IR — et `feats/` ne porte que du Markdown. Le graphe EST une
    section de la topologie, et son hash est celui du fichier qui la contient.
    """
    del root  # conservé pour la signature ; la source est unique désormais
    return spec.mermaid_text


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def validate_topology_text(text: str, *, path: Path | None, root: Path | None, config: LayeredConfig | None, pre: bool = False) -> tuple[Report, TopologySpec]:
    spec = parse_topology(text, path)
    loc = paths.rel(root, path) if (root and path) else (str(path) if path else "<texte>")
    report = Report(name="G2.topology", target=spec.mission_id or loc)
    report.data = {"missionId": spec.mission_id, "hash": spec.hash, "agents": spec.agents, "rootPattern": spec.root_pattern}

    # En-tête ------------------------------------------------------------------
    if not spec.mission_id:
        report.error("TOPOLOGY_INCOMPLETE", "`MISSION:` absent de l'en-tête", "écrire `MISSION: {n}-{MissionName}`", loc)
    mission = load_mission(root, spec.mission_id) if (root and spec.mission_id) else None
    if root is not None and spec.mission_id and mission is None:
        report.error("TOPOLOGY_MISSION_MISSING", f"MISSION `{spec.mission_id}` introuvable", "corriger `MISSION:` ou créer la MISSION", loc)
    if mission is not None:
        if not hashing.is_hash_ref(spec.mission_hash):
            report.error("TOPOLOGY_MISSION_HASH_STALE", f"`MISSION hash: {spec.mission_hash or '<absent>'}` invalide", f"épingler `MISSION hash: {hashing.short(mission.hash)}`", loc)
        elif not hashing.hashes_match(spec.mission_hash, mission.hash):
            report.error("TOPOLOGY_MISSION_HASH_STALE", f"la MISSION a changé (épinglé {spec.mission_hash}, courant {hashing.short(mission.hash)})",
                         f"revoir la topologie puis épingler `MISSION hash: {hashing.short(mission.hash)}`", loc)
    if spec.root_pattern == "network":
        report.error("TOPOLOGY_PATTERN_REFUSED", "`Root Pattern: network` est refusé par défaut (coût non borné)", "choisir `graph` avec des cycles bornés, ou produire un ADR démontrant qu'aucun graph ne convient", loc)
    elif spec.root_pattern not in PATTERNS:
        report.error("TOPOLOGY_INCOMPLETE", f"`Root Pattern: {spec.root_pattern or '<absent>'}` hors de {list(PATTERNS)}", "déclarer le pattern racine", loc)

    # Allocation ---------------------------------------------------------------------
    if not spec.allocation:
        report.error("TOPOLOGY_INCOMPLETE", "table `## 1. Allocation des capabilities` vide ou absente", "allouer chaque CAP à un outil, un agent ou un retriever", loc)
    elif root is not None and spec.mission_number:
        declared_caps = {p.stem for p in paths.caps_dir(root).glob(f"{spec.mission_number}-*.md")}
        allocated_caps = {markdown_io.strip_code(_col(r, "CAP")) for r in spec.allocation}
        allocated_caps = {c for c in allocated_caps if not markdown_io.is_placeholder(c)}
        for c in sorted(allocated_caps - declared_caps):
            report.error("TOPOLOGY_CAP_UNKNOWN", f"allocation d'une CAP inconnue `{c}`", "corriger l'id ou créer la CAP", loc)
        for c in sorted(declared_caps - allocated_caps):
            report.error("TOPOLOGY_CAP_UNALLOCATED", f"CAP `{c}` n'est portée par rien", "l'allouer dans la table `## 1.`", loc)

    # Alternative plus simple considérée — P7, CONSULTATIF ---------------------------------
    #
    # Ces contrôles étaient bloquants tant que la topologie était décidée par un
    # LLM : il fallait l'empêcher d'ajouter des agents par réflexe. Depuis que
    # l'architecture est déclarée par l'architecte (P7), le veto n'a plus de
    # destinataire légitime — un refus opposerait le framework à la personne qui
    # a précisément autorité pour décider. Ce qui reste bloquant est ailleurs, et
    # c'est le bon endroit : `validate_architecture.py` exige que la déclaration
    # soit COMPLÈTE avant toute génération.
    alt = spec.alternative_body
    if markdown_io.section_is_empty(alt):
        report.warn(
            "TOPOLOGY_SIMPLICITY_ADVISORY",
            "section « Alternative plus simple considérée » absente ou vide",
            "écrire la topologie à N-1 agents envisagée et ce qui la disqualifie — pour que le choix "
            "soit tracé, pas pour qu'il soit négocié",
            loc,
        )
    else:
        for marker in ("Topologie envisagée", "Ce qui la disqualifie"):
            if not _paragraph_after(alt or "", marker):
                report.warn("TOPOLOGY_SIMPLICITY_ADVISORY", f"« {marker} » non renseigné",
                            f"remplir le paragraphe sous **{marker}**", loc)

    n_agents = len(spec.agents)
    if n_agents >= 2:
        justified: dict[str, str] = {}
        for row in spec.justifications:
            slug = markdown_io.strip_code(_col(row, "Agent"))
            reason_txt = _col(row, "Raison invoquée")
            reason = classify_reason(reason_txt)
            if reason is None:
                refused = _REFUSED_REASON in _norm(reason_txt)
                report.warn("TOPOLOGY_SIMPLICITY_ADVISORY",
                            f"agent `{slug}` : raison « {reason_txt.strip()} » " + ("hors liste — « séparation des responsabilités » produit le graphe à cinq agents que P7 signale" if refused else "hors de la liste close de P7"),
                            "si l'une s'applique, la nommer : isolation de scope d'outils, tier distinct, pression de contexte (mesurée), fonction objectif différente, parallélisme requis", loc)
            else:
                justified[slug] = reason
            if markdown_io.is_placeholder(_col(row, "Élément de preuve")):
                report.warn("TOPOLOGY_JUSTIFICATION_UNPROVEN", f"agent `{slug}` : aucun élément de preuve", "", loc)
        unjustified = [a for a in spec.agents if a not in justified]
        if len(unjustified) > 1:
            report.warn("TOPOLOGY_SIMPLICITY_ADVISORY", f"{n_agents} agents, dont {len(unjustified[1:])} sans raison nommée : {unjustified[1:]}",
                        "l'architecture reste la vôtre ; la tracer coûte une ligne et se relit dans six mois", loc)
        report.data["justifications"] = justified
    warn_at = config.get_int("MaxAgentsWarnAt", 4) if config else 4
    if n_agents > warn_at:
        report.warn("TOPOLOGY_MANY_AGENTS", f"{n_agents} agents (> MaxAgentsWarnAt={warn_at}) : justification renforcée exigée", "", loc)

    # Graphe --------------------------------------------------------------------------------
    for key, label in (("noeud d'entree", "Nœud d'entrée"), ("noeuds terminaux", "Nœuds terminaux"), ("maxhops", "maxHops")):
        if markdown_io.is_placeholder(spec.graph_meta.get(key)):
            report.error("TOPOLOGY_GRAPH_INCOMPLETE", f"`{label}` non renseigné dans `## 3. Le graphe`", f"écrire `- **{label}** : …`", loc)
    mh = spec.graph_meta.get("maxhops", "")
    if mh and not markdown_io.is_placeholder(mh) and not re.match(r"^\d+$", mh.strip()):
        report.error("TOPOLOGY_GRAPH_INCOMPLETE", f"`maxHops` = `{mh}` n'est pas un entier", "", loc)
    mm_text = load_mermaid(root, spec)
    if not mm_text.strip():
        report.error("TOPOLOGY_GRAPH_INCOMPLETE", "aucun graphe : pas de bloc ```mermaid dans `## 4. Le graphe`", "dessiner le graphe dans la topologie — il sera compilé dans l'IR", loc)
    else:
        mg = mermaid.parse(mm_text)
        g = Graph(mg.nodes.keys(), [(e.src, e.dst) for e in mg.edges])
        cycles = g.elementary_cycles()
        if cycles and markdown_io.is_placeholder(spec.graph_meta.get("cycles")):
            report.error("UNBOUNDED_LOOP", f"le graphe contient {len(cycles)} cycle(s) ({' ; '.join('->'.join(c) for c in cycles[:3])}) et `Cycles` ne nomme pas la borne qui les coupe",
                         "lister chaque cycle et sa borne (maxHops, condition décrémentante, max_iterations) — P12", loc)
        has_router = spec.root_pattern == "router" or any(n.shape == "diamond" for n in mg.nodes.values())
        if has_router and markdown_io.is_placeholder(spec.graph_meta.get("chemin de repli")):
            report.error("ROUTER_NO_FALLBACK", "routeur sans `Chemin de repli` : le cas « aucune branche ne correspond » n'est pas pensé",
                         "déclarer `- **Chemin de repli** : …` et une arête de repli dans le graphe", loc)

    # Handoffs ---------------------------------------------------------------------------------
    real_handoffs = [r for r in spec.handoffs if not all(markdown_io.is_placeholder(markdown_io.strip_code(v)) for v in r.values())]
    for r in real_handoffs:
        src, dst = markdown_io.strip_code(_col(r, "De")), markdown_io.strip_code(_col(r, "Vers"))
        for col in ("Condition", "État transmis", "Retour attendu"):
            v = _col(r, col)
            if markdown_io.is_placeholder(markdown_io.strip_code(v)) or "le contexte suit" in _norm(v):
                report.error("HANDOFF_UNCONTRACTED", f"handoff `{src}` -> `{dst}` : `{col}` non contracté", "« le contexte suit » n'est pas un contrat : nommer l'état transmis et le retour attendu", loc)
    if n_agents >= 2 and not real_handoffs:
        report.error("HANDOFF_UNCONTRACTED", f"{n_agents} agents et aucun handoff contracté dans `## 6. Handoffs`", "déclarer chaque passage de main : condition, état transmis, retour attendu", loc)

    # Contrats sur disque ------------------------------------------------------------------------
    #
    # Sautés en mode `--pre`, et c'est tout l'intérêt du mode : `/sdda-topology`
    # joue ce script juste après `architect-topology`, AVANT de payer les quatre
    # architectes qui écrivent les contrats. À cet instant l'absence de contrat
    # est l'état nominal, pas une faute — la signaler ferait échouer le
    # post-check à chaque run et apprendrait à l'ignorer. Tout le reste (roster,
    # bornes, handoffs, simplicité) est vérifié dans les deux modes, et la passe
    # complète rejoue ces deux contrôles avant la compilation de l'IR.
    if root is not None and not pre:
        for r in spec.contracts:
            f = markdown_io.strip_code(_col(r, "Fichier"))
            if markdown_io.is_placeholder(f) or "{" in f:
                continue
            if not paths.resolve_rel(root, f).is_file():
                report.error("TOPOLOGY_CONTRACT_MISSING", f"contrat annoncé introuvable : `{f}`", "produire le contrat ou retirer la ligne", loc)
        for slug in spec.agents:
            p = paths.contracts_dir(root, "agents") / f"{spec.qualified(slug)}.agent.md"
            if not p.is_file():
                report.error("AGENT_CONTRACT_MISSING", f"agent `{slug}` sans contrat `{paths.rel(root, p)}`", "écrire le contrat depuis templates/agent-contract.template.md", loc)
    return report, spec


def _paragraph_after(body: str, marker: str) -> str:
    """Texte réel entre `**marker**` et le prochain `**…**` / table / fin."""
    m = re.search(rf"\*\*{re.escape(marker)}[^*]*\*\*\s*:?\s*", body)
    if not m:
        return ""
    rest = body[m.end():]
    nxt = re.search(r"^\s*(\*\*|\||>)", rest, re.MULTILINE)
    chunk = rest[: nxt.start()] if nxt else rest
    lines = [l.strip() for l in chunk.split("\n") if l.strip() and not markdown_io.is_placeholder(l.strip())]
    return " ".join(lines)


def validate_topology_file(path: Path, root: Path, config: LayeredConfig | None, *, write_report: bool = True, pre: bool = False) -> Report:
    report, spec = validate_topology_text(markdown_io.read_text(path), path=path, root=root, config=config, pre=pre)
    if write_report and spec.mission_id:
        pinned = {"topology": spec.hash}
        mission = load_mission(root, spec.mission_id)
        if mission:
            pinned["mission"] = mission.hash
        write_gate_report(root, "G2", spec.mission_id, report, pinned, part="topology")
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="G2 (Markdown) — TOPOLOGY GATE : simplicité justifiée, handoffs contractés")
    # `--mission {n}` est la forme qu'emploient les commandes : elles ne
    # connaissent qu'un numéro, jamais un chemin. Le positionnel reste pour
    # l'usage manuel et pour les tests.
    p.add_argument("--mission", type=int, default=None, help="numéro de MISSION ; restreint aux fichiers de cette MISSION")
    p.add_argument("files", nargs="*", type=Path, help="fichiers topology ; défaut : workspace/feats/topology/*-topology.md")
    p.add_argument("--pre", action="store_true",
                   help="passe PRÉ-CONTRATS : tout sauf l'existence des contrats sur disque. "
                        "C'est l'état nominal juste après architect-topology, avant de payer les architectes")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    combined = Report(name="G2.topology", target=str(root))
    config = load_config(root, combined)
    if args.files:
        files = list(args.files)
    elif args.mission is not None:
        files = sorted(paths.topology_dir(root).glob(f"{args.mission}-topology.md"))
    else:
        files = sorted(paths.topology_dir(root).glob("*-topology.md"))
    if not files:
        combined.error("TOPOLOGY_INCOMPLETE", "aucune topologie trouvée", "créer workspace/feats/topology/{n}-topology.md", str(paths.topology_dir(root)))
    for f in files:
        combined.extend(validate_topology_file(f, root, config, write_report=not args.no_report, pre=args.pre))
    return finish(combined, args)


if __name__ == "__main__":
    sys.exit(main())
