"""Générateur du squelette applicatif — déterminisme, refus, et périmètre.

Trois propriétés qui ne tiennent pas toutes seules :

1. **Il est idempotent.** Deux exécutions produisent les mêmes octets, sinon
   `--check` crie en CI à chaque passage et on cesse de le lire.
2. **Il refuse plutôt qu'il n'adapte.** Un langage qui n'est pas Python, un
   `AppName` non résolu, un livrable incohérent : dans les trois cas rien n'est
   écrit, et la classe dit laquelle des trois.
3. **Il ne marche pas sur les plates-bandes du voisin.** `gen_source_tools.py`
   copiait tout `templates/runtime/python/` : depuis que le squelette y vit, ce
   `rglob` livrerait une application entière à un projet qui n'a demandé que ses
   outils de données. Le test le fixe dans les deux sens — `data/` et `tools/`
   restent émis, `app/` ne l'est pas.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from conftest import make_project, run_main
from sdda_lib import markdown_io
from sdda_lib.errors import Report
from sdda_scripts import gen_app_skeleton as gas
from sdda_scripts import gen_source_tools as gst

APP = "SupportAssistant"
SRC = f"workspace/src/{APP}"   # layout plat : le projet EST le paquet


def classes(report: Report) -> set[str]:
    return {f.cls for f in report.findings}


@pytest.fixture
def written(project: Path) -> Path:
    report = gas.run(project, mode="write")
    assert report.ok, report.render_text()
    return project


# ---------------------------------------------------------------------------
# Génération
# ---------------------------------------------------------------------------
def test_check_before_generation_reports_the_whole_skeleton(project: Path) -> None:
    report = gas.run(project, mode="check")
    assert "APP_SKELETON_STALE" in classes(report)
    assert not report.ok
    assert report.data["missing"], "aucun fichier manquant signalé sur un projet vierge"


def test_write_puts_the_human_env_into_the_project_it_creates(project: Path) -> None:
    """L'humain dépose `assets/.env` ; le script qui crée le projet l'y pose — aucune commande à part."""
    secret = "sk-test-NEVER-PRINTED"
    source = project / "workspace/assets/.env"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(f"LLM_API_KEY={secret}\n", encoding="utf-8")
    report = gas.run(project, mode="write")
    assert report.ok, report.render_text()
    target = project / SRC / ".env"
    assert target.read_bytes() == source.read_bytes()
    assert report.data["env"]["copied"] is True
    assert secret not in json.dumps(report.data) and secret not in report.render_text()


def test_write_without_assets_env_warns_and_still_builds(project: Path) -> None:
    report = gas.run(project, mode="write")
    assert report.ok, report.render_text()
    assert "SECRET_FILE_MISSING" in {f.cls for f in report.warnings}
    assert not (project / SRC / ".env").exists()


def test_write_then_check_is_green(written: Path) -> None:
    again = gas.run(written, mode="check")
    assert again.ok, again.render_text()


def test_generation_is_byte_identical_on_rerun(written: Path) -> None:
    before = {p.name: p.read_bytes() for p in (written / SRC).rglob("*")if p.is_file()}
    second = gas.run(written, mode="write")
    after = {p.name: p.read_bytes() for p in (written / SRC).rglob("*") if p.is_file()}
    assert before == after
    assert second.data["written"] == []


def test_every_emitted_module_parses(written: Path) -> None:
    files = sorted((written / SRC).rglob("*.py"))
    assert len(files) >= 12
    for path in files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_a_hand_edit_is_reported_as_drift(written: Path) -> None:
    """Le squelette est du code invariant : une retouche locale doit se voir."""
    target = written / SRC / "bounds.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# retouche locale\n",
                      encoding="utf-8")
    report = gas.run(written, mode="check")
    assert "APP_SKELETON_STALE" in classes(report)
    assert any("bounds.py" in d for d in report.data["drifted"])


# ---------------------------------------------------------------------------
# Ce que le rendu porte
# ---------------------------------------------------------------------------
def test_app_config_is_resolved_and_carries_no_secret(written: Path) -> None:
    payload = json.loads((written / SRC / "app_config.json").read_text(encoding="utf-8"))
    assert payload["appName"] == APP
    # La carte tier -> modèle vient de `## Runtime Models`, jamais du code.
    assert payload["tierMap"]["balanced"] == "claude-sonnet-5"
    # Le NOM de la variable voyage, la valeur non.
    assert payload["secretEnv"]["llmApiKey"] == "ANTHROPIC_API_KEY"
    assert "sk-" not in json.dumps(payload)
    # Sans tarif, un coût recalculé vaudrait zéro — et zéro passe sous tous
    # les plafonds.
    assert payload["pricing"]["claude-sonnet-5"]["input"] > 0


def test_pyproject_declares_the_console_entry_point(written: Path) -> None:
    """`cli-exe` n'est un livrable que si le paquet est réellement installable."""
    text = (written / f"workspace/src/{APP}/pyproject.toml").read_text(encoding="utf-8")
    assert f'{APP} = "{APP}.serving.cli:main"' in text
    assert "[project.scripts]" in text


