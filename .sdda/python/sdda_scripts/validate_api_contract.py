#!/usr/bin/env python3
"""G6, part `api` — L'API GATE : l'OpenAPI publié est DÉRIVÉ de l'IR.

Ce que cette gate défend : *la couture entre l'IR et le monde extérieur est un
contrat, pas une convention.* C'est la transposition de l'`API Gate` de SDD_Pro,
qui validait le contrat back↔front avant de générer le front ; ici les deux
côtés sont l'IR et l'OpenAPI publié.

Elle était **annoncée bloquante et appliquée par rien.** `ARCHITECTURE.md §4`
rangeait `[API_CONTRACT_DRIFT]` et `[API_ROUTE_UNBACKED]` parmi les classes
bloquantes de G6, `stacks/serving/fastapi-sse.md §6` en donnait la table
complète — et aucun script ne les émettait. Le registre canonique se déclarait
« à jour » parce qu'il est régénéré depuis les émetteurs RÉELS : une classe qui
ne vit que dans la prose n'y entre jamais, et son absence ne se voit pas. C'est
le contrôle `errors.documented` du smoke qui a fini par le dire.

Trois divergences, toutes déterministes et 0 token :

    1. un champ publié absent de l'`inputSchema`/`outputSchema` de l'IR,
       ou un champ REQUIS de l'IR absent du contrat publié   [API_CONTRACT_DRIFT]
    2. une route publiée que rien ne soutient — `/resume` sans
       `humanInTheLoop`, ou un chemin hors du contrat de la
       surface active                                        [API_ROUTE_UNBACKED]
    3. un statut HTTP publié absent de la table de mapping
       `[CLASS]` -> code de la surface                       [API_STATUS_UNMAPPED]

**Ce que la gate ne fait PAS** : inventer un contrat quand il n'y en a pas.
Tant qu'aucun OpenAPI n'est publié sur le disque, la part n'est pas applicable —
elle n'écrit alors aucun rapport, et n'accorde donc aucun vert. C'est délibéré :
une part contributive absente ne bloque pas, mais un vert accordé à une API qui
n'existe pas serait exactement le faux vert que ce framework existe pour
empêcher.

Usage :
    python validate_api_contract.py --mission 1
    python validate_api_contract.py --mission 1 --json
    python validate_api_contract.py --explain
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import active_stacks  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import (  # noqa: E402
    add_common_args, ensure_utf8_stdout, finish, load_config, resolve_root,
)

#: Les surfaces qui publient un contrat HTTP. Les autres (`cli`, `batch`) n'ont
#: pas d'OpenAPI : la part `api` de G6 ne les concerne pas.
HTTP_SURFACES = ("fastapi-sse", "aspnet-minimal", "aspnet-mvc", "mcp-server")

#: Le contrat de routes de la surface, transcrit de `serving/fastapi-sse.md
#: §3.1` — identique pour `aspnet-minimal.md` (« mêmes chemins, mêmes rôles »).
#: Les paramètres de chemin sont normalisés en `{}` : ce qui est vérifié est la
#: FORME de la route, pas le nom de sa variable.
#:
#: Une route hors de cette table n'est pas « en trop » par purisme : elle est un
#: point d'entrée que ni l'IR ni la fiche de stack ne décrivent, donc que
#: personne n'a évalué et qu'aucune borne ne protège.
SURFACE_ROUTES: set[str] = {
    "/v1/runs", "/v1/runs/{}", "/v1/runs/{}/resume", "/v1/inspect",
    "/healthz", "/readyz", "/openapi.json", "/docs", "/redoc",
}

#: Route conditionnée par une capacité de l'IR : publiée sans elle, elle promet
#: une reprise que le graphe ne sait pas faire.
CONDITIONAL_ROUTES = {"/v1/runs/{}/resume": "humanInTheLoop"}

#: Statuts qu'une surface HTTP émet par construction, sans passer par la table
#: de mapping `[CLASS]` -> code : ils ne relèvent d'aucune classe du framework.
ALWAYS_MAPPED_STATUSES = {"200", "201", "202", "204", "default"}

_PATH_PARAM_RE = re.compile(r"\{[^}]*\}")


def normalize_route(path: str) -> str:
    return _PATH_PARAM_RE.sub("{}", path.rstrip("/") or "/")


# ---------------------------------------------------------------------------
# Lecture des deux côtés de la couture
# ---------------------------------------------------------------------------
def find_openapi(root: Path) -> Path | None:
    """Le contrat publié, tel que la surface l'exporte sur le disque.

    Cherché sous `workspace/src/`, jamais servi par le réseau : une gate
    déterministe ne démarre pas l'application. C'est la commande de smoke de la
    fiche de serving qui produit ce fichier (`--check` puis export).
    """
    candidates = sorted((paths.workspace(root) / "src").rglob("openapi.json"))
    return candidates[0] if candidates else None


def resolve_ref(doc: dict[str, Any], node: Any, _depth: int = 0) -> dict[str, Any]:
    """Déréférence un `$ref` local (`#/components/schemas/X`). Profondeur bornée."""
    if not isinstance(node, dict) or _depth > 8:
        return node if isinstance(node, dict) else {}
    ref = node.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return node
    target: Any = doc
    for part in ref[2:].split("/"):
        if not isinstance(target, dict) or part not in target:
            return {}
        target = target[part]
    return resolve_ref(doc, target, _depth + 1)


def entry_agent(ir: dict[str, Any]) -> dict[str, Any] | None:
    """L'agent du `entryNode` : c'est SON contrat que la surface publie."""
    orch = ir.get("orchestration") or {}
    nodes = {str(n.get("id")): n for n in orch.get("nodes") or []}
    node = nodes.get(str(orch.get("entryNode")))
    if not node or node.get("kind") != "agent":
        return None
    ref = str(node.get("ref"))
    return next((a for a in ir.get("agents") or [] if str(a.get("id")) == ref), None)


def _properties(schema: Any) -> tuple[set[str], set[str]]:
    """(propriétés, requises) d'un JSON Schema objet ; ensembles vides sinon."""
    if not isinstance(schema, dict):
        return set(), set()
    props = schema.get("properties")
    props = set(props) if isinstance(props, dict) else set()
    required = schema.get("required")
    required = {str(r) for r in required} if isinstance(required, list) else set()
    return props, required


