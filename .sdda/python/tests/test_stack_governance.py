"""Gouvernance par STACK.md : le gabarit dit vrai, et ce qu'il déclare est lu.

Ces tests gardent le lot « configuration » : un gabarit qui décrit un `.env`
à trois endroits différents, un registre qui compte des fiches qui n'existent
plus, une clé qui n'a aucun lecteur — chacun est une phrase que l'utilisateur
croit, et que le code ne tient pas.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from conftest import make_project  # noqa: F401,E402

PYTHON_DIR = Path(__file__).resolve().parents[1]
SDDA = PYTHON_DIR.parent
ROOT = SDDA.parent
TEMPLATE = SDDA / "templates" / "STACK.md.template"


# ---------------------------------------------------------------------------
# C7 — le gabarit parle du workspace v6, et d'un seul `.env`
# ---------------------------------------------------------------------------
#: Chemins de la v5 : `feats/` rangeait topologie et contrats, `proof/` la
#: vérité terrain et les baselines. Un gabarit qui les cite fait écrire
#: l'utilisateur là où plus rien ne lit.
V5_PATHS = ("proof/", "feats/topology", "feats/contracts", "proof/baselines")


@pytest.mark.parametrize("path", [TEMPLATE, ROOT / "bootstrap.py"], ids=["template", "bootstrap"])
def test_no_v5_path_survives(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    found = [p for p in V5_PATHS if p in text]
    assert not found, f"{path.name} cite encore des chemins v5 : {found}"


def test_the_env_lives_in_assets_and_is_copied_by_install_env() -> None:
    """ARCHITECTURE §2.ter : `assets/.env`, copié par `install-env`, jamais « à la racine »."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "racine du projet" not in text, "le `.env` n'est pas à la racine du projet (§2.ter)"
    assert "workspace/assets/.env" in text
    assert "install-env" in text


def test_the_settings_layer_belongs_to_dev_backend() -> None:
    """La couche Settings vit dans `app/`, la coquille de `dev-backend` — pas dans `serving/`."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "`dev-api` les projette" not in text
    assert "`dev-backend` les projette" in text


def test_the_template_counts_four_human_inputs() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "trois fichiers" not in text
    for depot in ("`stack/`", "`feats/`", "`assets/`", "`seed/`"):
        assert depot in text, f"{depot} manque au décompte des entrées humaines"


# ---------------------------------------------------------------------------
# C8 — la matrice compte ce que le disque porte, et le bootstrap la lit
# ---------------------------------------------------------------------------
MATRIX = SDDA / "registry" / "compatibility.matrix.json"
_LANGUAGES_RE = re.compile(r"^Languages:\s*(.+)$", re.M)


def _matrix() -> dict:
    return json.loads(MATRIX.read_text(encoding="utf-8-sig"))


def _fiches_by_language() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in sorted((SDDA / "stacks").rglob("*.md")):
        match = _LANGUAGES_RE.search(path.read_text(encoding="utf-8-sig"))
        rel = path.relative_to(SDDA / "stacks").as_posix().removesuffix(".md")
        for lang in ([t.strip() for t in match.group(1).split(",")] if match else ["?"]):
            out.setdefault("neutral" if lang == "*" else lang, []).append(rel)
    return out


def test_language_coverage_is_the_disk_not_a_prose_counter() -> None:
    """La prose disait 13 fiches Python et 4 C# quand le disque en portait 15 et 5."""
    declared = {k: sorted(v["fiches"]) for k, v in _matrix()["languageCoupling"]["coverage"].items()
                if isinstance(v, dict)}
    assert declared == {k: sorted(v) for k, v in _fiches_by_language().items()}


#: Champ de combo -> répertoire de `stacks/` où sa fiche doit exister.
COMBO_FIELD_DIRS = {
    "language": "lang", "framework": "framework", "orchestration": "orchestration", "rag": "rag",
    "vectorstore": "vectorstore", "embedding": "embedding", "rerank": "rerank", "dataaccess": "dataaccess",
    "tools": "tools", "memory": "memory", "eval": "eval", "observability": "observability",
    "serving": "serving", "archi": "archi", "backend": "backend", "guardrails": "guardrails",
}
#: `none` n'a de fiche que là où l'absence est une décision documentée.
NONE_HAS_A_FICHE = {"rag", "rerank", "dataaccess"}


