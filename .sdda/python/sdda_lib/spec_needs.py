"""Les besoins d'architecture DÉCLARÉS PAR LA SPEC — et le dimensionnement qui en découle (0 token).

Ce que ce module défend : *STACK.md dit ce que le Tech Lead AUTORISE ; la spec dit
ce dont le projet A BESOIN ; l'architecture générée est l'intersection.*

Le sens était inversé. STACK.md, écrit avant la MISSION, activait une mémoire
glissante de 12 tours, deux guardrails, un pattern d'orchestration et une
architecture MVC ; l'IR recopiait STACK.md ; la MISSION devait même le
RECOPIER (`## Required Stack`). Aucune ligne de la spec ne pouvait dire « pas de
multi-tour » ou « pas de sortie structurée ». Un chat question/réponse sur un
fichier JSON — une page de code, quinze minutes de développement — traversait le
pipeline d'un système multi-agents : cinq heures.

La MISSION porte donc une section fermée, `## Architecture Needs`, que
`po-elicitor` remplit DEPUIS LE BRIEF (défaut : le minimum) :

    - Conversation: single-turn | multi-turn
    - Memory: none | session | long-term
    - Documents: none | <corpus à interroger en langage naturel>        -> RAG
    - Agents: single | multi
    - SideEffects: none | <écritures, envois, actions externes>
    - StructuredOutput: no | yes                                          -> schema-validation
    - PersonalDataRedaction: no | yes                                     -> pii-redaction

Deux consommateurs :
  - `effective_architecture` et `ir_compiler` : une capacité que STACK.md
    autorise mais que ces besoins n'exigent pas n'entre ni dans l'IR ni dans le
    code (mémoire, guardrails, RAG) ;
  - `sizing()` : une spec qui n'exige rien au-delà d'un agent, de ses outils de
    lecture et d'une console est `micro` — le profil court de `/sdda-full`
    (`Profile: auto`), une application en un seul module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdda_lib import markdown_io, paths

SECTION = "Architecture Needs"

#: Clé -> (valeurs admises ou None si texte libre avec `none`, valeur minimale).
NEEDS_GRAMMAR: dict[str, tuple[tuple[str, ...] | None, str]] = {
    "Conversation": (("single-turn", "multi-turn"), "single-turn"),
    "Memory": (("none", "session", "long-term"), "none"),
    "Documents": (None, "none"),
    "Agents": (("single", "multi"), "single"),
    "SideEffects": (None, "none"),
    "StructuredOutput": (("no", "yes"), "no"),
    "PersonalDataRedaction": (("no", "yes"), "no"),
}

_NONE = frozenset({"none", "aucun", "aucune", "no", "non", "-", "n/a", "sans objet"})


@dataclass
class Needs:
    """Les besoins lus dans la MISSION. `declared` : la section existe."""

    values: dict[str, str] = field(default_factory=dict)
    declared: bool = False
    problems: list[str] = field(default_factory=list)
    untrusted_inputs: list[str] = field(default_factory=list)

    def get(self, key: str) -> str:
        return self.values.get(key, NEEDS_GRAMMAR[key][1])

    @property
    def multi_turn(self) -> bool:
        return self.get("Conversation") == "multi-turn" or self.get("Memory") in ("session", "long-term")

    @property
    def long_term_memory(self) -> bool:
        return self.get("Memory") == "long-term"

    @property
    def documents(self) -> bool:
        return self.get("Documents").strip().lower() not in _NONE

    @property
    def multi_agent(self) -> bool:
        return self.get("Agents") == "multi"

    @property
    def side_effects(self) -> bool:
        return self.get("SideEffects").strip().lower() not in _NONE

    @property
    def structured_output(self) -> bool:
        return self.get("StructuredOutput") == "yes"

    @property
    def pii_redaction(self) -> bool:
        return self.get("PersonalDataRedaction") == "yes"

    @property
    def untrusted(self) -> bool:
        return any(v.strip().lower() not in _NONE for v in self.untrusted_inputs)

    def to_dict(self) -> dict[str, Any]:
        return {"declared": self.declared, **{k: self.get(k) for k in NEEDS_GRAMMAR},
                "untrustedInputs": list(self.untrusted_inputs)}


def parse_needs(text: str) -> Needs:
    """Les besoins d'un texte de MISSION. Section absente : `declared=False`, valeurs minimales."""
    body = markdown_io.section_body(text, SECTION)
    trust = markdown_io.parse_kv_list(markdown_io.section_body(text, "Trust Boundaries") or "")
    untrusted = [v for k, v in trust.items() if k.strip().lower() == "untrusted"]
    needs = Needs(untrusted_inputs=[u for raw in untrusted for u in markdown_io.split_code_list(raw)] or untrusted)
    if body is None:
        return needs
    needs.declared = True
    raw = {k.strip(): markdown_io.strip_code(v).strip() for k, v in markdown_io.parse_kv_list(body).items()}
    lowered = {k.lower(): k for k in NEEDS_GRAMMAR}
    for key, value in raw.items():
        canonical = lowered.get(key.lower())
        if canonical is None:
            needs.problems.append(f"clé inconnue `{key}` (admises : {', '.join(NEEDS_GRAMMAR)})")
            continue
        allowed, _ = NEEDS_GRAMMAR[canonical]
        if markdown_io.is_placeholder(value) or not value:
            needs.problems.append(f"`{canonical}` non renseigné")
            continue
        head = re.split(r"\s+[—(-]\s*|\s*\(", value, maxsplit=1)[0].strip().lower()
        if allowed is not None and head not in allowed:
            needs.problems.append(f"`{canonical}: {value}` hors de {list(allowed)}")
            continue
        needs.values[canonical] = head if allowed is not None else value
    return needs


