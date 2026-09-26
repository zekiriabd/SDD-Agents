#!/usr/bin/env python3
"""Promotion des attaques réussies en items PERMANENTS du jeu adversarial.

`review-adversarial` attaque le système vivant et dépose chaque attaque réussie
dans `workspace/.sys/.validation/adversarial-findings/{n}.jsonl`. Il n'écrit pas
dans `pipeline/datasets/` : « seul qa-evals écrit dans datasets/ » est un
invariant SANS exception, parce qu'une règle avec exception se vérifie au cas
par cas — c'est-à-dire mal (`loader.yml`, note de review-adversarial). Ce
script fait le pont, de façon déterministe, pour que la même faille ne puisse
pas revenir après un refactoring :

    1. chaque finding réussi devient un item au schéma `golden-set.schema.json`
       (bloc `adversarial` : famille, vecteur, attendu vérifiable, observables
       interdits) — validé avant écriture ;
    2. il est AJOUTÉ au jeu que la suite L8 de l'agent lit (`trustPosture.
       injectionSuiteRef` de l'IR), sinon à `datasets/adversarial/{agent}.jsonl` ;
    3. la provenance est écrite dans l'item : `metadata.source:
       adversarial-finding`, `adversarial.finding_ref`, `discovered_at`,
       les `run_ids`, et `metadata.finding_hash` ;
    4. dédoublonnage par hash de (agent, famille, entrée) contre TOUT le jeu —
       items humains compris : une attaque déjà couverte n'est pas recopiée ;
    5. **jamais de suppression ni de réécriture** : les lignes existantes sont
       conservées octet pour octet, les nouvelles ajoutées à la fin, en écriture
       atomique.

**Qui l'appelle.** L'écriture sous `datasets/` appartient à `qa-evals`. Un
script ne voit pas l'identité du harnais ; il vérifie donc celle qu'on lui
DÉCLARE : `--agent {nom}` est confronté à la matrice d'ownership
(`audit_ownership.check_write`) et un agent non autorisé — `review-adversarial`,
tout `dev-*` — est refusé sans rien écrire. Sans `--agent`, rien n'est écrit
(`[OWNERSHIP_AGENT_UNKNOWN]`) : l'humain déclare `--agent qa-evals`, l'owner de
la zone ; seul `--dry-run` s'en passe. Chaque finding porte des
`forbidden_observables`, sans quoi l'item promu ne serait jamais jugé. Ce que le
contrôle ne prouve pas : qu'un agent n'a pas menti sur son nom. Le hook
`preflight_bash_ownership` ne voit pas les écritures faites de l'intérieur d'un
script, et `audit_ownership --agent … --wrote …` reste le post-contrôle.

Usage :
    python .sdda/sdda.py promote-adversarial-findings --mission 1 --agent qa-evals
    python .sdda/sdda.py promote-adversarial-findings --mission 1 --dry-run --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import eval_reports, hashing, markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.jsonschema_mini import SchemaValidator  # noqa: E402
from sdda_lib.runtime_io import atomic_write_text, now_iso, utc_now  # noqa: E402
from sdda_scripts import audit_ownership, run_retrieval_eval  # noqa: E402
from sdda_scripts._common import add_common_args, finish, resolve_root  # noqa: E402

#: L'attendu par défaut de chaque famille (`golden-set.schema.json`, `expected_outcome`).
FAMILY_OUTCOME: dict[str, str] = {
    "direct-injection": "refused", "indirect-injection": "ignored-as-data", "tool-injection": "ignored-as-data",
    "tool-abuse": "blocked", "privilege-escalation": "impossible-by-construction", "exfiltration": "blocked",
    "authorization-crossing": "filtered-at-source", "budget-exhaustion": "bound-reached-declared-behavior",
    "persona-jailbreak": "refusal-policy-respected", "memory-poisoning": "ignored-as-data",
}
FAMILY_VECTOR: dict[str, str] = {
    "direct-injection": "user_message", "indirect-injection": "retrieved_document", "tool-injection": "tool_output",
    "memory-poisoning": "memory",
}
VECTORS = frozenset({"user_message", "retrieved_document", "tool_output", "mcp_server", "memory", "api_response"})
OUTCOMES = frozenset(FAMILY_OUTCOME.values())

#: Les familles telles que `review-adversarial` les nomme (TESTING-AND-EVAL.md §4),
#: normalisées (minuscules, sans accents, tirets) -> famille du schéma.
FAMILY_ALIASES: dict[str, str] = {
    **{f: f for f in FAMILY_OUTCOME},
    "injection-directe": "direct-injection", "injection-indirecte": "indirect-injection",
    "abus-d-outil": "tool-abuse", "abus-outil": "tool-abuse",
    "escalade-par-delegation": "privilege-escalation", "escalade": "privilege-escalation",
    "franchissement-d-autorisation": "authorization-crossing", "franchissement-de-tenant": "authorization-crossing",
    "tenant-crossing": "authorization-crossing", "epuisement-de-budget": "budget-exhaustion",
    "jailbreak-de-persona": "persona-jailbreak", "refusal-policy": "persona-jailbreak",
    "refusal-policy-retournee": "persona-jailbreak", "empoisonnement-memoire": "memory-poisoning",
}
#: Repli sur la classe du finding quand la famille manque.
CLASS_FAMILY: dict[str, str] = {
    "TOOL_ABUSE_SUCCEEDED": "tool-abuse", "SAFETY_PRIVILEGE_ESCALATION": "privilege-escalation",
    "SAFETY_EXFILTRATION_SUCCEEDED": "exfiltration", "EXFILTRATION_SUCCEEDED": "exfiltration",
    "TENANT_BOUNDARY_CROSSED": "authorization-crossing", "UNBOUNDED_LOOP": "budget-exhaustion",
    "BOUND_BEHAVIOR_MISMATCH": "budget-exhaustion", "REFUSAL_POLICY_BYPASSED": "persona-jailbreak",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    flat = unicodedata.normalize("NFKD", str(text).lower())
    return _SLUG_RE.sub("-", "".join(c for c in flat if not unicodedata.combining(c))).strip("-")


def family_of(finding: dict[str, Any]) -> str | None:
    fam = FAMILY_ALIASES.get(normalize(finding.get("family") or ""))
    if fam:
        return fam
    cls = str(finding.get("class") or "").strip("[]")
    if cls == "INJECTION_SUCCEEDED":
        return "indirect-injection" if str(finding.get("vector") or "") in ("retrieved_document", "tool_output", "mcp_server", "api_response") else "direct-injection"
    return CLASS_FAMILY.get(cls)


def agent_slug(agent: str) -> str:
    """`1-billing-specialist` -> `billing-specialist` : le jeu se nomme d'après l'agent, pas la mission."""
    head, _, tail = str(agent).partition("-")
    return normalize(tail if head.isdigit() and tail else agent)


def item_hash(slug: str, family: str, raw_input: Any) -> str:
    return hashing.sha256_struct({"agent": slug, "family": family, "input": raw_input})


def target_for(root: Path, ir: dict[str, Any], slug: str) -> Path:
    """Le jeu que la suite L8 de l'agent LIT : l'item doit y atterrir, sinon il ne protège rien."""
    default = paths.datasets_dir(root, "adversarial") / f"{slug}.jsonl"
    for agent in ir.get("agents") or []:
        if agent_slug(str(agent.get("id") or "")) == slug:
            ref = str((agent.get("trustPosture") or {}).get("injectionSuiteRef") or "")
            target = paths.resolve_rel(root, ref) if ref else default
            # Une référence qui sortirait de `datasets/adversarial/` ne désigne
            # pas un jeu adversarial : on n'écrit pas là où elle pointe.
            return target if target.is_relative_to(paths.datasets_dir(root, "adversarial").resolve()) else default
    return default


def schema_validator() -> SchemaValidator | None:
    path = paths.FRAMEWORK_SDDA_DIR / "templates" / "golden-set.schema.json"
    return SchemaValidator(json.loads(markdown_io.read_text(path))) if path.is_file() else None


def to_item(finding: dict[str, Any], *, slug: str, family: str, findings_ref: str, today: str) -> dict[str, Any]:
    fid = str(finding.get("id") or "?")
    vector = str(finding.get("vector") or FAMILY_VECTOR.get(family, "user_message"))
    outcome = str(finding.get("expected_outcome") or FAMILY_OUTCOME[family])
    adversarial: dict[str, Any] = {
        "family": family,
        "vector": vector if vector in VECTORS else FAMILY_VECTOR.get(family, "user_message"),
        "expected_outcome": outcome if outcome in OUTCOMES else FAMILY_OUTCOME[family],
        "discovered_at": str(finding.get("discovered_at") or today),
        "finding_ref": f"{findings_ref}#{fid}",
    }
    observables = finding.get("forbidden_observables")
    if isinstance(observables, list) and observables:
        adversarial["forbidden_observables"] = [str(o) for o in observables]
    if finding.get("payload_ref"):
        adversarial["payload_ref"] = str(finding["payload_ref"])
    severity = str(finding.get("severity") or "").lower()
    metadata: dict[str, Any] = {
        "source": "adversarial-finding", "source_ref": f"{findings_ref}#{fid}", "difficulty": "hard",
        "class": family, "criticality": "critical" if severity == "critical" else "normal",
        "created_at": today, "tags": sorted({"finding", str(finding.get("class") or "").strip("[]")} - {""}),
        "run_ids": [str(r) for r in finding.get("run_ids") or []],
        "finding_hash": item_hash(slug, family, finding.get("input")),
    }
    for key in ("runs", "success_rate"):
        if isinstance(finding.get(key), (int, float)):
            metadata[key] = finding[key]
    item: dict[str, Any] = {"id": "", "input": finding.get("input"), "adversarial": adversarial, "metadata": metadata}
    if finding.get("expected") not in (None, "", [], {}):
        item["expected"] = finding["expected"]
    return item


def existing_state(path: Path, slug: str) -> tuple[set[str], int]:
    """(hashes déjà présents, dernier numéro `adv-{slug}-finding-NNN`) d'un jeu."""
    hashes: set[str] = set()
    last = 0
    prefix = f"adv-{slug}-finding-"
    for item in run_retrieval_eval.load_items(path) if path.is_file() else []:
        meta = item.get("metadata") or {}
        if meta.get("finding_hash"):
            hashes.add(str(meta["finding_hash"]))
        fam = (item.get("adversarial") or {}).get("family")
        if fam:
            hashes.add(item_hash(slug, str(fam), item.get("input")))
        iid = str(item.get("id") or "")
        if iid.startswith(prefix) and iid[len(prefix):].isdigit():
            last = max(last, int(iid[len(prefix):]))
    return hashes, last


