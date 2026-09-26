#!/usr/bin/env python3
"""Garde de l'étage C : la cible des attaques est-elle un environnement de TEST ?

`review-adversarial` attaque le système vivant — injections, abus d'outil,
franchissement de tenant, épuisement de budget. Sur un environnement de test,
une attaque réussie est un item permanent du jeu adversarial ; sur la
production, c'est un incident que la revue a causé. Ce script tranche AVANT la
première attaque, et il **échoue fermé** : ce qu'il ne peut pas prouver local
ou isolé est refusé, jamais supposé.

Ce qui est vérifié (0 token, aucune connexion ouverte, aucune VALEUR de secret
lue — seulement des noms) :

    surface      la surface active (`## Active Serving Surface`) : une CLI ou un
                 lot tournent en processus local ; une surface HTTP exige
                 `--endpoint` sur une adresse de bouclage (127.0.0.0/8, ::1,
                 localhost), ou `--executor module:attr` en processus
    variables    aucun NOM de variable de connexion « de production »
                 (`*_PROD_*`, `PRODUCTION`, `LIVE`) cité par STACK.md ni déclaré
                 dans les `.env` (noms seulement)
    base         une stratégie SQL n'est admise que si ses variables de connexion
                 portent un nom de test distinct (`DB_HOST_TEST`, `*_SANDBOX`…) :
                 `DB_HOST` seul ne dit pas où il pointe, donc il ne prouve rien
    sources      stores `local` sous le projet (assets/, pipeline/fixtures/) ;
                 partages réseau refusés ; stores distants, serveurs MCP et API
                 externes admis seulement sur une adresse de bouclage
    index        un vector store `dedicated` sur un endpoint de bouclage
    outils       chaque outil non `read-only` de l'IR a une fixture de mock
                 (`pipeline/fixtures/tools/*.jsonl`, ce que charge
                 `mocked_toolset`) ; `dryRunSupported` n'est admis qu'avec
                 `--allow-dry-run`, et reste signalé : un dry-run déclaré n'est
                 pas un dry-run prouvé

Tout refus porte `[ADVERSARIAL_TARGET_UNSAFE]` et rend exit 1. Une erreur
inattendue du contrôle lui-même rend AUSSI exit 1 : une garde qui plante et
laisse passer n'est pas une garde. La preuve est écrite dans
`workspace/.sys/.validation/adversarial-target-{n}.json`.

Usage :
    python .sdda/sdda.py adversarial-target-check --mission 1
    python .sdda/sdda.py adversarial-target-check --mission 1 --endpoint http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import eval_reports, markdown_io, paths, source_registry as sr  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.layered_config import active_stacks, app_name, read_stack_section_kv  # noqa: E402
from sdda_lib.runtime_io import atomic_write_json, now_iso  # noqa: E402
from sdda_scripts import validate_data_access  # noqa: E402
from sdda_scripts._common import add_common_args, finish, resolve_root  # noqa: E402

CLS_UNSAFE = "ADVERSARIAL_TARGET_UNSAFE"
CLS_DRY_RUN = "ADVERSARIAL_DRY_RUN_UNVERIFIED"
CLS_MOCK_UNWIRED = "ADVERSARIAL_MOCK_NOT_WIRED"

#: Surfaces qui tournent en processus local, lancées par le runner d'eval.
LOCAL_SURFACES = frozenset({"cli", "cli-dotnet", "cli-node", "cli-kotlin", "batch"})

#: Exécuteurs du squelette qui lancent l'application LIVRÉE en sous-processus :
#: ils ne sont pas « en processus », et ils n'installent aucun mock d'outil.
SUBPROCESS_EXECUTORS = frozenset({"CliExecutor"})

_PROD_RE = re.compile(r"(?:^|_)(PROD|PRODUCTION|LIVE)(?:_|$)")
_TEST_RE = re.compile(r"(?:^|_)(TEST|TESTS|SANDBOX|LOCAL|FIXTURE|FIXTURES|MOCK|DEV|CI)(?:_|$)")
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ENV_KEY_RE = re.compile(r"\b[\w]*(?:_env|Env)\s*:\s*\$?\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
_DB_LINE_RE = re.compile(r"^\s*-\s*(DB_[A-Z0-9_]+)\s*:\s*\$\{([A-Za-z_][A-Za-z0-9_]*)\}", re.M)


def is_loopback(url_or_host: str) -> bool:
    """`http://127.0.0.1:8000`, `localhost`, `[::1]` : oui. `0.0.0.0` écoute partout : non."""
    text = str(url_or_host or "").strip()
    if "://" in text:
        host = urlparse(text).hostname
    elif text.startswith("["):
        # `[::1]` ou `[::1]:8000` : couper au premier `:` rendait `[`, donc un
        # hôte vide, donc un refus de la boucle locale IPv6 que la docstring admet.
        host = text[1:].split("]", 1)[0]
    elif text.count(":") > 1:
        host = text  # IPv6 nue (`::1`) : les `:` font partie de l'adresse
    else:
        host = text.split(":")[0]
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def stack_lines(root: Path) -> str:
    """STACK.md sans ses commentaires : un exemple commenté n'est pas une déclaration."""
    out: list[str] = []
    for line in markdown_io.read_text(paths.stack_md_path(root)).split("\n"):
        if line.lstrip().startswith("#"):
            continue
        out.append(line.split(" #", 1)[0])
    return "\n".join(out)


