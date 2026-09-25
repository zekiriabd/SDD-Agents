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
    # `expected` peut être vide : le pattern `network`, seul composant refusé
    # par défaut, n'a ni fiche ni valeur d'IR — une règle d'ADR à son sujet
    # était inatteignable, et elle est sortie du registre.
    assert declared, "le registre des ADR est vide — validate_adr n'a plus rien à appliquer"
    assert expected <= declared, expected - declared


def test_the_unreachable_network_adr_rule_is_gone() -> None:
    """`network` n'est ni sur disque ni dans l'enum de l'IR : aucun ADR ne le débloque."""
    from sdda_lib import yaml_mini
    from sdda_scripts import validate_adr

    assert not (SDDA / "stacks" / "orchestration" / "network.md").exists()
    ir_schema = json.loads((SDDA / "registry" / "ir.schema.json").read_text(encoding="utf-8-sig"))
    assert "network" not in ir_schema["$defs"]["orchestrationPattern"]["enum"] if "$defs" in ir_schema else True
    assert all("network" not in r.values for r in validate_adr.load_requirements())
    assert "orchestration/network" not in _matrix()["refusedByDefault"]["components"]
    arch = yaml_mini.parse_mapping((SDDA / "registry" / "architecture-requirements.yml").read_text(encoding="utf-8"))
    assert not arch["orchestration"]["choices"]["network"].get("adr_required")


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


def test_a_red_stack_blocks_pipeline_agents_only(tmp_path: Path) -> None:
    """Un STACK.md rouge refusait TOUTE délégation, exploration comprise — jusqu'au
    spawn inoffensif de `hooks-selfcheck`. Seuls les Developer Agents le lisent
    comme une consigne ; le lancement à la main (sans agent) reste jugé."""
    project = strict(make_project(tmp_path))
    assert run_hook_as(project, "po-elicitor")[0] == 2
    assert run_hook(project)[0] == 2
    code, out = run_hook_as(project, "Explore")
    assert code == 0 and "hors pipeline" in out, out


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
# C2 — chaque clé du gabarit a un lecteur déclaré, et ce lecteur la nomme
# ---------------------------------------------------------------------------
_TOP_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)\s*:")
_SUB_KEY_RE = re.compile(r"^  ([A-Z][A-Za-z0-9]*)\s*:")


def template_keys() -> list[tuple[str, str]]:
    """`[(section, clé)]` des clés NON commentées du gabarit ; `Parent.Enfant` pour un mapping."""
    out, section, parent = [], None, None
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section, parent = line[3:].strip(), None
            continue
        if section is None:
            continue
        if (m := _TOP_KEY_RE.match(line)):
            parent = m.group(1)
            out.append((section, parent))
        elif parent and (m := _SUB_KEY_RE.match(line)):
            out.append((section, f"{parent}.{m.group(1)}"))
    return out


def schema_entry(schema: dict, section: str, key: str) -> dict | None:
    props = schema["properties"]
    head, _, leaf = key.partition(".")
    if section == "Project Config" or props.get(head, {}).get("x-section") == section:
        node = props.get(head)
    else:
        node = ((schema["x-stackSections"].get(section) or {}).get("properties") or {}).get(head)
    if node is not None and leaf:
        node = (node.get("properties") or {}).get(leaf)
    return node


def reader_file(reader: str) -> Path:
    kind, _, target = reader.partition(":")
    return {"script": PYTHON_DIR / target, "runtime": SDDA / "templates" / "runtime" / "python" / target,
            "agent": SDDA / "agents" / f"{target}.md", "command": SDDA / "commands" / f"{target}.md"}[kind]


def test_every_template_key_has_a_declared_reader_that_names_it() -> None:
    """`HybridEnabled`, `TlsVerify`, `BaselineStorage`, `TierMap`… étaient écrits et lus par personne.

    Une clé qu'on édite sans effet fait croire à un réglage. Chaque clé du
    gabarit doit donc porter `x-readBy` dans le schéma, et chaque lecteur
    déclaré — script, agent, commande, squelette — doit la NOMMER : un
    lecteur qui ne cite pas la clé ne la lit pas.
    """
    schema = json.loads((SDDA / "templates" / "project-config.schema.json").read_text(encoding="utf-8"))
    problems = []
    for section, key in template_keys():
        entry = schema_entry(schema, section, key)
        if entry is None:
            problems.append(f"{section} > {key} : absent du schéma")
            continue
        readers = entry.get("x-readBy") or []
        if not readers:
            problems.append(f"{section} > {key} : aucun lecteur déclaré (x-readBy)")
        names = {key.rpartition(".")[2], key.partition(".")[0]}
        for reader in readers:
            path = reader_file(reader)
            if not path.is_file():
                problems.append(f"{section} > {key} : lecteur `{reader}` introuvable")
            elif not any(re.search(rf"\b{re.escape(n)}\b", path.read_text(encoding="utf-8")) for n in names):
                problems.append(f"{section} > {key} : `{reader}` ne nomme pas la clé")
    assert not problems, "\n".join(problems)


