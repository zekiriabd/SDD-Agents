#!/usr/bin/env python3
"""G0 — MISSION GATE (0 token).

Vérifie qu'une MISSION est SPÉCIFIÉE au sens de LIFECYCLE.md : objectif chiffré
(Metric / Target / Deadline) sans `<à préciser>`, budget d'exécution complet
(P6, invariant `mission-budget-declared`), ground truth, trust boundaries,
failure policy, et cohérence de `## Required Stack` avec `STACK.md`.

Usage :
    python .sdda/sdda.py validate-mission workspace/feats/missions/1-SupportAssistant.md [--json]
    python .sdda/sdda.py validate-mission            # toutes les missions du workspace

Écrit `workspace/.sys/.validation/G0-{missionId}.json` (sauf --no-report).
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import LayeredConfig, active_stacks  # noqa: E402
from sdda_scripts._common import add_common_args, finish, load_config, resolve_root  # noqa: E402

MISSION_ID_RE = re.compile(r"^\d+-[A-Za-z0-9]+$")
REQUIRED_SECTIONS = (
    "Context", "Objective", "Quantified Goal", "Execution Budget", "Ground Truth",
    "Trust Boundaries", "Actors", "Business Rules", "Acceptance Criteria",
    "Failure Policy", "Required Stack", "Out of Scope",
)
BUDGET_KEYS = ("CostPerRunTargetUsd", "CostPerRunHardCapUsd", "LatencyP95TargetMs", "TokenCeilingPerRun")
FAILURE_POLICY_KEYS = ("Hors compétence", "Confiance faible", "Outil indisponible", "Budget atteint")
STATES = ("Draft", "Specified", "Architected", "Planned", "Implemented", "Tested", "Evaluated", "Approved", "Blocked", "Deferred", "Cancelled")
#: clé de `## Required Stack` -> (section STACK.md, catégorie de stack)
STACK_SECTIONS = {
    "language": "Active Language & Runtime",
    "framework": "Active Agent Framework",
    "orchestration": "Active Orchestration Pattern",
    "rag": "Active RAG Pattern",
    "dataaccess": "Active Data Access",
    "serving": "Active Serving Surface",
}
_ITEM_RE = re.compile(r"^(BR|AC)-(\d+)\s*:\s*(.*)$")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
#: Graders admis pour l'objectif de la MISSION — même liste close que les AC de
#: CAP, et pour la même raison : un grader hors liste n'a pas d'implémentation,
#: donc la suite qu'il produirait ne s'exécuterait jamais.
GOAL_GRADERS = ("exact", "regex", "schema", "numeric-tolerance", "semantic-similarity",
                "llm-judge", "trajectory", "cost", "latency")


@dataclass
class MissionSpec:
    id: str
    number: int
    name: str
    header: dict[str, str]
    goal: dict[str, str]
    budget: dict[str, float]
    ground_truth: dict[str, str]
    trust: dict[str, list[str]]
    business_rules: dict[str, str]
    acceptance_criteria: dict[str, str]
    failure_policy: dict[str, str]
    required_stack: dict[str, str]
    hash: str
    path: Path | None = None
    text: str = ""
    sections_missing: list[str] = field(default_factory=list)

    @property
    def items(self) -> list[str]:
        """BR-i et AC-i déclarés — ce que les CAPs doivent couvrir (G1)."""
        return [*self.business_rules, *self.acceptance_criteria]


def _parse_items(body: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in markdown_io.parse_bullets(body or ""):
        m = _ITEM_RE.match(item)
        if m:
            out[f"{m.group(1)}-{m.group(2)}"] = m.group(3).strip()
    return out


def _to_number(value: str | None) -> float | None:
    if value is None or markdown_io.is_placeholder(value):
        return None
    m = _NUM_RE.search(value.replace(",", "."))
    return float(m.group(0)) if m else None


def parse_mission(text: str, path: Path | None = None) -> MissionSpec:
    """Projection structurée d'un fichier MISSION (sans jugement de validité)."""
    header = markdown_io.parse_header_fields(text)
    mid = header.get("MISSION ID", "").strip()
    m = re.match(r"^(\d+)-(.+)$", mid)
    number, name = (int(m.group(1)), m.group(2)) if m else (0, mid)

    def sec(title: str) -> str | None:
        return markdown_io.section_body(text, title)

    budget_raw = markdown_io.parse_kv_list(sec("Execution Budget") or "")
    budget: dict[str, float] = {}
    for k in BUDGET_KEYS:
        v = _to_number(budget_raw.get(k))
        if v is not None:
            budget[k] = v
    trust_raw = markdown_io.parse_kv_list(sec("Trust Boundaries") or "")
    trust = {k.lower(): markdown_io.split_code_list(v) for k, v in trust_raw.items()}
    stack_raw = markdown_io.parse_kv_list(sec("Required Stack") or "")
    return MissionSpec(
        id=mid, number=number, name=name, header=header,
        goal=markdown_io.parse_kv_list(sec("Quantified Goal") or ""),
        budget=budget,
        ground_truth=markdown_io.parse_kv_list(sec("Ground Truth") or ""),
        trust=trust,
        business_rules=_parse_items(sec("Business Rules")),
        acceptance_criteria=_parse_items(sec("Acceptance Criteria")),
        failure_policy=markdown_io.parse_kv_list(sec("Failure Policy") or ""),
        required_stack={k.lower(): v.strip() for k, v in stack_raw.items()},
        hash=hashing.sha256_text(text),
        path=path, text=text,
        sections_missing=[s for s in REQUIRED_SECTIONS if sec(s) is None],
    )


