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
    python .sdda/sdda.py framework-smoke
    python .sdda/sdda.py framework-smoke --json
    python .sdda/sdda.py framework-smoke --strict   # WARN => exit 1

Exit codes : 0 = OK · 1 = au moins un FAIL (ou un WARN en --strict)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

SDDA = Path(__file__).resolve().parents[2]
ROOT = SDDA.parent
sys.path.insert(0, str(SDDA / "python"))

from sdda_lib.runtime_io import ensure_utf8_stdout  # noqa: E402

ensure_utf8_stdout()


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
_LAUNCHER_RE = re.compile(r"python \.sdda/sdda\.py\s+([a-z][a-z0-9-]*)(?![\w-])")


def _resolve_subcommand(name: str) -> str | None:
    """`sdda_cli.resolve`, tolérante : un CLI non chargeable ne fait pas échouer le smoke."""
    try:
        sys.path.insert(0, str(SDDA / "python"))
        import sdda_cli

        return sdda_cli.resolve(name)
    except Exception:
        return name          # non vérifiable : ne rien affirmer plutôt que crier


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

        # La forme courte `python .sdda/sdda.py {cmd}` ne laisse plus de chemin
        # à vérifier : c'est `sdda_cli` qui dit si la sous-commande existe. Sans
        # cette résolution, un prompt pourrait appeler un script jamais écrit
        # sans que rien ne le voie — exactement ce que la migration vers la
        # forme courte risquait de faire disparaître.
        for name in set(_LAUNCHER_RE.findall(text)):
            checked += 1
            if _resolve_subcommand(name) is None:
                planned.add(f"{path.relative_to(SDDA)} -> sdda {name}")

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


def check_command_flags() -> None:
    """Toute option citée par un prompt existe dans le script qu'elle invoque.

    Le pendant de `refs.planned`, pour la porte voisine : le script existe, mais
    la commande l'appelle avec une option qu'il n'a pas. `argparse` sort alors
    un `usage:` sur stderr, pas le bloc `ERROR/CAUSE/FIX` avec `[CLASS]` que le
    protocole promet — donc l'agent qui orchestre conclut que le contrôle n'a
    rien dit, et poursuit. Cinq options ont vécu ainsi tout un lot, dont le
    post-check censé refuser un contrat d'outil fautif avant la compilation.
    """
    try:
        sys.path.insert(0, str(SDDA / "python"))
        from sdda_admin import command_flags
    except Exception as exc:  # noqa: BLE001
        warn("refs.flags", f"command_flags non chargeable ({exc!r})")
        return

    findings = command_flags.scan(SDDA)
    if not findings:
        ok("refs.flags", "toutes les options citées existent dans les scripts appelés")
        return
    for f in findings[:12]:
        fail("refs.flags", f"{f['file']}:{f['line']} — `{f['command']} {f['flag']}` absente de {f['script']}")


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
# 4.ter-bis Classes promises par les tables de gate des commandes
# ---------------------------------------------------------------------------
PY_PACKAGES = ("sdda_scripts", "sdda_lib", "sdda_hooks", "sdda_admin")
_CLASS_LITERAL_RE = re.compile(r"""["']([A-Z][A-Z0-9_]{2,})["']""")
_CLASS_CELL_RE = re.compile(r"\[([A-Z][A-Z0-9_*]{2,})\]")
_TABLE_ROW_RE = re.compile(r"^\s*\|")


