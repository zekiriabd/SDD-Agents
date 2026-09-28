"""Le squelette suit le framework DÉCLARÉ — C1 : LangChain sert le modèle, LangGraph porte la boucle.

Le squelette appelait le SDK du fournisseur hors de tout framework : au premier
run poc, `validate-framework` a rendu G6 rouge (`[FRAMEWORK_DRIFT]`) sur une
application qui marchait, parce que STACK.md déclarait LangChain + LangGraph et
que rien, dans `agents/` ni `orchestration/`, ne les importait.

Ce que ces tests fixent :

  1. le squelette généré pour C1 passe la part `framework` de G6 tel quel ;
  2. un module de framework n'est émis qu'avec sa fiche (sinon la dérive
     change seulement de sens) ;
  3. le repli de `RunService` passe VRAIMENT par le graphe LangGraph, et le
     modèle par un `BaseChatModel` — pas seulement par un import ;
  4. une boucle écrite contre le SDK nu, à côté du squelette, reste une dérive.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langchain_core")

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from conftest import make_project  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_scripts import gen_app_skeleton, validate_framework  # noqa: E402
from test_runtime_app import APP, Runtime, rt  # noqa: E402,F401 - fixture réutilisée

SRC = f"workspace/src/{APP}"


def _without_frameworks(project: Path, *names: str) -> Path:
    stack = project / "workspace/stack/STACK.md"
    text = stack.read_text(encoding="utf-8")
    for name in names:
        text = text.replace(f" - .sdda/stacks/framework/{name}.md\n", "")
    stack.write_text(text, encoding="utf-8")
    return project


class ToolAwareFake(FakeMessagesListChatModel):
    """Le mock de `langchain.md §5.8`, qui accepte `bind_tools` et retient ce qu'on lui lie."""

    bound: list[Any] = []
    seen: list[Any] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        self.bound.append(tools)
        return self

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:  # noqa: A002
        self.seen.append(input)
        return super().invoke(input, config, **kwargs)


