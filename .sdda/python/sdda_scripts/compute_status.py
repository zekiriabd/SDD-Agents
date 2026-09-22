#!/usr/bin/env python3
"""La machine à états de LIFECYCLE.md — l'état est DÉRIVÉ des rapports de gate (0 token).

    Draft -G0/G1-> Specified -G2-> Architected -(PLAN)-> Planned -G3/G4-> Implemented
          -G5-> Tested -G6/G7-> Evaluated -G8-> Approved
    Depuis tout état : Blocked (gate rouge, classes portées) · Deferred · Cancelled (humain)

Règles appliquées :
  R1  L'état est dérivé, pas déclaré. Un `Status:` écrit dans un fichier au-dessus
      de l'état calculé est `[STATUS_UNBACKED]` : le script l'ÉCRASE (sauf --no-write).
      `Deferred` / `Cancelled` sont des décisions humaines : honorées telles quelles.
  R2  Un hash épinglé par un rapport a bougé -> le rapport est périmé, la gate n'est
      plus franchie, l'état redescend en silence ; WARN `[STATUS_PINNED_HASH_MOVED]`.
  R3  L'état d'une MISSION est le MINIMUM de ses CAPs ; une CAP Blocked bloque la MISSION.
  R4  Une CAP ne peut pas être plus confiante que sa MISSION : `[CONFIDENCE_ESCALATION]`.
  R5  Les bypasses audités (`.sys/.audit/bypasses.jsonl`) sont listés.

Ce que chaque niveau exige (rapports `workspace/.sys/.validation/…`) :
  Specified    G0-{mission} · G1-{mission} (traçabilité) · G1-{cap} pour chaque CAP
  Architected  G2-{mission}.topology / .ir / .budget
  Planned      PLAN-{mission} si présent (revue humaine conditionnelle : son absence ne bloque pas)
  Implemented  G3-{tool}.contracts / .suites pour chaque outil ·
               G4-{retriever} pour chaque retriever
               (sans outil ni retriever : un G3-{mission} ou G4-{mission} explicite)
  Tested       G5-{cap} pour chaque CAP
  Evaluated    G6-{mission} · G7-{mission}.suites / .adversarial
  Approved     G8-{mission}.datasets / .acceptance

Un rapport `ok: false` rend l'artefact Blocked avec ses classes. Un rapport dont
un hash épinglé a bougé ne compte pas (R2).

Usage :
    python .sdda/sdda.py compute-status [--mission 1] [--json] [--no-write] [--require-gate G1]

Exit : 0 · 1 si un `Status:` non étayé a été trouvé ou si `--require-gate` n'est pas satisfaite.
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

from sdda_lib import hashing, markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report, emit  # noqa: E402
from sdda_lib.gate_reports import (  # noqa: E402
    GATE_PARTS, GATE_PARTS_ADVISORY, GLOBAL_ARTIFACT, load_gate_reports,
)
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, load_config, resolve_root  # noqa: E402

LADDER = ("Draft", "Specified", "Architected", "Planned", "Implemented", "Tested", "Evaluated", "Approved")
HUMAN_STATES = ("Deferred", "Cancelled")
RANK = {s: i for i, s in enumerate(LADDER)}
RANK["Blocked"] = -1
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
GATE_NOT_PASSED = {"G0": "MISSION", "G1": "CAP", "G2": "TOPOLOGY", "G3": "TOOL", "G4": "RETRIEVAL", "G5": "AGENT", "G6": "ORCH", "G7": "SAFETY", "G8": "ACCEPTANCE"}


@dataclass
class GateEval:
    """Verdict d'une gate pour un artefact : green | red | stale | absent."""
    gate: str
    artifact: str
    verdict: str
    classes: list[str] = field(default_factory=list)
    stale_keys: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)

    @property
    def green(self) -> bool:
        return self.verdict == "green"