def python_emitted_classes() -> set[str]:
    """Toute chaîne littérale de forme `CLASSE` dans le code Python du framework.

    Plus large que `sync_error_registry.PY_EMIT_RE` (qui reconnaît l'appel
    d'émission) : une classe peut être choisie dans une table
    (`FAMILY_CLASS[...]`) puis émise par variable. Ce qui compte ici est
    qu'un fichier Python la PORTE — la prose et le Markdown ne comptent pas,
    c'est précisément la différence avec `errors.documented`.
    """
    out: set[str] = set()
    for package in PY_PACKAGES:
        for path in sorted((SDDA / "python" / package).rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            out.update(_CLASS_LITERAL_RE.findall(read(path)))
    return out


def gate_table_classes(commands_dir: Path) -> dict[str, set[str]]:
    """{classe: {fichiers}} depuis la colonne « Classe si KO » des tables des commandes."""
    cited: dict[str, set[str]] = {}
    for path in sorted(commands_dir.glob("*.md")):
        column: int | None = None
        for line in read(path).splitlines():
            if not _TABLE_ROW_RE.match(line):
                column = None
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if column is None:
                if any("Classe si KO" in c for c in cells):
                    column = next(i for i, c in enumerate(cells) if "Classe si KO" in c)
                continue
            if all(set(c) <= set(":- ") for c in cells):
                continue          # la ligne de séparation `|---|---|`
            if column < len(cells):
                for cls in _CLASS_CELL_RE.findall(cells[column]):
                    if "*" in cls:
                        continue  # `[ARCH_*]` : une famille, pas une classe
                    cited.setdefault(cls, set()).add(path.name)
    return cited


def check_gate_classes_emitted() -> None:
    """Chaque classe qu'une table de gate d'une commande promet a-t-elle un émetteur PYTHON ?

    Les commandes portent des tables « # | Contrôle | Classe si KO » : c'est le
    contrat lisible de chaque gate, celui que l'opérateur relit pour savoir ce
    qui bloque. Une classe qui n'y vit qu'en Markdown est une gate racontée :
    `[LATENCY_EXCEEDED_MEASURED]` et `[TOOL_LIVE_UNREACHABLE]` ont été
    promises ainsi, sans qu'aucun script ne les émette. `errors.documented`
    accepte un émetteur Markdown ; ici, seul un littéral Python compte.
    """
    try:
        from sdda_admin import sync_error_registry as registry
        skip = set(registry.NOT_A_CLASS)
    except Exception:
        skip = set()
    cited = gate_table_classes(SDDA / "commands")
    if not cited:
        warn("gates.classes_emitted", "aucune table « Classe si KO » dans .sdda/commands/ — rien à vérifier")
        return
    emitted = python_emitted_classes()
    orphans = sorted(cls for cls in cited if cls not in emitted and cls not in skip)
    for cls in orphans:
        fail("gates.classes_emitted", f"[{cls}] promise par {', '.join(sorted(cited[cls]))} — aucun littéral Python ne l'émet")
    if not orphans:
        ok("gates.classes_emitted", f"{len(cited)} classes promises par les tables de gate, toutes portées par le code")


# ---------------------------------------------------------------------------
# 4.ter-ter Frontmatter des façades Claude : YAML STRICT
# ---------------------------------------------------------------------------
def check_facades_frontmatter_strict() -> None:
    """Chaque `clé: valeur` du frontmatter des façades `.claude/` se relit en YAML strict.

    `yaml_mini` relit la source avec tolérance ; Claude Code, non. Une
    description sans guillemets qui portait `Profile: poc` rendait l'en-tête
    invalide, et le harnais ÉCARTAIT l'agent sans rien dire — `dev-app`
    n'existait plus pour lui, redémarrage ou pas. `harness_build.yaml_scalar`
    émet désormais soit un identifiant nu, soit du JSON (qui est du YAML) ;
    ce contrôle vérifie la façade sur le disque, pas la fonction qui l'écrit.
    Le test `test_harness_frontmatter_yaml.py` le couvre en CI ; ici, le
    smoke le voit aussi.
    """
    try:
        from sdda_admin.harness_build import YAML_PLAIN_RE, YAML_RETYPED, frontmatter_and_body
    except Exception as exc:
        warn("facades.frontmatter_strict", f"harness_build non chargeable ({exc!r})")
        return
    facades = sorted((ROOT / ".claude" / "agents").glob("*.md")) + sorted((ROOT / ".claude" / "commands").glob("*.md"))
    if not facades:
        warn("facades.frontmatter_strict", "aucune façade sous .claude/agents ni .claude/commands — harness-build non joué ?")
        return
    problems: list[str] = []
    checked = 0
    for path in facades:
        text = read(path)
        if not text.startswith("---"):
            problems.append(f"{path.relative_to(ROOT).as_posix()} : aucun frontmatter")
            continue
        head = text.split("\n---", 1)[0].split("\n", 1)[1] if "\n---" in text else ""
        for line in head.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, sep, value = line.partition(":")
            value = value.strip()
            if not sep or not key.strip():
                problems.append(f"{path.relative_to(ROOT).as_posix()} : ligne sans `clé: valeur` — `{line.strip()[:60]}`")
                continue
            checked += 1
            if YAML_PLAIN_RE.match(value) and value.lower() not in YAML_RETYPED:
                continue
            try:
                json.loads(value)
            except ValueError:
                problems.append(f"{path.relative_to(ROOT).as_posix()} : `{key.strip()}` n'est ni un identifiant nu ni du JSON — `{value[:60]}`")
        frontmatter_and_body(text)   # la lecture tolérante doit au moins aboutir
    for item in problems:
        fail("facades.frontmatter_strict", item)
    if not problems:
        ok("facades.frontmatter_strict", f"{checked} scalaires de frontmatter sur {len(facades)} façades, tous en YAML strict")


# ---------------------------------------------------------------------------
# 4.ter-quater Façades des autres harnais : lisibles par LEUR parseur, dans LEURS limites
# ---------------------------------------------------------------------------
def _strict_frontmatter(path: Path, text: str, required: tuple[str, ...], problems: list[str]) -> dict[str, object]:
    """Frontmatter relu comme le fait `facades.frontmatter_strict` : identifiant nu ou JSON."""
    from sdda_admin.harness_build import YAML_PLAIN_RE, YAML_RETYPED

    rel = path.relative_to(ROOT).as_posix()
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        problems.append(f"{rel} : aucun frontmatter")
        return {}
    values: dict[str, object] = {}
    for line in match.group(1).splitlines():
        key, sep, raw = line.partition(":")
        raw = raw.strip()
        if not sep:
            problems.append(f"{rel} : ligne sans `clé: valeur` — `{line[:60]}`")
            continue
        if YAML_PLAIN_RE.match(raw) and raw.lower() not in YAML_RETYPED:
            values[key.strip()] = raw
        elif raw in ("true", "false"):
            values[key.strip()] = raw == "true"
        else:
            try:
                values[key.strip()] = json.loads(raw)
            except ValueError:
                problems.append(f"{rel} : `{key.strip()}` n'est ni un identifiant nu ni du JSON")
    for key in required:
        if not values.get(key):
            problems.append(f"{rel} : `{key}` absent ou vide")
    return values


def _memport_imports(text: str) -> list[str]:
    """Les `@chemin` que l'import de mémoire de Gemini CLI tenterait de résoudre.

    Même règle que `findImports` (github.com/google-gemini/gemini-cli,
    memoryImportProcessor.ts) : un `@` en début de texte ou après un blanc,
    suivi d'un `.`, d'un `/` ou d'une lettre, hors blocs et spans de code.
    """
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = re.sub(r"`[^`]*`", "", line)
        out.extend(re.findall(r"(?:^|(?<=\s))@([./A-Za-z]\S*)", line))
    return out


def check_facades_other_harnesses() -> None:
    """`facades.harnesses` : chaque façade non-Claude se relit par le parseur de son harnais.

    `harness-build --check` prouve qu'une façade est à jour ; il ne prouve pas
    que son harnais sait la lire. Un TOML invalide, un frontmatter que Gemini
    écarte, une règle Antigravity au-delà de 24 000 octets, un `@{…}` que Gemini
    CLI exécute comme injection de fichier : chacun donne une façade à jour
    ET inerte. Ce contrôle relit ce que le harnais relit, avec ses limites
    documentées (constantes de `harness_build`, URL à côté).
    """
    try:
        from sdda_admin import harness_build as hb
    except Exception as exc:
        warn("facades.harnesses", f"harness_build non chargeable ({exc!r})")
        return
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        warn("facades.harnesses", "tomllib indisponible (Python < 3.11) : TOML non relu")
        tomllib = None  # type: ignore[assignment]

    problems: list[str] = []
    checked = 0

    def rel(path: Path) -> str:
        return path.relative_to(ROOT).as_posix()

    # -- dérive et orphelins, harnais par harnais --------------------------
    matrix = hb.load_matrix()
    for name in hb.default_targets(matrix):
        if name not in hb.ADAPTERS or name == "claude-code":
            continue
        plan, _counts = hb.build_harness(name, matrix[name])
        for item in hb.drift(plan) + hb.orphans(plan, hb.ADAPTERS[name]):
            problems.append(f"[{name}] {item} — python .sdda/sdda.py harness-build --prune")

    # -- Codex : agents TOML, hooks.json, pointeur racine ------------------
    for path in sorted((ROOT / ".codex" / "agents").glob("*.toml")):
        checked += 1
        if tomllib is None:
            break
        try:
            data = tomllib.loads(read(path))
        except Exception as exc:
            problems.append(f"{rel(path)} : TOML invalide ({exc})")
            continue
        for key in ("name", "description", "developer_instructions"):
            if not str(data.get(key) or "").strip():
                problems.append(f"{rel(path)} : `{key}` absent (obligatoire pour Codex)")
        if data.get("sandbox_mode") not in (None, *hb.CODEX_SANDBOX_MODES):
            problems.append(f"{rel(path)} : sandbox_mode `{data.get('sandbox_mode')}` hors {hb.CODEX_SANDBOX_MODES}")
        if data.get("model") and data["model"] not in matrix["codex"].tier_models.values():
            problems.append(f"{rel(path)} : modèle `{data['model']}` absent de tier_models de la matrice")
    for path in (ROOT / ".codex" / "hooks.json", ROOT / ".gemini" / "settings.json"):
        if not path.is_file():
            continue
        checked += 1
        try:
            hooks = json.loads(read(path)).get("hooks") or {}
            for event, entries in hooks.items():
                for entry in entries:
                    re.compile(str(entry.get("matcher") or ""))
                    for hook in entry.get("hooks") or []:
                        if hook.get("type") != "command" or "SDDA_HARNESS=" not in str(hook.get("command")):
                            problems.append(f"{rel(path)} : hook {event} sans `SDDA_HARNESS` — son payload "
                                            "ne serait pas traduit, donc jamais jugé")
        except (ValueError, AttributeError, re.error) as exc:
            problems.append(f"{rel(path)} : illisible ({exc})")

    # -- Gemini CLI : commandes TOML, agents, import de la mémoire ---------
    for path in sorted((ROOT / ".gemini" / "commands").glob("*.toml")):
        checked += 1
        if tomllib is None:
            break
        try:
            data = tomllib.loads(read(path))
        except Exception as exc:
            problems.append(f"{rel(path)} : TOML invalide ({exc})")
            continue
        prompt = str(data.get("prompt") or "")
        if not prompt.strip():
            problems.append(f"{rel(path)} : `prompt` absent (obligatoire pour Gemini CLI)")
        for injection in re.findall(r"[@!]\{[^}]*\}", prompt):
            problems.append(f"{rel(path)} : `{injection}` serait exécuté par Gemini CLI (injection de fichier/shell)")
    for path in sorted((ROOT / ".gemini" / "agents").glob("*.md")):
        checked += 1
        values = _strict_frontmatter(path, read(path), ("name", "description"), problems)
        if values.get("name") and not hb.GEMINI_AGENT_NAME_RE.match(str(values["name"])):
            problems.append(f"{rel(path)} : nom `{values['name']}` hors [a-z0-9_-]")
        known = {t for tools in hb.GEMINI_TOOLS.values() for t in tools}
        listed = values.get("tools")
        for tool in listed if isinstance(listed, list) else []:
            if tool not in known:
                problems.append(f"{rel(path)} : outil `{tool}` inconnu de Gemini CLI")
    memory = ROOT / ".gemini" / "GEMINI.md"
    if memory.is_file():
        stray = _memport_imports(read(memory))
        if stray:
            problems.append(f".gemini/GEMINI.md : `@{stray[0]}` serait résolu comme import par Gemini CLI")

    # -- Antigravity : règles, agents -------------------------------------
    for path in sorted((ROOT / ".agents" / "rules").glob("*.md")):
        checked += 1
        size = len(path.read_bytes())
        if size > hb.ANTIGRAVITY_RULE_MAX_BYTES:
            problems.append(f"{rel(path)} : {size} octets > {hb.ANTIGRAVITY_RULE_MAX_BYTES} (limite par règle)")
        values = _strict_frontmatter(path, read(path), ("trigger",), problems)
        if values.get("trigger") and values["trigger"] not in hb.ANTIGRAVITY_RULE_TRIGGERS:
            problems.append(f"{rel(path)} : trigger `{values['trigger']}` hors {hb.ANTIGRAVITY_RULE_TRIGGERS}")
        if values.get("trigger") == "model_decision" and not values.get("description"):
            problems.append(f"{rel(path)} : `model_decision` exige une description")
    for path in sorted((ROOT / ".agents" / "agents").glob("*.md")):
        checked += 1
        values = _strict_frontmatter(path, read(path), ("name", "description"), problems)
        if values.get("model") and values["model"] not in hb.ANTIGRAVITY_MODELS:
            problems.append(f"{rel(path)} : model `{values['model']}` hors {hb.ANTIGRAVITY_MODELS}")

    # -- Skills partagées Codex / Antigravity -----------------------------
    for path in sorted((ROOT / hb.SKILLS_DIR).glob("*/SKILL.md")):
        checked += 1
        values = _strict_frontmatter(path, read(path), ("name", "description"), problems)
        if values.get("name") and values["name"] != path.parent.name:
            problems.append(f"{rel(path)} : name `{values['name']}` ≠ dossier `{path.parent.name}`")

    # -- Pointeurs racine ---------------------------------------------------
    for filename, limit in (("AGENTS.md", hb.CODEX_PROJECT_DOC_MAX_BYTES), ("GEMINI.md", hb.ANTIGRAVITY_RULE_MAX_BYTES)):
        path = ROOT / filename
        checked += 1
        if not path.is_file():
            problems.append(f"{filename} absent : le harnais qui le lit à la racine ne voit aucune façade")
            continue
        size = len(path.read_bytes())
        if size > min(limit, hb.ANTIGRAVITY_RULE_MAX_BYTES):
            problems.append(f"{filename} : {size} octets — au-delà, Codex tronque en silence et Antigravity coupe")
    if (ROOT / "GEMINI.md").is_file() and "@./.gemini/GEMINI.md" not in read(ROOT / "GEMINI.md"):
        problems.append("GEMINI.md n'importe pas .gemini/GEMINI.md : Gemini CLI ne charge pas l'architecture")

    for item in problems:
        fail("facades.harnesses", item)
    if not problems:
        ok("facades.harnesses", f"{checked} fichiers de façade Codex / Gemini CLI / Antigravity relus par leur format")


# ---------------------------------------------------------------------------
# 4.quater Parité des jumeaux de documentation (`X.md` anglais / `X.fr.md`)
# ---------------------------------------------------------------------------
#: Un jumeau manquant est un ÉCHEC : toutes les docs ont leur référence anglaise
#: et leur jumeau français depuis le lot 2 de traduction, et une nouvelle page
#: sans jumeau ne doit plus pouvoir entrer. Une paire DIVERGENTE est toujours un
#: échec : deux pages qui disent deux choses différentes sont pire qu'une page seule.
MISSING_TWIN_IS_FAILURE = True

#: Où vivent les paires. Hors de ces emplacements, rien n'a de jumeau : les
#: prompts (`agents/`, `commands/`, `rules/`, `stacks/`, `templates/`,
#: `digests/`) restent en français seul, par décision.
TWIN_GLOBS: tuple[str, ...] = ("*.md", ".sdda/*.md", ".sdda/docs/*.md", ".sdda/python/**/README*.md")

#: Pages anglaises qui n'ont pas de jumeau, par nature : journal de versions
#: anglais, pointeurs racine générés pour Codex et Gemini CLI.
TWIN_EXEMPT: frozenset[str] = frozenset({"CHANGELOG.md", "AGENTS.md", "GEMINI.md"})
_TOOL_CACHE_DIRS: frozenset[str] = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                                              ".venv", "venv", "node_modules", "build", "dist"})

