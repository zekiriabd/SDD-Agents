#!/usr/bin/env python3
"""La file des tâches HUMAINES — ce qu'aucun agent n'a le droit de faire (0 token).

Un pipeline agentic se bloque rarement sur du code : il se bloque sur une
décision ou un travail que le framework interdit aux agents, et que personne
n'a listés. Ce script les dérive du disque, pour `/sdda-status` :

    labels    un juge LLM n'a pas ses labels humains (P9 : sans calibration, on
              mesure la complaisance d'un modèle envers un autre)
    roster    le roster n'est pas déclaré, ou pas complet, ou pas valide (P7 :
              l'architecte décide, le framework vérifie — il ne comble rien)
    adr       une décision de STACK.md / Project Config exige un ADR que
              personne n'a écrit (les règles sont celles des documents du
              framework, pas les nôtres — chaque tâche cite sa source)
    findings  une gate G5/G6/G8 est JAUNE : quelqu'un doit assumer (`--force`,
              audité) ou corriger — livrer un jaune est un pari

C'est un LISTING, pas une gate : exit 0 toujours, aucun rapport écrit. Ordre
stable : kind (roster, labels, adr, findings), puis mission, puis référence.

Usage :
    python .sdda/sdda.py human-tasks [--mission 1] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import calibration, markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import load_gate_reports  # noqa: E402
from sdda_lib.layered_config import LayeredConfig, active_stacks, read_stack_section_kv  # noqa: E402
from sdda_scripts import compute_status, ir_compiler, roster  # noqa: E402
from sdda_scripts import validate_architecture as va  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, load_config, resolve_root  # noqa: E402

KIND_ORDER: tuple[str, ...] = ("roster", "labels", "adr", "findings")

#: Gates dont un JAUNE appelle une décision humaine. G2/G3/G4 jaunes sont des
#: avertissements de scripts ; G5/G6/G8 jaunes sont des verdicts d'eval
#: instables (P3) que `/sdda-full --force` assume nominativement.
YELLOW_GATES: tuple[str, ...] = ("G5", "G6", "G8")

#: Où vivent les ADR. `.sys/.context/adrs/` est la zone déclarée (ownership.md,
#: loader.yml, smoke_check) ; `docs/adr/` est celle que citent le gabarit d'ADR
#: et `validate_architecture`. Les deux sont lues : un ADR écrit au mauvais
#: endroit reste un ADR écrit.
ADR_DIRS: tuple[str, ...] = ("workspace/feats/decisions", "workspace/feats/decisions")


def task(kind: str, mission: int | None, title: str, why: str, how: str, *, blocking: bool, ref: str) -> dict[str, Any]:
    return {"kind": kind, "mission": mission, "title": title, "why": why, "how": how, "blocking": bool(blocking), "ref": ref}


# ---------------------------------------------------------------------------
# roster — P7
# ---------------------------------------------------------------------------
def roster_tasks(root: Path, number: int, mission_id: str, index: compute_status.GateIndex) -> list[dict[str, Any]]:
    if index.evaluate("G2", mission_id).verdict == "green":
        return []                                  # l'architecture est validée : rien à déclarer
    target = roster.manifest_path(root, number)
    rel = paths.rel(root, target)
    if not target.is_file():
        topo = paths.topology_dir(root) / f"{number}-topology.md"
        if topo.is_file() and va.Roster.from_markdown(markdown_io.read_text(topo), paths.rel(root, topo)).present:
            return []                              # repli Markdown en place : G2 en jugera
        return [task("roster", number, "Déclarer le roster d'agents",
                     "aucun manifeste de roster : c'est l'architecte qui nomme les agents, leurs rôles et leurs "
                     "outils — le framework les vérifie, il ne les invente pas (P7)",
                     f"/sdda-roster {number} (scaffold pré-rempli), remplir `{rel}`, puis /sdda-topology {number}",
                     blocking=True, ref=rel)]
    report = roster.validate_manifest(root, number)
    holes = int(report.data.get("placeholders") or 0)
    if holes:
        return [task("roster", number, f"Compléter le manifeste de roster ({holes} `<à préciser>`)",
                     "chaque trou est une décision d'architecture ; laissé vide, il serait comblé par un LLM "
                     "au moment de la génération, sans que personne l'ait décidé ni relu (P7)",
                     f"éditer `{rel}`, puis python .sdda/sdda.py roster validate --mission {number}",
                     blocking=True, ref=rel)]
    if not report.ok:
        classes = sorted({f.cls for f in report.errors})
        first = report.errors[0]
        return [task("roster", number, f"Corriger le manifeste de roster ({len(report.errors)} finding(s))",
                     f"[{first.cls}] {first.message}" + (f" · {len(report.errors) - 1} autre(s)" if len(report.errors) > 1 else "")
                     + f" — classes : {', '.join(classes)}",
                     f"éditer `{rel}` puis python .sdda/sdda.py roster validate --mission {number} ; "
                     f"/sdda-topology {number} refuse de démarrer tant que c'est rouge",
                     blocking=True, ref=rel)]
    return []


# ---------------------------------------------------------------------------
# labels — P9
# ---------------------------------------------------------------------------
def _count_human_labels(path: Path) -> int:
    if not path.is_file():
        return 0
    n = 0
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict) and "human" in item and "judge" in item:
            n += 1
    return n


def labels_tasks(root: Path, number: int, config: LayeredConfig) -> list[dict[str, Any]]:
    ir_path = paths.ir_path(root, number)
    if not ir_path.is_file():
        return []                                  # sans IR, les suites ne sont pas encore dérivées
    try:
        ir = ir_compiler.load_ir(ir_path)
    except ValueError:
        return []
    min_items = config.get_int("JudgeCalibrationMinItems", 50)
    out: list[dict[str, Any]] = []
    for suite in (ir.get("evaluation") or {}).get("suites") or []:
        if suite.get("grader") != "llm-judge":
            continue
        sid = str(suite.get("id") or "?")
        ref = str(suite.get("judgeCalibrationRef") or "")
        grader = Path(ref).stem if ref else sid
        report_path = paths.resolve_rel(root, ref) if ref else None
        dataset = calibration.load_calibration_set(report_path) if report_path else None

        labels_ref = f"workspace/proof/datasets/calibration/{grader}-v1.jsonl"
        if report_path and report_path.is_file():
            try:
                declared_ref = json.loads(report_path.read_text(encoding="utf-8-sig")).get("labelsRef")
                if declared_ref:
                    labels_ref = str(declared_ref)
            except (ValueError, AttributeError):
                pass
        on_disk = len(dataset.human) if dataset is not None and not dataset.declared_only else 0
        if not on_disk:
            on_disk = _count_human_labels(paths.resolve_rel(root, labels_ref))
        if on_disk >= min_items:
            continue

        declared_ok = bool(dataset is not None and dataset.declared_only and dataset.declared_items >= min_items)
        advisory = bool(suite.get("advisory"))
        why = (f"la suite `{sid}` (CAP {suite.get('capRef', '?')}) est notée par un llm-judge : sans >= {min_items} "
               f"labels humains sur disque il ne peut pas rendre de verdict bloquant (P9)")
        if declared_ok:
            why += f" ; un accord est déclaré sur {dataset.declared_items} items mais les labels ne sont pas vérifiables"
        if advisory:
            why += " ; la suite est déjà `advisory` : la CAP ne peut pas être 🟢 tant qu'il n'est pas calibré"
        out.append(task("labels", number,
                        f"Labelliser {min_items - on_disk} item(s) pour le juge `{grader}`",
                        why,
                        f"écrire des paires {{id, human, judge}} dans `{labels_ref}` selon la grille exacte du juge "
                        f"(jamais générées par un modèle), puis calibrate_judge.py --grader {grader}",
                        blocking=not advisory and not declared_ok, ref=labels_ref))
    return out


# ---------------------------------------------------------------------------
# adr — les règles des documents du framework, et rien d'autre
# ---------------------------------------------------------------------------
def adr_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for rel in ADR_DIRS:
        d = root / rel
        if d.is_dir():
            out.extend(sorted(d.glob("ADR-*.md")))
    return out


def adr_covers(files: list[Path], needle: str) -> bool:
    """Un ADR « référencé » : un fichier ADR-*.md qui nomme la clé (ou la valeur) contournée."""
    low = needle.lower()
    for p in files:
        try:
            if low in markdown_io.read_text(p).lower():
                return True
        except OSError:
            continue
    return False


def adr_rules(root: Path, config: LayeredConfig) -> list[tuple[str, str, str, str]]:
    """[(clé, valeur, source de la règle, pourquoi)] pour chaque décision active qui exige un ADR."""
    rules: list[tuple[str, str, str, str]] = []

    # 1. config.base.yml `ApiContractFirst` (« `false` … exige un ADR ») ; INVARIANTS.yml
    #    `api-contract-derived-from-ir.bypasses` ; stacks/serving/fastapi-sse.md §6.
    if config.get("ApiContractFirst") is False:
        rules.append(("ApiContractFirst", "false", "config.base.yml ApiContractFirst · INVARIANTS.yml api-contract-derived-from-ir",
                      "l'API peut diverger des schémas de l'IR : la divergence se décide par écrit, elle ne se subit pas"))

    # 2. docs/DATA-ACCESS.md §3 (« `scoped-write` et `full` exigent un ADR ») ;
    #    loader.yml architect-data.writes (« obligatoire si DbAgentRole != readonly »).
    data = read_stack_section_kv(root, "Active Data Access")
    db_type = str(data.get("DatabaseType") or "none").strip().lower()
    role = str(data.get("DbAgentRole") or "").strip().lower()
    if db_type not in ("", "none") and role and role != "readonly":
        rules.append(("DbAgentRole", role, "docs/DATA-ACCESS.md §3 · loader.yml architect-data · agents/architect-data.md",
                      "un agent qui écrit en base est un incident qui attend son heure : le rôle se justifie nominativement"))

    # 3. INVARIANTS.yml `pii-not-in-vector-store.bypasses` ; templates/memory-contract §PII ;
    #    stacks/guardrails/pii-redaction.md.
    memory = str(read_stack_section_kv(root, "Active Memory Strategy").get("MemoryPIIPolicy") or "").strip().lower()
    if memory == "allow":
        rules.append(("MemoryPIIPolicy", "allow", "INVARIANTS.yml pii-not-in-vector-store · stacks/guardrails/pii-redaction.md",
                      "ce qui entre dans un index se retire mal : la base légale et le mécanisme d'effacement se déclarent"))

    # 4. rules/agent-safety.md (« `TracePIIPolicy` : … `raw` (ADR exigé) ») ;
    #    stacks/observability/otel-genai.md ; scan_pii.py [PII_POLICY_PERMISSIVE].
    trace = str(read_stack_section_kv(root, "Active Observability").get("TracePIIPolicy") or config.get("TracePIIPolicy") or "").strip().lower()
    if trace == "raw":
        rules.append(("TracePIIPolicy", "raw", "rules/agent-safety.md · stacks/observability/otel-genai.md",
                      "les PII détectées dans les traces ne bloquent plus : la décision doit être lisible en revue"))

    # 5. registry/architecture-requirements.yml `adr_required: true` (pattern `network`).
    patterns = active_stacks(root, roster.ORCHESTRATION_SECTION)
    if len(patterns) == 1:
        registry = va.load_registry(root, Report(name="adr", target=str(root)))
        _, entry = va.requirements_for(registry, "orchestration", patterns[0]) if registry else ({}, {})
        if entry.get("adr_required"):
            rules.append((roster.ORCHESTRATION_SECTION, patterns[0], "registry/architecture-requirements.yml adr_required",
                          " ".join(str(entry.get("why") or "refusé par défaut : trajectoires et coût non bornables").split())))
    return rules


def adr_tasks(root: Path, mission: int | None, config: LayeredConfig) -> list[dict[str, Any]]:
    files = adr_files(root)
    out: list[dict[str, Any]] = []
    for key, value, source, why in adr_rules(root, config):
        if adr_covers(files, key) or adr_covers(files, value):
            continue
        out.append(task("adr", mission, f"Écrire l'ADR exigé par `{key}: {value}`",
                        f"{why} — règle : {source}",
                        f"écrire `{ADR_DIRS[0]}/ADR-{{YYYYMMDDTHHMM}}-{{slug}}.md` depuis .sdda/templates/adr.template.md, "
                        f"en nommant `{key}` ; ou revenir à la valeur par défaut",
                        blocking=False, ref=f"{ADR_DIRS[0]}/"))
    return out


# ---------------------------------------------------------------------------
# findings — les jaunes de G5/G6/G8
# ---------------------------------------------------------------------------
def findings_tasks(root: Path, number: int, mission_id: str, reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    prefix = f"{number}-"
    for r in reports:
        gate = str(r.get("gate") or "")
        artifact = str(r.get("artifact") or "")
        if gate not in YELLOW_GATES or not (artifact == mission_id or artifact.startswith(prefix)):
            continue
        warnings = [w for w in r.get("warnings") or [] if isinstance(w, dict)]
        if not r.get("ok", False) or not warnings:
            continue                               # rouge = Blocked (compute_status) ; vert sans WARN = rien à assumer
        part = f".{r['part']}" if r.get("part") else ""
        classes = sorted({str(w.get("class") or "?") for w in warnings})
        first = str(warnings[0].get("message") or "")
        rel = paths.rel(root, Path(str(r.get("_path") or "")))
        out.append(task("findings", number, f"Assumer ou corriger le jaune {gate}{part} sur `{artifact}`",
                        f"[{', '.join(classes)}] {first}" + (f" · {len(warnings) - 1} autre(s)" if len(warnings) > 1 else "")
                        + " — un jaune franchi en moyenne mais instable : livrer maintenant est un pari (P3)",
                        f"corriger puis relancer la gate {gate} ; ou l'assumer nominativement par --force "
                        "(SDDA_BYPASS_REASON, journalisé dans workspace/.sys/.audit/bypasses.jsonl)",
                        blocking=True, ref=rel))
    return out


# ---------------------------------------------------------------------------
# Assemblage
# ---------------------------------------------------------------------------
def _sort_key(t: dict[str, Any]) -> tuple:
    return (KIND_ORDER.index(t["kind"]) if t["kind"] in KIND_ORDER else len(KIND_ORDER),
            -1 if t["mission"] is None else int(t["mission"]), str(t["ref"]), str(t["title"]))


def human_tasks(root: Path, *, mission: int | None = None) -> dict[str, Any]:
    config = load_config(root, Report(name="tasks", target=str(root)))
    index = compute_status.GateIndex(root)
    numbers = [mission] if mission is not None else ir_compiler.mission_numbers(root)
    tasks: list[dict[str, Any]] = []
    for n in numbers:
        found = sorted(paths.missions_dir(root).glob(f"{n}-*.md"))
        if not found:
            continue                               # pas de MISSION : rien à dériver — un roster pour rien n'est pas une tâche
        mission_id = found[0].stem
        tasks += roster_tasks(root, n, mission_id, index)
        tasks += labels_tasks(root, n, config)
        tasks += findings_tasks(root, n, mission_id, index.reports)
    # Les ADR portent sur STACK.md / Project Config : une fois par projet, ou
    # rattachés à la MISSION demandée quand il n'y en a qu'une en vue.
    tasks += adr_tasks(root, mission, config)
    tasks.sort(key=_sort_key)
    by_kind = {k: sum(1 for t in tasks if t["kind"] == k) for k in KIND_ORDER}
    return {"tasks": tasks,
            "counts": {"total": len(tasks), "blocking": sum(1 for t in tasks if t["blocking"]), "byKind": by_kind}}


def render(payload: dict[str, Any]) -> str:
    tasks = payload["tasks"]
    if not tasks:
        return "Tâches humaines : aucune."
    lines = [f"Tâches humaines : {payload['counts']['total']} ({payload['counts']['blocking']} bloquante(s))"]
    for t in tasks:
        flag = "⚠️ " if t["blocking"] else "   "
        where = f"MISSION {t['mission']}" if t["mission"] is not None else "projet"
        lines.append(f"  {flag}[{t['kind']:<8}] {where} — {t['title']}")
        lines.append(f"       → {t['how']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="File des tâches humaines dérivée du disque (listing, exit 0, aucun rapport)")
    p.add_argument("--mission", type=int, default=None, help="numéro de MISSION ; défaut : toutes")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    payload = human_tasks(root, mission=args.mission)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(render(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
