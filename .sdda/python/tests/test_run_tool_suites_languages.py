"""G3 part `suites` hors Python : les tests L2 joués par l'outil du langage, lus en JUnit.

Le script ne savait lancer que pytest : en C#, TypeScript, Kotlin ou Java, la
part `suites` était rouge par construction (« aucun test ne joue la suite »), et
le hook de G3 refusait ensuite tout `dev-agent`. Ici le runner du langage est
remplacé par un script qui écrit le rapport JUnit qu'écrirait `dotnet test` :
le script ne doit juger que ce rapport et la convention de nommage.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from sdda_scripts import run_tool_suites as rts

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="s">
  <testcase classname="Shop.Tests.LookupContractTests" name="Contract(caseId: &quot;happy-1&quot;)"/>
  <testcase classname="Shop.Tests.LookupContractTests" name="Contract(caseId: &quot;not-found-1&quot;)"/>
  {extra}
</testsuite></testsuites>
"""


def _project(tmp_path: Path, extra: str = "") -> tuple[Path, Path]:
    root = tmp_path
    (root / "workspace/stack").mkdir(parents=True)
    (root / "workspace/stack/STACK.md").write_text(
        "## Project Config\n\nAppName: Shop\n\n## Active Language & Runtime\n\n- .sdda/stacks/lang/csharp.md\n",
        encoding="utf-8")
    app = root / "workspace/src/Shop"
    (app / "Tests").mkdir(parents=True)
    (app / "Tools").mkdir(parents=True)
    (app / "Tools/LookupTool.cs").write_text("class LookupTool {}", encoding="utf-8")
    (app / "Tests/LookupContractTests.cs").write_text('var suite = "tool-1-lookup";', encoding="utf-8")
    (root / "workspace/pipeline/suites").mkdir(parents=True)
    (root / "workspace/pipeline/suites/tool-1-lookup.yaml").write_text(
        "id: tool-1-lookup\nlevel: L2\ntoolRef: 1-lookup\ncases:\n  - id: happy-1\n  - id: not-found-1\n",
        encoding="utf-8")
    ir = root / "workspace/.sys/.ir/1-system.ir.json"
    ir.parent.mkdir(parents=True)
    ir.write_text(json.dumps({"missionId": "1-Shop", "tools": [{"id": "1-lookup", "name": "lookup"}]}), encoding="utf-8")
    fake = root / "fake_dotnet.py"
    fake.write_text("import sys\nopen(sys.argv[1], 'w', encoding='utf-8').write(" + repr(JUNIT.replace("{extra}", extra))
                    + ")\n", encoding="utf-8")
    return root, fake


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch):
    def install(fake: Path) -> None:
        monkeypatch.setitem(rts.LANGUAGE_TESTS, "csharp",
                            rts.LanguageTests(("*Tests.cs",), (sys.executable, str(fake), "{junit}"), "{junit}"))
    return install


def _run(root: Path):
    from sdda_lib.errors import Report

    report = Report(name="G3", target=str(root))
    outcomes = rts.run(root, root / "workspace/.sys/.ir/1-system.ir.json", report, only=set(), write=False)
    return report, outcomes


def test_a_csharp_suite_whose_cases_pass_by_name_is_green(tmp_path: Path, fake_runner) -> None:
    root, fake = _project(tmp_path)
    fake_runner(fake)
    report, (outcome,) = _run(root)
    assert report.ok, [e.message for e in report.errors]
    assert outcome.passed == 2 and outcome.uncovered == []
    assert [p.name for p in outcome.code] == ["LookupTool.cs"], "le code C# de l'outil est épinglé"


def test_a_case_no_green_test_names_is_uncovered(tmp_path: Path, fake_runner) -> None:
    root, fake = _project(tmp_path)
    (root / "workspace/pipeline/suites/tool-1-lookup.yaml").write_text(
        "id: tool-1-lookup\nlevel: L2\ntoolRef: 1-lookup\ncases:\n  - id: happy-1\n  - id: timeout-1\n", encoding="utf-8")
    fake_runner(fake)
    report, (outcome,) = _run(root)
    assert outcome.uncovered == ["timeout-1"] and not report.ok


def test_a_failing_network_test_is_unreachable_not_a_broken_contract(tmp_path: Path, fake_runner) -> None:
    extra = ('<testcase classname="Shop.Tests.LookupNetworkTests" name="Live"><failure message="dns"/></testcase>')
    root, fake = _project(tmp_path, extra)
    (root / "workspace/src/Shop/Tests/LookupNetworkTests.cs").write_text('var suite = "tool-1-lookup";', encoding="utf-8")
    fake_runner(fake)
    report, (outcome,) = _run(root)
    assert outcome.unreachable and not outcome.failed
    assert "TOOL_LIVE_UNREACHABLE" in {e.cls for e in report.errors}


def test_the_test_files_follow_the_language(tmp_path: Path) -> None:
    root, _fake = _project(tmp_path)
    found = rts.test_files(root / "workspace/src/Shop", "csharp")
    assert [p.name for p in found] == ["LookupContractTests.cs"]


def test_an_unsupported_language_is_said(tmp_path: Path) -> None:
    root, _fake = _project(tmp_path)
    stack = root / "workspace/stack/STACK.md"
    stack.write_text(stack.read_text(encoding="utf-8").replace("csharp", "cobol"), encoding="utf-8")
    report, outcomes = _run(root)
    assert outcomes == [] and "aucun runner" in report.errors[0].message
