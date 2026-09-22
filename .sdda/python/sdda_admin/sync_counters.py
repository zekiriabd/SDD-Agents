#!/usr/bin/env python3
"""Les compteurs de la prose, régénérés depuis les sources — comme les digests.

Un document qui dit « 22 agents », « 30 fiches », « 328 classes » énonce un
fait sur le disque. Le disque bouge, la prose non : `agent-bounds.yaml`
annonçait 26 agents pour 22 fiches, `ARCHITECTURE.md` 27 fiches pour 30 et
328 classes pour 348, et la FAQ de `/sdda-help` recopiait des défauts de
`config.base.yml` que rien ne relisait. Chacun de ces chiffres était vrai un
jour ; aucun n'était vérifié.

Le mécanisme est celui de `sync_digests.py` : la prose porte des **marqueurs**,
ce script recalcule la valeur et la réécrit (`--write`) ou constate la dérive
(`--check`, exit 1 — appelé par `framework_smoke`).

    <!--sdda:count agents-->22<!--/sdda:count-->
    <!--sdda:config MaxParallel-->3<!--/sdda:config-->
    <!--sdda:graders-->`exact`, `regex`, …<!--/sdda:graders-->

Dans `agent-bounds.yaml`, qui n'a pas de commentaires HTML, le bilan entier est
un bloc régénéré entre `# sdda:bilan-begin` et `# sdda:bilan-end`.

**Les marqueurs ne coûtent aucun token aux agents** : `harness_build.py` les
retire en compilant les façades (`strip_sync_markers`). Ils vivent dans la
source, pas dans le prompt.

Compteurs :
    agents      fiches `.sdda/agents/*.md`
    commands    fiches `.sdda/commands/*.md`
    invariants  entrées `- id:` de INVARIANTS.yml
    stacks      fiches `stacks/**/*.md` (README exclus)
    classes     registre canonique (`sync_error_registry.collect()`)
    hooks       modules de `sdda_hooks/` porteurs d'un WIRING
    tests       fonctions `def test_` des `tests/test_*.py` (statique — pas de
                collecte pytest, qui coûterait des secondes à chaque smoke ;
                la prose dit « fonctions de test »)

Usage :
    python .sdda/python/sdda_admin/sync_counters.py            # état
    python .sdda/python/sdda_admin/sync_counters.py --check    # CI : exit 1 si dérive
    python .sdda/python/sdda_admin/sync_counters.py --write    # réécrit les marqueurs
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Any, Callable

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

SDDA = Path(__file__).resolve().parents[2]
ROOT = SDDA.parent
sys.path.insert(0, str(SDDA / "python"))

from sdda_lib import yaml_mini  # noqa: E402

COUNT_RE = re.compile(r"<!--sdda:count (?P<name>[a-z_]+)-->(?P<value>.*?)<!--/sdda:count-->", re.S)
CONFIG_RE = re.compile(r"<!--sdda:config (?P<key>[A-Za-z0-9_]+)-->(?P<value>.*?)<!--/sdda:config-->", re.S)
GRADERS_RE = re.compile(r"<!--sdda:graders-->(?P<value>.*?)<!--/sdda:graders-->", re.S)
BILAN_RE = re.compile(r"(?P<head># sdda:bilan-begin[^\n]*\n)(?P<body>.*?)(?P<tail># sdda:bilan-end)", re.S)

#: Fichiers porteurs de marqueurs. Un fichier hors liste avec un marqueur est
#: signalé : un marqueur que personne ne régénère est pire qu'un chiffre nu.
TARGETS: tuple[str, ...] = (
    "README.md",
    "README.fr.md",
    ".sdda/ARCHITECTURE.md",
    ".sdda/agent-bounds.yaml",
    ".sdda/docs/README.md",
    ".sdda/docs/AGENT-ROSTER.md",
    ".sdda/commands/sdda-help.md",
    ".sdda/commands/sdda-caps.md",
)


# ---------------------------------------------------------------------------
# Les faits
# ---------------------------------------------------------------------------
def count_agents() -> int:
    return len(list((SDDA / "agents").glob("*.md")))


def count_commands() -> int:
    return len(list((SDDA / "commands").glob("*.md")))


def count_invariants() -> int:
    text = (SDDA / "INVARIANTS.yml").read_text(encoding="utf-8")
    return len(re.findall(r"^\s*-\s+id:\s*\S+", text, re.M))


def count_stacks() -> int:
    return len([p for p in (SDDA / "stacks").rglob("*.md") if p.name != "README.md"])


def count_classes() -> int:
    from sdda_admin import sync_error_registry

    return len(set(sync_error_registry.collect()))


def count_hooks() -> int:
    n = 0
    for path in (SDDA / "python" / "sdda_hooks").glob("*.py"):
        if path.name.startswith("_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if any(isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "WIRING" for t in node.targets)
               for node in tree.body):
            n += 1
    return n


def count_tests() -> int:
    n = 0
    for path in (SDDA / "python" / "tests").glob("test_*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                n += 1
    return n


COUNTERS: dict[str, Callable[[], int]] = {
    "agents": count_agents,
    "commands": count_commands,
    "invariants": count_invariants,
    "stacks": count_stacks,
    "classes": count_classes,
    "hooks": count_hooks,
    "tests": count_tests,
}


def base_config() -> dict[str, Any]:
    return yaml_mini.parse_mapping((SDDA / "config.base.yml").read_text(encoding="utf-8"))


def format_config_value(value: Any) -> str:
    """`50.00` -> `50`, `0.6` -> `0.6`, `true` -> `true` : la forme que la prose lit."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:g}"
    return str(value)