# ---------------------------------------------------------------------------
# Dépendances : dérivées des `.libs.json` actifs, épinglées, déterministes
# ---------------------------------------------------------------------------
def _catalog_versions(name: str) -> dict[str, str]:
    from sdda_lib import paths as _paths
    return json.loads((_paths.FRAMEWORK_SDDA_DIR / f"stacks/{name}.libs.json").read_text(encoding="utf-8"))["versions"]


def test_pyproject_pins_the_active_catalogs_and_the_runtime_sdk(written: Path) -> None:
    """`dependencies = []` rendait vert un projet qui ne s'installait pas."""
    text = (written / SRC / "pyproject.toml").read_text(encoding="utf-8")
    runtime = gas._declared_dependencies(text)
    lc = _catalog_versions("framework/langchain")
    assert f"langchain-core=={lc['langchain-core']}" in runtime            # core d'une fiche active
    assert f"langchain-anthropic=={lc['langchain-anthropic']}" in runtime  # onDemand déclenché (RuntimeProvider: anthropic)
    assert not any(r.startswith(("langchain-openai", "langchain-google-genai")) for r in runtime)   # non déclenchés
    assert "anthropic==1.8.0" in runtime                                  # SDK du fournisseur, fiche providers
    assert f"psycopg[binary,pool]=={_catalog_versions('vectorstore/pgvector')['psycopg']}" in runtime
    assert all("==" in r for r in runtime), "toute dépendance dérivée est épinglée"
    assert runtime == sorted(runtime, key=gas.package_name)
    # Les outils d'atelier vont au groupe dev, jamais dans la roue.
    assert not any(gas.package_name(r) in {"ruff", "mypy", "pytest-xdist"} for r in runtime)
    dev_line = next(line for line in text.splitlines() if line.startswith("dev = "))
    assert f'"mypy=={lc["mypy"]}"' in dev_line and '"pytest-xdist==' in dev_line
    assert '"pytest-asyncio' in dev_line                  # socle dev, épinglé par le catalogue quand il le porte


def test_a_catalog_not_active_contributes_nothing(project: Path) -> None:
    stack = project / "workspace/stack/STACK.md"
    stack.write_text(markdown_io.read_text(stack).replace(" - .sdda/stacks/vectorstore/pgvector.md", ""), encoding="utf-8")
    assert gas.run(project, mode="write").ok
    runtime = gas._declared_dependencies((project / SRC / "pyproject.toml").read_text(encoding="utf-8"))
    assert not any(gas.package_name(r) == "pgvector" for r in runtime)


def test_a_missing_pin_is_drift_and_a_manual_addition_survives(written: Path) -> None:
    target = written / SRC / "pyproject.toml"
    text = target.read_text(encoding="utf-8")
    edited = text.replace('  "anthropic==1.8.0",\n', '  "anthropic==1.7.0",\n  "rich==14.0.0",\n')
    assert edited != text
    target.write_text(edited, encoding="utf-8")
    report = gas.run(written, mode="check")
    assert "APP_SKELETON_STALE" in classes(report) and "anthropic==1.8.0" in report.data["missingPins"]
    assert gas.run(written, mode="write").ok
    runtime = gas._declared_dependencies(target.read_text(encoding="utf-8"))
    assert "anthropic==1.8.0" in runtime and "anthropic==1.7.0" not in runtime
    assert "rich==14.0.0" in runtime                     # l'ajout du projet (`uv add`) est conservé
    assert gas.run(written, mode="check").ok


def test_two_catalogs_disagreeing_on_a_pin_is_an_error(project: Path) -> None:
    import shutil
    from sdda_lib import paths as _paths
    vendored = project / ".sdda"
    shutil.copytree(_paths.FRAMEWORK_SDDA_DIR / "stacks", vendored / "stacks")
    shutil.copytree(_paths.FRAMEWORK_SDDA_DIR / "providers", vendored / "providers")
    catalog = vendored / "stacks/vectorstore/pgvector.libs.json"
    data = json.loads(catalog.read_text(encoding="utf-8"))
    data["versions"]["pydantic"] = "1.0.0"
    data["core"].append({"module": "pydantic", "ref": "pydantic"})
    catalog.write_text(json.dumps(data), encoding="utf-8")
    report = gas.run(project, mode="check")
    assert gas.CLS_PIN_CONFLICT in classes(report)