#: Blocs dont les COMMANDES doivent être identiques dans les deux langues : une
#: commande traduite n'est plus la même commande. Leurs commentaires se
#: traduisent (cf. `_strip_shell_comments`).
_VERBATIM_LANGS = frozenset({"bash", "sh", "shell", "console"})
_FENCE_RE = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_CLASS_RE = re.compile(r"\[([A-Z][A-Z0-9_]{2,})\]")
_MARKER_RE = re.compile(r"<!--sdda:(count|config) ([A-Za-z0-9_]+)-->")
_LINK_RE = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")


_SHELL_COMMENT_RE = re.compile(r"\s+#[^'\"]*$")


def _strip_shell_comments(lines: list[str]) -> list[str]:
    """Les commandes d'un bloc shell, sans leurs commentaires.

    Une commande ne se traduit pas ; son commentaire, si : `# taxonomy` et
    `# taxonomie` décrivent la même ligne. Comparer les commentaires ferait
    échouer toute traduction honnête, et on finirait par désactiver le contrôle.
    """
    out: list[str] = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        out.append(_SHELL_COMMENT_RE.sub("", line).rstrip())
    return out


def doc_fingerprint(text: str) -> dict:
    """Ce qui doit être identique entre une page et son jumeau.

    Classes citées, nombre de titres par niveau (hors blocs de code, où `#` est
    un commentaire), marqueurs `sdda:count`/`sdda:config` dans l'ordre, langage
    de chaque bloc de code, et commandes des blocs shell (commentaires exclus).
    """
    headings: dict[int, int] = {}
    fences: list[str] = []
    verbatim: list[str] = []
    in_block: str | None = None
    fence_mark = ""
    buffer: list[str] = []
    for line in text.splitlines():
        m = _FENCE_RE.match(line)
        if in_block is None:
            if m:
                fence_mark, in_block = m.group(1), m.group(2).lower()
                fences.append(in_block)
                buffer = []
                continue
            h = _HEADING_RE.match(line)
            if h:
                headings[len(h.group(1))] = headings.get(len(h.group(1)), 0) + 1
        else:
            if m and m.group(1) == fence_mark and not m.group(2):
                if in_block in _VERBATIM_LANGS:
                    verbatim.append("\n".join(_strip_shell_comments(buffer)))
                in_block = None
                continue
            buffer.append(line.rstrip())
    return {
        "classes": sorted(set(_CLASS_RE.findall(text))),
        "headings": dict(sorted(headings.items())),
        "markers": [f"{kind}:{name}" for kind, name in _MARKER_RE.findall(text)],
        "fences": fences,
        "verbatim": verbatim,
    }


