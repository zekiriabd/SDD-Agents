"""Clients de jugement RÉELS — Anthropic, OpenAI (et compatibles), Google Gemini. Stdlib seule.

`llm_judge.py` définit la grille, le prompt et le parsing ; il déléguait l'appel
à un `JudgeClient` injecté, et aucun n'existait hors des tests. Le grader était
donc enregistré mais toujours indisponible : chaque AC notée par un juge LLM
partait en `[AC_GRADER_UNKNOWN]`, et la seule façon de la mesurer était d'écrire
soi-même le client — c'est-à-dire de laisser chaque projet réinventer les
retries, le comptage de tokens et la rédaction de la clé.

Ce module comble ce vide, avec les contraintes de l'outillage :

  - **stdlib seule** (`urllib.request`, `json`) — aucun SDK, parce que
    `.sdda/python` n'a aucune dépendance et doit tourner sur un clone nu ;
  - **le modèle vient de la configuration**, jamais du code : `JudgeModel`
    (STACK.md `## Runtime Models`), et le fournisseur est celui dont la fiche
    `.sdda/providers/*.yaml` déclare ce modèle (`tier_map` ou `pricing`) — à
    défaut `JudgeProvider`, puis `RuntimeProvider` ;
  - **la clé est lue dans l'environnement du PROCESSUS**, sous le NOM que
    déclare la fiche (`auth_env`) ou `JudgeApiKeyEnv`. Jamais une valeur écrite,
    jamais un `.env` lu par le framework : `assets/.env` appartient à
    l'application générée (ARCHITECTURE §2.ter), et un framework qui l'ouvrirait
    ferait de chaque script d'eval un lecteur de secrets. La variable doit donc
    être exportée dans le shell qui lance l'eval ; absente, c'est
    `[JUDGE_CLIENT_MISSING]`, qui nomme la variable et non sa valeur ;
  - **température 0** et **sortie JSON structurée** (schéma dérivé de la grille)
    quand le fournisseur la contraint ; la réponse est parsée STRICTEMENT — un
    corps qui n'est pas l'enveloppe attendue est `[JUDGE_RESPONSE_MALFORMED]`,
    jamais un score ;
  - **retries bornés** sur 429 et 5xx (et coupures réseau), en respectant
    `Retry-After`, puis `[JUDGE_TRANSPORT_FAILED]` ;
  - **tokens et coût** comptés à chaque appel (coût recalculé par
    `pricing.py`, jamais relu), et **un span de trace** par appel quand un
    écrivain de trace est fourni.

Sur la température : les modèles récents à raisonnement (Claude Opus 5 /
Sonnet 5, familles GPT-5) REFUSENT tout paramètre d'échantillonnage (400). Le
client demande 0, et sur un 400 qui nomme `temperature` il rejoue UNE fois sans
— en le disant dans le span (`sdda.judge.temperature_dropped`). Ne pas le faire
rendrait le juge inutilisable sur ces modèles ; le faire en silence ferait
croire à un jugement à température nulle.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from sdda_lib import pricing, yaml_mini
from sdda_lib.errors import SddaError

CLS_CLIENT_MISSING = "JUDGE_CLIENT_MISSING"
CLS_PROVIDER_UNKNOWN = "JUDGE_PROVIDER_UNKNOWN"
CLS_TRANSPORT_FAILED = "JUDGE_TRANSPORT_FAILED"
CLS_RESPONSE_MALFORMED = "JUDGE_RESPONSE_MALFORMED"

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_TOKENS = 1024
#: Codes rejoués. 529 : « overloaded » d'Anthropic. Tout autre 4xx est une
#: erreur de requête (clé refusée, modèle inconnu) : la rejouer ne change rien
#: et brûle le budget d'eval.
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
MAX_BACKOFF_S = 30.0

ANTHROPIC_VERSION = "2023-06-01"


# ---------------------------------------------------------------------------
# Fiches providers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProviderSheet:
    """Ce qu'une fiche `.sdda/providers/{name}.yaml` dit de l'accès à son API."""

    name: str
    endpoint_kind: str
    auth_env: str = ""
    base_url_env: str = ""
    default_base_url: str = ""
    api_prefix: str = ""
    models: frozenset[str] = frozenset()

    @classmethod
    def load(cls, path: Path) -> "ProviderSheet":
        doc = yaml_mini.parse_mapping(path.read_text(encoding="utf-8"))
        models = set(str(v) for v in (doc.get("tier_map") or {}).values())
        models |= set(str(k) for k in (doc.get("pricing") or {}))
        return cls(name=str(doc.get("name") or path.stem), endpoint_kind=str(doc.get("endpoint_kind") or ""),
                   auth_env=str(doc.get("auth_env") or ""), base_url_env=str(doc.get("base_url_env") or ""),
                   default_base_url=str(doc.get("default_base_url") or ""),
                   api_prefix=str(doc.get("api_prefix") or ""), models=frozenset(models))

    def base_url(self, environ: Mapping[str, str]) -> str:
        """La variable déclarée si elle est posée, le défaut de la fiche sinon ; préfixe d'API ajouté."""
        raw = str(environ.get(self.base_url_env, "") if self.base_url_env else "").strip() or self.default_base_url
        base = raw.rstrip("/")
        if base and self.api_prefix and not base.endswith(self.api_prefix.rstrip("/")):
            base += "/" + self.api_prefix.strip("/")
        return base