def published_field(doc: dict[str, Any], schema_name: str, field: str) -> dict[str, Any] | None:
    """Le sous-schéma `{schema_name}.{field}` des composants de l'OpenAPI."""
    schemas = ((doc.get("components") or {}).get("schemas") or {})
    envelope = resolve_ref(doc, schemas.get(schema_name))
    inner = (envelope.get("properties") or {}).get(field)
    return resolve_ref(doc, inner) if inner is not None else None


# ---------------------------------------------------------------------------
# Les trois contrôles
# ---------------------------------------------------------------------------
def check_schema_drift(
    doc: dict[str, Any], agent: dict[str, Any], report: Report, loc: str
) -> None:
    """Contrat publié <-> schémas de l'IR, dans les DEUX sens.

    Les deux sens comptent, et pour des raisons différentes : un champ publié
    en trop est une surface d'entrée que l'agent ne validera pas ; un champ
    requis de l'IR absent du contrat est une promesse que l'appelant ne peut
    pas tenir et qui échouera au premier appel réel.
    """
    for schema_name, field, ir_key in (
        ("RunRequest", "input", "inputSchema"),
        ("RunResponse", "output", "outputSchema"),
    ):
        ir_props, ir_required = _properties(agent.get(ir_key))
        if not ir_props:
            continue
        api_schema = published_field(doc, schema_name, field)
        if api_schema is None:
            report.error(
                "API_CONTRACT_DRIFT",
                f"`{schema_name}.{field}` absent de l'OpenAPI publié alors que "
                f"l'IR déclare `agents[].{ir_key}`",
                f"générer `schemas.py` depuis l'IR ({schema_name}.{field} <- {ir_key}) "
                "plutôt que de l'écrire à la main",
                loc,
            )
            continue
        api_props, _ = _properties(api_schema)
        extra = sorted(api_props - ir_props)
        missing = sorted(ir_required - api_props)
        if extra:
            report.error(
                "API_CONTRACT_DRIFT",
                f"`{schema_name}.{field}` publie {len(extra)} champ(s) absent(s) de "
                f"`{ir_key}` : {', '.join(extra[:6])}",
                "retirer le champ du contrat publié, ou le déclarer dans le contrat "
                "d'agent puis recompiler l'IR. Un champ publié que l'agent ne connaît "
                "pas est une entrée que rien ne valide",
                loc,
            )
        if missing:
            report.error(
                "API_CONTRACT_DRIFT",
                f"`{ir_key}` exige {len(missing)} champ(s) que l'OpenAPI ne publie pas : "
                f"{', '.join(missing[:6])}",
                "régénérer le contrat publié depuis l'IR : un champ requis absent du "
                "contrat échoue au premier appel réel, pas en revue",
                loc,
            )


