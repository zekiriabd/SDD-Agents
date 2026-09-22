"""Trace d'un run — le format canonique, et ce qu'on peut en prouver.

Il n'existe pas de *stack trace* dans un système non déterministe. Quand un
agent répond à côté, il n'y a ni exception, ni ligne fautive : il y a une suite
de décisions dont chacune était plausible. La trace est le seul artefact qui
permette un post-mortem — et, plus quotidiennement, qui permette de mesurer le
coût réel (P6) et de comparer deux runs du même item (P3).

Ce module ne produit pas les traces : c'est l'application générée qui les écrit,
via sa stack d'observabilité (`observability/otel-genai.md`, spans OTel-GenAI).
Il définit **le format d'atterrissage** et ce que le framework est en droit d'en
exiger :

    workspace/traces/runs/{run_id}.jsonl      une ligne JSON par SPAN

Un fichier par run, append-only, une ligne par span. Le choix du JSONL n'est pas
cosmétique : un run interrompu laisse une trace lisible jusqu'à l'interruption,
là où un JSON unique laisse un fichier invalide — et c'est précisément sur les
runs interrompus qu'on veut regarder.

Un SEUL format, et c'est celui des spans
----------------------------------------
Ce module a longtemps lu une grammaire d'« événements » (`{ts, runId, kind}`)
que **rien ne produisait** : l'application générée écrit des spans
(`{run_id, trace_id, span_id, parent_span_id, name, attributes}`), et le grader
`trajectory` les lisait déjà comme forme canonique. Deux formats pour un même
fichier, et les conséquences étaient silencieuses plutôt que bruyantes :
`audit_tool_scope` ne voyait aucun appel d'outil dans une trace réelle, donc
aucun dépassement de périmètre — un faux vert sur un contrôle de sûreté. Le coût
mesuré de G6 restait à zéro, donc sous le plafond.

La forme canonique est donc le span, parce que c'est elle qu'on produit, et
parce qu'elle seule porte `parent_span_id` : la profondeur de délégation et
l'agent responsable d'un appel d'outil se LISENT dans l'arbre, là où une suite
d'événements à plat obligeait à deviner « le dernier agent vu ».

Le coût est RECALCULÉ depuis les tokens
---------------------------------------
`sdda.cost.usd` est enregistré mais jamais cru sur parole : le coût rendu par
`summarize` vient des tokens et de `pricing.py`. Un chiffre qu'on relit sans le
recalculer n'est pas une mesure, c'est une déclaration — et l'écart entre les
deux, quand il apparaît, est exactement ce qu'on veut voir.

Ce que le framework en tire, sans LLM :
  - `trace-emitted-per-run` : un run sans trace n'a pas eu lieu, pour la gate ;
  - le coût et la latence **mesurés** de G6, opposés à l'estimation de G2 ;
  - la trajectoire (suite d'agents et d'outils), comparée au grader `trajectory` ;
  - l'agent responsable de chaque appel d'outil, pour l'audit de scope (G7) ;
  - les documents réellement retournés, pour la résolution des citations (G4).

Rédaction — ce qui ne doit jamais atterrir en clair
----------------------------------------------------
Les traces sont partagées pour déboguer : c'est leur raison d'être, et c'est ce
qui en fait un canal d'exfiltration involontaire. `redact` s'applique à
l'écriture, jamais à la lecture, et combine deux règles indépendantes :

1. **Par clé.** Le nom de la clé est normalisé — camelCase découpé en
   `snake_case`, `-` remplacé par `_`, minuscules — puis la valeur est
   remplacée par `[REDACTED]` si le nom normalisé :
     - est dans `FORBIDDEN_KEYS` (`api_key`, `access_token`, `cookie`, …), ou
     - se termine par l'un des `SECRET_KEY_SUFFIXES`
       (`_token`, `_secret`, `_key`, `_password`, `_passwd`),
   **sauf** s'il figure dans `SAFE_KEYS` — la liste blanche des `*_key` légitimes
   du framework (`primary_key`, `config_key`, `sort_key`, …), qui désignent une
   clé au sens *identifiant*, pas au sens *secret*. Le suffixe est comparé sur
   le nom normalisé, donc `topK` -> `top_k` n'est pas concerné, et `key_env`
   (qui porte un NOM de variable d'environnement, exactement ce qu'on veut lire
   dans une trace) ne se termine pas par `_key`. `tokensIn` / `maxTokens`
   (des comptes, pas des jetons) ne se terminent pas par `_token`.

2. **Par valeur.** Toute chaîne, à n'importe quelle profondeur, est passée au
   crible des motifs de `scan_secrets.PREFIXED` (clé OpenAI, PAT GitHub, JWT,
   clé AWS, URL avec identifiants, …) — importés, pas recopiés : le scan et la
   rédaction doivent reconnaître les mêmes formes, sinon l'un des deux ment.
   S'y ajoutent l'affectation littérale (`scan_secrets.ASSIGNMENT`, hors
   placeholders) et les en-têtes `Bearer <jeton>` / `Basic <b64>`. Seule la
   portion reconnue est remplacée ; le texte alentour reste lisible.

Les deux règles sont pures et déterministes : même entrée, même sortie, aucun
état, aucun réseau.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from sdda_lib import pricing

# Les formes de secrets vivent dans `scan_secrets` (G7) et nulle part ailleurs.
# Un import lib -> scripts est inhabituel ici, mais recopier les motifs les
# ferait dériver : la gate détecterait des formes que la trace ne rédige pas.
from sdda_scripts.scan_secrets import ASSIGNMENT as _ASSIGNMENT
from sdda_scripts.scan_secrets import COMPILED as _SECRET_PATTERNS
from sdda_scripts.scan_secrets import is_placeholder as _is_placeholder

#: Champs exigés sur CHAQUE span. `parent_span_id` est absent sur la racine, et
#: c'est la seule façon de la reconnaître — donc il n'est pas exigé.
SPAN_FIELDS: tuple[str, ...] = ("run_id", "trace_id", "span_id", "name")

#: Nom du span racine (`observability/otel-genai.md §3.1`) : `sdda.run {mission}`.
RUN_SPAN = "sdda.run"

#: Attributs lus par le framework. Nommés UNE fois : une constante recopiée dans
#: six scripts est six occasions de diverger d'une révision de semconv.
A_OPERATION = "gen_ai.operation.name"
A_AGENT_ID = "gen_ai.agent.id"
A_AGENT_NAME = "gen_ai.agent.name"
A_TOOL_NAME = "gen_ai.tool.name"
A_TOOL_ID = "sdda.tool.id"
A_SIDE_EFFECT = "sdda.tool.side_effect_class"
A_REQUEST_MODEL = "gen_ai.request.model"
A_RESPONSE_MODEL = "gen_ai.response.model"
A_TOKENS_IN = "gen_ai.usage.input_tokens"
A_TOKENS_OUT = "gen_ai.usage.output_tokens"
A_CACHE_READ = "sdda.usage.cache_read_tokens"
A_CACHE_WRITE = "sdda.usage.cache_write_tokens"
A_COST_DECLARED = "sdda.cost.usd"
A_BOUND_EXCEEDED = "sdda.bound.exceeded"
A_RETRIEVAL_IDS = "sdda.retrieval.result.ids"
A_MISSION_ID = "sdda.mission.id"

#: `gen_ai.operation.name` -> rôle logique. Grammaire close : une opération
#: inconnue est une trace qu'aucun lecteur ne saura agréger, donc une mesure
#: perdue au moment où elle compte.
OPERATIONS: dict[str, str] = {
    "invoke_agent": "agent",
    "chat": "llm",
    "execute_tool": "tool",
    "embeddings": "embedding",
}

#: Spans propres au framework, reconnus par PRÉFIXE de nom : ils n'ont pas de
#: `gen_ai.operation.name` parce que semconv n'en définit pas.
SDDA_SPANS: tuple[tuple[str, str], ...] = (
    (RUN_SPAN, "run"),
    ("sdda.retrieve", "retrieval"),
    ("sdda.data.query", "data_query"),
    ("sdda.guardrail", "guardrail"),
    ("sdda.gate", "gate"),
)

#: Écart toléré entre le coût déclaré et le coût recalculé, avant de le signaler.
#: Une part d'écart est légitime (arrondis, tarif de cache d'écriture propre au
#: fournisseur) ; au-delà, le chiffre déclaré et les tokens ne parlent plus du
#: même appel, et c'est le genre d'écart qui fait passer un run sous un plafond
#: qu'il dépasse.
COST_DIVERGENCE_RATIO = 0.05
COST_DIVERGENCE_ABS_USD = 0.0005

#: Clés dont la valeur ne doit JAMAIS atterrir en clair dans une trace. Les
#: traces sont partagées pour déboguer — c'est leur raison d'être, et c'est ce
#: qui en fait un canal d'exfiltration involontaire.
FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "api_key", "apikey", "password", "passwd", "secret", "token", "authorization",
    "proxy_authorization", "credential", "credentials", "private_key",
    "access_token", "refresh_token", "id_token", "client_secret",
    "x_api_key", "cookie", "set_cookie", "session",
})

#: Suffixes qui, sur un nom de clé normalisé, désignent un secret. Comparés
#: après normalisation : `apiKey` -> `api_key`, `X-Api-Key` -> `x_api_key`.
SECRET_KEY_SUFFIXES: tuple[str, ...] = ("_token", "_secret", "_key", "_password", "_passwd")

#: Liste blanche des `*_key` légitimes : une *clé* au sens identifiant, jamais
#: un secret. Chaque entrée est un nom relevé dans le framework (registres, IR,
#: scripts, templates runtime) ou une convention de base de données/dispatch.
#: Un nom absent d'ici et terminé par `_key` est rédigé — c'est le sens du défaut.
SAFE_KEYS: frozenset[str] = frozenset({
    # relevés dans le framework
    "config_key", "sort_key", "by_key", "norm_key", "ir_key", "prompt_key",
    "contract_key", "metric_key", "item_key", "source_key", "stack_key",
    "requirement_key", "unknown_key",
    # conventions base / dispatch / cache
    "primary_key", "foreign_key", "partition_key", "shard_key", "routing_key",
    "cache_key", "idempotency_key", "dedupe_key", "group_key", "public_key",
})

REDACTED = "[REDACTED]"

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: `Bearer <jeton>` / `Basic <b64>`. Le schéma reste lisible, seul le jeton
#: est remplacé. La longueur minimale et le test `_looks_like_credential`
#: écartent la prose (« Basic authentication », « Bearer token expired »). Un
#: `.` n'appartient au jeton que s'il est suivi d'un autre caractère de jeton :
#: le point qui termine la phrase reste à la phrase.
_AUTH_HEADER = re.compile(
    r"(?i)\b(Bearer|Basic)\s+((?:[A-Za-z0-9_~+/-]|\.(?=[A-Za-z0-9_~+/=-])){8,}={0,2})")


def _looks_like_credential(candidate: str) -> bool:
    """Un jeton a des chiffres, des symboles base64 ou une casse mêlée — un mot
    de prose n'a rien de tout cela."""
    if re.search(r"[0-9+/=._~-]", candidate):
        return True
    return bool(re.search(r"[a-z]", candidate) and re.search(r"[A-Z]", candidate))


