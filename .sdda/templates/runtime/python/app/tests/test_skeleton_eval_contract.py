"""Le contrat d'évaluation de la CLI (`stacks/serving/cli.md §3.5`) — L1/L2. GÉNÉRÉ, ne pas éditer.

Les runners du framework LANCENT l'application ; ils ne l'importent pas. Ces
tests tiennent les trois points que chaque langage doit honorer :

  1. `run --json --input-file -` lit l'entrée entière sur stdin ;
  2. `SDDA_EVAL_ISOLATION=mocked` sert outils et retrieval depuis
     `SDDA_EVAL_FIXTURES`, et refuse de démarrer (code 8) sur un outil sans
     fixture ;
  3. `retrieve --json --index ID --query-file -` interroge l'index sans agent ni
     modèle : un événement `retrieval`, puis `run_finished`.

Aucun réseau, aucune clé : le modèle est un `StubClient`, la trace reste en
mémoire.
"""
from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from {AppName}.config import ConfigError, Settings
from {AppName}.models import Completion, StubClient, ToolCall, Usage
from {AppName}.run_service import RunService, composed_service
from {AppName}.serving import cli
from {AppName}.serving.exit_codes import ExitCode, resolve_exit_code


def _settings(**overrides: Any) -> Settings:
    base = Settings.load(environ={})
    return replace(base, provider="stub", trace_enabled=False, **overrides)


def _stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="utf-8"))


def _events(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_run_reads_the_whole_input_from_stdin(monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    text = "-- une entrée qui commence par un tiret\net tient sur deux lignes"
    _stdin(monkeypatch, text)
    client = StubClient(answer="ok")
    args = cli.build_parser().parse_args(["run", "--json", "--input-file", "-"])
    code = cli.cmd_run(args, service=RunService(_settings(), client=client))
    assert code == int(ExitCode.OK)
    seen = " ".join(m.content for m in client.calls[-1])
    assert "une entrée qui commence par un tiret" in seen and "deux lignes" in seen
    events = _events(capsys.readouterr().out)
    assert events[-1]["event"] == "run_finished"


def test_isolation_refuses_to_start_on_a_tool_without_fixture(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    settings = _settings(eval_isolation="mocked", eval_fixtures=tmp_path, tool_names=("orders_lookup",))
    with pytest.raises(ConfigError) as excinfo:
        composed_service(settings, client=StubClient())
    assert excinfo.value.cls == "CONFIG_INVALID"
    assert resolve_exit_code(status="failed", error_class=excinfo.value.cls) == ExitCode.CONFIG


def test_isolation_serves_tools_from_fixtures(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "orders_lookup.jsonl").write_text(
        json.dumps({"record": {"status": "shipped"}}) + "\n", encoding="utf-8")
    settings = _settings(eval_isolation="mocked", eval_fixtures=tmp_path, tool_names=("orders_lookup",))
    client = StubClient(script=(
        Completion(text="", model="m", usage=Usage(10, 5),
                   tool_calls=(ToolCall(id="t1", name="orders_lookup", arguments={"order_id": "A1"}),)),
        Completion(text="expédiée", model="m", usage=Usage(10, 5)),
    ))
    service = composed_service(settings, client=client)
    result = service.run_sync(cli.RunRequest(input="où est A1 ?"))
    assert result.status == "ok", result.message
    tool_turn = [m for m in client.calls[-1] if m.role == "tool"]
    assert tool_turn and "shipped" in tool_turn[0].content, "réponse attendue : la fixture, pas le vrai outil"


def test_retrieve_queries_the_index_without_any_model_call(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "tools").mkdir()
    (tmp_path / "retrieval").mkdir()
    (tmp_path / "retrieval" / "kb.jsonl").write_text(json.dumps(
        {"query": "délai de remboursement", "results": [{"id": "doc-7#2", "score": 0.91},
                                                        {"id": "doc-3#1", "score": 0.40}]}) + "\n",
        encoding="utf-8")
    client = StubClient()
    service = composed_service(_settings(eval_isolation="mocked", eval_fixtures=tmp_path), client=client)
    _stdin(monkeypatch, "délai de remboursement\n")
    args = cli.build_parser().parse_args(["retrieve", "--json", "--index", "kb", "--query-file", "-"])
    assert cli.cmd_retrieve(args, service=service) == int(ExitCode.OK)
    events = _events(capsys.readouterr().out)
    assert [e["event"] for e in events] == ["retrieval", "run_finished"]
    assert events[0]["index_id"] == "kb"
    # Les DOCUMENTS pour la gate (mesure au niveau document), les chunks à part.
    assert events[0]["result_ids"] == ["doc-7", "doc-3"] and events[0]["scores"] == [0.91, 0.40]
    assert events[0]["chunk_ids"] == ["doc-7#2", "doc-3#1"]
    assert client.calls == [], "la RETRIEVAL GATE juge l'index : aucun appel au modèle"
