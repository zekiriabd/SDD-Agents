"""ARCHITECTURE GATE — l'architecte décide, le framework vérifie qu'il a décidé.

Le contrat que ces tests défendent (PHILOSOPHY P7) :

  - **bloquant** : la spécification est INCOMPLÈTE pour l'architecture choisie
    dans STACK.md. Un champ manquant ne reste pas manquant — il est comblé à la
    génération par un modèle qui inventera un rôle plausible.
  - **jamais bloquant** : l'architecture est *discutable*. Sept subagents là où
    deux suffiraient est l'erreur de l'architecte, et il a le droit de la
    commettre en connaissance de cause.

Un test qui rendrait la seconde catégorie bloquante casserait le principe ;
c'est pour cela qu'ils sont explicites (`test_a_debatable_architecture_is_never_blocked`).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_scripts import validate_architecture as va
from sdda_scripts import validate_topology as vt

TOPOLOGY = "workspace/pipeline/topology/1-topology.md"
STACK = "workspace/stack/STACK.md"


def patch(project: Path, rel: str, old: str, new: str) -> None:
    path = project / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"motif absent de {rel} : {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def set_pattern(project: Path, pattern: str) -> None:
    patch(project, STACK, " - .sdda/stacks/orchestration/router.md",
          f" - .sdda/stacks/orchestration/{pattern}.md")


def errors(project: Path) -> set[str]:
    return {f.cls for f in va.run(project, mission=1).errors}


def classes(project: Path) -> set[str]:
    return {f.cls for f in va.run(project, mission=1).findings}


# ---------------------------------------------------------------------------
# Le cas vert
# ---------------------------------------------------------------------------
def test_a_complete_declaration_passes(project: Path) -> None:
    report = va.run(project, mission=1)
    assert report.ok, report.render_text()
    assert report.data["orchestrator"] == "intent-classifier"
    assert report.data["subagents"] == ["billing-specialist"]
    assert report.data["relations"] == 3


def test_explain_reports_what_the_active_stack_demands(project: Path) -> None:
    report = va.run(project, explain=True)
    assert report.data["active"]["orchestration"] == "router"
    assert report.data["requires"]["orchestration/router"]["fallback"] is True


def test_cli_json_mode(project: Path) -> None:
    code, out = run_main(va.main, ["--root", str(project), "--mission", "1", "--json", "--no-report"])
    assert code == 0, out
    assert json.loads(out)["ok"] is True


# ---------------------------------------------------------------------------
# Le roster : c'est l'architecte qui le remplit
# ---------------------------------------------------------------------------
def test_a_missing_roster_blocks_generation(project: Path) -> None:
    path = project / TOPOLOGY
    text = path.read_text(encoding="utf-8")
    start, end = text.index("## 2. Roster déclaré"), text.index("## 3. Alternative")
    path.write_text(text[:start] + text[end:], encoding="utf-8")
    assert "ARCH_ROSTER_MISSING" in errors(project)


def test_an_orchestrator_without_a_role_blocks(project: Path) -> None:
    patch(project, TOPOLOGY,
          "- **rôle** : classe l'intention entrante et route vers le spécialiste adéquat\n", "")
    assert "ARCH_ORCHESTRATOR_INCOMPLETE" in errors(project)


def test_an_orchestrator_without_responsibilities_blocks(project: Path) -> None:
    patch(project, TOPOLOGY, "- **responsabilités** : router ou demander une clarification ;"
                             " ne répond jamais au fond\n", "")
    assert "ARCH_ORCHESTRATOR_INCOMPLETE" in errors(project)


def test_too_few_subagents_for_the_chosen_pattern(project: Path) -> None:
    """`supervisor` exige >= 2 subagents nominatifs ; la fixture n'en déclare qu'un."""
    set_pattern(project, "supervisor")
    assert "ARCH_ROSTER_INCOMPLETE" in errors(project)


def test_single_agent_refuses_declared_subagents(project: Path) -> None:
    """Le pattern et le roster doivent dire la même chose, dans les deux sens."""
    set_pattern(project, "single-agent")
    assert "ARCH_ROSTER_INCOHERENT" in errors(project)


def test_an_agent_without_tools_blocks(project: Path) -> None:
    """Un champ vide laisse le moindre privilège à la discrétion du générateur."""
    patch(project, TOPOLOGY, "| `invoice_lookup`, `zendesk_create_ticket` | balanced | |",
          "|  | balanced | |")
    assert "ARCH_AGENT_TOOLS_UNDECLARED" in errors(project)


def test_an_agent_without_tier_nor_model_blocks(project: Path) -> None:
    patch(project, TOPOLOGY, "| `invoice_lookup`, `zendesk_create_ticket` | balanced | |",
          "| `invoice_lookup` |  |  |")
    assert "ARCH_AGENT_MODEL_UNDECLARED" in errors(project)


def test_a_named_model_is_accepted_instead_of_a_tier(project: Path) -> None:
    patch(project, TOPOLOGY, "| `invoice_lookup`, `zendesk_create_ticket` | balanced | |",
          "| `invoice_lookup` |  | claude-sonnet-5 |")
    assert "ARCH_AGENT_MODEL_UNDECLARED" not in errors(project)


# ---------------------------------------------------------------------------
# Relations, repli, bornes
# ---------------------------------------------------------------------------
def test_missing_relations_block(project: Path) -> None:
    path = project / TOPOLOGY
    text = path.read_text(encoding="utf-8")
    start, end = text.index("### 2.3 Relations"), text.index("### 2.4 Bornes")
    path.write_text(text[:start] + text[end:], encoding="utf-8")
    assert "ARCH_RELATIONS_MISSING" in errors(project)


def test_a_relation_without_condition_blocks(project: Path) -> None:
    """« Le contexte suit » n'est pas une condition."""
    patch(project, TOPOLOGY, "| `intent == 'billing' && confidence >= 0.7` | oui |", "|  | oui |")
    assert "ARCH_CONDITION_MISSING" in errors(project)


