"""Le squelette n'émet son manifeste que s'il tient l'orchestration.

`diff_code_vs_ir.py` refuse deux manifestes sous `**/orchestration/`, et le
manifeste trivial de la boucle de démarrage écraserait celui d'un graphe
LangGraph écrit par `dev-orchestration` — un diff vert contre un dessin qui
n'est plus celui du code. Quand un graphe de framework est déclaré, ou qu'un
manifeste étranger est déjà là, `write_manifest` se tait et le dit (`None`).
"""
from __future__ import annotations

import json
from pathlib import Path

from test_runtime_app import Runtime, rt  # noqa: F401 — la fixture `rt`

FOREIGN = {"generatedBy": "orchestration.langgraph", "entryNode": "supervisor", "terminalNodes": ["finalize"],
           "maxHops": 6, "nodes": [{"id": "supervisor", "kind": "agent"}], "edges": []}


def _graph(rt: Runtime):
    return rt.base.SingleAgentGraph("agent", ref="1-assistant")


def test_without_a_framework_graph_the_skeleton_writes_its_manifest(rt: Runtime) -> None:
    rt.base.declare_framework_graph(None)
    path = _graph(rt).write_manifest(rt.package / "orchestration")
    assert path is not None and path.name == rt.base.MANIFEST_NAME
    assert json.loads(path.read_text(encoding="utf-8"))["generatedBy"] == rt.base.GENERATED_BY


def test_a_foreign_manifest_is_never_overwritten(rt: Runtime) -> None:
    rt.base.declare_framework_graph(None)
    target = rt.package / "orchestration" / rt.base.MANIFEST_NAME
    target.write_text(json.dumps(FOREIGN), encoding="utf-8")
    assert _graph(rt).write_manifest(target.parent) is None
    assert json.loads(target.read_text(encoding="utf-8")) == FOREIGN


def test_a_declared_framework_graph_silences_the_skeleton(rt: Runtime) -> None:
    rt.base.declare_framework_graph("SupportAssistant.orchestration.graph")
    try:
        assert _graph(rt).write_manifest(rt.package / "orchestration") is None
        assert not (rt.package / "orchestration" / rt.base.MANIFEST_NAME).exists()
    finally:
        rt.base.declare_framework_graph(None)
    assert _graph(rt).write_manifest(rt.package / "orchestration") is not None


def test_run_service_declares_the_framework_graph_from_its_agent_factory(rt: Runtime) -> None:
    rt.base.declare_framework_graph(None)
    rt.service()                                   # boucle de démarrage : rien de déclaré
    assert rt.base.framework_graph_origin() is None

    def factory(**kwargs):                          # ce que `dev-orchestration` fournit
        raise AssertionError("jamais appelé ici")

    rt.service(agent_factory=factory)
    try:
        assert rt.base.framework_graph_origin() == factory.__module__
        assert _graph(rt).write_manifest(rt.package / "orchestration") is None
    finally:
        rt.base.declare_framework_graph(None)


def test_the_skeleton_manifest_is_the_one_a_single_agent_project_compares(rt: Runtime) -> None:
    """Le cas nominal ne régresse pas : un projet sans graphe de framework a toujours un manifeste."""
    from sdda_lib.errors import Report
    from sdda_scripts import diff_code_vs_ir

    rt.base.declare_framework_graph(None)
    path = _graph(rt).write_manifest(rt.package / "orchestration")
    assert path is not None
    report = Report(name="T")
    diff_code_vs_ir.diff_orchestration(
        {"entryNode": "agent", "terminalNodes": ["agent"], "nodes": [{"id": "agent", "kind": "agent"}], "edges": []},
        json.loads(path.read_text(encoding="utf-8")), report, "manifest")
    assert report.ok, report.render_text()
