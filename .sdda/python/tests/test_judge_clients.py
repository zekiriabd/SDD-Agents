"""Clients de juge RÉELS — contre un faux serveur HTTP local, jamais contre l'API facturée.

Ce que ces tests défendent :

  1. **Chaque fournisseur parle son protocole** (Messages API, Chat
     Completions, generateContent) : URL, en-tête d'authentification,
     température 0, sortie structurée, tokens lus dans l'enveloppe, coût
     recalculé par `pricing.py`, span émis.
  2. **Les retries sont bornés** et ne portent que sur 429/5xx ; `Retry-After`
     est respecté (le sommeil est injecté : aucun test n'attend vraiment).
  3. **Une réponse malformée est une erreur CLASSÉE**, jamais un score.
  4. **Une clé absente est `[JUDGE_CLIENT_MISSING]`**, qui nomme la variable —
     et le framework ne lit aucun `.env` pour la trouver.
  5. **Le runner construit le juge tout seul** depuis STACK.md, sans casser
     l'injection de faux qu'utilisent les autres tests.
  6. **D2** — un juge qui est aussi le modèle évalué rend un verdict advisory.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

from conftest import make_project
from sdda_lib import pricing, tracing
from sdda_lib.errors import SddaError
from sdda_lib.graders import judge_clients as jc
from sdda_lib.graders import llm_judge

RUBRIC = {"name": "t", "criteria": [{"id": "C1", "description": "cite la source"},
                                     {"id": "C2", "description": "répond à la question"}]}
VERDICT = {"criteria": {"C1": True, "C2": False}, "rationale": "C1 oui ; C2 non"}


# ---------------------------------------------------------------------------
# Faux serveur : une file de réponses scriptées, les requêtes reçues gardées
# ---------------------------------------------------------------------------
class FakeServer:
    def __init__(self) -> None:
        self.script: list[tuple[int, Any, dict[str, str]]] = []
        self.requests: list[dict[str, Any]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — API de http.server
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                server.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                        "body": body})
                status, payload, headers = server.script.pop(0) if server.script else (500, {"error": "vide"}, {})
                raw = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
                data = raw.encode("utf-8") if isinstance(raw, str) else raw
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_: Any) -> None:
                return

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def reply(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> "FakeServer":
        self.script.append((status, payload, headers or {}))
        return self

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server() -> Iterator[FakeServer]:
    fake = FakeServer()
    yield fake
    fake.close()


def anthropic_ok(text: str, tokens_in: int = 1200, tokens_out: int = 80) -> dict[str, Any]:
    return {"type": "message", "role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out}}


def _client(server: FakeServer, cls: type[jc.HttpJudgeClient] = jc.AnthropicJudgeClient, model: str = "claude-sonnet-5",
            **kw: Any) -> tuple[jc.HttpJudgeClient, list[tuple[str, dict[str, Any], str, float]], list[float]]:
    spans: list[tuple[str, dict[str, Any], str, float]] = []
    sleeps: list[float] = []
    client = cls(model, api_key="test-key-not-a-secret", base_url=server.url, api_key_env="ANTHROPIC_API_KEY",
                 span_sink=lambda *a: spans.append(a), sleep=sleeps.append, **kw)
    return client, spans, sleeps


# ---------------------------------------------------------------------------
# 1. Protocoles
# ---------------------------------------------------------------------------
def test_anthropic_client_speaks_the_messages_api_and_counts_cost(server: FakeServer) -> None:
    server.reply(200, anthropic_ok(json.dumps(VERDICT)))
    client, spans, _ = _client(server)
    schema = llm_judge.response_schema(llm_judge.Rubric.from_config(RUBRIC))
    text = client.judge("juge ceci", schema=schema)
    assert json.loads(text) == VERDICT
    req = server.requests[0]
    assert req["path"] == "/v1/messages"
    assert req["headers"]["x-api-key"] == "test-key-not-a-secret" and req["headers"]["anthropic-version"]
    assert req["body"]["model"] == "claude-sonnet-5" and req["body"]["temperature"] == 0
    assert req["body"]["output_config"]["format"] == {"type": "json_schema", "schema": schema}
    expected = pricing.estimate_cost_usd("claude-sonnet-5", 1200, 80)
    assert client.usage.to_dict() == {"calls": 1, "inputTokens": 1200, "outputTokens": 80,
                                      "costUsd": expected, "unpricedCalls": 0}
    name, attrs, status, _ = spans[0]
    assert name == "sdda.judge claude-sonnet-5" and status == "OK"
    assert attrs["gen_ai.operation.name"] == "chat" and attrs["gen_ai.usage.input_tokens"] == 1200
    assert attrs["sdda.cost.usd"] == expected
    assert "test-key-not-a-secret" not in repr(client) and "test-key-not-a-secret" not in json.dumps(attrs)


def test_openai_client_speaks_chat_completions(server: FakeServer) -> None:
    server.reply(200, {"choices": [{"message": {"role": "assistant", "content": json.dumps(VERDICT)}}],
                       "usage": {"prompt_tokens": 300, "completion_tokens": 20}})
    client, _, _ = _client(server, jc.OpenAIJudgeClient, model="gpt-5.4")
    assert json.loads(client.judge("p", schema={"type": "object"})) == VERDICT
    req = server.requests[0]
    assert req["path"] == "/chat/completions" and req["headers"]["authorization"] == "Bearer test-key-not-a-secret"
    assert req["body"]["response_format"]["json_schema"]["strict"] is True and req["body"]["temperature"] == 0
    assert client.usage.input_tokens == 300 and client.usage.cost_usd == pricing.estimate_cost_usd("gpt-5.4", 300, 20)


def test_gemini_client_speaks_generate_content(server: FakeServer) -> None:
    server.reply(200, {"candidates": [{"content": {"parts": [{"text": json.dumps(VERDICT)}]}}],
                       "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 7}})
    client, _, _ = _client(server, jc.GeminiJudgeClient, model="gemini-3.8-flash")
    schema = llm_judge.response_schema(llm_judge.Rubric.from_config(RUBRIC))
    assert json.loads(client.judge("p", schema=schema)) == VERDICT
    req = server.requests[0]
    assert req["path"] == "/v1beta/models/gemini-3.8-flash:generateContent"
    assert req["headers"]["x-goog-api-key"] == "test-key-not-a-secret"
    config = req["body"]["generationConfig"]
    assert config["temperature"] == 0 and config["responseMimeType"] == "application/json"
    assert "additionalProperties" not in json.dumps(config["responseSchema"])   # hors du sous-ensemble Gemini
    assert client.usage.output_tokens == 7


def test_a_model_that_refuses_temperature_is_replayed_once_without_it(server: FakeServer) -> None:
    server.reply(400, {"error": {"message": "temperature is not supported for this model"}})
    server.reply(200, anthropic_ok(json.dumps(VERDICT)))
    client, spans, sleeps = _client(server)
    client.judge("p", schema={"type": "object"})
    assert "temperature" in server.requests[0]["body"] and "temperature" not in server.requests[1]["body"]
    assert spans[0][1]["sdda.judge.temperature_dropped"] is True and sleeps == []


# ---------------------------------------------------------------------------
# 2. Retries
# ---------------------------------------------------------------------------
def test_a_429_then_success_is_retried_honouring_retry_after(server: FakeServer) -> None:
    server.reply(429, {"error": "rate"}, {"retry-after": "2"})
    server.reply(503, {"error": "busy"})
    server.reply(200, anthropic_ok(json.dumps(VERDICT)))
    client, spans, sleeps = _client(server)
    assert json.loads(client.judge("p", schema={"type": "object"})) == VERDICT
    assert len(server.requests) == 3 and sleeps == [2.0, 1.0]
    assert spans[0][1]["sdda.judge.attempts"] == 3


def test_retries_are_bounded_then_the_failure_is_classed(server: FakeServer) -> None:
    for _ in range(3):
        server.reply(529, {"error": "overloaded"})
    client, spans, sleeps = _client(server, max_retries=2)
    with pytest.raises(SddaError) as exc:
        client.judge("p")
    assert exc.value.cls == jc.CLS_TRANSPORT_FAILED and "529" in exc.value.error
    assert len(server.requests) == 3 and len(sleeps) == 2
    assert spans[-1][2] == "ERROR" and spans[-1][1]["error.type"] == jc.CLS_TRANSPORT_FAILED


def test_a_client_error_is_not_retried(server: FakeServer) -> None:
    server.reply(401, {"error": "invalid x-api-key"})
    client, _, sleeps = _client(server)
    with pytest.raises(SddaError) as exc:
        client.judge("p")
    assert exc.value.cls == jc.CLS_TRANSPORT_FAILED and "ANTHROPIC_API_KEY" in exc.value.fix
    assert len(server.requests) == 1 and sleeps == []


# ---------------------------------------------------------------------------
# 3. Réponses malformées
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("payload", [
    "<html>proxy error</html>",                                  # pas du JSON
    {"type": "message", "usage": {}},                            # pas de `content`
    anthropic_ok("Je dirais 7/10."),                              # sortie structurée non JSON
    anthropic_ok("[1, 2]"),                                       # JSON, mais pas un objet
])
def test_a_malformed_response_is_a_classed_error_not_a_score(server: FakeServer, payload: Any) -> None:
    server.reply(200, payload)
    client, _, _ = _client(server)
    with pytest.raises(SddaError) as exc:
        client.judge("p", schema={"type": "object"})
    assert exc.value.cls == jc.CLS_RESPONSE_MALFORMED
    assert client.usage.calls == 0


def test_a_malformed_response_is_an_item_error_through_the_grader(server: FakeServer) -> None:
    """Le runner compte une exception de grader en erreur d'item : jamais en score 0."""
    server.reply(200, anthropic_ok("pas du json"))
    client, _, _ = _client(server)
    with pytest.raises(SddaError):
        llm_judge.GRADER.grade({"input": "q", "expected": "r"}, "réponse", None, None,
                               {"rubric": RUBRIC, "client": client})