@dataclass
class ArtifactStatus:
    id: str
    kind: str                       # mission | cap
    state: str = "Draft"
    declared: str | None = None
    confidence: str | None = None
    blocked_by: list[str] = field(default_factory=list)
    gates: dict[str, str] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)
    path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "state": self.state, "declared": self.declared, "confidence": self.confidence,
                "blockedBy": self.blocked_by, "gates": self.gates, "stale": self.stale}


# --------------------------------------------------------------------------
# Fraîcheur des hashes épinglés (R2)
# --------------------------------------------------------------------------
def _mission_number(artifact: str) -> int:
    m = re.match(r"^(\d+)-", artifact)
    return int(m.group(1)) if m else 0


def current_hash(root: Path, key: str, artifact: str) -> str | None:
    """Hash courant de la source désignée par une clé épinglée ; None si non résolvable."""
    n = _mission_number(artifact)
    if key == "mission":
        files = sorted(paths.missions_dir(root).glob(f"{n}-*.md"))
        return hashing.sha256_file(files[0]) if files else ""
    if key == "cap":
        p = paths.caps_dir(root) / f"{artifact}.md"
        return hashing.sha256_file(p) if p.is_file() else ""
    if key.startswith("cap:"):
        p = paths.caps_dir(root) / f"{key[4:]}.md"
        return hashing.sha256_file(p) if p.is_file() else ""
    if key == "topology":
        p = paths.topology_dir(root) / f"{n}-topology.md"
        return hashing.sha256_file(p) if p.is_file() else ""
    if key == "topology-mmd":
        p = paths.topology_dir(root) / f"{n}-topology.mmd"
        return hashing.sha256_file(p) if p.is_file() else ""
    if key == "stack":
        p = paths.stack_md_path(root)
        return hashing.sha256_file(p) if p.is_file() else ""
    if key == "ir":
        p = paths.ir_path(root, n)
        if not p.is_file():
            return ""
        try:
            return ir_compiler.ir_identity_hash(ir_compiler.load_ir(p))
        except ValueError:
            return ""
    if key.startswith("prompt:"):
        p = paths.prompts_dir(root) / f"{key[7:]}.system.md"
        return hashing.sha256_file(p) if p.is_file() else ""
    if ":" in key and "/" in key:
        p = paths.resolve_rel(root, key.split(":", 1)[1])
        return hashing.sha256_file(p) if p.is_file() else ""
    if "/" in key:
        p = paths.resolve_rel(root, key)
        return hashing.sha256_file(p) if p.is_file() else ""
    return None


def stale_keys(root: Path, report: dict[str, Any]) -> list[str]:
    out = []
    for key, pinned in sorted((report.get("pinnedHashes") or {}).items()):
        cur = current_hash(root, key, str(report.get("artifact", "")))
        if cur is None:
            continue
        if not cur or not hashing.hashes_match(str(pinned), cur):
            out.append(key)
    return out