def runs_dir(root: Path) -> Path:
    return root / "workspace" / "traces" / "runs"


def trace_path(root: Path, run_id: str) -> Path:
    return runs_dir(root) / f"{run_id}.jsonl"


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------
def normalize_key(key: Any) -> str:
    """`apiKey`, `Api-Key`, `API_KEY` -> `api_key`. Les trois désignent la même chose."""
    return _CAMEL_BOUNDARY.sub("_", str(key)).replace("-", "_").lower()


def is_sensitive_key(key: Any) -> bool:
    """La règle par clé du module (cf. docstring) : liste blanche, puis liste
    noire, puis suffixe."""
    name = normalize_key(key)
    if name in SAFE_KEYS:
        return False
    if name in FORBIDDEN_KEYS:
        return True
    return name.endswith(SECRET_KEY_SUFFIXES)


def _redact_auth_header(m: re.Match[str]) -> str:
    return m.group(0) if not _looks_like_credential(m.group(2)) else f"{m.group(1)} {REDACTED}"


def _redact_assignment(m: re.Match[str]) -> str:
    if _is_placeholder(m.group(2)):
        return m.group(0)
    whole, offset = m.group(0), m.start(0)
    return whole[:m.start(2) - offset] + REDACTED + whole[m.end(2) - offset:]


