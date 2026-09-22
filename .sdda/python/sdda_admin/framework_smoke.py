#!/usr/bin/env python3
"""
SDD_Agents — smoke test du FRAMEWORK lui-même.

Ne teste pas un projet : teste que `.sdda/` est cohérent avec lui-même.
C'est le dispositif anti-pourrissement, l'équivalent du `framework_smoke.py`
de SDD_Pro.

Le principe qu'il applique : **un framework qui déclare des contrats doit
prouver qu'ils sont encore branchés.** Une règle écrite que plus rien
n'applique est pire qu'une règle absente — elle rassure.

Usage :
    python .sdda/python/sdda_admin/framework_smoke.py
    python .sdda/python/sdda_admin/framework_smoke.py --json
    python .sdda/python/sdda_admin/framework_smoke.py --strict   # WARN => exit 1

Exit codes : 0 = OK · 1 = au moins un FAIL (ou un WARN en --strict)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

SDDA = Path(__file__).resolve().parents[2]
ROOT = SDDA.parent


# ---------------------------------------------------------------------------
@dataclass
class Finding:
    check: str
    level: str  # ok | warn | fail
    message: str


FINDINGS: list[Finding] = []


def ok(check: str, message: str) -> None:
    FINDINGS.append(Finding(check, "ok", message))


def warn(check: str, message: str) -> None:
    FINDINGS.append(Finding(check, "warn", message))


def fail(check: str, message: str) -> None:
    FINDINGS.append(Finding(check, "fail", message))


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 1. Agents : fichier, frontmatter, tiers
# ---------------------------------------------------------------------------
def declared_agents() -> dict[str, dict[str, str]]:
    """Lit agent-bounds.yaml sans dépendance PyYAML (format connu et plat)."""
    text = read(SDDA / "agent-bounds.yaml")
    agents: dict[str, dict[str, str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        header = re.match(r"^  ([a-z][a-z0-9-]*):\s*(\{.*\})?\s*$", line)
        if header:
            current = header.group(1)
            agents[current] = {}
            if header.group(2):
                for key, value in re.findall(r"(\w+):\s*([a-z]+)", header.group(2)):
                    agents[current][key] = value
            continue
        if current and (inline := re.match(r"^\s+\{(.*)\}\s*$", line)):
            for key, value in re.findall(r"(\w+):\s*([a-z]+)", inline.group(1)):
                agents[current][key] = value
    return agents


TIER_ORDER = {"fast": 0, "balanced": 1, "deep": 2}


def check_agents() -> None:
    bounds = declared_agents()
    if not bounds:
        fail("agents.bounds", "agent-bounds.yaml illisible ou vide")
        return

    agent_dir = SDDA / "agents"
    on_disk = {p.stem for p in agent_dir.glob("*.md")} if agent_dir.is_dir() else set()

    missing = sorted(set(bounds) - on_disk)
    extra = sorted(on_disk - set(bounds))

    if missing:
        warn("agents.missing", f"{len(missing)} agent(s) déclarés sans fiche : {', '.join(missing)}")
    if extra:
        fail("agents.undeclared", f"fiche sans entrée dans agent-bounds.yaml : {', '.join(extra)}")
    if not missing and not extra:
        ok("agents.coverage", f"{len(on_disk)} agents, fiches et bornes alignées")

    # Cohérence des bornes elles-mêmes : floor <= default <= ceiling
    for name, tiers in bounds.items():
        floor, default, ceiling = (
            tiers.get("tier_floor"),
            tiers.get("tier_default"),
            tiers.get("tier_ceiling"),
        )
        if not all((floor, default, ceiling)):
            fail("agents.bounds", f"{name} : bornes incomplètes")
            continue
        if not (TIER_ORDER[floor] <= TIER_ORDER[default] <= TIER_ORDER[ceiling]):
            fail(
                "agents.bounds",
                f"{name} : bornes incohérentes (floor={floor} default={default} ceiling={ceiling})",
            )

    # Parité frontmatter <-> bornes
    divergences = []
    for name in sorted(on_disk & set(bounds)):
        text = read(agent_dir / f"{name}.md")
        match = re.search(r"^model_tier:\s*([a-z]+)\s*$", text, re.M)
        if not match:
            fail("agents.frontmatter", f"{name} : `model_tier:` absent du frontmatter")
            continue
        if match.group(1) != bounds[name].get("tier_default"):
            divergences.append(f"{name} ({match.group(1)} != {bounds[name].get('tier_default')})")
    if divergences:
        fail("agents.tier_parity", f"tier divergent : {', '.join(divergences)}")
    elif on_disk:
        ok("agents.tier_parity", "tous les tiers concordent avec agent-bounds.yaml")

    # Aucun agent ne doit spawner un agent : la commande orchestre.
    for name in sorted(on_disk):
        text = read(agent_dir / f"{name}.md")
        if re.search(r"\b(spawn|invoque[rz]?)\s+(l')?agent\b", text, re.I) and "ne spawne" not in text:
            warn("agents.no_spawn", f"{name} : formulation évoquant un spawn d'agent — à vérifier")


# ---------------------------------------------------------------------------
# 2. Invariants : chaque enforcer déclaré existe-t-il ?
# ---------------------------------------------------------------------------
def check_invariants() -> None:
    text = read(SDDA / "INVARIANTS.yml")
    if not text:
        fail("invariants.file", "INVARIANTS.yml illisible")
        return

    ids = re.findall(r"^\s*- id:\s*(\S+)", text, re.M)
    declared_total = re.search(r"^total:\s*(\d+)", text, re.M)

    if declared_total and int(declared_total.group(1)) != len(ids):
        fail(
            "invariants.count",
            f"`total: {declared_total.group(1)}` mais {len(ids)} invariants listés",
        )
    else:
        ok("invariants.count", f"{len(ids)} invariants, compte cohérent")

    # Enforcers : chemins relatifs à la racine du dépôt.
    enforcers = re.findall(r"^\s+- (\.sdda/[\w\-./*{}]+\.py)\s*$", text, re.M)
    missing = sorted({e for e in enforcers if not (ROOT / e).is_file()})
    if missing:
        # Phase de conception : les enforcers sont planifiés, pas encore écrits.
        status = re.search(r"^status:\s*(\S+)", text, re.M)
        design_phase = bool(status and "design" in status.group(1))
        report = warn if design_phase else fail
        report(
            "invariants.enforcers",
            f"{len(missing)}/{len(set(enforcers))} enforcer(s) absent(s) du disque"
            + (" — attendu en phase de conception" if design_phase else ""),
        )
    else:
        ok("invariants.enforcers", f"{len(set(enforcers))} enforcers présents sur disque")


def check_hooks_reachable() -> None:
    """Tout hook du disque est-il réellement atteignable au runtime ?

    `invariants.enforcers` vérifie qu'un enforcer **existe**. Ce contrôle-ci
    vérifie qu'il **s'exécute** — et l'écart entre les deux a laissé cinq gates
    sans bras armé : `preflight_cap_gate`, `preflight_tool_gate`,
    `preflight_retrieval_gate`, `preflight_db_envelope` et
    `postflight_trace_present` étaient écrits, testés, déclarés dans
    `INVARIANTS.yml`, et aucun chemin d'exécution ne les atteignait.

    Un enforcer présent mais non câblé est le pire des deux mondes : il rassure
    comme une protection et se comporte comme une absence. C'est un FAIL, pas un
    WARN — contrairement à un enforcer non écrit, qui est une dette assumée et
    visible.
    """
    hooks_dir = SDDA / "python" / "sdda_hooks"
    modules = sorted(p.stem for p in hooks_dir.glob("*.py") if not p.stem.startswith("_"))
    if not modules:
        fail("hooks.reachable", "aucun hook sur le disque")
        return

    # 1. Chaque module déclare-t-il son câblage ?
    undeclared = [m for m in modules if not re.search(
        r"^WIRING\s*=\s*\{", read(hooks_dir / f"{m}.py") or "", re.M)]

    # 2. Chaque module apparaît-il dans la façade du harnais de référence ?
    settings = read(ROOT / ".claude" / "settings.json") or ""
    unwired = [m for m in modules if f"sdda_hooks/{m}.py" not in settings]

    if undeclared or unwired:
        detail = []
        if undeclared:
            detail.append(f"{len(undeclared)} sans WIRING ({', '.join(undeclared[:3])})")
        if unwired:
            detail.append(f"{len(unwired)} absent(s) de .claude/settings.json ({', '.join(unwired[:3])})")
        fail("hooks.reachable", " · ".join(detail)
             + " — un enforcer non câblé rassure sans protéger ; "
               "`harness_build.py` le recâble depuis son WIRING")
    else:
        ok("hooks.reachable", f"{len(modules)} hooks déclarés et câblés dans la façade de référence")


# ---------------------------------------------------------------------------
# 3. Classes d'erreur : réciprocité émetteurs <-> taxonomie
# ---------------------------------------------------------------------------
def _delegate(check: str, script_name: str, missing_msg: str) -> None:
    """Délègue un contrôle à son script de synchronisation.

    Dupliquer la logique ici produirait deux vérités divergentes — précisément
    le défaut que ces contrôles existent pour attraper.
    """
    import subprocess

    script = SDDA / "python" / "sdda_admin" / script_name
    if not script.is_file():
        fail(check, missing_msg)
        return

    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    output = [line.strip() for line in (result.stdout or "").strip().splitlines()]
    if result.returncode == 0:
        ok(check, output[0].replace("ok — ", "") if output else "ok")
    else:
        fail(check, " · ".join(output[:3]) if output else "échec sans sortie")


def check_error_classes() -> None:
    _delegate(
        "errors.reciprocity",
        "sync_error_registry.py",
        "sync_error_registry.py absent — réciprocité non vérifiable",
    )


def check_digests() -> None:
    _delegate(
        "digests.sync",
        "sync_digests.py",
        "sync_digests.py absent — digests non vérifiables",
    )


def check_counters() -> None:
    """Les chiffres de la prose (agents, fiches, classes, défauts de config…)
    sont ceux du disque — même mécanisme que les digests."""
    _delegate(
        "counters.sync",
        "sync_counters.py",
        "sync_counters.py absent — compteurs de la prose non vérifiables",
    )


# ---------------------------------------------------------------------------
# 4. Références de fichiers : ce que les docs promettent existe-t-il ?
# ---------------------------------------------------------------------------
def check_references() -> None:
    """Références internes.

    Deux catégories à ne pas confondre :
      - `.py` manquant = un enforcer ou un script PLANIFIÉ mais pas encore écrit.
        Attendu en phase de conception ; c'est la dette, pas une erreur.
      - `.md` / `.yml` / `.json` manquant = une promesse de documentation non
        tenue. C'est ce qui produit un framework qui ment sur lui-même.
    """
    planned: set[str] = set()
    broken: set[str] = set()
    checked = 0

    sources = list(SDDA.rglob("*.md")) + list(SDDA.rglob("*.yml")) + list(SDDA.rglob("*.yaml"))
    for path in sources:
        # Les fixtures de test contiennent volontairement des références à des
        # fichiers absents : c'est leur objet.
        if "fixtures" in path.parts or "__pycache__" in path.parts:
            continue
        text = read(path)
        # Lookbehind : écarte `~/.sdda/config.team.yml` (répertoire personnel).
        # Lookahead : sans lui, `STACK.md.template` matche comme `STACK.md` et
        # produit un faux positif sur une référence parfaitement correcte.
        for ref in re.findall(
            r"(?<![~/\w])\.sdda/([\w\-./]+\.(?:md|yml|yaml|json|py))(?![\w.])", text
        ):
            # Les chemins à motif (glob, {n}, {agent}) ne sont pas résolvables.
            if any(ch in ref for ch in "*{"):
                continue
            checked += 1
            if (SDDA / ref).exists():
                continue
            entry = f"{path.relative_to(SDDA)} -> .sdda/{ref}"
            # Tout ce qui vit sous `python/` est du code ou un schéma que le
            # code produit : c'est la dette des lots d'implémentation, pas une
            # promesse de documentation non tenue.
            is_planned = ref.endswith(".py") or ref.startswith("python/")
            (planned if is_planned else broken).add(entry)

    if planned:
        warn("refs.planned", f"{len(planned)} script(s) référencé(s) mais pas encore écrit(s) — dette des lots à venir")
        # Une dette annoncée est une dette ; une dette MUETTE est un prompt qui
        # ment : l'agent croit disposer d'un outil et invente sa sortie. Chaque
        # appel à un script absent doit être suivi d'un « Planifié » qui dit
        # quoi faire tant qu'il manque.
        try:
            sys.path.insert(0, str(SDDA / "python"))
            from sdda_admin import planned_scripts

            pathed, _bare, declared = planned_scripts.collect()
            silent = planned_scripts.undeclared_missing(pathed, declared)
        except Exception as exc:
            warn("refs.planned.undeclared", f"planned_scripts non chargeable ({exc!r})")
            silent = {}
        for ref, callers in sorted(silent.items()):
            fail("refs.planned.undeclared",
                 f"`{ref}` absent et cité sans « Planifié » par {', '.join(sorted(callers)[:3])} — "
                 "le lecteur croit à un outil disponible")
    if broken:
        warn("refs.broken", f"{len(broken)} document(s) référencé(s) mais absent(s) (sur {checked} refs)")
        for item in sorted(broken)[:12]:
            warn("refs.broken.detail", item)
    if not planned and not broken:
        ok("refs", f"{checked} références internes, toutes résolues")
    elif not broken:
        ok("refs.docs", "aucune référence de documentation cassée")


# ---------------------------------------------------------------------------
# 4.bis Numérotation des templates et références `§n`
# ---------------------------------------------------------------------------
#: `## 7. Règles` suivi de `## 7. Retrievers`, sans `## 6` : le contrat d'agent
#: a vécu ainsi pendant tout un lot. Rien ne l'a vu — ni `pytest`, ni `refs`,
#: qui vérifient l'existence des FICHIERS, jamais la cohérence interne d'un
#: template. Les conséquences, elles, étaient réelles : cinq fiches d'agent
#: lisaient `§12 handoffs` là où le template écrivait `§13`, et
#: `lint_prompts.py` cherchait `## 6. Règles` dans un template qui n'en avait
#: pas. Une section décalée ne lève aucune erreur : elle fait lire la mauvaise.
SECTION_HEADING_RE = re.compile(r"^##\s+(\d+)\.\s+\S", re.MULTILINE)


def check_template_numbering() -> None:
    """Chaque template numérote-t-il ses sections sans trou ni doublon ?

    Tolère un départ à `## 0.` (retrieval-contract commence par « Pourquoi du
    RAG »). Au-delà, la suite doit progresser de 1 en 1 : c'est la seule forme
    sur laquelle un `§n` écrit ailleurs peut s'appuyer.
    """
    checked = 0
    broken: list[str] = []

    for path in sorted((SDDA / "templates").glob("*.template.md")):
        numbers = [int(n) for n in SECTION_HEADING_RE.findall(read(path))]
        if not numbers:
            continue
        checked += 1
        name = path.name
        if numbers[0] not in (0, 1):
            broken.append(f"{name} commence à §{numbers[0]}")
        duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
        if duplicates:
            broken.append(f"{name} numéro dupliqué : " + ", ".join(f"§{n}" for n in duplicates))
        gaps = [
            f"§{prev}->§{cur}"
            for prev, cur in zip(numbers, numbers[1:])
            if cur != prev + 1 and cur not in (prev, prev + 1)
        ]
        if gaps:
            broken.append(f"{name} suite non contiguë : " + ", ".join(gaps))

    if broken:
        for item in broken[:8]:
            fail("templates.numbering", item)
    else:
        ok("templates.numbering", f"{checked} templates, numérotation contiguë et sans doublon")


#: Un `§n` n'est vérifiable que si la ligne dit DE QUEL artefact il parle.
#: On ne devine pas : seules les lignes qui nomment un chemin d'artefact ou un
#: template sont contrôlées. C'est étroit — et c'est exactement la classe de
#: régression observée (`contracts/agents/… §12`, `topology.template.md §8`).
ARTIFACT_TEMPLATES: tuple[tuple[str, str], ...] = (
    (r"\.agent\.md", "agent-contract.template.md"),
    (r"\.tool\.md", "tool-contract.template.md"),
    (r"\.retrieval\.md", "retrieval-contract.template.md"),
    (r"-memory\.md|memory-contract", "memory-contract.template.md"),
    (r"-topology\.md|topology\.template\.md", "topology.template.md"),
    (r"eval-suite", "eval-suite.template.md"),
    (r"adr\.template\.md", "adr.template.md"),
)


def _template_sections(name: str, cache: dict[str, set[int]]) -> set[int]:
    if name not in cache:
        cache[name] = {int(n) for n in SECTION_HEADING_RE.findall(read(SDDA / "templates" / name))}
    return cache[name]


def check_section_refs() -> None:
    """Un `§n` cité à côté d'un artefact pointe-t-il une section qui existe ?

    Ne couvre QUE les références désambiguïsées par leur propre ligne. Un `§n`
    isolé dans une phrase (« depuis §9 ») reste hors de portée : le dire est
    plus honnête que de deviner le template visé et de produire un faux
    positif que l'on finirait par désactiver.
    """
    cache: dict[str, set[int]] = {}
    checked = 0
    broken: list[str] = []

    for path in sorted(SDDA.rglob("*.md")):
        if {"fixtures", "__pycache__", "digests"} & set(path.parts):
            continue
        for lineno, line in enumerate(read(path).splitlines(), 1):
            refs = [int(n) for n in re.findall(r"§\s?(\d+)(?![.\d])", line)]
            if not refs:
                continue
            template = next(
                (tpl for pattern, tpl in ARTIFACT_TEMPLATES if re.search(pattern, line)), None
            )
            if template is None:
                continue
            sections = _template_sections(template, cache)
            if not sections:
                continue
            for ref in refs:
                checked += 1
                if ref not in sections:
                    broken.append(
                        f"{path.relative_to(SDDA)}:{lineno} §{ref} absent de {template} "
                        f"(max §{max(sections)})"
                    )

    if broken:
        for item in broken[:8]:
            fail("refs.sections", item)
    else:
        ok("refs.sections", f"{checked} références §n qualifiées, toutes résolues")


# ---------------------------------------------------------------------------
# 1.bis Frontmatter : ce que la façade recevra est-il ce que la fiche dit ?
# ---------------------------------------------------------------------------
def check_frontmatter_intact() -> None:
    """Aucune valeur de frontmatter tronquée par un commentaire YAML.

    En YAML, ` #` en plein scalaire ouvre un commentaire. La description
    d'`architect-data` citait `STACK.md ## Active Data Access` : tout ce qui
    suivait disparaissait à la compilation, et la façade Claude annonçait un
    agent qui « lit la topologie, les CAPs et STACK.md » — sans ce qu'il ÉCRIT
    ni ce qu'il REFUSE. La source était juste, la façade mentait, et rien ne
    comparait les deux.

    Le contrôle relit chaque scalaire brut et le confronte à ce que le parseur
    en garde : un écart est une troncature, donc un échec.
    """
    try:
        sys.path.insert(0, str(SDDA / "python"))
        from sdda_lib import yaml_mini
    except Exception as exc:
        warn("agents.frontmatter", f"yaml_mini non chargeable ({exc!r})")
        return

    truncated: list[str] = []
    checked = 0
    for path in sorted(list((SDDA / "agents").glob("*.md")) + list((SDDA / "commands").glob("*.md"))):
        text = read(path)
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        raw_block = parts[1]
        try:
            meta = yaml_mini.parse_mapping(raw_block)
        except Exception as exc:
            truncated.append(f"{path.relative_to(SDDA)} : frontmatter illisible ({exc})")
            continue
        for line in raw_block.splitlines():
            m = re.match(r"^([A-Za-z_][\w-]*):\s+(.+?)\s*$", line)
            if not m or m.group(2).startswith(("[", "{", '"', "'")):
                continue
            key, raw_value = m.group(1), m.group(2)
            parsed = meta.get(key)
            if not isinstance(parsed, str):
                continue
            checked += 1
            if parsed.strip() != raw_value.strip():
                truncated.append(
                    f"{path.relative_to(SDDA)} : `{key}` perd « {raw_value[len(parsed):].strip()[:60]} » "
                    f"(` #` ouvre un commentaire YAML — reformuler sans dièse)"
                )
    if truncated:
        for item in truncated[:8]:
            fail("agents.frontmatter", item)
    else:
        ok("agents.frontmatter", f"{checked} scalaires de frontmatter intacts après parsing")


# ---------------------------------------------------------------------------
# 4.ter Classes d'erreur citées par la prose normative du framework
# ---------------------------------------------------------------------------
def check_documented_classes() -> None:
    """Une classe annoncée BLOQUANTE dans la doc du framework est-elle émise ?

    `errors.reciprocity` régénère le registre depuis les émetteurs réels —
    donc une classe qui ne vit QUE dans `ARCHITECTURE.md` n'entre jamais au
    registre, et le registre se déclare « à jour » sans la mentionner. C'est
    ainsi que l'API GATE a pu être annoncée bloquante sur une classe de dérive
    de contrat qu'aucun script n'émettait : le dispositif anti-doc-theater ne
    regardait pas la prose qui promet.

    Les classes ne sont jamais citées ENTRE CROCHETS dans ce module : la forme
    crochetée est celle que `sync_error_registry.collect()` reconnaît comme une
    émission, et un exemple en commentaire deviendrait une classe fantôme au
    registre — le défaut que ce contrôle existe pour trouver.

    Périmètre volontairement restreint à `.sdda/*.md` et `.sdda/docs/*.md` : ce
    sont les documents NORMATIFS du framework. Les fiches de `stacks/` citent
    légitimement des classes émises par l'application GÉNÉRÉE ou par le lint
    d'une stack, dont le cycle de vie n'est pas celui des gates.
    """
    try:
        sys.path.insert(0, str(SDDA / "python"))
        from sdda_admin import sync_error_registry as registry
    except Exception as exc:
        warn("errors.documented", f"registre non chargeable ({exc!r})")
        return

    emitted = set(registry.collect())
    prose = sorted(SDDA.glob("*.md")) + sorted(SDDA.glob("docs/*.md"))
    cited: dict[str, set[str]] = {}
    for path in prose:
        for cls in re.findall(r"\[([A-Z][A-Z0-9_]{2,})\]", read(path)):
            if cls in registry.NOT_A_CLASS:
                continue
            cited.setdefault(cls, set()).add(path.name)

    orphans = sorted(cls for cls in cited if cls not in emitted)
    if orphans:
        for cls in orphans[:8]:
            fail("errors.documented", f"[{cls}] annoncée dans {', '.join(sorted(cited[cls]))} — aucun émetteur")
    else:
        ok("errors.documented", f"{len(cited)} classes citées en prose normative, toutes émises")


# ---------------------------------------------------------------------------
# 5. Schémas JSON : parsables ?
# ---------------------------------------------------------------------------
def check_json() -> None:
    bad = []
    count = 0
    for path in SDDA.rglob("*.json"):
        count += 1
        try:
            json.loads(read(path))
        except Exception as exc:
            bad.append(f"{path.relative_to(SDDA)} : {exc}")
    if bad:
        for item in bad:
            fail("json.parse", item)
    else:
        ok("json.parse", f"{count} fichiers JSON valides")


# ---------------------------------------------------------------------------
# 6. Honnêteté du catalogue — le garde-fou contre notre propre faux vert
# ---------------------------------------------------------------------------
def check_honesty() -> None:
    """Un framework qui refuse le faux vert ne doit pas s'en accorder un.

    Toute fiche de stack déclare un `Validation:`. Tant qu'aucun run mesuré
    n'a eu lieu, aucune ne doit se prétendre validée.
    """
    unvalidated = 0
    overclaiming: list[str] = []
    missing_header: list[str] = []
    bad_id: list[str] = []

    for path in (SDDA / "stacks").rglob("*.md"):
        if path.name == "README.md":
            continue
        text = read(path)

        # `Stack ID` doit être préfixé par sa catégorie : le préfixe vaut le nom
        # du dossier, donc deux fiches homonymes dans deux catégories ne peuvent
        # pas entrer en collision (`langsmith` existe en framework, eval et
        # observability).
        expected = f"{path.parent.name}-{path.stem}"
        id_match = re.search(r"^Stack ID:\s*`?([\w-]+)`?\s*$", text, re.M)
        if not id_match:
            bad_id.append(f"{path.relative_to(SDDA)} : `Stack ID:` absent")
        elif id_match.group(1) != expected:
            bad_id.append(f"{path.relative_to(SDDA)} : `{id_match.group(1)}` attendu `{expected}`")

        match = re.search(r"^Validation:\s*(.+)$", text, re.M)
        if not match:
            missing_header.append(str(path.relative_to(SDDA)))
            continue
        value = match.group(1).strip()
        if "design-phase" in value:
            unvalidated += 1
        elif "validated" in value and "non" not in value.lower():
            overclaiming.append(f"{path.relative_to(SDDA)} : « {value[:60]} »")

    if bad_id:
        for item in bad_id[:8]:
            fail("stacks.id", item)
    if missing_header:
        warn("stacks.header", f"{len(missing_header)} fiche(s) sans `Validation:` : {', '.join(missing_header[:5])}")
    if overclaiming:
        for item in overclaiming:
            fail("stacks.overclaim", f"prétend une validation non mesurée — {item}")
    if not overclaiming and not bad_id:
        ok("stacks.honesty", f"{unvalidated} fiche(s) en design-phase, identifiants conformes, aucune sur-déclaration")


def check_libs_catalogs() -> None:
    """Chaque `.libs.json` : schéma, et surtout aucune `ref` morte.

    Une `ref` qui ne pointe vers aucune entrée de `versions` produit une
    dépendance sans version — donc un agent qui en invente une.
    """
    catalogs = sorted((SDDA / "stacks").rglob("*.libs.json"))
    if not catalogs:
        warn("libs.catalogs", "aucun catalogue .libs.json")
        return

    # Le méta-schéma est vérifié ICI et non seulement documenté : il a divergé de
    # ses cinq catalogues sans que rien ne le dise (`capability`, `triggers`,
    # `installCommand`, `plugins`, la moitié de `metadata`). Un schéma que
    # personne n'applique cesse d'être une contrainte et devient un commentaire.
    sys.path.insert(0, str(SDDA / "python"))
    from sdda_lib.jsonschema_mini import SchemaValidator  # noqa: E402  (import tardif : sys.path)

    schema_path = SDDA / "templates" / "libs-catalog.schema.json"
    validator = SchemaValidator(json.loads(read(schema_path))) if schema_path.is_file() else None

    problems: list[str] = []
    schema_problems: list[str] = []
    unverified = 0
    for path in catalogs:
        try:
            data = json.loads(read(path))
        except Exception as exc:
            problems.append(f"{path.relative_to(SDDA)} : JSON invalide ({exc})")
            continue

        rel = path.relative_to(SDDA)
        if data.get("category") != path.parent.name:
            problems.append(f"{rel} : category `{data.get('category')}` != dossier `{path.parent.name}`")

        if validator is not None:
            schema_problems.extend(f"{rel} : {violation}" for violation in validator.validate(data))

        versions = data.get("versions", {})
        for bucket in ("core", "dev", "onDemand", "plugins"):
            for dep in data.get(bucket, []) or []:
                ref = dep.get("ref")
                if ref and ref not in versions:
                    problems.append(f"{rel} : ref morte `{ref}` ({bucket}/{dep.get('module')})")
        for engine, drv in (data.get("dbDrivers") or {}).items():
            if drv.get("ref") not in versions:
                problems.append(f"{rel} : ref morte `{drv.get('ref')}` (dbDrivers/{engine})")

        meta = data.get("metadata") or {}
        if not meta.get("versionsVerified", False):
            unverified += 1
        elif not str(meta.get("verifiedAt") or "").strip():
            # `versionsVerified: true` sans date ne peut pas se périmer, donc ne
            # peut pas être contredit. C'est une déclaration déguisée en mesure —
            # le motif exact que ce champ existe pour empêcher.
            problems.append(f"{rel} : versionsVerified: true sans `verifiedAt`")

    if problems:
        for item in problems[:10]:
            fail("libs.refs", item)
    else:
        ok("libs.refs", f"{len(catalogs)} catalogues, aucune ref morte")

    if validator is None:
        warn("libs.schema", "méta-schéma libs-catalog.schema.json absent — catalogues non validés")
    elif schema_problems:
        for item in schema_problems[:10]:
            fail("libs.schema", item)
    else:
        ok("libs.schema", f"{len(catalogs)} catalogues conformes au méta-schéma")

    if unverified:
        warn(
            "libs.unverified",
            f"{unverified}/{len(catalogs)} catalogue(s) avec versions non vérifiées "
            "contre le registre de paquets — attendu avant le premier bootstrap réel",
        )
    else:
        ok("libs.verified", f"{len(catalogs)} catalogue(s) avec versions vérifiées contre le registre")


# ---------------------------------------------------------------------------
def check_context_budgets() -> None:
    """Chaque budget de `loader.yml` tient-il le contexte STABLE de son agent ?

    Le test se fait à vide, sans workspace : on additionne les lectures communes,
    les `reads:` qui pointent le framework, et le pack que ses `pack_sources`
    produiraient. Si cette part déjà incompressible sature le budget, l'agent ne
    partira jamais sur un projet réel — et ce serait découvert au premier spawn,
    c'est-à-dire trop tard.
    """
    sys.path.insert(0, str(SDDA / "python"))
    from sdda_scripts import context_pack  # noqa: E402  (import tardif : dépend de sys.path)

    loader = context_pack.load_loader(ROOT)
    agents = context_pack.agent_names(loader)
    if not agents:
        fail("context.budgets", "loader.yml ne déclare aucun agent")
        return

    common_patterns = {str(e.get("path") if isinstance(e, dict) else e) for e in loader.get("cross_agent_reads") or []}
    common = sum(p.stat().st_size for pattern in common_patterns
                 for p in context_pack.expand(ROOT, pattern, mission=None, target=None, obj=None)[0])
    tight: list[str] = []
    over: list[str] = []
    missing_pack: list[str] = []
    for agent in agents:
        spec = loader.get(agent) or {}
        budget = int(spec.get("budget_bytes") or 0)
        if not budget:
            over.append(f"{agent} (aucun budget déclaré)")
            continue
        stable = common
        for pattern, _entry in context_pack.read_entries(loader, agent):
            if pattern in common_patterns:
                continue  # déjà compté une fois : le commun ne se paie pas deux fois
            if pattern.startswith(".sdda/"):
                stable += sum(p.stat().st_size for p in context_pack.expand(ROOT, pattern, mission=None, target=None, obj=None)[0])
            elif "/.context/packs/" in pattern and not spec.get("pack_sources"):
                missing_pack.append(agent)
        stable += context_pack.pack_source_bytes(ROOT, loader, agent)
        if stable > budget:
            over.append(f"{agent} ({stable} > {budget})")
        elif stable > 0.80 * budget:
            tight.append(f"{agent} ({stable / budget:.0%})")

    if over:
        fail("context.budgets", f"budget intenable pour {len(over)} agent(s) : {', '.join(over)}")
    elif missing_pack:
        warn("context.budgets", f"{len(sorted(set(missing_pack)))} agent(s) lisent un pack sans `pack_sources` : {', '.join(sorted(set(missing_pack)))}")
    elif tight:
        warn("context.budgets", f"marge < 20 % pour {', '.join(tight)} — un work-item long fera dépasser")
    else:
        ok("context.budgets", f"{len(agents)} budgets tiennent leur contexte stable (commun {common // 1024} Ko)")


def check_catalog_integrity() -> None:
    """Toute ligne de stack proposée par le template pointe-t-elle un fichier réel ?

    Le template annonce 18 frameworks, 11 patterns RAG, 7 surfaces de serving.
    Une ligne commentée qui désigne une fiche inexistante est **activable** : on
    la décommente, `STACK.md` référence un fichier absent, et rien ne le dit
    avant le premier spawn — où l'agent reçoit un pack vide et improvise.

    Le framework a une règle contre ça (« ne pas annoncer un catalogue avant de
    l'avoir mesuré ») et la viole dans son propre template. Ce contrôle la rend
    opposable : une ligne sans fiche doit porter `(fiche absente)`, et devient
    alors une intention déclarée au lieu d'un faux vert.
    """
    template = SDDA / "templates" / "STACK.md.template"
    if not template.is_file():
        fail("catalog.integrity", "STACK.md.template absent")
        return

    line_re = re.compile(r"^#?\s*-\s*(\.sdda/stacks/[A-Za-z0-9._/-]+\.md)\s*(.*)$")
    total = marked = 0
    unmarked: list[str] = []
    for raw in read(template).split("\n"):
        m = line_re.match(raw.strip())
        if not m:
            continue
        ref, tail = m.group(1), m.group(2)
        if "{{" in ref:
            continue  # placeholder substitué par bootstrap.py
        total += 1
        if (ROOT / ref).is_file():
            continue
        if "fiche absente" in tail.lower():
            marked += 1
            continue
        unmarked.append(ref.split("stacks/", 1)[-1])

    if unmarked:
        fail("catalog.integrity",
             f"{len(unmarked)} ligne(s) de STACK.md.template désignent une fiche inexistante "
             f"sans le dire : {', '.join(unmarked[:8])}" + (" …" if len(unmarked) > 8 else ""))
    elif marked:
        ok("catalog.integrity",
           f"{total} lignes de stack, {total - marked} avec fiche, {marked} annoncée(s) « fiche absente »")
    else:
        ok("catalog.integrity", f"{total} lignes de stack, toutes avec une fiche sur disque")


def check_stack_languages() -> None:
    """Chaque fiche déclare `Languages:`, et ce qu'elle déclare existe.

    L'en-tête est ce sur quoi `preflight_stack_combo` refuse une combinaison
    incohérente ([STACK_LANGUAGE_MISMATCH]). Une fiche sans en-tête est un trou
    silencieux dans ce contrôle : le hook ne peut pas la juger, donc il la
    laisse passer — et c'est précisément par là que `lang/csharp.md` +
    `vectorstore/pgvector.md` passait au vert.

    Le hook, lui, ne bloque PAS sur un en-tête manquant : ce serait punir
    l'utilisateur d'un défaut du framework. C'est donc ici, et seulement ici,
    que l'absence est une erreur.
    """
    matrix_path = SDDA / "registry" / "compatibility.matrix.json"
    try:
        known = set((json.loads(read(matrix_path)).get("componentLevels") or {}).get("language") or {})
    except Exception:
        known = set()
    if not known:
        warn("stacks.languages", "componentLevels.language illisible — en-têtes non vérifiables")
        return

    header_re = re.compile(r"^Languages:\s*(.+)$", re.M)
    missing: list[str] = []
    unknown: list[str] = []
    counts: dict[str, int] = {}

    for path in sorted((SDDA / "stacks").rglob("*.md")):
        if path.name == "README.md":
            continue
        rel = str(path.relative_to(SDDA / "stacks")).replace("\\", "/")
        match = header_re.search(read(path))
        if not match:
            missing.append(rel)
            continue
        values = {token.strip() for token in match.group(1).split(",") if token.strip()}
        if not values:
            missing.append(rel)
            continue
        for value in values:
            if value != "*" and value not in known:
                unknown.append(f"{rel} : `{value}`")
        key = "*" if values == {"*"} else "/".join(sorted(values))
        counts[key] = counts.get(key, 0) + 1

    if missing:
        fail("stacks.languages",
             f"{len(missing)} fiche(s) sans en-tête `Languages:` — invisibles pour "
             f"[STACK_LANGUAGE_MISMATCH] : {', '.join(missing[:6])}")
    if unknown:
        for item in unknown[:6]:
            fail("stacks.languages", f"langage inconnu de componentLevels.language — {item}")
    if not missing and not unknown:
        summary = " · ".join(f"{key}: {count}" for key, count in sorted(counts.items()))
        ok("stacks.languages", f"{sum(counts.values())} fiches, couplage déclaré ({summary})")


def check_generated_stack_drift() -> None:
    """La `STACK.md` d'un projet a-t-elle dérivé du gabarit qui l'a produite ?

    `catalog.integrity` audite le TEMPLATE. Mais c'est la copie GÉNÉRÉE que le
    Tech Lead lit et décommente, et elle est figée à la date du bootstrap : un
    projet bootstrapé avant l'ajout des annotations « (fiche absente) » propose
    un menu de 18 frameworks sans le moindre avertissement. Rien ne le disait.

    Deux dérives distinctes, et la seconde est la dangereuse :
      - une SECTION du gabarit manque dans la copie (`## Active Reranker`) —
        le hook ne trouve rien à contrôler, donc il autorise ;
      - une LIGNE de la copie désigne une fiche absente sans le dire — on la
        décommente en confiance.
    """
    template = SDDA / "templates" / "STACK.md.template"
    generated = ROOT / "workspace" / "stack" / "STACK.md"
    if not generated.is_file():
        ok("stack.drift", "aucune STACK.md générée (bootstrap non joué) — sans objet")
        return
    if not template.is_file():
        fail("stack.drift", "STACK.md.template absent")
        return

    template_text, generated_text = read(template), read(generated)

    section_re = re.compile(r"^## (.+)$", re.M)
    expected = [s.strip() for s in section_re.findall(template_text)]
    present = {s.strip() for s in section_re.findall(generated_text)}
    absent_sections = [s for s in expected if s not in present]

    line_re = re.compile(r"^#?\s*-\s*(\.sdda/stacks/[A-Za-z0-9._/-]+\.md)\s*(.*)$")
    unmarked: list[str] = []
    for raw in generated_text.split("\n"):
        m = line_re.match(raw.strip())
        if not m:
            continue
        ref, tail = m.group(1), m.group(2)
        if "{{" in ref or (ROOT / ref).is_file():
            continue
        if "fiche absente" not in tail.lower():
            unmarked.append(ref.split("stacks/", 1)[-1])

    if absent_sections:
        fail("stack.drift",
             f"{len(absent_sections)} section(s) du gabarit absente(s) de workspace/stack/STACK.md — "
             f"rejouer bootstrap ou reporter à la main : {', '.join(absent_sections[:5])}")
    if unmarked:
        fail("stack.drift",
             f"{len(unmarked)} ligne(s) de workspace/stack/STACK.md désignent une fiche inexistante "
             f"sans le dire : {', '.join(sorted(set(unmarked))[:8])}"
             + (" …" if len(set(unmarked)) > 8 else ""))
    if not absent_sections and not unmarked:
        ok("stack.drift", f"workspace/stack/STACK.md alignée sur le gabarit ({len(expected)} sections)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test du framework SDD_Agents.")
    parser.add_argument("--json", action="store_true", help="sortie machine")
    parser.add_argument("--strict", action="store_true", help="un WARN suffit à échouer")
    args = parser.parse_args()

    for check in (
        check_agents,
        check_frontmatter_intact,
        check_invariants,
        check_hooks_reachable,
        check_error_classes,
        check_digests,
        check_counters,
        check_references,
        check_template_numbering,
        check_section_refs,
        check_documented_classes,
        check_json,
        check_honesty,
        check_stack_languages,
        check_libs_catalogs,
        check_catalog_integrity,
        check_generated_stack_drift,
        check_context_budgets,
    ):
        try:
            check()
        except Exception as exc:  # un check cassé ne doit pas masquer les autres
            fail(check.__name__, f"le contrôle a levé : {exc!r}")

    fails = [f for f in FINDINGS if f.level == "fail"]
    warns = [f for f in FINDINGS if f.level == "warn"]

    if args.json:
        print(json.dumps(
            {
                "findings": [asdict(f) for f in FINDINGS],
                "fail": len(fails),
                "warn": len(warns),
                "ok": len(FINDINGS) - len(fails) - len(warns),
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        glyph = {"ok": "  ok  ", " warn ": " warn ", "warn": " WARN ", "fail": " FAIL "}
        print()
        print("  SDD_Agents — smoke test du framework")
        print("  " + "-" * 62)
        for finding in FINDINGS:
            print(f"  [{glyph.get(finding.level, finding.level)}] {finding.check:<26} {finding.message}")
        print("  " + "-" * 62)
        print(f"  {len(FINDINGS) - len(fails) - len(warns)} ok · {len(warns)} warn · {len(fails)} fail")
        print()

    if fails:
        return 1
    if warns and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
