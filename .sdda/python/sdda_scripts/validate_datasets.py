#!/usr/bin/env python3
"""Datasets — disjonction golden/holdout par hash, tailles minimales, schéma des items (0 token).

Invariant `holdout-disjoint-from-golden` (eval-protocol.md §7) : optimiser
contre le jeu qui rend le verdict est la manière agentic de se mentir. La
disjonction se vérifie par **hash du champ `input`** (JSON canonique) : deux
items dont l'entrée est identique sont le même item, quel que soit leur `id`.

Contrôles :
    - chaque ligne JSONL est un objet conforme à `golden-set.schema.json`
      (si le schéma est trouvé, sinon contrôles structurels)   [DATASET_ITEM_INVALID]
    - `id` unique dans un fichier                               [DATASET_DUPLICATE_ID]
    - golden ∩ holdout = ∅ par hash d'`input`                    [HOLDOUT_NOT_DISJOINT]
      (`HoldoutDisjointCheck: warn` -> WARN, audit-loggué)
    - tailles minimales : GoldenSetMinItems, HoldoutSetMinItems,
      CalibrationSetMinItems, AdversarialSetMinItems            [EVAL_DATASET_TOO_SMALL]
    - tout dataset nommé par un AC ou une suite d'injection existe   [EVAL_DATASET_MISSING]
    - aucun AC n'itère sur le holdout                            [AC_DATASET_IS_HOLDOUT]
    - aucun secret, aucune PII non déclarée dans les items       [SECRET_LEAK] [PII_IN_DATASET]
      (motifs de scan_secrets / scan_pii ; `pii_status: present-authorized` déclare)

Le rapport épingle le hash de chaque fichier (`dataset:{chemin}`) : c'est le
`dataset_hash` du tuple P10. `--freeze` est accepté (compatibilité /sdda-eval)
et n'ajoute rien : les hashes sont toujours épinglés.

Usage :
    python .sdda/sdda.py validate-datasets [--mission 1] [--json] [--freeze]

Rapport : `G8-{missionId}.datasets.json` (part `datasets` de la gate G8 ; la
part `acceptance` — le verdict holdout — reste à franchir).
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
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import append_bypass_audit, write_gate_report  # noqa: E402
from sdda_lib.jsonschema_mini import SchemaValidator  # noqa: E402
from sdda_lib.layered_config import LayeredConfig  # noqa: E402
from sdda_scripts import scan_pii, scan_secrets  # noqa: E402
from sdda_scripts._common import add_common_args, finish, load_config, resolve_root  # noqa: E402
from sdda_scripts.ir_compiler import mission_numbers  # noqa: E402
from sdda_scripts.validate_cap import load_caps_for_mission  # noqa: E402

#: `metadata.pii_status` d'un item (golden-set.schema.json) qui DÉCLARE la PII :
#: elle est autorisée par un ADR, et le scan ne la compte pas. Toute autre
#: valeur — `none`, `redacted`, absente — promet qu'il n'y en a pas.
PII_AUTHORIZED = "present-authorized"

KINDS = ("golden", "holdout", "calibration", "adversarial")
#: rôle -> (clé de config, défaut config.base.yml)
MIN_ITEMS_KEYS: dict[str, tuple[str, int]] = {
    "golden": ("GoldenSetMinItems", 50),
    "holdout": ("HoldoutSetMinItems", 30),
    "calibration": ("CalibrationSetMinItems", 50),
    "adversarial": ("AdversarialSetMinItems", 25),
}
MAX_ITEM_ERRORS_PER_FILE = 10


@dataclass
class Dataset:
    path: Path
    rel: str
    kind: str
    hash: str
    items: list[dict[str, Any]] = field(default_factory=list)
    input_hashes: dict[str, list[str]] = field(default_factory=dict)   # hash(input) -> [ids]
    line_errors: list[str] = field(default_factory=list)


def golden_schema_path(root: Path) -> Path | None:
    for p in (root / ".sdda" / "templates" / "golden-set.schema.json", paths.FRAMEWORK_SDDA_DIR / "templates" / "golden-set.schema.json"):
        if p.is_file():
            return p
    return None


def input_hash(item: dict[str, Any]) -> str:
    """Hash canonique du champ `input` — l'identité réelle d'un item."""
    return hashing.sha256_struct(item.get("input"))


def load_dataset(root: Path, path: Path, kind: str) -> Dataset:
    ds = Dataset(path=path, rel=paths.rel(root, path), kind=kind, hash=hashing.sha256_file(path))
    text = markdown_io.read_text(path)
    for lineno, raw in enumerate(text.split("\n"), start=1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except ValueError as exc:
            ds.line_errors.append(f"ligne {lineno} : JSON illisible ({exc})")
            continue
        if not isinstance(item, dict):
            ds.line_errors.append(f"ligne {lineno} : un item est un objet JSON, pas {type(item).__name__}")
            continue
        ds.items.append(item)
        ds.input_hashes.setdefault(input_hash(item), []).append(str(item.get("id", f"ligne {lineno}")))
    return ds


def structural_problems(item: dict[str, Any]) -> list[str]:
    """Repli quand golden-set.schema.json est introuvable : le minimum vital."""
    out = []
    if not isinstance(item.get("id"), str) or not item["id"].strip():
        out.append("`id` absent ou vide")
    if "input" not in item or item["input"] in ("", None, {}, []):
        out.append("`input` absent ou vide")
    if not any(k in item for k in ("expected", "expected_documents", "expected_trajectory", "adversarial")):
        out.append("aucune vérité attendue (expected / expected_documents / expected_trajectory / adversarial)")
    return out


def scan_item_content(root: Path, ds: Dataset, report: Report, *, pii_blocking: bool) -> dict[str, int]:
    """Secrets et PII dans les ITEMS — le contrôle 4 de `/sdda-eval`, promis et joué par personne.

    Un golden set est COMMITÉ : une clé d'API collée dans un `input` d'exemple,
    un e-mail réel dans une vérité terrain construite depuis un export, y
    deviennent publics au premier push. Les motifs sont ceux de `scan_secrets`
    et `scan_pii` — un seul jeu de motifs, deux enforcers, pas deux vérités.

    Secrets : `scan_secrets.scan_file` tel quel (`[SECRET_LEAK]`, la valeur
    jamais recopiée). PII : par ligne, donc par item — un item qui déclare
    `metadata.pii_status: present-authorized` est exclu, c'est la seule forme
    de « PII déclarée » que le schéma des items connaît. `TracePIIPolicy: raw`
    rend le finding non bloquant, comme pour `scan_pii` : une politique qui
    accepte la PII brute en trace ne peut pas la refuser dans le jeu qui
    produit la trace.
    """
    counts = {"secrets": scan_secrets.scan_file(root, ds.path, report), "pii": 0, "piiAuthorized": 0}
    emit = report.error if pii_blocking else report.warn
    for number, line in enumerate(markdown_io.read_text(ds.path).split("\n"), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        status = str(((item.get("metadata") or {}) if isinstance(item, dict) else {}).get("pii_status") or "")
        if status == PII_AUTHORIZED:
            counts["piiAuthorized"] += 1
            continue
        if scan_pii.EXAMPLE_RE.search(line):
            continue
        for label, pattern in scan_pii.COMPILED:
            m = pattern.search(line)
            if not m or (label == "carte bancaire" and not scan_pii._luhn(m.group(0))):
                continue
            counts["pii"] += 1
            iid = item.get("id", f"ligne {number}") if isinstance(item, dict) else f"ligne {number}"
            emit("PII_IN_DATASET", f"{ds.rel}:{number} — {label} dans l'item `{iid}` sans `pii_status: {PII_AUTHORIZED}`",
                 "rediger avant écriture, ou déclarer `metadata.pii_status: present-authorized` sous couvert d'un ADR : "
                 "un jeu construit depuis des données réelles est COMMITÉ, la PII y devient publique", ds.rel)
            break
    return counts


def validate_datasets(root: Path, *, mission: int | None = None, config: LayeredConfig | None = None,
                      write_report: bool = True, require: tuple[str, ...] = (),
                      min_items: int | None = None) -> Report:
    """Valide les jeux. `require` restreint ce qui doit EXISTER, `min_items` le seuil.

    `require` sert les pré-requis de phase : `/sdda-build` vérifie le golden de
    retrieval avant la RETRIEVAL GATE, à un moment où le holdout et le jeu
    adversarial n'existent pas encore et n'ont pas à exister. Sans ce filtre,
    le seul contrôle possible était « tous les jeux, tout de suite », donc
    aucun contrôle de phase — et la commande appelait une option que le script
    n'avait pas, ce qui rendait une erreur argparse au lieu d'une mesure.
    """
    report = Report(name="datasets", target=str(root))
    required_kinds = tuple(k for k in require if k in KINDS)
    unknown = sorted(set(require) - set(KINDS))
    if unknown:
        report.error("EVAL_DATASET_MISSING", f"`--require {','.join(unknown)}` : type de jeu inconnu",
                     f"types admis : {', '.join(KINDS)}", str(paths.datasets_dir(root)))
    schema_path = golden_schema_path(root)
    validator = SchemaValidator(json.loads(markdown_io.read_text(schema_path))) if schema_path else None
    if validator is None:
        report.warn("EVAL_DATASET_MISSING", "golden-set.schema.json introuvable : contrôles structurels seulement", "", str(root))
    mode = str(config.get("HoldoutDisjointCheck", "strict") if config else "strict").lower()

    # Chargement -----------------------------------------------------------------
    datasets: dict[str, Dataset] = {}
    for kind in KINDS:
        for p in sorted(paths.datasets_dir(root, kind).glob("*.jsonl")):
            datasets[paths.rel(root, p)] = load_dataset(root, p, kind)
    if not datasets:
        report.error("GOLDEN_SET_MISSING", "aucun dataset sous workspace/pipeline/datasets/{golden,holdout,calibration,adversarial}/", "l'qa-evals produit les jeux avant toute eval", str(paths.datasets_dir(root)))
    for kind in required_kinds:
        if not any(d.kind == kind for d in datasets.values()):
            report.error("GOLDEN_SET_MISSING", f"aucun jeu `{kind}` sous workspace/pipeline/datasets/{kind}/",
                         f"produire le jeu `{kind}` via qa-evals (`/sdda-eval {{n}} --datasets-only`) : "
                         "il est exigé par la phase en cours, pas par principe",
                         str(paths.datasets_dir(root, kind)))

    # Secrets et PII dans les items -----------------------------------------------------
    pii_blocking = scan_pii.policy_of(root)[0] != "raw"
    content: dict[str, int] = {"secrets": 0, "pii": 0, "piiAuthorized": 0}
    for rel, ds in sorted(datasets.items()):
        for key, n in scan_item_content(root, ds, report, pii_blocking=pii_blocking).items():
            content[key] += n

    # Items -------------------------------------------------------------------------
    for rel, ds in sorted(datasets.items()):
        problems = list(ds.line_errors)
        seen_ids: dict[str, int] = {}
        for item in ds.items:
            iid = str(item.get("id", "?"))
            seen_ids[iid] = seen_ids.get(iid, 0) + 1
            if validator is not None:
                problems.extend(f"item `{iid}` : {v}" for v in validator.validate(item))
            else:
                problems.extend(f"item `{iid}` : {v}" for v in structural_problems(item))
        for p in problems[:MAX_ITEM_ERRORS_PER_FILE]:
            report.error("DATASET_ITEM_INVALID", p, "aligner l'item sur .sdda/templates/golden-set.schema.json", rel)
        if len(problems) > MAX_ITEM_ERRORS_PER_FILE:
            report.error("DATASET_ITEM_INVALID", f"… et {len(problems) - MAX_ITEM_ERRORS_PER_FILE} autre(s) item(s) invalide(s)", "", rel)
        for iid, n in sorted(seen_ids.items()):
            if n > 1:
                report.error("DATASET_DUPLICATE_ID", f"`id: {iid}` apparaît {n} fois", "un id est stable et unique, jamais renuméroté", rel)
        for h, ids in sorted(ds.input_hashes.items()):
            if len(ids) > 1:
                report.warn("DATASET_DUPLICATE_ID", f"items {ids} ont le même `input` (hash {hashing.short(h)})", "", rel)
        key, default = MIN_ITEMS_KEYS[ds.kind]
        minimum = config.get_int(key, default) if config else default
        if ds.kind == "calibration" and config and config.get("CalibrationSetMinItems") is None:
            minimum = config.get_int("JudgeCalibrationMinItems", default)
        # `--min-items` ne s'applique qu'aux types explicitement exigés : relever
        # le seuil du golden ne doit pas relever celui du jeu de calibration,
        # qui répond à une autre contrainte (JudgeCalibrationMinItems).
        if min_items is not None and (not required_kinds or ds.kind in required_kinds):
            minimum = max(minimum, int(min_items))
            key = "--min-items"
        if len(ds.items) < minimum:
            report.error("EVAL_DATASET_TOO_SMALL", f"{len(ds.items)} item(s) < {key}={minimum}", "compléter le jeu : un seuil mesuré sur 5 items n'est pas un seuil", rel)

    # Disjonction golden ∩ holdout par hash d'input -----------------------------------
    goldens = [d for d in datasets.values() if d.kind == "golden"]
    holdouts = [d for d in datasets.values() if d.kind == "holdout"]
    overlaps = 0
    for h in holdouts:
        for g in goldens:
            common = sorted(set(h.input_hashes) & set(g.input_hashes))
            if not common:
                continue
            overlaps += len(common)
            pairs = [f"{g.input_hashes[c][0]}~{h.input_hashes[c][0]}" for c in common[:5]]
            msg = f"{len(common)} item(s) communs (par hash d'input) entre `{g.rel}` et `{h.rel}` : {pairs}"
            fix = "retirer les items du golden (le holdout rend le verdict, il ne s'ajuste pas)"
            if mode == "warn":
                report.warn("HOLDOUT_NOT_DISJOINT", f"{msg} — toléré par HoldoutDisjointCheck: warn (audit-loggué)", fix, h.rel)
                append_bypass_audit(root, "G8.datasets", f"[HOLDOUT_NOT_DISJOINT] HoldoutDisjointCheck: warn — {msg}")
            else:
                report.error("HOLDOUT_NOT_DISJOINT", msg, fix, h.rel)

    # Le holdout de chaque mission -----------------------------------------------------
    #
    # Ce contrôle vivait dans `ir_compiler` et fermait la boucle du pipeline sur
    # elle-même : l'IR se compile en PHASE 2, le holdout naît en PHASE 6a, et
    # `qa-evals` lit l'IR pour savoir quoi produire. Aucune mission neuve ne
    # franchissait G2, donc aucune n'atteignait la phase qui aurait produit le
    # fichier réclamé. Il est ici parce qu'ici il est actionnable : ce script
    # s'exécute APRÈS `qa-evals`, et il écrit la part `datasets` de G8 — la
    # gate à laquelle le jeu de verdict sert réellement.
    for n in ([mission] if mission is not None else mission_numbers(root)):
        if required_kinds and "holdout" not in required_kinds:
            break  # pré-requis de phase : on ne réclame que ce qui est demandé
        candidates = sorted(paths.datasets_dir(root, "holdout").glob(f"mission-{n}-*.jsonl"))
        if not candidates:
            report.error("HOLDOUT_SET_MISSING",
                         f"mission {n} : aucun holdout `workspace/pipeline/datasets/holdout/mission-{n}-*.jsonl`",
                         "produire le jeu de verdict (qa-evals) : sans lui G8 n'a rien à mesurer, "
                         "et un objectif qu'on ne mesure que sur le jeu d'ajustement n'est pas mesuré",
                         "workspace/pipeline/datasets/holdout/")
        elif len(candidates) > 1:
            report.error("HOLDOUT_SET_MISSING",
                         f"mission {n} : {len(candidates)} holdouts candidats ({[p.name for p in candidates]})",
                         "un seul `mission-{n}-v*.jsonl` par mission — deux jeux de verdict, c'est choisir "
                         "le verdict après coup", "workspace/pipeline/datasets/holdout/")

    # Références depuis les CAPs et les contrats d'agents ----------------------------------
    numbers = [mission] if mission is not None else mission_numbers(root)
    per_mission: dict[int, dict[str, str]] = {}
    for n in numbers:
        pins: dict[str, str] = {}
        for cap in load_caps_for_mission(root, n):
            for ac in cap.acs:
                ds_ref = ac.fields.get("dataset", "").strip()
                if not ds_ref or markdown_io.is_placeholder(ds_ref):
                    continue
                if ds_ref.startswith("workspace/pipeline/datasets/holdout/"):
                    report.error("AC_DATASET_IS_HOLDOUT", f"{cap.id} {ac.id} itère sur le holdout `{ds_ref}`", "pointer un jeu golden : le holdout rend le verdict (G8)", f"workspace/pipeline/caps/{cap.id}.md")
                if ds_ref not in datasets:
                    report.error("EVAL_DATASET_MISSING", f"{cap.id} {ac.id} : dataset `{ds_ref}` introuvable", "produire le jeu (qa-evals) ou corriger le chemin", f"workspace/pipeline/caps/{cap.id}.md")
                else:
                    pins[f"dataset:{ds_ref}"] = datasets[ds_ref].hash
        for p in sorted(paths.contracts_dir(root, "agents").glob(f"{n}-*.agent.md")):
            body = markdown_io.section_body(markdown_io.read_text(p), "Posture de confiance") or ""
            ref = markdown_io.strip_code(markdown_io.parse_kv_list(body).get("Suite d'injection", ""))
            if ref and not markdown_io.is_placeholder(ref):
                if ref not in datasets:
                    report.error("EVAL_DATASET_MISSING", f"suite d'injection `{ref}` introuvable", "produire le jeu adversarial (>= AdversarialSetMinItems)", paths.rel(root, p))
                else:
                    pins[f"dataset:{ref}"] = datasets[ref].hash
        for h in holdouts:
            if re.match(rf"^mission-{n}-", h.path.name):
                pins[f"dataset:{h.rel}"] = h.hash
        missions = sorted(paths.missions_dir(root).glob(f"{n}-*.md"))
        if missions:
            pins["mission"] = hashing.sha256_spec_file(missions[0])   # `Status:` exclu (hashing.spec_text)
        per_mission[n] = pins

    report.data = {
        "datasetHashes": {rel: d.hash for rel, d in sorted(datasets.items())},
        "itemCounts": {rel: len(d.items) for rel, d in sorted(datasets.items())},
        "holdoutOverlaps": overlaps,
        "disjointCheck": mode,
        "content": content,
    }
    if write_report:
        for n, pins in per_mission.items():
            missions = sorted(paths.missions_dir(root).glob(f"{n}-*.md"))
            if missions:
                write_gate_report(root, "G8", missions[0].stem, report, pins, part="datasets")
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Datasets : golden et holdout disjoints par hash, tailles minimales, schéma des items, secrets et PII")
    p.add_argument("--mission", type=int, default=None, help="numéro de mission ; défaut : toutes")
    p.add_argument("--freeze", action="store_true", help="compatibilité /sdda-eval : les hashes sont toujours épinglés")
    p.add_argument("--require", default=None,
                   help=f"types de jeu qui DOIVENT exister, séparés par des virgules ({', '.join(KINDS)}). "
                        "Défaut : tous, holdout compris. Restreint aux pré-requis d'une phase")
    p.add_argument("--min-items", type=int, default=None,
                   help="seuil minimal d'items pour les types exigés ; relève le seuil du Project Config, ne l'abaisse jamais")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    combined = Report(name="datasets", target=str(root))
    config = load_config(root, combined)
    require = tuple(k.strip().lower() for k in (args.require or "").split(",") if k.strip())
    rep = validate_datasets(root, mission=args.mission, config=config, write_report=not args.no_report,
                            require=require, min_items=args.min_items)
    combined.extend(rep)
    combined.data = rep.data
    return finish(combined, args)


if __name__ == "__main__":
    sys.exit(main())