#: Valeurs de `RuntimeProvider` (STACK.md) -> nom de fiche. STACK.md dit
#: `azure` et `local` ; les fiches s'appellent `azure-openai` et `local-ollama`.
PROVIDER_ALIASES: dict[str, str] = {"azure": "azure-openai", "local": "local-ollama", "ollama": "local-ollama"}


def load_sheets(directory: Path | None = None) -> dict[str, ProviderSheet]:
    folder = directory or pricing.PROVIDERS_DIR
    out: dict[str, ProviderSheet] = {}
    for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        try:
            sheet = ProviderSheet.load(path)
        except (OSError, yaml_mini.YamlMiniError):
            continue
        out[sheet.name] = sheet
    return out


def provider_for_model(model_id: str, sheets: Mapping[str, ProviderSheet]) -> str:
    """Le fournisseur dont la fiche déclare ce modèle, ou `""` s'il n'y en a pas UN seul.

    Deux fiches peuvent déclarer le même modèle (`openai` et `azure-openai`) :
    le catalogue ne tranche pas alors, et c'est `JudgeProvider` qui le doit.
    """
    base = pricing.base_model_id(model_id)
    owners = sorted(name for name, sheet in sheets.items() if base in sheet.models)
    return owners[0] if len(owners) == 1 else ""


