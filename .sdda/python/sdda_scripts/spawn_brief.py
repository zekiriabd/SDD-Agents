#!/usr/bin/env python3
"""Brief de spawn : tout ce qu'un harnais doit savoir pour lancer un agent.

C'est la jonction manquante entre les fiches d'agent (`.sdda/agents/*.md`, du
comportement) et les commandes (`.sdda/commands/*.md`, de l'orchestration).
Jusqu'ici chaque commande réécrivait son prompt d'invocation à la main — donc
quatre formulations du même contrat, dérivant chacune de son côté.

Ce script **n'appelle aucun LLM et ne lance rien**. Il assemble, vérifie, et
refuse :

    fiche        le prompt système de l'agent, sur disque, avec son hash
    tier         résolu contre `agent-bounds.yaml` — `tier_floor` n'est PAS
                 surchargeable, une demande en dessous est ramenée au plancher
                 et le brief le dit
    contexte     `context_pack.py` : fichiers, couches, octets, budget, packs
    ownership    ce que l'agent a le droit d'écrire, et ce qui lui est interdit
    exécution    `spawn` (sous-agent) ou `inline` (dans le fil principal)

Un brief rouge **n'est pas un brief** : exit 1, et le harnais ne lance rien.
Lancer un agent sur un contexte incomplet produit une sortie confiante et
fausse, qui coûte une revue au lieu d'une seconde.

Sur `execution: inline` — `po-elicitor` interroge un humain. Un sous-agent
ne parle à personne : le lancer comme sous-agent transformerait ses cinq
questions en cinq hypothèses. Les agents interactifs s'exécutent donc dans le
fil qui a l'humain, et le brief le déclare plutôt que de laisser chaque
commande le redécouvrir.

Usage :
    python .sdda/sdda.py spawn-brief --agent po-capabilities --mission 1
    python .sdda/sdda.py spawn-brief --agent dev-agent --mission 1 --target billing-specialist --json
    python .sdda/sdda.py spawn-brief --agent po-elicitor --mission 1 --work-item "assistant facturation"
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, paths, yaml_mini  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.layered_config import active_stacks  # noqa: E402
from sdda_scripts import context_pack  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, load_config, resolve_root  # noqa: E402

TIERS: tuple[str, ...] = ("fast", "balanced", "deep")

#: Clé de `## Required Stack` -> section de STACK.md. Miroir de la table de
#: `validate_mission.py` : c'est ce que G0 contrôlera, donc ce que l'agent doit
#: pouvoir remplir.
STACK_SECTIONS: dict[str, str] = {
    "language": "Active Language & Runtime",
    "framework": "Active Agent Framework",
    "orchestration": "Active Orchestration Pattern",
    "rag": "Active RAG Pattern",
    "dataaccess": "Active Data Access",
    "serving": "Active Serving Surface",
}

#: Défauts de Project Config qu'un agent doit connaître sans lire STACK.md.
BUDGET_KEYS: tuple[str, ...] = ("CostPerRunTargetUsd", "CostPerRunHardCapUsd", "LatencyP95TargetMs", "TokenCeilingPerRun")

#: Modes d'exécution. `inline` = pas de sous-agent : la commande joue la fiche
#: dans le fil principal, parce que l'agent a besoin de l'humain.
EXECUTIONS: tuple[str, ...] = ("spawn", "inline")

#: Le contrat de sortie commun, rappelé dans chaque brief. Il vit ici et nulle
#: part ailleurs : quatre copies dans quatre commandes divergeraient.
OUTPUT_CONTRACT = """\
- Une ligne de succès `[{FAMILLE}] …`, ou un bloc ERROR de 3 lignes :
  `ERROR:` ce qui a échoué / `CAUSE:` `[CLASS]` + détail / `FIX:` l'action qui corrige.
- N'écrire QUE dans les chemins autorisés ci-dessous. Une écriture hors périmètre
  est une violation d'ownership, pas un raccourci.
