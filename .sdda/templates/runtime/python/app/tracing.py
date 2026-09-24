"""Les spans du run — un fichier, une ligne par décision. GÉNÉRÉ, ne pas éditer.

Sans trace, un système non déterministe n'est pas débogable : il n'y a pas de
*stack trace* à lire, seulement une suite de décisions dont chacune était
plausible. La trace est donc l'artefact de première classe (ARCHITECTURE §8), et
ce module est ce qui la produit :

    workspace/.sys/traces/runs/{run_id}.jsonl      une ligne JSON par SPAN

**Le format n'est pas négociable**, parce que trois lecteurs du framework le
parsent déjà : `sdda_lib/tracing.py` (coût mesuré de G6, audit de scope de G7,
invariant `trace-emitted-per-run`), le grader `trajectory` (L5 et L8, donc la
SAFETY GATE) et `review-orchestration`. Une graphie d'attribut inventée ici ne
produit pas une erreur : elle produit un zéro. Un coût mesuré à zéro passe sous
n'importe quel plafond, et un audit de scope qui ne voit aucun appel d'outil ne
trouve aucun dépassement — deux faux verts silencieux.

Ce que chaque ligne porte
-------------------------
`run_id`, `trace_id`, `span_id`, `name` sont exigés sur CHAQUE span ;
`parent_span_id` est absent sur la racine, et c'est la seule façon de la
reconnaître. C'est ce champ qui fait la valeur du format : la profondeur de
délégation et l'agent responsable d'un appel d'outil se **lisent** dans l'arbre,
là où une suite d'événements à plat obligeait à deviner « le dernier agent vu » —
faux dès que deux agents travaillent en parallèle, c'est-à-dire précisément au
moment où le périmètre d'outils compte.

Les noms d'attributs vivent ici et nulle part ailleurs
------------------------------------------------------
Les conventions sémantiques GenAI sont en développement : `gen_ai.system` est
devenu `gen_ai.provider.name`, `gen_ai.usage.prompt_tokens` est devenu
`gen_ai.usage.input_tokens`. Un littéral `"gen_ai.…"` ailleurs dans `src/` est
`[TRACE_ATTR_LITERAL]` : à la révision suivante de semconv, la moitié des
attributs change de nom et l'autre non, et les tableaux de bord affichent des
panneaux vides sans que rien n'échoue.

Le coût est calculé, la rédaction est par défaut
-------------------------------------------------
`llm_call` recalcule `sdda.cost.usd` depuis les tokens et la table de tarifs ;
le framework le recalculera encore à la lecture, pour que l'écart apparaisse si
les deux divergent. Et `redact` s'applique à l'ÉCRITURE, jamais à la lecture :
une valeur jamais écrite ne peut pas fuir par une copie du fichier, un dossier
partagé ou un ticket de support auquel on a joint « juste la trace ».

Aucune dépendance : quand la stack `observability/otel-genai.md` est installée,
`setup.py` branche un vrai `TracerProvider` et ce module reste l'exportateur
JSONL. Sans elle — au premier bootstrap, en CI, dans un test — la trace est
quand même écrite, parce qu'un run sans trace n'a pas eu lieu pour la gate.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets as _secrets
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

# ---------------------------------------------------------------------------
# Append atomique entre PROCESSUS — même mécanique que `sdda_lib.tracing`
# ---------------------------------------------------------------------------
# Recopiée et non importée : l'application générée ne dépend pas du framework.
# Le runner d'eval lance jusqu'à `EvalMaxParallel` exécutions en parallèle, et
# chacune peut écrire la trace d'un même run (reprise, sous-processus d'outil) :
# `open("a").write` découpe une ligne longue en plusieurs appels système, et le
# mode append du CRT Windows positionne PUIS écrit — deux spans pouvaient donc
# se retrouver collés sur une ligne, que le lecteur ignore. Un span perdu est un
# appel d'outil absent de l'audit de scope, ou un coût qui manque au plafond.

#: Octet verrouillé sous Windows, loin après toute fin de fichier : le verrou y
#: est obligatoire, et il ne doit bloquer aucun LECTEUR de la trace.
_WIN_LOCK_OFFSET = 0x7FFFFFFF
#: Au-delà, on écrit sans verrou : perdre un span parce qu'un processus est mort
#: en tenant le verrou serait pire qu'un risque d'entrelacement.
LOCK_TIMEOUT_S = 10.0

if sys.platform == "win32":  # pragma: no cover - dépend de la plateforme
    import msvcrt

    def _lock(fd: int) -> bool:
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        delay = 0.001
        while True:
            os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(delay)
                delay = min(delay * 2, 0.05)

    def _unlock(fd: int) -> None:
        os.lseek(fd, _WIN_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:  # pragma: no cover - dépend de la plateforme
    import fcntl

    def _lock(fd: int) -> bool:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def append_line(path: Path, line: str) -> None:
    """Ajoute UNE ligne, écrite en entier sous verrou exclusif, sur un fd `O_APPEND`.

    Les octets sont construits d'abord, puis écrits en boucle jusqu'au dernier
    sous le verrou : c'est lui qui rend la ligne atomique, `O_APPEND` qui
    garantit la fin de fichier. `\\n` seul, jamais `\\r\\n` : sous Windows, un
    retour chariot casse la lecture ligne à ligne et la comparaison de hashes.
    """
    data = (line.rstrip("\n") + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags, 0o644)
    try:
        held = _lock(fd)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        finally:
            if held:
                _unlock(fd)
    finally:
        os.close(fd)

#: Version des conventions suivies. Portée par chaque ligne : une console doit
#: pouvoir lire une trace de six mois sans deviner quelle graphie y régnait.
SEMCONV_VERSION = "0.61b0"

#: Champs exigés sur chaque span par `sdda_lib/tracing.py`. Un span incomplet
#: n'est pas ignoré : il est signalé, et le run cesse d'être `complete`.
SPAN_FIELDS: tuple[str, ...] = ("run_id", "trace_id", "span_id", "name")

# --- Noms de spans -----------------------------------------------------------
RUN_SPAN = "sdda.run"
AGENT_SPAN = "invoke_agent"
LLM_SPAN = "chat"
TOOL_SPAN = "execute_tool"
RETRIEVAL_SPAN = "sdda.retrieve"
GUARDRAIL_SPAN = "sdda.guardrail"

# --- Attributs : la liste close que le framework relit ----------------------
A_OPERATION = "gen_ai.operation.name"
A_PROVIDER = "gen_ai.provider.name"
A_AGENT_ID = "gen_ai.agent.id"
A_AGENT_NAME = "gen_ai.agent.name"
A_CONVERSATION_ID = "gen_ai.conversation.id"
A_TOOL_NAME = "gen_ai.tool.name"
A_TOOL_CALL_ID = "gen_ai.tool.call.id"
A_REQUEST_MODEL = "gen_ai.request.model"
A_RESPONSE_MODEL = "gen_ai.response.model"
A_TOKENS_IN = "gen_ai.usage.input_tokens"
A_TOKENS_OUT = "gen_ai.usage.output_tokens"
A_FINISH_REASONS = "gen_ai.response.finish_reasons"
A_ERROR_TYPE = "error.type"

A_RUN_ID = "sdda.run.id"
A_MISSION_ID = "sdda.mission.id"
A_SERVING_SURFACE = "sdda.serving.surface"
A_CACHE_READ = "sdda.usage.cache_read_tokens"
A_CACHE_WRITE = "sdda.usage.cache_write_tokens"
A_COST_DECLARED = "sdda.cost.usd"
A_MODEL_TIER = "sdda.model.tier"
A_AGENT_ITERATION = "sdda.agent.iteration"
A_BOUNDS_MAX_ITERATIONS = "sdda.bounds.max_iterations"
A_BOUNDS_MAX_TOOL_CALLS = "sdda.bounds.max_tool_calls"
A_BOUNDS_BUDGET_USD = "sdda.bounds.budget_usd"
A_BOUND_EXCEEDED = "sdda.bound.exceeded"
A_BOUND_LIMIT = "sdda.bound.limit"
A_BOUND_OBSERVED = "sdda.bound.observed"
A_BOUND_POLICY = "sdda.bound.policy"
A_TOOL_ID = "sdda.tool.id"
A_SIDE_EFFECT = "sdda.tool.side_effect_class"
A_TOOL_TRUST = "sdda.tool.trust"
A_TOOL_ARGS = "sdda.tool.args"
A_TOOL_RESULT_BYTES = "sdda.tool.result.bytes"
A_TOOL_ERROR_CODE = "sdda.tool.error_code"
A_RETRIEVAL_INDEX_ID = "sdda.retrieval.index_id"
A_RETRIEVAL_TOP_K = "sdda.retrieval.top_k"
A_RETRIEVAL_IDS = "sdda.retrieval.result.ids"
A_RETRIEVAL_SCORES = "sdda.retrieval.result.scores"
A_GUARDRAIL_ID = "sdda.guardrail.id"
A_GUARDRAIL_STAGE = "sdda.guardrail.stage"
A_GUARDRAIL_PASSED = "sdda.guardrail.passed"

# --- Rédaction ---------------------------------------------------------------
REDACTED = "[REDACTED]"

#: Clés dont la valeur ne doit JAMAIS atterrir en clair. Même liste que
#: `sdda_lib/tracing.py` : le scan G7 et la rédaction doivent reconnaître les
#: mêmes formes, sinon l'un des deux ment.
FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "api_key", "apikey", "password", "passwd", "secret", "token", "authorization",
    "proxy_authorization", "credential", "credentials", "private_key",
    "access_token", "refresh_token", "id_token", "client_secret",
    "x_api_key", "cookie", "set_cookie", "session",
})

#: Suffixes qui désignent un secret sur un nom NORMALISÉ (`apiKey` -> `api_key`).
SECRET_KEY_SUFFIXES: tuple[str, ...] = ("_token", "_secret", "_key", "_password", "_passwd")

#: Les `*_key` légitimes : une clé au sens *identifiant*, pas au sens *secret*.
#: Sans cette liste, `primary_key` et `cache_key` disparaîtraient des traces —
#: et ce sont exactement les valeurs qu'on y cherche pour rejouer un run.
SAFE_KEYS: frozenset[str] = frozenset({
    "primary_key", "foreign_key", "partition_key", "shard_key", "routing_key",
    "cache_key", "idempotency_key", "dedupe_key", "group_key", "public_key",
    "sort_key", "config_key", "item_key", "source_key", "metric_key",
})

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: Motifs de secrets reconnus à la VALEUR, quel que soit le nom de la clé. Un
#: secret recopié par erreur dans un argument d'outil ne porte aucun nom
#: révélateur : c'est le seul filet qui l'attrape.
_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/-]{12,}={0,2}"),
)


def normalize_key(key: Any) -> str:
    """`apiKey`, `Api-Key`, `API_KEY` -> `api_key`. Les trois disent la même chose."""
    return _CAMEL_BOUNDARY.sub("_", str(key)).replace("-", "_").lower()


def is_sensitive_key(key: Any) -> bool:
    name = normalize_key(key)
    if name in SAFE_KEYS:
        return False
    if name in FORBIDDEN_KEYS:
        return True
    return name.endswith(SECRET_KEY_SUFFIXES)


def redact(payload: Any, *, secret_values: Sequence[str] = ()) -> Any:
    """Retire les secrets d'une charge utile, récursivement — par clé et par valeur.

    `secret_values` porte les valeurs connues de `Settings` : c'est la seule
    règle qui attrape un secret qui ne ressemble à rien de connu, parce qu'elle
    ne cherche pas une forme mais une égalité. Les trois règles sont pures et
    déterministes : même entrée, même sortie, aucun état, aucun réseau.
    """
    if isinstance(payload, dict):
        return {k: (REDACTED if is_sensitive_key(k) else redact(v, secret_values=secret_values))
                for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [redact(v, secret_values=secret_values) for v in payload]
    if isinstance(payload, str):
        return redact_text(payload, secret_values=secret_values)
    return payload


def redact_text(text: str, *, secret_values: Sequence[str] = ()) -> str:
    for value in secret_values:
        # Un secret court (« 1 », « x ») rendrait la trace illisible en
        # remplaçant des fragments innocents : on ne remplace qu'au-delà d'une
        # longueur où la collision cesse d'être plausible.
        if value and len(value) >= 8:
            text = text.replace(value, REDACTED)
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


# ---------------------------------------------------------------------------
# Horloge et identifiants
# ---------------------------------------------------------------------------
def now_iso() -> str:
    """`2026-09-20T14:12:03.120456Z` — microsecondes, suffixe `Z`, jamais `+00:00`.

    `summarize` trie les spans par cette chaîne, lexicographiquement : deux
    graphies d'un même instant produiraient deux ordres différents, donc deux
    trajectoires pour un même run.

    **Microsecondes et non millisecondes**, et ce n'est pas du zèle : l'ordre
    d'écriture d'un exportateur est celui des FINS, la trajectoire est celle des
    débuts. À la milliseconde, un tour d'agent et l'appel d'outil qu'il contient
    partagent le même horodatage de début — le tri devient stable sur l'ordre
    d'écriture, et la trajectoire rendue est `tool, agent` au lieu de
    `agent, tool`. Le grader de trajectoire lirait alors une séquence que le run
    n'a pas eue. La largeur est fixe, donc le tri lexicographique reste juste.
    """
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    moment = (_dt.datetime.fromtimestamp(int(epoch), _dt.timezone.utc) if epoch.isdigit()
              else _dt.datetime.now(_dt.timezone.utc))
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_run_id() -> str:
    """Identifiant de run, triable dans le temps et sûr comme nom de fichier."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + _secrets.token_hex(4)