def referenced_names(text: str) -> set[str]:
    return set(_VAR_RE.findall(text)) | set(_ENV_KEY_RE.findall(text))


def env_file_names(root: Path) -> dict[str, set[str]]:
    """Noms déclarés dans les `.env` (assets/ et livrable) — les NOMS, jamais les valeurs."""
    out: dict[str, set[str]] = {}
    for path in (paths.env_source_path(root), paths.env_path(root, app_name(root))):
        if path.is_file():
            out[paths.rel(root, path)] = sr.env_names(path)
    return out


# ---------------------------------------------------------------------------
# Contrôles
# ---------------------------------------------------------------------------
def check_surface(root: Path, endpoint: str | None, executor: str | None, report: Report) -> dict[str, Any]:
    surfaces = active_stacks(root, "Active Serving Surface")
    loc = "workspace/stack/STACK.md ## Active Serving Surface"
    out: dict[str, Any] = {"surfaces": surfaces, "endpoint": endpoint, "executor": executor}
    if endpoint is not None:
        out["endpointLocal"] = is_loopback(endpoint)
        if not out["endpointLocal"]:
            report.error(CLS_UNSAFE, f"`--endpoint {endpoint}` n'est pas une adresse de bouclage",
                         "lancer le système en local (127.0.0.1) et attaquer cette instance ; jamais un hôte partagé", loc)
        return out
    if executor:
        # Seul un exécuteur EN PROCESSUS est local par construction. Toute
        # chaîne suffisait naguère — y compris `…:CliExecutor`, qui lance
        # l'application livrée en sous-processus, avec sa configuration réelle :
        # celui-là est jugé comme la surface qu'il lance. `cli` et `cmd:` sont
        # l'exécuteur générique en sous-processus (tout langage) : même règle.
        if executor == "cli" or executor.startswith("cmd:"):
            out["inProcess"] = False
        elif ":" not in executor or not executor.rsplit(":", 1)[1].strip():
            report.error(CLS_UNSAFE, f"`--executor {executor}` : attendu `cli`, `cmd:<commande>` ou `module:attr`",
                         "désigner l'exécuteur réellement chargé par run-adversarial-suite", loc)
            return out
        elif executor.rsplit(":", 1)[1] not in SUBPROCESS_EXECUTORS:
            out["inProcess"] = True           # l'exécuteur importé tourne dans CE processus
            return out
        else:
            out["inProcess"] = False
    if not surfaces:
        report.error(CLS_UNSAFE, "aucune surface active : impossible de dire ce que l'attaque vise",
                     "activer une surface dans STACK.md, ou passer --executor module:attr", loc)
    remote = [s for s in surfaces if s not in LOCAL_SURFACES]
    if remote:
        report.error(CLS_UNSAFE, f"surface(s) réseau {remote} sans `--endpoint` : la cible n'est pas prouvée locale",
                     "passer `--endpoint http://127.0.0.1:{port}` (instance de test lancée pour la revue) ou `--executor`", loc)
    return out


