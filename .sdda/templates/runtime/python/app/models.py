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
class Message:
    """Un message de la conversation. `name` porte l'outil pour un rôle `tool`."""

    role: str
    content: str
    name: str = ""
    tool_call_id: str = ""

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"rôle `{self.role}` hors liste close {list(ROLES)}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            out["name"] = self.name
        if self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        return out


@dataclass(frozen=True)
class ToolCall:
    """Un appel d'outil demandé par le modèle. `id` corrèle demande et résultat."""

    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


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
    if provider in ("stub", "none", ""):
        return StubClient()
    if provider == "anthropic":
        import anthropic  # noqa: PLC0415 - import paresseux volontaire

        return _AnthropicClient(anthropic.Anthropic(
            api_key=settings.secret("llmApiKey").get_secret_value()))
    if provider in ("openai", "azure"):
        import openai  # noqa: PLC0415 - import paresseux volontaire

        return _OpenAIClient(openai.OpenAI(
            api_key=settings.secret("llmApiKey").get_secret_value()))
    raise ConfigError(
        f"fournisseur `{settings.provider}` inconnu",
        fix="déclarer un fournisseur servi par `.sdda/providers/` dans `## Runtime Models`, "
            "ou `stub` pour un run qui n'appelle aucun modèle")


class _AnthropicClient:
    """Adaptateur minimal. Le SDK ne traverse jamais cette frontière."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def complete(self, messages: Sequence[Message], *, model: str,
                 tools: Sequence[Mapping[str, Any]] = (), **options: Any) -> Completion:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [m.to_dict() for m in messages if m.role != "system"]
        response = self._raw.messages.create(
            model=model, system=system, messages=turns,
            max_tokens=int(options.get("max_tokens", 4096)),
            **({"tools": list(tools)} if tools else {}))
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

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def complete(self, messages: Sequence[Message], *, model: str,
                 tools: Sequence[Mapping[str, Any]] = (), **options: Any) -> Completion:
        response = self._raw.chat.completions.create(
            model=model, messages=[m.to_dict() for m in messages],
            **({"tools": list(tools)} if tools else {}))
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