def load_mission(root: Path, mission_id: str) -> MissionSpec | None:
    p = paths.missions_dir(root) / f"{mission_id}.md"
    if not p.is_file():
        return None
    return parse_mission(markdown_io.read_text(p), p)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def validate_mission_text(text: str, *, path: Path | None, root: Path | None, config: LayeredConfig | None) -> tuple[Report, MissionSpec]:
    spec = parse_mission(text, path)
    loc = paths.rel(root, path) if (root and path) else (str(path) if path else "<texte>")
    report = Report(name="G0", target=spec.id or loc)
    report.data = {"missionId": spec.id, "hash": spec.hash, "budget": spec.budget, "items": spec.items}

    # En-tête ---------------------------------------------------------------
    if not MISSION_ID_RE.match(spec.id):
        report.error("MISSION_ID_MISMATCH", f"`MISSION ID: {spec.id or '<absent>'}` ne respecte pas `{{n}}-{{Name}}`",
                     "écrire `MISSION ID: 1-SupportAssistant` (chiffres, tiret, nom alphanumérique)", loc)
    elif path is not None and path.stem != spec.id:
        report.error("MISSION_ID_MISMATCH", f"le fichier `{path.name}` ne porte pas l'id déclaré `{spec.id}`",
                     f"renommer en `{spec.id}.md` ou corriger `MISSION ID:`", loc)
    if spec.header.get("Status", "Draft") not in STATES:
        report.warn("MISSION_INCOMPLETE", f"`Status: {spec.header.get('Status')}` n'est pas un état de LIFECYCLE.md", "", loc)
    if spec.header.get("Confidence", "high").lower() not in ("high", "medium", "low"):
        report.warn("MISSION_INCOMPLETE", f"`Confidence: {spec.header.get('Confidence')}` attendu high|medium|low", "", loc)

    # Sections obligatoires --------------------------------------------------
    for s in spec.sections_missing:
        hint = markdown_io.similar_headings(text, s)
        fix = (f"retirer l'annotation du titre : `## {hint[0]}` -> `## {s}` (les scripts compilent `^##\\s+{s}\\s*$`)"
               if hint else f"ajouter la section `## {s}` (voir templates/mission.template.md)")
        report.error("MISSION_INCOMPLETE", f"section `## {s}` introuvable", fix, loc)

    # Placeholders résiduels ---------------------------------------------------
    residual = markdown_io.find_placeholders(text)
    if residual:
        report.error("MISSION_PLACEHOLDER_RESIDUAL", f"{len(residual)} `<à préciser>` résiduel(s)",
                     "renseigner chaque trou ; G0 refuse une MISSION avec un `<à préciser>`", loc)

    # Quantified Goal ---------------------------------------------------------
    if "Quantified Goal" not in spec.sections_missing:
        for k in ("Metric", "Target", "Deadline"):
            v = spec.goal.get(k)
            if markdown_io.is_placeholder(v):
                report.error("MISSION_GOAL_UNQUANTIFIED", f"Quantified Goal : `{k}` absent ou non renseigné",
                             f"écrire `- {k}: …` avec une valeur réelle", loc)
        target = spec.goal.get("Target")
        if target and not markdown_io.is_placeholder(target) and not _NUM_RE.search(target):
            report.error("MISSION_GOAL_UNQUANTIFIED", f"Quantified Goal : `Target: {target}` ne contient aucun nombre",
                         "une cible est un seuil chiffré, ex. `>= 0.75 sur le holdout`", loc)
        deadline = spec.goal.get("Deadline")
        if deadline and not markdown_io.is_placeholder(deadline) and not re.search(r"\d{4}-(\d{2}-\d{2}|Q[1-4]|\d{2})", deadline):
            report.error("MISSION_GOAL_UNQUANTIFIED", f"Quantified Goal : `Deadline: {deadline}` n'est pas une date (AAAA-MM-JJ)",
                         "écrire une échéance ISO, ex. `2026-12-01`", loc)
        # Le grader de l'objectif — averti ici, bloquant en G8.
        #
        # Une cible chiffrée dit COMBIEN, jamais COMMENT on le mesure. C'est de
        # cette ligne que `ir_compiler` fait naître la suite d'acceptation L9,
        # la seule que `eval_runner` mappe sur la part `acceptance` de G8.
        # Absente, la MISSION reste parfaitement valide et G8 n'a rien à
        # exécuter — un avertissement ici coûte une ligne, la même chose
        # découverte en phase 8 coûte tout le pipeline.
        grader = spec.goal.get("Grader")
        if not grader or markdown_io.is_placeholder(grader):
            report.warn("MISSION_GOAL_UNQUANTIFIED",
                        "Quantified Goal : `Grader` absent — l'objectif est chiffré, sa mesure n'est pas déclarée",
                        f"ajouter `- Grader: <{'|'.join(GOAL_GRADERS)}>` : sans lui, aucune suite L9 n'est compilée "
                        "et la gate d'acceptation reste sans exécution", loc)
        elif grader.strip().lower() not in GOAL_GRADERS:
            report.error("MISSION_GOAL_UNQUANTIFIED", f"Quantified Goal : `Grader: {grader}` hors liste close",
                         f"graders admis : {', '.join(GOAL_GRADERS)}", loc)

    # Execution Budget (P6) -----------------------------------------------------
    if "Execution Budget" not in spec.sections_missing:
        for k in BUDGET_KEYS:
            v = spec.budget.get(k)
            if v is None or v <= 0:
                report.error("MISSION_BUDGET_MISSING", f"Execution Budget : `{k}` absent ou non positif",
                             f"déclarer `- {k}: <nombre > 0>` — le coût d'exécution est une exigence fonctionnelle (P6)", loc)
        t, h = spec.budget.get("CostPerRunTargetUsd"), spec.budget.get("CostPerRunHardCapUsd")
        if t is not None and h is not None and h < t:
            report.error("MISSION_BUDGET_INCOHERENT", f"CostPerRunHardCapUsd ({h}) < CostPerRunTargetUsd ({t})",
                         "le plafond dur doit être >= la cible", loc)
        just = markdown_io.parse_kv_list(markdown_io.section_body(text, "Execution Budget") or "").get("Justification")
        if markdown_io.is_placeholder(just):
            report.warn("MISSION_BUDGET_UNJUSTIFIED", "Execution Budget : `Justification` non renseignée",
                        "dire d'où viennent ces chiffres (modèle économique, SLA, volume)", loc)

    # Ground Truth ---------------------------------------------------------------
    if "Ground Truth" not in spec.sections_missing:
        for k in ("Source", "Owner", "Volume available"):
            if markdown_io.is_placeholder(spec.ground_truth.get(k)):
                report.error("MISSION_GROUND_TRUTH_MISSING", f"Ground Truth : `{k}` absent ou non renseigné",
                             "sans vérité terrain aucune eval n'est possible : nommer la source, son owner et le volume", loc)
        if markdown_io.is_placeholder(spec.ground_truth.get("Gaps")):
            report.warn("MISSION_GROUND_TRUTH_MISSING", "Ground Truth : `Gaps` non renseigné (écrire `aucun` si c'est le cas)", "", loc)

    # Trust Boundaries (P8) ----------------------------------------------------
    if "Trust Boundaries" not in spec.sections_missing:
        raw = markdown_io.parse_kv_list(markdown_io.section_body(text, "Trust Boundaries") or "")
        for k in ("Untrusted", "Trusted"):
            if markdown_io.is_placeholder(raw.get(k)):
                report.error("MISSION_TRUST_BOUNDARIES_MISSING", f"Trust Boundaries : `{k}` absent ou non renseigné",
                             "lister les sources non maîtrisées (ou `aucune`) : chacune impose une suite d'injection", loc)

    # Failure Policy ---------------------------------------------------------------
    if "Failure Policy" not in spec.sections_missing:
        missing = [k for k in FAILURE_POLICY_KEYS if markdown_io.is_placeholder(spec.failure_policy.get(k))]
        if missing:
            report.error("MISSION_FAILURE_POLICY_MISSING", f"Failure Policy : cas non décidé(s) : {', '.join(missing)}",
                         "décider quoi faire pour chaque cas — ne pas décider, c'est décider que le système inventera", loc)

    # BR / AC ---------------------------------------------------------------------
    if "Business Rules" not in spec.sections_missing and not any(not markdown_io.is_placeholder(v) for v in spec.business_rules.values()):
        report.error("MISSION_INCOMPLETE", "Business Rules : aucune règle `BR-i:` renseignée", "écrire au moins une `- BR-1: …`", loc)
    if "Acceptance Criteria" not in spec.sections_missing and not any(not markdown_io.is_placeholder(v) for v in spec.acceptance_criteria.values()):
        report.error("MISSION_INCOMPLETE", "Acceptance Criteria : aucun `AC-i:` renseigné", "écrire au moins un `- AC-1: …`", loc)

    # Required Stack vs STACK.md ---------------------------------------------------
    if "Required Stack" not in spec.sections_missing:
        for k in STACK_SECTIONS:
            if markdown_io.is_placeholder(spec.required_stack.get(k)):
                report.error("MISSION_INCOMPLETE", f"Required Stack : `{k}` non renseigné", f"écrire `- {k}: <stack ou none>`", loc)
        if root is not None:
            if not paths.stack_md_path(root).is_file():
                report.warn("MISSION_STACK_UNVERIFIED", "STACK.md absent : cohérence de Required Stack non vérifiée",
                            "lancer bootstrap.py pour générer workspace/stack/STACK.md")
            else:
                _check_stack(spec, root, report, loc)

    return report, spec