def schema_properties(schema: dict) -> list[tuple[str, dict]]:
    """`[(chemin, entrée)]` de TOUTES les propriétés du schéma — objets imbriqués et sections compris.

    Le contrôle précédent ne regardait que les clés écrites dans le gabarit :
    une clé du schéma absente du gabarit (les 9 `*Gate`, `BuildModelMode`,
    `CheckpointMode`, `AnswerRelevanceMin`…) pouvait rester déclarée, décrite,
    et lue par personne — pendant qu'ARCHITECTURE §4 affirmait que « les clés
    que personne ne lisait ont été retirées ».
    """
    out: list[tuple[str, dict]] = []

    def walk(props: dict, prefix: str) -> None:
        for name, entry in props.items():
            if not isinstance(entry, dict):
                continue
            path = f"{prefix}{name}"
            out.append((path, entry))
            if isinstance(entry.get("properties"), dict):
                walk(entry["properties"], path + ".")

    walk(schema["properties"], "")
    for section, node in schema["x-stackSections"].items():
        if isinstance(node, dict) and isinstance(node.get("properties"), dict):
            walk(node["properties"], f"{section} > ")
    return out


def _reader_problems(path: str, entry: dict) -> list[str]:
    readers = entry.get("x-readBy") or []
    if not readers:
        return [f"{path} : aucun lecteur déclaré (x-readBy)"]
    dotted = path.rpartition(" > ")[2]
    names = {dotted.rpartition(".")[2], dotted.partition(".")[0]}
    problems = []
    for reader in readers:
        file = reader_file(reader)
        if not file.is_file():
            problems.append(f"{path} : lecteur `{reader}` introuvable")
        elif not any(re.search(rf"\b{re.escape(n)}\b", file.read_text(encoding="utf-8")) for n in names):
            problems.append(f"{path} : `{reader}` ne nomme pas la clé")
    return problems


def test_every_schema_property_has_a_declared_reader_that_names_it() -> None:
    """Récursif : Project Config, objets imbriqués (RuntimeTierMap.*, HybridWeights.*,
    VectorStoreConnection.*) et sections `## Active *`. Une clé sans lecteur est
    retirée, pas documentée."""
    schema = json.loads((SDDA / "templates" / "project-config.schema.json").read_text(encoding="utf-8"))
    entries = schema_properties(schema)
    assert len(entries) > 100, "le parcours récursif a raté des propriétés"
    problems = [p for path, entry in entries for p in _reader_problems(path, entry)]
    assert not problems, "\n".join(problems)


#: Les clés que rien ne lisait — retirées du schéma, de config.base.yml et du gabarit.
DEAD_KEYS = (
    "SystemName", "AnswerRelevanceMin", "AuditorBatchMode", "CostLatencyFailOn", "RagQualityFailOn",
    "MissionGate", "CapGate", "TopologyGate", "ToolGate", "RetrievalGate", "AgentGate", "OrchGate",
    "SafetyGate", "AcceptanceGate", "MaxNestingDepth", "TopologyJustificationRequired",
    "PromptInlineForbidden", "PromptHashPinning", "BuildModelMode", "CheckpointMode",
    "ResumeReusesIdentifiers", "PreserveHumanEdits",
)


def test_dead_keys_are_gone_from_schema_base_and_template() -> None:
    schema = json.loads((SDDA / "templates" / "project-config.schema.json").read_text(encoding="utf-8"))
    base = (SDDA / "config.base.yml").read_text(encoding="utf-8")
    base_keys = set(re.findall(r"^([A-Za-z][A-Za-z0-9_]*)\s*:", base, re.M))
    template = {k for _, k in template_keys()}
    leaked = [k for k in DEAD_KEYS if k in schema["properties"] or k in base_keys or k in template]
    assert not leaked, f"clé(s) mortes encore déclarées : {leaked}"
    protected = schema["properties"]["security_down_protected"]
    assert not set(DEAD_KEYS) & set(protected["default"])
    assert not set(DEAD_KEYS) & set(protected["items"]["enum"])


