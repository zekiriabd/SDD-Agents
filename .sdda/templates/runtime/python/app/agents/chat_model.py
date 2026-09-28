"""Le modèle de la boucle d'agent, servi par LangChain. GÉNÉRÉ, ne pas éditer.

Émis seulement quand `framework/langchain.md` est actif (`gen_app_skeleton`).
C'est la moitié `agents/` de l'alignement sur la stack déclarée : la boucle
bornée (`orchestration/base.BoundedLoop`) reste celle de `langchain.md §3.3` —
une boucle écrite à la main, parce que LangChain n'offre aucune des cinq
bornes —, et le modèle qu'elle appelle est un `BaseChatModel` lié à ses outils
par `bind_tools`. Avant lui, le squelette appelait le SDK du fournisseur sans
framework : la déclaration de STACK.md ne décrivait pas le code, et
`validate-framework` rendait G6 rouge (`[FRAMEWORK_DRIFT]`) sur une
application qui marchait.

Ce module est un ADAPTATEUR, pas une seconde boucle : il rend la même
`Completion` que les clients SDK de `models.py`, tokens de cache séparés
compris. La boucle, le traceur, le budget et `RecordingClient` ne voient pas la
différence — et c'est ce qui permet au stub de tourner sans LangChain installé.

`models.provider_client` le choisit dès que `langchain` figure dans
`app_config.json` (`frameworks`) : une composition qui passe par
`provider_client` passe donc par LangChain sans rien savoir de lui.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable, Mapping, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from ..config import ConfigError, Settings
from ..models import (
    AZURE_API_VERSION_ENV,
    AZURE_DEPLOYMENT_ENV,
    AZURE_ENDPOINT_ENV,
    GEMINI_OPENAI_BASE_URL,
    Completion,
    Message,
    ToolCall,
    Usage,
    _tool_spec_parts,
)


def to_langchain(messages: Sequence[Message]) -> list[BaseMessage]:
    """L'historique de la boucle dans la grammaire de `langchain_core.messages`.

    Le texte non maîtrisé arrive déjà enveloppé (`trust.wrap`) dans un message
    `user` ou `tool` : il ne rejoint jamais le `SystemMessage` (P8).
    """
    out: list[BaseMessage] = []
    for m in messages:
        if m.role == "system":
            out.append(SystemMessage(content=m.content))
        elif m.role == "user":
            out.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            out.append(AIMessage(content=m.content, tool_calls=[
                {"id": c.id, "name": c.name, "args": dict(c.arguments), "type": "tool_call"}
                for c in m.tool_calls]))
        else:
            out.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id, name=m.name or None,
                                   status="error" if m.is_error else "success"))
    return out


def tool_specs(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Les specs d'outils sous la forme que `bind_tools` accepte de tout fournisseur."""
    specs = []
    for spec in tools:
        name, description, schema = _tool_spec_parts(spec)
        specs.append({"type": "function",
                      "function": {"name": name, "description": description, "parameters": dict(schema)}})
    return specs


