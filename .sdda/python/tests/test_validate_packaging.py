"""PACKAGING — le livrable déclaré, et le défaut console dans les quatre langages.

Ce que ces tests défendent : le défaut du framework est `cli-exe` et il est
déclaré UNE fois, pas quatre fois différemment ; chaque langage qui prétend
pouvoir livrer une console a réellement une fiche derrière ; et `backend-api`,
qui change la nature du livrable, exige ce qu'il exige.

Le bug qui a motivé ce fichier : trois sources déclaraient trois défauts.
`config.base.yml` disait `cli-exe`, le schéma JSON et le repli de
`validate_packaging` disaient `backend-api`. La valeur retenue dépendait donc du
chemin de lecture, et la clé la moins lue gagnait.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from sdda_lib.errors import Report
from sdda_scripts import validate_packaging as vp

SDDA = Path(vp.__file__).resolve().parents[2]
ROOT = SDDA.parent
STACK = "workspace/stack/STACK.md"

CONSOLE_DEFAULT = "cli-exe"


# ---------------------------------------------------------------------------
# Aides
# ---------------------------------------------------------------------------
def set_config(project: Path, **keys: str) -> None:
    """Pose des clés sous `## Project Config` de la STACK.md de la fixture."""
    path = project / STACK
    text = path.read_text(encoding="utf-8")
    block = "".join(f"{k}: {v}\n" for k, v in keys.items())
    path.write_text(text.replace("## Project Config\n", f"## Project Config\n{block}", 1), encoding="utf-8")


def set_surface(project: Path, sheet: str) -> None:
    path = project / STACK
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(" - .sdda/stacks/serving/cli.md", f" - .sdda/stacks/serving/{sheet}.md", 1), encoding="utf-8")


def check(project: Path) -> Report:
    return vp.run(project, Report(name="PACKAGING", target=str(project)))


def classes(report: Report) -> set[str]:
    return {f.cls for f in report.errors}


# ---------------------------------------------------------------------------
# Le défaut est déclaré une fois
# ---------------------------------------------------------------------------
def test_the_four_declarations_of_the_default_agree_on_console() -> None:
    base = (SDDA / "config.base.yml").read_text(encoding="utf-8")
    assert re.search(rf"^DeliverableType:\s*{CONSOLE_DEFAULT}\b", base, re.M), "config.base.yml"

    schema = json.loads((SDDA / "templates" / "project-config.schema.json").read_text(encoding="utf-8-sig"))
    assert schema["properties"]["DeliverableType"]["default"] == CONSOLE_DEFAULT, "project-config.schema.json"

    boot = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
    assert re.search(rf'deliverable:\s*str\s*=\s*"{CONSOLE_DEFAULT}"', boot), "bootstrap.Combo"

    source = Path(vp.__file__).read_text(encoding="utf-8")
    assert f'config.get("DeliverableType", "{CONSOLE_DEFAULT}")' in source, "repli de validate_packaging"


def test_a_stack_that_declares_nothing_delivers_a_console_app(project: Path) -> None:
    """La fixture ne déclare aucun `DeliverableType` : le défaut doit s'appliquer, et passer."""
    report = check(project)
    assert report.data["deliverableType"] == CONSOLE_DEFAULT
    assert report.ok, [f.cls for f in report.errors]


# ---------------------------------------------------------------------------
# La console existe vraiment, langage par langage
# ---------------------------------------------------------------------------
def test_every_console_surface_has_a_sheet_that_declares_its_language() -> None:
    """Un défaut qu'un langage ne peut pas honorer est un piège, pas un défaut."""
    for surface in vp.CONSOLE_SURFACES:
        sheet = SDDA / "stacks" / "serving" / f"{surface}.md"
        assert sheet.is_file(), f"{surface} annoncée dans CONSOLE_SURFACES sans fiche sur disque"
        assert re.search(r"^Languages:\s*\S", sheet.read_text(encoding="utf-8"), re.M), surface


def test_one_console_surface_per_language() -> None:
    """Deux consoles pour un même langage rendraient le défaut ambigu."""
    languages = []
    for surface in vp.CONSOLE_SURFACES:
        text = (SDDA / "stacks" / "serving" / f"{surface}.md").read_text(encoding="utf-8")
        languages.append(re.search(r"^Languages:\s*(.+)$", text, re.M).group(1).strip())
    assert len(languages) == len(set(languages)), languages