# ---------------------------------------------------------------------------
# 4. Construction depuis la configuration — la clé vient de l'ENVIRONNEMENT
# ---------------------------------------------------------------------------
def test_the_provider_is_read_from_the_sheets_and_the_model_from_stack() -> None:
    sheets = jc.load_sheets()
    assert jc.provider_for_model("claude-sonnet-5", sheets) == "anthropic"
    assert jc.provider_for_model("gemini-3.8-flash", sheets) == "google"
    # `gpt-5.4` est porté par `openai` ET `azure-openai` : le catalogue ne tranche pas.
    assert jc.provider_for_model("gpt-5.4", sheets) == ""
    settings = jc.JudgeSettings(model_id="gpt-5.4")
    assert settings.resolve_provider(sheets, runtime_provider="openai") == "openai"
    assert jc.JudgeSettings(model_id="x", provider="local").resolve_provider(sheets) == "local-ollama"


def test_a_missing_key_is_judge_client_missing_naming_the_variable() -> None:
    with pytest.raises(SddaError) as exc:
        jc.build_client(jc.JudgeSettings(model_id="claude-sonnet-5"), environ={})
    assert exc.value.cls == jc.CLS_CLIENT_MISSING
    assert "ANTHROPIC_API_KEY" in exc.value.error and ".env" in exc.value.fix