@pytest.mark.parametrize("combo", _matrix()["combos"], ids=lambda c: c["id"])
def test_every_combo_component_has_a_fiche(combo: dict) -> None:
    """`repository-tools` figurait dans C1 parmi les OUTILS, sans fiche et dans la mauvaise catégorie."""
    missing = []
    for field_name, directory in COMBO_FIELD_DIRS.items():
        values = combo.get(field_name)
        for value in (values if isinstance(values, list) else [values]):
            if value in (None, "") or (value == "none" and field_name not in NONE_HAS_A_FICHE):
                continue
            if not (SDDA / "stacks" / directory / f"{value}.md").is_file():
                missing.append(f"{directory}/{value}")
    assert not missing, f"combo {combo['id']} : composants sans fiche {missing}"


def test_bootstrap_offers_exactly_the_matrix_combos() -> None:
    """Une seule définition : le menu du bootstrap EST la matrice."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import bootstrap as bs

    expected = {c["bootstrapId"] for c in _matrix()["combos"] if c.get("bootstrapId")}
    assert set(bs.COMBOS) == expected
    c1 = next(c for c in _matrix()["combos"] if c["id"] == "C1")
    assert bs.COMBOS["c1"].orchestration == c1["orchestration"][0]
    assert bs.COMBOS["c1"].tools == c1["tools"]


# ---------------------------------------------------------------------------
# C1 — les VALEURS sont validées, pas seulement les noms
# ---------------------------------------------------------------------------
import subprocess  # noqa: E402

HOOK = PYTHON_DIR / "sdda_hooks" / "preflight_stack_combo.py"
STACK_REL = Path("workspace") / "stack" / "STACK.md"


def patch_stack(project: Path, old: str, new: str) -> Path:
    stack = project / STACK_REL
    text = stack.read_text(encoding="utf-8")
    assert old in text, f"ancre absente de la fixture : {old!r}"
    stack.write_text(text.replace(old, new, 1), encoding="utf-8")
    return project


def issues(project: Path) -> list:
    from sdda_lib.layered_config import validate_config

    return validate_config(project)


def blocking_classes(project: Path) -> set[str]:
    return {i.cls for i in issues(project) if i.blocking}


def run_hook(project: Path, *env: tuple[str, str]) -> tuple[int, str]:
    import os

    environment = {k: v for k, v in os.environ.items() if not k.startswith("SDDA_")}
    environment.update(dict(env))
    proc = subprocess.run([sys.executable, str(HOOK), "--root", str(project)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=environment, stdin=subprocess.DEVNULL, cwd=project)
    return proc.returncode, proc.stdout + proc.stderr


def test_the_fixture_and_the_base_layer_are_valid(tmp_path: Path) -> None:
    assert not [i for i in issues(make_project(tmp_path)) if i.blocking]


def test_an_enum_value_out_of_domain_is_blocking(tmp_path: Path) -> None:
    """`OnBoundExceeded: foo` passait : seuls les NOMS de clés étaient contrôlés."""
    project = patch_stack(make_project(tmp_path), "AdversarialSetMinItems: 2\n",
                          "AdversarialSetMinItems: 2\nOnBoundExceeded: foo\n")
    found = [i for i in issues(project) if i.cls == "CONFIG_VALUE_INVALID"]
    assert found and "OnBoundExceeded" in found[0].message and found[0].blocking


def test_a_section_value_out_of_domain_is_blocking_at_preflight(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), "CitationMode: required", "CitationMode: requried")
    code, out = run_hook(project)
    assert code == 2, out
    assert "CONFIG_VALUE_INVALID" in out and "CitationMode" in out


def test_the_smoke_check_reports_it_too(tmp_path: Path) -> None:
    from sdda_scripts import smoke_check

    project = patch_stack(make_project(tmp_path), "ShortTermMaxTurns: 12", "ShortTermMaxTurns: douze")
    report = smoke_check.run(project)
    assert report.has("CONFIG_VALUE_INVALID")


def test_cross_key_constraints_announced_by_the_schema_are_enforced(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), "AdversarialSetMinItems: 2\n",
                          "AdversarialSetMinItems: 2\nCostPerRunTargetUsd: 0.5\nCostPerRunHardCapUsd: 0.1\n")
    assert any("CostPerRunHardCapUsd" in i.message for i in issues(project) if i.cls == "CONFIG_VALUE_INVALID")


def test_env_declarations_no_longer_blind_the_data_access_section(tmp_path: Path) -> None:
    """` - DB_HOST: ${DB_HOST}` rendait `## Active Data Access` illisible — donc `{}`."""
    from sdda_lib.layered_config import read_stack_section_kv

    project = patch_stack(make_project(tmp_path), "DatabaseType: none\n",
                          "DatabaseType: PostgreSql\n - DB_HOST: ${DB_HOST}\n - DB_PASSWORD: ${DB_PASSWORD}\n"
                          "DbAgentRole: readonly\n")
    values = read_stack_section_kv(project, "Active Data Access")
    assert values.get("DbAgentRole") == "readonly" and values.get("DatabaseType") == "PostgreSql"


def test_a_minimum_written_in_its_section_is_the_one_applied(tmp_path: Path) -> None:
    """`GoldenSetMinItems` sous `## Active Eval Stack` n'était lu par personne."""
    from sdda_lib.layered_config import read_layered_config

    project = patch_stack(make_project(tmp_path), "GoldenSetMinItems: 5\n", "")
    patch_stack(project, " - .sdda/stacks/eval/pytest-eval.md\n",
                " - .sdda/stacks/eval/pytest-eval.md\nGoldenSetMinItems: 77\n")
    assert read_layered_config(project).get("GoldenSetMinItems") == 77


