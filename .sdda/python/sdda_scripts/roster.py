#!/usr/bin/env python3
"""Le manifeste de roster — gabarit pré-rempli, puis vérification (0 token, PHILOSOPHY P7).

Le roster est la décision d'architecture : combien d'agents, lesquels, qui porte
quelle CAP, avec quels outils et quel tier. C'est l'**architecte** qui l'écrit ;
ce script fait les deux choses qu'un humain n'a pas à faire lui-même :

    scaffold   écrit `workspace/stack/topology/{n}-roster.yml` depuis la MISSION,
               les CAPs et le pattern actif de STACK.md — tout ce qui se DÉRIVE
               est pré-rempli, tout ce qui se DÉCIDE reste `<à préciser>`.
    validate   vérifie que la déclaration est complète pour le pattern actif
               (registry/architecture-requirements.yml), que chaque CAP est
               portée par exactement un agent déclaré, que chaque subagent sert
               une CAP ou dit pourquoi il existe, qu'aucun trou ne reste et
               qu'aucune API de framework n'y est nommée (P11).

Emplacement : la convention de `STACK.md ## Active Agent Topology` —
`RosterManifestRoot` (défaut `workspace/stack/topology`) + `{n}-roster.yml`. C'est
le chemin que `validate_architecture.py` lit en G2 ; `workspace/stack/` est la zone
de l'humain (ownership.md), là où `workspace/feats/topology/` appartient à
`architect-topology`.

Aucun rapport de gate n'est écrit : `roster` n'est pas une part connue de G2
(`gate_reports.GATE_PARTS_ADVISORY`), et un rapport que la machine à états ignore
est un faux vert qui attend. La commande lit le code de sortie / le JSON.

Usage :
    python .sdda/sdda.py roster scaffold --mission 1 [--force --reason "…"]
    python .sdda/sdda.py roster validate --mission 1 [--json] [--if-present]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import markdown_io, paths, yaml_mini  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import append_bypass_audit  # noqa: E402
from sdda_lib.layered_config import active_stacks, read_stack_section_kv  # noqa: E402
from sdda_scripts import validate_architecture as va  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402
from sdda_scripts.audit_bypass import is_real_reason  # noqa: E402
from sdda_scripts.validate_ir import framework_leaks  # noqa: E402
from sdda_scripts.validate_mission import parse_mission  # noqa: E402

DEFAULT_MANIFEST_ROOT = "workspace/stack/topology"
PLACEHOLDER = "<à préciser>"
BYPASS_NAME = "ROSTER_SCAFFOLD_FORCE"
ORCHESTRATION_SECTION = "Active Orchestration Pattern"

_PLACEHOLDER_RE = re.compile(r"<\s*[àa]\s+pr[ée]ciser[^>]*>", re.IGNORECASE)
_ANGLE_RE = re.compile(r"^<[^>]*>$")


# ---------------------------------------------------------------------------
# Localisation
# ---------------------------------------------------------------------------
def manifest_path(root: Path, mission: int | str) -> Path:
    """Le chemin du manifeste de la MISSION — même résolution que `validate_architecture`.

    `RosterManifests[]` de STACK.md prime s'il nomme un fichier de cette mission ;
    sinon la convention `{n}-roster.yml` sous `RosterManifestRoot`.
    """
    section = read_stack_section_kv(root, "Active Agent Topology")
    declared_root = str(section.get("RosterManifestRoot") or DEFAULT_MANIFEST_ROOT).strip()
    base = Path(declared_root)
    manifest_root = base if base.is_absolute() else root / base
    listed = section.get("RosterManifests")
    if isinstance(listed, list):
        for entry in listed:
            rel = entry.get("path") if isinstance(entry, dict) else entry
            if rel and Path(str(rel)).name.startswith(f"{mission}-"):
                return manifest_root / str(rel)
    return manifest_root / f"{mission}-roster.yml"


def find_mission(root: Path, mission: int | str, report: Report):
    """La MISSION `{n}-*.md`, parsée. None (et un finding) si absente ou ambiguë."""
    found = sorted(paths.missions_dir(root).glob(f"{mission}-*.md"))
    if not found:
        report.error("MISSION_NOT_FOUND", f"aucune MISSION `{mission}-*.md` dans workspace/feats/missions/",
                     fix="créer la MISSION : /sdda-mission {Name}", location=paths.rel(root, paths.missions_dir(root)))
        return None
    if len(found) > 1:
        report.error("MISSION_AMBIGUOUS", f"{len(found)} fichiers `{mission}-*.md` : {[p.name for p in found]}",
                     fix="un seul fichier par numéro de MISSION", location=paths.rel(root, paths.missions_dir(root)))
        return None
    return parse_mission(markdown_io.read_text(found[0]), found[0])


def active_pattern(root: Path, report: Report) -> str:
    """Le pattern d'orchestration actif de STACK.md ; "" (et un finding) s'il n'est pas unique."""
    names = active_stacks(root, ORCHESTRATION_SECTION)
    if len(names) == 1:
        return names[0]
    if not names:
        report.error("STACK_MALFORMED", f"`## {ORCHESTRATION_SECTION}` n'active aucun pattern",
                     fix="activer exactement une fiche `.sdda/stacks/orchestration/*.md`", location="workspace/stack/STACK.md")
    else:
        report.error("STACK_MALFORMED", f"`## {ORCHESTRATION_SECTION}` active plusieurs patterns : {names}",
                     fix="exactement un", location="workspace/stack/STACK.md")
    return ""