def test_no_judge_model_and_an_unsupported_provider_are_classed() -> None:
    with pytest.raises(SddaError) as exc:
        jc.build_client(jc.JudgeSettings(model_id=""), environ={})
    assert exc.value.cls == jc.CLS_CLIENT_MISSING
    with pytest.raises(SddaError) as exc:
        jc.build_client(jc.JudgeSettings(model_id="gpt-5.4", provider="azure-openai"), environ={"AZURE_OPENAI_API_KEY": "k"})
    assert exc.value.cls == jc.CLS_PROVIDER_UNKNOWN


def test_the_base_url_variable_overrides_the_sheet_default(server: FakeServer) -> None:
    env = {"ANTHROPIC_API_KEY": "k-test", "ANTHROPIC_BASE_URL": server.url}
    client = jc.build_client(jc.JudgeSettings(model_id="claude-haiku-4-5"), environ=env)
    assert isinstance(client, jc.AnthropicJudgeClient) and client.base_url == server.url
    default = jc.build_client(jc.JudgeSettings(model_id="claude-haiku-4-5"), environ={"ANTHROPIC_API_KEY": "k"})
    assert default.base_url == "https://api.anthropic.com"
    ollama = jc.build_client(jc.JudgeSettings(model_id="qwen3:32b"), environ={})   # clé optionnelle en local
    assert isinstance(ollama, jc.OpenAIJudgeClient) and ollama.base_url == "http://localhost:11434/v1"


