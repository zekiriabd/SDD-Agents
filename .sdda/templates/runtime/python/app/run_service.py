"""`RunService` — le point d'entrée UNIQUE de toutes les surfaces. GÉNÉRÉ, ne pas éditer.

La CLI, l'API HTTP, le serveur MCP, le bot, le traitement par lot : tous
appellent ceci, et seul le transport change. C'est ce qui empêche la dérive la
plus banale et la plus coûteuse d'un système agentic — la CLI qui borne le
budget et l'API qui ne le borne pas, l'API qui enveloppe l'entrée non maîtrisée
et le lot qui l'oublie. Quand chaque surface porte sa propre sémantique, ce
qu'on mesure en eval n'est plus ce qu'on livre en production, et l'écart ne se
voit qu'en incident.

Ce que le service garantit, quelle que soit la surface qui l'appelle :

    1. un `run_id`, un fichier de trace, un span racine — toujours ;
    2. l'entrée traitée comme non maîtrisée, et bornée en taille ;
    3. les bornes appliquées, et leur dépassement traduit en résultat ;
    4. un coût RECALCULÉ depuis les tokens, jamais relu ;
    5. une suite d'événements identique (schéma `RunEvent`, `event_schema`) ;
    6. `run_finished` en dernier, toujours, y compris après une erreur.

Les événements et le résultat ne s'opposent pas : `run()` rend un `RunResult`
structuré **et** appelle `on_event` au fil de l'eau. Une surface qui streame
consomme les seconds ; une surface en lot lit le premier ; le runner d'eval lit
les deux. N'exposer qu'un flux obligerait chaque appelant à le réagréger, et
chacun le ferait un peu différemment.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Callable

from .bounds import Bounds
from .config import ConfigError, Settings
from .guardrails import Guardrails, GuardrailTripped
from .models import LLMClient, RecordingClient, StubClient, provider_client, provider_error_class, resolve
from .orchestration.base import AgentResult, BoundedLoop, DictToolset, declare_framework_graph
from .tracing import Tracer, new_run_id
from .trust import untrusted

#: Version du schéma d'événement. Un consommateur qui lit du NDJSON doit pouvoir
#: refuser une version qu'il ne connaît pas plutôt que d'ignorer des champs en
#: silence — un champ ignoré, c'est un `degraded: true` qui ne se voit pas.
EVENT_SCHEMA = "1"

#: La grammaire close des événements. Un événement hors liste est un événement
#: qu'aucune surface ne sait afficher et qu'aucun test ne couvre.
EVENTS: tuple[str, ...] = (
    "run_started", "agent_started", "agent_finished", "token", "tool_call", "tool_result",
    "retrieval", "guardrail", "bound_exceeded", "interrupted", "final", "error", "run_finished",
)

#: Bornes de repli quand ni l'IR ni `app_config.json` n'en déclarent. Elles ne
#: sont PAS des valeurs par défaut au sens de `Bounds` (qui n'en a aucune) :
#: c'est le minimum qui permet au squelette de tourner avant que la topologie
#: existe, et il est volontairement serré pour qu'un oubli coûte peu.
STARTER_BOUNDS: dict[str, Any] = {
    "max_iterations": 4, "max_tool_calls": 8, "max_delegation_depth": 0,
    "timeout_s": 60.0, "budget_usd": 0.50, "on_bound_exceeded": "fail-explicit",
}


@dataclass(frozen=True)
class RunRequest:
    """Ce qu'une surface passe au service. Rien d'autre n'entre.

    `tenant_id` est l'identité de l'appelant établie par le TRANSPORT. Elle ne
    traverse jamais le modèle et n'est jamais un paramètre d'outil : un filtre
    d'identité qui passe par le prompt est un filtre qu'une phrase suffit à
    lever.
    """

    input: str
    thread_id: str = ""
    tenant_id: str = ""
    run_id: str = ""
    max_budget_usd: float | None = None
    surface: str = "cli"


@dataclass
class RunResult:
    """Ce que le service rend. Structuré, et suffisant pour un code de sortie."""

    run_id: str
    output: Any = None
    status: str = "ok"
    error_class: str = ""
    message: str = ""
    degraded: bool = False
    cost_usd: float = 0.0
    latency_ms: int = 0
    hops: int = 0
    tool_calls: int = 0
    bound_exceeded: str = ""
    bound_policy: str = ""
    trace_path: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"runId": self.run_id, "output": self.output, "status": self.status,
                "degraded": self.degraded, "errorClass": self.error_class,
                "message": self.message, "costUsd": round(self.cost_usd, 6),
                "latencyMs": self.latency_ms, "hops": self.hops,
                "toolCalls": self.tool_calls, "boundExceeded": self.bound_exceeded,
                "boundPolicy": self.bound_policy, "tracePath": self.trace_path,
                "problems": self.problems}


class RunService:
    """La mission, exécutée. Une instance par configuration, réutilisable.

    `agent_factory` est le point d'extension qu'utilise `dev-orchestration` :
    il rend l'objet exécutable (boucle bornée, graphe LangGraph compilé,
    pipeline) à partir des dépendances résolues ici. Tant qu'il n'est pas
    fourni, le service câble la boucle bornée de `orchestration/base.py` — ce
    qui suffit à faire tourner un `single-agent`, donc à valider la plomberie
    avant qu'une topologie existe.
    """

    def __init__(self, settings: Settings | None = None, *,
                 client: LLMClient | None = None,
                 agent_factory: Callable[..., Any] | None = None,
                 toolset: DictToolset | None = None,
                 bounds: Bounds | None = None,
                 system_prompt: str = "",
                 tracer_factory: Callable[[str], Tracer] | None = None,
                 guardrails: Guardrails | None = None) -> None:
        self.settings = settings or Settings.load()
        self._guardrails = guardrails
        self._client = client
        self._agent_factory = agent_factory
        if agent_factory is not None:
            # Un graphe de framework porte l'orchestration : c'est lui qui émet
            # le manifeste, et la boucle de démarrage cesse d'écrire le sien.
            declare_framework_graph(getattr(agent_factory, "__module__", None) or "agent_factory")
        self._toolset = toolset or DictToolset()
        self._bounds = bounds
        self._system_prompt = system_prompt
        self._tracer_factory = tracer_factory

    # -- Dépendances résolues -----------------------------------------------
    def bounds(self) -> Bounds:
        declared = dict(STARTER_BOUNDS)
        declared.update({k: v for k, v in (self.settings.bounds or {}).items() if v is not None})
        return self._bounds or Bounds.from_mapping(declared)

    def client(self) -> LLMClient:
        """Le client, enveloppé pour que ses tokens soient comptés.

        `RecordingClient` n'est pas optionnel : sans lui, le coût du run serait
        ce que le fournisseur déclare, c'est-à-dire une affirmation. Avec lui,
        c'est un calcul sur des tokens qu'on a vus passer.
        """
        inner = self._client if self._client is not None else provider_client(self.settings)
        if isinstance(inner, RecordingClient):
            return inner
        return RecordingClient(inner=inner, pricing=self.settings.pricing)

    def guardrails(self) -> Guardrails:
        """Les guardrails DÉCLARÉS (`app_config.json` <- `## Active Guardrails`)."""
        if self._guardrails is None:
            self._guardrails = Guardrails.from_settings(self.settings)
        return self._guardrails

    def tracer(self, run_id: str) -> Tracer:
        if self._tracer_factory is not None:
            return self._tracer_factory(run_id)
        return Tracer.for_run(self.settings, run_id)

    # -- Exécution ----------------------------------------------------------
    async def run(self, request: RunRequest, *,
                  on_event: Callable[[dict[str, Any]], None] | None = None) -> RunResult:
        run_id = request.run_id or new_run_id()
        tracer = self.tracer(run_id)
        result = RunResult(run_id=run_id, trace_path=str(tracer.path or ""))

        def emit(event: str, **payload: Any) -> dict[str, Any]:
            if event not in EVENTS:
                raise ValueError(f"événement `{event}` hors grammaire close {list(EVENTS)}")
            record = {"event": event, "event_schema": EVENT_SCHEMA, "run_id": run_id, **payload}
            result.events.append(record)
            if on_event is not None:
                on_event(record)
            return record

        with tracer.run_span(mission_id=self.settings.mission_id,
                             surface=request.surface) as root:
            try:
                text = self._read_input(request)
                bounds = self._effective_bounds(request)
                emit("run_started", thread_id=request.thread_id,
                     mission_id=self.settings.mission_id,
                     bounds=bounds.to_dict())

                # Le texte non maîtrisé ENTRE ici : injection directe, puis PII,
                # AVANT que le modèle — donc la trace et le fournisseur — ne le
                # voie. Un refus lève `GuardrailTripped`, traduit plus bas.
                guards = self.guardrails()
                text, verdict = guards.check_input(text, tracer=tracer)
                if verdict is not None and verdict.hits:
                    emit("guardrail", guardrail="injection-detection", point="user_input",
                         **verdict.to_dict())

                agent = self._build_agent(bounds, tracer)
                emit("agent_started", agent_id=getattr(agent, "agent_id", "agent"))
                outcome = agent.run(untrusted(text), thread_id=request.thread_id)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
                agent_result: AgentResult = outcome
                emit("agent_finished", agent_id=getattr(agent, "agent_id", "agent"),
                     iterations=agent_result.iterations)

                # La sortie SORT ici : validée contre l'`outputSchema` de l'IR
                # avant d'être rendue. Une sortie non conforme n'est pas une
                # réponse dégradée, c'est une réponse fausse dans sa forme.
                if agent_result.status == "ok":
                    checked, violations = guards.check_output(
                        agent_result.output, agent_id=str(getattr(agent, "agent_id", "")), tracer=tracer)
                    if violations:
                        agent_result.status = "failed"
                        agent_result.error_class = "AGENT_OUTPUT_INVALID"
                        agent_result.message = "sortie non conforme au schéma : " + "; ".join(violations[:5])
                        emit("guardrail", guardrail="schema-validation", point="final_output",
                             violations=violations[:10])
                    else:
                        agent_result.output = checked

                self._absorb(result, agent_result, tracer)
                if agent_result.bound_exceeded:
                    emit("bound_exceeded", bound=agent_result.bound_exceeded,
                         policy=agent_result.bound_policy,
                         observed=agent_result.partial_state.get("cost_usd"))
                if agent_result.status == "interrupted":
                    emit("interrupted", thread_id=request.thread_id,
                         reason=agent_result.message)
                elif agent_result.status == "failed":
                    root.error(agent_result.error_class or "AGENT_FAILED")
                    emit("error", **{"class": agent_result.error_class or "INTERNAL_ERROR",
                                     "message": agent_result.message})
                else:
                    emit("final", output=agent_result.output, degraded=agent_result.degraded)
            except GuardrailTripped as exc:
                # Un refus est le résultat ATTENDU d'une attaque (L8) : statut
                # `failed` et classe `SAFETY_GUARDRAIL_TRIPPED`, que `exit_codes`
                # traduit en 4 (REFUSED) — jamais en 1, qui compterait le refus
                # comme une panne.
                root.error(exc.cls)
                result.status, result.error_class, result.message = "failed", exc.cls, str(exc)
                emit("guardrail", guardrail=exc.guardrail, point="user_input", action="blocked",
                     **exc.verdict.to_dict())
                emit("error", **{"class": exc.cls, "message": str(exc)})
            except ConfigError as exc:
                root.error(exc.cls)
                result.status, result.error_class, result.message = "failed", exc.cls, str(exc)
                emit("error", **{"class": exc.cls, "message": str(exc)})
            except Exception as exc:  # noqa: BLE001 - la surface doit rendre un code, pas une pile
                root.error(type(exc).__name__)
                # Une clé refusée ou un fournisseur injoignable n'est pas une
                # panne de l'agent : l'eval doit le dire « non mesuré », pas
                # noter 0 un agent qui n'a jamais reçu de réponse du modèle.
                cls = provider_error_class(exc) or "INTERNAL_ERROR"
                result.status, result.error_class = "failed", cls
                result.message = f"{type(exc).__name__}: {exc}"
                emit("error", **{"class": cls, "message": result.message})

        # Après le span racine : sa durée est la latence du run, et elle n'est
        # connue qu'une fois le span fermé.
        summary = _summarize(tracer)
        result.hops = summary["hops"]
        result.tool_calls = summary["tool_calls"]
        result.latency_ms = summary["latency_ms"]
        result.trace = tracer.as_trace()
        result.problems.extend(tracer.problems)
        # `run_finished` est TOUJOURS le dernier événement, y compris après une
        # erreur : c'est lui qui porte le chemin de trace et le coût, c'est-à-dire
        # ce qu'on va lire justement quand quelque chose a mal tourné.
        emit("run_finished", status=result.status, cost_usd=round(result.cost_usd, 6),
             duration_ms=result.latency_ms, hops=result.hops,
             tool_calls=result.tool_calls, trace_path=result.trace_path)
        return result

    def run_sync(self, request: RunRequest, *,
                 on_event: Callable[[dict[str, Any]], None] | None = None) -> RunResult:
        """La même chose, pour un appelant qui n'est pas asynchrone.

        La CLI et l'exécuteur d'eval passent par ici. Concentrer l'`asyncio.run`
        en un seul endroit évite le piège n°2 de `serving/cli.md` : un
        `sys.exit` dans du code async saute le vidage des traces.
        """
        import asyncio  # noqa: PLC0415 - un seul point d'entrée dans la boucle d'événements

        return asyncio.run(self.run(request, on_event=on_event))

    # -- Détails ------------------------------------------------------------
    def _read_input(self, request: RunRequest) -> str:
        raw = request.input or ""
        size = len(raw.encode("utf-8"))
        if size > self.settings.max_input_bytes:
            raise ConfigError(
                f"entrée de {size} octets au-delà de `maxInputBytes` "
                f"({self.settings.max_input_bytes})",
                cls="CLI_USAGE",
                fix="tronquer ou découper en amont — la coupure doit être une décision de "
                    "l'appelant, pas un silence du modèle qui ignore la fin du contexte")
        return raw

    def _effective_bounds(self, request: RunRequest) -> Bounds:
        """Les bornes du run. `--max-budget-usd` ne peut que BAISSER le plafond.

        Autoriser la hausse ferait de la ligne de commande un moyen de
        contourner le budget déclaré, c'est-à-dire de rendre P6 décoratif.
        """
        bounds = self.bounds()
        requested = request.max_budget_usd
        if requested is None:
            return bounds
        if requested > bounds.budget_usd:
            raise ConfigError(
                f"`max_budget_usd={requested}` au-dessus du plafond déclaré "
                f"({bounds.budget_usd})",
                cls="CLI_USAGE",
                fix="la surface peut baisser un plafond, jamais le relever")
        return Bounds(max_iterations=bounds.max_iterations,
                      max_tool_calls=bounds.max_tool_calls,
                      max_delegation_depth=bounds.max_delegation_depth,
                      timeout_s=bounds.timeout_s, budget_usd=requested,
                      on_bound_exceeded=bounds.on_bound_exceeded)

    def _build_agent(self, bounds: Bounds, tracer: Tracer) -> Any:
        client = self.client()
        if self._agent_factory is not None:
            return self._agent_factory(bounds=bounds, tracer=tracer, client=client,
                                       settings=self.settings, toolset=self._toolset)
        return BoundedLoop(
            agent_id="agent", agent_name="agent", bounds=bounds, client=client,
            model=resolve(self.settings.default_tier, self.settings),
            tier=self.settings.default_tier, system_prompt=self._system_prompt,
            toolset=self._toolset, tracer=tracer, guardrails=self.guardrails())

    def _absorb(self, result: RunResult, agent: AgentResult, tracer: Tracer) -> None:
        result.output = agent.output
        result.status = agent.status
        result.degraded = agent.degraded
        result.message = agent.message or result.message
        result.error_class = agent.error_class or result.error_class
        result.bound_exceeded = agent.bound_exceeded
        result.bound_policy = agent.bound_policy
        result.cost_usd = agent.cost_usd


def _summarize(tracer: Tracer) -> dict[str, Any]:
    """Hops, appels d'outils et latence, LUS DANS LA TRACE.

    Pas recomptés depuis des variables du service : ce que la gate mesurera est
    ce que le fichier contient, et un compteur local qui diverge de la trace
    produirait deux vérités dont la plus flatteuse serait dans le rapport.
    """
    hops = tool_calls = 0
    latency = 0
    for span in tracer.spans:
        attrs = span.get("attributes") or {}
        operation = str(attrs.get("gen_ai.operation.name") or "")
        if operation == "invoke_agent":
            hops += 1
        elif operation == "execute_tool":
            tool_calls += 1
        if str(span.get("name") or "").startswith("sdda.run"):
            latency = int(span.get("duration_ms") or 0)
    return {"hops": hops, "tool_calls": tool_calls, "latency_ms": latency}


def app_build_system() -> Callable[..., RunService] | None:
    """`build_system` de la composition de l'application, si elle existe.

    La composition est écrite par `dev-backend` (`app/composition.py`), après le
    squelette : on la cherche au moment de construire, pas à l'import.
    """
    if not __package__:
        return None
    try:
        module = import_module(f"{__package__}.app.composition")
    except ImportError:
        return None
    fn = getattr(module, "build_system", None)
    return fn if callable(fn) else None


def composed_service(settings: Settings | None = None, **kwargs: Any) -> RunService:
    """Le système que l'APPLICATION compose — ce que toute surface doit servir.

    `build_system` câble l'agent de l'IR, son prompt épinglé et ses outils. Un
    `RunService` nu fait tourner la boucle de démarrage, au prompt vide et sans
    outil : la CLI rendait ainsi la réponse d'un modèle nu, que le schéma de
    sortie refusait (code 9), alors que l'exécuteur d'eval mesurait le vrai
    agent. Repli sur `RunService` seulement tant que la composition n'existe pas.
    """
    resolved = settings or Settings.load()
    build_system = app_build_system()
    if build_system is not None:
        accepted = inspect.signature(build_system).parameters
        return build_system(resolved, **{k: v for k, v in kwargs.items() if k in accepted})
    return RunService(resolved, **kwargs)


def default_service(**kwargs: Any) -> RunService:
    """Un service prêt à tourner sans clé d'API — pour le smoke et les tests.

    Il existe parce qu'un squelette qu'on ne peut pas exécuter avant d'avoir un
    compte chez un fournisseur est un squelette qu'on n'exécute jamais, et dont
    on découvre les défauts au premier run facturé.
    """
    kwargs.setdefault("client", StubClient())
    return RunService(**kwargs)