- Ne jamais spawner un autre agent : l'orchestration appartient à la commande.
- Ne jamais combler un champ inconnu par une valeur plausible. `<à préciser>`
  bloque une gate, ce qui coûte une conversation ; une invention coûte un projet."""


# ---------------------------------------------------------------------------
# Bornes de tier
# ---------------------------------------------------------------------------
def load_bounds(root: Path) -> dict[str, dict[str, str]]:
    """`agent-bounds.yaml` -> {agent: {tier_default, tier_floor, tier_ceiling}}."""
    path = root / ".sdda" / "agent-bounds.yaml"
    if not path.is_file():
        path = paths.FRAMEWORK_SDDA_DIR / "agent-bounds.yaml"
    data = yaml_mini.parse_mapping(markdown_io.read_text(path))
    agents = data.get("agents") or {}
    return {k: v for k, v in agents.items() if isinstance(v, dict)}


def resolve_tier(bounds: dict[str, str], requested: str | None, report: Report, agent: str) -> tuple[str, tuple[str, str] | None]:
    """(tier retenu, (classe, raison) du recadrage). Le plancher n'est jamais franchi.

    Les deux recadrages ne disent pas la même chose : sous le plancher, on
    essayait d'acheter moins de qualité là où une erreur est silencieuse ;
    au-dessus du plafond, on payait plus sans rien acheter du tout.
    """
    default = str(bounds.get("tier_default") or "balanced")
    floor = str(bounds.get("tier_floor") or default)
    ceiling = str(bounds.get("tier_ceiling") or default)
    tier = requested or default
    if tier not in TIERS:
        report.error("INVALID_ARG", f"tier `{tier}` inconnu", f"tiers : {', '.join(TIERS)}", agent)
        return default, None
    if TIERS.index(tier) < TIERS.index(floor):
        return floor, ("CONFIG_SECURITY_DOWNGRADE", f"`{tier}` refusé : tier_floor `{floor}` n'est pas surchargeable (agent-bounds.yaml)")
    if TIERS.index(tier) > TIERS.index(ceiling):
        return ceiling, ("SCOPE_CREEP", f"`{tier}` ramené au plafond `{ceiling}` : au-delà, le coût n'achète plus de qualité sur cette tâche")
    return tier, None


# ---------------------------------------------------------------------------
# Fiche
# ---------------------------------------------------------------------------
def fiche_path(root: Path, agent: str) -> Path:
    local = root / ".sdda" / "agents" / f"{agent}.md"
    return local if local.is_file() else paths.FRAMEWORK_SDDA_DIR / "agents" / f"{agent}.md"


def fiche_frontmatter(text: str) -> dict[str, str]:
    """Frontmatter YAML plat d'une fiche d'agent (name, description, model_tier…)."""
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        return {}
    out: dict[str, str] = {}
    for line in match.group(1).split("\n"):
        kv = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if kv:
            out[kv.group(1)] = kv.group(2).strip()
    return out


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------
@dataclass
class Brief:
    agent: str
    fiche: str
    fiche_hash: str
    description: str
    tier: str
    tier_note: str | None
    execution: str
    mission: str | None
    target: str | None
    work_item: str
    context: dict[str, Any]
    writes: list[str] = field(default_factory=list)
    forbidden_writes: list[str] = field(default_factory=list)
    forbidden_reads: list[str] = field(default_factory=list)
    facts: dict[str, str] = field(default_factory=dict)
    verdict: str = "green"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "fiche": self.fiche,
            "ficheHash": self.fiche_hash,
            "description": self.description,
            "modelTier": self.tier,
            "tierNote": self.tier_note,
            "execution": self.execution,
            "missionId": self.mission,
            "target": self.target,
            "workItem": self.work_item,
            "verdict": self.verdict,
            "context": self.context,
            "ownership": {
                "writes": self.writes,
                "forbiddenWrites": self.forbidden_writes,
                "forbiddenReads": self.forbidden_reads,
            },
            "facts": self.facts,
            "outputContract": OUTPUT_CONTRACT,
            "prompt": self.render_prompt(),
        }

    def render_prompt(self) -> str:
        """Le prompt d'invocation — assemblé, pas recopié dans chaque commande."""
        reads = "\n".join(f"  - {f['path']}  ({f['layer']}, {f['bytes']} o)" for f in self.context.get("files") or [])
        writes = "\n".join(f"  - {w}" for w in self.writes) or "  - (aucune écriture déclarée)"
        forbidden = "\n".join(f"  - {p}" for p in self.forbidden_writes + self.forbidden_reads)
        missing = self.context.get("missing") or []
        lines = [
            f"Tu es `{self.agent}`. Ton prompt système est `{self.fiche}` — lis-le d'abord et applique ses STEPs dans l'ordre.",
            "",
            f"Travail demandé : {self.work_item}",
        ]
        if self.mission:
            lines.append(f"MISSION : {self.mission}")
        if self.target:
            lines.append(f"Cible : {self.target}")
        lines += ["", "Contexte à lire (et rien d'autre) :", reads or "  - (aucun)"]
        if missing:
            lines += [
                "",
                "Absents du disque — ce contenu ne t'a PAS été donné. Ne suppose pas ce qu'il contient :",
                *(f"  - {m}" for m in missing),
            ]
        if self.facts:
            lines += ["", "Faits fournis (tu n'as pas le droit de lire leur source — ne les redemande pas, ne les devine pas) :",
                      *(f"  - {k} = {v}" for k, v in sorted(self.facts.items()))]
        lines += ["", "Tu écris exclusivement dans :", writes]
        if forbidden:
            lines += ["", "Interdits (une écriture ici est une violation d'ownership) :", forbidden]
            if any(ph in forbidden for ph in OTHER_PLACEHOLDERS):
                lines.append("  ({m} et {other} désignent tout AUTRE que celui de ce brief — le tien reste lisible.)")
        lines += ["", "Contrat de sortie :", OUTPUT_CONTRACT]
        return "\n".join(lines)

    def render_text(self) -> str:
        glyph = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[self.verdict]
        ctx = self.context
        return (f"{glyph} {self.agent} · tier {self.tier} · {self.execution} · "
                f"{ctx.get('totalBytes', 0) // 1024} Ko / {ctx.get('budgetBytes', 0) // 1024} Ko · "
                f"{len(ctx.get('files') or [])} fichier(s)"
                + (f"\n   {self.tier_note}" if self.tier_note else ""))


