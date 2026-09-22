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
from sdda_scripts._common import add_common_args, finish, load_config, resolve_root  # noqa: E402
from sdda_scripts.ir_compiler import mission_numbers  # noqa: E402
from sdda_scripts.validate_cap import load_caps_for_mission  # noqa: E402

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


def validate_datasets(root: Path, *, mission: int | None = None, config: LayeredConfig | None = None, write_report: bool = True) -> Report:
    report = Report(name="datasets", target=str(root))
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
        report.error("GOLDEN_SET_MISSING", "aucun dataset sous workspace/datasets/{golden,holdout,calibration,adversarial}/", "l'qa-evals produit les jeux avant toute eval", str(paths.datasets_dir(root)))

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
                if ds_ref.startswith("workspace/datasets/holdout/"):
                    report.error("AC_DATASET_IS_HOLDOUT", f"{cap.id} {ac.id} itère sur le holdout `{ds_ref}`", "pointer un jeu golden : le holdout rend le verdict (G8)", f"workspace/caps/{cap.id}.md")
                if ds_ref not in datasets:
                    report.error("EVAL_DATASET_MISSING", f"{cap.id} {ac.id} : dataset `{ds_ref}` introuvable", "produire le jeu (qa-evals) ou corriger le chemin", f"workspace/caps/{cap.id}.md")
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
            pins["mission"] = hashing.sha256_file(missions[0])
        per_mission[n] = pins

    report.data = {
        "datasetHashes": {rel: d.hash for rel, d in sorted(datasets.items())},
        "itemCounts": {rel: len(d.items) for rel, d in sorted(datasets.items())},
        "holdoutOverlaps": overlaps,
        "disjointCheck": mode,
    }
    if write_report:
        for n, pins in per_mission.items():
            missions = sorted(paths.missions_dir(root).glob(f"{n}-*.md"))
            if missions:
                write_gate_report(root, "G8", missions[0].stem, report, pins, part="datasets")
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Datasets : golden ∩ holdout = ∅ par hash, tailles minimales, schéma des items")
    p.add_argument("--mission", type=int, default=None, help="numéro de mission ; défaut : toutes")
    p.add_argument("--freeze", action="store_true", help="compatibilité /sdda-eval : les hashes sont toujours épinglés")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    combined = Report(name="datasets", target=str(root))
    config = load_config(root, combined)
    rep = validate_datasets(root, mission=args.mission, config=config, write_report=not args.no_report)
    combined.extend(rep)
    combined.data = rep.data
    return finish(combined, args)


if __name__ == "__main__":
    sys.exit(main())
