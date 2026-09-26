"""L'exécuteur générique — l'application lancée par son contrat CLI, quel que soit son langage.

Les runners chargeaient l'application par `importlib` : hors Python, il n'y
avait rien à charger, et G4 à G8 étaient impossibles en C#, TypeScript, Kotlin
et Java. Ici une « application » minimale, écrite en Python mais vue seulement
par sa ligne de commande, joue le rôle de n'importe quel langage : c'est
précisément ce que l'exécuteur ne doit pas savoir.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from sdda_lib import executors

FAKE_APP = textwrap.dedent('''
    import json, os, sys
    args = sys.argv[1:]
    stdin = sys.stdin.buffer.read().decode("utf-8")
    def emit(e): sys.stdout.buffer.write((json.dumps(e) + "\\n").encode("utf-8")); sys.stdout.flush()
    if args[:1] == ["run"]:
        assert args[1:4] == ["--json", "--input-file", "-"], args
        isolated = os.environ.get("SDDA_EVAL_ISOLATION") == "mocked"
        leaked = "OPERATOR_TOKEN" in os.environ
        trace = os.path.join("workspace", ".sys", "traces", "runs", "r1.jsonl")
        os.makedirs(os.path.dirname(trace), exist_ok=True)
        with open(trace, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_id": "r1", "trace_id": "t", "span_id": "s", "name": "sdda.run 1"}) + "\\n")
        emit({"event": "run_started", "run_id": "r1"})
        emit({"event": "final", "output": {"echo": stdin, "isolated": isolated, "leaked": leaked,
                                           "fixtures": os.environ.get("SDDA_EVAL_FIXTURES", "")}})
        emit({"event": "run_finished", "exit_code": 0, "cost_usd": 0.01, "duration_ms": 12,
              "trace_path": trace, "run_id": "r1", "status": "ok"})
        sys.exit(0)
    if args[:1] == ["retrieve"]:
        k = int(args[args.index("--k") + 1]) if "--k" in args else 3
        emit({"event": "retrieval", "index_id": args[args.index("--index") + 1],
              "result_ids": [f"doc-{i}" for i in range(k)], "scores": [1.0 / (i + 1) for i in range(k)]})
        emit({"event": "run_finished", "exit_code": 0})
        sys.exit(0)
    emit({"event": "error", "class": "CLI_USAGE", "exit_code": 2})
    sys.exit(2)
''')


@pytest.fixture
def app(tmp_path: Path) -> executors.CommandExecutor:
    script = tmp_path / "fake_app.py"
    script.write_text(FAKE_APP, encoding="utf-8")
    return executors.CommandExecutor(command=(sys.executable, str(script)), root=tmp_path, timeout_s=60)


def test_a_run_goes_through_stdin_and_reads_the_ndjson(app: executors.CommandExecutor,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_TOKEN", "do-not-leak")
    out = app.run({"id": "i1", "input": "-une entrée qui commence par un tiret"})
    assert out["exit_code"] == 0 and out["status"] == "ok"
    assert out["output"]["echo"] == "-une entrée qui commence par un tiret"
    assert out["output"]["leaked"] is False, "le shell de construction ne passe pas à l'application"
    assert out["output"]["isolated"] is False
    assert out["cost_usd"] == pytest.approx(0.01) and len(out["trace"]["spans"]) == 1


def test_an_isolated_suite_sets_the_contract_variables(app: executors.CommandExecutor) -> None:
    out = app.run({"id": "i1", "input": "x"}, suite={"isolation": {"tools": "mocked"}})
    assert out["output"]["isolated"] is True and out["isolated"] is True
    assert out["output"]["fixtures"].replace("\\", "/").endswith("workspace/pipeline/fixtures")


def test_retrieve_is_the_g4_contract(app: executors.CommandExecutor) -> None:
    got = app.retrieve("facture 42", retriever="1-kb", k=5)
    assert got["docIds"] == [f"doc-{i}" for i in range(5)] and got["indexId"] == "1-kb"


def test_a_retrieve_without_event_is_an_error_not_an_empty_result(tmp_path: Path) -> None:
    silent = executors.CommandExecutor(command=(sys.executable, "-c", "print('rien')"), root=tmp_path)
    with pytest.raises(RuntimeError, match="retrieval"):
        silent.retrieve("q", retriever="1-kb", k=3)


@pytest.mark.parametrize("language,head", [("python", "uv"), ("csharp", "dotnet"), ("typescript", "node"),
                                           ("kotlin", "java"), ("java", "java")])
def test_cli_derives_the_command_from_the_active_language(project: Path, language: str, head: str) -> None:
    stack = project / "workspace/stack/STACK.md"
    text = stack.read_text(encoding="utf-8")
    assert "- .sdda/stacks/lang/python.md" in text
    stack.write_text(text.replace("- .sdda/stacks/lang/python.md", f"- .sdda/stacks/lang/{language}.md"),
                     encoding="utf-8")
    ex = executors.load_executor("cli", root=project)
    assert isinstance(ex, executors.CommandExecutor) and ex.command[0] == head
    assert any("SupportAssistant" in part for part in ex.command)


def test_cmd_imposes_the_command(tmp_path: Path) -> None:
    ex = executors.load_executor("cmd:dotnet run --project workspace/src/Shop --", root=tmp_path)
    assert ex.command == ("dotnet", "run", "--project", "workspace/src/Shop", "--")


def test_an_unknown_spec_says_the_three_forms(tmp_path: Path) -> None:
    with pytest.raises(executors.ExecutorLoadError, match="cmd:"):
        executors.load_executor("nimportequoi", root=tmp_path)


def test_the_launch_table_covers_the_five_languages() -> None:
    assert set(executors.LAUNCH_COMMANDS) == {"python", "csharp", "typescript", "kotlin", "java"}
    json.dumps(executors.LAUNCH_COMMANDS)   # sérialisable : recopiée dans le contexte projet
