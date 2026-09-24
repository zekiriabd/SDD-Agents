#!/usr/bin/env python3
"""TOOL GATE (G3), part `suites` : les tests de contrat L2 réellement JOUÉS — 0 token.

Pourquoi un script à part, et pas `eval-runner --level L2`
-----------------------------------------------------------
Une suite d'outil n'est pas un jeu d'items qu'on note : c'est une liste de CAS
(happy path, chaque erreur déclarée, timeout, cloisonnement de tenant) dont
certains exigent une source dégradée — une copie périmée, indisponible, lente.
`qa-evals` déclare ces cas dans `proof/suites/tool-{n}-{outil}.yaml` ; `qa-tests`
les joue en `pytest` sous `src/{App}/**/tests/`, en relisant la suite. Le runner
d'eval, lui, charge des items JSONL depuis `dataset:` — ici un export brut
(`assets/orders.json`). Il ne trouvait donc rien à jouer, et la part `suites`
de G3 n'avait AUCUN écrivain capable de la rendre verte : le hook
`preflight_tool_gate` bloquait ensuite tout `dev-agent`, sans issue.

Ce script ferme la boucle sans dupliquer les tests :

1. pour chaque outil câblé de l'IR, trouver SA suite L2 (`toolRef`) ;
2. trouver les fichiers de test qui la chargent (le littéral de son `id`) ;
3. les jouer avec `pytest` dans l'application (`uv run`, l'environnement de l'app) ;
4. exiger que chaque cas déclaré soit EXERCÉ — nommé en paramètre d'un test
   (`test_generic[happy-1]`) ou désigné par son id dans le fichier de test —
   et que rien n'échoue ; un `xfail` est un défaut connu, donc un contrat non
   tenu : rouge, pas vert ;
5. écrire `G3-{outil}.suites.json`, épinglé sur la suite, les tests, le code de
   l'outil et le runtime de données : qu'un seul bouge, et la gate est périmée.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import app_name  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, finish, resolve_root  # noqa: E402

LEVEL = "L2"
_SKIP_DIRS = {".venv", "venv", "__pycache__", "build", "dist", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_PARAM_RE = re.compile(r"\[(.+)\]$")


@dataclass
class ToolOutcome:
    tool_id: str
    suite: Path | None = None
    tests: list[Path] = field(default_factory=list)
    passed: int = 0
    failed: list[str] = field(default_factory=list)
    xfailed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    code: list[Path] = field(default_factory=list)


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml  # dépendance déjà requise par les suites

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def find_suites(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    """toolRef -> (fichier, suite) pour les suites L2."""
    out: dict[str, tuple[Path, dict[str, Any]]] = {}
    suites_dir = paths.resolve_rel(root, "workspace/proof/suites")
    for path in sorted(suites_dir.glob("*.yaml")) if suites_dir.is_dir() else []:
        try:
            suite = _load_yaml(path)
        except Exception:  # noqa: BLE001 — une suite illisible est signalée par validate-datasets
            continue
        if str(suite.get("level")) == LEVEL and suite.get("toolRef"):
            out[str(suite["toolRef"])] = (path, suite)
    return out


def test_files(app_dir: Path) -> list[Path]:
    out = []
    for path in sorted(app_dir.rglob("test_*.py")):
        if not any(part in _SKIP_DIRS for part in path.relative_to(app_dir).parts):
            out.append(path)
    return out


def files_playing(suite_id: str, candidates: list[Path]) -> list[Path]:
    needle = re.compile(r"""["']""" + re.escape(suite_id) + r"""["']""")
    return [p for p in candidates if needle.search(p.read_text(encoding="utf-8", errors="replace"))]


def _pytest_command() -> list[str] | None:
    """`uv run` si disponible (l'environnement de l'app), sinon l'interpréteur courant."""
    if os.environ.get("SDDA_TOOL_SUITES_PYTHON"):
        return [os.environ["SDDA_TOOL_SUITES_PYTHON"], "-m", "pytest"]
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "--no-sync", "python", "-m", "pytest"]
    return [sys.executable, "-m", "pytest"]