def test_the_framework_never_reads_a_dotenv_to_find_the_key(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "workspace/assets").mkdir(parents=True, exist_ok=True)
    (project / "workspace/assets/.env").write_text("ANTHROPIC_API_KEY=sk-ant-from-dotenv\n", encoding="utf-8")
    cfg, problem = jc.prepare_config(project, {"rubric": RUBRIC}, environ={})
    assert problem is not None and problem.cls == jc.CLS_CLIENT_MISSING and "client" not in cfg


# ---------------------------------------------------------------------------
# 5. Le runner construit le juge — sans casser l'injection de faux
# ---------------------------------------------------------------------------
def _judge_suite_project(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    from sdda_scripts import ir_compiler

    project = make_project(tmp_path)
    ir_compiler.compile_to_file(project, 1)
    from sdda_lib import paths
    ir = ir_compiler.load_ir(paths.ir_path(project, 1))
    dataset = project / "workspace/pipeline/datasets/golden/judge-v1.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text("".join(json.dumps({"id": f"j{i}", "input": f"q{i}", "expected": f"r{i}"}) + "\n" for i in range(2)),
                       encoding="utf-8")
    agent = next(a for a in ir["agents"] if a["id"] == "1-billing-specialist")
    suite = {"id": "1-2-judge_quality", "level": "L4", "capRef": "1-2-ExplainInvoiceLine", "agentRef": agent["id"],
             "metric": "rubric_score", "grader": "llm-judge", "dataset": "workspace/pipeline/datasets/golden/judge-v1.jsonl",
             "threshold": ">= 0.4", "runs": 2, "graderConfig": {"rubric": RUBRIC}}
    ir["evaluation"]["suites"] = [suite]
    return project, ir