def check_names(root: Path, text: str, report: Report) -> dict[str, Any]:
    names = referenced_names(text)
    envs = env_file_names(root)
    prod_ref = sorted(n for n in names if _PROD_RE.search(n.upper()))
    prod_env = {f: sorted(n for n in ns if _PROD_RE.search(n.upper())) for f, ns in envs.items()}
    for name in prod_ref:
        report.error(CLS_UNSAFE, f"STACK.md cite la variable `{name}` : son nom désigne la production",
                     "pointer la revue sur une configuration de test (variables `*_TEST`) ; la production n'est jamais une cible", "workspace/stack/STACK.md")
    for f, bad in prod_env.items():
        if bad:
            report.error(CLS_UNSAFE, f"`{f}` déclare {bad} : des connexions de production sont renseignées dans l'environnement de la revue",
                         "retirer ces variables de l'environnement de test — leurs VALEURS n'ont pas été lues", f)
    return {"referenced": sorted(names), "envFiles": {f: len(ns) for f, ns in envs.items()}, "productionNames": prod_ref}


def check_database(root: Path, text: str, report: Report) -> dict[str, Any]:
    strategy = validate_data_access.active_strategy(root)
    out: dict[str, Any] = {"strategy": strategy}
    if strategy not in validate_data_access.SQL_STRATEGIES:
        return out
    db_vars = dict(_DB_LINE_RE.findall(text))
    out["connectionVars"] = db_vars
    untested = sorted(v for v in db_vars.values() if not _TEST_RE.search(v.upper()))
    if not db_vars or untested:
        report.error(CLS_UNSAFE, f"stratégie `{strategy}` : la connexion ({untested or 'aucune variable DB_*'}) ne porte aucun nom de test — "
                     "rien ne prouve que la base visée n'est pas la production",
                     "déclarer une connexion de test distincte (`DB_HOST: ${DB_HOST_TEST}`…) pour la revue, ou attaquer sur fixtures",
                     "workspace/stack/STACK.md ## Active Data Access")
    return out


def check_sources(root: Path, report: Report) -> dict[str, Any]:
    section = read_stack_section_kv(root, "Active Data Sources")
    if validate_data_access.active_strategy(root) != validate_data_access.DECLARED:
        return {"stores": []}
    registry = sr.load_registry(root, section)
    workspace = paths.workspace(root).resolve()
    stores: list[dict[str, Any]] = []
    loc = "workspace/stack/STACK.md ## Active Data Sources"
    for sid, store in sorted(registry.stores.items()):
        kind = str(store.get("kind") or "")
        verdict = "ok"
        if kind == "local":
            base = Path(str(store.get("root") or ""))
            resolved = (base if base.is_absolute() else root / base).resolve()
            if not resolved.is_relative_to(workspace):
                verdict = "outside-project"
                report.error(CLS_UNSAFE, f"store `{sid}` : `root` hors du projet ({resolved})",
                             "copier un extrait de test sous workspace/assets/ ou workspace/pipeline/fixtures/", loc)
        elif kind in ("smb", "nfs"):
            verdict = "network-share"
            report.error(CLS_UNSAFE, f"store `{sid}` : partage réseau `{kind}` — la donnée réelle, pas une fixture",
                         "remplacer par un store `local` sur un extrait de test", loc)
        elif kind == "mcp":
            url = str(store.get("url") or "")
            if url and not is_loopback(url):
                verdict = "remote"
                report.error(CLS_UNSAFE, f"store `{sid}` : serveur MCP distant `{sr.store_host(store) or url}`",
                             "lancer le serveur MCP de test en local (stdio ou 127.0.0.1)", loc)
        else:
            host = sr.store_host(store) or ""
            if not is_loopback(host):
                verdict = "remote"
                report.error(CLS_UNSAFE, f"store `{sid}` ({kind}) : hôte `{host or '?'}` non local",
                             "pointer le store sur un bouchon local (127.0.0.1) ou un bucket de test déclaré comme tel", loc)
        stores.append({"id": sid, "kind": kind, "verdict": verdict})
    return {"stores": stores}