def run_pytest(app_dir: Path, files: list[Path], junit: Path) -> tuple[int, str]:
    cmd = _pytest_command() + ["-q", "-p", "no:cacheprovider", f"--junitxml={junit}",
                               *[str(f.relative_to(app_dir)) for f in files]]
    proc = subprocess.run(cmd, cwd=app_dir, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=900, check=False)
    return proc.returncode, (proc.stdout or "")[-2000:] + (proc.stderr or "")[-1000:]


def parse_junit(junit: Path) -> list[dict[str, str]]:
    """Un dict par testcase : file (stem), name, outcome ∈ passed|failed|xfailed|skipped."""
    if not junit.is_file():
        return []
    cases = []
    for tc in ET.parse(junit).getroot().iter("testcase"):
        outcome = "passed"
        for child in tc:
            tag = child.tag
            if tag in ("failure", "error"):
                outcome = "failed"
            elif tag == "skipped":
                outcome = "xfailed" if "xfail" in (child.get("type", "") + child.get("message", "")).lower() else "skipped"
        classname = tc.get("classname", "")
        cases.append({"file": classname.rsplit(".", 1)[-1], "name": tc.get("name", ""), "outcome": outcome})
    return cases


def case_ids(suite: dict[str, Any]) -> list[str]:
    return [str(c["id"]) for c in (suite.get("cases") or []) if isinstance(c, dict) and c.get("id")]


def coverage_gaps(ids: list[str], tests: list[Path], results: list[dict[str, str]]) -> list[str]:
    """Les cas qu'aucun test n'exerce : ni paramètre d'un test, ni id cité dans un fichier de test."""
    params: set[str] = set()
    for r in results:
        m = _PARAM_RE.search(r["name"])
        if m:
            params.add(m.group(1))
    sources = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in tests)
    gaps = []
    for cid in ids:
        if cid in params:
            continue
        if re.search(r"""["']""" + re.escape(cid) + r"""["']""", sources):
            continue
        gaps.append(cid)
    return gaps


def tool_code(app_dir: Path, tool: dict[str, Any]) -> list[Path]:
    name = str(tool.get("name") or "")
    out = []
    for sub in ("tools", "data/tools"):
        p = app_dir / sub / f"{name}.py"
        if p.is_file():
            out.append(p)
    envelope = app_dir / "data" / "envelope.py"
    if envelope.is_file():
        out.append(envelope)
    return out


def pins(root: Path, ir_file: Path, outcome: ToolOutcome) -> dict[str, str]:
    pinned = {"ir": ir_compiler.ir_identity_hash(ir_compiler.load_ir(ir_file))}
    for label, files in (("suite", [outcome.suite] if outcome.suite else []), ("tests", outcome.tests),
                         ("code", outcome.code)):
        for f in files:
            pinned[f"{label}:{paths.rel(root, f)}"] = hashing.sha256_file(f)
    return pinned