def _usage(message: AIMessage) -> Usage:
    """`usage_metadata` -> `Usage`. Les tokens de cache sont retirés de l'entrée.

    LangChain compte le cache DANS `input_tokens` (et le détaille à part) ; le
    tarif, lui, est différent : les laisser dedans surfacturait chaque prompt
    caché au tarif plein.
    """
    meta: Mapping[str, Any] = message.usage_metadata or {}
    details: Mapping[str, Any] = meta.get("input_token_details") or {}
    cache_read = int(details.get("cache_read") or 0)
    cache_write = int(details.get("cache_creation") or 0)
    return Usage(input_tokens=max(0, int(meta.get("input_tokens") or 0) - cache_read - cache_write),
                 output_tokens=int(meta.get("output_tokens") or 0),
                 cache_read_tokens=cache_read, cache_write_tokens=cache_write)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b.get("text", "")) if isinstance(b, Mapping) else str(b) for b in content
                       if not isinstance(b, Mapping) or b.get("type") in (None, "text"))
    return str(content or "")


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        value = json.loads(str(raw or "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


@dataclass
class LangChainClient:
    """`LLMClient` servi par un `BaseChatModel` — un modèle par identifiant, construit à la demande.

    `factory(model, max_tokens)` rend le `BaseChatModel` ; il est injectable pour
    que les tests L1/L2 passent un `FakeMessagesListChatModel` (`langchain.md §5.8`)
    sans clé ni réseau.
    """

    factory: Callable[[str, int | None], Any]

    def complete(self, messages: Sequence[Message], *, model: str,
                 tools: Sequence[Mapping[str, Any]] = (), **options: Any) -> Completion:
        max_tokens = options.get("max_tokens")
        chat = self.factory(model, int(max_tokens) if max_tokens else None)
        if tools:
            # Liste CLOSE : exactement les outils que la boucle expose.
            chat = chat.bind_tools(tool_specs(tools))
        reply = chat.invoke(to_langchain(messages))
        if not isinstance(reply, AIMessage):
            raise TypeError(f"le modèle a rendu `{type(reply).__name__}`, pas un `AIMessage`")
        metadata: Mapping[str, Any] = reply.response_metadata or {}
        return Completion(
            text=_text(reply.content),
            model=str(metadata.get("model_name") or metadata.get("model") or model),
            usage=_usage(reply),
            tool_calls=tuple(ToolCall(id=str(c.get("id") or ""), name=str(c.get("name") or ""),
                                      arguments=_arguments(c.get("args")))
                             for c in reply.tool_calls),
            finish_reason=str(metadata.get("stop_reason") or metadata.get("finish_reason") or "stop"))


def _integration(module: str, name: str) -> Any:
    """La classe `name` du paquet d'intégration `module`, importée à la demande.

    Par `import_module` et non par une instruction `import` : seule
    l'intégration du fournisseur déclaré est installée (`onDemand` du
    catalogue), et un import statique des trois ferait tomber le paquet — et
    son typage strict — sur un projet Anthropic sans `langchain-openai`.
    """
    try:
        return getattr(import_module(module), name)
    except ImportError as exc:
        raise ConfigError(
            f"intégration LangChain `{module}` absente ({exc})", cls="CONFIG_INVALID",
            fix="régénérer `pyproject.toml` (gen-app-skeleton --write) : le catalogue du framework "
                "l'ajoute pour le `RuntimeProvider` déclaré") from None


def langchain_client(settings: Settings, timeout: float) -> LangChainClient:
    """Le client LangChain du fournisseur ACTIF."""
    provider = settings.provider.lower()
    api_key = settings.secret("llmApiKey").get_secret_value()

    if provider == "anthropic":
        chat_anthropic = _integration("langchain_anthropic", "ChatAnthropic")

        def anthropic_model(model: str, max_tokens: int | None) -> Any:
            # `max_tokens` est exigé par l'API Messages : 4096 comme l'adaptateur SDK.
            return chat_anthropic(model=model, api_key=api_key, timeout=timeout,
                                  max_tokens=max_tokens or 4096)
        return LangChainClient(factory=anthropic_model)

    if provider in ("openai", "google", "gemini"):
        chat_openai = _integration("langchain_openai", "ChatOpenAI")
        # Gemini par son point d'entrée compatible OpenAI, comme `models.provider_client` :
        # une seule traduction des appels d'outils, un seul comptage de tokens.
        base_url = GEMINI_OPENAI_BASE_URL if provider in ("google", "gemini") else None

        def openai_model(model: str, max_tokens: int | None) -> Any:
            return chat_openai(model=model, api_key=api_key, base_url=base_url, timeout=timeout,
                               max_tokens=max_tokens)
        return LangChainClient(factory=openai_model)

    if provider == "azure":
        endpoint = settings.provider_setting(AZURE_ENDPOINT_ENV)
        version = settings.provider_setting(AZURE_API_VERSION_ENV)
        missing = [n for n, v in ((AZURE_ENDPOINT_ENV, endpoint), (AZURE_API_VERSION_ENV, version)) if not v]
        if missing:
            raise ConfigError(f"Azure OpenAI : variable(s) {missing} non posée(s)",
                              fix="les poser dans `.env` — une ressource Azure n'a ni URL "
                                  "ni version par défaut")
        chat_azure = _integration("langchain_openai", "AzureChatOpenAI")
        deployments = {model: settings.provider_setting(AZURE_DEPLOYMENT_ENV.format(tier=tier.upper()))
                       for tier, model in settings.tier_map.items()}

        def azure_model(model: str, max_tokens: int | None) -> Any:
            return chat_azure(azure_deployment=deployments.get(model) or model, api_key=api_key,
                              azure_endpoint=endpoint, api_version=version, timeout=timeout,
                              max_tokens=max_tokens)
        return LangChainClient(factory=azure_model)

    raise ConfigError(
        f"fournisseur `{settings.provider}` sans intégration LangChain dans ce squelette",
        fix="déclarer anthropic, openai, azure ou google dans `## Runtime Models`, ou `stub`")