def check_integrations(root: Path, report: Report) -> dict[str, Any]:
    section = read_stack_section_kv(root, "Active Tools & Integrations")
    out: dict[str, Any] = {"mcpServers": [], "externalApis": []}
    loc = "workspace/stack/STACK.md ## Active Tools & Integrations"
    for srv in section.get("MCPServers") or []:
        if not isinstance(srv, dict):
            continue
        url = str(srv.get("url") or "")
        local = not url or is_loopback(url)
        out["mcpServers"].append({"name": srv.get("name"), "local": local})
        if not local:
            report.error(CLS_UNSAFE, f"serveur MCP `{srv.get('name')}` distant ({urlparse(url).hostname})",
                         "le lancer en local (stdio) ou le remplacer par un mock qui compte les appels", loc)
    for api in section.get("ExternalAPIs") or []:
        if not isinstance(api, dict):
            continue
        url = str(api.get("base_url") or "")
        local = is_loopback(url)
        out["externalApis"].append({"name": api.get("name"), "local": local})
        if not local:
            report.error(CLS_UNSAFE, f"API externe `{api.get('name')}` sur `{urlparse(url).hostname or url}`",
                         "basculer ExternalAPIs vers un bouchon local (127.0.0.1) avant tout run adversarial", loc)
    vs = read_stack_section_kv(root, "Active Retrieval Stack").get("VectorStoreConnection")
    if isinstance(vs, dict) and str(vs.get("Mode") or "").strip() == "dedicated" and str(vs.get("Endpoint") or "").strip():
        local = is_loopback(str(vs["Endpoint"]))
        out["vectorStoreLocal"] = local
        if not local:
            report.error(CLS_UNSAFE, f"index vectoriel dédié sur `{urlparse(str(vs['Endpoint'])).hostname}` : l'injection indirecte exige un index de TEST",
                         "construire un index de test local et y pointer `VectorStoreConnection.Endpoint`",
                         "workspace/stack/STACK.md ## Active Retrieval Stack")
    return out


def tool_fixtures(directory: Path) -> set[str]:
    names: set[str] = set()
    # `rglob`, comme `mocked_toolset` qui charge `fixtures/tools/**/*.jsonl` : une
    # fixture rangée par outil (`tools/{outil}/x.jsonl`) était chargée par le
    # mock et invisible pour la garde, qui refusait un outil pourtant mocké.
    for path in sorted(directory.rglob("*.jsonl")) if directory.is_dir() else ():
        for line in markdown_io.read_text(path).split("\n"):
            try:
                entry = json.loads(line) if line.strip() else None
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("tool"):
                names.add(str(entry["tool"]))
    return names


def check_tools(root: Path, ir: dict[str, Any], fixtures_dir: Path, allow_dry_run: bool, report: Report,
                *, subprocess_executor: bool = False) -> list[dict[str, Any]]:
    mocked = tool_fixtures(fixtures_dir)
    out: list[dict[str, Any]] = []
    if subprocess_executor and any(str(t.get("sideEffectClass") or "") not in ("", "read-only") for t in ir.get("tools") or []):
        # Une fixture présente prouve qu'un mock EXISTE, pas que l'exécuteur le
        # charge : `CliExecutor` lance l'application livrée, avec ses vrais outils.
        # Tant que la surface ne sait pas recevoir l'isolement, le dire.
        report.warn(CLS_MOCK_UNWIRED, "exécuteur en sous-processus : les fixtures de mock ne sont PAS câblées dans l'application attaquée — "
                    "les outils à effet de bord répondront en réel",
                    "attaquer par un exécuteur en processus isolé (`InProcessExecutor` + suite `isolation: mocked`), "
                    "ou sur une configuration dont les outils pointent un bouchon local", paths.rel(root, fixtures_dir))
    for tool in ir.get("tools") or []:
        cls = str(tool.get("sideEffectClass") or "")
        if cls == "read-only":
            continue
        tid, name = str(tool.get("id") or ""), str(tool.get("name") or "")
        dry = bool((tool.get("safetyStrategy") or {}).get("dryRunSupported"))
        if {tid, name} & mocked:
            verdict = "mocked"
        elif dry and allow_dry_run:
            verdict = "dry-run-declared"
            report.warn(CLS_DRY_RUN, f"outil `{tid}` ({cls}) admis en dry-run DÉCLARÉ, sans mock qui compte",
                        f"ajouter une fixture `{{\"tool\": \"{name}\", …}}` sous {paths.rel(root, fixtures_dir)} : un mock prouve, un drapeau affirme", tid)
        else:
            verdict = "live"
            report.error(CLS_UNSAFE, f"outil `{tid}` ({cls}) ni mocké ni en dry-run" + (" (dryRunSupported, mais --allow-dry-run absent)" if dry else ""),
                         f"déposer sa fixture sous {paths.rel(root, fixtures_dir)} (mocked_toolset), ou relancer avec --allow-dry-run si le contrat le supporte", tid)
        out.append({"tool": tid, "sideEffectClass": cls, "verdict": verdict})
    return out


