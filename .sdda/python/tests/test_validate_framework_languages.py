"""Part `framework` de G6 hors Python — le code importe le framework déclaré, et lui seul.

Hors Python, le script rendait « non vérifiable » : une application C# qui
ignorait Microsoft Agent Framework, ou une application TypeScript qui importait
Mastra à côté de LangGraph.js, passait l'ORCH GATE sans que rien ne le voie.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sdda_lib.errors import Report
from sdda_scripts import validate_framework as vf

CASES = {
    "csharp": ("ms-agent-framework", "orchestration/Router.cs", "using Microsoft.Agents.AI;\nclass R {}",
               "agents/triage/Agent.cs", "using Microsoft.SemanticKernel;\n"),
    "typescript": ("langgraph-js", "orchestration/graph.ts", 'import { StateGraph } from "@langchain/langgraph";\n',
                   "agents/triage/agent.ts", 'import { Agent } from "@mastra/core";\n'),
    "kotlin": ("spring-ai", "agents/triage/Agent.kt", "import org.springframework.ai.chat.client.ChatClient\n",
               "tools/Lookup.kt", "import dev.langchain4j.agent.tool.Tool\n"),
    "java": ("spring-ai", "agents/triage/Agent.java", "import org.springframework.ai.chat.client.ChatClient;\n",
             "tools/Lookup.java", "import dev.langchain4j.agent.tool.Tool;\n"),
}


def _project(tmp_path: Path, language: str, framework: str) -> Path:
    (tmp_path / "workspace/stack").mkdir(parents=True)
    (tmp_path / "workspace/stack/STACK.md").write_text(
        "## Project Config\n\nAppName: Shop\n\n## Active Language & Runtime\n\n"
        f"- .sdda/stacks/lang/{language}.md\n\n## Active Agent Framework\n\n- .sdda/stacks/framework/{framework}.md\n",
        encoding="utf-8")
    (tmp_path / "workspace/src/Shop").mkdir(parents=True)
    return tmp_path


def _write(root: Path, rel: str, text: str) -> None:
    path = root / "workspace/src/Shop" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("language", sorted(CASES))
def test_the_declared_framework_in_its_layer_is_green(tmp_path: Path, language: str) -> None:
    framework, rel, text, _other, _competitor = CASES[language]
    root = _project(tmp_path, language, framework)
    _write(root, rel, text)
    report = vf.run(root, Report(name="F", target=str(root)))
    assert report.ok, [e.message for e in report.errors]
    assert report.data.get("applicable", True) is True


@pytest.mark.parametrize("language", sorted(CASES))
def test_a_declared_framework_the_code_ignores_is_drift(tmp_path: Path, language: str) -> None:
    framework, _rel, _text, _other, _competitor = CASES[language]
    root = _project(tmp_path, language, framework)
    _write(root, "orchestration/Nothing.txt", "rien")
    report = vf.run(root, Report(name="F", target=str(root)))
    assert "FRAMEWORK_DRIFT" in {e.cls for e in report.errors}


@pytest.mark.parametrize("language", sorted(CASES))
def test_an_undeclared_competitor_is_drift(tmp_path: Path, language: str) -> None:
    framework, rel, text, other, competitor = CASES[language]
    root = _project(tmp_path, language, framework)
    _write(root, rel, text)
    _write(root, other, competitor)
    report = vf.run(root, Report(name="F", target=str(root)))
    assert any("non déclaré" in e.message for e in report.errors), [e.message for e in report.errors]


def test_build_outputs_are_not_read(tmp_path: Path) -> None:
    root = _project(tmp_path, "csharp", "ms-agent-framework")
    _write(root, "orchestration/Router.cs", "using Microsoft.Agents.AI;\n")
    _write(root, "obj/Debug/Generated.cs", "using Microsoft.SemanticKernel;\n")
    assert vf.run(root, Report(name="F", target=str(root))).ok