def test_a_key_written_twice_with_two_values_is_a_conflict(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), "GoldenSetMinItems: 5\n", "GoldenSetMinItems: 5\n")
    patch_stack(project, " - .sdda/stacks/eval/pytest-eval.md\n",
                " - .sdda/stacks/eval/pytest-eval.md\nGoldenSetMinItems: 50\n")
    assert "CONFIG_KEY_CONFLICT" in blocking_classes(project)


def test_a_project_key_in_the_wrong_section_is_said(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), "OnGuardrailTrip: block-and-log\n",
                          "OnGuardrailTrip: block-and-log\nMaxIterations: 99\n")
    found = [i for i in issues(project) if i.cls == "CONFIG_KEY_MISPLACED"]
    assert found and found[0].blocking, "99 n'est pas la valeur appliquée (12) : réglage ignoré en silence"


# ---------------------------------------------------------------------------
# C4 — le juge n'est pas le modèle qu'il note
# ---------------------------------------------------------------------------
def judge(project: Path) -> list:
    from sdda_lib.layered_config import judge_issues

    return judge_issues(project)


def with_ir(project: Path, *tiers: str) -> Path:
    ir_dir = project / "workspace" / ".sys" / ".ir"
    ir_dir.mkdir(parents=True, exist_ok=True)
    agents = [{"id": f"a{i}", "modelTier": t} for i, t in enumerate(tiers)]
    (ir_dir / "1-system.ir.json").write_text(json.dumps({"agents": agents}), encoding="utf-8")
    return project


def test_a_judge_that_is_one_tier_among_others_is_said_before_the_ir(tmp_path: Path) -> None:
    """Fixture : `JudgeModel: claude-sonnet-5` = `balanced`, sans IR — on ne sait pas encore."""
    found = judge(make_project(tmp_path))
    assert [i.cls for i in found] == ["JUDGE_SAME_AS_EVALUATED"] and not found[0].blocking


def test_a_judge_that_every_tier_resolves_to_is_blocking_without_ir(tmp_path: Path) -> None:
    """Tous les tiers sur un seul modèle, juge compris : la réponse est déjà connue."""
    project = patch_stack(make_project(tmp_path), "  deep: claude-opus-5\n  balanced: claude-sonnet-5\n  fast: claude-haiku-4-5\n",
                          "  deep: claude-sonnet-5\n  balanced: claude-sonnet-5\n  fast: claude-sonnet-5\n")
    code, out = run_hook(project)
    assert code == 2 and "JUDGE_SAME_AS_EVALUATED" in out, out


def test_the_ir_decides_which_models_are_evaluated(tmp_path: Path) -> None:
    assert [i.blocking for i in judge(with_ir(make_project(tmp_path), "fast", "balanced"))] == [True]
    assert judge(with_ir(make_project(tmp_path / "b"), "fast", "deep")) == []


def test_a_tier_name_as_judge_is_resolved(tmp_path: Path) -> None:
    project = patch_stack(with_ir(make_project(tmp_path), "deep"), "JudgeModel: claude-sonnet-5", "JudgeModel: deep")
    assert [i.blocking for i in judge(project)] == [True]


# ---------------------------------------------------------------------------
# C5 — les ADR : un registre déclaratif, une couverture structurée, une part de G2
# ---------------------------------------------------------------------------
def adr_report(project: Path, *argv: str) -> tuple[int, dict]:
    from conftest import run_main
    from sdda_scripts import validate_adr

    code, out = run_main(validate_adr.main, ["--root", str(project), "--json", "--mission", "1", *argv])
    return code, json.loads(out)


def classes_of(payload: dict) -> list[str]:
    return [f["class"] for f in payload.get("errors", [])]


def write_adr(project: Path, name: str, body: str) -> None:
    d = project / "workspace" / "pipeline" / "decisions"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")