def redact_text(text: str) -> str:
    """La règle par valeur : remplace chaque portion reconnue comme secret,
    garde le reste. Une chaîne sans secret revient inchangée."""
    for _label, pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _ASSIGNMENT.sub(_redact_assignment, text)
    return _AUTH_HEADER.sub(_redact_auth_header, text)


def redact(payload: Any) -> Any:
    """Retire tout secret d'une charge utile, récursivement — par clé et par valeur.

    Appliqué à l'écriture et non à la lecture : une valeur qui n'est jamais
    écrite ne peut pas fuir par une copie du fichier, un partage de dossier ou
    un ticket de support auquel on a joint « juste la trace ». Les valeurs
    non textuelles (nombres, booléens, `None`) reviennent telles quelles.
    """
    if isinstance(payload, dict):
        return {k: (REDACTED if is_sensitive_key(k) else redact(v)) for k, v in payload.items()}
    if isinstance(payload, list):
        return [redact(v) for v in payload]
    if isinstance(payload, tuple):
        return tuple(redact(v) for v in payload)
    if isinstance(payload, str):
        return redact_text(payload)
    return payload


@dataclass
class TraceWriter:
    """Écrivain append-only d'une trace de run, au format span.

    Pas de tampon : chaque span est écrit et vidé immédiatement. Un run qui part
    en boucle ou qui est tué doit laisser derrière lui ce qui a mené là — c'est
    exactement le run qu'on voudra lire.

    L'application générée n'utilise pas cette classe : elle passe par son
    `JsonlSpanExporter` OTel. Elle existe pour les scripts du framework (traces
    de construction) et pour les tests, et elle écrit EXACTEMENT la même forme —
    sans quoi on retomberait dans les deux formats que ce module vient de
    réduire à un.
    """

    root: Path
    run_id: str
    trace_id: str = ""
    _path: Path = field(init=False)

    def __post_init__(self) -> None:
        self._path = trace_path(self.root, self.run_id)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self.trace_id = self.trace_id or self.run_id

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, name: str, *, span_id: str, parent_span_id: str | None = None,
             attributes: dict[str, Any] | None = None, status: str = "OK",
             start: str | None = None, end: str | None = None,
             duration_ms: int | float | None = None, **extra: Any) -> dict[str, Any]:
        """Écrit un span. `attributes` est redigé, jamais le reste de l'enveloppe."""
        span: dict[str, Any] = {
            "run_id": self.run_id, "trace_id": self.trace_id, "span_id": span_id,
            "name": name, "status": status, "attributes": redact(dict(attributes or {})),
        }
        if parent_span_id:
            span["parent_span_id"] = parent_span_id
        for key, value in (("start", start), ("end", end), ("duration_ms", duration_ms)):
            if value is not None:
                span[key] = value
        span.update(redact(extra))
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(span, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
        return span


# ---------------------------------------------------------------------------
# Lecture et vérification
# ---------------------------------------------------------------------------
def read_spans(path: Path) -> Iterator[dict[str, Any]]:
    """Les spans d'une trace. Une ligne illisible est ignorée, pas fatale.

    Une trace tronquée par une interruption reste exploitable jusqu'au point de
    coupure : refuser de la lire entièrement reviendrait à perdre l'information
    au moment précis où elle a le plus de valeur.
    """
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                span = json.loads(line)
            except ValueError:
                continue
            if isinstance(span, dict):
                yield span


def is_legacy_event(span: dict[str, Any]) -> bool:
    """Ligne de l'ancienne grammaire d'événements (`{ts, runId, kind}`), sans span.

    Reconnue pour être SIGNALÉE, jamais pour être lue : une trace à l'ancien
    format ne porte ni `parent_span_id` ni attributs semconv, donc ni profondeur
    de délégation ni agent responsable d'un appel d'outil. La lire à moitié
    rendrait des chiffres partiels qu'on croirait complets.
    """
    return "kind" in span and "span_id" not in span


def attributes_of(span: dict[str, Any]) -> dict[str, Any]:
    attrs = span.get("attributes") or span.get("attrs") or {}
    return attrs if isinstance(attrs, dict) else {}


def span_role(span: dict[str, Any]) -> str | None:
    """Rôle logique d'un span : `run`, `agent`, `llm`, `tool`, `retrieval`… ou None.

    L'opération semconv prime ; à défaut, le préfixe de nom des spans `sdda.*`,
    pour lesquels semconv ne définit rien.
    """
    operation = str(attributes_of(span).get(A_OPERATION) or "")
    if operation in OPERATIONS:
        return OPERATIONS[operation]
    name = str(span.get("name") or "")
    for prefix, role in SDDA_SPANS:
        if name == prefix or name.startswith(prefix + " "):
            return role
    return None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def span_cost_usd(attrs: dict[str, Any]) -> tuple[float | None, str | None]:
    """(coût recalculé depuis les tokens, problème) pour un span `chat`.

    Le coût n'est jamais relu depuis `sdda.cost.usd` : il est RECALCULÉ. Un
    modèle absent de la table de tarifs ne rend pas zéro — il rend un problème,
    parce qu'un zéro passerait sous n'importe quel plafond sans rien dire.
    """
    model = attrs.get(A_RESPONSE_MODEL) or attrs.get(A_REQUEST_MODEL)
    if not isinstance(model, str) or not model.strip():
        return None, f"span `chat` sans `{A_REQUEST_MODEL}` : coût non recalculable"
    tokens_in, tokens_out = _int(attrs.get(A_TOKENS_IN)), _int(attrs.get(A_TOKENS_OUT))
    if tokens_in is None or tokens_out is None:
        return None, f"span `chat` ({model}) sans `{A_TOKENS_IN}`/`{A_TOKENS_OUT}` : coût non recalculable"
    try:
        rates = pricing.get_pricing(model, strict=True)
    except pricing.UnknownModelPricing:
        return None, (f"modèle `{model}` absent de la table de tarifs [BUDGET_PRICING_UNKNOWN] : "
                      "coût non recalculable")
    usd = (tokens_in * rates["input"] + tokens_out * rates["output"]
           + (_int(attrs.get(A_CACHE_READ)) or 0) * rates["cache_read"]
           + (_int(attrs.get(A_CACHE_WRITE)) or 0) * rates["cache_creation"]) / 1e6
    return round(usd, 6), None


def _duration_ms(span: dict[str, Any]) -> int:
    declared = _int(span.get("duration_ms"))
    if declared is not None:
        return declared
    start, end = span.get("start"), span.get("end")
    if isinstance(start, str) and isinstance(end, str):
        try:
            t0 = _dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
            t1 = _dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
            return max(0, int((t1 - t0).total_seconds() * 1000))
        except ValueError:
            return 0
    return 0


@dataclass
class ToolCall:
    """Un appel d'outil, RATTACHÉ à l'agent qui l'a fait.

    Le rattachement se lit dans l'arbre (`parent_span_id`), il ne se devine plus
    en suivant « le dernier agent vu » : deux agents en parallèle rendaient cette
    heuristique fausse au moment précis où le scope compte.

    Nom ET identifiant de contrat sont portés, parce qu'ils ne servent pas la
    même question. Le nom (`gen_ai.tool.name`) est ce qu'un grader de trajectoire
    compare à un attendu écrit par un humain ; l'identifiant (`sdda.tool.id`,
    `gen_ai.agent.id`) est ce que l'audit de scope confronte à l'IR. Les
    confondre ferait échouer l'un des deux sans qu'on sache lequel a raison.
    """

    tool: str
    agent: str
    tool_id: str = ""
    agent_id: str = ""
    side_effect_class: str = ""
    ok: bool = True

    def matches_tool(self, declared: set[str]) -> bool:
        """L'appel est-il couvert par un ensemble d'outils déclarés à l'IR ?"""
        return bool({self.tool_id, self.tool} & declared)


@dataclass
class TraceSummary:
    """Ce qu'une trace prouve, sans interprétation."""

    run_id: str
    spans: int = 0
    roles: dict[str, int] = field(default_factory=dict)
    agents: list[str] = field(default_factory=list)
    trajectory: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    hops: int = 0
    max_depth: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cost_declared_usd: float = 0.0
    latency_ms: int = 0
    documents: int = 0
    bounds_exceeded: list[str] = field(default_factory=list)
    complete: bool = False
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id, "spans": self.spans, "roles": self.roles,
            "agents": self.agents, "trajectory": self.trajectory,
            "toolCalls": [{"tool": c.tool, "agent": c.agent, "toolId": c.tool_id,
                           "agentId": c.agent_id, "sideEffectClass": c.side_effect_class,
                           "ok": c.ok} for c in self.tool_calls],
            "hops": self.hops, "maxDepth": self.max_depth,
            "tokensIn": self.tokens_in, "tokensOut": self.tokens_out,
            "costUsd": round(self.cost_usd, 6),
            "costDeclaredUsd": round(self.cost_declared_usd, 6),
            "latencyMs": self.latency_ms, "documents": self.documents,
            "boundsExceeded": self.bounds_exceeded,
            "complete": self.complete, "problems": self.problems,
        }


