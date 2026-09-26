"""Tier -> client -> tokens. GÉNÉRÉ, ne pas éditer.

Le seul point de contact avec un fournisseur de modèle, et le seul endroit où un
identifiant de modèle circule. Le reste du code manipule un **tier** (`fast`,
`balanced`, `deep`) : un nom de modèle en dur ailleurs est
`[MODEL_NAME_HARDCODED]`, parce qu'il rend la substitution de fournisseur
impossible sans relire tout le paquet — et parce qu'il désigne, six mois plus
tard, un modèle qui n'a plus le même prix ni le même comportement.

Trois choses vivent ici, et rien d'autre :

1. **La résolution du tier**, lue dans `app_config.json` (`## Runtime Models` de
   STACK.md). Aucun défaut : un tier non déclaré lève, il ne retombe pas sur un
   modèle « raisonnable ».
2. **Un client minimal**, `LLMClient`, volontairement pauvre : un message, des
   outils éventuels, une `Completion`. Un protocole riche obligerait chaque
   implémentation à porter la surface complète d'un SDK.
3. **Le comptage des tokens**, parce que c'est lui qui rend le coût
   *recalculable*. Un client qui déclare un coût sans ses tokens fournit une
   affirmation ; `RecordingClient` fournit une mesure.

Le coût est RECALCULÉ, jamais déclaré
--------------------------------------
`cost_usd()` part des tokens et de la table de tarifs versionnée. La valeur que
le span porte dans `sdda.cost.usd` est produite par ce calcul — et le framework
la recalcule à nouveau à la lecture (`sdda_lib/tracing.py`), précisément pour
que l'écart apparaisse si les deux divergent un jour. Un modèle absent de la
table ne rend pas zéro : il rend `None` et un problème nommé. Zéro passerait
sous n'importe quel plafond sans rien dire, et c'est le faux vert le plus cher
de tout le pipeline.

Aucun SDK n'est importé au chargement du module : `provider_client()` importe
`anthropic` ou `openai` **dans** la fonction. Le squelette reste ainsi
importable sur un clone nu, sans clé, ce qui est la condition pour que les
tests L0/L1 et le `health` tournent en CI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from .config import ConfigError, Settings

#: Rôles admis dans un échange. Liste close : un rôle inventé est silencieusement
#: ignoré par certains fournisseurs et pris pour un `user` par d'autres.
ROLES: tuple[str, ...] = ("system", "user", "assistant", "tool")


@dataclass(frozen=True)
class ToolCall:
    """Un appel d'outil demandé par le modèle. `id` corrèle demande et résultat."""

    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Message:
    """Un message de la conversation. `name` porte l'outil pour un rôle `tool`.

    `tool_calls` porte les appels qu'un tour `assistant` a demandés. Sans lui,
    l'historique renvoyé au modèle perdait ses demandes d'outil : les résultats
    arrivaient au tour suivant sans la demande qu'ils honorent, ce qu'Anthropic
    refuse (un `tool_result` doit suivre son `tool_use`) et qu'OpenAI refuse
    aussi (un message `tool` doit suivre un `tool_calls`). Tout run à outils
    échouait au deuxième tour.
    """

    role: str
    content: str
    name: str = ""
    tool_call_id: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    #: Résultat d'outil en ÉCHEC (erreur déclarée). Anthropic le lit
    #: (`is_error`) : sans lui, un échec rendu comme contenu ordinaire est pris
    #: pour une réponse, et le modèle raisonne sur un message d'erreur.
    is_error: bool = False

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"rôle `{self.role}` hors liste close {list(ROLES)}")

    def to_dict(self) -> dict[str, Any]:
        """La forme « chat completions » (OpenAI et compatibles)."""
        import json  # noqa: PLC0415

        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            out["name"] = self.name
        if self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            out["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(dict(c.arguments), ensure_ascii=False)}}
                for c in self.tool_calls]
        return out