def test_no_placeholder_survives_the_rendering(written: Path) -> None:
    for path in sorted((written / f"workspace/src/{APP}").rglob("*")):
        if path.is_file() and path.suffix in (".py", ".json", ".toml"):
            assert "{AppName}" not in path.read_text(encoding="utf-8"), path


# ---------------------------------------------------------------------------
# Refus
# ---------------------------------------------------------------------------
def test_a_non_python_language_is_refused_before_anything_is_written(project: Path) -> None:
    stack = project / "workspace/stack/STACK.md"
    stack.write_text(
        markdown_io.read_text(stack).replace(".sdda/stacks/lang/python.md",
                                             ".sdda/stacks/lang/csharp.md"),
        encoding="utf-8")
    report = gas.run(project, mode="write")
    assert "STACK_LANGUAGE_MISMATCH" in classes(report)
    assert not (project / SRC / "config.py").exists()


def test_an_unresolved_app_name_is_refused(project: Path) -> None:
    stack = project / "workspace/stack/STACK.md"
    stack.write_text(markdown_io.read_text(stack).replace(f"AppName: {APP}", "AppName: <à préciser>"),
                     encoding="utf-8")
    report = gas.run(project, mode="write")
    assert "STACK_PLACEHOLDER_UNRESOLVED" in classes(report)


def test_missing_stack_is_refused(tmp_path: Path) -> None:
    (tmp_path / "workspace").mkdir()
    report = gas.run(tmp_path, mode="check")
    assert "STACK_MISSING" in classes(report)


def test_an_incoherent_deliverable_is_refused_by_the_packaging_enforcer(project: Path) -> None:
    """Le jugement appartient à `validate_packaging`, pas à une règle recopiée ici.

    Un `backend-api` derrière la surface console : le générateur ne réécrit pas
    la règle, il délègue — et la classe rendue est bien celle de l'enforcer.
    """
    stack = project / "workspace/stack/STACK.md"
    markdown_io.read_text(stack)
    stack.write_text(
        markdown_io.read_text(stack).replace(f"AppName: {APP}",
                                             f"AppName: {APP}\nDeliverableType: backend-api"),
        encoding="utf-8")
    report = gas.run(project, mode="write")
    assert {"PACKAGING_SURFACE_MISMATCH", "PACKAGING_API_FRAMEWORK_MISSING"} & classes(report)
    assert not (project / SRC / "config.py").exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_cli_check_exits_one_on_drift_and_zero_once_written(project: Path) -> None:
    code, _ = run_main(gas.main, ["--check", "--root", str(project)])
    assert code == 1
    code, _ = run_main(gas.main, ["--write", "--root", str(project)])
    assert code == 0
    code, out = run_main(gas.main, ["--check", "--root", str(project), "--json"])
    assert code == 0
    assert json.loads(out)["ok"] is True


# ---------------------------------------------------------------------------
# Frontière avec gen_source_tools
# ---------------------------------------------------------------------------
def test_source_tools_no_longer_emits_the_application_skeleton(tmp_path: Path) -> None:
    """Un projet `declared-sources` reçoit ses outils, pas une application.

    Sans l'allowlist, `emit_runtime` copierait `app/**` dans tout projet en
    sources déclarées — et `--check` le lui réclamerait ensuite à chaque
    passage, sur du code qu'il n'a jamais demandé.
    """
    project = make_project(tmp_path, "project_declared_sources")
    report = gst.run(project, mode="write")
    assert report.ok, report.render_text()
    emitted = {Path(p).as_posix() for p in report.data["runtimeWritten"]}
    assert any("/data/envelope.py" in p for p in emitted), "le runtime de données doit rester émis"
    assert any("/tools/registry.py" in p for p in emitted)
    assert not any("/config.py" in p or "/run_service.py" in p or "/serving/" in p
                   for p in emitted), f"squelette applicatif émis par le mauvais générateur : {emitted}"
    assert not (project / SRC / "run_service.py").exists()


def test_the_two_generators_coexist_on_the_same_project(tmp_path: Path) -> None:
    """Les deux tournent sur le même paquet sans se marcher dessus."""
    project = make_project(tmp_path, "project_declared_sources")
    assert gst.run(project, mode="write").ok
    assert gas.run(project, mode="write").ok
    assert gst.run(project, mode="check").ok
    assert gas.run(project, mode="check").ok
    assert (project / SRC / "data" / "envelope.py").is_file()
    assert (project / SRC / "run_service.py").is_file()