def _check_stack(spec: MissionSpec, root: Path, report: Report, loc: str) -> None:
    for key, heading in STACK_SECTIONS.items():
        wanted_raw = spec.required_stack.get(key)
        if markdown_io.is_placeholder(wanted_raw):
            continue
        wanted = {w.strip().lower() for w in re.split(r"[,+/]| et ", wanted_raw or "") if w.strip()}
        active = {a.lower() for a in active_stacks(root, heading)}
        wanted_effective = {w for w in wanted if w != "none"}
        none_wanted = "none" in wanted
        if none_wanted and (active - {"none", "raw-sdk"}):
            report.error("MISSION_STACK_MISMATCH", f"Required Stack `{key}: none` mais STACK.md active {sorted(active)}",
                         f"aligner `## {heading}` de STACK.md ou la MISSION", loc)
        missing = wanted_effective - active
        if missing:
            report.error("MISSION_STACK_MISMATCH", f"Required Stack `{key}: {wanted_raw}` — non actif dans STACK.md `## {heading}` (actifs : {sorted(active) or 'aucun'})",
                         f"activer ` - .sdda/stacks/{key if key != 'language' else 'lang'}/{sorted(missing)[0]}.md` ou corriger la MISSION", loc)


def validate_mission_file(path: Path, root: Path, config: LayeredConfig | None, *, write_report: bool = True) -> Report:
    text = markdown_io.read_text(path)
    report, spec = validate_mission_text(text, path=path, root=root, config=config)
    if write_report and MISSION_ID_RE.match(spec.id):
        write_gate_report(root, "G0", spec.id, report, {"mission": spec.hash})
    return report


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="G0 — MISSION GATE (déterministe, 0 token)")
    # `--mission {n}` est la forme qu'emploient les commandes : elles ne
    # connaissent qu'un numéro, jamais un chemin. Le positionnel reste pour
    # l'usage manuel et pour les tests.
    p.add_argument("--mission", type=int, default=None, help="numéro de MISSION ; restreint aux fichiers de cette MISSION")
    p.add_argument("files", nargs="*", type=Path, help="fichiers MISSION ; défaut : workspace/feats/missions/*.md")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    combined = Report(name="G0", target=str(root))
    config = load_config(root, combined)
    if args.files:
        files = list(args.files)
    elif args.mission is not None:
        files = sorted(paths.missions_dir(root).glob(f"{args.mission}-*.md"))
    else:
        files = sorted(paths.missions_dir(root).glob("*.md"))
    if not files:
        combined.error("MISSION_INCOMPLETE", "aucune MISSION trouvée", f"créer workspace/feats/missions/{{n}}-{{Name}}.md depuis le template", str(paths.missions_dir(root)))
    for f in files:
        combined.extend(validate_mission_file(f, root, config, write_report=not args.no_report))
    return finish(combined, args)


if __name__ == "__main__":
    sys.exit(main())