def test_a_router_without_a_fallback_blocks(project: Path) -> None:
    """La branche « aucune classe » est celle que personne n'écrit, et celle qui arrive."""
    patch(project, TOPOLOGY,
          "| `intent-classifier` | `clarify_request` | aucune classe au-dessus du seuil — chemin de repli | oui |\n",
          "")
    assert "ARCH_FALLBACK_MISSING" in errors(project)


def test_a_cyclic_pattern_without_loop_bounds_blocks(project: Path) -> None:
    set_pattern(project, "graph")
    path = project / TOPOLOGY
    text = path.read_text(encoding="utf-8")
    start, end = text.index("### 2.4 Bornes"), text.index("\n---\n\n## 3. Alternative")
    path.write_text(text[:start] + text[end:], encoding="utf-8")
    assert "ARCH_LOOP_BOUND_MISSING" in errors(project)


def test_parallel_requires_a_declared_merge_strategy(project: Path) -> None:
    set_pattern(project, "parallel")
    assert "ARCH_MERGE_STRATEGY_MISSING" in errors(project)


# ---------------------------------------------------------------------------
# Les autres axes d'architecture
# ---------------------------------------------------------------------------
def test_rag_without_its_parameters_blocks(project: Path) -> None:
    patch(project, STACK, "ChunkStrategy: recursive-structural\n", "")
    assert "ARCH_SPEC_INCOMPLETE" in errors(project)


def test_rag_none_demands_nothing(project: Path) -> None:
    patch(project, STACK, " - .sdda/stacks/rag/hybrid.md", " - .sdda/stacks/rag/none.md")
    patch(project, STACK, "ChunkStrategy: recursive-structural\n", "")
    assert "ARCH_SPEC_INCOMPLETE" not in errors(project)


def test_runtime_models_keys_are_required(project: Path) -> None:
    patch(project, STACK, "RuntimeProvider: anthropic\n", "")
    assert "ARCH_SPEC_INCOMPLETE" in errors(project)


