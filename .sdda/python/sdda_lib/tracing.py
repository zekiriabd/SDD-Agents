"""Trace d'un run — le format canonique, et ce qu'on peut en prouver.

Il n'existe pas de *stack trace* dans un système non déterministe. Quand un
agent répond à côté, il n'y a ni exception, ni ligne fautive : il y a une suite
de décisions dont chacune était plausible. La trace est le seul artefact qui
permette un post-mortem — et, plus quotidiennement, qui permette de mesurer le
coût réel (P6) et de comparer deux runs du même item (P3).

Ce module ne produit pas les traces : c'est l'application générée qui les écrit,
via sa stack d'observabilité (`observability/*.md`, spans OTel-GenAI). Il définit
**le format d'atterrissage** et ce que le framework est en droit d'en exiger :

    workspace/traces/runs/{run_id}.jsonl      une ligne JSON par événement

Un fichier par run, append-only, une ligne par événement. Le choix du JSONL
n'est pas cosmétique : un run interrompu laisse une trace lisible jusqu'à
l'interruption, là où un JSON unique laisse un fichier invalide — et c'est
précisément sur les runs interrompus qu'on veut regarder.

Ce que le framework en tire, sans LLM :
  - `trace-emitted-per-run` : un run sans trace n'a pas eu lieu, pour la gate ;
  - le coût et la latence **mesurés** de G6, opposés à l'estimation de G2 ;
  - la trajectoire (suite d'agents et d'outils), comparée au grader `trajectory` ;
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

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# Les formes de secrets vivent dans `scan_secrets` (G7) et nulle part ailleurs.
# Un import lib -> scripts est inhabituel ici, mais recopier les motifs les
# ferait dériver : la gate détecterait des formes que la trace ne rédige pas.
from sdda_scripts.scan_secrets import ASSIGNMENT as _ASSIGNMENT
from sdda_scripts.scan_secrets import COMPILED as _SECRET_PATTERNS
from sdda_scripts.scan_secrets import is_placeholder as _is_placeholder

#: Types d'événement. Grammaire close : un type inconnu est une trace qu'aucun
#: lecteur ne saura agréger, donc une mesure perdue au moment où elle compte.
EVENT_KINDS: tuple[str, ...] = (
    "run_start",      # runId, missionId, entrée, hashes épinglés (P10)
    "agent_turn",     # un tour d'agent : agentId, tier, modèle, tokens, coût, latence
    "llm_call",       # un appel modèle : modèle, tokens in/out, coût, latence
    "tool_call",      # nom, arguments redigés, classe d'effet de bord, succès/erreur
    "retrieval",      # requête, index, documents retournés (ids + scores)
    "guardrail",      # id, verdict, action
    "bound_exceeded", # quelle borne, quelle politique appliquée
    "handoff",        # de -> vers, état transmis (redigé)
    "run_end",        # verdict, coût total, latence totale
)

#: Champs exigés sur chaque événement, quel que soit son type.
COMMON_FIELDS: tuple[str, ...] = ("ts", "runId", "kind")

#: Champs exigés par type — ce sans quoi l'événement n'est pas exploitable.
REQUIRED_BY_KIND: dict[str, tuple[str, ...]] = {
    "run_start": ("missionId",),
    "agent_turn": ("agentId",),
    "llm_call": ("model", "tokensIn", "tokensOut"),
    "tool_call": ("tool", "sideEffectClass", "ok"),
    "retrieval": ("index", "documents"),
    "guardrail": ("guardrail", "passed"),
    "bound_exceeded": ("bound", "policy"),
    "handoff": ("fromAgent", "toAgent"),
    "run_end": ("verdict",),
}

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
    """Écrivain append-only d'une trace de run.

    Pas de tampon : chaque événement est écrit et vidé immédiatement. Un run qui
    part en boucle ou qui est tué doit laisser derrière lui ce qui a mené là —
    c'est exactement le run qu'on voudra lire.
    """

    root: Path
    run_id: str
    _path: Path = field(init=False)

    def __post_init__(self) -> None:
        self._path = trace_path(self.root, self.run_id)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, kind: str, ts: str, **fields: Any) -> dict[str, Any]:
        if kind not in EVENT_KINDS:
            raise ValueError(f"type d'événement inconnu : {kind!r} (admis : {list(EVENT_KINDS)})")
        event = {"ts": ts, "runId": self.run_id, "kind": kind, **redact(fields)}
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
        return event


# ---------------------------------------------------------------------------
# Lecture et vérification
# ---------------------------------------------------------------------------
def read_events(path: Path) -> Iterator[dict[str, Any]]:
    """Les événements d'une trace. Une ligne illisible est ignorée, pas fatale.

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
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                yield event