def test_the_console_default_is_reachable_in_csharp() -> None:
    """C'est le manque que `serving/cli-dotnet.md` comble : `cli-exe` échouait en C#."""
    assert "cli-dotnet" in vp.DELIVERABLE_SURFACES[CONSOLE_DEFAULT]
    assert "cli-dotnet" in vp.DELIVERABLE_SURFACES["library"]


def test_a_library_and_a_batch_job_are_also_served_by_a_console() -> None:
    assert vp.CONSOLE_SURFACES <= vp.DELIVERABLE_SURFACES["library"]
    assert vp.CONSOLE_SURFACES <= vp.DELIVERABLE_SURFACES["batch-job"]


# ---------------------------------------------------------------------------
# `backend-api` change la nature du livrable, et exige ce qu'il exige
# ---------------------------------------------------------------------------
def test_a_backend_api_without_a_framework_is_red(project: Path) -> None:
    set_config(project, DeliverableType="backend-api")
    set_surface(project, "fastapi-sse")
    assert "PACKAGING_API_FRAMEWORK_MISSING" in classes(check(project))


def test_an_api_framework_from_another_language_is_red(project: Path) -> None:
    """La fixture est Python : `spring-boot` n'échouerait qu'à la compilation."""
    set_config(project, DeliverableType="backend-api", ApiFramework="spring-boot")
    set_surface(project, "fastapi-sse")
    assert "PACKAGING_LANG_MISMATCH" in classes(check(project))


def test_a_backend_api_behind_a_console_surface_is_red(project: Path) -> None:
    """Du code qui compile et ne sert rien : le livrable réseau n'a pas d'entrée réseau."""
    set_config(project, DeliverableType="backend-api", ApiFramework="fastapi")
    assert "PACKAGING_SURFACE_MISMATCH" in classes(check(project))


def test_a_console_app_that_also_names_an_api_framework_is_a_warning(project: Path) -> None:
    set_config(project, ApiFramework="fastapi")
    report = check(project)
    assert report.ok
    assert "PACKAGING_API_FRAMEWORK_UNUSED" in {f.cls for f in report.warnings}


def test_an_unknown_deliverable_is_named_and_stops_there(project: Path) -> None:
    set_config(project, DeliverableType="desktop-app")
    report = check(project)
    assert "PACKAGING_TYPE_UNKNOWN" in classes(report)


# ---------------------------------------------------------------------------
# Les listes closes s'accordent entre elles
# ---------------------------------------------------------------------------
def test_the_schema_enum_and_the_surface_table_cover_the_same_deliverables() -> None:
    schema = json.loads((SDDA / "templates" / "project-config.schema.json").read_text(encoding="utf-8-sig"))
    assert set(schema["properties"]["DeliverableType"]["enum"]) == set(vp.DELIVERABLE_SURFACES)


def test_every_api_framework_of_the_schema_declares_its_language() -> None:
    schema = json.loads((SDDA / "templates" / "project-config.schema.json").read_text(encoding="utf-8-sig"))
    declared = set(schema["properties"]["ApiFramework"]["enum"]) - {"none"}
    assert declared == set(vp.API_FRAMEWORK_LANG)


@pytest.mark.parametrize("framework,language", sorted(vp.API_FRAMEWORK_LANG.items()))
def test_each_api_framework_targets_one_of_the_four_languages(framework: str, language: str) -> None:
    # Un framework peut être porté par plusieurs langages (Spring Boot : Kotlin et Java) ;
    # chacun doit être un langage que le catalogue connaît.
    assert isinstance(language, tuple) and language, framework
    assert set(language) <= {"python", "csharp", "java", "typescript", "kotlin"}, framework


# ---------------------------------------------------------------------------
# La coquille : `## Active Architecture Pattern` et `## Active Backend Stack`
# (hérités de SDD_Pro le 2026-09-23)
# ---------------------------------------------------------------------------
def set_archi(project: Path, sheet: str | None) -> None:
    """Remplace la fiche archi active ; `None` vide la section."""
    path = project / STACK
    text = path.read_text(encoding="utf-8")
    line = " - .sdda/stacks/archi/mvc.md"
    assert line in text
    path.write_text(text.replace(line, f" - .sdda/stacks/archi/{sheet}.md" if sheet else "# (aucune)", 1), encoding="utf-8")


def set_backend(project: Path, *sheets: str) -> None:
    path = project / STACK
    text = path.read_text(encoding="utf-8")
    marker = "## Active Backend Stack\n"
    assert marker in text
    lines = "".join(f" - .sdda/stacks/backend/{s}.md\n" for s in sheets)
    path.write_text(text.replace(marker, marker + lines, 1), encoding="utf-8")