# --------------------------------------------------------------------------
# Évaluation d'une gate
# --------------------------------------------------------------------------
class GateIndex:
    def __init__(self, root: Path):
        self.root = root
        self.reports = load_gate_reports(root)
        self._stale: dict[str, list[str]] = {}

    def _for(self, gate: str, artifact: str) -> list[dict[str, Any]]:
        """Les rapports d'une gate pour un artefact — par son nom complet OU son numéro.

        Les validateurs de gate écrivent `artifact` sous deux formes : le stem de
        la MISSION (`1-SupportAssistant`) quand ils la chargent, le NUMÉRO seul
        (`1`) quand ils ne reçoivent que `--mission {n}` — et les neuf appels des
        commandes ne passent que le numéro. Six parts contributives (`calibration`,
        `ownership`, `prompts`, `toolscope`, `pii`, `architecture`, `api`,
        `packaging`) étaient donc écrites sous `G7-1.toolscope.json` et cherchées
        sous `G7-1-SupportAssistant` : présentes sur disque, invisibles pour la
        machine à états. `GATE_PARTS_ADVISORY` prétendait avoir fermé « écrit sur
        disque et lu par personne » ; il l'avait fermé pour la seule G7, que
        `validate_safety_gate` lit lui-même en acceptant les deux formes.

        On accepte donc ici les deux, comme lui. Le numéro seul ne peut désigner
        qu'une MISSION : les CAPs et les outils portent toujours un suffixe.
        """
        number = artifact.split("-", 1)[0] if "-" in artifact else artifact
        aliases = {artifact, number} if number.isdigit() else {artifact}
        return [r for r in self.reports if r.get("gate") == gate and str(r.get("artifact")) in aliases]

    def stale_of(self, report: dict[str, Any]) -> list[str]:
        key = str(report.get("_path"))
        if key not in self._stale:
            self._stale[key] = stale_keys(self.root, report)
        return self._stale[key]

    def evaluate(self, gate: str, artifact: str) -> GateEval:
        ev = GateEval(gate=gate, artifact=artifact, verdict="absent")
        reports = self._for(gate, artifact)
        if not reports:
            return ev
        parts = GATE_PARTS.get(gate)
        wanted = list(parts) if parts else [None]
        # Les parts contributives s'ajoutent SI elles existent : absentes elles
        # ne bloquent pas, rouges elles bloquent. Cf. GATE_PARTS_ADVISORY —
        # c'est ce qui rend un `[SECRET_LEAK]` rouge réellement bloquant, alors
        # qu'il était écrit puis ignoré.
        advisory = GATE_PARTS_ADVISORY.get(gate, ())
        present_advisory = {r.get("part") for r in reports if r.get("part") in advisory}
        # Un rapport global (`scan_secrets` sur toute la stack) vaut pour chaque
        # mission : sans cela il n'est rattaché à rien et redevient invisible.
        global_reports = [r for r in self.reports
                          if r.get("gate") == gate
                          and r.get("artifact") == GLOBAL_ARTIFACT
                          and r.get("part") in advisory]
        reports = reports + global_reports
        present_advisory |= {r.get("part") for r in global_reports}
        wanted += sorted(p for p in advisory if p in present_advisory)

        verdicts = []
        for part in wanted:
            matching = [r for r in reports if (r.get("part") or None) == part]
            if not matching:
                verdicts.append("absent")
                continue
            r = matching[-1]
            ev.reports.append(str(r.get("_path")))
            if not r.get("ok", False):
                verdicts.append("red")
                ev.classes.extend(sorted({e.get("class", "?") for e in r.get("errors", [])}))
                continue
            stale = self.stale_of(r)
            if stale:
                verdicts.append("stale")
                ev.stale_keys.extend(stale)
                continue
            verdicts.append("green")
        if "red" in verdicts:
            ev.verdict = "red"
        elif "absent" in verdicts:
            ev.verdict = "absent"
        elif "stale" in verdicts:
            ev.verdict = "stale"
        else:
            ev.verdict = "green"
        return ev

    def evaluate_all(self, gate: str, artifacts: list[str]) -> GateEval:
        """Une gate sur plusieurs artefacts (ex. G3 sur chaque outil) : le pire l'emporte."""
        if not artifacts:
            return GateEval(gate=gate, artifact="*", verdict="absent")
        evs = [self.evaluate(gate, a) for a in artifacts]
        agg = GateEval(gate=gate, artifact=",".join(artifacts), verdict="green")
        for e in evs:
            agg.classes.extend(e.classes)
            agg.stale_keys.extend(f"{e.artifact}:{k}" for k in e.stale_keys)
            agg.reports.extend(e.reports)
        order = ("red", "absent", "stale", "green")
        agg.verdict = min((e.verdict for e in evs), key=order.index)
        return agg


# --------------------------------------------------------------------------
# Calcul par mission
# --------------------------------------------------------------------------
#: Niveaux dont la gate est conditionnelle : absente, elle ne donne pas l'état
#: mais n'empêche pas de monter plus haut (revue de plan humaine, LIFECYCLE §2).
OPTIONAL_LEVELS = frozenset({"Planned"})