def graders_list() -> str:
    from sdda_lib.graders import GRADERS

    return ", ".join(f"`{name}`" for name in GRADERS)


def bilan_block(indent: str = "# ") -> str:
    """Le bilan d'`agent-bounds.yaml`, calculé depuis ses propres entrées."""
    data = yaml_mini.parse_mapping((SDDA / "agent-bounds.yaml").read_text(encoding="utf-8"))
    agents = {k: v for k, v in (data.get("agents") or {}).items() if isinstance(v, dict)}
    by_default: dict[str, int] = {}
    by_floor: dict[str, int] = {}
    capped_balanced: list[str] = []
    for name, spec in sorted(agents.items()):
        by_default[str(spec.get("tier_default"))] = by_default.get(str(spec.get("tier_default")), 0) + 1
        by_floor[str(spec.get("tier_floor"))] = by_floor.get(str(spec.get("tier_floor")), 0) + 1
        if str(spec.get("tier_ceiling")) == "balanced":
            capped_balanced.append(name)
    lines = [
        f"{len(agents)} agents : {by_default.get('deep', 0)} tier_default=deep, {by_default.get('balanced', 0)} balanced, {by_default.get('fast', 0)} fast.",
        f"{' ' * len(str(len(agents)))}          {by_floor.get('balanced', 0)} floor=balanced, {by_floor.get('fast', 0)} floor=fast, {by_floor.get('deep', 0)} floor=deep.",
        f"{' ' * len(str(len(agents)))}          {len(capped_balanced)} plafonnés balanced ({', '.join(capped_balanced)}).",
    ]
    return "".join(f"{indent}{line}\n" for line in lines)