# ---------------------------------------------------------------------------
# 1–2. Génération
# ---------------------------------------------------------------------------
def test_the_c1_skeleton_passes_the_framework_part_of_g6_as_generated(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    assert gen_app_skeleton.run(project, mode="write").ok
    app = project / SRC
    assert (app / "agents/chat_model.py").is_file() and (app / "orchestration/single_agent.py").is_file()
    config = json.loads((app / "app_config.json").read_text(encoding="utf-8"))
    assert config["frameworks"] == ["langchain", "langgraph"]
    report = validate_framework.run(project, Report(name="F", target=str(project)))
    assert report.ok, [e.message for e in report.errors]


def test_a_framework_module_is_emitted_only_with_its_sheet(tmp_path: Path) -> None:
    project = _without_frameworks(make_project(tmp_path), "langgraph")
    assert gen_app_skeleton.run(project, mode="write").ok
    app = project / SRC
    assert (app / "agents/chat_model.py").is_file()
    assert not (app / "orchestration/single_agent.py").exists(), "langgraph importé sans `langgraph.md` actif"
    assert json.loads((app / "app_config.json").read_text(encoding="utf-8"))["frameworks"] == ["langchain"]
    assert gen_app_skeleton.run(project, mode="check").ok


def test_without_any_framework_the_skeleton_imports_none(tmp_path: Path) -> None:
    project = _without_frameworks(make_project(tmp_path), "langchain", "langgraph")
    assert gen_app_skeleton.run(project, mode="write").ok
    app = project / SRC
    assert not (app / "agents/chat_model.py").exists() and not (app / "orchestration/single_agent.py").exists()


# ---------------------------------------------------------------------------
# 3. Exécution
# ---------------------------------------------------------------------------
def test_the_fallback_agent_runs_inside_a_langgraph_graph(rt: Runtime) -> None:
    graph = __import__(f"{APP}.orchestration.single_agent", fromlist=["LangGraphAgent"])
    service = rt.service()
    agent = service._build_agent(rt.bounds_of(), rt.tracing.Tracer(), rt.run_service.Guardrails())
    assert isinstance(agent, graph.LangGraphAgent)
    result = service.run_sync(rt.run_service.RunRequest(input="bonjour"))
    assert result.status == "ok" and result.output == "réponse", result.to_dict()
    assert result.hops == 1


def test_the_manifest_is_introspected_from_the_compiled_graph(rt: Runtime, tmp_path: Path) -> None:
    graph = __import__(f"{APP}.orchestration.single_agent", fromlist=["LangGraphAgent"])
    loop = rt.base.BoundedLoop(agent_id="1-helper", bounds=rt.bounds_of(), client=rt.models.StubClient(),
                               model="m")
    agent = graph.LangGraphAgent(loop, ref="1-helper", max_hops=2)
    manifest = agent.dump_graph()
    assert manifest == {"generatedBy": "orchestration.langgraph", "entryNode": "agent",
                        "terminalNodes": ["agent"], "maxHops": 2,
                        "nodes": [{"id": "agent", "kind": "agent", "ref": "1-helper"}], "edges": []}
    written = agent.write_manifest(tmp_path)
    # La boucle de démarrage ne réécrit pas par-dessus le manifeste du framework.
    assert rt.base.foreign_manifest(written)


def test_a_bound_still_falls_inside_the_graph(rt: Runtime) -> None:
    graph = __import__(f"{APP}.orchestration.single_agent", fromlist=["LangGraphAgent"])
    call = rt.models.ToolCall(id="c1", name="lookup", arguments={})
    looping = rt.models.StubClient(script=[rt.models.Completion(text="", model="m", tool_calls=(call,))] * 5)
    loop = rt.base.BoundedLoop(agent_id="a", bounds=rt.bounds_of(max_iterations=2), client=looping, model="m",
                               toolset=rt.base.DictToolset(tools={"lookup": lambda: "ok"}))
    result = asyncio.run(graph.LangGraphAgent(loop).run(rt.trust.untrusted("x")))
    assert result.status == "failed" and result.bound_exceeded == "max_iterations"


def test_the_langchain_client_round_trips_tool_calls_and_separates_cache_tokens(rt: Runtime) -> None:
    chat_model = __import__(f"{APP}.agents.chat_model", fromlist=["LangChainClient"])
    reply = AIMessage(content="", tool_calls=[{"id": "t1", "name": "lookup", "args": {"q": "x"}}],
                      usage_metadata={"input_tokens": 100, "output_tokens": 7, "total_tokens": 107,
                                      "input_token_details": {"cache_read": 60}},
                      response_metadata={"model_name": "claude-x", "stop_reason": "tool_use"})
    fake = ToolAwareFake(responses=[reply])
    client = chat_model.LangChainClient(factory=lambda model, max_tokens: fake)
    Message = rt.models.Message
    history = [Message(role="system", content="S"), Message(role="user", content="U"),
               Message(role="assistant", content="", tool_calls=(rt.models.ToolCall("t0", "lookup", {}),)),
               Message(role="tool", content="err", name="lookup", tool_call_id="t0", is_error=True)]
    spec = {"type": "function", "function": {"name": "lookup", "description": "d", "parameters": {"type": "object"}}}
    completion = client.complete(history, model="claude-x", tools=[spec])
    assert completion.tool_calls == (rt.models.ToolCall("t1", "lookup", {"q": "x"}),)
    assert (completion.usage.input_tokens, completion.usage.cache_read_tokens) == (40, 60)
    assert completion.model == "claude-x" and completion.finish_reason == "tool_use"
    sent = fake.seen[-1]
    assert [type(m).__name__ for m in sent] == ["SystemMessage", "HumanMessage", "AIMessage", "ToolMessage"]
    assert sent[3].status == "error" and fake.bound[-1][0]["function"]["name"] == "lookup"


def test_provider_client_goes_through_langchain_when_it_is_declared(rt: Runtime) -> None:
    pytest.importorskip("langchain_anthropic")
    chat_model = __import__(f"{APP}.agents.chat_model", fromlist=["LangChainClient"])
    settings = replace(rt.settings(), provider="anthropic",
                       _secrets={"llmApiKey": rt.config.Secret("k" * 20)}, secret_env={"llmApiKey": "K"})
    client = rt.models.provider_client(settings)
    assert isinstance(client, chat_model.LangChainClient)
    model = client.factory("claude-x", 128)
    assert type(model).__name__ == "ChatAnthropic" and model.model == "claude-x" and model.max_tokens == 128


def test_the_langchain_azure_client_uses_the_deployment(rt: Runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def azure(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return object()

    monkeypatch.setitem(sys.modules, "langchain_openai", types.SimpleNamespace(AzureChatOpenAI=azure))
    settings = replace(rt.settings(), provider="azure", tier_map={"balanced": "gpt-5.4"},
                       provider_env={"AZURE_OPENAI_ENDPOINT": "https://r.openai.azure.com",
                                     "AZURE_OPENAI_API_VERSION": "2025-01-01",
                                     "AZURE_OPENAI_DEPLOYMENT_BALANCED": "prod-balanced"},
                       _secrets={"llmApiKey": rt.config.Secret("k" * 20)}, secret_env={"llmApiKey": "K"})
    rt.models.provider_client(settings).factory("gpt-5.4", None)
    assert seen["azure_deployment"] == "prod-balanced" and seen["azure_endpoint"] == "https://r.openai.azure.com"
    with pytest.raises(rt.config.ConfigError):
        rt.models.provider_client(replace(settings, provider_env={}))


# ---------------------------------------------------------------------------
# 4. Contournement
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("rel", "drift"), [
    ("agents/billing/agent.py", True),
    ("orchestration/router_loop.py", True),
    ("agents/billing/tests/test_agent.py", False),
    ("models_extra.py", False),
])
def test_a_loop_written_against_the_bare_sdk_is_drift(tmp_path: Path, rel: str, drift: bool) -> None:
    project = make_project(tmp_path)
    assert gen_app_skeleton.run(project, mode="write").ok
    target = project / SRC / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("import anthropic\n", encoding="utf-8")
    report = validate_framework.run(project, Report(name="F", target=str(project)))
    hits = [e for e in report.errors if e.cls == "FRAMEWORK_DRIFT" and "SDK" in e.message]
    assert bool(hits) is drift, [e.message for e in report.errors]
