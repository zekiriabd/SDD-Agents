"""Traces de runs lues comme des ARBRES — plomberie commune de `cost_report` et `trajectory_report`.

`tracing.summarize` rend ce qu'une trace prouve au niveau du RUN (coût recalculé,
trajectoire, bornes). Les revues d'étage B ont besoin d'un grain plus fin :
quel agent a payé quel appel LLM, quel nœud a été traversé combien de fois,
quelle CAP a coûté quoi. Tout cela se lit dans l'arbre (`parent_span_id`), et
ce module ne fait que le parcourir — il ne recalcule rien que `tracing` ne
sache déjà : le coût d'un span `chat` vient de `tracing.span_cost_usd`, jamais
de `sdda.cost.usd`.

Module privé (`_`) : `sdda_cli.discover()` ne l'expose pas en sous-commande.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdda_lib import tracing

A_CAP_IDS = "sdda.cap.ids"
A_RUN_KIND = "sdda.run.kind"
#: La trace des appels de JUGE d'un run d'eval (`eval_runner.JudgeTrace`) : un
#: coût d'évaluation, jamais un run du produit.
JUDGE_RUN_KIND = "eval-judge"
A_JUDGE_ROLE = "sdda.judge.role"
A_BOUND_POLICY = "sdda.bound.policy"
A_BOUND_POLICY_APPLIED = "sdda.bound.policy_applied"
A_TOOL_RETRY = "sdda.tool.retry_attempt"
A_TOOL_ERROR_CODE = "sdda.tool.error_code"
A_RESULT_BYTES = "sdda.tool.result.bytes"

#: `{item}-{run_index}` : la forme d'identifiant de run qu'écrit l'exécuteur
#: d'eval généré (`InProcessExecutor`, `CliExecutor`). C'est elle qui rattache
#: une trace à l'item du jeu qui l'a produite.
_RUN_INDEX_RE = re.compile(r"^(?P<item>.+)-(?P<index>\d+)$")


def percentile(values: list[float], q: float) -> float:
    """Rang le plus proche, comme `retrieval_metrics._p95` : sur peu de points, la queue."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def distribution(values: list[float], digits: int = 6) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "p50": None, "p95": None, "p99": None, "max": None}
    return {"n": len(values), "mean": round(sum(values) / len(values), digits),
            "p50": round(percentile(values, 0.50), digits), "p95": round(percentile(values, 0.95), digits),
            "p99": round(percentile(values, 0.99), digits), "max": round(max(values), digits)}


def item_of(run_id: str) -> str:
    m = _RUN_INDEX_RE.match(run_id)
    return m.group("item") if m else run_id


def span_duration_ms(span: dict[str, Any]) -> int:
    return tracing._duration_ms(span)


@dataclass
class RunTrace:
    """Une trace de run, spans triés par début, indexés par identifiant."""

    run_id: str
    path: Path
    spans: list[dict[str, Any]]
    by_id: dict[str, dict[str, Any]]
    summary: tracing.TraceSummary
    root: dict[str, Any] | None = None
    mission: str = ""

    def role(self, span: dict[str, Any]) -> str | None:
        return tracing.span_role(span)

    def owning_agent(self, span: dict[str, Any]) -> dict[str, Any] | None:
        """Le span d'agent le plus proche en remontant l'arbre — celui qui a fait l'appel."""
        seen, cursor = {str(span.get("span_id"))}, span
        while True:
            parent = str(cursor.get("parent_span_id") or "")
            if not parent or parent in seen or parent not in self.by_id:
                return None
            seen.add(parent)
            cursor = self.by_id[parent]
            if tracing.span_role(cursor) == "agent":
                return cursor

    def agents(self) -> list[dict[str, Any]]:
        return [s for s in self.spans if tracing.span_role(s) == "agent"]

    @property
    def judge(self) -> bool:
        """La trace des appels du juge LLM d'un run d'eval.

        Par son span racine, ou — racine jamais écrite, eval interrompue — par
        ses spans : que des appels `chat` marqués `sdda.judge.role`.
        """
        if self.root is not None and tracing.attributes_of(self.root).get(A_RUN_KIND) == JUDGE_RUN_KIND:
            return True
        llm = [s for s in self.spans if tracing.span_role(s) == "llm"]
        return bool(llm) and all(tracing.attributes_of(s).get(A_JUDGE_ROLE) for s in llm) \
            and not any(tracing.span_role(s) in ("agent", "tool") for s in self.spans)

    @property
    def product(self) -> bool:
        """Un run du PRODUIT (au moins un agent ou un appel LLM), pas une trace de construction.

        La trace du juge a des spans `chat` : sans l'exclure, elle entrait dans
        la distribution de coût du produit comme un run « non attribué » — la
        facture de toute une eval comptée comme UN run, qui pouvait crever le
        plafond, ce que `JudgeTrace` promettait justement d'éviter.
        """
        return not self.judge and any(tracing.span_role(s) in ("agent", "llm", "tool") for s in self.spans)

    @property
    def status_ok(self) -> bool:
        return self.root is None or str(self.root.get("status") or "OK").upper() != "ERROR"


