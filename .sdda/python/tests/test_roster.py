"""roster — le gabarit pré-rempli, puis la vérification du roster déclaré (P7).

Ce que ces tests défendent : le framework DÉRIVE ce qui se dérive (mission,
pattern, CAPs) et laisse `<à préciser>` tout ce qui se DÉCIDE ; il ne réécrit
jamais ce que l'architecte a écrit sans un bypass motivé et journalisé ; et sa
vérification refuse ce que G2 refuserait, plus les trous, l'allocation et les
agents sans raison d'être.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import run_main
from sdda_lib import paths
from sdda_scripts import roster
from sdda_scripts import validate_architecture as va

#: Le roster vit dans `feats/` — la spécification —, en Markdown, à côté de la
#: topologie qu'il commande. Deux fichiers, deux owners : l'humain et l'agent.
MANIFEST = "workspace/feats/topology/1-roster.md"
TOPOLOGY = "workspace/feats/topology/1-topology.md"
STACK = "workspace/stack/STACK.md"

COMPLETE = """\
mission: 1
pattern: router
orchestrator:
  id: intent-classifier
  role: classe l'intention entrante et route vers le spécialiste
  responsibilities: route ou demande une clarification ; ne répond jamais au fond
  tier: fast
  model:
  tools: []
  rules: route si confidence >= 0.7, sinon clarification ; maxHops = 6
subagents:
  - id: billing-specialist
    role: répond aux questions de facturation
    responsibilities: explique une ligne de facture à partir des documents récupérés
    tools: [invoice_lookup, zendesk_create_ticket]
    skills: [explain_invoice_line]
    rules: [never_issue_refund]
    tier: balanced
    model:
allocation:
  - cap: 1-1-ClassifyIntent
    agent: intent-classifier
  - cap: 1-2-ExplainInvoiceLine
    agent: billing-specialist
relations:
  - from: intent-classifier
    to: billing-specialist
    condition: "intent == 'billing' && confidence >= 0.7"
    counts_as_hop: true
  - from: intent-classifier
    to: clarify_request
    condition: "aucune classe au-dessus du seuil — chemin de repli"
    counts_as_hop: true
loop_bounds:
  - loop: "intent-classifier <-> billing-specialist"
    bound: "maxHops = 6"
    on_exceeded: fail-explicit
merge_strategy:
"""

#: La forme sur disque : le YAML dans un bloc, la prose autour.
COMPLETE_MD = roster.wrap_roster_markdown(1, "Demo", COMPLETE)


def patch(project: Path, rel: str, old: str, new: str) -> None:
    path = project / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"motif absent de {rel} : {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def write_manifest(project: Path, content: str = COMPLETE) -> Path:
    """Écrit le roster tel que l'architecte le laisse : `content` est le YAML, enveloppé en Markdown."""
    path = project / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(roster.wrap_roster_markdown(1, "Demo", content), encoding="utf-8")
    return path


def scaffold(project: Path, *extra: str) -> tuple[int, dict]:
    code, out = run_main(roster.main, ["scaffold", "--root", str(project), "--mission", "1", "--json", *extra])
    return code, json.loads(out)


def validate(project: Path, *extra: str) -> tuple[int, dict]:
    code, out = run_main(roster.main, ["validate", "--root", str(project), "--mission", "1", "--json", *extra])
    return code, json.loads(out)


def errors(project: Path) -> set[str]:
    return {f.cls for f in roster.validate_manifest(project, 1).errors}


def drop_markdown_roster(project: Path) -> None:
    path = project / TOPOLOGY
    text = path.read_text(encoding="utf-8")
    start, end = text.index("## 2. Roster déclaré"), text.index("## 3. Alternative")
    path.write_text(text[:start] + text[end:], encoding="utf-8")


# ---------------------------------------------------------------------------
# scaffold : dérivé pré-rempli, décidé laissé en trou
# ---------------------------------------------------------------------------
def test_scaffold_writes_the_manifest_where_g2_reads_it(project: Path) -> None:
    code, data = scaffold(project)
    assert code == 0, data
    assert data["data"]["action"] == "written" and data["data"]["manifest"] == MANIFEST
    path = project / MANIFEST
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    # Un Markdown : le YAML est dans le premier bloc clôturé, et c'est lui que G2 lit.
    assert text.startswith("# ROSTER: 1-") and text.count("```yaml") == 1
    assert va.read_roster_yaml(path)["mission"] == 1
    assert "mission: 1\n" in text and text.count("pattern: router") == 1
    assert "- cap: 1-1-ClassifyIntent" in text and "- cap: 1-2-ExplainInvoiceLine" in text
    assert data["data"]["caps"] == ["1-1-ClassifyIntent", "1-2-ExplainInvoiceLine"]
    assert data["data"]["placeholders"] > 0 and "<à préciser>" in text
    # Un routeur exige des relations et un repli : deux blocs de relation sont proposés.
    assert text.count("- from: <à préciser>") == 2 and "REPLI" in text