def parity_diffs(english: str, french: str) -> list[str]:
    """Les écarts de contenu technique entre une page anglaise et son jumeau."""
    en, fr = doc_fingerprint(english), doc_fingerprint(french)
    out: list[str] = []
    only_en = sorted(set(en["classes"]) - set(fr["classes"]))
    only_fr = sorted(set(fr["classes"]) - set(en["classes"]))
    if only_en or only_fr:
        out.append("classes " + " ".join([f"+en:{c}" for c in only_en] + [f"+fr:{c}" for c in only_fr])[:160])
    if en["headings"] != fr["headings"]:
        out.append(f"titres par niveau en {en['headings']} ≠ fr {fr['headings']}")
    if en["markers"] != fr["markers"]:
        out.append(f"marqueurs sdda en {len(en['markers'])} ≠ fr {len(fr['markers'])} (ou ordre différent)")
    if en["fences"] != fr["fences"]:
        out.append(f"blocs de code en {len(en['fences'])} ≠ fr {len(fr['fences'])} (ou langages différents)")
    elif en["verbatim"] != fr["verbatim"]:
        diff = next(i for i, (a, b) in enumerate(zip(en["verbatim"], fr["verbatim"])) if a != b)
        out.append(f"bloc de commandes n°{diff + 1} différent — une commande ne se traduit pas")
    return out