def new_span_id() -> str:
    return _secrets.token_hex(8)


def new_trace_id() -> str:
    return _secrets.token_hex(16)


#: Le span courant, propagé par contexte. Un `ContextVar` et non un attribut :
#: deux tâches `asyncio` concurrentes partageraient sinon le même parent, et
#: l'arbre dirait qu'un outil a été appelé par l'agent d'à côté.
_current: ContextVar[str] = ContextVar("sdda_current_span", default="")


@dataclass
class Span:
    """Un span ouvert. Fermé par le gestionnaire de contexte qui l'a créé."""

    span_id: str
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"
    start: str = ""
    _t0: float = 0.0

    def set(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def update(self, values: Mapping[str, Any]) -> None:
        self.attributes.update(values)

    def error(self, error_type: str) -> None:
        """Marque le span en erreur. `status` est lu par l'audit de scope."""
        self.status = "ERROR"
        self.attributes[A_ERROR_TYPE] = error_type

    def bound_exceeded(self, bound: str, limit: float, observed: float, policy: str) -> None:
        """La borne atteinte, sur le span de l'agent — là où la L5 la cherche.

        La politique est enregistrée avec elle : la L5 ne vérifie pas seulement
        qu'une borne est tombée, mais que le comportement observé est celui que
        le contrat déclarait (`[BOUND_BEHAVIOR_MISMATCH]`).
        """
        self.attributes.update({A_BOUND_EXCEEDED: bound, A_BOUND_LIMIT: limit,
                                A_BOUND_OBSERVED: observed, A_BOUND_POLICY: policy})
        if policy == "fail-explicit":
            self.status = "ERROR"


@dataclass
class Tracer:
    """Écrivain append-only d'une trace, au format span.

    Pas de tampon : chaque span est écrit et vidé immédiatement. Un run parti en
    boucle ou tué doit laisser derrière lui ce qui a mené là — c'est exactement
    le run qu'on voudra lire. Le JSONL n'est pas cosmétique non plus : une
    exécution interrompue laisse un fichier lisible jusqu'au point de coupure,
    là où un JSON unique laisserait un fichier invalide.

    `path=None` garde tout en mémoire : c'est ce que fait un test, et ce que
    fait l'exécuteur d'eval en cours de processus, qui rend `spans` au grader
    `trajectory` sans passer par le disque.
    """

    run_id: str = field(default_factory=new_run_id)
    trace_id: str = ""
    path: Path | None = None
    redact_enabled: bool = True
    secret_values: tuple[str, ...] = ()
    pricing: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    spans: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.trace_id = self.trace_id or new_trace_id()
        if self.path is not None:
            self.path = Path(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_run(cls, settings: Any, run_id: str | None = None) -> "Tracer":
        """Le tracer d'un run, câblé depuis `Settings`. Le JSONL n'est pas désactivable.

        `trace_enabled: false` coupe l'ÉCRITURE sur disque, pas la collecte : le
        rapport d'eval et l'exécuteur en cours de processus continuent de voir
        les spans. Couper la collecte rendrait la L5 muette sans le dire.
        """
        rid = run_id or new_run_id()
        return cls(run_id=rid,
                   path=(settings.traces_dir() / f"{rid}.jsonl") if settings.trace_enabled else None,
                   redact_enabled=settings.trace_redact,
                   secret_values=tuple(settings.secret_values()),
                   pricing=settings.pricing)

    # -- Émission -----------------------------------------------------------
    def emit(self, name: str, *, span_id: str, parent_span_id: str = "",
             attributes: Mapping[str, Any] | None = None, status: str = "OK",
             start: str = "", end: str = "", duration_ms: float | None = None
             ) -> dict[str, Any]:
        """Écrit UN span. Les attributs sont rédigés, l'enveloppe ne l'est pas.

        L'enveloppe (identifiants, nom, durée) est ce qui rend la trace
        exploitable ; la rédiger la rendrait illisible sans rien protéger.
        """
        attrs = dict(attributes or {})
        payload = redact(attrs, secret_values=self.secret_values) if self.redact_enabled else attrs
        span: dict[str, Any] = {
            "semconv_version": SEMCONV_VERSION,
            "run_id": self.run_id, "trace_id": self.trace_id, "span_id": span_id,
            "name": name, "status": status, "attributes": payload,
        }
        if parent_span_id:
            span["parent_span_id"] = parent_span_id
        if start:
            span["start"] = start
        if end:
            span["end"] = end
        if duration_ms is not None:
            span["duration_ms"] = round(duration_ms)
        self.spans.append(span)
        if self.path is not None:
            append_line(self.path, json.dumps(span, ensure_ascii=False, sort_keys=True))
        return span

    @contextmanager
    def span(self, name: str, *, attributes: Mapping[str, Any] | None = None) -> Iterator[Span]:
        """Ouvre un span enfant du span courant, et l'écrit à la sortie.

        L'écriture a lieu dans un `finally` : un span qu'on n'écrit qu'en cas de
        succès disparaît précisément quand on en a besoin.
        """
        current = Span(span_id=new_span_id(), name=name,
                       attributes=dict(attributes or {}), start=now_iso())
        current._t0 = time.monotonic()
        parent = _current.get()
        token = _current.set(current.span_id)
        try:
            yield current
        except Exception as exc:  # le span sort en ERROR, l'exception poursuit sa route
            current.error(type(exc).__name__)
            raise
        finally:
            _current.reset(token)
            self.emit(current.name, span_id=current.span_id, parent_span_id=parent,
                      attributes=current.attributes, status=current.status,
                      start=current.start, end=now_iso(),
                      duration_ms=(time.monotonic() - current._t0) * 1000)

    # -- Les cinq spans du vocabulaire --------------------------------------
    @contextmanager
    def run_span(self, *, mission_id: str = "", surface: str = "cli",
                 attributes: Mapping[str, Any] | None = None) -> Iterator[Span]:
        """Le span racine, `sdda.run {mission}`. Un seul par fichier de trace.

        Il est le seul à connaître le début, la fin et le cumul du run : sans
        lui, `summarize` ne sait ni la latence ni si le run s'est terminé.
        """
        attrs = {A_RUN_ID: self.run_id, A_MISSION_ID: mission_id,
                 A_SERVING_SURFACE: surface, **dict(attributes or {})}
        with self.span(f"{RUN_SPAN} {mission_id}".strip(), attributes=attrs) as span:
            yield span

    @contextmanager
    def agent_turn(self, *, agent_id: str, agent_name: str = "", iteration: int = 0,
                   tier: str = "", thread_id: str = "", bounds: Any = None) -> Iterator[Span]:
        """`invoke_agent {name}` — un hop. La profondeur se lit dans l'arbre."""
        name = agent_name or agent_id
        attrs: dict[str, Any] = {
            A_OPERATION: "invoke_agent", A_AGENT_ID: agent_id, A_AGENT_NAME: name,
            A_AGENT_ITERATION: iteration,
        }
        if thread_id:
            attrs[A_CONVERSATION_ID] = thread_id
        if tier:
            attrs[A_MODEL_TIER] = tier
        if bounds is not None:
            attrs.update({A_BOUNDS_MAX_ITERATIONS: bounds.max_iterations,
                          A_BOUNDS_MAX_TOOL_CALLS: bounds.max_tool_calls,
                          A_BOUNDS_BUDGET_USD: bounds.budget_usd})
        with self.span(f"{AGENT_SPAN} {name}", attributes=attrs) as span:
            yield span

    @contextmanager
    def llm_call(self, *, model: str, tier: str = "", provider: str = "") -> Iterator[Span]:
        """`chat {model}` — et le coût, RECALCULÉ à la fermeture depuis les tokens.

        `record_usage` est appelé par la boucle avec l'usage réel ; le montant
        n'est jamais recopié depuis ce que déclare un fournisseur.
        """
        attrs: dict[str, Any] = {A_OPERATION: "chat", A_REQUEST_MODEL: model}
        if tier:
            attrs[A_MODEL_TIER] = tier
        if provider:
            attrs[A_PROVIDER] = provider
        with self.span(f"{LLM_SPAN} {model}", attributes=attrs) as span:
            yield span

    def record_usage(self, span: Span, *, model: str, usage: Any,
                     finish_reason: str = "") -> float:
        """Pose les tokens sur un span `chat` et rend le coût recalculé.

        Un `usage` absent est un problème nommé (`[TRACE_USAGE_MISSING]`), pas
        un zéro : `CostTrackingEnabled` impose que l'absence se voie.
        """
        from .models import cost_usd as _cost  # noqa: PLC0415 - évite un cycle d'import

        span.set(A_RESPONSE_MODEL, model)
        span.update({
            A_TOKENS_IN: int(getattr(usage, "input_tokens", 0) or 0),
            A_TOKENS_OUT: int(getattr(usage, "output_tokens", 0) or 0),
            A_CACHE_READ: int(getattr(usage, "cache_read_tokens", 0) or 0),
            A_CACHE_WRITE: int(getattr(usage, "cache_write_tokens", 0) or 0),
        })
        if finish_reason:
            span.set(A_FINISH_REASONS, [finish_reason])
        if not (span.attributes[A_TOKENS_IN] or span.attributes[A_TOKENS_OUT]):
            self.problems.append(
                f"appel `{model}` sans tokens [TRACE_USAGE_MISSING] : son coût ne peut pas "
                "être recalculé, et un zéro passerait sous n'importe quel plafond")
        usd, problem = _cost(model, usage, self.pricing)
        if problem:
            self.problems.append(problem)
        else:
            span.set(A_COST_DECLARED, usd)
        return usd or 0.0

    @contextmanager
    def tool_call(self, *, tool: str, tool_id: str = "", call_id: str = "",
                  side_effect_class: str = "read-only", trust: str = "trusted",
                  args: Mapping[str, Any] | None = None) -> Iterator[Span]:
        """`execute_tool {name}` — émis AUSSI en cas d'échec ou de refus.

        Un appel refusé par un guardrail qui n'émettrait pas de span rendrait
        l'audit de scope de G7 aveugle exactement sur le cas intéressant.
        """
        attrs: dict[str, Any] = {
            A_OPERATION: "execute_tool", A_TOOL_NAME: tool,
            A_TOOL_ID: tool_id or tool, A_SIDE_EFFECT: side_effect_class, A_TOOL_TRUST: trust,
        }
        if call_id:
            attrs[A_TOOL_CALL_ID] = call_id
        if args is not None:
            # Sérialisés puis rédigés : un argument est du texte d'utilisateur,
            # et c'est le premier endroit où une PII ou une clé atterrit.
            attrs[A_TOOL_ARGS] = json.dumps(dict(args), ensure_ascii=False, sort_keys=True)
        with self.span(f"{TOOL_SPAN} {tool}", attributes=attrs) as span:
            yield span

    @contextmanager
    def retrieval(self, *, index_id: str, top_k: int = 0) -> Iterator[Span]:
        """`sdda.retrieve {index}` — identifiants et scores, jamais le contenu.

        Mettre le texte des chunks dans un span, c'est mettre des PII, du volume
        et du texte hostile dans le backend de traces. Le contenu se relit dans
        l'index par `chunk_id` ; les identifiants suffisent à résoudre les
        citations (G4).
        """
        attrs: dict[str, Any] = {A_RETRIEVAL_INDEX_ID: index_id}
        if top_k:
            attrs[A_RETRIEVAL_TOP_K] = top_k
        with self.span(f"{RETRIEVAL_SPAN} {index_id}", attributes=attrs) as span:
            yield span

    @contextmanager
    def guardrail(self, *, guardrail_id: str, stage: str) -> Iterator[Span]:
        attrs = {A_GUARDRAIL_ID: guardrail_id, A_GUARDRAIL_STAGE: stage,
                 A_GUARDRAIL_PASSED: True}
        with self.span(f"{GUARDRAIL_SPAN} {guardrail_id}", attributes=attrs) as span:
            yield span

    # -- Lecture ------------------------------------------------------------
    def as_trace(self) -> dict[str, Any]:
        """Ce que l'exécuteur d'eval passe au grader `trajectory`.

        La clé `spans` est la forme CANONIQUE que le grader lit en premier :
        lui donner `tool_calls` ferait passer une vraie trace pour une trace de
        test, et perdrait les hops et les bornes que seuls les spans portent.
        """
        return {"spans": list(self.spans), "run_id": self.run_id,
                "trace_path": str(self.path) if self.path else ""}