def _climb(levels: list[tuple[str, list[GateEval]]], status: ArtifactStatus) -> None:
    """Monte l'échelle tant que toutes les gates du niveau sont vertes.

    Une gate rouge, à quelque niveau que ce soit, rend l'artefact Blocked avec ses
    classes (elle a été tentée et a échoué). Une gate absente ou périmée arrête
    la montée — sauf pour un niveau optionnel absent, qui est simplement sauté.
    Aucun état n'est jamais accordé sans rapport vert et frais.
    """
    for _, evs in levels:
        for e in evs:
            status.gates[e.gate] = e.verdict
            status.stale.extend(f"{e.gate}:{k}" for k in e.stale_keys)
    reds = [e for _, evs in levels for e in evs if e.verdict == "red"]
    if reds:
        status.state = "Blocked"
        status.blocked_by = sorted({c for e in reds for c in e.classes})
        return
    state = "Draft"
    for level, evs in levels:
        if all(e.green for e in evs):
            state = level
        elif level in OPTIONAL_LEVELS and all(e.verdict == "absent" for e in evs):
            continue
        else:
            break
    status.state = state


def _header(path: Path) -> dict[str, str]:
    return markdown_io.parse_header_fields(markdown_io.read_text(path))


def compute_mission(root: Path, number: int, index: GateIndex) -> tuple[ArtifactStatus, list[ArtifactStatus], dict[str, list[Path]]]:
    missions = sorted(paths.missions_dir(root).glob(f"{number}-*.md"))
    mid = missions[0].stem if missions else f"{number}-?"
    mission = ArtifactStatus(id=mid, kind="mission", path=missions[0] if missions else None)
    if mission.path:
        h = _header(mission.path)
        mission.declared, mission.confidence = h.get("Status"), (h.get("Confidence") or "").lower() or None
    cap_paths = sorted(paths.caps_dir(root).glob(f"{number}-*.md"))
    cap_ids = [p.stem for p in cap_paths]
    tool_ids = [p.name[: -len(".tool.md")] for p in sorted(paths.contracts_dir(root, "tools").glob(f"{number}-*.tool.md"))]
    retr_ids = [p.name[: -len(".retrieval.md")] for p in sorted(paths.contracts_dir(root, "retrieval").glob(f"{number}-*.retrieval.md"))]
    agent_paths = sorted(paths.contracts_dir(root, "agents").glob(f"{number}-*.agent.md"))
    topo_paths = [p for p in (paths.topology_dir(root) / f"{number}-topology.md",) if p.is_file()]

    g0 = index.evaluate("G0", mid)
    g1m = index.evaluate("G1", mid)
    g2 = index.evaluate("G2", mid)
    plan = index.evaluate("PLAN", mid)   # conditionnelle : absente, le niveau Planned est sauté (OPTIONAL_LEVELS)
    if tool_ids or retr_ids:
        g34 = [index.evaluate_all("G3", tool_ids), index.evaluate_all("G4", retr_ids)]
        g34 = [e for e in g34 if e.artifact != "*"]
    else:
        g3m, g4m = index.evaluate("G3", mid), index.evaluate("G4", mid)
        g34 = [g3m if g3m.verdict != "absent" or g4m.verdict == "absent" else g4m]
    g5 = index.evaluate_all("G5", cap_ids)
    g67 = [index.evaluate("G6", mid), index.evaluate("G7", mid)]
    g8 = index.evaluate("G8", mid)

    mission_levels = [
        ("Specified", [g0, g1m] + [index.evaluate("G1", c) for c in cap_ids]),
        ("Architected", [g2]),
        ("Planned", [plan]),
        ("Implemented", g34),
        ("Tested", [g5]),
        ("Evaluated", g67),
        ("Approved", [g8]),
    ]
    _climb(mission_levels, mission)
    mission.gates["G1"] = index.evaluate_all("G1", [mid] + cap_ids).verdict

    caps: list[ArtifactStatus] = []
    for p in cap_paths:
        cap = ArtifactStatus(id=p.stem, kind="cap", path=p)
        h = _header(p)
        cap.declared, cap.confidence = h.get("Status"), (h.get("Confidence") or "").lower() or None
        cap_levels = [
            ("Specified", [index.evaluate("G1", p.stem)]),
            ("Architected", [g2]),
            ("Planned", [plan]),
            ("Implemented", g34),
            ("Tested", [index.evaluate("G5", p.stem)]),
            ("Evaluated", g67),
            ("Approved", [g8]),
        ]
        _climb(cap_levels, cap)
        caps.append(cap)

    # R3 : le parent est le minimum de ses enfants.
    if caps:
        worst = min(caps, key=lambda c: RANK.get(c.state, -1))
        if RANK.get(worst.state, -1) < RANK.get(mission.state, -1):
            mission.state = worst.state
            if worst.state == "Blocked":
                mission.blocked_by = sorted(set(mission.blocked_by) | set(worst.blocked_by))
    elif mission.state != "Blocked":
        mission.state = "Draft"      # sans CAP, rien n'est spécifié
    return mission, caps, {"agents": agent_paths, "topology": topo_paths,
                           "tools": sorted(paths.contracts_dir(root, "tools").glob(f"{number}-*.tool.md")),
                           "retrieval": sorted(paths.contracts_dir(root, "retrieval").glob(f"{number}-*.retrieval.md"))}