def cap_ids(root: Path, mission: int | str) -> list[str]:
    return sorted(p.stem for p in paths.caps_dir(root).glob(f"{mission}-*.md"))


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------
def _agent_block(indent: str) -> str:
    return (
        f"{indent}- id: {PLACEHOLDER}\n"
        f"{indent}  role: {PLACEHOLDER}\n"
        f"{indent}  responsibilities: {PLACEHOLDER}\n"
        f"{indent}  tools: []                     # liste explicite ; [] = « aucun », et c'est une décision\n"
        f"{indent}  skills: []                    # ce qu'il SAIT FAIRE (implémenté par dev-prompt)\n"
        f"{indent}  rules: []                     # ce qu'il NE PEUT PAS enfreindre\n"
        f"{indent}  tier: {PLACEHOLDER}              # fast | balanced | deep\n"
        f"{indent}  model:                        # optionnel — prime sur le tier s'il est renseigné\n"
        f"{indent}  # reason: <pourquoi cet agent existe s'il ne porte aucune CAP>\n"
    )


def render_scaffold(number: int, name: str, pattern: str, caps: list[str], requires: dict[str, Any]) -> str:
    """Le manifeste pré-rempli. Dérivé : mission, pattern, CAPs. Décidé : le reste."""
    needs_sub = int(requires.get("subagents_min") or 0) > 0 or requires.get("subagents_max") != 0
    needs_rel = bool(requires.get("relations"))
    needs_fallback = bool(requires.get("fallback"))
    needs_loop = bool(requires.get("loop_bound"))
    needs_merge = bool(requires.get("merge_strategy"))

    out: list[str] = [
        f"# Roster d'agents — MISSION {number}-{name} (PHILOSOPHY P7).",
        f"# Généré par `python .sdda/sdda.py roster scaffold --mission {number}`.",
        "# À COMPLÉTER par l'ARCHITECTE : chaque `<à préciser>` est une décision qui lui",
        f"# revient. Le framework vérifie (`roster.py validate --mission {number}`), il n'en",
        "# décide aucune ligne. Gabarit commenté : .sdda/templates/roster.manifest.template.yml",
        "#",
        f"# Pattern actif (STACK.md ## {ORCHESTRATION_SECTION}) : {pattern}",
        "# Ce qu'il exige : python .sdda/sdda.py validate-architecture --explain",
        "",
        f"mission: {number}",
        f"pattern: {pattern}" + " " * max(1, 24 - len(pattern)) + "# doit rester égal au pattern actif de STACK.md",
        "",
        "# L'orchestrateur — reçoit l'entrée, décide de la suite. Se déclare même en single-agent.",
        "orchestrator:",
        f"  id: {PLACEHOLDER}",
        f"  role: {PLACEHOLDER}",
        f"  responsibilities: {PLACEHOLDER}",
        f"  tier: {PLACEHOLDER}                # fast | balanced | deep",
        "  model:                          # optionnel — prime sur le tier s'il est renseigné",
        "  tools: []                       # liste explicite ; [] signifie « aucun », et c'est une décision",
        f"  rules: {PLACEHOLDER}",
        "",
        "# Les subagents — un bloc par agent. `[]` pour single-agent.",
    ]
    if needs_sub:
        out.append("subagents:")
        out.append(_agent_block("  ").rstrip("\n"))
    else:
        out.append("subagents: []                   # le pattern actif n'admet aucun subagent")
    out += [
        "",
        "# L'allocation — chaque CAP de la MISSION est portée par EXACTEMENT un agent du roster (son id).",
        "allocation:",
    ]
    if caps:
        for cap in caps:
            out.append(f"  - cap: {cap}")
            out.append(f"    agent: {PLACEHOLDER}")
    else:
        out.append(f"  # aucune CAP `{number}-*.md` dans workspace/feats/caps/ — lancer /sdda-caps {number} puis re-scaffold")
    out += [
        "",
        "# Les relations — qui appelle qui, à quelle condition. Une condition vide est refusée :",
        "# « le contexte suit » n'est pas une condition.",
    ]
    if needs_rel:
        out.append("relations:")
        out += [f"  - from: {PLACEHOLDER}", f"    to: {PLACEHOLDER}", f"    condition: {PLACEHOLDER}", "    counts_as_hop: true"]
        if needs_fallback:
            out += [f"  - from: {PLACEHOLDER}", f"    to: {PLACEHOLDER}",
                    f"    condition: {PLACEHOLDER}          # le chemin de REPLI : « aucune classe ne correspond »",
                    "    counts_as_hop: true"]
    else:
        out.append("relations: []")
    out += ["", "# Les bornes — obligatoires dès que le pattern autorise un cycle (P12)."]
    if needs_loop:
        out += ["loop_bounds:", f"  - loop: {PLACEHOLDER}", f"    bound: {PLACEHOLDER}                # ex. maxHops = 6",
                f"    on_exceeded: {PLACEHOLDER}          # fail-explicit | degrade | escalate-human"]
    else:
        out.append("loop_bounds: []")
    out += ["", "# La fusion — obligatoire pour `parallel` uniquement."]
    out.append(f"merge_strategy: {PLACEHOLDER}" if needs_merge
               else "merge_strategy:                 # vote | priorité déclarée | synthèse par un agent dédié | échec si divergence")
    return "\n".join(out) + "\n"