#: Placeholders qui désignent « tout AUTRE que celui de ce brief » dans les
#: listes d'ownership : `{m}` une autre MISSION, `{other}` un autre agent.
OTHER_PLACEHOLDERS = ("{m}", "{other}")


def injected_facts(root: Path, spec: dict[str, Any], config: Any, extra: list[str]) -> dict[str, str]:
    """Les faits qu'un agent doit connaître mais n'a pas le droit de lire.

    `po-elicitor` doit remplir `## Required Stack` — que G0 contrôle — alors
    que `STACK.md` figure dans ses `forbidden_reads` : le choix technique ne le
    regarde pas, mais son résultat le concerne. Sans ce pont, la fiche est
    intenable : soit l'agent viole l'ownership, soit il laisse une section que
    la gate refusera. Le script lit ce que l'agent ne peut pas lire, et ne lui
    transmet que le dérivé minimal.
    """
    facts: dict[str, str] = {}
    forbidden = {str(p) for p in spec.get("forbidden_reads") or []}
    readable = {str(p) for p in spec.get("reads") or []}
    if "workspace/stack/STACK.md" in forbidden and "workspace/stack/STACK.md" not in readable:
        for key, heading in STACK_SECTIONS.items():
            chosen = active_stacks(root, heading)
            facts[f"stack.{key}"] = ", ".join(chosen) if chosen else "none"
        if config is not None:
            for key in BUDGET_KEYS:
                value = config.get(key)
                if value not in (None, ""):
                    facts[f"config.{key}"] = str(value)
    for item in extra or []:
        key, sep, value = item.partition("=")
        if sep:
            facts[key.strip()] = value.strip()
    return facts


def _substitute_all(patterns: Any, mission: str | None, target: str | None) -> list[str]:
    """Les chemins d'ownership portent les mêmes placeholders que les lectures.

    Laisser `{n}` dans un prompt revient à demander à l'agent de deviner son
    propre périmètre d'écriture — et il devinera. Mais `{m}` et `{other}` ne
    s'élargissent PAS en `*` : `workspace/pipeline/missions/{m}-*.md` veut dire « les
    autres MISSIONs », et le transformer en `workspace/pipeline/missions/*-*.md`
    interdirait à l'agent de lire la sienne. Un élargissement qui inverse le
    sens d'une règle est pire que le placeholder qu'il remplace.
    """
    out: list[str] = []
    for raw in patterns or []:
        pattern = str(raw)
        if mission:
            pattern = pattern.replace("{n}", str(mission))
        if target:
            pattern = pattern.replace("{agent}", str(target))
        out.append(pattern)
    return out