@dataclass
class TraceSummary:
    """Ce qu'une trace prouve, sans interprétation."""

    run_id: str
    events: int = 0
    kinds: dict[str, int] = field(default_factory=dict)
    agents: list[str] = field(default_factory=list)
    trajectory: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    documents: int = 0
    bounds_exceeded: list[str] = field(default_factory=list)
    complete: bool = False
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id, "events": self.events, "kinds": self.kinds,
            "agents": self.agents, "trajectory": self.trajectory,
            "tokensIn": self.tokens_in, "tokensOut": self.tokens_out,
            "costUsd": round(self.cost_usd, 6), "latencyMs": self.latency_ms,
            "documents": self.documents, "boundsExceeded": self.bounds_exceeded,
            "complete": self.complete, "problems": self.problems,
        }


def summarize(path: Path) -> TraceSummary:
    run_id = path.stem
    summary = TraceSummary(run_id=run_id)
    seen_start = seen_end = False

    for event in read_events(path):
        summary.events += 1
        kind = str(event.get("kind") or "")
        summary.kinds[kind] = summary.kinds.get(kind, 0) + 1

        if kind not in EVENT_KINDS:
            summary.problems.append(f"type d'événement inconnu : `{kind}`")
            continue
        missing = [f for f in COMMON_FIELDS + REQUIRED_BY_KIND.get(kind, ()) if event.get(f) is None]
        if missing:
            summary.problems.append(f"`{kind}` : champ(s) absent(s) {missing}")

        if kind == "run_start":
            seen_start = True
        elif kind == "run_end":
            seen_end = True
            summary.latency_ms = int(event.get("latencyMs") or summary.latency_ms)
        elif kind == "agent_turn":
            agent = str(event.get("agentId") or "")
            if agent:
                summary.trajectory.append(agent)
                if agent not in summary.agents:
                    summary.agents.append(agent)
        elif kind == "tool_call":
            tool = str(event.get("tool") or "")
            if tool:
                summary.trajectory.append(f"tool:{tool}")
        elif kind == "retrieval":
            docs = event.get("documents")
            summary.documents += len(docs) if isinstance(docs, list) else 0
        elif kind == "bound_exceeded":
            summary.bounds_exceeded.append(str(event.get("bound") or "?"))

        for key, target in (("tokensIn", "tokens_in"), ("tokensOut", "tokens_out")):
            if isinstance(event.get(key), int):
                setattr(summary, target, getattr(summary, target) + event[key])
        if isinstance(event.get("costUsd"), (int, float)):
            summary.cost_usd += float(event["costUsd"])

    # Un run sans `run_start` ni `run_end` n'est pas une trace incomplète : c'est
    # une trace dont on ne sait pas si elle est complète, ce qui est pire — on ne
    # peut ni mesurer la latence, ni affirmer que le run a terminé.
    if not seen_start:
        summary.problems.append("aucun événement `run_start` — début du run inconnu")
    if not seen_end:
        summary.problems.append("aucun événement `run_end` — le run n'a pas déclaré sa fin")
    summary.complete = seen_start and seen_end and not summary.problems
    return summary


def summarize_all(root: Path) -> list[TraceSummary]:
    directory = runs_dir(root)
    if not directory.is_dir():
        return []
    return [summarize(p) for p in sorted(directory.glob("*.jsonl"))]