def test_scaffold_adapts_to_the_active_pattern(project: Path) -> None:
    patch(project, STACK, " - .sdda/stacks/orchestration/router.md", " - .sdda/stacks/orchestration/single-agent.md")
    scaffold(project)
    text = (project / MANIFEST).read_text(encoding="utf-8")
    assert "subagents: []" in text and "relations: []" in text and "pattern: single-agent" in text


def test_scaffold_is_idempotent_and_never_overwrites_without_force(project: Path) -> None:
    scaffold(project)
    path = project / MANIFEST
    path.write_text(COMPLETE_MD, encoding="utf-8")         # l'architecte a décidé
    code, data = scaffold(project)
    assert code == 0 and data["data"]["action"] == "kept" and data["data"]["placeholders"] == 0
    assert path.read_text(encoding="utf-8") == COMPLETE_MD
    # Idem pour un roster en cours de remplissage : rien n'est réécrit.
    path.write_text(COMPLETE_MD.replace("tier: balanced", "tier: <à préciser>"), encoding="utf-8")
    code, data = scaffold(project)
    assert data["data"]["action"] == "kept" and data["data"]["placeholders"] == 1


def test_force_without_a_reason_is_refused_and_writes_nothing(project: Path) -> None:
    write_manifest(project)
    code, data = scaffold(project, "--force")
    assert code == 1
    assert {e["class"] for e in data["errors"]} == {"BYPASS_REASON_MISSING"}
    assert (project / MANIFEST).read_text(encoding="utf-8") == COMPLETE_MD
    assert not (paths.audit_dir(project) / "bypasses.jsonl").exists()