def test_multi_model_demands_a_binding_per_agent(project: Path) -> None:
    """Trois modèles distincts déclarés : « qui utilise quoi » devient une décision."""
    patch(project, TOPOLOGY, "| `invoice_lookup`, `zendesk_create_ticket` | balanced | |",
          "| `invoice_lookup` |  |  |")
    assert "ARCH_AGENT_MODEL_UNDECLARED" in errors(project)


def test_mcp_server_without_allowlist_blocks(tmp_path: Path) -> None:
    project = make_project(tmp_path, "project_declared_sources")
    (project / "workspace/pipeline/topology/1-topology.md").write_text(
        (make_project(tmp_path / "ref") / TOPOLOGY).read_text(encoding="utf-8"), encoding="utf-8")
    patch(project, STACK, "    tools_allowlist: [crm_get_contract]\n", "")
    assert "ARCH_MCP_UNDECLARED" in errors(project)


# ---------------------------------------------------------------------------
# La frontière : ce qui ne doit JAMAIS bloquer
# ---------------------------------------------------------------------------
def test_a_debatable_architecture_is_never_blocked(project: Path) -> None:
    """Sept agents sans raison nommée : un AVERTISSEMENT, jamais un refus (P7)."""
    patch(project, TOPOLOGY,
          "| `billing-specialist` | répond aux questions de facturation |",
          "\n".join(
              f"| `agent-{i}` | rôle {i} | responsabilité {i} | `invoice_lookup` | balanced | |"
              for i in range(2, 8)
          ) + "\n| `billing-specialist` | répond aux questions de facturation |")
    report = va.run(project, mission=1)
    assert report.ok, report.render_text()


def test_simplicity_is_advisory_not_blocking(project: Path) -> None:
    """Le veto de P7 est devenu un conseil : l'architecture appartient à l'architecte."""
    patch(project, TOPOLOGY, "| `billing-specialist` | tier distinct |",
          "| `billing-specialist` | séparation des responsabilités |")
    report = vt.validate_topology_file(project / TOPOLOGY, project, None, write_report=False)
    assert "TOPOLOGY_SIMPLICITY_ADVISORY" in {f.cls for f in report.warnings}
    assert "TOPOLOGY_SIMPLICITY_ADVISORY" not in {f.cls for f in report.errors}
    assert "TOPOLOGY_UNJUSTIFIED" not in {f.cls for f in report.findings}


def test_an_empty_simplicity_section_only_warns(project: Path) -> None:
    path = project / TOPOLOGY
    text = path.read_text(encoding="utf-8")
    start, end = text.index("## 3. Alternative"), text.index("## 4. Le graphe")
    path.write_text(text[:start] + "## 3. Alternative plus simple considérée\n\n" + text[end:], encoding="utf-8")
    report = vt.validate_topology_file(path, project, None, write_report=False)
    assert "TOPOLOGY_SIMPLICITY_ADVISORY" in {f.cls for f in report.warnings}
    assert not any(f.cls == "TOPOLOGY_SIMPLICITY_ADVISORY" for f in report.errors)


# ---------------------------------------------------------------------------
# Le manifeste de roster — la forme recommandée
# ---------------------------------------------------------------------------
TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "roster.template.md"
ROSTER = "workspace/feats/1-roster.md"


def use_manifest(project: Path, content: str | None = None) -> Path:
    """Bascule le projet sur le roster Markdown : la section de la topologie ne peut pas coexister."""
    manifest = project / ROSTER
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(content if content is not None else TEMPLATE.read_text(encoding="utf-8"),
                        encoding="utf-8")
    return manifest


def drop_markdown_roster(project: Path) -> None:
    path = project / TOPOLOGY
    text = path.read_text(encoding="utf-8")
    start, end = text.index("## 2. Roster déclaré"), text.index("## 3. Alternative")
    path.write_text(text[:start] + text[end:], encoding="utf-8")


def test_a_manifest_roster_is_accepted(project: Path) -> None:
    use_manifest(project)
    drop_markdown_roster(project)
    report = va.run(project, mission=1)
    assert report.ok, report.render_text()
    assert report.data["rosterSource"] == ROSTER
    assert report.data["orchestrator"] == "support-orchestrator"
    assert report.data["subagents"] == ["billing-specialist"]
    assert report.data["loopBounds"] == 1


