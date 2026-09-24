"""Les quatre scripts que les commandes appelaient sans qu'ils existent.

`audit_bypass`, `smoke_check`, `resolve_cap_hash_sentinel`, `diff_code_vs_ir` :
chacun était nommé par une commande (`/sdda-caps`, `/sdda-bootstrap`,
`/sdda-build`) et absent du disque — la commande aurait échoué au moment précis
où elle croyait vérifier quelque chose. Plus `sync_counters`, le mécanisme qui
empêche la prose de mentir sur ses propres chiffres.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_lib import hashing, markdown_io, paths
from sdda_scripts import (
    audit_bypass,
    diff_code_vs_ir,
    ir_compiler,
    resolve_cap_hash_sentinel,
    smoke_check,
    validate_mission,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path)


# ---------------------------------------------------------------------------
# audit_bypass
# ---------------------------------------------------------------------------
def _audit_lines(root: Path) -> list[dict]:
    path = paths.audit_dir(root) / "bypasses.jsonl"
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_a_motivated_bypass_is_journalised_with_command_and_name(project: Path) -> None:
    code, out = run_main(audit_bypass.main, ["--root", str(project), "--command", "/sdda-caps 1",
                                             "--bypass", "CapGranularityHardCap", "--reason", "mission volontairement large, découpe en 3 lots", "--json"])
    assert code == 0, out
    lines = _audit_lines(project)
    assert len(lines) == 1
    entry = lines[0]
    assert entry["command"] == "/sdda-caps 1" and entry["bypass"] == "CapGranularityHardCap"
    assert entry["gate"] == "G1" and entry["reason"].startswith("mission volontairement")
    assert "at" in entry and "operator" in entry


@pytest.mark.parametrize("reason", [None, "", "non renseignée", "<à préciser>", "TODO"])
def test_a_bypass_without_a_real_reason_is_refused_and_nothing_is_written(project: Path, reason) -> None:
    argv = ["--root", str(project), "--command", "/sdda-topology 1", "--bypass", "SDDA_BYPASS_BUDGET_ESTIMATE", "--json"]
    if reason is not None:
        argv += ["--reason", reason]
    code, out = run_main(audit_bypass.main, argv)
    assert code == 1
    assert "BYPASS_REASON_MISSING" in out
    assert _audit_lines(project) == []


def test_the_gate_is_inferred_from_the_bypass_name_or_given(project: Path) -> None:
    assert audit_bypass.gate_for("SDDA_BYPASS_BUDGET_ESTIMATE", None) == "G2"
    assert audit_bypass.gate_for("SDDA_BYPASS_RETRIEVAL_GATE", None) == "G4"
    assert audit_bypass.gate_for("SDDA_ALLOW_UNTESTED_COMBO", None) == "stack"
    assert audit_bypass.gate_for("quelque-chose", None) == "config"
    assert audit_bypass.gate_for("quelque-chose", "G7") == "G7"


def test_the_journal_stays_readable_by_sdda_state(project: Path) -> None:
    """Les quatre champs que `sdda_state.bypasses_of` lit ne sont jamais écrasés par `extra`."""
    run_main(audit_bypass.main, ["--root", str(project), "--command", "c", "--bypass", "b", "--reason", "une vraie raison"])
    from sdda_scripts import sdda_state

    found = sdda_state.bypasses_of(project, {"startedAt": "2000-01-01T00:00:00Z"})
    assert len(found) == 1 and found[0]["gate"] == "config"


# ---------------------------------------------------------------------------
# smoke_check — sur un projet RÉELLEMENT amorcé par bootstrap.py
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def bootstrapped(tmp_path_factory) -> Path:
    """Un projet amorcé comme `bootstrap.py --combo c1` le ferait — avec le VRAI
    `build_stack_md` et la VRAIE arborescence du bootstrap, dans un répertoire
    vierge (le script lui-même écrit à sa racine, il ne prend pas de `--root`).
    La fixture `project_ok` est un projet de test minimal, pas un projet amorcé."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import bootstrap as bs

    from sdda_lib.workspace import write_workspace_version

    root = tmp_path_factory.mktemp("boot")
    for rel in bs.WORKSPACE_TREE:
        (root / "workspace" / rel).mkdir(parents=True, exist_ok=True)
    stack = root / "workspace" / "stack" / "STACK.md"
    stack.write_text(bs.build_stack_md("SmokeProbe", bs.COMBOS["c1"], {}), encoding="utf-8")
    write_workspace_version(root)   # ce que create_workspace() écrit aussi
    return root