def scaffold(root: Path, mission: int | str, *, force: bool = False, reason: str | None = None,
             command: str | None = None) -> Report:
    report = Report(name="ROSTER.scaffold", target=str(root))
    if not paths.stack_md_path(root).is_file():
        report.error("STACK_MISSING", "STACK.md introuvable", fix="lancer `python bootstrap.py`")
        return report
    spec = find_mission(root, mission, report)
    pattern = active_pattern(root, report)
    if spec is None or not pattern:
        return report

    target = manifest_path(root, mission)
    rel = paths.rel(root, target)
    report.data.update({"manifest": rel, "pattern": pattern, "mission": spec.id})

    if target.is_file():
        existing = markdown_io.read_text(target)
        try:
            holes = placeholders(yaml_mini.parse_mapping(existing))
        except yaml_mini.YamlMiniError:
            holes = [f"$ ({len(_PLACEHOLDER_RE.findall(existing))} `<à préciser>`)"] if _PLACEHOLDER_RE.search(existing) else []
        report.data["placeholders"] = len(holes)
        if not force:
            # Idempotent : ce que l'architecte a écrit ne se regénère pas. Un
            # manifeste sans trou est une décision prise ; un manifeste à trous
            # est une décision en cours — ni l'un ni l'autre ne s'écrase sans le dire.
            report.data["action"] = "kept"
            return report
        # `--force` est un bypass : il détruit une déclaration. Journalisé (R5),
        # et refusé sans raison — un contournement anonyme est indistinguable
        # d'un oubli, et six mois plus tard, d'une décision.
        if not is_real_reason(reason):
            report.error("BYPASS_REASON_MISSING",
                         f"`--force` écraserait `{rel}` sans raison ({reason!r})",
                         fix="passer --reason \"<pourquoi, en une phrase>\" ou SDDA_BYPASS_REASON ; "
                             "ou éditer le manifeste existant plutôt que le regénérer",
                         location=rel)
            report.data["action"] = "refused"
            return report
        audit = append_bypass_audit(root, "G2", str(reason).strip(),
                                    extra={"command": command or f"/sdda-roster {mission} scaffold --force",
                                           "bypass": BYPASS_NAME, "manifest": rel})
        report.data["audit"] = paths.rel(root, audit)
        report.data["action"] = "overwritten"
    else:
        report.data["action"] = "written"

    registry = va.load_registry(root, report)
    requires, _ = va.requirements_for(registry, "orchestration", pattern) if registry else ({}, {})
    caps = cap_ids(root, mission)
    text = render_scaffold(spec.number, spec.name, pattern, caps, requires)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    report.data.update({"caps": caps, "placeholders": len(placeholders(yaml_mini.parse_mapping(text)))})
    if not caps:
        report.warn("ARCH_ROSTER_CAP_UNALLOCATED", f"aucune CAP `{mission}-*.md` : l'allocation est vide",
                    fix=f"lancer /sdda-caps {mission}, puis compléter `allocation:`", location=rel)
    return report


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------
def placeholders(data: Any, path: str = "$") -> list[str]:
    """Chemins des valeurs encore `<à préciser>` (ou entièrement entre chevrons)."""
    out: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            out.extend(placeholders(v, f"{path}.{k}"))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out.extend(placeholders(v, f"{path}[{i}]"))
    elif isinstance(data, str):
        s = data.strip()
        if _PLACEHOLDER_RE.search(s) or _ANGLE_RE.match(s):
            out.append(path)
    return out