def _tool_spec_parts(spec: Mapping[str, Any]) -> tuple[str, str, Mapping[str, Any]]:
    """(nom, description, schéma d'entrée) d'une spec d'outil, quelle que soit sa graphie.

    La composition émet la forme `{"type": "function", "function": {…, "parameters"}}` ;
    un appelant peut aussi passer `{name, description, input_schema | parameters}`.
    Chaque adaptateur rend ensuite la forme de SON fournisseur : envoyer la forme
    OpenAI à Anthropic faisait refuser la requête dès qu'un outil était exposé.
    """
    function = spec.get("function")
    inner: Mapping[str, Any] = function if isinstance(function, Mapping) else spec
    schema = inner.get("parameters") or inner.get("input_schema") or {"type": "object"}
    return str(inner.get("name") or ""), str(inner.get("description") or ""), schema


@dataclass(frozen=True)
class Usage:
    """Ce qui a été consommé. Les tokens de cache sont SÉPARÉS.

    Les compter dans `input_tokens` surestime le coût d'un ordre de grandeur sur
    un prompt long et caché : le tarif de lecture de cache vaut souvent un
    dixième du tarif d'entrée. Un budget faux par excès se traduit par une
    architecture rabotée pour rien.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True)
class Completion:
    """Ce qu'un client rend. `usage` n'est jamais optionnel.

    `CostTrackingEnabled` impose qu'un `usage` absent soit une erreur
    (`[TRACE_USAGE_MISSING]`) et non un zéro silencieux : un run sans tokens
    passe tous les plafonds, y compris ceux qu'il crève.
    """

    text: str
    model: str
    usage: Usage = field(default_factory=Usage)
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"


@runtime_checkable
class LLMClient(Protocol):
    """Le contrat minimal d'un client de modèle.

    Volontairement pauvre : tout ce que la boucle bornée a besoin de savoir
    faire. Un protocole qui exposerait le streaming, les images et les fichiers
    obligerait `StubClient` — donc les tests — à implémenter des méthodes que
    personne n'appelle, et cesserait vite d'être implémenté honnêtement.
    """

    def complete(self, messages: Sequence[Message], *, model: str,
                 tools: Sequence[Mapping[str, Any]] = (), **options: Any) -> Completion: ...


# ---------------------------------------------------------------------------
# Coût — recalculé depuis les tokens, jamais relu
# ---------------------------------------------------------------------------
def cost_usd(model: str, usage: Usage, pricing: Mapping[str, Mapping[str, float]]
             ) -> tuple[float | None, str | None]:
    """(coût en USD, problème). Un modèle hors table rend `None`, jamais `0.0`.

    Rendre `(None, "…")` plutôt que lever : un run ne doit pas s'interrompre
    parce qu'un tarif manque, mais le rapport ne doit pas non plus prétendre
    avoir mesuré. Le problème remonte dans le span et dans le rapport d'eval,
    là où quelqu'un le lira avant de conclure « sous le plafond ».
    """
    rates = pricing.get(model)
    if not rates:
        return None, (f"modèle `{model}` absent de la table de tarifs "
                      "[BUDGET_PRICING_UNKNOWN] : coût non recalculable")
    usd = (usage.input_tokens * float(rates.get("input", 0.0))
           + usage.output_tokens * float(rates.get("output", 0.0))
           + usage.cache_read_tokens * float(rates.get("cache_read", 0.0))
           + usage.cache_write_tokens * float(rates.get("cache_creation", 0.0))) / 1e6
    return round(usd, 6), None


# ---------------------------------------------------------------------------
# Résolution du tier
# ---------------------------------------------------------------------------
#: Point d'entrée compatible OpenAI de l'API Gemini (Google AI Studio).
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


#: Exceptions des SDK fournisseurs (anthropic, openai — Gemini passe par ce
#: dernier) qui disent « le modèle n'a pas pu répondre », par nom de classe :
#: le squelette ne doit pas importer un SDK pour le reconnaître.
PROVIDER_AUTH_ERRORS = frozenset({"AuthenticationError", "PermissionDeniedError"})
PROVIDER_DOWN_ERRORS = frozenset({"APIConnectionError", "APITimeoutError", "InternalServerError",
                                  "ServiceUnavailableError", "OverloadedError", "RateLimitError"})


def provider_error_class(exc: BaseException) -> str | None:
    """`[CLASS]` d'une panne du FOURNISSEUR, ou None si l'exception vient d'ailleurs.

    Distinguée d'`INTERNAL_ERROR` parce qu'elle ne dit rien de l'agent : une clé
    refusée faisait noter 0.000 chaque item d'une suite, et le rapport concluait
    « l'agent répond faux » là où aucun appel n'avait abouti.
    """
    name = type(exc).__name__
    if name in PROVIDER_AUTH_ERRORS:
        return "LLM_PROVIDER_AUTH_FAILED"
    if name in PROVIDER_DOWN_ERRORS:
        return "LLM_PROVIDER_UNAVAILABLE"
    return None


def resolve(tier: str | None, settings: Settings) -> str:
    """Tier -> identifiant de modèle, déclaré dans `## Runtime Models`."""
    return settings.model_for(tier)


def provider_client(settings: Settings) -> LLMClient:
    """Le client du fournisseur ACTIF, importé paresseusement.

    L'import tardif n'est pas une optimisation : il fait que `config`, `bounds`,
    `tracing` et la CLI restent importables sans aucun SDK installé. Un
    squelette qui exige `pip install` avant de pouvoir être inspecté est un
    squelette qu'on n'inspecte pas.
    """
    provider = settings.provider.lower()
    # Le délai du SDK est la borne de temps déclarée : sans lui, le client
    # attend le défaut du fournisseur (dix minutes chez certains), bien au-delà
    # de `timeout_s`, et la boucle ne reprend la main qu'après.
    timeout = float(settings.bounds.get("timeout_s") or 60.0)
    if provider in ("stub", "none", ""):
        return StubClient()
    if provider == "anthropic":
        import anthropic  # noqa: PLC0415 - import paresseux volontaire

        return _AnthropicClient(anthropic.Anthropic(
            api_key=settings.secret("llmApiKey").get_secret_value(), timeout=timeout))
    if provider == "openai":
        import openai  # noqa: PLC0415 - import paresseux volontaire

        return _OpenAIClient(openai.OpenAI(
            api_key=settings.secret("llmApiKey").get_secret_value(), timeout=timeout))
    if provider == "azure":
        return _azure_client(settings, timeout)
    if provider in ("google", "gemini"):
        # Gemini expose une API compatible OpenAI : un seul client, une seule
        # traduction des appels d'outils, et le même comptage de tokens dans les
        # spans. Le SDK natif n'apporterait rien que le squelette utilise.
        import openai  # noqa: PLC0415 - import paresseux volontaire

        return _OpenAIClient(openai.OpenAI(
            api_key=settings.secret("llmApiKey").get_secret_value(),
            base_url=GEMINI_OPENAI_BASE_URL, timeout=timeout), max_tokens_param="max_tokens")
    raise ConfigError(
        f"fournisseur `{settings.provider}` inconnu",
        fix="déclarer un fournisseur servi par `.sdda/providers/` dans `## Runtime Models`, "
            "ou `stub` pour un run qui n'appelle aucun modèle")


#: Les variables d'une ressource Azure OpenAI (`providers/azure-openai.yaml`).
AZURE_ENDPOINT_ENV = "AZURE_OPENAI_ENDPOINT"
AZURE_API_VERSION_ENV = "AZURE_OPENAI_API_VERSION"
AZURE_DEPLOYMENT_ENV = "AZURE_OPENAI_DEPLOYMENT_{tier}"


def _azure_client(settings: Settings, timeout: float) -> LLMClient:
    """Le client Azure : une RESSOURCE (endpoint + version d'API) et un DÉPLOIEMENT par tier.

    Le client `openai.OpenAI` nu envoyait la clé Azure à `api.openai.com`, et le
    nom de modèle à la place du nom de déploiement : aucun appel ne pouvait
    aboutir. Chaque valeur manquante est dite au démarrage, par son NOM.
    """
    endpoint = settings.provider_setting(AZURE_ENDPOINT_ENV)
    version = settings.provider_setting(AZURE_API_VERSION_ENV)
    missing = [n for n, v in ((AZURE_ENDPOINT_ENV, endpoint), (AZURE_API_VERSION_ENV, version)) if not v]
    if missing:
        raise ConfigError(f"Azure OpenAI : variable(s) {missing} non posée(s)",
                          fix="les poser dans `.env` — une ressource Azure n'a ni URL ni version par défaut")
    deployments: dict[str, str] = {}
    for tier, model in settings.tier_map.items():
        deployment = settings.provider_setting(AZURE_DEPLOYMENT_ENV.format(tier=tier.upper()))
        if deployment:
            deployments[model] = deployment
    import openai  # noqa: PLC0415 - import paresseux volontaire

    return _OpenAIClient(openai.AzureOpenAI(
        api_key=settings.secret("llmApiKey").get_secret_value(), azure_endpoint=endpoint,
        api_version=version, timeout=timeout), deployments=deployments)


class _AnthropicClient:
    """Adaptateur minimal. Le SDK ne traverse jamais cette frontière."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @staticmethod
    def turns(messages: Sequence[Message]) -> list[dict[str, Any]]:
        """L'historique dans la grammaire de l'API Messages.

        - un tour `assistant` qui a demandé des outils porte ses blocs `tool_use` ;
        - les messages `tool` consécutifs deviennent UN tour `user` de blocs
          `tool_result` (l'API n'a pas de rôle `tool`, et exige tous les
          résultats d'un tour dans le message qui le suit).
        """
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                block: dict[str, Any] = {"type": "tool_result", "tool_use_id": m.tool_call_id,
                                         "content": m.content}
                if m.is_error:
                    block["is_error"] = True
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and all(b.get("type") == "tool_result" for b in out[-1]["content"]):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
                continue
            if m.role == "assistant" and m.tool_calls:
                blocks: list[dict[str, Any]] = [{"type": "text", "text": m.content}] if m.content else []
                blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": dict(c.arguments)}
                           for c in m.tool_calls]
                out.append({"role": "assistant", "content": blocks})
                continue
            out.append({"role": m.role, "content": m.content})
        return out

    @staticmethod
    def tool_specs(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        specs = []
        for spec in tools:
            name, description, schema = _tool_spec_parts(spec)
            specs.append({"name": name, "description": description, "input_schema": dict(schema)})
        return specs

    def complete(self, messages: Sequence[Message], *, model: str,
                 tools: Sequence[Mapping[str, Any]] = (), **options: Any) -> Completion:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        response = self._raw.messages.create(
            model=model, system=system, messages=self.turns(messages),
            max_tokens=int(options.get("max_tokens", 4096)),
            **({"tools": self.tool_specs(tools)} if tools else {}))
        usage = getattr(response, "usage", None)
        blocks = [b for b in getattr(response, "content", []) if getattr(b, "type", "") == "text"]
        return Completion(
            text="".join(getattr(b, "text", "") for b in blocks),
            model=str(getattr(response, "model", model)),
            usage=Usage(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0)),
            tool_calls=tuple(
                ToolCall(id=str(getattr(b, "id", "")), name=str(getattr(b, "name", "")),
                         arguments=dict(getattr(b, "input", {}) or {}))
                for b in getattr(response, "content", []) if getattr(b, "type", "") == "tool_use"),
            finish_reason=str(getattr(response, "stop_reason", "stop") or "stop"))