def test_every_backend_sheet_of_the_map_exists_and_speaks_its_language() -> None:
    """La clé `ApiFramework` et la fiche `backend/` disent la même chose : la seconde doit exister."""
    for framework, sheet in vp.API_FRAMEWORK_BACKEND_SHEET.items():
        path = SDDA / "stacks" / "backend" / f"{sheet}.md"
        assert path.is_file(), f"{framework} -> backend/{sheet}.md absent"
        text = path.read_text(encoding="utf-8")
        declared = re.search(r"^Languages:\s*(.+)$", text, re.M)
        assert declared and declared.group(1).strip() in vp.API_FRAMEWORK_LANG[framework], sheet


def test_every_console_surface_has_a_sheet_on_disk() -> None:
    for surface in vp.CONSOLE_SURFACES:
        assert (SDDA / "stacks" / "serving" / f"{surface}.md").is_file(), surface


def test_backend_api_without_a_backend_sheet_is_refused(project: Path) -> None:
    set_config(project, DeliverableType="backend-api", ApiFramework="fastapi", ApiAuthMode="oauth2")
    set_surface(project, "fastapi-sse")
    assert "PACKAGING_BACKEND_SHEET_MISSING" in classes(check(project))


def test_backend_api_with_the_matching_sheet_is_green_on_that_axis(project: Path) -> None:
    set_config(project, DeliverableType="backend-api", ApiFramework="fastapi", ApiAuthMode="oauth2")
    set_surface(project, "fastapi-sse")
    set_backend(project, "python-fastapi")
    report = check(project)
    assert not {c for c in classes(report) if c.startswith("PACKAGING_BACKEND_")}
    assert report.data["backendStack"] == ["python-fastapi"]


def test_a_backend_sheet_that_contradicts_the_key_is_refused(project: Path) -> None:
    set_config(project, DeliverableType="backend-api", ApiFramework="fastapi", ApiAuthMode="oauth2")
    set_surface(project, "fastapi-sse")
    set_backend(project, "node-express")
    assert "PACKAGING_BACKEND_SHEET_MISMATCH" in classes(check(project))


def test_two_backend_sheets_are_refused(project: Path) -> None:
    set_config(project, DeliverableType="backend-api", ApiFramework="fastapi", ApiAuthMode="oauth2")
    set_surface(project, "fastapi-sse")
    set_backend(project, "python-fastapi", "nestjs")
    assert "PACKAGING_BACKEND_SHEET_MISMATCH" in classes(check(project))


def test_a_backend_sheet_on_a_console_deliverable_is_a_warning(project: Path) -> None:
    set_backend(project, "python-fastapi")
    report = check(project)
    assert "PACKAGING_BACKEND_SHEET_UNUSED" in {f.cls for f in report.warnings}
    assert "PACKAGING_BACKEND_SHEET_UNUSED" not in classes(report)


def test_the_shell_needs_exactly_one_architecture(project: Path) -> None:
    assert "PACKAGING_ARCHI_UNDECLARED" not in classes(check(project))     # mvc par défaut dans la fixture
    set_archi(project, None)
    assert "PACKAGING_ARCHI_UNDECLARED" in classes(check(project))


def test_microservice_needs_a_network_deliverable(project: Path) -> None:
    set_archi(project, "microservice")
    assert "PACKAGING_ARCHI_DELIVERABLE_MISMATCH" in classes(check(project))          # cli-exe par défaut
    set_config(project, DeliverableType="backend-api", ApiFramework="fastapi", ApiAuthMode="oauth2")
    set_surface(project, "fastapi-sse")
    set_backend(project, "python-fastapi")
    assert "PACKAGING_ARCHI_DELIVERABLE_MISMATCH" not in classes(check(project))


def test_spring_boot_is_carried_by_kotlin(project: Path) -> None:
    """Spring Boot se compile en Kotlin comme en Java ; la fiche livrée est Kotlin."""
    path = project / STACK
    text = path.read_text(encoding="utf-8").replace(" - .sdda/stacks/lang/python.md", " - .sdda/stacks/lang/kotlin.md", 1)
    path.write_text(text, encoding="utf-8")
    set_config(project, DeliverableType="backend-api", ApiFramework="spring-boot", ApiAuthMode="oauth2")
    set_backend(project, "kotlin-spring-boot")
    assert "PACKAGING_LANG_MISMATCH" not in classes(check(project))