def agent_key(span: dict[str, Any]) -> str:
    attrs = tracing.attributes_of(span)
    for key in (tracing.A_AGENT_ID, tracing.A_AGENT_NAME):
        if isinstance(attrs.get(key), str) and attrs[key].strip():
            return attrs[key].strip()
    name = str(span.get("name") or "")
    return name.split(" ", 1)[1] if " " in name else name


def visits(run: RunTrace) -> list[tuple[str, dict[str, Any]]]:
    """Les passages par un agent, dans l'ordre : deux spans consécutifs du MÊME agent sont un passage.

    Un agent qui boucle (`sdda.agent.iteration` 1, 2, 3) émet un span par tour ;
    ce ne sont pas trois hops, c'est un nœud traversé une fois qui a réfléchi
    trois fois. Compter chaque tour comme un hop ferait franchir `maxHops` à un
    graphe qui ne s'est pas déplacé.
    """
    out: list[tuple[str, dict[str, Any]]] = []
    for span in run.agents():
        key = agent_key(span)
        if not out or out[-1][0] != key:
            out.append((key, span))
    return out


def node_index(ir: dict[str, Any]) -> dict[str, str]:
    """Clé d'agent observée -> id de nœud de l'IR.

    Une trace nomme l'agent par `gen_ai.agent.id` (l'id de contrat,
    `1-billing-specialist`) ou par `gen_ai.agent.name` (`billing-specialist`) ;
    le graphe le nomme par son nœud (`billing`). Les trois formes sont admises,
    pour qu'aucune trace correcte ne passe pour une arête hors IR.
    """
    out: dict[str, str] = {}
    for node in (ir.get("orchestration") or {}).get("nodes") or []:
        nid, ref = str(node.get("id") or ""), str(node.get("ref") or "")
        if not nid:
            continue
        out[nid] = nid
        if ref:
            out[ref] = nid
            head, _, tail = ref.partition("-")
            if head.isdigit() and tail:
                out.setdefault(tail, nid)
    return out


def load_run(path: Path) -> RunTrace:
    spans = [s for s in tracing.read_spans(path) if not tracing.is_legacy_event(s) and s.get("span_id")]
    spans.sort(key=lambda s: str(s.get("start") or ""))
    by_id = {str(s["span_id"]): s for s in spans}
    roots = [s for s in spans if str(s.get("name") or "").startswith(tracing.RUN_SPAN)
             and (not s.get("parent_span_id") or str(s["parent_span_id"]) not in by_id)]
    root = roots[0] if len(roots) == 1 else None
    mission = str(tracing.attributes_of(root).get(tracing.A_MISSION_ID) or "") if root else ""
    return RunTrace(path.stem, path, spans, by_id, tracing.summarize(path), root, mission)


def mission_matches(run: RunTrace, number: int | None, mission_id: str) -> bool | None:
    """True/False si la trace dit sa mission ; None si elle ne le dit pas."""
    if not run.mission:
        return None
    if number is None:
        return True
    head = run.mission.split("-", 1)[0]
    return run.mission in (str(number), mission_id) or head == str(number)


@dataclass
class Loaded:
    runs: list[RunTrace] = field(default_factory=list)
    build_only: list[RunTrace] = field(default_factory=list)
    judge: list[RunTrace] = field(default_factory=list)
    other_mission: list[str] = field(default_factory=list)
    unattributed: list[str] = field(default_factory=list)


def load_runs(traces_dir: Path, number: int | None, mission_id: str) -> Loaded:
    """Les runs du PRODUIT de la mission ; les traces de construction à part, jamais mêlées."""
    out = Loaded()
    if not traces_dir.is_dir():
        return out
    for path in sorted(traces_dir.glob("*.jsonl")):
        run = load_run(path)
        if run.judge:
            out.judge.append(run)
            continue
        if not run.product:
            out.build_only.append(run)
            continue
        match = mission_matches(run, number, mission_id)
        if match is False:
            out.other_mission.append(run.run_id)
            continue
        if match is None:
            out.unattributed.append(run.run_id)
        out.runs.append(run)
    return out