def check_routes(doc: dict[str, Any], ir: dict[str, Any], report: Report, loc: str) -> None:
    orch = ir.get("orchestration") or {}
    published = {normalize_route(p) for p in (doc.get("paths") or {})}

    for route in sorted(published - SURFACE_ROUTES):
        report.error(
            "API_ROUTE_UNBACKED",
            f"route `{route}` publiée hors du contrat de la surface",
            "retirer la route, ou l'ajouter à la fiche de serving active (§3.1) avec "
            "son rôle et son coût en tokens. Une route non décrite est un point "
            "d'entrée que personne n'a évalué et qu'aucune borne ne protège",
            loc,
        )

    for route, capability in CONDITIONAL_ROUTES.items():
        if route in published and not orch.get(capability):
            report.error(
                "API_ROUTE_UNBACKED",
                f"route `{route}` publiée alors que `orchestration.{capability}` est faux",
                f"activer `{capability}` dans la topologie (le graphe doit savoir "
                "s'interrompre et reprendre), ou ne pas publier la route",
                loc,
            )


def check_statuses(doc: dict[str, Any], mapping: set[str], report: Report, loc: str, mapping_loc: str | None) -> None:
    published: set[str] = set()
    for methods in (doc.get("paths") or {}).values():
        if not isinstance(methods, dict):
            continue
        for operation in methods.values():
            if isinstance(operation, dict) and isinstance(operation.get("responses"), dict):
                published.update(str(code) for code in operation["responses"])

    unmapped = sorted(published - ALWAYS_MAPPED_STATUSES - mapping)
    if not unmapped:
        return
    if mapping_loc is None:
        report.error(
            "API_STATUS_UNMAPPED",
            f"{len(unmapped)} statut(s) publié(s) ({', '.join(unmapped[:8])}) sans module "
            "de mapping `[CLASS]` -> code sur le disque",
            "générer `serving/status.py` (ou son équivalent) depuis la table de la fiche "
            "de serving : un statut sans classe en face rend l'erreur inclassable pour "
            "l'appelant comme pour les tableaux de bord",
            loc,
        )
        return
    report.error(
        "API_STATUS_UNMAPPED",
        f"statut(s) {', '.join(unmapped[:8])} publié(s) et absent(s) de `{mapping_loc}`",
        "mapper chaque statut sur une classe de la taxonomie, ou cesser de l'émettre",
        loc,
    )


def read_status_mapping(root: Path) -> tuple[set[str], str | None]:
    """Codes HTTP cités par le module de mapping de la surface, s'il existe."""
    for pattern in ("**/serving/status.py", "**/Serving/Status.cs", "**/serving/status.ts"):
        for path in sorted((paths.workspace(root) / "src").glob(pattern)):
            text = path.read_text(encoding="utf-8", errors="replace")
            return set(re.findall(r"\b([1-5]\d{2})\b", text)), paths.rel(root, path)
    return set(), None