def _tool_name(span: dict[str, Any], attrs: dict[str, Any]) -> str:
    for key in (A_TOOL_NAME, A_TOOL_ID):
        if isinstance(attrs.get(key), str) and attrs[key].strip():
            return attrs[key].strip()
    name = str(span.get("name") or "")
    return name.split(" ", 1)[1].strip() if " " in name else ""


def _attr_str(attrs: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if isinstance(attrs.get(key), str) and attrs[key].strip():
            return attrs[key].strip()
    return ""


def _agent_name(attrs: dict[str, Any]) -> str:
    return _attr_str(attrs, A_AGENT_NAME, A_AGENT_ID)


def summarize(path: Path) -> TraceSummary:
    """Ce qu'on peut prouver d'une trace : trajectoire, scope, coût recalculé, bornes."""
    summary = TraceSummary(run_id=path.stem)
    ordered: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    legacy = 0

    for span in read_spans(path):
        summary.spans += 1
        if is_legacy_event(span):
            legacy += 1
            continue
        missing = [f for f in SPAN_FIELDS if not span.get(f)]
        if missing:
            summary.problems.append(f"span `{span.get('name') or '?'}` : champ(s) absent(s) {missing}")
            continue
        ordered.append(span)
        by_id[str(span["span_id"])] = span

    if legacy:
        summary.problems.append(
            f"{legacy} ligne(s) à l'ancien format d'événements (`kind`) : cette trace ne porte ni "
            "`parent_span_id` ni attributs semconv, donc ni profondeur de délégation ni agent "
            "responsable d'un appel d'outil. Régénérer le run avec la stack `observability/`")

    # L'ordre d'écriture d'un exportateur de spans est celui des FINS ; la
    # trajectoire, elle, est celle des débuts.
    ordered.sort(key=lambda s: str(s.get("start") or ""))

    def depth_of(span: dict[str, Any]) -> int:
        """Profondeur en nombre de spans d'AGENT jusqu'à la racine — la délégation."""
        depth, seen, cursor = 0, {str(span["span_id"])}, span
        while True:
            parent_id = str(cursor.get("parent_span_id") or "")
            if not parent_id or parent_id in seen or parent_id not in by_id:
                return depth
            seen.add(parent_id)
            cursor = by_id[parent_id]
            if span_role(cursor) == "agent":
                depth += 1

    def owning_agent(span: dict[str, Any]) -> tuple[str, str]:
        """(nom, identifiant IR) de l'agent responsable, lu dans l'arbre plutôt que deviné."""
        seen, cursor = {str(span["span_id"])}, span
        while True:
            parent_id = str(cursor.get("parent_span_id") or "")
            if not parent_id or parent_id in seen or parent_id not in by_id:
                return "", ""
            seen.add(parent_id)
            cursor = by_id[parent_id]
            if span_role(cursor) == "agent":
                attrs = attributes_of(cursor)
                return _agent_name(attrs), _attr_str(attrs, A_AGENT_ID, A_AGENT_NAME)

    roots = [s for s in ordered if not str(s.get("parent_span_id") or "") or str(s["parent_span_id"]) not in by_id]
    run_roots = [s for s in roots if str(s.get("name") or "").startswith(RUN_SPAN)]

    for span in ordered:
        attrs = attributes_of(span)
        role = span_role(span)
        if role is None:
            summary.problems.append(
                f"span `{span.get('name')}` : rôle inconnu — ni `{A_OPERATION}` de la liste close "
                f"({', '.join(sorted(OPERATIONS))}) ni préfixe `sdda.*` reconnu")
            continue
        summary.roles[role] = summary.roles.get(role, 0) + 1

        if role == "agent":
            summary.hops += 1
            summary.max_depth = max(summary.max_depth, depth_of(span) + 1)
            agent = _agent_name(attrs)
            if agent:
                summary.trajectory.append(agent)
                if agent not in summary.agents:
                    summary.agents.append(agent)
            if isinstance(attrs.get(A_BOUND_EXCEEDED), str):
                summary.bounds_exceeded.append(attrs[A_BOUND_EXCEEDED])
        elif role == "tool":
            tool = _tool_name(span, attrs)
            if tool:
                agent, agent_id = owning_agent(span)
                summary.trajectory.append(f"tool:{tool}")
                summary.tool_calls.append(ToolCall(
                    tool=tool, agent=agent,
                    tool_id=_attr_str(attrs, A_TOOL_ID, A_TOOL_NAME), agent_id=agent_id,
                    side_effect_class=str(attrs.get(A_SIDE_EFFECT) or ""),
                    ok=str(span.get("status") or "OK").upper() != "ERROR"))
        elif role == "llm":
            summary.tokens_in += _int(attrs.get(A_TOKENS_IN)) or 0
            summary.tokens_out += _int(attrs.get(A_TOKENS_OUT)) or 0
            usd, problem = span_cost_usd(attrs)
            if problem:
                summary.problems.append(problem)
            else:
                summary.cost_usd += usd or 0.0
            declared = attrs.get(A_COST_DECLARED)
            if isinstance(declared, (int, float)) and not isinstance(declared, bool):
                summary.cost_declared_usd += float(declared)
        elif role == "retrieval":
            ids = attrs.get(A_RETRIEVAL_IDS)
            summary.documents += len(ids) if isinstance(ids, list) else 0

    # Le coût déclaré n'est pas une mesure ; l'écart avec le coût recalculé en
    # est une. Au-delà du seuil, les tokens et le chiffre annoncé ne parlent plus
    # du même appel — et c'est ce genre d'écart qui fait passer un run sous un
    # plafond qu'il dépasse.
    gap = abs(summary.cost_declared_usd - summary.cost_usd)
    if summary.cost_declared_usd and gap > COST_DIVERGENCE_ABS_USD and gap > COST_DIVERGENCE_RATIO * summary.cost_usd:
        summary.problems.append(
            f"coût déclaré ${summary.cost_declared_usd:.6f} contre ${summary.cost_usd:.6f} recalculé "
            f"depuis les tokens (écart ${gap:.6f}) — le chiffre du run ne suit pas ses propres tokens")

    if not run_roots:
        summary.problems.append(f"aucun span racine `{RUN_SPAN}` — début et fin du run inconnus")
    elif len(run_roots) > 1:
        summary.problems.append(f"{len(run_roots)} spans racines `{RUN_SPAN}` — un fichier de trace vaut pour UN run")
    else:
        root = run_roots[0]
        summary.latency_ms = _duration_ms(root)
        if not root.get("end") and not root.get("duration_ms"):
            summary.problems.append("le span racine n'a ni `end` ni `duration_ms` — le run n'a pas déclaré sa fin")

    summary.complete = bool(run_roots) and len(run_roots) == 1 and not summary.problems
    return summary


def summarize_all(root: Path) -> list[TraceSummary]:
    directory = runs_dir(root)
    if not directory.is_dir():
        return []
    return [summarize(p) for p in sorted(directory.glob("*.jsonl"))]