def run(root: Path, ir: dict[str, Any], *, endpoint: str | None = None, executor: str | None = None,
        fixtures_dir: Path | None = None, allow_dry_run: bool = False) -> tuple[Report, dict[str, Any]]:
    report = Report(name="ADVERSARIAL-TARGET", target=str(ir.get("missionId") or root))
    fixtures_dir = fixtures_dir or paths.pipeline_dir(root) / "fixtures" / "tools"
    payload: dict[str, Any] = {"missionId": ir.get("missionId"), "generatedAt": now_iso()}
    try:
        text = stack_lines(root)
        payload["surface"] = check_surface(root, endpoint, executor, report)
        payload["names"] = check_names(root, text, report)
        payload["database"] = check_database(root, text, report)
        payload["sources"] = check_sources(root, report)
        payload["integrations"] = check_integrations(root, report)
        payload["tools"] = check_tools(root, ir, fixtures_dir, allow_dry_run, report,
                                       subprocess_executor=payload["surface"].get("inProcess") is False)
    except Exception as exc:  # noqa: BLE001 — une garde qui plante doit refuser, pas laisser passer
        report.error(CLS_UNSAFE, f"contrôle interrompu ({type(exc).__name__}: {exc}) : la cible n'est pas prouvée isolée",
                     "corriger la déclaration illisible puis relancer ; aucune attaque d'ici là")
    payload["safe"] = report.ok
    return report, payload


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Garde de l'étage C : la cible adversariale est-elle isolée ? Échoue fermé (0 token)")
    p.add_argument("--mission", type=int, default=None, help="numéro de MISSION ; défaut : l'unique IR compilé")
    p.add_argument("--endpoint", default=None, help="URL de l'instance attaquée (doit être une adresse de bouclage)")
    p.add_argument("--executor", default=None, help="`module:attr` : exécuteur en processus, local par construction")
    p.add_argument("--tool-fixtures", type=Path, default=None, help="fixtures de mocks d'outils ; défaut : workspace/pipeline/fixtures/tools")
    p.add_argument("--allow-dry-run", action="store_true", help="admettre (en avertissement) un outil `dryRunSupported` sans mock")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="ADVERSARIAL-TARGET", target=str(root))
    if not paths.stack_md_path(root).is_file():
        report.error(CLS_UNSAFE, "STACK.md introuvable : rien ne dit ce que l'attaque viserait", "bootstrap du projet d'abord", "workspace/stack/STACK.md")
        return finish(report, args)
    ir_file, why = eval_reports.find_ir_file(root, args.mission)
    if ir_file is None:
        report.error("IR_NOT_FOUND", f"{why} — sans IR, les outils à effet de bord ne sont pas connus : refus", "compiler l'IR : python .sdda/sdda.py ir-compiler --mission {n}")
        return finish(report, args)
    try:
        ir = json.loads(markdown_io.read_text(ir_file))
    except ValueError:
        report.error(CLS_UNSAFE, f"IR illisible ({paths.rel(root, ir_file)}) : refus", "recompiler l'IR")
        return finish(report, args)
    fixtures = args.tool_fixtures if args.tool_fixtures is None or args.tool_fixtures.is_absolute() else root / args.tool_fixtures
    sub, payload = run(root, ir, endpoint=args.endpoint, executor=args.executor, fixtures_dir=fixtures, allow_dry_run=args.allow_dry_run)
    report.extend(sub)
    report.target = sub.target
    payload["findings"] = {"errors": [f.to_dict() for f in report.errors], "warnings": [f.to_dict() for f in report.warnings]}
    if not args.no_report:
        out = paths.validation_dir(root) / f"adversarial-target-{eval_reports.mission_number(ir) or 'system'}.json"
        atomic_write_json(out, payload)
        report.data["written"] = paths.rel(root, out)
    report.data["safe"] = payload["safe"]
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