def mission_path(root: Path, mission: str | int | None) -> Path | None:
    if root is None or not mission:
        return None
    head = str(mission).split("-", 1)[0]
    found = sorted(paths.missions_dir(root).glob(f"{head}-*.md"))
    return found[0] if found else None


def load_needs(root: Path | None, mission: str | int | None) -> Needs:
    """Les besoins de la MISSION `mission`, ou `Needs()` (non déclarés) si introuvable."""
    path = mission_path(root, mission) if root is not None else None
    if path is None or not path.is_file():
        return Needs()
    return parse_needs(markdown_io.read_text(path))


# ---------------------------------------------------------------------------
# Dimensionnement
# ---------------------------------------------------------------------------
#: Surfaces qu'un projet `micro` admet : une console, rien à héberger.
MICRO_SURFACES = ("cli", "cli-dotnet", "cli-node", "cli-jvm")
#: Nombre d'outils au-delà duquel un agent seul n'est plus « une page de code ».
MICRO_MAX_TOOLS = 6


@dataclass
class Sizing:
    complexity: str = "standard"          # micro | standard
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    decided: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"complexity": self.complexity, "decided": self.decided,
                "reasons": list(self.reasons), "blockers": list(self.blockers)}


def sizing(root: Path, mission: str | int | None) -> Sizing:
    """`micro` si la spec n'exige rien au-delà d'UN agent, d'outils de lecture et d'une console.

    Chaque critère non tenu est un `blocker` NOMMÉ : le dimensionnement se lit,
    il ne se devine pas. Sans `## Architecture Needs`, rien n'est décidable
    (`decided=False`) et le pipeline complet s'applique.
    """
    from sdda_lib.effective_architecture import _active, _stack_values, roster_tools  # noqa: PLC0415

    out = Sizing()
    needs = load_needs(root, mission)
    if not needs.declared:
        out.blockers.append("MISSION sans `## Architecture Needs` : besoins non déclarés")
        return out
    out.decided = True
    if needs.problems:
        out.blockers.extend(f"Architecture Needs : {p}" for p in needs.problems)
    for flag, why in ((needs.multi_agent, "Agents: multi"), (needs.documents, "Documents : un corpus à interroger (RAG)"),
                      (needs.long_term_memory, "Memory: long-term"), (needs.side_effects, "SideEffects : le système agit")):
        if flag:
            out.blockers.append(why)
    from sdda_scripts.validate_architecture import read_roster_yaml  # noqa: PLC0415

    roster_file = paths.roster_path(root, str(mission).split("-", 1)[0]) if mission else None
    if roster_file is not None and roster_file.is_file():
        try:
            roster = read_roster_yaml(roster_file)
        except Exception:  # jugé par `roster validate`
            roster = {}
        if roster.get("subagents"):
            out.blockers.append(f"roster : {len(roster['subagents'])} sous-agent(s)")
        tools = roster_tools(root, str(mission).split("-", 1)[0]) or set()
        if len(tools) > MICRO_MAX_TOOLS:
            out.blockers.append(f"roster : {len(tools)} outils (> {MICRO_MAX_TOOLS})")
    access = _active(root, "Active Data Access")
    if any(a.startswith("view-per-agent") for a in access):
        out.blockers.append("accès base (view-per-agent)")
    surfaces = _active(root, "Active Serving Surface")
    if surfaces and not all(s in MICRO_SURFACES for s in surfaces):
        out.blockers.append(f"surface {surfaces} (une console seulement en micro)")
    deliverable = str(_stack_values(root, "Active Serving Surface").get("DeliverableType") or "cli-exe").split()[0]
    if deliverable not in ("cli-exe", "container"):
        out.blockers.append(f"DeliverableType: {deliverable}")
    if not out.blockers:
        out.complexity = "micro"
        out.reasons.append("un agent, des outils de lecture, une console, ni RAG, ni mémoire longue, ni effet de bord")
        if needs.multi_turn:
            out.reasons.append("conversation multi-tour : historique de session borné, sans couche mémoire")
    return out
