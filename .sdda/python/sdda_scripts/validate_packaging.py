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
    python validate_packaging.py --json
    python validate_packaging.py --explain     # ce que chaque livrable exige
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import active_stacks, read_stack_section_kv  # noqa: E402
from sdda_scripts._common import (  # noqa: E402
    add_common_args, ensure_utf8_stdout, finish, load_config, resolve_root,
)

#: Framework HTTP -> langage qui peut le porter. Le seul accord que personne ne
#: peut rattraper plus tard : on ne compile pas du Spring Boot en Python.
API_FRAMEWORK_LANG = {
    "fastapi": "python",
    "django-ninja": "python",
    "flask": "python",
    "aspnet-minimal": "csharp",
    "aspnet-mvc": "csharp",
    "spring-boot": "java",
    "express": "typescript",
    "nestjs": "typescript",
}

#: Livrable -> surfaces d'exposition qui le servent. Un livrable ne dicte pas la
#: surface (un `container` peut exposer du HTTP ou tourner en lot) : la table ne
#: liste que les accords qui ont un sens, et l'absence d'accord est un refus.
DELIVERABLE_SURFACES = {
    "backend-api": {"fastapi-sse", "aspnet-minimal", "mcp-server", "chainlit", "slack-bot"},
    "cli-exe": {"cli"},
    "batch-job": {"batch", "cli"},
    "library": {"cli"},           # une bibliothèque n'expose rien ; la CLI sert son smoke
    "container": set(),           # toute surface : le conteneur est l'emballage, pas l'entrée
    "mcp-server": {"mcp-server"},
}

#: Livrables qui ouvrent une surface réseau — donc qui doivent établir une
#: identité d'appelant au transport.
NETWORK_DELIVERABLES = ("backend-api", "mcp-server")


def run(root: Path, report: Report) -> Report:
    config = load_config(root, report)

    deliverable = str(config.get("DeliverableType", "backend-api"))
    api_framework = str(config.get("ApiFramework", "none"))
    auth_mode = str(config.get("ApiAuthMode", "none"))
    contract_first = bool(config.get("ApiContractFirst", True))

    languages = active_stacks(root, "Active Language & Runtime")
    surfaces = active_stacks(root, "Active Serving Surface")
    data_access = active_stacks(root, "Active Data Access")
    loc = "workspace/stack/STACK.md ## Project Config"

    report.data.update({
        "deliverableType": deliverable, "apiFramework": api_framework,
        "apiAuthMode": auth_mode, "apiContractFirst": contract_first,
        "language": languages[0] if languages else None, "servingSurfaces": surfaces,
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

    # -- 2. Le framework doit tenir dans le langage actif --------------------
    expected = API_FRAMEWORK_LANG.get(api_framework)
    if expected and languages and expected not in languages:
        report.error("PACKAGING_LANG_MISMATCH",
                     f"`ApiFramework: {api_framework}` exige `lang/{expected}`, "
                     f"la stack active est `lang/{languages[0]}`",
                     f"activer `.sdda/stacks/lang/{expected}.md`, ou choisir un framework de "
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
    parser.add_argument("--mission", default="0", help="numéro de MISSION (artefact du rapport de gate)")
    args = parser.parse_args(argv)

    if args.explain:
        return explain()

    root = resolve_root(args)
    report = run(root, Report(name="PACKAGING", target=str(root)))
    if not args.no_report:
        # Le packaging est une décision d'architecture : son rapport est une
        # PART de G2, au même titre que le budget estimé ou l'IR. En faire une
        # gate séparée créerait un dixième verrou pour une seule question.
        write_gate_report(root, "G2", str(args.mission), report, {}, part="packaging")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