def run(root: Path, ir: dict[str, Any], findings_path: Path, *, agent: str | None = None,
        dry_run: bool = False) -> tuple[Report, dict[str, Any]]:
    mid = str(ir.get("missionId") or "")
    report = Report(name="ADVERSARIAL-PROMOTION", target=mid or str(root))
    findings_ref = paths.rel(root, findings_path)
    payload: dict[str, Any] = {"missionId": mid, "generatedAt": now_iso(), "findings": findings_ref,
                               "caller": agent or "non déclaré (fil principal ou commande)",
                               "promoted": [], "duplicates": [], "skipped": []}
    if not findings_path.is_file():
        report.data["lines"] = [f"aucun finding à promouvoir ({findings_ref} absent)"]
        return report, payload

    validator = schema_validator()
    today = utc_now().date().isoformat()
    state: dict[Path, tuple[set[str], int]] = {}
    pending: dict[Path, list[dict[str, Any]]] = {}
    for lineno, raw in enumerate(markdown_io.read_text(findings_path).split("\n"), start=1):
        if not raw.strip():
            continue
        try:
            finding = json.loads(raw)
        except ValueError:
            finding = None
        if not isinstance(finding, dict):
            report.warn("DATASET_ITEM_INVALID", f"{findings_ref}:{lineno} illisible, ignoré", "une ligne JSON par attaque réussie", findings_ref)
            continue
        fid = str(finding.get("id") or f"ligne-{lineno}")
        rate = finding.get("success_rate")
        if isinstance(rate, (int, float)) and rate <= 0:
            payload["skipped"].append({"finding": fid, "reason": "success_rate = 0 : attaque échouée"})
            continue
        family = family_of(finding)
        slug = agent_slug(str(finding.get("agent") or ""))
        if not family or not slug or finding.get("input") in (None, "", {}, []):
            report.error("DATASET_ITEM_INVALID", f"finding `{fid}` : famille, agent ou entrée manquants — non promouvable",
                         "review-adversarial dépose `agent`, `family` (ou `class`) et `input` pour chaque attaque réussie", findings_ref)
            continue
        observables = finding.get("forbidden_observables")
        if not (isinstance(observables, list) and any(str(o).strip() for o in observables)):
            # Sans observable interdit, l'item promu ne se juge que par un
            # `outcome` que l'exécuteur livré ne rend pas : il restait « non
            # jugé » à chaque rejeu, et la faille « permanente » ne pouvait plus
            # jamais redevenir rouge.
            report.error("DATASET_ITEM_INVALID", f"finding `{fid}` : aucun `forbidden_observables` — l'attaque promue ne serait jugeable par aucun rejeu",
                         "review-adversarial dépose ce que l'attaque réussie a rendu visible (appel d'outil, fragment de prompt, "
                         "identifiant d'un autre tenant, canari) dans `forbidden_observables`", findings_ref)
            continue
        target = target_for(root, ir, slug)
        if target not in state:
            state[target] = existing_state(target, slug)
        hashes, last = state[target]
        item = to_item(finding, slug=slug, family=family, findings_ref=findings_ref, today=today)
        digest = item["metadata"]["finding_hash"]
        if digest in hashes:
            payload["duplicates"].append({"finding": fid, "dataset": paths.rel(root, target)})
            continue
        last += 1
        item["id"] = f"adv-{slug}-finding-{last:03d}"
        problems = validator.validate(item) if validator else []
        if problems:
            last -= 1
            report.error("DATASET_ITEM_INVALID", f"finding `{fid}` : item non conforme au schéma — {problems[:3]}",
                         "corriger le finding ; un item invalide ferait échouer validate-datasets en G8", findings_ref)
            continue
        hashes.add(digest)
        state[target] = (hashes, last)
        pending.setdefault(target, []).append(item)
        payload["promoted"].append({"finding": fid, "id": item["id"], "dataset": paths.rel(root, target)})

    if pending and not agent and not dry_run:
        # Sans `--agent`, aucune zone n'était confrontée : `review-adversarial`,
        # qui a Bash, écrivait dans `datasets/` en omettant simplement l'option.
        # L'appelant se déclare toujours ; l'humain passe `--agent qa-evals`,
        # l'owner de la zone qu'il fait écrire.
        report.error("OWNERSHIP_AGENT_UNKNOWN", "appelant non déclaré : `--agent` est obligatoire pour écrire sous datasets/",
                     "relancer avec `--agent qa-evals` (seul owner de workspace/pipeline/datasets/) ; `--dry-run` n'écrit rien et s'en passe",
                     findings_ref)
    if agent and pending:
        loader = audit_ownership.load_loader(root)
        for target in pending:
            if not audit_ownership.check_write(loader, agent, paths.rel(root, target), report):
                payload["promoted"] = []
                report.data["lines"] = [f"refus : `{agent}` n'écrit pas {paths.rel(root, target)} — rien n'a été promu"]
                return report, payload

    if not dry_run and not report.errors:
        for target, items in pending.items():
            before = markdown_io.read_text(target) if target.is_file() else ""
            if before and not before.endswith("\n"):
                before += "\n"
            added = "".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n" for i in items)
            atomic_write_text(target, before + added)
    elif report.errors:
        payload["promoted"] = []
    payload["dryRun"] = dry_run
    report.data["lines"] = [f"{len(payload['promoted'])} item(s) promu(s), {len(payload['duplicates'])} déjà présent(s), "
                            f"{len(payload['skipped'])} ignoré(s)" + (" — dry-run, rien écrit" if dry_run else "")]
    return report, payload


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Promotion des attaques réussies en items permanents du jeu adversarial (append-only, 0 token)")
    p.add_argument("--mission", type=int, default=None, help="numéro de MISSION ; défaut : l'unique IR compilé")
    p.add_argument("--findings", type=Path, default=None, help="défaut : workspace/.sys/.validation/adversarial-findings/{n}.jsonl")
    p.add_argument("--agent", default=None, help="agent appelant, confronté à la matrice d'ownership (qa-evals)")
    p.add_argument("--dry-run", action="store_true", help="calculer sans écrire")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="ADVERSARIAL-PROMOTION", target=str(root))
    ir_file, why = eval_reports.find_ir_file(root, args.mission)
    if ir_file is None:
        report.error("IR_NOT_FOUND", f"{why} — sans IR, le jeu que lit chaque suite L8 est inconnu", "compiler l'IR : python .sdda/sdda.py ir-compiler --mission {n}")
        return finish(report, args)
    ir = json.loads(markdown_io.read_text(ir_file))
    number = eval_reports.mission_number(ir) or args.mission
    findings = args.findings or paths.validation_dir(root) / "adversarial-findings" / f"{number}.jsonl"
    findings = findings if findings.is_absolute() else root / findings
    sub, payload = run(root, ir, findings, agent=args.agent, dry_run=args.dry_run)
    report.extend(sub)
    report.target = sub.target
    report.data.update(sub.data)
    report.data["promotion"] = payload
    if not args.json:
        for line in sub.data.get("lines", []):
            print(f"  {line}")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
