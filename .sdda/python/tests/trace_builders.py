"""Constructeurs de traces de runs pour les tests des revues d'étage B.

Une trace réaliste au format span — `sdda.run` > `invoke_agent` > (`chat`,
`execute_tool`) — écrite par `tracing.TraceWriter`, c'est-à-dire exactement la
forme que l'application générée produit. Pas un test : un outil pour en écrire.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sdda_lib import tracing

MODEL = "claude-sonnet-5"


def _ts(second: int) -> str:
    minutes, sec = divmod(second, 60)
    return f"2026-09-21T10:{minutes:02d}:{sec:02d}Z"


def write_run(root: Path, run_id: str, path: list[str], *, mission: str | None = "1", tokens_in: int = 1000,
              tokens_out: int = 200, latency_ms: int = 2000, declared: float | None = None,
              tools: dict[int, list[tuple[str, str]]] | None = None, bound: dict[int, str] | None = None,
              agent_status: dict[int, str] | None = None, root_status: str = "OK",
              cap_ids: dict[int, list[str]] | None = None, extra_attrs: dict[int, dict[str, Any]] | None = None) -> Path:
    """Un run dont les agents successifs sont `path` (ids d'agents de l'IR).

    `tools[i]` : appels d'outil (nom, statut) du i-ème agent ; `bound[i]` : borne
    atteinte ; `agent_status[i]` : statut du span d'agent.
    """
    w = tracing.TraceWriter(root, run_id)
    attrs: dict[str, Any] = {"sdda.run.id": run_id}
    if mission is not None:
        attrs["sdda.mission.id"] = mission
    w.emit(f"sdda.run {mission or '?'}", span_id="root", start=_ts(0), end=_ts(max(1, latency_ms // 1000)),
           duration_ms=latency_ms, status=root_status, attributes=attrs)
    t = 1
    for i, agent in enumerate(path):
        a_attrs: dict[str, Any] = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.id": agent,
                                   "gen_ai.agent.name": agent.split("-", 1)[1] if agent[:1].isdigit() else agent}
        if bound and i in bound:
            a_attrs["sdda.bound.exceeded"] = bound[i]
        if cap_ids and i in cap_ids:
            a_attrs["sdda.cap.ids"] = cap_ids[i]
        a_attrs.update((extra_attrs or {}).get(i, {}))
        w.emit(f"invoke_agent {agent}", span_id=f"a{i}", parent_span_id="root", start=_ts(t), end=_ts(t + 1),
               duration_ms=300, status=(agent_status or {}).get(i, "OK"), attributes=a_attrs)
        chat: dict[str, Any] = {"gen_ai.operation.name": "chat", "gen_ai.request.model": MODEL,
                                "gen_ai.usage.input_tokens": tokens_in, "gen_ai.usage.output_tokens": tokens_out}
        if declared is not None:
            chat["sdda.cost.usd"] = declared
        w.emit(f"chat {MODEL}", span_id=f"c{i}", parent_span_id=f"a{i}", start=_ts(t), end=_ts(t + 1),
               duration_ms=200, attributes=chat)
        for j, (tool, status) in enumerate((tools or {}).get(i, [])):
            w.emit(f"execute_tool {tool}", span_id=f"t{i}-{j}", parent_span_id=f"a{i}", start=_ts(t), end=_ts(t + 1),
                   duration_ms=50, status=status,
                   attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool,
                               "sdda.tool.side_effect_class": "read-only"})
        t += 2
    return w.path


def write_build_run(root: Path, run_id: str, cost: float) -> Path:
    """Une trace de CONSTRUCTION : un Developer Agent et son coût déclaré par le harnais."""
    w = tracing.TraceWriter(root, run_id)
    w.emit("sdda.run build", span_id="root", start=_ts(0), end=_ts(5), duration_ms=5000, attributes={})
    w.emit("sdda.build.agent qa-evals", span_id="b1", parent_span_id="root", start=_ts(1), end=_ts(2),
           attributes={"sdda.build.agent": "qa-evals", "sdda.cost.usd": cost})
    return w.path