def test_wired_keys_are_read_by_the_script_that_now_applies_them() -> None:
    """`MaxBypassesPerRun` était codé en dur à 2 ; `LocalCompute*` n'existaient que dans la base."""
    schema = json.loads((SDDA / "templates" / "project-config.schema.json").read_text(encoding="utf-8"))
    props = schema["properties"]
    assert props["MaxBypassesPerRun"]["x-readBy"] == ["script:sdda_scripts/preflight_force_cumul.py"]
    assert "script:sdda_scripts/estimate_budget.py" in props["LocalComputeCostPerHourUsd"]["x-readBy"]
    assert "script:sdda_scripts/estimate_budget.py" in props["LocalComputeThroughputTokensPerSec"]["x-readBy"]
    assert "script:sdda_scripts/validate_safety_gate.py" in props["AgentSafetyRequiredInProduction"]["x-readBy"]


def test_retired_keys_are_gone_from_the_template() -> None:
    keys = {k for _, k in template_keys()}
    assert not keys & {"HybridEnabled", "ParentChildEnabled", "BaselineStorage", "SecretsFile", "TierMap",
                       "Provider", "Endpoint", "SystemName"}


def test_observability_keys_now_mean_something(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), "TraceLevel: full", "TraceLevel: off")
    assert any("TraceRequiredPerRun" in i.message for i in issues(project) if i.blocking)


def test_a_retired_key_that_contradicts_the_fiche_is_drift(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), "ChunkStrategy: recursive-structural",
                          "ChunkStrategy: recursive-structural\nHybridEnabled: false")
    code, out = run_hook(project)
    assert code == 2 and "RETRIEVAL_CONFIG_DRIFT" in out and "HybridEnabled" in out, out


def test_a_dedicated_store_needs_a_collection(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), " - .sdda/stacks/embedding/voyage.md\n",
                          " - .sdda/stacks/embedding/voyage.md\nVectorStoreConnection:\n  Mode: dedicated\n"
                          "  Endpoint: https://idx.internal\n")
    code, out = run_hook(project)
    assert code == 2 and "Collection" in out, out


# ---------------------------------------------------------------------------
# C6 — le code généré utilise le framework déclaré, et lui seul
# ---------------------------------------------------------------------------
def with_app(project: Path, files: dict[str, str]) -> Path:
    app = project / "workspace" / "src" / "SupportAssistant"
    for rel, body in files.items():
        target = app / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return project


def framework_report(project: Path) -> tuple[int, dict]:
    from conftest import run_main
    from sdda_scripts import validate_framework

    code, out = run_main(validate_framework.main, ["--root", str(project), "--json", "--mission", "1"])
    return code, json.loads(out)


CONFORMING = {
    "agents/billing/agent.py": "from langchain_core.messages import AIMessage\n",
    "orchestration/graph.py": "from langgraph.graph import StateGraph\n",
}


def test_code_that_follows_the_declared_frameworks_is_green(tmp_path: Path) -> None:
    project = with_app(make_project(tmp_path), CONFORMING)
    code, payload = framework_report(project)
    assert code == 0, payload
    assert (project / "workspace/.sys/.validation/G6-1.framework.json").is_file()


def test_a_declared_framework_that_the_code_ignores_is_drift(tmp_path: Path) -> None:
    """L'orchestration écrite à la main alors que `langgraph.md` est déclaré."""
    project = with_app(make_project(tmp_path), {**CONFORMING, "orchestration/graph.py": "import anthropic\n"})
    code, payload = framework_report(project)
    assert code == 1 and any("orchestration/" in f["message"] for f in payload["errors"])


def test_an_undeclared_competitor_is_drift_even_in_tests(tmp_path: Path) -> None:
    project = with_app(make_project(tmp_path), {**CONFORMING, "agents/billing/tests/test_x.py": "import crewai\n"})
    code, payload = framework_report(project)
    assert code == 1 and any("crewai" in f["message"] for f in payload["errors"])


def test_create_agent_without_langgraph_declared_is_drift(tmp_path: Path) -> None:
    project = patch_stack(make_project(tmp_path), " - .sdda/stacks/framework/langgraph.md\n", "")
    with_app(project, {"agents/billing/agent.py": "from langchain.agents import create_agent\n"})
    code, payload = framework_report(project)
    assert code == 1 and any("langgraph" in f["message"] for f in payload["errors"])


def test_the_framework_part_is_a_g6_part_whose_red_blocks() -> None:
    from sdda_lib.gate_reports import GATE_PARTS_ADVISORY

    assert "framework" in GATE_PARTS_ADVISORY["G6"] and "adr" in GATE_PARTS_ADVISORY["G2"]


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