class _OpenAIClient:
    """Adaptateur minimal, même frontière."""

    def __init__(self, raw: Any, *, deployments: Mapping[str, str] | None = None,
                 max_tokens_param: str = "max_completion_tokens") -> None:
        self._raw = raw
        #: modèle -> déploiement (Azure) ; vide ailleurs, le modèle est envoyé tel quel.
        self._deployments = dict(deployments or {})
        #: `max_tokens` est déprécié chez OpenAI et refusé par les modèles de
        #: raisonnement ; l'API compatible de Gemini, elle, ne lit que lui.
        self._max_tokens_param = max_tokens_param

    def complete(self, messages: Sequence[Message], *, model: str,
                 tools: Sequence[Mapping[str, Any]] = (), **options: Any) -> Completion:
        extra: dict[str, Any] = {}
        if options.get("max_tokens"):
            extra[self._max_tokens_param] = int(options["max_tokens"])
        specs = []
        for spec in tools:
            name, description, schema = _tool_spec_parts(spec)
            specs.append({"type": "function", "function": {"name": name, "description": description,
                                                           "parameters": dict(schema)}})
        response = self._raw.chat.completions.create(
            model=self._deployments.get(model, model), messages=[m.to_dict() for m in messages],
            **({"tools": specs} if specs else {}), **extra)
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        details = getattr(usage, "prompt_tokens_details", None)
        return Completion(
            text=str(getattr(choice.message, "content", "") or ""),
            model=str(getattr(response, "model", model)),
            usage=Usage(
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                cache_read_tokens=int(getattr(details, "cached_tokens", 0) or 0)),
            tool_calls=tuple(
                ToolCall(id=str(c.id), name=str(c.function.name),
                         arguments=_loads(getattr(c.function, "arguments", "")))
                for c in (getattr(choice.message, "tool_calls", None) or [])),
            finish_reason=str(getattr(choice, "finish_reason", "stop") or "stop"))