def _ids(value: Any) -> list[str]:
    """`agent: x` ou `agent: [x, y]` -> identifiants, chaînes non vides."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v or "").strip()]
    return [str(value).strip()] if value not in (None, "") else []


def load_manifest(root: Path, mission: int | str, report: Report) -> tuple[Path, dict[str, Any] | None]:
    target = manifest_path(root, mission)
    rel = paths.rel(root, target)
    if not target.is_file():
        report.error("ARCH_ROSTER_MANIFEST_MISSING", f"manifeste de roster introuvable ({rel})",
                     fix=f"python .sdda/sdda.py roster scaffold --mission {mission} — puis le remplir. "
                         "C'est l'architecte qui déclare les agents ; le framework les vérifie (P7)",
                     location=rel)
        return target, None
    try:
        data = yaml_mini.parse_mapping(markdown_io.read_text(target))
    except (yaml_mini.YamlMiniError, OSError) as exc:
        report.error("ARCH_ROSTER_MANIFEST_MALFORMED", f"manifeste `{rel}` illisible : {exc}",
                     fix="corriger la syntaxe (sous-ensemble YAML de sdda_lib/yaml_mini)", location=rel)
        return target, None
    return target, data


def validate_manifest(root: Path, mission: int | str, *, if_present: bool = False) -> Report:
    report = Report(name="ROSTER.validate", target=str(root))
    if not paths.stack_md_path(root).is_file():
        report.error("STACK_MISSING", "STACK.md introuvable", fix="lancer `python bootstrap.py`")
        return report

    target, data = load_manifest(root, mission, report)
    loc = paths.rel(root, target)
    report.data["manifest"] = loc
    if data is None:
        if if_present and not target.is_file():
            # Le repli `## 2. Roster déclaré` du Markdown reste accepté par G2 :
            # sans manifeste, ce script n'a rien à juger et le dit.
            report.findings = [f for f in report.findings if f.cls != "ARCH_ROSTER_MANIFEST_MISSING"]
            report.warn("ARCH_ROSTER_MANIFEST_MISSING", f"aucun manifeste `{loc}` — repli Markdown possible",
                        fix=f"/sdda-roster {mission} pour la forme recommandée", location=loc)
            report.data["action"] = "absent"
        return report

    # 1. Aucun trou -------------------------------------------------------------
    holes = placeholders(data)
    report.data["placeholders"] = len(holes)
    for path in holes:
        report.error("ARCH_ROSTER_PLACEHOLDER", f"`{path}` est encore `<à préciser>`",
                     fix="l'architecte tranche — le framework ne comble aucun trou (P7)", location=f"{loc}:{path}")

    # 2. Cohérence mission / pattern -------------------------------------------
    declared_mission = str(data.get("mission") if data.get("mission") is not None else "").strip()
    if declared_mission and declared_mission != str(mission):
        report.error("ARCH_ROSTER_INCOHERENT", f"`mission: {declared_mission}` mais le manifeste est celui de la MISSION {mission}",
                     fix=f"écrire `mission: {mission}`", location=f"{loc}:$.mission")
    pattern = active_pattern(root, report)
    declared_pattern = str(data.get("pattern") or "").strip().lower()
    if pattern and declared_pattern and declared_pattern != pattern and not _PLACEHOLDER_RE.search(declared_pattern):
        report.error("ARCH_ROSTER_INCOHERENT",
                     f"`pattern: {declared_pattern}` mais STACK.md active `{pattern}`",
                     fix=f"une seule source : changer `## {ORCHESTRATION_SECTION}` dans STACK.md, ou aligner le manifeste",
                     location=f"{loc}:$.pattern")
    report.data["pattern"] = pattern

    # 3. Complétude pour le pattern actif (registre, même code que G2) ---------
    roster = va.Roster.from_manifest(data, loc)
    if pattern:
        registry = va.load_registry(root, report)
        if registry:
            requires, entry = va.requirements_for(registry, "orchestration", pattern)
            va.check_requirements(root, "orchestration", pattern, requires, entry, roster, report, loc)

    # 4. Allocation : chaque CAP -> exactement un agent déclaré ---------------
    agent_ids = [aid for aid, _ in roster.agents()]
    caps = cap_ids(root, mission)
    allocation: dict[str, list[str]] = {}
    raw_alloc = data.get("allocation")
    for i, entry in enumerate(raw_alloc if isinstance(raw_alloc, list) else []):
        if not isinstance(entry, dict):
            report.error("ARCH_ROSTER_ALLOCATION_INVALID", f"`allocation[{i}]` n'est pas un bloc `cap:` / `agent:`",
                         fix="écrire `- cap: {n}-{m}-{Name}` puis `  agent: {id}`", location=f"{loc}:$.allocation[{i}]")
            continue
        cap = str(entry.get("cap") or "").strip()
        agents = [a for a in _ids(entry.get("agent")) if not _PLACEHOLDER_RE.search(a) and not _ANGLE_RE.match(a)]
        if not cap:
            report.error("ARCH_ROSTER_ALLOCATION_INVALID", f"`allocation[{i}]` sans `cap:`", fix="nommer la CAP", location=f"{loc}:$.allocation[{i}]")
            continue
        if cap not in caps:
            report.error("ARCH_ROSTER_ALLOCATION_INVALID", f"allocation d'une CAP inconnue `{cap}`",
                         fix=f"CAPs de la MISSION {mission} : {caps or 'aucune'}", location=f"{loc}:$.allocation[{i}]")
        if cap in allocation:
            report.error("ARCH_ROSTER_ALLOCATION_INVALID", f"CAP `{cap}` allouée deux fois",
                         fix="exactement un agent par CAP — une CAP portée par deux agents est une CAP que personne n'évalue isolément",
                         location=f"{loc}:$.allocation[{i}]")
        if len(agents) > 1:
            report.error("ARCH_ROSTER_ALLOCATION_INVALID", f"CAP `{cap}` allouée à {len(agents)} agents {agents}",
                         fix="exactement un agent par CAP", location=f"{loc}:$.allocation[{i}]")
        for aid in agents:
            if aid not in agent_ids:
                report.error("ARCH_ROSTER_ALLOCATION_INVALID", f"CAP `{cap}` allouée à `{aid}`, qui n'est pas un agent du roster",
                             fix=f"agents déclarés : {agent_ids or 'aucun'}", location=f"{loc}:$.allocation[{i}]")
        allocation.setdefault(cap, []).extend(agents)
    for cap in caps:
        if not allocation.get(cap):
            report.error("ARCH_ROSTER_CAP_UNALLOCATED", f"CAP `{cap}` n'est portée par aucun agent",
                         fix=f"ajouter `- cap: {cap}` / `  agent: {{id}}` dans `allocation:`", location=f"{loc}:$.allocation")

    # 5. Un subagent sert une CAP, ou dit pourquoi il existe -------------------
    carried = {a for ids in allocation.values() for a in ids}
    reasons = {str(s.get("id") or "").strip(): str(s.get("reason") or "").strip()
               for s in data.get("subagents") or [] if isinstance(s, dict)}
    for sub in roster.subagents:
        sid = sub["id"]
        reason = reasons.get(sid, "")
        if sid not in carried and (not reason or _PLACEHOLDER_RE.search(reason) or _ANGLE_RE.match(reason)):
            report.error("ARCH_ROSTER_AGENT_IDLE", f"subagent `{sid}` ne porte aucune CAP et n'a pas de `reason:`",
                         fix="lui allouer une CAP, écrire `reason:` (une des 5 raisons closes de P7), ou le retirer",
                         location=f"{loc}:$.subagents")

    # 6. Neutralité framework (P11) --------------------------------------------
    for path, token in framework_leaks(data):
        report.error("FRAMEWORK_LEAK_IN_CONTRACT", f"`{token}` à {path}",
                     fix="le roster décrit QUOI, jamais avec quelle API ; le framework vit dans .sdda/stacks/framework/",
                     location=f"{loc}:{path}")

    report.data.update({
        "orchestrator": roster.orchestrator_id,
        "subagents": [s["id"] for s in roster.subagents],
        "caps": caps,
        "allocation": {c: (ids[0] if len(ids) == 1 else ids) for c, ids in sorted(allocation.items())},
        "relations": len(roster.relations),
        "loopBounds": len(roster.loops),
        "action": "validated",
    })
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Manifeste de roster : gabarit pré-rempli puis vérification (P7, 0 token, aucun rapport de gate)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scaffold", help="écrire {n}-roster.yml pré-rempli ; idempotent, --force écrase (bypass audité)")
    sc.add_argument("--mission", required=True, help="numéro de MISSION")
    sc.add_argument("--force", action="store_true", help="écraser un manifeste existant — exige --reason ou SDDA_BYPASS_REASON")
    sc.add_argument("--reason", default=None, help="pourquoi écraser, en une phrase (défaut : $SDDA_BYPASS_REASON)")
    sc.add_argument("--command", default=None, help="commande appelante, pour l'audit (défaut : /sdda-roster {n} scaffold --force)")
    add_common_args(sc)

    vl = sub.add_parser("validate", help="vérifier le manifeste ; exit 1 si rouge")
    vl.add_argument("--mission", required=True, help="numéro de MISSION")
    vl.add_argument("--if-present", action="store_true",
                    help="un manifeste absent rend exit 0 (le repli `## 2. Roster déclaré` reste accepté par G2)")
    add_common_args(vl)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    if args.cmd == "scaffold":
        reason = args.reason if args.reason is not None else os.environ.get("SDDA_BYPASS_REASON")
        report = scaffold(root, args.mission, force=args.force, reason=reason, command=args.command)
        if report.ok and not args.json:
            action = report.data.get("action")
            holes = report.data.get("placeholders", 0)
            print({"written": f"  manifeste écrit -> {report.data.get('manifest')} ({holes} `<à préciser>` à remplir)",
                   "overwritten": f"  manifeste RÉÉCRIT -> {report.data.get('manifest')} ({holes} `<à préciser>`) · bypass journalisé {report.data.get('audit')}",
                   "kept": f"  manifeste déjà présent -> {report.data.get('manifest')} ({holes} `<à préciser>` restant(s)) — rien réécrit (--force pour regénérer)",
                   }.get(str(action), f"  {action}"))
        return finish(report, args)
    report = validate_manifest(root, args.mission, if_present=args.if_present)
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
