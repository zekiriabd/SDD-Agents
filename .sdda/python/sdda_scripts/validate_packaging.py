#!/usr/bin/env python3
"""PACKAGING — le livrable déclaré est réalisable, et la surface le sert.

Ce que cette gate défend : *ce qu'on installe à la fin est une décision, pas un
reste.* Sans elle, `DeliverableType` n'existait pas : le pipeline savait décrire
par où l'on entre (`## Active Serving Surface`) et pas ce qu'on livre. La
question « et on livre quoi ? » n'ayant pas de réponse dans la configuration,
elle était arbitrée par l'agent — donc différemment à chaque run, et jamais
discutée en revue.

Quatre accords à vérifier, tous déterministes et tous jouables **avant** la
première ligne de code :

1. **Livrable x langage.** `spring-boot` sur une stack Python n'échoue pas à la
   validation, il échoue à la compilation — trois phases plus loin, après avoir
   payé les contrats, les prompts et les agents.
2. **Livrable x surface.** Un `backend-api` servi par `serving/cli.md` livre un
   binôme incohérent : la fiche de stack chargée décrit un point d'entrée que le
   livrable ne fournit pas. C'est l'erreur qui produit du code qui compile et ne
   sert rien.
3. **Identité de l'appelant.** `ApiAuthMode: none` sur un livrable réseau qui
   touche des données multi-locataires rend tout le filtrage à la source
   contournable : les vues SQL par agent et les filtres de retrieval s'appuient
   sur une identité que personne n'établit.
4. **Contrat d'abord.** `ApiContractFirst: false` autorise l'API à diverger de
   l'IR. C'est défendable — et c'est exactement le genre de décision qui doit
   porter un ADR référencé plutôt que de se découvrir en production.

Usage :
    python .sdda/sdda.py validate-packaging --json
    python .sdda/sdda.py validate-packaging --explain     # ce que chaque livrable exige
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import active_stacks  # noqa: E402
from sdda_scripts._common import (  # noqa: E402
    add_common_args, ensure_utf8_stdout, finish, load_config, resolve_root,
)
from sdda_scripts.validate_mission import mission_artifact  # noqa: E402

#: Framework HTTP -> langages qui peuvent le porter. Le seul accord que personne
#: ne peut rattraper plus tard : on ne compile pas du Spring Boot en Python.
#: Spring Boot se porte en Java comme en Kotlin — la fiche livrée est Kotlin.
API_FRAMEWORK_LANG: dict[str, tuple[str, ...]] = {
    "fastapi": ("python",),
    "django-ninja": ("python",),
    "flask": ("python",),
    "aspnet-minimal": ("csharp",),
    "aspnet-mvc": ("csharp",),
    "spring-boot": ("kotlin", "java"),
    "express": ("typescript",),
    "nestjs": ("typescript",),
}

#: Framework HTTP -> fiche `backend/` qui le porte. `## Active Backend Stack`
#: et `ApiFramework` disent la même chose sous deux formes — la fiche que
#: l'agent LIT, la clé que la politique d'équipe peut interdire — et doivent
#: donc s'accorder. Un framework sans fiche (django-ninja, flask, aspnet-mvc)
#: reste déclarable : la clé passe, la section reste vide, et c'est dit.
API_FRAMEWORK_BACKEND_SHEET = {
    "fastapi": "python-fastapi",
    "express": "node-express",
    "nestjs": "nestjs",
    "spring-boot": "kotlin-spring-boot",
    "aspnet-minimal": "dotnet-minimalapi",
}

#: Patterns d'architecture de la coquille qui supposent un SERVICE déployé :
#: sans appelant réseau, `/readyz`, l'idempotence et l'identité au transport
#: n'ont pas d'objet.
NETWORK_ARCHI = {"microservice"}

#: Les surfaces CONSOLE, une par langage. `cli-exe` est le défaut du framework
#: dans les quatre langages (`config.base.yml`) : le nommer ici une seule fois
#: évite qu'un langage ajouté plus tard rende ce défaut inatteignable sans que
#: rien ne le dise. C'est ce qui était arrivé à `csharp` : la seule fiche console
#: déclarait `Languages: python`, donc le défaut échouait au preflight.
CONSOLE_SURFACES = {
    "cli",          # [python]     serving/cli.md
    "cli-dotnet",   # [csharp]     serving/cli-dotnet.md
    "cli-node",     # [typescript] serving/cli-node.md
    "cli-kotlin",   # [kotlin]     serving/cli-kotlin.md
    "cli-java",     # [java]       serving/cli-java.md
}

#: Les surfaces HTTP/SSE, une par écosystème : c'est ce qui rend `backend-api`
#: atteignable dans les cinq langages. Sans elles, la table n'admettait qu'une
#: surface Python et une C#, et un backend TypeScript ou JVM était refusé en G2
#: alors que sa fiche `backend/` existait.
HTTP_SURFACES = {
    "fastapi-sse",      # [python]        serving/fastapi-sse.md
    "aspnet-minimal",   # [csharp]        serving/aspnet-minimal.md
    "http-sse-node",    # [typescript]    serving/http-sse-node.md
    "spring-sse",       # [kotlin, java]  serving/spring-sse.md
}

#: Livrable -> surfaces d'exposition qui le servent. Un livrable ne dicte pas la
#: surface (un `container` peut exposer du HTTP ou tourner en lot) : la table ne
#: liste que les accords qui ont un sens, et l'absence d'accord est un refus.
DELIVERABLE_SURFACES = {
    "backend-api": HTTP_SURFACES | {"mcp-server", "chainlit", "slack-bot"},
    "cli-exe": set(CONSOLE_SURFACES),
    "batch-job": {"batch"} | CONSOLE_SURFACES,
    "library": set(CONSOLE_SURFACES),   # une bibliothèque n'expose rien ; la console sert son smoke
    "container": set(),                 # toute surface : le conteneur est l'emballage, pas l'entrée
    "mcp-server": {"mcp-server"},
}

#: Livrables qui ouvrent une surface réseau — donc qui doivent établir une
#: identité d'appelant au transport.
NETWORK_DELIVERABLES = ("backend-api", "mcp-server")


def run(root: Path, report: Report) -> Report:
    config = load_config(root, report)

    # Défaut aligné sur `config.base.yml` : `cli-exe`, dans les quatre langages.
    # Un repli différent de la couche 1 du Project Config ferait deux vérités sur
    # la même clé, et c'est celle qu'on ne lit pas qui gagnerait.
    deliverable = str(config.get("DeliverableType", "cli-exe"))
    api_framework = str(config.get("ApiFramework", "none"))
    auth_mode = str(config.get("ApiAuthMode", "none"))
    contract_first = bool(config.get("ApiContractFirst", True))

    languages = active_stacks(root, "Active Language & Runtime")
    surfaces = active_stacks(root, "Active Serving Surface")
    data_access = active_stacks(root, "Active Data Access")
    archis = active_stacks(root, "Active Architecture Pattern")
    backends = active_stacks(root, "Active Backend Stack")
    loc = "workspace/stack/STACK.md ## Project Config"

    report.data.update({
        "deliverableType": deliverable, "apiFramework": api_framework,
        "apiAuthMode": auth_mode, "apiContractFirst": contract_first,
        "language": languages[0] if languages else None, "servingSurfaces": surfaces,
        "archiPattern": archis[0] if len(archis) == 1 else None, "backendStack": backends,
    })

    if deliverable not in DELIVERABLE_SURFACES:
        report.error("PACKAGING_TYPE_UNKNOWN",
                     f"`DeliverableType: {deliverable}` hors liste close",
                     f"valeurs admises : {', '.join(sorted(DELIVERABLE_SURFACES))}", loc)
        return report

    # -- 1. backend-api exige un framework, et lui seul ----------------------
    if deliverable == "backend-api":
        if api_framework == "none":
            report.error("PACKAGING_API_FRAMEWORK_MISSING",
                         "`DeliverableType: backend-api` sans `ApiFramework`",
                         "choisir le framework HTTP : sans lui, `dev-api` en invente un, "
                         "et le choix ne sera jamais relu en revue", loc)
    elif api_framework != "none":
        report.warn("PACKAGING_API_FRAMEWORK_UNUSED",
                    f"`ApiFramework: {api_framework}` alors que le livrable est `{deliverable}`",
                    "poser `ApiFramework: none` — une clé qui ne sert à rien finit par être "
                    "lue comme si elle servait", loc)

    # -- 1bis. La fiche backend dit la même chose que la clé ------------------
    sheet_loc = "workspace/stack/STACK.md ## Active Backend Stack"
    if deliverable == "backend-api":
        expected_sheet = API_FRAMEWORK_BACKEND_SHEET.get(api_framework)
        if not backends:
            if expected_sheet:
                report.error("PACKAGING_BACKEND_SHEET_MISSING",
                             "`DeliverableType: backend-api` sans fiche `backend/` active",
                             f"activer `.sdda/stacks/backend/{expected_sheet}.md` : c'est la fiche que "
                             "dev-backend lit pour le projet, la DI, la config et le packaging — sans "
                             "elle, il les invente", sheet_loc)
            elif api_framework != "none":
                report.warn("PACKAGING_BACKEND_SHEET_MISSING",
                            f"`ApiFramework: {api_framework}` n'a aucune fiche `backend/` au catalogue",
                            "le framework est déclarable, mais dev-backend travaillera sans fiche : "
                            "l'écrire, ou choisir un framework qui en a une", sheet_loc)
        elif len(backends) > 1:
            report.error("PACKAGING_BACKEND_SHEET_MISMATCH",
                         f"{len(backends)} fiches `backend/` actives : {', '.join(backends)}",
                         "une seule maison HTTP — en activer exactement une", sheet_loc)
        elif expected_sheet and backends[0] != expected_sheet:
            report.error("PACKAGING_BACKEND_SHEET_MISMATCH",
                         f"`ApiFramework: {api_framework}` mais la fiche active est `backend/{backends[0]}`",
                         f"activer `backend/{expected_sheet}.md`, ou aligner `ApiFramework` — la clé et la "
                         "fiche disent la même chose sous deux formes et doivent s'accorder", sheet_loc)
    elif backends:
        report.warn("PACKAGING_BACKEND_SHEET_UNUSED",
                    f"fiche `backend/{backends[0]}` active alors que le livrable est `{deliverable}`",
                    "la retirer : une fiche lue pour rien finit par être suivie", sheet_loc)

    # -- 1ter. L'architecture de la coquille : une, et compatible avec le livrable
    archi_loc = "workspace/stack/STACK.md ## Active Architecture Pattern"
    if len(archis) != 1:
        report.error("PACKAGING_ARCHI_UNDECLARED",
                     f"`## Active Architecture Pattern` active {len(archis)} fiche(s) : {archis or 'aucune'}",
                     "activer exactement une fiche `.sdda/stacks/archi/*.md` (mvc par défaut) : sans elle, "
                     "chaque dev-* impose son découpage et le projet en porte trois", archi_loc)
    elif archis[0] in NETWORK_ARCHI and deliverable not in ("backend-api", "container"):
        report.error("PACKAGING_ARCHI_DELIVERABLE_MISMATCH",
                     f"`archi/{archis[0]}` avec `DeliverableType: {deliverable}`",
                     "un microservice est un service déployé seul, appelé par d'autres : il exige "
                     "`backend-api` ou `container`. Un exécutable lancé à la main n'a ni /readyz ni "
                     "appelant à authentifier — choisir archi/mvc ou archi/ddd", archi_loc)

    # -- 2. Le framework doit tenir dans le langage actif --------------------
    expected = API_FRAMEWORK_LANG.get(api_framework)
    if expected and languages and not (set(expected) & set(languages)):
        report.error("PACKAGING_LANG_MISMATCH",
                     f"`ApiFramework: {api_framework}` exige `lang/{'|'.join(expected)}`, "
                     f"la stack active est `lang/{languages[0]}`",
                     f"activer `.sdda/stacks/lang/{expected[0]}.md`, ou choisir un framework de "
                     f"`{languages[0]}`. Cet écart n'échoue pas ici mais à la compilation, "
                     "trois phases plus loin, après avoir payé contrats, prompts et agents", loc)

    # -- 3. La surface doit servir le livrable -------------------------------
    allowed = DELIVERABLE_SURFACES[deliverable]
    if allowed and surfaces and not (set(surfaces) & allowed):
        report.error("PACKAGING_SURFACE_MISMATCH",
                     f"livrable `{deliverable}` servi par `{', '.join(surfaces)}`",
                     f"activer une surface qui le sert : {', '.join(sorted(allowed))}. "
                     "Un livrable réseau derrière une surface CLI produit du code qui "
                     "compile et ne sert rien", "workspace/stack/STACK.md ## Active Serving Surface")

    # -- 4. Identité de l'appelant sur une surface réseau --------------------
    if deliverable in NETWORK_DELIVERABLES and auth_mode == "none":
        touches_data = bool(data_access and data_access != ["none"])
        if touches_data:
            report.error("PACKAGING_IDENTITY_UNESTABLISHED",
                         f"`ApiAuthMode: none` sur un `{deliverable}` avec accès aux données "
                         f"(`{data_access[0]}`)",
                         "l'identité de l'appelant entre par le transport (dev-api STEP 3) : "
                         "sans elle, les vues SQL par agent et les filtres de retrieval filtrent "
                         "sur une identité que personne n'a établie, donc ne filtrent rien", loc)
        else:
            report.warn("PACKAGING_IDENTITY_UNESTABLISHED",
                        f"`ApiAuthMode: none` sur un `{deliverable}`",
                        "acceptable derrière une passerelle qui établit déjà l'identité — "
                        "à dire dans un ADR si c'est le cas", loc)

    # -- 5. Contrat d'abord : la divergence se décide, elle ne se subit pas --
    if deliverable == "backend-api" and not contract_first:
        report.warn("PACKAGING_CONTRACT_DRIFT_ALLOWED",
                    "`ApiContractFirst: false` — l'API peut diverger des schémas de l'IR",
                    "exige un ADR référencé : sans lui, la divergence se découvre chez "
                    "l'appelant, pas en revue", loc)

    return report


def explain() -> int:
    print("\nCe que chaque livrable exige\n" + "-" * 62)
    for deliverable, allowed in DELIVERABLE_SURFACES.items():
        surfaces = ", ".join(sorted(allowed)) if allowed else "toute surface"
        network = "  · identité d'appelant obligatoire" if deliverable in NETWORK_DELIVERABLES else ""
        print(f"  {deliverable:<14} surface(s) : {surfaces}{network}")
    print("\nFramework HTTP -> langage requis\n" + "-" * 62)
    for framework, language in sorted(API_FRAMEWORK_LANG.items()):
        print(f"  {framework:<14} -> lang/{language}")
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="Valide le livrable déclaré et sa surface.")
    add_common_args(parser)
    parser.add_argument("--explain", action="store_true", help="ce que chaque livrable exige")
    parser.add_argument("--mission", default=None, help="numéro de MISSION (artefact du rapport de gate) ; défaut : `stack`, valable pour toutes")
    args = parser.parse_args(argv)

    if args.explain:
        return explain()

    root = resolve_root(args)
    report = run(root, Report(name="PACKAGING", target=str(root)))
    if not args.no_report:
        # Le packaging est une décision d'architecture : son rapport est une
        # PART de G2, au même titre que le budget estimé ou l'IR. En faire une
        # gate séparée créerait un dixième verrou pour une seule question.
        #
        # Artefact `stack` sans `--mission` (et non plus `0`, rattaché à rien :
        # `dev-backend` l'appelait ainsi, et son rouge restait invisible).
        # Épinglé sur STACK.md, où vivent toutes les clés lues : vide, la part
        # restait verte après n'importe quel changement de livrable.
        stack = paths.stack_md_path(root)
        pins = {"stack": hashing.sha256_file(stack)} if stack.is_file() else {}
        write_gate_report(root, "G2", mission_artifact(root, args.mission), report, pins, part="packaging")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
