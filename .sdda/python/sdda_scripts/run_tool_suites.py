#!/usr/bin/env python3
"""TOOL GATE (G3), part `suites` : les tests de contrat L2 réellement JOUÉS — 0 token.

Pourquoi un script à part, et pas `eval-runner --level L2`
-----------------------------------------------------------
Une suite d'outil n'est pas un jeu d'items qu'on note : c'est une liste de CAS
(happy path, chaque erreur déclarée, timeout, cloisonnement de tenant) dont
certains exigent une source dégradée — une copie périmée, indisponible, lente.
`qa-evals` déclare ces cas dans `pipeline/suites/tool-{n}-{outil}.yaml` ; `qa-tests`
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
import ast
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

from sdda_lib import hashing, markdown_io, paths, yaml_mini  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import app_name  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, finish, resolve_root  # noqa: E402

LEVEL = "L2"
_SKIP_DIRS = {".venv", "venv", "__pycache__", "build", "dist", ".pytest_cache", ".mypy_cache", ".ruff_cache",
              "bin", "obj", "node_modules", ".gradle", "target", "out"}
_PARAM_RE = re.compile(r"\[(.+)\]$")


@dataclass(frozen=True)
class LanguageTests:
    """Comment un langage joue ses tests L2 et où il écrit son rapport JUnit.

    Le script ne savait lancer que pytest : hors Python, la part `suites` de G3
    était rouge par construction (« aucun test ne joue la suite »), et aucun
    agent ne pouvait câbler un outil. Chaque entrée est recopiée dans la fiche
    `eval/*` du langage (section « Contrat d'exécution ») — c'est un contrat.

    Hors Python, la couverture d'un cas se lit dans le rapport JUnit, sans
    parseur de source par langage : le NOM AFFICHÉ d'un test vert contient
    l'id du cas (`[Theory]`/`InlineData`, `it.each`, `@ParameterizedTest(name
    = "{0}")` le produisent tous). Un test `network` est celui dont la classe
    ou le nom contient `network`.
    """

    globs: tuple[str, ...]
    command: tuple[str, ...]          # `{junit}` : fichier (ou répertoire) du rapport
    reports: str                      # glob des rapports, relatif à l'application ; `{junit}` = fichier imposé


LANGUAGE_TESTS: dict[str, LanguageTests] = {
    "csharp": LanguageTests(("*Tests.cs", "*Test.cs"),
                            ("dotnet", "test", "--logger", "junit;LogFilePath={junit}"), "{junit}"),
    "typescript": LanguageTests(("*.test.ts", "*.spec.ts"),
                                ("npx", "--no-install", "vitest", "run", "--reporter=junit", "--outputFile={junit}"),
                                "{junit}"),
    "kotlin": LanguageTests(("*Test.kt", "*Tests.kt"), ("{gradle}", "test", "--console=plain"),
                            "build/test-results/test/*.xml"),
    "java": LanguageTests(("*Test.java", "*Tests.java"), ("{gradle}", "test", "--console=plain"),
                          "build/test-results/test/*.xml"),
}


def active_language(root: Path) -> str:
    from sdda_lib.layered_config import active_stacks  # noqa: PLC0415

    languages = active_stacks(root, "Active Language & Runtime")
    return languages[0] if languages else "python"


@dataclass
class ToolOutcome:
    tool_id: str
    suite: Path | None = None
    tests: list[Path] = field(default_factory=list)
    passed: int = 0
    failed: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)   # tests `network` rouges
    xfailed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)          # connectivité live sautée hors CI
    skipped_contract: list[str] = field(default_factory=list)  # tests de contrat sautés : rouges
    uncovered: list[str] = field(default_factory=list)
    code: list[Path] = field(default_factory=list)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Une suite L2, lue par `yaml_mini` — jamais par PyYAML.

    L'outillage est stdlib seul (`pyproject.toml` n'en déclare aucune
    dépendance). L'import de PyYAML qui vivait ici supposait PyYAML « déjà requis
    par les suites » : il l'était sur la machine du développeur, pas sur un
    clone nu. Là, l'ImportError tombait dans le `except` de `find_suites`, qui
    la prenait pour une suite illisible — toutes les suites disparaissaient, et
    G3 rendait « aucune suite déclarée » au lieu de dire qu'il ne savait pas lire.
    """
    data = yaml_mini.parse(markdown_io.read_text(path))
    return data if isinstance(data, dict) else {}


def find_suites(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    """toolRef -> (fichier, suite) pour les suites L2."""
    out: dict[str, tuple[Path, dict[str, Any]]] = {}
    suites_dir = paths.resolve_rel(root, "workspace/pipeline/suites")
    for path in sorted(suites_dir.glob("*.yaml")) if suites_dir.is_dir() else []:
        try:
            suite = _load_yaml(path)
        except Exception:  # noqa: BLE001 — une suite illisible est signalée par validate-datasets
            continue
        if str(suite.get("level")) == LEVEL and suite.get("toolRef"):
            out[str(suite["toolRef"])] = (path, suite)
    return out


def test_files(app_dir: Path, language: str = "python") -> list[Path]:
    spec = LANGUAGE_TESTS.get(language)
    globs = spec.globs if spec else ("test_*.py",)
    out: set[Path] = set()
    for pattern in globs:
        for path in app_dir.rglob(pattern):
            if not any(part in _SKIP_DIRS for part in path.relative_to(app_dir).parts):
                out.add(path)
    return sorted(out)


def _gradle(app_dir: Path) -> str:
    wrapper = app_dir / ("gradlew.bat" if os.name == "nt" else "gradlew")
    return str(wrapper) if wrapper.is_file() else (shutil.which("gradle") or "gradle")


def run_language_tests(app_dir: Path, language: str, junit: Path) -> tuple[int, str, list[Path]]:
    """Les tests du projet, par l'outil du langage — (code, fin de sortie, rapports JUnit)."""
    spec = LANGUAGE_TESTS[language]
    cmd = [part.replace("{junit}", str(junit)).replace("{gradle}", _gradle(app_dir)) for part in spec.command]
    try:
        proc = subprocess.run(cmd, cwd=app_dir, capture_output=True, text=True, encoding="utf-8",  # noqa: S603
                              errors="replace", timeout=PYTEST_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return -1, f"`{' '.join(cmd[:3])}` interrompu après {PYTEST_TIMEOUT_S} s (timeout)", []
    except OSError as exc:
        return -1, f"`{cmd[0]}` non lancé : {exc}", []
    reports = [junit] if spec.reports == "{junit}" else sorted(app_dir.glob(spec.reports))
    return proc.returncode, (proc.stdout or "")[-2000:] + (proc.stderr or "")[-1000:], reports


def coverage_gaps_by_name(ids: list[str], results: list[dict[str, Any]]) -> list[str]:
    """Hors Python : un cas est exercé si un test VERT porte son id dans son nom affiché."""
    names = [r["name"] for r in results if r["outcome"] == "passed"]
    return [cid for cid in ids if not any(cid in name for name in names)]


def is_network_by_name(result: dict[str, Any]) -> bool:
    text = " ".join([result.get("name", ""), *result.get("classes", [])]).casefold()
    return NETWORK_MARK in text


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


PYTEST_TIMEOUT_S = 900


def run_pytest(app_dir: Path, files: list[Path], junit: Path) -> tuple[int, str]:
    cmd = _pytest_command() + ["-q", "-p", "no:cacheprovider", f"--junitxml={junit}",
                               *[str(f.relative_to(app_dir)) for f in files]]
    try:
        proc = subprocess.run(cmd, cwd=app_dir, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=PYTEST_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        # Le `TimeoutExpired` faisait tomber le script : aucune part G3 écrite,
        # aucun message. Rendu comme un run sans résultat, il devient rouge
        # et le dit.
        return -1, f"pytest interrompu après {PYTEST_TIMEOUT_S} s (timeout)"
    except OSError as exc:
        return -1, f"pytest non lancé : {exc}"
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
        # `classes` : tous les segments (`tests.test_x.TestCase`) — le module
        # n'est pas le dernier segment quand le test vit dans une classe.
        cases.append({"file": classname.rsplit(".", 1)[-1], "classes": classname.split("."),
                      "name": tc.get("name", ""), "outcome": outcome})
    return cases


def case_ids(suite: dict[str, Any]) -> list[str]:
    return [str(c["id"]) for c in (suite.get("cases") or []) if isinstance(c, dict) and c.get("id")]


def _result_matches(result: dict[str, Any], module: str, func: str) -> bool:
    return result["name"].split("[", 1)[0] == func and module in (result.get("classes") or [result["file"]])


def literals_by_test(tests: list[Path]) -> dict[tuple[str, str], set[str]]:
    """`{(module, fonction de test): chaînes littérales du test}` — décorateurs compris.

    Lu dans l'AST, et non par recherche de texte : un id cité dans un COMMENTAIRE,
    ou dans un test qui n'est jamais exécuté, comptait comme cas exercé.
    """
    out: dict[tuple[str, str], set[str]] = {}
    for path in tests:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                out[(path.stem, node.name)] = {c.value for c in ast.walk(node)
                                              if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    return out


def coverage_gaps(ids: list[str], tests: list[Path], results: list[dict[str, Any]]) -> list[str]:
    """Les cas qu'aucun test VERT n'exerce.

    Un cas est exercé s'il est le paramètre d'un test passé (`test_x[happy-1]`),
    ou un littéral d'une fonction de test dont un résultat est passé. Avant, un
    paramètre SAUTÉ ou un id cité n'importe où dans le fichier suffisait : une
    suite entièrement `skip` rendait G3 verte sans qu'aucun cas ait tourné.
    """
    passed = [r for r in results if r["outcome"] == "passed"]
    params: set[str] = set()
    for r in passed:
        m = _PARAM_RE.search(r["name"])
        if m:
            params.add(m.group(1))
    literals = literals_by_test(tests)
    exercised = {lit for (module, func), lits in literals.items()
                 if any(_result_matches(r, module, func) for r in passed) for lit in lits}
    return [cid for cid in ids if cid not in params and cid not in exercised]


#: Le marqueur pytest de la connectivité live (`pytest -m network`, contrat §8 :
#: « connectivité live, marqué `network` »). Le rapport JUnit ne porte pas les
#: marqueurs : on les lit dans la SOURCE des tests, comme la couverture des cas.
NETWORK_MARK = "network"


def _marks(expr: ast.AST) -> set[str]:
    """Les noms `pytest.mark.X` d'une expression (décorateur, ou `pytestmark = …`)."""
    out: set[str] = set()
    for node in ast.walk(expr):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == "mark":
            out.add(node.attr)
    return out


def network_tests(tests: list[Path]) -> set[tuple[str, str]]:
    """`{(module, fonction)}` des tests marqués `network` — par décorateur, ou par `pytestmark` du module."""
    out: set[tuple[str, str]] = set()
    for path in tests:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        module_marked = any(
            isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
            and NETWORK_MARK in _marks(node.value)
            for node in tree.body)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                if module_marked or any(NETWORK_MARK in _marks(d) for d in node.decorator_list):
                    out.add((path.stem, node.name))
    return out


def _in_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() in ("1", "true", "yes", "on")


def is_network_case(result: dict[str, str], marked: set[tuple[str, str]]) -> bool:
    """Le résultat JUnit `test_live[case-1]` désigne-t-il un test marqué `network` ?"""
    # Le module ET la fonction : le repli « même nom de fonction ailleurs »
    # classait en « injoignable » un contrat rouge d'un autre fichier.
    name = result["name"].split("[", 1)[0]
    return any((module, name) in marked for module in (result.get("classes") or [result["file"]]))


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def tool_code(app_dir: Path, tool: dict[str, Any], language: str = "python") -> list[Path]:
    """Le code de l'outil, épinglé : qu'il bouge, et la part `suites` est périmée.

    Hors Python, l'outil `invoice_lookup` vit dans `Tools/InvoiceLookupTool.cs`,
    `tools/invoiceLookup.ts` ou `tools/InvoiceLookupTool.kt` : un fichier sous un
    répertoire `tools` dont le nom normalisé contient celui de l'outil. Sans ce
    repli, une modification d'outil C# ne périmait pas G3.
    """
    name = str(tool.get("name") or "")
    out = []
    if language == "python":
        for sub in ("tools", "data/tools"):
            p = app_dir / sub / f"{name}.py"
            if p.is_file():
                out.append(p)
        envelope = app_dir / "data" / "envelope.py"
        if envelope.is_file():
            out.append(envelope)
        return out
    wanted = _norm(name)
    suffixes = {"csharp": ".cs", "typescript": ".ts", "kotlin": ".kt", "java": ".java"}.get(language, "")
    for path in sorted(app_dir.rglob(f"*{suffixes}")) if wanted and suffixes else []:
        parts = [p.casefold() for p in path.relative_to(app_dir).parts]
        if any(p in _SKIP_DIRS for p in parts) or "tools" not in parts[:-1]:
            continue
        if wanted in _norm(path.stem):
            out.append(path)
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
    language = active_language(root)
    if language != "python" and language not in LANGUAGE_TESTS:
        report.error("TOOL_CONTRACT_FAILED", f"langage `{language}` : aucun runner de tests L2 connu",
                     f"langages outillés : python, {', '.join(sorted(LANGUAGE_TESTS))}",
                     "workspace/stack/STACK.md ## Active Language & Runtime")
        return []
    candidates = test_files(app_dir, language) if app_dir.is_dir() else []
    outcomes: list[ToolOutcome] = []

    for tool in ir.get("tools") or []:
        tid = str(tool.get("id"))
        if only and tid not in only and str(tool.get("name")) not in only:
            continue
        code_files = [p for p in tool_code(app_dir, tool, language) if p not in candidates]
        outcome = ToolOutcome(tool_id=tid, code=code_files)
        outcomes.append(outcome)
        sub = Report(name="G3.suites", target=tid)
        found = suites.get(tid)
        if not found:
            sub.error("TOOL_CONTRACT_FAILED", f"`{tid}` : aucune suite {LEVEL} (`toolRef: {tid}`) sous pipeline/suites/",
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
                    if language == "python":
                        code, tail = run_pytest(app_dir, outcome.tests, junit)
                        results = parse_junit(junit)
                    else:
                        # Le projet entier : l'outil du langage ne sait pas
                        # toujours filtrer par fichier ; seuls les résultats des
                        # tests qui jouent la suite comptent (couverture par nom).
                        code, tail, reports = run_language_tests(app_dir, language, junit)
                        results = [r for rep in reports for r in parse_junit(rep)]
                        stems = {p.stem.casefold() for p in outcome.tests}
                        results = [r for r in results
                                   if any(c.casefold() in stems for c in r.get("classes", []))
                                   or r["file"].casefold() in stems] or results
                runner = "pytest" if language == "python" else LANGUAGE_TESTS[language].command[0]
                if not results:
                    sub.error("TOOL_CONTRACT_FAILED", f"`{tid}` : {runner} n'a rendu aucun résultat (exit {code})",
                              "lancer les tests à la main depuis l'application pour lire l'erreur",
                              tail.strip().splitlines()[-1] if tail.strip() else tid)
                marked = network_tests(outcome.tests) if language == "python" else set()
                def net(r: dict[str, Any]) -> bool:
                    return is_network_case(r, marked) if language == "python" else is_network_by_name(r)
                for r in results:
                    label = f"{r['file']}::{r['name']}"
                    if r["outcome"] == "passed":
                        outcome.passed += 1
                    elif r["outcome"] == "failed":
                        # La connectivité live est la SECONDE moitié de la TOOL GATE
                        # (contrat §8) : un service injoignable n'est pas un contrat
                        # non tenu, c'est une infrastructure absente — la correction
                        # n'est pas chez `dev-tools`, et le tableau de bord doit le voir.
                        (outcome.unreachable if net(r) else outcome.failed).append(label)
                    elif r["outcome"] == "xfailed":
                        outcome.xfailed.append(label)
                    elif net(r) and not _in_ci():
                        # La connectivité live sautée hors CI (poste hors ligne)
                        # informe ; en CI elle doit avoir tourné.
                        outcome.skipped.append(label)
                    else:
                        outcome.skipped_contract.append(label)
                for label in outcome.failed:
                    sub.error("TOOL_CONTRACT_FAILED", f"`{tid}` : test de contrat rouge — {label}",
                              "corriger l'outil (dev-tools) ou le runtime ; jamais le test pour qu'il passe", label)
                for label in outcome.unreachable:
                    sub.error("TOOL_LIVE_UNREACHABLE", f"`{tid}` : connectivité live en échec — {label}",
                              "vérifier l'URL, la clé (`workspace/src/{App}/.env`) et l'allowlist d'egress ; "
                              "un service injoignable au moment de la gate ne se prouve pas par un mock", label)
                for label in outcome.xfailed:
                    sub.error("TOOL_CONTRACT_FAILED", f"`{tid}` : défaut connu (xfail) — {label}",
                              "un xfail documente un contrat NON tenu : corriger, puis retirer le marqueur", label)
                for label in outcome.skipped:
                    sub.warn("TOOL_LIVE_UNREACHABLE", f"`{tid}` : connectivité live sautée hors CI — {label}",
                             "la rejouer réseau disponible : en CI, un test `network` sauté est rouge", label)
                for label in outcome.skipped_contract:
                    # Erreur : un test de contrat sauté n'a rien prouvé, et une
                    # suite entièrement `skip` rendait la part `suites` verte.
                    sub.error("TOOL_CONTRACT_FAILED", f"`{tid}` : test de contrat sauté — {label}",
                              "un cas sauté n'est pas un cas prouvé : retirer le `skip`, ou rendre la dépendance disponible", label)
                if results and not outcome.passed:
                    sub.error("TOOL_CONTRACT_FAILED", f"`{tid}` : aucun test de contrat vert sur {len(results)} résultat(s)",
                              "un contrat se prouve par des tests qui PASSENT", paths.rel(root, outcome.suite))
                outcome.uncovered = (coverage_gaps(case_ids(suite), outcome.tests, results) if language == "python"
                                     else coverage_gaps_by_name(case_ids(suite), results))
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
            f"{len(outcome.unreachable)} injoignable(s), {len(outcome.xfailed)} xfail, {len(outcome.uncovered)} cas non exercé(s)")
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
    report.data["tools"] = [{"tool": o.tool_id, "passed": o.passed, "failed": o.failed, "unreachable": o.unreachable,
                             "xfailed": o.xfailed, "skipped": o.skipped, "skippedContract": o.skipped_contract,
                             "uncovered": o.uncovered} for o in outcomes]
    if not args.json:
        for line in report.data.get("lines", []):
            print(line)
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