@pytest.fixture
def booted(bootstrapped: Path, tmp_path: Path) -> Path:
    """Une copie jetable du projet amorcé, pour les mutations."""
    dst = tmp_path / "boot"
    shutil.copytree(bootstrapped, dst)
    return dst


def test_a_bootstrapped_project_passes_the_smoke(booted: Path) -> None:
    code, out = run_main(smoke_check.main, ["--root", str(booted), "--json"])
    assert code == 0, out
    data = json.loads(out)
    assert data["data"]["stack"]["present"] and data["data"]["missingDirs"] == []
    assert data["data"]["stack"]["sections"]["Active Language & Runtime"] == 1


def _stack(project: Path) -> Path:
    return project / "workspace/stack/STACK.md"


def _replace_active(project: Path, old: str, new: str) -> None:
    import re

    p = _stack(project)
    text = p.read_text(encoding="utf-8")
    pattern = re.compile(r"^(\s*-\s+)" + re.escape(old) + r"\s*$", re.M)
    assert pattern.search(text), f"ligne active `{old}` introuvable dans STACK.md"
    p.write_text(pattern.sub(lambda m: m.group(1) + new, text, count=1), encoding="utf-8")


def test_an_unresolved_template_placeholder_fails(booted: Path) -> None:
    p = _stack(booted)
    p.write_text(p.read_text(encoding="utf-8") + "\nAppName: {{AppName}}\n", encoding="utf-8")
    code, out = run_main(smoke_check.main, ["--root", str(booted), "--json"])
    assert code == 1 and "STACK_PLACEHOLDER_UNRESOLVED" in out


def test_an_active_sheet_missing_from_disk_is_unloadable(booted: Path) -> None:
    _replace_active(booted, ".sdda/stacks/lang/python.md", ".sdda/stacks/lang/cobol.md")
    code, out = run_main(smoke_check.main, ["--root", str(booted), "--json"])
    assert code == 1 and "STACK_COMBO_UNLOADABLE" in out and "cobol" in out


def test_two_active_languages_break_the_cardinality(booted: Path) -> None:
    _replace_active(booted, ".sdda/stacks/lang/python.md", ".sdda/stacks/lang/python.md\n - .sdda/stacks/lang/csharp.md")
    code, out = run_main(smoke_check.main, ["--root", str(booted), "--json"])
    assert code == 1 and "STACK_CARDINALITY_INVALID" in out and "exactement 1" in out


def test_a_missing_section_is_named(booted: Path) -> None:
    p = _stack(booted)
    p.write_text(p.read_text(encoding="utf-8").replace("## Active Reranker", "## Active Rerankers"), encoding="utf-8")
    code, out = run_main(smoke_check.main, ["--root", str(booted), "--json"])
    assert code == 1 and "STACK_SECTION_MISSING" in out and "Active Reranker" in out


def test_a_missing_directory_is_reported(booted: Path) -> None:
    shutil.rmtree(booted / "workspace/pipeline/datasets/holdout")
    code, out = run_main(smoke_check.main, ["--root", str(booted), "--json"])
    assert code == 1 and "WORKSPACE_TREE_INCOMPLETE" in out and "datasets/holdout" in out


def test_a_missing_stack_file_is_the_first_thing_said(booted: Path) -> None:
    _stack(booted).unlink()
    code, out = run_main(smoke_check.main, ["--root", str(booted), "--json"])
    assert code == 1 and "STACK_FILE_MISSING" in out