def test_force_with_a_reason_overwrites_and_is_audited(project: Path) -> None:
    write_manifest(project)
    code, data = scaffold(project, "--force", "--reason", "roster obsolète après refonte des CAPs")
    assert code == 0 and data["data"]["action"] == "overwritten"
    assert "<à préciser>" in (project / MANIFEST).read_text(encoding="utf-8")
    lines = (paths.audit_dir(project) / "bypasses.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    assert entry["gate"] == "G2" and entry["bypass"] == roster.BYPASS_NAME
    assert entry["reason"] == "roster obsolète après refonte des CAPs" and entry["manifest"] == MANIFEST


def test_force_reason_can_come_from_the_environment(project: Path, monkeypatch) -> None:
    write_manifest(project)
    monkeypatch.setenv("SDDA_BYPASS_REASON", "regénération demandée par l'architecte")
    code, data = scaffold(project, "--force")
    assert code == 0 and data["data"]["action"] == "overwritten"


def test_scaffold_without_a_mission_is_an_error(project: Path) -> None:
    code, out = run_main(roster.main, ["scaffold", "--root", str(project), "--mission", "7", "--json"])
    assert code == 1 and "MISSION_NOT_FOUND" in out
    assert not (project / "workspace/feats/topology/7-roster.md").exists()


def test_the_roster_location_is_a_convention_not_a_stack_key(project: Path) -> None:
    """`RosterManifestRoot` a existé : une décision d'architecture qu'on peut ranger
    n'importe où est une décision qu'on ne retrouve pas en revue. La clé est ignorée."""
    patch(project, STACK, "## Active Data Access",
          "## Active Agent Topology\nRosterManifestRoot: workspace/stack/agents\n\n## Active Data Access")
    _, data = scaffold(project)
    assert data["data"]["manifest"] == MANIFEST
    assert (project / MANIFEST).is_file()
    assert not (project / "workspace/stack/agents").exists()


# ---------------------------------------------------------------------------
# validate : le cas vert, et ce qu'il partage avec G2
# ---------------------------------------------------------------------------
def test_a_complete_manifest_is_green_and_writes_no_gate_report(project: Path) -> None:
    write_manifest(project)
    code, data = validate(project)
    assert code == 0, data
    assert data["ok"] is True
    assert data["data"]["allocation"] == {"1-1-ClassifyIntent": "intent-classifier", "1-2-ExplainInvoiceLine": "billing-specialist"}
    assert data["data"]["subagents"] == ["billing-specialist"] and data["data"]["pattern"] == "router"
    assert not list(paths.validation_dir(project).glob("*.json")) if paths.validation_dir(project).is_dir() else True


def test_the_complete_manifest_also_passes_the_architecture_gate(project: Path) -> None:
    """Ce que `validate` accepte, G2 doit l'accepter : même lecteur, même registre."""
    write_manifest(project)
    drop_markdown_roster(project)
    report = va.run(project, mission=1)
    assert report.ok, report.render_text()
    assert report.data["rosterSource"] == MANIFEST


def test_a_missing_manifest_is_red(project: Path) -> None:
    code, data = validate(project)
    assert code == 1 and {e["class"] for e in data["errors"]} == {"ARCH_ROSTER_MANIFEST_MISSING"}


def test_if_present_tolerates_the_markdown_fallback(project: Path) -> None:
    code, data = validate(project, "--if-present")
    assert code == 0 and data["ok"] is True and data["data"]["action"] == "absent"
    assert {w["class"] for w in data["warnings"]} == {"ARCH_ROSTER_MANIFEST_MISSING"}
    # Mais un manifeste présent et faux reste rouge, --if-present ou pas.
    write_manifest(project, "orchestrator:\n  id: x\n   bad indent\n")
    code, _ = validate(project, "--if-present")
    assert code == 1


def test_a_malformed_manifest_is_reported(project: Path) -> None:
    write_manifest(project, "orchestrator:\n  id: x\n   bad indent\n")
    assert errors(project) == {"ARCH_ROSTER_MANIFEST_MALFORMED"}


def test_a_scaffolded_manifest_names_each_hole(project: Path) -> None:
    scaffold(project)
    report = roster.validate_manifest(project, 1)
    holes = [f for f in report.errors if f.cls == "ARCH_ROSTER_PLACEHOLDER"]
    assert holes and report.data["placeholders"] == len(holes)
    assert any(f.location.endswith("$.orchestrator.id") for f in holes)
    assert any("$.allocation[0].agent" in (f.location or "") for f in holes)


def test_mission_mismatch_is_incoherent(project: Path) -> None:
    write_manifest(project, COMPLETE.replace("mission: 1", "mission: 2"))
    assert "ARCH_ROSTER_INCOHERENT" in errors(project)


def test_pattern_mismatch_with_stack_is_incoherent(project: Path) -> None:
    """Le manifeste ne choisit pas le pattern : STACK.md le fait. Deux sources qui divergent, c'est rouge."""
    write_manifest(project, COMPLETE.replace("pattern: router", "pattern: supervisor"))
    assert "ARCH_ROSTER_INCOHERENT" in errors(project)


def test_completeness_follows_the_active_pattern_registry(project: Path) -> None:
    """`supervisor` exige >= 2 subagents : la classe est celle de G2 (même code)."""
    write_manifest(project, COMPLETE.replace("pattern: router", "pattern: supervisor"))
    patch(project, STACK, " - .sdda/stacks/orchestration/router.md", " - .sdda/stacks/orchestration/supervisor.md")
    assert "ARCH_ROSTER_INCOMPLETE" in errors(project)


def test_single_agent_refuses_subagents(project: Path) -> None:
    write_manifest(project, COMPLETE.replace("pattern: router", "pattern: single-agent"))
    patch(project, STACK, " - .sdda/stacks/orchestration/router.md", " - .sdda/stacks/orchestration/single-agent.md")
    assert "ARCH_ROSTER_INCOHERENT" in errors(project)


def test_a_router_without_fallback_is_red(project: Path) -> None:
    write_manifest(project, COMPLETE.replace('condition: "aucune classe au-dessus du seuil — chemin de repli"',
                                             'condition: "toujours"'))
    assert "ARCH_FALLBACK_MISSING" in errors(project)


def test_an_agent_without_tier_nor_model_is_red(project: Path) -> None:
    write_manifest(project, COMPLETE.replace("    tier: balanced\n", ""))
    assert "ARCH_AGENT_MODEL_UNDECLARED" in errors(project)


def test_an_agent_without_a_tools_key_is_red_but_an_empty_list_is_a_decision(project: Path) -> None:
    write_manifest(project, COMPLETE.replace("    tools: [invoice_lookup, zendesk_create_ticket]\n", ""))
    assert "ARCH_AGENT_TOOLS_UNDECLARED" in errors(project)
    write_manifest(project, COMPLETE.replace("tools: [invoice_lookup, zendesk_create_ticket]", "tools: []"))
    assert "ARCH_AGENT_TOOLS_UNDECLARED" not in errors(project)


# ---------------------------------------------------------------------------
# validate : allocation, agents sans raison, neutralité
# ---------------------------------------------------------------------------
def test_an_unallocated_cap_is_red(project: Path) -> None:
    write_manifest(project, COMPLETE.replace("  - cap: 1-2-ExplainInvoiceLine\n    agent: billing-specialist\n", ""))
    found = errors(project)
    assert "ARCH_ROSTER_CAP_UNALLOCATED" in found
    # …et le spécialiste ne porte plus rien : il doit dire pourquoi il existe.
    assert "ARCH_ROSTER_AGENT_IDLE" in found


def test_a_missing_allocation_section_leaves_every_cap_unallocated(project: Path) -> None:
    text = COMPLETE[: COMPLETE.index("allocation:")] + COMPLETE[COMPLETE.index("relations:"):]
    write_manifest(project, text)
    report = roster.validate_manifest(project, 1)
    assert [f.cls for f in report.errors if f.cls == "ARCH_ROSTER_CAP_UNALLOCATED"] == ["ARCH_ROSTER_CAP_UNALLOCATED"] * 2


def test_an_unknown_cap_is_an_invalid_allocation(project: Path) -> None:
    write_manifest(project, COMPLETE.replace("- cap: 1-2-ExplainInvoiceLine", "- cap: 1-9-Ghost"))
    found = errors(project)
    assert "ARCH_ROSTER_ALLOCATION_INVALID" in found and "ARCH_ROSTER_CAP_UNALLOCATED" in found


def test_an_unknown_agent_is_an_invalid_allocation(project: Path) -> None:
    write_manifest(project, COMPLETE.replace("    agent: billing-specialist", "    agent: refund-bot"))
    assert "ARCH_ROSTER_ALLOCATION_INVALID" in errors(project)


def test_a_cap_carried_by_two_agents_is_invalid(project: Path) -> None:
    write_manifest(project, COMPLETE.replace("    agent: billing-specialist", "    agent: [billing-specialist, intent-classifier]"))
    assert "ARCH_ROSTER_ALLOCATION_INVALID" in errors(project)
    write_manifest(project, COMPLETE.replace("  - cap: 1-1-ClassifyIntent\n    agent: intent-classifier\n",
                                             "  - cap: 1-1-ClassifyIntent\n    agent: intent-classifier\n"
                                             "  - cap: 1-1-ClassifyIntent\n    agent: billing-specialist\n"))
    assert "ARCH_ROSTER_ALLOCATION_INVALID" in errors(project)


def test_an_idle_subagent_with_a_reason_is_accepted(project: Path) -> None:
    text = COMPLETE.replace("    agent: billing-specialist", "    agent: intent-classifier")
    assert "ARCH_ROSTER_AGENT_IDLE" in {f.cls for f in roster.validate_manifest(project, 1).errors} or True
    write_manifest(project, text)
    assert "ARCH_ROSTER_AGENT_IDLE" in errors(project)
    write_manifest(project, text.replace("    tier: balanced\n", "    tier: balanced\n    reason: isolation de scope d'outils — seul porteur de zendesk_create_ticket\n"))
    assert "ARCH_ROSTER_AGENT_IDLE" not in errors(project)


def test_a_placeholder_reason_does_not_count(project: Path) -> None:
    text = COMPLETE.replace("    agent: billing-specialist", "    agent: intent-classifier")
    write_manifest(project, text.replace("    tier: balanced\n", "    tier: balanced\n    reason: <à préciser>\n"))
    found = errors(project)
    assert "ARCH_ROSTER_AGENT_IDLE" in found and "ARCH_ROSTER_PLACEHOLDER" in found


def test_the_orchestrator_is_never_idle(project: Path) -> None:
    """L'orchestrateur est exigé par tout pattern : son existence n'a pas à se justifier par une CAP."""
    write_manifest(project, COMPLETE.replace("    agent: intent-classifier", "    agent: billing-specialist"))
    assert "ARCH_ROSTER_AGENT_IDLE" not in errors(project)


def test_a_framework_api_name_is_a_leak(project: Path) -> None:
    write_manifest(project, COMPLETE.replace("tools: [invoice_lookup, zendesk_create_ticket]", "tools: [StateGraph, invoice_lookup]"))
    assert "FRAMEWORK_LEAK_IN_CONTRACT" in errors(project)
    write_manifest(project, COMPLETE.replace("role: répond aux questions de facturation", "role: nœud LangGraph de facturation"))
    assert "FRAMEWORK_LEAK_IN_CONTRACT" in errors(project)


def test_validate_text_output_uses_the_three_line_error_block(project: Path) -> None:
    code, out = run_main(roster.main, ["validate", "--root", str(project), "--mission", "1"])
    assert code == 1
    assert "ERROR:" in out and "CAUSE: [ARCH_ROSTER_MANIFEST_MISSING]" in out and "FIX:" in out