# ---------------------------------------------------------------------------
def run(root: Path, mission: str, report: Report) -> bool:
    """Renvoie True si la part est APPLICABLE (un contrat publié a été confronté)."""
    config = load_config(root, report)
    surfaces = active_stacks(root, "Active Serving Surface")
    openapi_path = find_openapi(root)

    report.data.update({
        "servingSurfaces": surfaces,
        "openapi": paths.rel(root, openapi_path) if openapi_path else None,
    })

    if openapi_path is None:
        report.data["applicable"] = False
        report.data["reason"] = "aucun openapi.json publié sous workspace/src/"
        return False

    loc = paths.rel(root, openapi_path)

    if surfaces and not (set(surfaces) & set(HTTP_SURFACES)):
        report.warn(
            "API_ROUTE_UNBACKED",
            f"un OpenAPI est publié alors que la surface active est `{', '.join(surfaces)}`",
            "la surface active ne publie pas de contrat HTTP : soit le fichier est un "
            "reste d'une surface précédente, soit STACK.md ne dit pas ce que le code fait",
            loc,
        )

    try:
        doc = json.loads(openapi_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        report.error("API_CONTRACT_DRIFT", f"OpenAPI illisible : {exc}",
                     "régénérer le contrat publié", loc)
        report.data["applicable"] = True
        return True
    if not isinstance(doc, dict):
        report.error("API_CONTRACT_DRIFT", "OpenAPI : document racine non objet", "", loc)
        report.data["applicable"] = True
        return True

    ir_file = paths.ir_path(root, mission)
    if not ir_file.is_file():
        report.error("IR_NOT_FOUND", f"IR `{paths.rel(root, ir_file)}` absent",
                     f"compiler : python .sdda/python/sdda_scripts/ir_compiler.py --mission {mission}",
                     paths.rel(root, ir_file))
        report.data["applicable"] = True
        return True
    ir = ir_compiler.load_ir(ir_file)

    check_routes(doc, ir, report, loc)
    mapping, mapping_loc = read_status_mapping(root)
    check_statuses(doc, mapping, report, loc, mapping_loc)

    # `ApiContractFirst: false` autorise la divergence de SCHÉMAS — jamais celle
    # des routes ni des statuts : une route non soutenue reste une surface
    # d'attaque, que la divergence soit assumée ou non.
    if not bool(config.get("ApiContractFirst", True)):
        report.warn(
            "API_CONTRACT_DRIFT",
            "`ApiContractFirst: false` — confrontation des schémas désactivée",
            "exige un ADR référencé (validate_packaging.py le dit aussi) ; les routes "
            "et les statuts restent vérifiés",
            "workspace/stack/STACK.md ## Project Config",
        )
    else:
        agent = entry_agent(ir)
        if agent is None:
            report.warn(
                "API_CONTRACT_DRIFT",
                "le nœud d'entrée de l'IR n'est pas un agent : schémas du système non dérivables",
                "faire porter l'entrée par un nœud `kind: agent`, ou déclarer explicitement "
                "les schémas du système. La gate ne devine pas un contrat",
                loc,
            )
        else:
            report.data["entryAgent"] = agent.get("id")
            check_schema_drift(doc, agent, report, loc)

    report.data["applicable"] = True
    return True


def explain() -> int:
    print("\nAPI GATE (G6, part `api`) — ce qui est confronté\n" + "-" * 62)
    print("  IR .agents[entry].inputSchema   <->  RunRequest.input")
    print("  IR .agents[entry].outputSchema  <->  RunResponse.output")
    print("  IR .orchestration.humanInTheLoop <-> présence de /v1/runs/{}/resume")
    print("\nRoutes au contrat de la surface\n" + "-" * 62)
    for route in sorted(SURFACE_ROUTES):
        mark = "  (conditionnée par l'IR)" if route in CONDITIONAL_ROUTES else ""
        print(f"  {route}{mark}")
    print("\nNon applicable tant qu'aucun openapi.json n'est publié sous workspace/src/.")
    print("Aucun rapport n'est alors écrit : une part absente ne bloque pas,")
    print("mais elle n'accorde aucun vert non plus.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="API GATE — l'OpenAPI publié est dérivé de l'IR (0 token).")
    add_common_args(parser)
    parser.add_argument("--mission", default="1", help="numéro de MISSION")
    parser.add_argument("--explain", action="store_true", help="ce que la gate confronte")
    args = parser.parse_args(argv)

    if args.explain:
        return explain()

    root = resolve_root(args)
    report = Report(name="G6.api", target=str(root))
    applicable = run(root, str(args.mission), report)

    if not applicable:
        if not args.json:
            print("⚪ API GATE — non applicable : aucun openapi.json publié sous workspace/src/")
        return finish(report, args) if args.json else 0

    if not args.no_report:
        write_gate_report(root, "G6", str(args.mission), report, {}, part="api")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