def test_the_shipped_template_passes_its_own_gate(project: Path) -> None:
    """Un gabarit que la gate refuse est un gabarit que personne ne réutilise."""
    use_manifest(project)
    drop_markdown_roster(project)
    assert va.run(project, mission=1).ok


def test_two_roster_sources_are_refused(project: Path) -> None:
    """Deux déclarations divergentes : c'est celle que personne ne relit qui gouverne."""
    use_manifest(project)  # la section Markdown reste en place
    assert "ARCH_ROSTER_DUPLICATE_SOURCE" in errors(project)


def test_an_empty_tool_list_means_none_not_forgotten(project: Path) -> None:
    """`tools: []` est une décision ; une clé absente est un oubli."""
    use_manifest(project, TEMPLATE.read_text(encoding="utf-8").replace(
        "    tools: [invoice_lookup, zendesk_create_ticket]\n", "    tools: []\n"))
    drop_markdown_roster(project)
    assert "ARCH_AGENT_TOOLS_UNDECLARED" not in errors(project)


def test_a_missing_tool_key_in_the_manifest_blocks(project: Path) -> None:
    use_manifest(project, TEMPLATE.read_text(encoding="utf-8").replace(
        "    tools: [invoice_lookup, zendesk_create_ticket]\n", ""))
    drop_markdown_roster(project)
    assert "ARCH_AGENT_TOOLS_UNDECLARED" in errors(project)


def test_a_manifest_without_fallback_blocks(project: Path) -> None:
    use_manifest(project, TEMPLATE.read_text(encoding="utf-8").replace(
        '    condition: "aucune classe au-dessus du seuil — chemin de repli"',
        '    condition: "toujours"'))
    drop_markdown_roster(project)
    assert "ARCH_FALLBACK_MISSING" in errors(project)


def test_a_malformed_manifest_is_reported(project: Path) -> None:
    use_manifest(project, "orchestrator:\n  id: x\n   bad indent\n")
    drop_markdown_roster(project)
    assert "ARCH_ROSTER_MANIFEST_MALFORMED" in errors(project)


def test_a_roster_file_without_a_yaml_block_is_malformed(project: Path) -> None:
    """La prose est pour le relecteur ; sans bloc ```yaml, il n'y a rien à lire pour G2."""
    use_manifest(project, "# ROSTER: 1-Demo\n\nQuatre agents, on verra les détails plus tard.\n")
    drop_markdown_roster(project)
    assert "ARCH_ROSTER_MANIFEST_MALFORMED" in errors(project)


def test_no_roster_at_all_blocks(project: Path) -> None:
    drop_markdown_roster(project)
    report = va.run(project, mission=1)
    assert "ARCH_ROSTER_MISSING" in {f.cls for f in report.errors}


# ---------------------------------------------------------------------------
# Le registre lui-même
# ---------------------------------------------------------------------------
def test_an_unknown_requirement_key_is_refused(project: Path) -> None:
    """Une clé mal orthographiée désactiverait une exigence sans rien signaler."""
    registry = project / ".sdda/registry/architecture-requirements.yml"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        "orchestration:\n"
        "  section: \"Active Orchestration Pattern\"\n"
        "  choices:\n"
        "    router:\n"
        "      requires:\n"
        "        orchestrator: true\n"
        "        fallbak: true\n",
        encoding="utf-8")
    assert "ARCH_REQUIREMENT_UNKNOWN" in errors(project)


@pytest.mark.parametrize("pattern", [
    "single-agent", "router", "sequential", "parallel", "supervisor",
    "graph", "plan-execute", "reflection", "blackboard", "network",
])
def test_every_orchestration_pattern_has_requirements(project: Path, pattern: str) -> None:
    """Un pattern activable sans exigences serait une porte ouverte silencieuse."""
    report = va.run(project, explain=True)
    registry = va.load_registry(project, report)
    requires, _ = va.requirements_for(registry, "orchestration", pattern)
    assert requires, f"`{pattern}` n'impose aucun champ de spécification"
    assert set(requires) <= va.KNOWN_REQUIREMENTS