def twin_candidates(root: Path) -> list[Path]:
    """Les pages anglaises qui doivent avoir un jumeau `.fr.md`."""
    found: set[Path] = set()
    for pattern in TWIN_GLOBS:
        for path in root.glob(pattern):
            if path.name.endswith(".fr.md") or path.name in TWIN_EXEMPT:
                continue
            # Caches d'outils : leurs README sont écrits par pytest, mypy, ruff…
            # (gitignorés), pas par nous — ils n'ont ni lecteur ni jumeau.
            if _TOOL_CACHE_DIRS & set(path.parts):
                continue
            # Les fixtures de test sont des données, sauf leur README.
            if "fixtures" in path.relative_to(root).parts and path.name != "README.md":
                continue
            found.add(path)
    return sorted(found)


def twin_path(path: Path) -> Path:
    return path.with_name(path.name[: -len(".md")] + ".fr.md")


def hub_listed_docs(hub: Path) -> list[Path]:
    """Les pages `.md` que le hub de documentation référence (liens relatifs)."""
    out: list[Path] = []
    for target in _LINK_RE.findall(read(hub)):
        if "://" in target or target.endswith(".fr.md"):
            continue
        path = (hub.parent / target).resolve()
        if path.is_file() and path not in out:
            out.append(path)
    return out


