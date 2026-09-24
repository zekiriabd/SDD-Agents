#!/usr/bin/env python3
"""ADR — chaque décision qui en exige un est couverte par un ADR accepté (0 token).

Part `adr` de G2 : une décision d'architecture qui contredit un défaut du
framework (`DbAgentRole: full`, `ApiContractFirst: false`, `TlsVerify: false`,
le pattern `network`…) se décide PAR ÉCRIT, avant l'implémentation.

Ce que ce script remplace, et pourquoi :

- Les exigences étaient codées en dur dans `human_tasks.py` — cinq règles
  qu'aucun autre script ne connaissait, et que les documents qui les
  énonçaient (DATA-ACCESS.md, INVARIANTS.yml, le gabarit) ne pouvaient pas
  étendre. Elles vivent désormais dans `registry/adr-requirements.yml`.
- Elles étaient des tâches NON bloquantes : rien ne refusait de construire une
  base en écriture sans ADR. La part `adr` est rouge tant qu'une exigence
  active n'est pas couverte, et le rouge d'une part contributive bloque G2.
- Un ADR « couvrait » une exigence dès qu'il contenait la clé OU la valeur en
  sous-chaîne : un ADR qui écrivait « false » n'importe où couvrait toutes les
  décisions booléennes du projet. Un ADR couvre désormais une exigence
  seulement s'il la NOMME dans une ligne structurée, et s'il est accepté :

      Status: Accepted
      Covers: DbAgentRole=scoped-write, VectorStoreConnection.TlsVerify=false

Classes :
    [ADR_MISSING]            exigence active, aucun ADR ne la couvre     (bloquant)
    [ADR_NOT_ACCEPTED]       un ADR la couvre, mais n'est pas `Accepted` (bloquant)
    [ADR_COVERS_MALFORMED]   ligne `Covers:` illisible                    (avertissement)

Usage :
    python .sdda/sdda.py validate-adr --mission 1 [--json] [--no-report]
    python .sdda/sdda.py validate-adr --explain      # le registre, lisible
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io, paths, yaml_mini  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.layered_config import LayeredConfig, active_stacks, read_stack_section_kv  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, load_config, resolve_root  # noqa: E402

#: Le registre, résolu depuis le framework qui s'exécute.
REGISTRY = Path(__file__).resolve().parents[2] / "registry" / "adr-requirements.yml"

#: `Covers: A=b, C.d=e` — une ligne, n'importe où dans l'ADR.
_COVERS_RE = re.compile(r"^\s*Covers\s*:\s*(.*?)\s*(?:#.*)?$", re.M | re.I)
_STATUS_RE = re.compile(r"^\s*Status\s*:\s*([A-Za-z-]+)", re.M | re.I)
_ITEM_RE = re.compile(r"^([A-Za-z][\w.&-]*)\s*=\s*(\S+)$")

#: Une catégorie de stack est une clé d'exigence quand la section est un `## Active *` de fiches.
COMPONENT_SECTIONS = {"Active Orchestration Pattern", "Active RAG Pattern", "Active Data Access",
                      "Active Memory Strategy", "Active Serving Surface", "Active Agent Framework"}


@dataclass(frozen=True)
class Requirement:
    id: str
    key: str
    section: str
    values: tuple[str, ...]
    source: str
    why: str
    only_if: tuple[dict[str, Any], ...] = ()


@dataclass
class Adr:
    path: Path
    status: str
    covers: set[tuple[str, str]] = field(default_factory=set)
    malformed: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


def load_requirements(path: Path = REGISTRY) -> list[Requirement]:
    data = yaml_mini.parse_mapping(markdown_io.read_text(path))
    out = []
    for raw in data.get("requirements") or []:
        out.append(Requirement(
            id=str(raw["id"]), key=str(raw["key"]), section=str(raw["section"]),
            values=tuple(_norm(v) for v in raw.get("values") or []),
            source=str(raw.get("source") or ""), why=" ".join(str(raw.get("why") or "").split()),
            only_if=tuple(raw.get("onlyIf") or ()),
        ))
    return out


def _norm(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value).strip().lower()


def current_value(root: Path, config: LayeredConfig, key: str, section: str) -> str | None:
    """La valeur active de `key` dans `section` — `None` si elle n'est pas déclarée.

    Composant (`orchestration` sous `## Active Orchestration Pattern`) : la fiche
    activée. Sous-clé (`VectorStoreConnection.TlsVerify`) : descente dans le mapping.
    """
    if section in COMPONENT_SECTIONS and "." not in key and key[:1].islower():
        active = active_stacks(root, section)
        return _norm(active[0]) if len(active) == 1 else None
    if section == "Project Config":
        node: Any = config.config
    else:
        node = read_stack_section_kv(root, section)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return _norm(node)


def _condition_holds(root: Path, config: LayeredConfig, cond: dict[str, Any]) -> bool:
    value = current_value(root, config, str(cond.get("key")), str(cond.get("section") or "Project Config"))
    value = "" if value is None else value
    if "in" in cond:
        return value in {_norm(v) for v in cond.get("in") or []}
    if "notIn" in cond:
        return value not in {_norm(v) for v in cond.get("notIn") or []}
    return True


def active_requirements(root: Path, config: LayeredConfig,
                        requirements: list[Requirement] | None = None) -> list[tuple[Requirement, str]]:
    """[(exigence, valeur active)] pour chaque décision de STACK.md qui exige un ADR."""
    out = []
    for req in requirements if requirements is not None else load_requirements():
        value = current_value(root, config, req.key, req.section)
        if value is None or value not in req.values:
            continue
        if all(_condition_holds(root, config, c) for c in req.only_if):
            out.append((req, value))
    return out


def parse_adr(path: Path) -> Adr:
    text = markdown_io.read_text(path)
    status = (_STATUS_RE.search(text).group(1).lower() if _STATUS_RE.search(text) else "")
    adr = Adr(path=path, status=status)
    for line in _COVERS_RE.findall(text):
        for item in re.split(r"[,;]", line):
            item = item.strip().strip("`")
            if not item or item.startswith("<"):
                continue                               # gabarit non rempli
            m = _ITEM_RE.match(item)
            if m:
                adr.covers.add((m.group(1).lower(), _norm(m.group(2).strip("`"))))
            else:
                adr.malformed.append(item)
    return adr


def adr_files(root: Path) -> list[Path]:
    d = paths.decisions_dir(root)
    return sorted(d.glob("ADR-*.md")) if d.is_dir() else []


def covering(adrs: list[Adr], req: Requirement, value: str) -> list[Adr]:
    """Les ADR qui NOMMENT l'exigence — clé ET valeur, dans une ligne `Covers:`."""
    wanted = (req.key.lower(), value)
    return [a for a in adrs if wanted in a.covers]