# ---------------------------------------------------------------------------
# Usage et client de base
# ---------------------------------------------------------------------------
@dataclass
class JudgeUsage:
    """Cumul thread-safe : le runner note plusieurs items en parallèle (`EvalMaxParallel`)."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    unpriced_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, tokens_in: int, tokens_out: int, cost: float | None) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += tokens_in
            self.output_tokens += tokens_out
            if cost is None:
                self.unpriced_calls += 1
            else:
                self.cost_usd = round(self.cost_usd + cost, 6)

    def to_dict(self) -> dict[str, Any]:
        return {"calls": self.calls, "inputTokens": self.input_tokens, "outputTokens": self.output_tokens,
                "costUsd": self.cost_usd, "unpricedCalls": self.unpriced_calls}


#: (nom, attributs, statut, durée ms) -> None. Le runner y branche un
#: `tracing.TraceWriter` ; les tests, une liste.
SpanSink = Callable[[str, dict[str, Any], str, float], None]


class HttpJudgeClient:
    """Socle : transport HTTP JSON, retries bornés, comptage, span. Les sous-classes forment la requête."""

    provider = ""
    accepts_schema = True

    def __init__(self, model_id: str, *, api_key: str, base_url: str, api_key_env: str = "",
                 timeout_s: float = DEFAULT_TIMEOUT_S, max_retries: int = DEFAULT_MAX_RETRIES,
                 max_tokens: int = DEFAULT_MAX_TOKENS, span_sink: SpanSink | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not model_id:
            raise SddaError("juge LLM sans modèle", CLS_CLIENT_MISSING, "déclarer `JudgeModel` dans STACK.md `## Runtime Models`")
        self.model_id = model_id
        self._api_key = api_key
        self.api_key_env = api_key_env
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_retries = max(0, int(max_retries))
        self.max_tokens = max_tokens
        self.span_sink = span_sink
        self._sleep = sleep
        self.usage = JudgeUsage()

    def __repr__(self) -> str:  # la clé n'apparaît jamais, même dans un repr de débogage
        return f"{type(self).__name__}(model_id={self.model_id!r}, base_url={self.base_url!r}, api_key_env={self.api_key_env!r})"

    # -- À fournir par les sous-classes -------------------------------------
    def request(self, prompt: str, schema: dict[str, Any] | None, temperature: float | None) -> tuple[str, dict[str, str], dict[str, Any]]:
        raise NotImplementedError

    def extract(self, payload: dict[str, Any]) -> tuple[str, int, int]:
        raise NotImplementedError

    # -- Appel ---------------------------------------------------------------
    def judge(self, prompt: str, *, schema: dict[str, Any] | None = None) -> str:
        """Le texte de la réponse. JSON strict si un schéma est demandé."""
        started = time.monotonic()
        temperature: float | None = 0.0
        attempts = 0
        dropped = False
        attrs: dict[str, Any] = {"gen_ai.operation.name": "chat", "gen_ai.system": self.provider,
                                 "gen_ai.request.model": self.model_id, "sdda.judge.role": "llm-judge"}
        try:
            while True:
                attempts += 1
                url, headers, body = self.request(prompt, schema, temperature)
                status, raw, retry_after = self._post(url, headers, body)
                if status == 200:
                    break
                if status == 400 and temperature is not None and "temperature" in raw.lower():
                    temperature, dropped = None, True
                    continue
                if status in RETRYABLE_STATUS and attempts <= self.max_retries:
                    self._sleep(self._backoff(attempts, retry_after))
                    continue
                raise SddaError(
                    f"juge `{self.model_id}` ({self.provider}) : HTTP {status} après {attempts} tentative(s) — {_excerpt(raw)}",
                    CLS_TRANSPORT_FAILED,
                    "429/5xx : réduire `EvalMaxParallel` ou relancer plus tard ; 401/403 : vérifier la clé "
                    f"exportée sous `{self.api_key_env}` ; 404 : vérifier `JudgeModel`")
            payload = _strict_json(raw, f"enveloppe de réponse {self.provider}")
            text, tokens_in, tokens_out = self.extract(payload)
            if schema is not None:
                parsed = _strict_json(text, "sortie structurée du juge")
                if not isinstance(parsed, dict):
                    raise SddaError(f"juge `{self.model_id}` : sortie structurée non objet", CLS_RESPONSE_MALFORMED,
                                    "le schéma de la grille exige un objet {criteria, rationale}")
            cost: float | None
            try:
                cost = pricing.estimate_cost_usd(self.model_id, tokens_in, tokens_out)
            except pricing.UnknownModelPricing:
                cost = None
            self.usage.add(tokens_in, tokens_out, cost)
            attrs.update({"gen_ai.usage.input_tokens": tokens_in, "gen_ai.usage.output_tokens": tokens_out,
                          "sdda.judge.attempts": attempts, "sdda.judge.temperature_dropped": dropped})
            if cost is not None:
                attrs["sdda.cost.usd"] = cost
            else:
                attrs["sdda.cost.unpriced"] = f"[BUDGET_PRICING_UNKNOWN] {self.model_id}"
            self._span(attrs, "OK", started)
            return text
        except SddaError as exc:
            attrs.update({"sdda.judge.attempts": attempts, "error.type": exc.cls})
            self._span(attrs, "ERROR", started)
            raise

    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> tuple[int, str, float | None]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"content-type": "application/json", "accept": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310 — URL construite depuis la fiche provider
                return int(resp.status), resp.read().decode("utf-8", "replace"), None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace") if exc.fp is not None else ""
            return int(exc.code), raw, _retry_after(exc.headers.get("retry-after") if exc.headers else None)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            # Coupure réseau ou délai : rejouable comme un 503, pour la même raison.
            return 503, f"transport : {type(exc).__name__}: {getattr(exc, 'reason', exc)}", None

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(MAX_BACKOFF_S, max(0.0, retry_after))
        return min(MAX_BACKOFF_S, 0.5 * (2 ** (attempt - 1)))

    def _span(self, attrs: dict[str, Any], status: str, started: float) -> None:
        if self.span_sink is not None:
            self.span_sink(f"sdda.judge {self.model_id}", dict(attrs), status, (time.monotonic() - started) * 1000.0)


# ---------------------------------------------------------------------------
# Fournisseurs
# ---------------------------------------------------------------------------
class AnthropicJudgeClient(HttpJudgeClient):
    """Messages API : `POST {base}/v1/messages`, sortie contrainte par `output_config.format`."""

    provider = "anthropic"

    def request(self, prompt: str, schema: dict[str, Any] | None, temperature: float | None) -> tuple[str, dict[str, str], dict[str, Any]]:
        body: dict[str, Any] = {"model": self.model_id, "max_tokens": self.max_tokens,
                                "messages": [{"role": "user", "content": prompt}]}
        if temperature is not None:
            body["temperature"] = temperature
        if schema is not None:
            body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        return (f"{self.base_url}/v1/messages",
                {"x-api-key": self._api_key, "anthropic-version": ANTHROPIC_VERSION}, body)

    def extract(self, payload: dict[str, Any]) -> tuple[str, int, int]:
        blocks = payload.get("content")
        if not isinstance(blocks, list):
            raise _malformed(self, "`content` absent")
        if payload.get("stop_reason") == "refusal":
            raise SddaError(f"juge `{self.model_id}` : refus du modèle", CLS_RESPONSE_MALFORMED,
                            "un refus n'est pas un jugement : revoir l'item, ou choisir un autre JudgeModel")
        text = "".join(str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        usage = payload.get("usage") or {}
        return text, _tokens(usage, "input_tokens"), _tokens(usage, "output_tokens")


class OpenAIJudgeClient(HttpJudgeClient):
    """Chat Completions : `POST {base}/chat/completions`, `response_format` json_schema strict."""

    provider = "openai"

    def request(self, prompt: str, schema: dict[str, Any] | None, temperature: float | None) -> tuple[str, dict[str, str], dict[str, Any]]:
        body: dict[str, Any] = {"model": self.model_id, "messages": [{"role": "user", "content": prompt}],
                                "max_completion_tokens": self.max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        if schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "judge_verdict", "strict": True, "schema": schema}}
        headers = {"authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        return f"{self.base_url}/chat/completions", headers, body

    def extract(self, payload: dict[str, Any]) -> tuple[str, int, int]:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise _malformed(self, "`choices` absent")
        message = choices[0].get("message") or {}
        if message.get("refusal"):
            raise SddaError(f"juge `{self.model_id}` : refus du modèle", CLS_RESPONSE_MALFORMED,
                            "un refus n'est pas un jugement : revoir l'item, ou choisir un autre JudgeModel")
        content = message.get("content")
        if not isinstance(content, str):
            raise _malformed(self, "`choices[0].message.content` absent")
        usage = payload.get("usage") or {}
        return content, _tokens(usage, "prompt_tokens"), _tokens(usage, "completion_tokens")


class GeminiJudgeClient(HttpJudgeClient):
    """generateContent : `POST {base}/v1beta/models/{model}:generateContent`, `responseSchema`."""

    provider = "google"

    def request(self, prompt: str, schema: dict[str, Any] | None, temperature: float | None) -> tuple[str, dict[str, str], dict[str, Any]]:
        generation: dict[str, Any] = {"maxOutputTokens": self.max_tokens}
        if temperature is not None:
            generation["temperature"] = temperature
        if schema is not None:
            generation["responseMimeType"] = "application/json"
            generation["responseSchema"] = _gemini_schema(schema)
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": generation}
        return (f"{self.base_url}/v1beta/models/{self.model_id}:generateContent",
                {"x-goog-api-key": self._api_key}, body)

    def extract(self, payload: dict[str, Any]) -> tuple[str, int, int]:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise _malformed(self, "`candidates` absent")
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        if not isinstance(parts, list):
            raise _malformed(self, "`content.parts` absent")
        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
        usage = payload.get("usageMetadata") or {}
        return text, _tokens(usage, "promptTokenCount"), _tokens(usage, "candidatesTokenCount")


#: endpoint_kind / nom de fiche -> classe. Azure est absent À DESSEIN : on n'y
#: appelle pas un modèle mais un DÉPLOIEMENT nommé par le client, avec une
#: version d'API explicite — le deviner produirait un 404 qui ressemble à une
#: clé refusée.
CLIENTS: dict[str, type[HttpJudgeClient]] = {
    "anthropic": AnthropicJudgeClient,
    "openai": OpenAIJudgeClient,
    "local-ollama": OpenAIJudgeClient,
    "google": GeminiJudgeClient,
}
#: Fournisseurs pour lesquels une clé absente n'empêche pas l'appel (local).
KEY_OPTIONAL = frozenset({"local-ollama"})


# ---------------------------------------------------------------------------
# Fabrique
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class JudgeSettings:
    """Ce que STACK.md dit du juge. Des NOMS de variables, jamais des valeurs."""

    model_id: str
    provider: str = ""
    api_key_env: str = ""

    @classmethod
    def from_stack(cls, root: Path) -> "JudgeSettings":
        from sdda_lib.layered_config import read_stack_section_kv  # noqa: PLC0415 — évite un cycle d'import

        section = read_stack_section_kv(root, "Runtime Models")
        return cls(model_id=str(section.get("JudgeModel") or "").strip(),
                   provider=str(section.get("JudgeProvider") or "").strip(),
                   api_key_env=str(section.get("JudgeApiKeyEnv") or "").strip())

    def resolve_provider(self, sheets: Mapping[str, ProviderSheet], runtime_provider: str = "") -> str:
        name = self.provider or provider_for_model(self.model_id, sheets) or runtime_provider
        return PROVIDER_ALIASES.get(name.lower(), name.lower())


def build_client(settings: JudgeSettings, *, environ: Mapping[str, str] | None = None,
                 sheets: Mapping[str, ProviderSheet] | None = None, runtime_provider: str = "",
                 span_sink: SpanSink | None = None, options: Mapping[str, Any] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> HttpJudgeClient:
    """Le client du juge déclaré, ou une `SddaError` classée qui dit ce qui manque."""
    env = os.environ if environ is None else environ
    sheets = load_sheets() if sheets is None else sheets
    if not settings.model_id or settings.model_id.lower() == "none":
        raise SddaError("aucun `JudgeModel` déclaré", CLS_CLIENT_MISSING,
                        "déclarer `JudgeModel` sous `## Runtime Models` de STACK.md (≠ du modèle évalué)")
    name = settings.resolve_provider(sheets, runtime_provider)
    sheet = sheets.get(name)
    factory = CLIENTS.get(name)
    if sheet is None or factory is None:
        raise SddaError(
            f"juge `{settings.model_id}` : fournisseur `{name or '?'}` sans client de jugement "
            f"(supportés : {sorted(CLIENTS)})", CLS_PROVIDER_UNKNOWN,
            "déclarer `JudgeProvider` sous `## Runtime Models`, ou ajouter le modèle au `tier_map`/`pricing` "
            "de sa fiche `.sdda/providers/*.yaml` ; Azure exige un déploiement nommé et n'est pas deviné")
    key_env = settings.api_key_env or sheet.auth_env
    api_key = str(env.get(key_env, "") if key_env else "").strip()
    if not api_key and name not in KEY_OPTIONAL:
        raise SddaError(
            f"juge `{settings.model_id}` ({name}) : variable `{key_env or '<auth_env absent>'}` absente de "
            "l'environnement du processus d'eval", CLS_CLIENT_MISSING,
            f"exporter `{key_env}` dans le shell qui lance l'eval. Le framework ne lit jamais de `.env` : "
            "celui d'`assets/` appartient à l'application générée, pas au runner")
    base_url = sheet.base_url(env)
    if not base_url:
        raise SddaError(f"fiche `{name}` sans `default_base_url` ni `{sheet.base_url_env}`", CLS_PROVIDER_UNKNOWN,
                        f"poser `{sheet.base_url_env}` ou compléter `default_base_url` dans la fiche")
    opts = dict(options or {})
    return factory(settings.model_id, api_key=api_key, base_url=base_url, api_key_env=key_env,
                   timeout_s=float(opts.get("timeoutSec", DEFAULT_TIMEOUT_S)),
                   max_retries=int(opts.get("maxRetries", DEFAULT_MAX_RETRIES)),
                   max_tokens=int(opts.get("maxTokens", DEFAULT_MAX_TOKENS)),
                   span_sink=span_sink, sleep=sleep)


def prepare_config(root: Path, grader_config: Mapping[str, Any] | None, *, layered: Any = None,
                   evaluated_model_id: str | list[str] | None = None, span_sink: SpanSink | None = None,
                   environ: Mapping[str, str] | None = None) -> tuple[dict[str, Any], SddaError | None]:
    """Le `graderConfig` d'une suite `llm-judge`, complété de ce que le runner sait.

    - `client` : le client RÉEL du `JudgeModel` déclaré — sauf si la suite (ou
      un test) en injecte déjà un, qui garde la priorité ;
    - `judge_must_differ` : `JudgeMustDifferFromEvaluated` de la config ;
    - `evaluated_model_id` : le(s) modèle(s) que la suite évalue, résolus par
      le runner depuis l'IR et la `RuntimeTierMap`.

    Rend `(config, problème)` : un client impossible à construire n'est pas une
    exception qui arrête le run, c'est un finding classé que le runner écrit
    au rapport — et la suite n'est pas mesurée, ce qui est le seul résultat
    honnête.
    """
    cfg: dict[str, Any] = dict(grader_config or {})
    if layered is not None and "judge_must_differ" not in cfg:
        raw = layered.get("JudgeMustDifferFromEvaluated", True)
        cfg["judge_must_differ"] = raw if isinstance(raw, bool) else str(raw).strip().lower() not in ("false", "0", "no")
    if evaluated_model_id and "evaluated_model_id" not in cfg:
        cfg["evaluated_model_id"] = evaluated_model_id
    if cfg.get("client") is not None:
        return cfg, None
    from sdda_lib.layered_config import read_stack_section_kv  # noqa: PLC0415 — évite un cycle d'import

    runtime_provider = str(read_stack_section_kv(root, "Runtime Models").get("RuntimeProvider") or "")
    try:
        cfg["client"] = build_client(JudgeSettings.from_stack(root), environ=environ, runtime_provider=runtime_provider,
                                     span_sink=span_sink, options=cfg.get("judge") if isinstance(cfg.get("judge"), dict) else None)
    except SddaError as exc:
        return cfg, exc
    return cfg, None


# ---------------------------------------------------------------------------
# Aides
# ---------------------------------------------------------------------------
def _strict_json(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        raise SddaError(f"{what} : JSON invalide — {_excerpt(text)}", CLS_RESPONSE_MALFORMED,
                        "la réponse n'est pas l'objet attendu ; un juge qui ne sait pas répondre dans le format "
                        "n'a pas jugé — l'item est en erreur, jamais noté 0") from None


def _malformed(client: HttpJudgeClient, what: str) -> SddaError:
    return SddaError(f"juge `{client.model_id}` ({client.provider}) : réponse malformée — {what}",
                     CLS_RESPONSE_MALFORMED, "l'enveloppe ne correspond pas à l'API du fournisseur : vérifier l'URL de base")


def _tokens(usage: Mapping[str, Any], key: str) -> int:
    value = usage.get(key)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _retry_after(raw: str | None) -> float | None:
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _excerpt(text: str, limit: int = 200) -> str:
    flat = " ".join(str(text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Le sous-ensemble OpenAPI que `responseSchema` accepte : sans `additionalProperties`."""
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "additionalProperties":
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: _gemini_schema(v) if isinstance(v, dict) else v for k, v in value.items()}
        elif isinstance(value, dict):
            out[key] = _gemini_schema(value)
        else:
            out[key] = value
    return out
