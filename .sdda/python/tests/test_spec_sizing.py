"""La spec choisit dans STACK.md — dimensionnement `micro` et besoins déclarés.

Ce que ces tests tiennent :

1. **La MISSION déclare ses besoins** (`## Architecture Needs`) : G0 refuse une
   MISSION qui ne les déclare pas, ou hors grammaire, ou qui exige ce que
   STACK.md n'autorise pas ; il ne refuse plus une MISSION qui utilise MOINS
   que STACK.md (`rag: none`, `single-agent`).
2. **STACK.md autorise, la spec exige** : une fenêtre de mémoire, un guardrail
   de sortie autorisés par STACK.md n'entrent ni dans l'IR ni dans
   l'architecture effective si la MISSION ne les demande pas.
3. **Dimensionnement** : un chat question/réponse sur un fichier (un agent, deux
   outils de lecture, une console) est `micro` ; `Profile: auto` le résout, et
   `micro` porte des jeux minimaux ; le projet complexe reste `standard` avec la
   liste NOMMÉE de ce qui l'empêche d'être simple.
4. **Le roster** : un agent seul est admis sous un STACK.md `router`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import make_project, run_main
from sdda_lib import effective_architecture as ea
from sdda_lib import spec_needs
from sdda_lib.layered_config import read_layered_config, resolve_profile
from sdda_scripts import ir_compiler, project_profile, roster, validate_architecture, validate_mission
from test_effective_architecture import order_lookup

MISSION = "workspace/pipeline/missions/1-SupportAssistant.md"
MANIFEST = "workspace/stack/STACK.md"

SIMPLE_NEEDS = """## Architecture Needs
- Conversation: multi-turn
- Memory: none
- Documents: none
- Agents: single
- SideEffects: none
- StructuredOutput: no
- PersonalDataRedaction: no