def build_brief(
    root: Path,
    agent: str,
    *,
    report: Report,
    mission: str | None = None,
    target: str | None = None,
    work_item: str = "",
    tier: str | None = None,
    config: Any = None,
    extra_facts: list[str] | None = None,
) -> Brief | None:
    loader = context_pack.load_loader(root)
    spec = loader.get(agent)
    if not isinstance(spec, dict):
        report.error("CONFIG_UNKNOWN_KEY", f"agent `{agent}` absent de loader.yml : rien ne dit ce qu'il lit ni ce qu'il écrit",
                     f"agents déclarés : {', '.join(context_pack.agent_names(loader))}", agent)
        return None

    path = fiche_path(root, agent)
    if not path.is_file():
        report.error("PACK_UNUSABLE", f"agent `{agent}` : fiche `{paths.rel(root, path)}` absente — il n'a pas de prompt système",
                     "écrire la fiche dans .sdda/agents/, ou retirer l'entrée de loader.yml", agent)
        return None
    text = markdown_io.read_text(path)
    front = fiche_frontmatter(text)

    bounds = load_bounds(root).get(agent) or {}
    resolved_tier, clamp = resolve_tier(bounds, tier, report, agent)
    note = clamp[1] if clamp else None
    if clamp:
        report.warn(clamp[0], f"agent `{agent}` : {clamp[1]}",
                    "le brief part avec le tier recadré ; pour le changer vraiment, il faut un commit framework sur agent-bounds.yaml", agent)
    if front.get("model_tier") and front["model_tier"] != str(bounds.get("tier_default") or front["model_tier"]):
        report.warn("DIGEST_DRIFT", f"agent `{agent}` : `model_tier: {front['model_tier']}` dans la fiche ≠ `tier_default: {bounds.get('tier_default')}` dans agent-bounds.yaml",
                    "aligner les deux — la borne fait foi, la fiche doit la refléter", agent)

    execution = str(spec.get("execution") or "spawn")
    if execution not in EXECUTIONS:
        report.error("INVALID_ARG", f"agent `{agent}` : `execution: {execution}` inconnu dans loader.yml",
                     f"valeurs : {', '.join(EXECUTIONS)}", agent)
        return None

    resolution = context_pack.resolve_context(root, loader, agent, report=report, mission=mission, target=target)
    if resolution is None:
        return None

    brief = Brief(
        agent=agent,
        fiche=paths.rel(root, path) if path.is_relative_to(root) else ".sdda/agents/" + path.name,
        fiche_hash=hashing.sha256_file(path),
        description=front.get("description", ""),
        tier=resolved_tier,
        tier_note=note,
        execution=execution,
        mission=mission,
        target=target,
        work_item=work_item or f"exécuter {agent} sur la MISSION {mission or '(non précisée)'}",
        context=resolution.to_dict(),
        facts=injected_facts(root, spec, config, extra_facts or []),
        writes=_substitute_all(spec.get("writes"), mission, target),
        forbidden_writes=_substitute_all(spec.get("forbidden_writes"), mission, target),
        forbidden_reads=_substitute_all(spec.get("forbidden_reads"), mission, target),
        verdict=resolution.verdict,
    )
    if not brief.writes:
        report.warn("OWNERSHIP_VIOLATION", f"agent `{agent}` : aucun `writes:` dans loader.yml — rien ne borne ce qu'il peut écrire",
                    "déclarer ses chemins d'écriture ; un agent sans périmètre est un agent qui écrasera un jour celui d'un autre", agent)
    if report.errors:
        brief.verdict = "red"
    return brief


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Brief de spawn d'un Developer Agent : fiche, tier, contexte, ownership — vérifié avant lancement (0 token)")
    p.add_argument("--agent", required=True)
    p.add_argument("--mission", default=None, help="valeur de `{n}`")
    p.add_argument("--target", default=None, help="valeur de `{agent}` (l'agent généré)")
    p.add_argument("--work-item", default="", help="ce qui est demandé, en une phrase")
    p.add_argument("--tier", default=None, choices=list(TIERS), help="tier demandé ; clampé par agent-bounds.yaml")
    p.add_argument("--fact", action="append", default=None, metavar="CLE=VALEUR",
                   help="fait supplémentaire à transmettre (répétable) — ce que l'agent ne peut pas lire lui-même")
    p.add_argument("--prompt-only", action="store_true", help="n'écrire que le prompt d'invocation sur stdout")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="SPAWN", target=args.agent)
    config = load_config(root, report)

    brief = build_brief(root, args.agent, report=report, mission=args.mission, target=args.target,
                        work_item=args.work_item, tier=args.tier, config=config, extra_facts=args.fact)
    if brief is None:
        return finish(report, args)

    report.data.update(brief.to_dict())
    if args.prompt_only:
        # Seul cas où stdout n'est pas un rapport : la sortie est destinée à
        # être passée telle quelle au harnais.
        print(brief.render_prompt())
        return report.exit_code
    if not args.json:
        print(brief.render_text())
    # Même enveloppe que tous les autres scripts (`ok`, `errors`, `warnings`,
    # `data`) : les commandes parsent une seule forme, pas une par script.
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