def test_bootstrap_delegates_its_smoke_to_the_same_script(bootstrapped: Path) -> None:
    """Deux implémentations rendraient deux verdicts : bootstrap.py appelle smoke_check.problems."""
    source = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
    assert "smoke_check.problems(" in source


# ---------------------------------------------------------------------------
# resolve_cap_hash_sentinel
# ---------------------------------------------------------------------------
SENTINEL = "Parent MISSION hash: sha256:COMPUTE_REQUIRED"


def _cap(project: Path) -> Path:
    return project / "workspace/pipeline/caps/1-1-ClassifyIntent.md"


def _plant_sentinel(project: Path, path: Path | None = None) -> None:
    p = path or _cap(project)
    text = p.read_text(encoding="utf-8")
    import re

    p.write_text(re.sub(r"^Parent MISSION hash: .*$", SENTINEL, text, count=1, flags=re.M), encoding="utf-8")


def _expected_hash(project: Path) -> str:
    mission = next(paths.missions_dir(project).glob("1-*.md"))
    return hashing.short(validate_mission.parse_mission(markdown_io.read_text(mission), mission).hash)


def test_the_sentinel_is_replaced_by_the_missions_short_hash(project: Path) -> None:
    _plant_sentinel(project)
    code, out = run_main(resolve_cap_hash_sentinel.main, ["--root", str(project), "--mission", "1", "--json"])
    assert code == 0, out
    assert f"Parent MISSION hash: {_expected_hash(project)}" in _cap(project).read_text(encoding="utf-8")
    assert json.loads(out)["data"]["resolved"] == 1


def test_resolution_is_idempotent_and_leaves_other_hashes_alone(project: Path) -> None:
    other = project / "workspace/pipeline/caps/1-2-ExplainInvoiceLine.md"
    before = other.read_text(encoding="utf-8")
    _plant_sentinel(project)
    assert run_main(resolve_cap_hash_sentinel.main, ["--root", str(project), "--mission", "1"])[0] == 0
    once = _cap(project).read_text(encoding="utf-8")
    code, out = run_main(resolve_cap_hash_sentinel.main, ["--root", str(project), "--mission", "1", "--json"])
    assert code == 0 and json.loads(out)["data"]["resolved"] == 0
    assert _cap(project).read_text(encoding="utf-8") == once
    assert other.read_text(encoding="utf-8") == before      # un hash posé, même périmé, n'est pas touché


def test_check_mode_writes_nothing(project: Path) -> None:
    _plant_sentinel(project)
    code, out = run_main(resolve_cap_hash_sentinel.main, ["--root", str(project), "--mission", "1", "--check"])
    assert code == 0 and "CAP_HASH_PLACEHOLDER" in out
    assert SENTINEL in _cap(project).read_text(encoding="utf-8")


def test_a_missing_mission_is_an_infra_error_exit_3(project: Path) -> None:
    code, out = run_main(resolve_cap_hash_sentinel.main, ["--root", str(project), "--mission", "9", "--json"])
    assert code == 3 and "INFRA_BLOCKED" in out


def test_the_resolved_cap_passes_the_cap_gates_hash_check(project: Path) -> None:
    """Le hash écrit est celui que validate_cap attend — même calcul, pas un second."""
    from sdda_scripts import validate_cap

    _plant_sentinel(project)
    assert run_main(resolve_cap_hash_sentinel.main, ["--root", str(project), "--mission", "1"])[0] == 0
    code, out = run_main(validate_cap.main, ["--root", str(project), "--mission", "1", "--json", "--no-report"])
    assert "CAP_PARENT_HASH_STALE" not in out


def test_crlf_files_keep_their_line_endings(project: Path) -> None:
    p = _cap(project)
    text = p.read_text(encoding="utf-8").replace("\n", "\r\n")
    import re

    text = re.sub(r"^Parent MISSION hash: .*?$", SENTINEL, text, count=1, flags=re.M)
    p.write_bytes(text.encode("utf-8"))
    assert run_main(resolve_cap_hash_sentinel.main, ["--root", str(project), "--mission", "1"])[0] == 0
    raw = p.read_bytes()
    assert b"\r\n" in raw and b"COMPUTE_REQUIRED" not in raw