def with_db_write(project: Path) -> Path:
    return patch_stack(project, "DatabaseType: none\n",
                       "DatabaseType: PostgreSql\n - DB_PASSWORD: ${DB_PASSWORD}\nDbAgentRole: scoped-write\n")


def test_a_default_stack_needs_no_adr_and_writes_a_green_g2_part(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    code, payload = adr_report(project)
    assert code == 0 and classes_of(payload) == []
    assert (project / "workspace/.sys/.validation/G2-1.adr.json").is_file()


def test_a_write_role_without_adr_is_blocking(tmp_path: Path) -> None:
    code, payload = adr_report(with_db_write(make_project(tmp_path)))
    assert code == 1 and classes_of(payload) == ["ADR_MISSING"]


def test_a_structured_accepted_adr_covers_it(tmp_path: Path) -> None:
    project = with_db_write(make_project(tmp_path))
    write_adr(project, "ADR-20260924T1000-db-write.md", "Status: Accepted\nCovers: DbAgentRole=scoped-write\n")
    assert adr_report(project)[0] == 0


def test_a_proposed_adr_does_not_unlock_anything(tmp_path: Path) -> None:
    project = with_db_write(make_project(tmp_path))
    write_adr(project, "ADR-20260924T1000-db-write.md", "Status: Proposed\nCovers: DbAgentRole=scoped-write\n")
    assert classes_of(adr_report(project)[1]) == ["ADR_NOT_ACCEPTED"]


def test_a_substring_no_longer_covers_a_decision(tmp_path: Path) -> None:
    """« false » n'importe où couvrait `ApiContractFirst: false` — et toute autre décision booléenne."""
    project = patch_stack(make_project(tmp_path), "AdversarialSetMinItems: 2\n",
                          "AdversarialSetMinItems: 2\nApiContractFirst: false\n")
    write_adr(project, "ADR-0001-x.md", "Status: Accepted\nCovers: TracePIIPolicy=raw\n\nApiContractFirst false, bien sûr.\n")
    assert classes_of(adr_report(project)[1]) == ["ADR_MISSING"]


def test_tls_off_on_a_dedicated_store_requires_an_adr(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), " - .sdda/stacks/embedding/voyage.md\n",
                          " - .sdda/stacks/embedding/voyage.md\nVectorStoreConnection:\n  Mode: dedicated\n"
                          "  Endpoint: https://idx.internal\n  Collection: kb\n  TlsVerify: false\n")
    _, payload = adr_report(project, "--no-report")
    assert any("VectorStoreConnection.TlsVerify" in f["message"] for f in payload["errors"])


def test_every_adr_required_rule_of_the_other_registries_is_in_the_adr_registry() -> None:
    """Un seul registre : ce que `architecture-requirements.yml` et la matrice disent exiger un ADR y figure."""
    from sdda_lib import yaml_mini
    from sdda_scripts import validate_adr

    declared = {(r.key, v) for r in validate_adr.load_requirements() for v in r.values}
    arch = yaml_mini.parse_mapping((SDDA / "registry" / "architecture-requirements.yml").read_text(encoding="utf-8"))
    expected = set()
    for axis, entries in arch.items():
        if not isinstance(entries, dict) or not isinstance(entries.get("choices"), dict):
            continue
        for name, entry in entries["choices"].items():
            if isinstance(entry, dict) and entry.get("adr_required"):
                expected.add((axis, name))
    for component in _matrix()["refusedByDefault"]["components"]:
        expected.add(tuple(component.split("/", 1)))
    assert expected and expected <= declared, expected - declared


# ---------------------------------------------------------------------------
# C3 — la combinaison est reconnue, et le refus par défaut a besoin d'un ADR
# ---------------------------------------------------------------------------
def strict(project: Path) -> Path:
    return patch_stack(project, "StackComboCheck: warn", "StackComboCheck: strict")


def run_hook_as(project: Path, agent: str, *env: tuple[str, str]) -> tuple[int, str]:
    import os

    environment = {k: v for k, v in os.environ.items() if not k.startswith("SDDA_")}
    environment.update(dict(env))
    proc = subprocess.run([sys.executable, str(HOOK), "--root", str(project), "--agent", agent],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=environment, stdin=subprocess.DEVNULL, cwd=project)
    return proc.returncode, proc.stdout + proc.stderr


def test_an_unlisted_combination_is_refused_in_strict_mode(tmp_path: Path) -> None:
    """La matrice annonçait « bloque toute combinaison absente » ; le hook ne lisait pas `combos`."""
    code, out = run_hook(strict(make_project(tmp_path)))
    assert code == 2 and "STACK_COMBO_UNLISTED" in out, out
    assert "dataaccess=none" in out, "le refus nomme l'écart à la combo la plus proche"


