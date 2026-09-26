"""`project-init` hors Python : le `.env` part avec l'application, dans tous les langages.

Il n'était copié que par le squelette Python ; un projet C#, TypeScript ou JVM
passait ses smokes de packaging sans clé, alors que les prompts affirmaient le
contraire.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sdda_scripts import project_init


@pytest.mark.parametrize("language", ["csharp", "typescript", "kotlin", "java"])
def test_the_env_is_copied_and_the_restore_waits_for_the_manifest(project: Path, language: str) -> None:
    stack = project / "workspace/stack/STACK.md"
    text = stack.read_text(encoding="utf-8")
    assert "- .sdda/stacks/lang/python.md" in text
    stack.write_text(text.replace("- .sdda/stacks/lang/python.md", f"- .sdda/stacks/lang/{language}.md"),
                     encoding="utf-8")
    source = project / "workspace/assets/.env"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("LLM_API_KEY=x\n", encoding="utf-8")

    report = project_init.run(project, mission="1", install=True)

    assert (project / "workspace/src/SupportAssistant/.env").is_file()
    deps = report.data["steps"]["dependencies"]
    assert "skipped" in deps and "dev-backend" in deps["skipped"]