def test_the_runner_builds_the_real_judge_from_stack_and_traces_it(tmp_path: Path, server: FakeServer,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from sdda_scripts import eval_runner

    project, ir = _judge_suite_project(tmp_path)
    for _ in range(4):
        server.reply(200, anthropic_ok(json.dumps(VERDICT), tokens_in=100, tokens_out=10))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
    report, payload = eval_runner.run_evals(project, ir, eval_runner.OracleExecutor(), write_gates=False, run_id="judge-run")
    assert len(server.requests) == 4                    # 2 items x 2 runs, aucun cache
    suite = payload["suites"][0]
    assert not report.has("AC_GRADER_UNKNOWN") and not report.has("JUDGE_CLIENT_MISSING")
    assert suite["mean"] == pytest.approx(0.5) and suite["errors"] == 0   # C1 vrai, C2 faux : 1/2
    trace = project / payload["judgeTrace"]
    summary = tracing.summarize(trace)
    assert summary.complete, summary.problems
    assert summary.tokens_in == 400 and summary.cost_usd == pytest.approx(4 * pricing.estimate_cost_usd("claude-sonnet-5", 100, 10))


def test_the_runner_reports_a_missing_key_as_a_classed_finding(tmp_path: Path) -> None:
    from sdda_scripts import eval_runner

    project, ir = _judge_suite_project(tmp_path)
    report, payload = eval_runner.run_evals(project, ir, eval_runner.OracleExecutor(), write_gates=False, write_report=False)
    assert report.has("JUDGE_CLIENT_MISSING") and report.has("AC_GRADER_UNKNOWN")
    assert "judgeTrace" not in payload


def test_an_injected_fake_judge_still_wins(tmp_path: Path) -> None:
    from sdda_scripts import eval_runner

    class Fake:
        model_id = "fake-judge"

        def judge(self, prompt: str) -> str:
            return json.dumps(VERDICT)

    project, ir = _judge_suite_project(tmp_path)
    ir["evaluation"]["suites"][0]["graderConfig"]["client"] = Fake()
    report, payload = eval_runner.run_evals(project, ir, eval_runner.OracleExecutor(), write_gates=False, write_report=False)
    assert not report.has("JUDGE_CLIENT_MISSING") and not report.has("AC_GRADER_UNKNOWN")


# ---------------------------------------------------------------------------
# 6. D2 — le juge ne note pas son propre modèle
# ---------------------------------------------------------------------------
class _Fake:
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    def judge(self, prompt: str) -> str:
        return json.dumps(VERDICT)


CALIBRATED = {"kappa": 0.9, "n": 80}


def test_a_judge_equal_to_the_evaluated_model_turns_advisory() -> None:
    r = llm_judge.GRADER.grade({"input": "q"}, "s", None, None,
                               {"rubric": RUBRIC, "client": _Fake("claude-sonnet-5"), "calibration": CALIBRATED,
                                "evaluated_model_id": ["claude-sonnet-5[1m]"], "judge_must_differ": True})
    assert r.detail["judge_equals_evaluated"] is True and r.detail["advisory"] is True
    assert r.detail["advisory_reason"].startswith(f"[{llm_judge.CLS_JUDGE_EQUALS_EVALUATED}]")


def test_the_evaluated_model_is_also_read_from_the_trace() -> None:
    """La config dit ce qui DEVAIT tourner ; la trace, ce qui a tourné."""
    trace = {"spans": [{"name": "chat", "attributes": {"gen_ai.operation.name": "chat",
                                                       "gen_ai.request.model": "claude-sonnet-5"}}]}
    r = llm_judge.GRADER.grade({"input": "q"}, "s", trace, None,
                               {"rubric": RUBRIC, "client": _Fake("claude-sonnet-5"), "calibration": CALIBRATED,
                                "evaluated_model_id": "claude-haiku-4-5"})
    assert r.detail["advisory"] is True and r.detail["evaluated_model_ids"] == ["claude-haiku-4-5", "claude-sonnet-5"]


def test_a_different_calibrated_judge_stays_blocking_and_the_option_can_be_off() -> None:
    cfg = {"rubric": RUBRIC, "calibration": CALIBRATED, "evaluated_model_id": "claude-haiku-4-5"}
    r = llm_judge.GRADER.grade({"input": "q"}, "s", None, None, {**cfg, "client": _Fake("claude-sonnet-5")})
    assert r.detail["advisory"] is False and r.detail["judge_equals_evaluated"] is False
    off = llm_judge.GRADER.grade({"input": "q"}, "s", None, None,
                                 {**cfg, "client": _Fake("claude-haiku-4-5"), "judge_must_differ": False})
    assert off.detail["judge_equals_evaluated"] is True and off.detail["advisory"] is False


def test_the_runner_makes_a_self_judging_suite_advisory(tmp_path: Path) -> None:
    """Bout en bout : l'agent évalué tourne sur `balanced` = claude-sonnet-5, le juge aussi."""
    from sdda_scripts import eval_runner

    project, ir = _judge_suite_project(tmp_path)
    suite = ir["evaluation"]["suites"][0]
    suite["graderConfig"].update({"client": _Fake("claude-sonnet-5"), "calibration": CALIBRATED})
    agent = next(a for a in ir["agents"] if a["id"] == suite["agentRef"])
    agent["tier"] = "balanced"
    _, payload = eval_runner.run_evals(project, ir, eval_runner.OracleExecutor(), write_gates=False, write_report=False)
    result = payload["suites"][0]
    assert result["advisory"] is True and result["blockingVerdict"] != "red"
    assert any(llm_judge.CLS_JUDGE_EQUALS_EVALUATED in note for note in result["notes"])