def test_warn_says_it_and_the_bypass_assumes_it(tmp_path: Path) -> None:
    code, out = run_hook(make_project(tmp_path))
    assert code == 0 and "hors matrice" in out
    code, out = run_hook(strict(make_project(tmp_path / "b")), ("SDDA_ALLOW_UNTESTED_COMBO", "1"))
    assert code == 0 and "SDDA_ALLOW_UNTESTED_COMBO" in out


def test_a_listed_combination_is_recognised_by_name(tmp_path: Path) -> None:
    project = strict(make_project(tmp_path))
    patch_stack(project, " - .sdda/stacks/dataaccess/none.md\n", " - .sdda/stacks/dataaccess/view-per-agent.md\n")
    code, out = run_hook(project)
    assert code == 0 and "combo C1 reconnue" in out, out


def test_the_signature_follows_the_matrix_semantics() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("psc", HOOK)
    psc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(psc)
    c1 = next(c for c in _matrix()["combos"] if c["id"] == "C1")
    sig = {"language": "python", "framework": ["langgraph", "langchain"], "orchestration": "sequential",
           "rag": "hybrid", "vectorstore": "pgvector", "rerank": "none", "dataaccess": "view-per-agent",
           "serving": "cli", "provider": "anthropic"}
    fields = _matrix()["comboSignatureFields"]["fields"]
    assert psc.mismatches(c1, sig, fields) == [], "framework = ensemble, orchestration = patterns admis"
    assert psc.mismatches(c1, {**sig, "framework": ["langchain"]}, fields), "LangChain seul est une autre stack"


def test_a_refused_by_default_decision_blocks_builders_until_an_adr_is_accepted(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), "MemoryPIIPolicy: redact-before-write", "MemoryPIIPolicy: allow")
    code, out = run_hook_as(project, "dev-agent")
    assert code == 2 and "ADR_MISSING" in out and "MemoryPIIPolicy" in out, out
    code, _ = run_hook_as(project, "architect-memory")
    assert code == 0, "l'architecte est celui qui rédige l'ADR : le bloquer le rendrait impossible"
    write_adr(project, "ADR-0001-memory.md", "Status: Accepted\nCovers: MemoryPIIPolicy=allow\n")
    assert run_hook_as(project, "dev-agent")[0] == 0


# ---------------------------------------------------------------------------
# C9 — ce que le parseur accepte et que rien n'implémente est refusé, pas avalé
# ---------------------------------------------------------------------------
def test_long_term_memory_is_refused_for_lack_of_an_implementation(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), "LongTermEnabled: false", "LongTermEnabled: true")
    code, out = run_hook(project)
    assert code == 2 and "STACK_VALUE_UNIMPLEMENTED" in out and "long terme" in out, out


def test_a_guardrail_named_without_its_fiche_is_refused(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), "OutputGuardrails: [schema-validation]",
                          "OutputGuardrails: [schema-validation, llm-judge]")
    code, out = run_hook(project)
    assert code == 2 and "llm-judge" in out, out


def test_a_remote_store_or_connector_has_no_runtime_client(tmp_path: Path) -> None:
    from conftest import make_project as mk

    project = mk(tmp_path, "project_declared_sources")
    stack = project / STACK_REL
    text = stack.read_text(encoding="utf-8")
    assert "connector: file" in text
    stack.write_text(text.replace("connector: file", "connector: http-api", 1), encoding="utf-8")
    code, out = run_hook(project, ("SDDA_ALLOW_UNTESTED_COMBO", ""))
    assert code == 2 and "http-api" in out, out
    code, out = run_hook(project, ("SDDA_ALLOW_UNTESTED_COMBO", "1"))
    assert code == 0 and "assumé" in out


def test_human_in_the_loop_without_checkpointing_is_refused(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), " - .sdda/stacks/framework/langgraph.md\n", "")
    patch_stack(project, "HumanInTheLoopEnabled: false", "HumanInTheLoopEnabled: true")
    code, out = run_hook(project)
    assert code == 2 and "HumanInTheLoopEnabled" in out, out


def test_the_rule_can_be_lifted_explicitly(tmp_path: Path) -> None:
    project = patch_stack(with_ir(make_project(tmp_path), "balanced"), "AdversarialSetMinItems: 2\n",
                          "AdversarialSetMinItems: 2\nJudgeMustDifferFromEvaluated: false\n")
    assert judge(project) == []
