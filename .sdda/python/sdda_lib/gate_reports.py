"""Rapports de gate sur disque — la matière première de `compute_status.py`.

Emplacement : `workspace/.sys/.validation/{GATE}-{artefact}[.{part}].json`

    G0-1-SupportAssistant.json            MISSION GATE
    G1-1-2-ExplainInvoiceLine.json        CAP GATE (une par CAP)
    G2-1-SupportAssistant.topology.json   TOPOLOGY GATE, part « topology »
    G2-1-SupportAssistant.ir.json         TOPOLOGY GATE, part « ir »
    G2-1-SupportAssistant.budget.json     TOPOLOGY GATE, part « budget »

Contenu : verdict, findings, et `pinnedHashes` — les hashes des sources
validées. Si l'un bouge, le rapport est périmé (LIFECYCLE R2, P10).
L'audit des bypasses vit dans `workspace/.sys/.audit/bypasses.jsonl` (R5).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any

from sdda_lib import paths
from sdda_lib.errors import Report

_NAME_RE = re.compile(r"^(?P<gate>G[0-8]|PLAN)-(?P<artifact>[A-Za-z0-9-]+?)(?:\.(?P<part>[a-z]+))?\.json$")

#: Parts OBLIGATOIRES d'une gate composite. Une gate composite n'est franchie
#: que si TOUTES ses parts sont vertes : un rapport `G8.datasets` vert seul ne
#: vaut pas une acceptation (le verdict holdout est la part `acceptance`).
#: Une part absente rend la gate `absent`, jamais `green`.
GATE_PARTS: dict[str, tuple[str, ...]] = {
    "G2": ("topology", "ir", "budget"),
    "G3": ("contracts", "suites"),
    # `verdict` est la part de `validate_safety_gate.py`, qui AGRÈGE les autres.
    # Sans elle, G7 pouvait être « verte » alors que rien n'avait confronté les
    # scans aux findings de reviewers : chaque part disait oui de son côté et
    # personne ne rendait de verdict.
    "G7": ("suites", "adversarial", "verdict"),
    "G8": ("datasets", "acceptance"),
}

#: Parts CONTRIBUTIVES : leur absence ne bloque pas, leur ROUGE bloque.
#:
#: Ces rapports étaient écrits sur le disque et **lus par personne** : la
#: sélection ne retenait que `GATE_PARTS`, si bien qu'un `[SECRET_LEAK]` ou un
#: `[PII_IN_INDEX]` rouge laissait l'état monter jusqu'à `Approved`. Un scan de
#: sécurité qui tourne, qui écrit rouge et que la machine à états ignore est
#: exactement le faux vert que ce framework existe pour empêcher.
#:
#: Pourquoi contributives et non obligatoires : ces contrôles sont jouables
#: hors mission (`scan_secrets` couvre toute la stack) et certains n'existent
#: que depuis peu. Les rendre obligatoires ferait redescendre tout projet
#: antérieur sans qu'aucune régression n'ait eu lieu — et on apprendrait à
#: ignorer la redescente. Le rouge, lui, n'a jamais d'excuse.
GATE_PARTS_ADVISORY: dict[str, tuple[str, ...]] = {
    "G2": ("packaging", "architecture"),
    "G3": ("dataaccess",),
    "G5": ("calibration", "ownership", "prompts"),
    # `api` est contributive et non obligatoire parce qu'une surface `cli` ou
    # `batch` ne publie aucun contrat HTTP : l'exiger ferait échouer G6 sur des
    # livrables qui n'ont pas d'API. Son ROUGE, lui, bloque — une API qui a
    # dérivé de l'IR est une promesse rompue à l'appelant.
    "G6": ("api",),
    "G7": ("secrets", "pii", "toolscope"),
}

#: Artefact des rapports qui portent sur le PROJET et non sur une mission —
#: `scan_secrets` scanne la stack entière. Sans cette convention, leur rapport
#: n'est rattachable à aucune mission et redevient invisible.
GLOBAL_ARTIFACT = "stack"


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def report_path(root: Path, gate: str, artifact: str, part: str | None = None) -> Path:
    suffix = f".{part}" if part else ""
    return paths.validation_dir(root) / f"{gate}-{artifact}{suffix}.json"


def write_gate_report(root: Path, gate: str, artifact: str, report: Report, pinned: dict[str, str], part: str | None = None) -> Path:
    payload: dict[str, Any] = {
        "gate": gate,
        "artifact": artifact,
        "ok": report.ok,
        "checkedAt": now_iso(),
        "pinnedHashes": dict(sorted(pinned.items())),
        "errors": [f.to_dict() for f in report.errors],
        "warnings": [f.to_dict() for f in report.warnings],
    }
    if part:
        payload["part"] = part
    path = report_path(root, gate, artifact, part)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_gate_reports(root: Path) -> list[dict[str, Any]]:
    """Tous les rapports lisibles, enrichis de `gate`/`artifact`/`part` depuis le nom."""
    out: list[dict[str, Any]] = []
    vdir = paths.validation_dir(root)
    if not vdir.is_dir():
        return out
    for p in sorted(vdir.glob("*.json")):
        m = _NAME_RE.match(p.name)
        if not m:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        data.setdefault("gate", m.group("gate"))
        data.setdefault("artifact", m.group("artifact"))
        data["part"] = data.get("part") or m.group("part")
        data["_path"] = p.as_posix()
        out.append(data)
    return out


def append_bypass_audit(root: Path, gate: str, reason: str, operator: str | None = None) -> Path:
    """Journalise un bypass (R5) : horodatage, opérateur, raison. Append-only."""
    adir = paths.audit_dir(root)
    adir.mkdir(parents=True, exist_ok=True)
    path = adir / "bypasses.jsonl"
    entry = {
        "at": now_iso(),
        "gate": gate,
        "operator": operator or os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        "reason": reason,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return path