# ---------------------------------------------------------------------------
# diff_code_vs_ir
# ---------------------------------------------------------------------------
@pytest.fixture
def compiled(project: Path) -> tuple[Path, dict]:
    assert ir_compiler.main(["--root", str(project), "--mission", "1", "--no-report"]) == 0
    return project, ir_compiler.load_ir(paths.ir_path(project, 1))


def _manifest_from(ir: dict, **overrides) -> dict:
    orch = ir["orchestration"]
    manifest = {
        "generatedBy": "orchestration.dump_graph",
        "entryNode": orch["entryNode"],
        "terminalNodes": list(orch["terminalNodes"]),
        "nodes": [{"id": n["id"], "kind": n["kind"]} for n in orch["nodes"]],
        "edges": [{"from": e["from"], "to": e["to"], "condition": e["condition"]} for e in orch["edges"]],
    }
    manifest.update(overrides)
    return manifest


def _write_manifest(root: Path, manifest: dict) -> Path:
    path = root / "workspace/src/orchestration/graph.manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def test_an_isomorphic_graph_passes(compiled) -> None:
    root, ir = compiled
    _write_manifest(root, _manifest_from(ir))
    code, out = run_main(diff_code_vs_ir.main, ["--root", str(root), "--mission", "1", "--scope", "orchestration", "--json"])
    assert code == 0, out
    assert json.loads(out)["data"]["diff"]["isomorphic"] is True


def test_no_manifest_means_nothing_is_comparable(compiled) -> None:
    root, _ = compiled
    code, out = run_main(diff_code_vs_ir.main, ["--root", str(root), "--mission", "1", "--json"])
    assert code == 1 and "ORCH_MANIFEST_MISSING" in out


@pytest.mark.parametrize("mutation", ["drop_edge", "extra_node", "rename_entry", "reword_condition", "drop_terminal"])
def test_every_kind_of_divergence_is_named(compiled, mutation: str) -> None:
    root, ir = compiled
    manifest = _manifest_from(ir)
    if mutation == "drop_edge":
        manifest["edges"] = manifest["edges"][1:]
    elif mutation == "extra_node":
        manifest["nodes"].append({"id": "ghost", "kind": "function"})
    elif mutation == "rename_entry":
        manifest["entryNode"] = "elsewhere"
    elif mutation == "reword_condition":
        manifest["edges"][0]["condition"] = manifest["edges"][0]["condition"] + " and True"
    elif mutation == "drop_terminal":
        manifest["terminalNodes"] = []
    _write_manifest(root, manifest)
    code, out = run_main(diff_code_vs_ir.main, ["--root", str(root), "--mission", "1", "--json"])
    assert code == 1, mutation
    assert {e["class"] for e in json.loads(out)["errors"]} == {"ORCH_DIVERGES_FROM_IR"}, mutation


def test_an_unattributed_manifest_is_a_warning(compiled) -> None:
    root, ir = compiled
    manifest = _manifest_from(ir)
    manifest.pop("generatedBy")
    _write_manifest(root, manifest)
    code, out = run_main(diff_code_vs_ir.main, ["--root", str(root), "--mission", "1", "--json"])
    assert code == 0
    assert "ORCH_MANIFEST_UNATTRIBUTED" in out


def test_an_unknown_scope_is_an_invalid_arg(compiled) -> None:
    root, _ = compiled
    code, out = run_main(diff_code_vs_ir.main, ["--root", str(root), "--mission", "1", "--scope", "tools", "--json"])
    assert code == 1 and "INVALID_ARG" in out