def _loads(payload: Any) -> dict[str, Any]:
    import json  # noqa: PLC0415 - utilisé par les seuls adaptateurs

    try:
        value = json.loads(payload) if isinstance(payload, str) else payload
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Enveloppes : mesure et double de test
# ---------------------------------------------------------------------------
@dataclass
class RecordingClient:
    """Enveloppe un client et retient ce qu'il a consommé.

    C'est ici que « le coût est une mesure » devient vrai : chaque appel est
    conservé avec ses tokens, et le coût s'obtient en les repassant dans la
    table de tarifs. Aucun fournisseur n'est cru sur parole, y compris quand il
    renvoie lui-même un montant.

    L'enveloppe est aussi le point d'observation le plus honnête pour un test :
    « combien d'appels au modèle ce tour a-t-il coûté ? » se répond sans ouvrir
    la boucle, donc sans figer sa structure interne dans une assertion.
    """

    inner: LLMClient
    pricing: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    calls: list[Completion] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def complete(self, messages: Sequence[Message], *, model: str,
                 tools: Sequence[Mapping[str, Any]] = (), **options: Any) -> Completion:
        completion = self.inner.complete(messages, model=model, tools=tools, **options)
        self.calls.append(completion)
        _, problem = cost_usd(completion.model or model, completion.usage, self.pricing)
        if problem:
            self.problems.append(problem)
        return completion

    @property
    def usage(self) -> Usage:
        total = Usage()
        for call in self.calls:
            total = total + call.usage
        return total

    @property
    def cost_usd(self) -> float:
        """Le coût des appels dont le tarif est connu. Les autres sont dans `problems`."""
        total = 0.0
        for call in self.calls:
            usd, _ = cost_usd(call.model, call.usage, self.pricing)
            total += usd or 0.0
        return round(total, 6)


