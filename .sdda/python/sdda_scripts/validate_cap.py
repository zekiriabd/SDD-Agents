#!/usr/bin/env python3
"""G1 — CAP GATE (0 token). LE contrôle central du framework (P2).

Chaque AC d'une CAP doit nommer : metric + threshold + dataset + grader + runs.
Sinon `[AC_NOT_EVALUABLE]` (invariant `cap-ac-must-be-evaluable`). Vérifie aussi
le hash de la MISSION parente, la traçabilité montante (chaque BR/AC de la
MISSION couvert par >= 1 CAP, sinon `[TRACEABILITY_GAP]`) et la granularité.

Usage :
    python validate_cap.py workspace/caps/1-2-ExplainInvoiceLine.md [--json]
    python validate_cap.py            # toutes les CAPs, traçabilité par mission

Rapports : `G1-{capId}.json` par CAP, `G1-{missionId}.json` pour la traçabilité.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import LayeredConfig  # noqa: E402
from sdda_scripts._common import add_common_args, finish, load_config, resolve_root  # noqa: E402
from sdda_scripts.validate_mission import MissionSpec, load_mission  # noqa: E402

CAP_ID_RE = re.compile(r"^(\d+)-(\d+)-([A-Za-z0-9]+)$")
GRADERS = ("exact", "regex", "schema", "numeric-tolerance", "semantic-similarity", "llm-judge", "trajectory", "cost", "latency")
AC_REQUIRED_FIELDS = ("metric", "threshold", "dataset", "grader", "runs")
_METRIC_RE = re.compile(r"^[a-z][a-z0-9_@./-]*$")
_THRESHOLD_RE = re.compile(r"^(>=|<=|>|<|==|=)?\s*-?\d+(\.\d+)?\s*%?(\s+.*)?$")
# Le groupe couvre l'identifiant ENTIER : `findall` renvoie ce que capture le
# groupe, et `(BR|AC)-\d+` rendrait "BR" au lieu de "BR-1" — la traçabilité
# deviendrait fausse en silence, ce qui est le pire mode d'échec ici.
_COVER_RE = re.compile(r"\b((?:BR|AC)-\d+)\b")


@dataclass
class AcSpec:
    id: str
    fields: dict[str, str]

    @property
    def threshold_value(self) -> float | None:
        m = re.search(r"-?\d+(\.\d+)?", self.fields.get("threshold", ""))
        return float(m.group(0)) if m else None

    @property
    def runs(self) -> int | None:
        m = re.match(r"^\s*(\d+)", self.fields.get("runs", ""))
        return int(m.group(1)) if m else None


@dataclass
class CapSpec:
    id: str
    number: int
    index: int
    name: str
    header: dict[str, str]
    parent_mission: str
    parent_hash: str
    criticality: str
    statement: str
    acs: list[AcSpec]
    covers: list[str]
    allocated: dict[str, list[str]]
    hash: str
    path: Path | None = None
    text: str = ""
    sections_missing: list[str] = field(default_factory=list)


REQUIRED_SECTIONS = ("Statement", "Acceptance Criteria", "Covers", "Inputs / Outputs", "Failure Behavior")


def parse_cap(text: str, path: Path | None = None) -> CapSpec:
    header = markdown_io.parse_header_fields(text)
    cid = header.get("ID", "").strip()
    m = CAP_ID_RE.match(cid)
    number, index, name = (int(m.group(1)), int(m.group(2)), m.group(3)) if m else (0, 0, cid)

    def sec(title: str) -> str | None:
        return markdown_io.section_body(text, title)

    acs = []
    for block in markdown_io.parse_nested_list(sec("Acceptance Criteria") or ""):
        if re.match(r"^AC-\d+$", block["key"]):
            acs.append(AcSpec(block["key"], block["fields"]))
    covers: list[str] = []
    for item in markdown_io.parse_bullets(sec("Covers") or ""):
        covers.extend(_COVER_RE.findall(item))
    statement_lines = [l.strip() for l in (sec("Statement") or "").split("\n") if l.strip() and not l.strip().startswith("<")]
    alloc_raw = markdown_io.parse_kv_list(sec("Allocated To") or "")
    allocated = {k.lower(): [a for a in markdown_io.split_code_list(v) if not markdown_io.is_placeholder(a)] for k, v in alloc_raw.items()}
    return CapSpec(
        id=cid, number=number, index=index, name=name, header=header,
        parent_mission=header.get("Parent MISSION", "").strip(),
        parent_hash=header.get("Parent MISSION hash", "").strip(),
        criticality=header.get("Criticality", "normal").strip().lower(),
        statement=statement_lines[0] if statement_lines else "",
        acs=acs, covers=sorted(set(covers), key=_item_key), allocated=allocated,
        hash=hashing.sha256_text(text), path=path, text=text,
        sections_missing=[s for s in REQUIRED_SECTIONS if sec(s) is None],
    )


def _item_key(item: str) -> tuple[str, int]:
    fam, _, num = item.partition("-")
    return fam, int(num) if num.isdigit() else 0


def load_caps_for_mission(root: Path, mission_number: int) -> list[CapSpec]:
    out = []
    for p in sorted(paths.caps_dir(root).glob(f"{mission_number}-*.md")):
        out.append(parse_cap(markdown_io.read_text(p), p))
    return out


# --------------------------------------------------------------------------
# Validation d'un AC — le cœur de P2
# --------------------------------------------------------------------------
def ac_problems(ac: AcSpec, config: LayeredConfig | None, criticality: str) -> tuple[list[str], list[str]]:
    """(problèmes bloquants -> [AC_NOT_EVALUABLE], avertissements)."""
    problems: list[str] = []
    warnings: list[str] = []
    f = ac.fields
    missing = [k for k in AC_REQUIRED_FIELDS if markdown_io.is_placeholder(f.get(k))]
    if missing:
        problems.append(f"champ(s) manquant(s) ou non renseigné(s) : {', '.join(missing)}")
    if "metric" not in missing and not _METRIC_RE.match(f["metric"].strip()):
        problems.append(f"metric `{f['metric']}` n'est pas un identifiant de métrique (ex. groundedness, routing_accuracy, recall@k)")
    if "threshold" not in missing and not _THRESHOLD_RE.match(f["threshold"].strip()):
        problems.append(f"threshold `{f['threshold']}` n'est pas un seuil chiffré (ex. `>= 0.85`)")
    if "grader" not in missing and f["grader"].strip().lower() not in GRADERS:
        problems.append(f"grader `{f['grader']}` hors de la liste close {list(GRADERS)}")
    if "dataset" not in missing and not f["dataset"].strip().startswith("workspace/datasets/"):
        problems.append(f"dataset `{f['dataset']}` doit être un chemin sous workspace/datasets/")
    if "runs" not in missing:
        runs = ac.runs
        if runs is None or runs < 1:
            problems.append(f"runs `{f['runs']}` doit être un entier >= 1")
        else:
            key = "EvalRunsCritical" if criticality == "critical" else "EvalRuns"
            default = 5 if criticality == "critical" else 3
            required = config.get_int(key, default) if config else default
            if runs < required:
                warnings.append(f"runs={runs} < {key}={required} : un run vert n'est pas une preuve (P3)")
    if f.get("grader", "").strip().lower() == "llm-judge" and markdown_io.is_placeholder(f.get("calibration")):
        warnings.append("grader llm-judge sans `calibration:` déclarée — le compilateur utilisera workspace/evals/calibration/{metric}.json (P9)")
    return problems, warnings


def validate_cap_text(text: str, *, path: Path | None, root: Path | None, config: LayeredConfig | None, mission: MissionSpec | None = None) -> tuple[Report, CapSpec]:
    spec = parse_cap(text, path)
    loc = paths.rel(root, path) if (root and path) else (str(path) if path else "<texte>")
    report = Report(name="G1", target=spec.id or loc)
    report.data = {"capId": spec.id, "hash": spec.hash, "covers": spec.covers, "acs": [a.id for a in spec.acs]}

    # En-tête ----------------------------------------------------------------
    if not CAP_ID_RE.match(spec.id):
        report.error("CAP_ID_MISMATCH", f"`ID: {spec.id or '<absent>'}` ne respecte pas `{{n}}-{{m}}-{{Name}}`", "écrire `ID: 1-2-ExplainInvoiceLine`", loc)
    elif path is not None and path.stem != spec.id:
        report.error("CAP_ID_MISMATCH", f"le fichier `{path.name}` ne porte pas l'id déclaré `{spec.id}`", f"renommer en `{spec.id}.md`", loc)
    if spec.criticality not in ("normal", "critical"):
        report.error("CAP_INCOMPLETE", f"`Criticality: {spec.criticality}` attendu normal|critical", "", loc)
    for s in spec.sections_missing:
        hint = markdown_io.similar_headings(text, s)
        fix = f"retirer l'annotation du titre `## {hint[0]}` -> `## {s}`" if hint else f"ajouter `## {s}` (voir templates/capability.template.md)"
        report.error("CAP_INCOMPLETE", f"section `## {s}` introuvable", fix, loc)
    if markdown_io.is_placeholder(spec.statement):
        report.error("CAP_INCOMPLETE", "Statement vide", "écrire « Le système doit pouvoir <action observable> »", loc)

    # MISSION parente ----------------------------------------------------------------
    if root is not None and mission is None and spec.parent_mission:
        mission = load_mission(root, spec.parent_mission)
    if not spec.parent_mission or markdown_io.is_placeholder(spec.parent_mission):
        report.error("CAP_PARENT_MISSING", "`Parent MISSION:` absent", "référencer l'id de la MISSION parente", loc)
    elif root is not None and mission is None:
        report.error("CAP_PARENT_MISSING", f"MISSION parente `{spec.parent_mission}` introuvable dans workspace/missions/", "corriger `Parent MISSION:` ou créer la MISSION", loc)
    if mission is not None:
        if spec.number and mission.number != spec.number:
            report.error("CAP_PARENT_MISSING", f"l'id `{spec.id}` n'appartient pas à la MISSION {mission.number}", "le préfixe de la CAP doit être le numéro de sa MISSION", loc)
        if not hashing.is_hash_ref(spec.parent_hash):
            report.error("CAP_PARENT_HASH_STALE", f"`Parent MISSION hash: {spec.parent_hash or '<absent>'}` n'est pas un hash `sha256:…`",
                         f"écrire `Parent MISSION hash: {hashing.short(mission.hash)}`", loc)
        elif not hashing.hashes_match(spec.parent_hash, mission.hash):
            report.error("CAP_PARENT_HASH_STALE", f"la MISSION a changé sous la CAP (épinglé {spec.parent_hash}, courant {hashing.short(mission.hash)})",
                         f"relire la MISSION, ajuster la CAP, puis épingler `Parent MISSION hash: {hashing.short(mission.hash)}`", loc)

    # Acceptance Criteria — P2 --------------------------------------------------------
    if "Acceptance Criteria" not in spec.sections_missing:
        if not spec.acs:
            report.error("AC_NOT_EVALUABLE", "aucun AC structuré (`- AC-1:` + metric/threshold/dataset/grader/runs)",
                         "un AC nomme métrique, seuil, dataset, grader et k runs — sinon ce n'est pas un critère", loc)
        seen: set[str] = set()
        for ac in spec.acs:
            if ac.id in seen:
                report.error("CAP_INCOMPLETE", f"{ac.id} déclaré deux fois", "les ids d'AC sont uniques et stables", loc)
            seen.add(ac.id)
            problems, warns = ac_problems(ac, config, spec.criticality)
            for p in problems:
                report.error("AC_NOT_EVALUABLE", f"{ac.id} : {p}",
                             "réécrire l'AC : `metric`, `threshold` (ex. `>= 0.85`), `dataset` (workspace/datasets/…), `grader` (liste close), `runs` (>= 3, 5 si critical)", loc)
            for w in warns:
                cls = "CAP_RUNS_INSUFFICIENT" if w.startswith("runs=") else "JUDGE_CALIBRATION_UNDECLARED"
                report.warn(cls, f"{ac.id} : {w}", "", loc)
            ds = ac.fields.get("dataset", "")
            if root is not None and ds.startswith("workspace/datasets/") and not paths.resolve_rel(root, ds).is_file():
                report.warn("EVAL_DATASET_NOT_FOUND", f"{ac.id} : dataset `{ds}` absent sur disque (attendu avant G5)", "", loc)

    # Covers -----------------------------------------------------------------------------
    if "Covers" not in spec.sections_missing:
        if not spec.covers:
            report.error("CAP_INCOMPLETE", "Covers vide : la CAP ne trace vers aucun BR/AC de la MISSION", "lister `- BR-i` / `- AC-i`", loc)
        elif mission is not None:
            unknown = [c for c in spec.covers if c not in mission.items]
            if unknown:
                report.error("CAP_COVERS_UNKNOWN_ITEM", f"Covers référence {unknown}, absent(s) de la MISSION {mission.id} (déclarés : {mission.items})",
                             "corriger l'id ou ajouter l'élément à la MISSION", loc)
    return report, spec


# --------------------------------------------------------------------------
# Traçabilité montante et granularité (niveau MISSION)
# --------------------------------------------------------------------------
def validate_traceability(mission: MissionSpec, caps: list[CapSpec], config: LayeredConfig | None) -> Report:
    report = Report(name="G1", target=mission.id)
    covered: dict[str, list[str]] = defaultdict(list)
    for cap in caps:
        for item in cap.covers:
            covered[item].append(cap.id)
    gaps = [i for i in mission.items if i not in covered]
    if gaps:
        report.error("TRACEABILITY_GAP", f"MISSION {mission.id} : {gaps} couvert(s) par aucune CAP",
                     "ajouter l'élément au `## Covers` d'une CAP existante, ou créer la CAP qui le porte", f"workspace/missions/{mission.id}.md")
    n = len(caps)
    hard = config.get_int("CapGranularityHardCap", 15) if config else 15
    warn_at = config.get_int("CapGranularityWarnAt", 8) if config else 8
    if n > hard:
        report.error("CAP_GRANULARITY_EXCEEDED", f"{n} CAPs pour la MISSION {mission.id} (> CapGranularityHardCap={hard})",
                     "regrouper ou scinder la MISSION", f"workspace/caps/{mission.number}-*.md")
    elif n > warn_at:
        report.warn("CAP_GRANULARITY_HIGH", f"{n} CAPs pour la MISSION {mission.id} (> CapGranularityWarnAt={warn_at})", "", "")
    report.data = {"missionId": mission.id, "capCount": n, "coverage": dict(sorted(covered.items()))}
    return report


def validate_caps(root: Path, files: list[Path], config: LayeredConfig | None, *, write_report: bool = True) -> Report:
    combined = Report(name="G1", target=str(root))
    missions: dict[str, MissionSpec | None] = {}
    by_mission: dict[int, list[CapSpec]] = defaultdict(list)
    for f in files:
        text = markdown_io.read_text(f)
        pre = parse_cap(text, f)
        if pre.parent_mission not in missions:
            missions[pre.parent_mission] = load_mission(root, pre.parent_mission) if pre.parent_mission else None
        report, spec = validate_cap_text(text, path=f, root=root, config=config, mission=missions[pre.parent_mission])
        combined.extend(report)
        if write_report and CAP_ID_RE.match(spec.id):
            pinned = {"cap": spec.hash}
            m = missions[pre.parent_mission]
            if m:
                pinned["mission"] = m.hash
            write_gate_report(root, "G1", spec.id, report, pinned)
        if spec.number:
            by_mission[spec.number].append(spec)
    for number in sorted(by_mission):
        mission = next((m for m in missions.values() if m and m.number == number), None)
        if mission is None:
            continue
        all_caps = load_caps_for_mission(root, number)  # les frères non passés en argument comptent aussi
        tr = validate_traceability(mission, all_caps, config)
        combined.extend(tr)
        if write_report:
            write_gate_report(root, "G1", mission.id, tr, {"mission": mission.hash, **{f"cap:{c.id}": c.hash for c in all_caps}})
    return combined


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="G1 — CAP GATE : chaque AC nomme metric + threshold + dataset + grader + runs")
    # `--mission {n}` est la forme qu'emploient les commandes : elles ne
    # connaissent qu'un numéro, jamais un chemin. Le positionnel reste pour
    # l'usage manuel et pour les tests.
    p.add_argument("--mission", type=int, default=None, help="numéro de MISSION ; restreint aux fichiers de cette MISSION")
    p.add_argument("files", nargs="*", type=Path, help="fichiers CAP ; défaut : workspace/caps/*.md")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    combined = Report(name="G1", target=str(root))
    config = load_config(root, combined)
    if args.files:
        files = list(args.files)
    elif args.mission is not None:
        files = sorted(paths.caps_dir(root).glob(f"{args.mission}-*.md"))
    else:
        files = sorted(paths.caps_dir(root).glob("*.md"))
    if not files:
        combined.error("CAP_INCOMPLETE", "aucune CAP trouvée", "créer workspace/caps/{n}-{m}-{Name}.md depuis le template", str(paths.caps_dir(root)))
    combined.extend(validate_caps(root, files, config, write_report=not args.no_report))
    return finish(combined, args)


if __name__ == "__main__":
    sys.exit(main())