# --------------------------------------------------------------------------
# R1 : Status déclaré vs calculé
# --------------------------------------------------------------------------
def reconcile_status(root: Path, path: Path, computed: str, report: Report, *, write: bool) -> str | None:
    """Compare `Status:` du fichier à l'état calculé. Écrase si le fichier prétend plus haut."""
    text = markdown_io.read_text(path)
    declared = markdown_io.parse_header_fields(text).get("Status")
    loc = paths.rel(root, path)
    if declared is None:
        return None
    if declared in HUMAN_STATES:
        return declared
    if declared not in RANK:
        report.warn("STATUS_UNBACKED", f"`Status: {declared}` n'est pas un état de LIFECYCLE.md", "", loc)
        return declared
    if RANK[declared] > RANK.get(computed, -1):
        report.error("STATUS_UNBACKED", f"`Status: {declared}` déclaré, mais les rapports de gate n'étayent que `{computed}`",
                     ("écrasé par le script : " if write else "relancer avec écriture : ") + "l'état est un fait dérivé des gates, jamais une déclaration (R1)", loc)
        if write:
            path.write_text(markdown_io.replace_header_field(text, "Status", computed), encoding="utf-8", newline="\n")
    return declared


def compute_status(root: Path, *, mission: int | None = None, write: bool = True) -> Report:
    report = Report(name="status", target=str(root))
    index = GateIndex(root)
    numbers = [mission] if mission is not None else ir_compiler.mission_numbers(root)
    missions_out: list[dict[str, Any]] = []
    for n in numbers:
        m, caps, files = compute_mission(root, n, index)
        # R1 sur la MISSION, ses CAPs, la topologie et les contrats.
        if m.path:
            declared = reconcile_status(root, m.path, m.state, report, write=write)
            if declared in HUMAN_STATES:
                m.state = declared
        for c in caps:
            if c.path:
                declared = reconcile_status(root, c.path, c.state, report, write=write)
                if declared in HUMAN_STATES:
                    c.state = declared
        for p in [*files["topology"], *files["agents"], *files["tools"], *files["retrieval"]]:
            reconcile_status(root, p, m.state, report, write=write)
        # R2 : hashes épinglés qui ont bougé.
        for key in sorted(set(m.stale) | {k for c in caps for k in c.stale}):
            report.warn("STATUS_PINNED_HASH_MOVED", f"MISSION {m.id} : `{key}` a bougé depuis la validation — rapport périmé, gate non franchie (R2)", "relancer la gate concernée", f"workspace/feats/missions/{m.id}.md")
        # R4 : confiance plafonnée par le parent.
        for c in caps:
            if c.confidence and m.confidence and CONFIDENCE_RANK.get(c.confidence, 0) > CONFIDENCE_RANK.get(m.confidence, 0):
                report.warn("CONFIDENCE_ESCALATION", f"CAP {c.id} `Confidence: {c.confidence}` sous une MISSION `{m.confidence}` : plafonnée à `{m.confidence}` (R4)", "", f"workspace/feats/caps/{c.id}.md")
        missions_out.append({**m.to_dict(), "caps": [c.to_dict() for c in caps]})
    # R5 : bypasses audités.
    audit = paths.audit_dir(root) / "bypasses.jsonl"
    bypasses: list[dict[str, Any]] = []
    if audit.is_file():
        for line in markdown_io.read_text(audit).split("\n"):
            if line.strip():
                try:
                    bypasses.append(json.loads(line))
                except ValueError:
                    continue
    report.data = {"missions": missions_out, "bypasses": bypasses[-10:], "bypassCount": len(bypasses)}
    return report