# ---------------------------------------------------------------------------
# Réécriture
# ---------------------------------------------------------------------------
def render(text: str, facts: dict[str, int], config: dict[str, Any], graders: str, bilan: str,
           drifts: list[str], where: str) -> str:
    def sub_count(m: re.Match) -> str:
        name, old = m.group("name"), m.group("value").strip()
        if name not in facts:
            drifts.append(f"{where} : compteur inconnu `{name}`")
            return m.group(0)
        new = str(facts[name])
        if old != new:
            drifts.append(f"{where} : {name} {old or '∅'} -> {new}")
        return f"<!--sdda:count {name}-->{new}<!--/sdda:count-->"

    def sub_config(m: re.Match) -> str:
        key, old = m.group("key"), m.group("value").strip()
        if key not in config:
            drifts.append(f"{where} : clé de config inconnue `{key}`")
            return m.group(0)
        new = format_config_value(config[key])
        if old != new:
            drifts.append(f"{where} : {key} {old or '∅'} -> {new}")
        return f"<!--sdda:config {key}-->{new}<!--/sdda:config-->"

    def sub_graders(m: re.Match) -> str:
        old = m.group("value").strip()
        if old != graders:
            drifts.append(f"{where} : liste des graders périmée")
        return f"<!--sdda:graders-->{graders}<!--/sdda:graders-->"

    def sub_bilan(m: re.Match) -> str:
        if m.group("body") != bilan:
            drifts.append(f"{where} : bilan agent-bounds périmé")
        return f"{m.group('head')}{bilan}{m.group('tail')}"

    text = COUNT_RE.sub(sub_count, text)
    text = CONFIG_RE.sub(sub_config, text)
    text = GRADERS_RE.sub(sub_graders, text)
    text = BILAN_RE.sub(sub_bilan, text)
    return text


def stray_markers() -> list[str]:
    """Marqueurs dans des fichiers hors TARGETS — personne ne les régénère."""
    out: list[str] = []
    targets = {str((ROOT / t).resolve()) for t in TARGETS}
    for path in list(SDDA.rglob("*.md")) + list(SDDA.rglob("*.yaml")) + list(ROOT.glob("*.md")):
        if "fixtures" in path.parts or str(path.resolve()) in targets:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "<!--sdda:" in text or "# sdda:bilan-begin" in text:
            out.append(str(path.relative_to(ROOT)).replace("\\", "/"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Régénère les compteurs de la prose depuis les sources.")
    parser.add_argument("--check", action="store_true", help="CI : ne rien écrire, échouer si dérive")
    parser.add_argument("--write", action="store_true", help="réécrire les marqueurs")
    args = parser.parse_args()

    facts = {name: fn() for name, fn in COUNTERS.items()}
    config = base_config()
    graders = graders_list()
    bilan = bilan_block()

    drifts: list[str] = []
    rewritten: list[str] = []
    markers = 0
    for rel in TARGETS:
        path = ROOT / rel
        if not path.is_file():
            drifts.append(f"{rel} : fichier cible absent")
            continue
        text = path.read_text(encoding="utf-8")
        markers += len(COUNT_RE.findall(text)) + len(CONFIG_RE.findall(text)) + len(GRADERS_RE.findall(text)) + len(BILAN_RE.findall(text))
        new = render(text, facts, config, graders, bilan, drifts, rel)
        if new != text:
            rewritten.append(rel)
            if args.write:
                path.write_text(new, encoding="utf-8")

    for stray in stray_markers():
        drifts.append(f"{stray} : marqueur hors des cibles de sync_counters (jamais régénéré)")

    summary = " · ".join(f"{k} {v}" for k, v in facts.items())
    if args.write:
        print(f"  {len(rewritten)} fichier(s) réécrit(s), {markers} marqueur(s) — {summary}")
        for d in drifts:
            print(f"    {d}")
        return 0
    if drifts:
        if args.check:
            print("ERROR: sync_counters — la prose a dérivé des sources")
            print(f"CAUSE: [COUNTER_DRIFT] {len(drifts)} écart(s) : " + " ; ".join(drifts[:4]))
            print("FIX: python .sdda/python/sdda_admin/sync_counters.py --write")
            return 1
        print(f"  {len(drifts)} écart(s) — {summary}")
        for d in drifts:
            print(f"    {d}")
        return 0
    print(f"  ok — {markers} compteur(s) à jour ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