"""


def classes(report) -> set[str]:
    return {f.cls for f in report.findings}


def _needs(project: Path, block: str) -> None:
    path = project / MISSION
    text = path.read_text(encoding="utf-8")
    start = text.index("## Architecture Needs")
    end = text.index("## Required Stack")
    path.write_text(text[:start] + block + text[end:], encoding="utf-8")


def _g0(project: Path):
    return validate_mission.validate_mission_file(project / MISSION, project, write_report=False)


# ---------------------------------------------------------------------------
# 1. G0
# ---------------------------------------------------------------------------
def test_a_mission_without_architecture_needs_is_incomplete(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    path = project / MISSION
    text = path.read_text(encoding="utf-8")
    start, end = text.index("## Architecture Needs"), text.index("## Required Stack")
    path.write_text(text[:start] + text[end:], encoding="utf-8")
    report = _g0(project)
    assert any(f.cls == "MISSION_INCOMPLETE" and "Architecture Needs" in f.message for f in report.errors)


def test_a_need_outside_the_grammar_is_refused(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _needs(project, SIMPLE_NEEDS.replace("Memory: none", "Memory: forever"))
    assert "MISSION_NEEDS_INVALID" in classes(_g0(project))


def test_using_less_than_stack_allows_is_not_a_drift(tmp_path: Path) -> None:
    project = make_project(tmp_path)                                  # STACK.md : router + hybrid
    _needs(project, SIMPLE_NEEDS)
    path = project / MISSION
    text = path.read_text(encoding="utf-8")
    text = text.replace("- orchestration: router", "- orchestration: single-agent").replace("- rag: hybrid", "- rag: none")
    path.write_text(text, encoding="utf-8")
    assert "MISSION_STACK_MISMATCH" not in classes(_g0(project))


def test_a_need_that_stack_does_not_allow_is_a_mismatch(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    text = (project / MANIFEST).read_text(encoding="utf-8")
    (project / MANIFEST).write_text(text.replace(" - .sdda/stacks/rag/hybrid.md", " - .sdda/stacks/rag/none.md"),
                                    encoding="utf-8")
    report = _g0(project)                          # la MISSION de la fixture exige un corpus
    assert any(f.cls == "MISSION_STACK_MISMATCH" and "RAG" in f.message for f in report.errors)


# ---------------------------------------------------------------------------
# 2. STACK.md autorise, la spec exige
# ---------------------------------------------------------------------------
def test_needs_parse_with_minimal_defaults() -> None:
    needs = spec_needs.parse_needs("# M\n\n## Trust Boundaries\n- Untrusted: `user_message`\n\n" + SIMPLE_NEEDS)
    assert needs.declared and needs.multi_turn and not needs.long_term_memory
    assert not needs.documents and not needs.side_effects and not needs.structured_output and needs.untrusted


def test_a_single_turn_text_chat_gets_no_memory_and_no_output_schema_guardrail(tmp_path: Path) -> None:
    project = make_project(tmp_path)                  # STACK.md autorise sliding-window + 2 guardrails
    _needs(project, SIMPLE_NEEDS.replace("Conversation: multi-turn", "Conversation: single-turn"))
    ir, report = ir_compiler.compile_mission(project, 1)
    assert report.ok, report.render_text()
    assert ir["memory"]["shortTermPolicy"] == "none" and "shortTermMaxTurns" not in ir["memory"]
    guards = {g["id"] for point in ("input", "output") for g in ir.get("guardrails", {}).get(point, [])}
    assert guards == {"injection-detection"}, "l'entrée utilisateur est non maîtrisée ; aucune sortie structurée"
    required = ir["architecture"]["required"]
    assert "conversation.session" not in required and "guardrail.schema-validation" not in required
    assert "guardrail.injection-detection" in required and "mission" in ir["architecture"]["derivedFrom"]


def test_a_multi_turn_chat_gets_a_session_not_a_memory_layer(tmp_path: Path) -> None:
    project = order_lookup(tmp_path)
    _needs(project, SIMPLE_NEEDS)
    arch = ea.derive(root=project, mission="1")
    assert arch.requires("conversation.session")
    assert not arch.requires("memory.layer") and not arch.requires("guardrail.schema-validation")
    assert "Conversation: multi-turn" in arch.justified["conversation.session"][0]


def test_a_memory_contract_may_restrict_stack(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    contract = project / "workspace/pipeline/contracts/memory/1-memory.md"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text("# MEMORY\n\n| Clé | Valeur |\n|---|---|\n| ShortTermMaxTurns | 4 |\n"
                        "| CrossAgentSharedState | none |\n", encoding="utf-8")
    report = ir_compiler.Report(name="IR", target="1")
    memory = ir_compiler.compile_memory(project, 1, report)
    assert "MEMORY_CONTRACT_MISMATCH" not in classes(report)
    assert memory["shortTermMaxTurns"] == 4 and memory["crossAgentSharedState"] == "none"


# ---------------------------------------------------------------------------
# 3. Dimensionnement
# ---------------------------------------------------------------------------
def test_a_question_answer_chat_over_one_file_is_micro(tmp_path: Path) -> None:
    project = order_lookup(tmp_path)                 # 1 agent, 2 outils de lecture, JSON, console
    _needs(project, SIMPLE_NEEDS)
    sizing = spec_needs.sizing(project, "1")
    assert sizing.decided and sizing.complexity == "micro", sizing.blockers
    _patch_profile(project, "auto")
    assert resolve_profile(project, "auto") == "micro"
    cfg = read_layered_config(project)
    # La fixture écrit ses tailles de jeux (la couche projet l'emporte) ; ce qu'elle
    # n'écrit pas vient du profil `micro`.
    assert cfg.get("EvalRuns") == 2 and cfg.sources["EvalRuns"] == "profile"
    assert cfg.get("CalibrationSetMinItems") == 10
    code, out = run_main(project_profile.main, ["--root", str(project), "--mission", "1", "--json"])
    data = json.loads(out)["data"]
    assert code == 0 and data["profile"] == "micro" and data["shortPath"] and data["sizing"]["complexity"] == "micro"


def test_the_complex_project_stays_standard_and_says_why(tmp_path: Path) -> None:
    project = make_project(tmp_path)                 # router, RAG, ticket Zendesk
    sizing = spec_needs.sizing(project, "1")
    assert sizing.complexity == "standard"
    joined = " | ".join(sizing.blockers)
    for blocker in ("Agents: multi", "RAG", "SideEffects"):
        assert blocker in joined, joined
    assert resolve_profile(project, "auto") == "standard"


def test_without_declared_needs_nothing_is_shortened(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    path = project / MISSION
    text = path.read_text(encoding="utf-8")
    start, end = text.index("## Architecture Needs"), text.index("## Required Stack")
    path.write_text(text[:start] + text[end:], encoding="utf-8")
    sizing = spec_needs.sizing(project, "1")
    assert not sizing.decided and sizing.complexity == "standard"


def _patch_profile(project: Path, profile: str) -> None:
    path = project / MANIFEST
    text = path.read_text(encoding="utf-8")
    text, n = re.subn(r"^Profile:.*$", f"Profile: {profile}", text, count=1, flags=re.M)
    if not n:
        text = text.replace("## Project Config\n", f"## Project Config\nProfile: {profile}\n", 1)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. Roster
# ---------------------------------------------------------------------------
def test_a_single_agent_roster_is_admitted_under_a_router_stack(tmp_path: Path) -> None:
    project = make_project(tmp_path)                 # STACK.md : router
    manifest = project / "workspace/feats/1-roster.md"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        "# ROSTER: 1\n\n```yaml\nmission: 1\npattern: single-agent\norchestrator:\n  id: support\n"
        "  role: repond aux questions de facturation\n  responsibilities: classer et repondre\n  tier: balanced\n"
        "  model:\n  tools: []\nsubagents: []\nallocation:\n  - cap: 1-1-ClassifyIntent\n    agent: support\n"
        "  - cap: 1-2-ExplainInvoiceLine\n    agent: support\nrelations: []\nloop_bounds: []\nmerge_strategy:\n```\n",
        encoding="utf-8")
    report = roster.validate_manifest(project, 1)
    assert "ARCH_ROSTER_INCOHERENT" not in classes(report), report.render_text()
    assert report.data.get("pattern") == "single-agent"
    assert "ARCH_SPEC_INCOMPLETE" not in classes(validate_architecture.run(project, mission=1))