# ---------------------------------------------------------------------------
# sync_error_registry — un indiçage Python n'est pas un marqueur de classe
# ---------------------------------------------------------------------------
def test_a_python_subscript_is_not_mistaken_for_an_error_class() -> None:
    """`attrs[A_BOUND_EXCEEDED]` en avait fait entrer une au registre.

    Une constante d'attribut de trace y devenait une classe d'erreur que rien
    n'émet — exactement le doc-theater que ce registre existe pour empêcher,
    mais dans l'autre sens.
    """
    from sdda_admin.sync_error_registry import BRACKET_RE

    assert BRACKET_RE.findall("if attrs[A_BOUND_EXCEEDED] is None:") == []
    assert BRACKET_RE.findall("out[TOOL_NAME] = x") == []
    assert BRACKET_RE.findall("CAUSE: [BUDGET_PRICING_UNKNOWN] modèle absent") == ["BUDGET_PRICING_UNKNOWN"]
    assert BRACKET_RE.findall("| `[TOOL_SCHEMA_INVALID]` | schéma |") == ["TOOL_SCHEMA_INVALID"]
    assert BRACKET_RE.findall("refuse -> [STACK_LANGUAGE_MISMATCH].") == ["STACK_LANGUAGE_MISMATCH"]


def test_the_registry_holds_no_trace_attribute_constant() -> None:
    """Garde-fou de non-régression sur le registre lui-même."""
    from sdda_admin.sync_error_registry import collect

    assert [c for c in collect() if c.startswith("A_")] == []


# ---------------------------------------------------------------------------
# sync_counters — la prose ne ment pas sur ses chiffres
# ---------------------------------------------------------------------------
def test_sync_counters_check_mode_runs_and_names_its_class() -> None:
    """`--check` est joué par framework_smoke (CI) ; ici on vérifie seulement qu'il
    s'exécute et que, s'il dérive, il le dit avec sa classe et son FIX. Le faire
    ÉCHOUER ici rougirait toute la suite dès qu'un test est ajouté ailleurs (le
    compteur `tests` bouge) — c'est au smoke, pas à pytest, de tenir ce fil."""
    result = subprocess.run(
        [sys.executable, str(ROOT / ".sdda/python/sdda_admin/sync_counters.py"), "--check"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode in (0, 1), result.stdout + result.stderr
    if result.returncode == 1:
        assert "[COUNTER_DRIFT]" in result.stdout and "--write" in result.stdout
    else:
        assert "compteur(s) à jour" in result.stdout


def test_render_rewrites_markers_and_reports_each_drift() -> None:
    sys.path.insert(0, str(ROOT / ".sdda/python"))
    from sdda_admin import sync_counters

    drifts: list[str] = []
    text = ("agents: <!--sdda:count agents-->7<!--/sdda:count-->, "
            "k=<!--sdda:config EvalRuns-->1<!--/sdda:config-->, "
            "graders: <!--sdda:graders-->`x`<!--/sdda:graders-->")
    out = sync_counters.render(text, {"agents": 22}, {"EvalRuns": 3}, "`exact`, `regex`", "", drifts, "t.md")
    assert "<!--sdda:count agents-->22<!--/sdda:count-->" in out
    assert "<!--sdda:config EvalRuns-->3<!--/sdda:config-->" in out
    assert "<!--sdda:graders-->`exact`, `regex`<!--/sdda:graders-->" in out
    assert len(drifts) == 3


def test_facades_carry_no_sync_markers() -> None:
    """Les marqueurs vivent dans la source ; un agent ne paie aucun token pour eux."""
    for facade in (".claude", ".codex", ".gemini"):
        for path in (ROOT / facade).rglob("*"):
            if path.is_file() and path.suffix in (".md", ".toml"):
                assert "<!--sdda:" not in path.read_text(encoding="utf-8", errors="replace"), path


def test_format_config_value_reads_like_the_prose() -> None:
    from sdda_admin import sync_counters

    assert sync_counters.format_config_value(50.0) == "50"
    assert sync_counters.format_config_value(0.6) == "0.6"
    assert sync_counters.format_config_value(3) == "3"
    assert sync_counters.format_config_value(True) == "true"