@dataclass
class StubClient:
    """Un modèle qui ne coûte rien et répond toujours la même chose.

    Il sert deux usages qu'on aurait tort de séparer : les tests, et le run réel
    d'un projet sans clé d'API. Le second compte autant que le premier — au
    premier `bootstrap`, la CLI doit pouvoir démarrer, écrire sa trace et rendre
    un code de sortie, sinon rien de la plomberie n'est vérifiable avant d'avoir
    ouvert un compte chez un fournisseur.

    Déterministe par construction : même entrée, même sortie, mêmes tokens. Une
    eval rejouée sur le stub mesure donc la plomberie et rien d'autre, ce qui
    est exactement ce qu'on lui demande.
    """

    answer: str = "stub"
    script: Sequence[Completion] = ()
    on_call: Callable[[Sequence[Message]], Completion] | None = None
    calls: list[list[Message]] = field(default_factory=list)

    def complete(self, messages: Sequence[Message], *, model: str,
                 tools: Sequence[Mapping[str, Any]] = (), **options: Any) -> Completion:
        self.calls.append(list(messages))
        if self.on_call is not None:
            return self.on_call(messages)
        index = len(self.calls) - 1
        if index < len(self.script):
            scripted = self.script[index]
            return Completion(text=scripted.text, model=scripted.model or model,
                              usage=scripted.usage, tool_calls=scripted.tool_calls,
                              finish_reason=scripted.finish_reason)
        prompt = " ".join(m.content for m in messages)
        # Des tokens plausibles plutôt que zéro : un zéro ferait passer tout
        # test de budget au vert sans rien prouver.
        return Completion(
            text=self.answer, model=model,
            usage=Usage(input_tokens=max(1, len(prompt) // 4),
                        output_tokens=max(1, len(self.answer) // 4)),
            finish_reason="stop")


def stub_client(answer: str = "stub") -> StubClient:
    return StubClient(answer=answer)