def uncovered(root: Path, config: LayeredConfig) -> list[tuple[Requirement, str, list[Adr]]]:
    """[(exigence, valeur, ADR non acceptés qui la nomment)] pour chaque exigence sans ADR accepté."""
    adrs = [parse_adr(p) for p in adr_files(root)]
    out = []
    for req, value in active_requirements(root, config):
        found = covering(adrs, req, value)
        if not any(a.accepted for a in found):
            out.append((req, value, found))
    return out


def run(root: Path, report: Report) -> Report:
    config = load_config(root, report)
    adrs = [parse_adr(p) for p in adr_files(root)]
    active = active_requirements(root, config)
    report.data["requirements"] = [f"{r.key}={v}" for r, v in active]
    report.data["adrs"] = [paths.rel(root, a.path) for a in adrs]
    for a in adrs:
        for item in a.malformed:
            report.warn("ADR_COVERS_MALFORMED", f"`{a.path.name}` : entrée `Covers:` illisible « {item} »",
                        "écrire `Covers: Clé=valeur, Autre.Clé=valeur`", paths.rel(root, a.path))
    decisions = paths.rel(root, paths.decisions_dir(root))
    for req, value in active:
        found = covering(adrs, req, value)
        where = f"workspace/stack/STACK.md ## {req.section}"
        if any(a.accepted for a in found):
            continue
        if found:
            report.error("ADR_NOT_ACCEPTED",
                         f"`{req.key}: {value}` est couvert par {', '.join(a.path.name for a in found)} "
                         f"(Status: {found[0].status or 'absent'}), pas par un ADR accepté",
                         "un ADR `Proposed` n'autorise rien : le faire accepter (Status: Accepted) avant "
                         "l'implémentation, ou revenir à la valeur par défaut", where)
        else:
            report.error("ADR_MISSING",
                         f"`{req.key}: {value}` exige un ADR — {req.why} (règle {req.id} : {req.source})",
                         f"écrire `{decisions}/ADR-{{YYYYMMDDTHHMM}}-{{slug}}.md` depuis "
                         f".sdda/templates/adr.template.md avec `Status: Accepted` et "
                         f"`Covers: {req.key}={value}` ; ou revenir à la valeur par défaut", where)
    return report


def explain() -> int:
    print("\nDécisions qui exigent un ADR (registry/adr-requirements.yml)\n" + "-" * 62)
    for r in load_requirements():
        cond = " · si " + ", ".join(f"{c.get('key')} {'∈' if 'in' in c else '∉'} {c.get('in') or c.get('notIn')}"
                                    for c in r.only_if) if r.only_if else ""
        print(f"  {r.key} ∈ {list(r.values)}  (## {r.section}){cond}\n      {r.why}\n      source : {r.source}")
    print("\nUn ADR couvre une exigence s'il porte `Status: Accepted` et `Covers: Clé=valeur`.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="Chaque décision qui exige un ADR est couverte par un ADR accepté.")
    add_common_args(parser)
    parser.add_argument("--explain", action="store_true", help="liste les décisions qui exigent un ADR")
    parser.add_argument("--mission", default="0", help="numéro de MISSION (artefact du rapport de gate)")
    args = parser.parse_args(argv)
    if args.explain:
        return explain()
    root = resolve_root(args)
    report = run(root, Report(name="ADR", target=str(root)))
    if not args.no_report:
        # Part de G2 : une décision d'architecture se tranche avant la
        # première ligne de code, au même titre que le packaging.
        write_gate_report(root, "G2", str(args.mission), report, {}, part="adr")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