def check_docs_parity(root: Path | None = None, *, missing_is_failure: bool | None = None) -> None:
    """`docs.parity` : un jumeau dit la même chose que sa page, techniquement.

    La traduction est libre ; le contenu technique ne l'est pas. Une classe
    citée d'un côté seulement, une section en plus, un marqueur de compteur
    oublié ou une commande « traduite » font deux références pour une même
    règle — et c'est celle que personne ne relit qui gouverne.
    """
    base = root or ROOT
    missing_fails = MISSING_TWIN_IS_FAILURE if missing_is_failure is None else missing_is_failure
    on_missing = fail if missing_fails else warn

    pairs = 0
    missing: list[str] = []
    diverged = 0
    for english in twin_candidates(base):
        rel = english.relative_to(base).as_posix()
        french = twin_path(english)
        if not french.is_file():
            missing.append(rel)
            continue
        pairs += 1
        for diff in parity_diffs(read(english), read(french)):
            diverged += 1
            fail("docs.parity", f"{rel} ↔ {french.name} : {diff}")

    # Orphelins : un `.fr.md` sans page anglaise n'est le jumeau de rien.
    for pattern in TWIN_GLOBS:
        for french in base.glob(pattern):
            if not french.name.endswith(".fr.md"):
                continue
            english = french.with_name(french.name[: -len(".fr.md")] + ".md")
            if not english.is_file():
                diverged += 1
                fail("docs.parity", f"{french.relative_to(base).as_posix()} sans page anglaise `{english.name}`")

    hub = base / ".sdda" / "docs" / "README.md"
    hub_missing: list[str] = []
    if hub.is_file():
        for doc in hub_listed_docs(hub):
            if doc.suffix == ".md" and not twin_path(doc).is_file():
                try:
                    hub_missing.append(doc.relative_to(base.resolve()).as_posix())
                except ValueError:
                    hub_missing.append(doc.name)

    for rel in sorted(set(missing) | set(hub_missing)):
        on_missing("docs.twins", f"{rel} sans jumeau `.fr.md`"
                   + (" (listé par le hub)" if rel in hub_missing else ""))
    if not diverged:
        ok("docs.parity", f"{pairs} paire(s) en/fr au même contenu technique"
           + (f" · {len(set(missing) | set(hub_missing))} jumeau(x) manquant(s)" if missing or hub_missing else ""))


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
                sizes = [p.stat().st_size for p in context_pack.expand(ROOT, pattern, mission=None, target=None, obj=None)[0]]
                # `.sdda/stacks/{cat}/{placeholder}.md` désigne LA fiche active,
                # une seule par projet : à vide, le placeholder s'étend à toutes
                # les fiches de la catégorie, et chaque langage ajouté gonflait
                # un budget qu'aucun run réel ne consomme. On compte la pire.
                one_of = pattern.startswith(".sdda/stacks/") and re.search(r"\{[a-z_]+\}", pattern)
                stable += (max(sizes) if sizes else 0) if one_of else sum(sizes)
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
        check_command_flags,
        check_template_numbering,
        check_section_refs,
        check_documented_classes,
        check_gate_classes_emitted,
        check_facades_frontmatter_strict,
        check_facades_other_harnesses,
        check_docs_parity,
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