def gate_passed(report: Report, mission_id: str, gate: str) -> bool:
    for m in report.data.get("missions", []):
        if m["id"] == mission_id or m["id"].split("-", 1)[0] == mission_id:
            return m["gates"].get(gate) == "green"
    return False


# --------------------------------------------------------------------------
# Rendu
# --------------------------------------------------------------------------
def render_tree(report: Report) -> str:
    lines = []
    for m in report.data.get("missions", []):
        tag = f"  [{', '.join(m['blockedBy'])}]" if m["blockedBy"] else ""
        lines.append(f"MISSION {m['id']:<44} {m['state']}{tag}")
        gates = " · ".join(f"{g} {v}" for g, v in sorted(m["gates"].items()))
        lines.append(f"  gates   {gates}")
        for c in m["caps"]:
            ctag = f"  [{', '.join(c['blockedBy'])}]" if c["blockedBy"] else ""
            lines.append(f"  CAP {c['id']:<40} {c['state']}{ctag}")
        if m["stale"]:
            lines.append(f"  périmé  {', '.join(m['stale'])}")
    if report.data.get("bypassCount"):
        lines.append(f"bypasses audités : {report.data['bypassCount']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="État dérivé des rapports de gate (LIFECYCLE R1..R5) — jamais de la ligne Status:")
    p.add_argument("--mission", type=int, default=None)
    p.add_argument("--no-write", action="store_true", help="ne pas écraser les `Status:` non étayés (rapport seul)")
    p.add_argument("--require-gate", default=None, help="exit 1 si cette gate (G0..G8) n'est pas franchie pour la mission")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = compute_status(root, mission=args.mission, write=not args.no_write)
    combined = Report(name="status", target=str(root))
    load_config(root, combined)
    combined.extend(report)
    combined.data = report.data
    if args.require_gate:
        gate = args.require_gate.upper()
        targets = [m["id"] for m in report.data.get("missions", [])]
        for mid in targets:
            if not gate_passed(report, mid, gate):
                fam = GATE_NOT_PASSED.get(gate, gate)
                combined.error(f"{fam}_GATE_NOT_PASSED", f"MISSION {mid} : gate {gate} non franchie (verdict : {next((m['gates'].get(gate, 'absent') for m in report.data['missions'] if m['id'] == mid), 'absent')})",
                               f"relancer la gate {gate} et corriger ses findings", f"workspace/.sys/.validation/{gate}-{mid}*.json")
    if args.json:
        return emit(combined, True)
    print(render_tree(report))
    if combined.findings:
        print()
    return emit(combined, False)


if __name__ == "__main__":
    sys.exit(main())