def run(root: Path, ir_file: Path, report: Report, *, only: set[str], write: bool) -> list[ToolOutcome]:
    ir = ir_compiler.load_ir(ir_file)
    app = app_name(root)
    app_dir = paths.app_dir(root, app)
    suites = find_suites(root)
    candidates = test_files(app_dir) if app_dir.is_dir() else []
    outcomes: list[ToolOutcome] = []

    for tool in ir.get("tools") or []:
        tid = str(tool.get("id"))
        if only and tid not in only and str(tool.get("name")) not in only:
            continue
        outcome = ToolOutcome(tool_id=tid, code=tool_code(app_dir, tool))
        outcomes.append(outcome)
        sub = Report(name="G3.suites", target=tid)
        found = suites.get(tid)
        if not found:
            sub.error("TOOL_CONTRACT_FAILED", f"`{tid}` : aucune suite {LEVEL} (`toolRef: {tid}`) sous proof/suites/",
                      "qa-evals déclare la suite de contrat de chaque outil câblé", tid)
        else:
            outcome.suite, suite = found
            outcome.tests = files_playing(str(suite.get("id")), candidates)
            if not outcome.tests:
                sub.error("TOOL_CONTRACT_FAILED",
                          f"`{tid}` : aucun test ne joue la suite `{suite.get('id')}`",
                          "qa-tests écrit les tests L2 qui chargent cette suite, sous src/{App}/**/tests/",
                          paths.rel(root, outcome.suite))
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    junit = Path(tmp) / "junit.xml"
                    code, tail = run_pytest(app_dir, outcome.tests, junit)
                    results = parse_junit(junit)
                if not results:
                    sub.error("TOOL_CONTRACT_FAILED", f"`{tid}` : pytest n'a rendu aucun résultat (exit {code})",
                              "lancer les tests à la main depuis l'application pour lire l'erreur",
                              tail.strip().splitlines()[-1] if tail.strip() else tid)
                for r in results:
                    label = f"{r['file']}::{r['name']}"
                    if r["outcome"] == "passed":
                        outcome.passed += 1
                    elif r["outcome"] == "failed":
                        outcome.failed.append(label)
                    elif r["outcome"] == "xfailed":
                        outcome.xfailed.append(label)
                    else:
                        outcome.skipped.append(label)
                for label in outcome.failed:
                    sub.error("TOOL_CONTRACT_FAILED", f"`{tid}` : test de contrat rouge — {label}",
                              "corriger l'outil (dev-tools) ou le runtime ; jamais le test pour qu'il passe", label)
                for label in outcome.xfailed:
                    sub.error("TOOL_CONTRACT_FAILED", f"`{tid}` : défaut connu (xfail) — {label}",
                              "un xfail documente un contrat NON tenu : corriger, puis retirer le marqueur", label)
                for label in outcome.skipped:
                    sub.warn("TOOL_CONTRACT_FAILED", f"`{tid}` : test sauté — {label}",
                             "un cas sauté n'est pas un cas prouvé", label)
                outcome.uncovered = coverage_gaps(case_ids(suite), outcome.tests, results)
                for cid in outcome.uncovered:
                    sub.error("TOOL_CONTRACT_FAILED", f"`{tid}` : cas `{cid}` déclaré, exercé par aucun test",
                              "qa-tests : chaque cas de la suite est joué (paramètre ou `case_by_id`)",
                              paths.rel(root, outcome.suite))
        report.findings.extend(sub.findings)
        if write:
            write_gate_report(root, "G3", tid, sub, pins(root, ir_file, outcome), part="suites")
        mark = "🟢" if sub.ok else "🔴"
        report.data.setdefault("lines", []).append(
            f"{mark} {tid} — {outcome.passed} vert(s), {len(outcome.failed)} rouge(s), "
            f"{len(outcome.xfailed)} xfail, {len(outcome.uncovered)} cas non exercé(s)")
    return outcomes


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sdda run-tool-suites", description=__doc__.splitlines()[0])
    p.add_argument("--mission", type=int, default=None, help="numéro de mission ; défaut : l'unique IR compilé")
    p.add_argument("--tool", action="append", default=[], help="identifiant(s) d'outil, répétable")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="G3", target=str(root))
    if args.mission is not None:
        ir_file = paths.ir_path(root, args.mission)
    else:
        candidates = sorted(paths.ir_dir(root).glob("*-system.ir.json"))
        ir_file = candidates[0] if len(candidates) == 1 else root / "__none__"
    if not ir_file.is_file():
        report.error("IR_NOT_FOUND", "IR compilé introuvable : préciser --mission",
                     "python .sdda/sdda.py ir-compiler --mission {n}", str(paths.ir_dir(root)))
        return finish(report, args)
    only = {t for raw in args.tool for t in raw.split(",") if t}
    outcomes = run(root, ir_file, report, only=only, write=not args.no_report)
    report.data["tools"] = [{"tool": o.tool_id, "passed": o.passed, "failed": o.failed, "xfailed": o.xfailed,
                             "skipped": o.skipped, "uncovered": o.uncovered} for o in outcomes]
    if not args.json:
        for line in report.data.get("lines", []):
            print(line)
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
