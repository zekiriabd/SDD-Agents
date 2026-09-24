#!/usr/bin/env python3
"""La combinaison de stacks activée est chargeable, cohérente, et son niveau est dit.

Le hook que `registry/compatibility.matrix.json` nomme dans
`comboSignatureFields` et que `STACK.md.template` annonce — et qui n'existait
pas. Un registre qui désigne son garde sans que le garde existe est la forme la
plus tranquille du doc-theater : tout le monde le cite, personne ne l'exécute.

Sept contrôles, sept pannes distinctes :

1. **Chargeabilité** — `missing` vs `untested`. La distinction est le tout du
   sujet :

   - `missing` — une ligne activée dans `STACK.md` pour un composant **sans
     fiche sur disque**. Elle ne charge rien. L'agent travaille alors sans le
     mapping de couches, sans les idiomes, sans le `.libs.json` : il invente.
     C'est un refus, parce que c'est une panne silencieuse maquillée en
     configuration.
   - `untested` / `experimental` — la fiche existe, elle n'a pas encore été
     mesurée de bout en bout. C'est l'état nominal du framework en phase de
     conception (`frameworkStatus: design-phase`) : refuser ici bloquerait tout,
     y compris la combo C1 que la ROADMAP construit. On le **dit**, on ne bloque
     pas.

2. **Cohérence de langage** — `[STACK_LANGUAGE_MISMATCH]`. Le contrôle (1) ne
   regarde que l'existence du fichier, et c'est un trou : `lang/csharp.md` +
   `vectorstore/pgvector.md` + `rag/hybrid.md` passait au VERT. Les trois fiches
   existent — mais les deux dernières ne contiennent que du `psycopg` et
   déclarent « Suppose `lang/python.md` ». Le dev .NET reçoit du Python comme
   référence d'implémentation et l'agent générateur invente une traduction.
   C'est exactement le faux vert que ce framework existe pour empêcher, et il se
   produisait dans son propre garde-fou. Chaque fiche déclare désormais son
   en-tête `Languages:` ; `*` vaut « neutre, aucun runtime supposé ».

3. **Dérive de configuration** — `[RETRIEVAL_CONFIG_DRIFT]`. `RerankEnabled:
   true` avec `rerank/none.md` actif est une clé lue que rien n'implémente.
   Avant la catégorie `rerank/`, ces clés ne chargeaient RIEN quelle que soit
   leur valeur ; elles ont maintenant une fiche derrière, donc un mensonge
   possible — et un contrôle.

4. **Valeurs** — `[CONFIG_VALUE_INVALID]`, `[JUDGE_SAME_AS_EVALUATED]`… :
   `layered_config.validate_config` et `judge_issues`, les mêmes que le smoke.

5. **Reconnaissance de la combinaison** — `[STACK_COMBO_UNLISTED]`. La
   signature (`comboSignatureFields` : langage, ensemble des frameworks,
   pattern racine, RAG, vector store, reranker, accès données, surface,
   fournisseur runtime) est calculée depuis STACK.md et cherchée dans
   `combos[]`. Absente : `StackComboCheck: strict` (défaut) refuse, `warn` le
   dit, `off` se tait — et `off` exige lui-même un ADR. La matrice annonçait ce
   contrôle depuis sa création ; le hook ne lisait ni `combos` ni la signature.

6. **Décisions refusées par défaut** — `[ADR_MISSING]`. Toute exigence active
   de `registry/adr-requirements.yml` (`DbAgentRole: full`, `MemoryPIIPolicy:
   allow`, pattern `network`…) sans ADR ACCEPTÉ refuse le spawn des agents qui
   construisent (`dev-*`, `qa-*`, `review-*`). Cette docstring le promettait ;
   rien ne le faisait.

7. **Valeurs sans implémentation** — `[STACK_VALUE_UNIMPLEMENTED]`. Ce que les
   parseurs acceptent et que rien n'exécute : mémoire long terme, stores
   distants (s3, http, mcp…), connecteurs `http-api` / `mcp`, garde-fou nommé
   sans fiche active, reprise humaine sans checkpointing. `smb`/`nfs` sont
   dits (lus comme chemins montés), pas refusés.

Bypass : `SDDA_ALLOW_UNTESTED_COMBO=1` (chargeabilité et combinaison hors matrice, hérité de SDD_Pro) et
`SDDA_ALLOW_LANG_MISMATCH=1` (cohérence de langage — le cas légitime est le
projet polyglotte, un reranker Python en side-car d'une application .NET). Les
deux sont audit-loggués.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _hook import ALLOW, agent_of, allow, bypassed, deny, run  # noqa: E402

HOOK = "preflight_stack_combo"

#: Câblage — lu par `harness_build.py`. Tout spawn : une combinaison non
#: chargeable fausse le travail du PREMIER agent qui lit une fiche de stack, et
#: tous les suivants héritent de l'invention.
WIRING = {"event": "PreToolUse", "matcher": "Task|Agent", "applies_to": ()}

#: Section de `STACK.md` -> catégorie de `componentLevels`. Titres SANS `## ` :
#: `markdown_io.section_body` préfixe lui-même, et le lui donner deux fois rend
#: la section introuvable — donc « aucune stack activée », donc un hook qui
#: autorise tout en ayant l'air de travailler.
#:
#: Seules les sections dont l'absence de fiche change ce que l'agent LIT sont
#: contrôlées ; les clés scalaires (`ChunkSize`, seuils…) relèvent du
#: JSON-schema, pas d'ici.
SECTIONS = {
    "Active Language & Runtime": "language",
    "Active Agent Framework": "framework",
    "Active Orchestration Pattern": "orchestration",
    "Active RAG Pattern": "rag",
    "Active Retrieval Stack": "vectorstore",
    "Active Reranker": "rerank",
    "Active Data Access": "dataaccess",
    "Active Memory Strategy": "memory",
    "Active Tools & Integrations": "tools",
    "Active Eval Stack": "eval",
    "Active Observability": "observability",
    "Active Guardrails": "guardrails",
    "Active Serving Surface": "serving",
    "Active Architecture Pattern": "archi",
    "Active Backend Stack": "backend",
}


#: Le framework, résolu depuis l'emplacement de ce module et non depuis la
#: racine du projet : `.sdda/` peut être vendoré ailleurs que sous le projet
#: inspecté, et le catalogue est celui du framework qui s'exécute — jamais une
#: copie que le projet porterait.
SDDA = Path(__file__).resolve().parents[2]


def _matrix() -> dict:
    return json.loads((SDDA / "registry" / "compatibility.matrix.json").read_text(encoding="utf-8-sig"))


def _activated(root: Path) -> list[tuple[str, str]]:
    """`[(catégorie, composant)]` réellement activés dans `STACK.md`."""
    from sdda_lib.layered_config import active_stacks  # noqa: E402

    out: list[tuple[str, str]] = []
    for heading, category in SECTIONS.items():
        for name in active_stacks(root, heading):
            out.append((category, name))
    return out


#: Catégorie de `componentLevels` -> répertoire(s) de `stacks/`. Le nom de la
#: catégorie n'est pas toujours celui du répertoire (`language` vit sous
#: `lang/`), et `## Active Retrieval Stack` active à la fois un vector store et
#: un modèle d'embedding. Sans cette table, un composant parfaitement présent
#: est déclaré absent — un faux rouge, et le premier réflexe sera de couper le
#: hook.
CATEGORY_DIRS = {
    "language": ("lang",),
    "vectorstore": ("vectorstore", "embedding"),
}


def _fiche_path(category: str, name: str) -> Path | None:
    """Le chemin de la fiche sur disque, ou `None` si aucune ne correspond."""
    for directory in CATEGORY_DIRS.get(category, (category,)):
        candidate = SDDA / "stacks" / directory / f"{name}.md"
        if candidate.is_file():
            return candidate
    return None


#: `Languages: python, csharp` | `Languages: *`. En-tête de fiche, contrôlé
#: présent par `framework_smoke.stacks.languages` : un hook ne doit pas punir
#: l'utilisateur d'un défaut du framework, donc son absence est dite ici, pas
#: refusée.
_LANGUAGES_RE = re.compile(r"^Languages:\s*(.+)$", re.M)


def _declared_languages(path: Path) -> set[str] | None:
    """Langages déclarés par une fiche. `{'*'}` = neutre. `None` = non déclaré."""
    try:
        match = _LANGUAGES_RE.search(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return None
    if not match:
        return None
    return {token.strip() for token in match.group(1).split(",") if token.strip()}


def _check_languages(activated: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """`(incohérences, fiches sans en-tête)` au regard du langage actif."""
    languages = [name for category, name in activated if category == "language"]
    if len(languages) != 1:
        # 0 = section absente (bootstrap non joué) ; 2+ = `[STACK_MALFORMED]`,
        # qui relève de validate_architecture et non d'ici. Sans langage actif
        # unique, la question « cohérent avec quoi ? » n'a pas de réponse.
        return [], []

    active = languages[0]
    mismatched: list[str] = []
    undeclared: list[str] = []
    for category, name in activated:
        if category == "language":
            continue
        path = _fiche_path(category, name)
        if path is None:
            continue  # déjà compté en `missing`
        declared = _declared_languages(path)
        if declared is None:
            undeclared.append(f"{category}/{name}")
        elif "*" not in declared and active not in declared:
            mismatched.append(f"{category}/{name} ({'/'.join(sorted(declared))})")
    return mismatched, undeclared


#: Vector stores qui peuvent vivre DANS la base métier — `Mode:
#: same-as-database` n'est légitime que pour eux. Tous les autres sont des
#: services distincts : sans `Endpoint`, personne ne sait où ils sont.
EMBEDDED_STORES = {"pgvector"}


def _retrieval_drift(root: Path, activated: list[tuple[str, str]]) -> str | None:
    """Dérives de `## Active Retrieval Stack` : reranking et connexion au store.

    Les deux sont la même panne sous deux formes — une clé lue que rien
    n'implémente. Elles ne se voient jamais à l'exécution : le reranking
    n'a simplement pas lieu, et la connexion tombe sur la base métier.
    """
    from sdda_lib.layered_config import read_stack_section_kv  # noqa: E402

    values = read_stack_section_kv(root, "Active Retrieval Stack")

    def truthy(raw: object) -> bool:
        if isinstance(raw, str):
            return raw.strip().lower() in {"true", "yes", "on", "1"}
        return bool(raw)

    if [name for category, name in activated if category == "rerank"] == ["none"]:
        if truthy(values.get("RerankEnabled")):
            return "RerankEnabled: true alors que `## Active Reranker` -> rerank/none.md"

    stores = [
        name for category, name in activated
        if category == "vectorstore" and (SDDA / "stacks" / "vectorstore" / f"{name}.md").is_file()
    ]
    connection = values.get("VectorStoreConnection")
    if stores and isinstance(connection, dict):
        mode = str(connection.get("Mode") or "").strip() or "same-as-database"
        external = [name for name in stores if name not in EMBEDDED_STORES]
        if mode == "same-as-database" and external:
            return (f"VectorStoreConnection.Mode: same-as-database alors que le store actif "
                    f"`{external[0]}` est un service distinct de la base métier")
        if mode == "dedicated" and not str(connection.get("Endpoint") or "").strip():
            return "VectorStoreConnection.Mode: dedicated sans Endpoint — l'index n'a aucune adresse"
    return None


#: `- .sdda/stacks/{répertoire}/{nom}.md` — le répertoire compte : `## Active
#: Retrieval Stack` active un vector store ET un modèle d'embedding.
_ACTIVE_PATH_RE = re.compile(r"^\s*-\s+\.sdda/stacks/([\w-]+)/([\w.-]+)\.md\s*(?:#.*)?$", re.M)


def _active_paths(root: Path, heading: str) -> list[tuple[str, str]]:
    from sdda_lib import markdown_io, paths  # noqa: E402

    p = paths.stack_md_path(root)
    if not p.is_file():
        return []
    body = markdown_io.section_body(markdown_io.read_text(p), heading) or ""
    return _ACTIVE_PATH_RE.findall(body)


#: Champ de signature -> (section, répertoire de fiche). `provider` n'est pas
#: une fiche : c'est `RuntimeProvider`, lu à part.
SIGNATURE_SOURCES = {
    "language": ("Active Language & Runtime", "lang"),
    "framework": ("Active Agent Framework", "framework"),
    "orchestration": ("Active Orchestration Pattern", "orchestration"),
    "rag": ("Active RAG Pattern", "rag"),
    "vectorstore": ("Active Retrieval Stack", "vectorstore"),
    "rerank": ("Active Reranker", "rerank"),
    "dataaccess": ("Active Data Access", "dataaccess"),
    "serving": ("Active Serving Surface", "serving"),
}


def signature(root: Path) -> dict[str, object]:
    """La combinaison activée, dans les termes de `comboSignatureFields`.

    `framework` est un ensemble (la composition est explicite) ; les autres
    champs sont une valeur, `none` quand la section n'active rien.
    """
    from sdda_lib.layered_config import read_stack_section_kv  # noqa: E402

    sig: dict[str, object] = {}
    for fname, (heading, directory) in SIGNATURE_SOURCES.items():
        names = sorted(n for d, n in _active_paths(root, heading) if d == directory)
        if fname == "framework":
            sig[fname] = names
        else:
            sig[fname] = names[0] if len(names) == 1 else ("none" if not names else ",".join(names))
    sig["provider"] = str(read_stack_section_kv(root, "Runtime Models").get("RuntimeProvider") or "none").strip()
    return sig


def mismatches(combo: dict, sig: dict[str, object], fields: list[str]) -> list[str]:
    """Les champs de `fields` où la combinaison activée s'écarte de `combo`."""
    out = []
    for fname in fields:
        declared, active = combo.get(fname), sig.get(fname)
        if fname == "framework":
            ok = sorted(declared or []) == sorted(active or [])
        elif isinstance(declared, list):
            ok = active in declared
        else:
            ok = str(declared if declared not in (None, "") else "none") == str(active)
        if not ok:
            out.append(f"{fname}={active if not isinstance(active, list) else '+'.join(active) or 'none'} "
                       f"(combo : {declared if not isinstance(declared, list) else '|'.join(declared)})")
    return out


def recognise(matrix: dict, sig: dict[str, object]) -> tuple[dict | None, dict | None, list[str]]:
    """`(combo reconnue, combo la plus proche, écarts à la plus proche)`."""
    fields = list((matrix.get("comboSignatureFields") or {}).get("fields") or SIGNATURE_SOURCES)
    best: tuple[dict | None, list[str]] = (None, [])
    for combo in matrix.get("combos") or []:
        gaps = mismatches(combo, sig, fields)
        if not gaps:
            return combo, combo, []
        if best[0] is None or len(gaps) < len(best[1]):
            best = (combo, gaps)
    return None, best[0], best[1]


#: Valeurs que les parseurs ACCEPTENT et que rien n'implémente. Chacune passait
#: en silence : `source_registry` admet `connector: http-api` et `kind: s3`,
#: l'IR compile `LongTermEnabled: true` — et le runtime généré ne sait lire que
#: des fichiers sur un chemin, sans aucune mémoire longue. L'agent recevait une
#: déclaration valide et inventait le client qui manquait.
#:
#: Connecteurs / stores : le runtime (`templates/runtime/python/data/`) résout
#: une racine de fichiers et rien d'autre. `smb` / `nfs` passent s'ils sont
#: MONTÉS (un chemin que l'OS résout) : dit, pas refusé.
IMPLEMENTED_CONNECTORS = {"file"}
MOUNTED_STORE_KINDS = {"smb", "nfs"}
IMPLEMENTED_STORE_KINDS = {"local"} | MOUNTED_STORE_KINDS


def _unimplemented(root: Path, activated: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """`(refus, avertissements)` pour les valeurs acceptées sans implémentation."""
    from sdda_lib.layered_config import read_stack_section_kv  # noqa: E402

    refused: list[str] = []
    said: list[str] = []
    active = {(c, n) for c, n in activated}

    memory = read_stack_section_kv(root, "Active Memory Strategy")
    store = str(memory.get("LongTermStore") or "none").strip().lower()
    if memory.get("LongTermEnabled") is True or store not in ("", "none"):
        refused.append(f"mémoire long terme (`LongTermEnabled: {memory.get('LongTermEnabled')}`, `LongTermStore: {store}`) "
                       "— aucune fiche memory/vector|store, aucun module runtime : l'IR la compile, rien ne l'exécute")

    if ("dataaccess", "declared-sources") in active:
        sources = read_stack_section_kv(root, "Active Data Sources")
        for s in sources.get("Stores") or []:
            if not isinstance(s, dict):
                continue
            kind = str(s.get("kind") or "")
            if kind not in IMPLEMENTED_STORE_KINDS:
                refused.append(f"store `{s.get('id')}` kind `{kind}` — aucun client runtime (seuls `local`, et `smb`/`nfs` montés, sont lus)")
            elif kind in MOUNTED_STORE_KINDS:
                said.append(f"store `{s.get('id')}` kind `{kind}` lu comme un chemin monté : le montage n'est pas vérifié")
        for s in sources.get("Sources") or []:
            if isinstance(s, dict) and str(s.get("connector") or "file") not in IMPLEMENTED_CONNECTORS:
                refused.append(f"source `{s.get('id')}` connector `{s.get('connector')}` — aucun client runtime (seul `file` est implémenté)")

    guards = read_stack_section_kv(root, "Active Guardrails")
    fiches = {n for c, n in activated if c == "guardrails"}
    for key in ("InputGuardrails", "OutputGuardrails"):
        for name in guards.get(key) or []:
            if str(name) not in fiches:
                refused.append(f"`{key}: [{name}]` sans fiche `guardrails/{name}.md` active — le garde-fou nommé n'existe pas")

    serving = read_stack_section_kv(root, "Active Serving Surface")
    if serving.get("HumanInTheLoopEnabled") is True and ("framework", "langgraph") not in active:
        refused.append("`HumanInTheLoopEnabled: true` sans `framework/langgraph.md` — aucune autre fiche active ne porte le checkpointing qu'une reprise humaine exige")
    return refused, said


def _combo_check_mode(root: Path) -> str:
    """`StackComboCheck` effectif : strict (défaut) | warn | off."""
    from sdda_lib.errors import SddaError  # noqa: E402
    from sdda_lib.layered_config import read_layered_config  # noqa: E402
    import io

    try:
        value = read_layered_config(root, warn_stream=io.StringIO()).get("StackComboCheck", "strict")
    except SddaError:
        return "strict"
    value = str(value).strip().lower()
    return value if value in ("strict", "warn", "off") else "strict"


#: Agents qui IMPLÉMENTENT une décision : au-delà de la topologie, une décision
#: refusée par défaut sans ADR accepté ne se code pas.
BUILD_AGENT_PREFIXES = ("dev-", "qa-", "review-")


def _adr_refusal(root: Path, agent: str) -> int | None:
    """Refus si une décision de `registry/adr-requirements.yml` n'a pas d'ADR accepté."""
    from sdda_lib.errors import SddaError  # noqa: E402
    from sdda_lib.layered_config import read_layered_config  # noqa: E402
    from sdda_scripts import validate_adr  # noqa: E402
    import io

    try:
        config = read_layered_config(root, warn_stream=io.StringIO())
    except SddaError:
        return None
    missing = validate_adr.uncovered(root, config)
    if not missing:
        return None
    what = ", ".join(f"`{req.key}: {value}`" for req, value, _ in missing[:4])
    return deny(HOOK, "ADR_MISSING",
                f"`{agent}` implémenterait {len(missing)} décision(s) refusée(s) par défaut sans ADR accepté : {what}",
                "écrire l'ADR (Status: Accepted, `Covers: Clé=valeur`) sous workspace/pipeline/decisions/, "
                "ou revenir à la valeur par défaut — `python .sdda/sdda.py validate-adr --explain`")


def _config_refusal(root: Path) -> int | None:
    """Refus sur la première classe bloquante de `validate_config`, les autres nommées."""
    from sdda_lib.layered_config import judge_issues, validate_config  # noqa: E402

    blocking = [i for i in validate_config(root) + judge_issues(root) if i.blocking]
    if not blocking:
        return None
    first = blocking[0]
    others = f" · {len(blocking) - 1} autre(s) : " + "; ".join(i.message for i in blocking[1:4]) if len(blocking) > 1 else ""
    return deny(HOOK, first.cls, f"{first.message} [{first.location}]{others}",
                f"{first.fix}. `python .sdda/sdda.py smoke-check` liste tous les constats")


def check(root: Path, data: dict) -> int:
    try:
        matrix = _matrix()
    except (OSError, ValueError) as exc:
        # Registre illisible : c'est le travail d'un validateur, pas d'un hook.
        return allow(f"matrice de compatibilité illisible ({exc.__class__.__name__}) — contrôle reporté au CI")

    activated = _activated(root)
    if not activated:
        return allow("aucune stack activée — STACK.md absent ou vide (bootstrap non joué)")

    levels: dict[str, dict[str, str]] = matrix.get("componentLevels") or {}
    priority: dict[str, int] = matrix.get("levelPriority") or {}
    design_phase = str(matrix.get("frameworkStatus", "")) == "design-phase"

    missing: list[str] = []
    weak: list[str] = []
    for category, name in activated:
        declared = str((levels.get(category) or {}).get(name, "missing"))
        # Le disque a le dernier mot : une fiche absente est `missing`, quel que
        # soit le niveau que le registre lui prête. C'est le disque que l'agent
        # lit, pas le registre.
        if _fiche_path(category, name) is None:
            missing.append(f"{category}/{name}")
        elif priority.get(declared, 3) >= 2:
            weak.append(f"{category}/{name} ({declared})")

    if missing:
        if bypassed("SDDA_ALLOW_UNTESTED_COMBO"):
            return allow(f"{len(missing)} composant(s) sans fiche, assumé(s) par "
                         f"SDDA_ALLOW_UNTESTED_COMBO : {', '.join(missing)}")
        return deny(
            HOOK, "STACK_COMBO_UNLOADABLE",
            f"{len(missing)} composant(s) activé(s) sans fiche sur disque : {', '.join(missing[:4])}",
            "une ligne activée pour un composant sans fiche NE CHARGE RIEN : l'agent travaille "
            "sans mapping de couches, sans idiomes et sans .libs.json, donc il invente. "
            "Désactiver la ligne, ou écrire la fiche (.sdda/stacks/{catégorie}/{nom}.md). "
            "Assumer explicitement : SDDA_ALLOW_UNTESTED_COMBO=1 (audit-loggué)",
        )

    # Les fiches existent toutes : reste à savoir si elles parlent le même
    # langage. Une fiche présente mais écrite pour un autre runtime est PIRE
    # qu'une fiche absente — l'absence se voit, la traduction inventée non.
    mismatched, undeclared = _check_languages(activated)
    if mismatched:
        active = next(name for category, name in activated if category == "language")
        if bypassed("SDDA_ALLOW_LANG_MISMATCH"):
            return allow(f"{len(mismatched)} fiche(s) d'un autre langage, assumée(s) par "
                         f"SDDA_ALLOW_LANG_MISMATCH : {', '.join(mismatched)}")
        return deny(
            HOOK, "STACK_LANGUAGE_MISMATCH",
            f"langage actif `{active}`, mais {len(mismatched)} fiche(s) activée(s) visent un autre "
            f"runtime : {', '.join(mismatched[:4])}",
            "ces fiches existent mais leur code, leurs idiomes et leur .libs.json sont écrits pour "
            "un autre langage : l'agent reçoit une référence qu'il devra traduire, donc inventer. "
            "Choisir une fiche du langage actif, ou changer `## Active Language & Runtime`. "
            "Aucune fiche RAG/vectorstore n'existe pour csharp (cf. ROADMAP Lot 7). "
            "Projet polyglotte assumé (side-car) : SDDA_ALLOW_LANG_MISMATCH=1 (audit-loggué)",
        )

    # Les VALEURS : un agent lit `OnBoundExceeded: foo` ou `CitationMode:
    # requried` comme une consigne, et en invente le sens.
    if (refusal := _config_refusal(root)) is not None:
        return refusal

    if (drift := _retrieval_drift(root, activated)) is not None:
        return deny(
            HOOK, "RETRIEVAL_CONFIG_DRIFT", drift,
            "une clé de `## Active Retrieval Stack` est lue et rien ne l'implémente : le reranking "
            "n'aura pas lieu, ou l'index sera cherché dans la base métier. Aucune des deux pannes "
            "ne se voit à l'exécution. Accorder RerankEnabled avec `## Active Reranker`, et "
            "VectorStoreConnection.Mode avec le store actif (`same-as-database` n'est légitime que "
            "pour pgvector ; tout autre store exige Mode: dedicated + Endpoint)",
        )

    notes: list[str] = []

    refused, said = _unimplemented(root, activated)
    if refused and bypassed("SDDA_ALLOW_UNTESTED_COMBO"):
        said += [f"assumé par SDDA_ALLOW_UNTESTED_COMBO : {r}" for r in refused]
        refused = []
    if refused:
        return deny(
            HOOK, "STACK_VALUE_UNIMPLEMENTED",
            f"{len(refused)} valeur(s) acceptée(s) sans implémentation : {'; '.join(refused[:3])}",
            "ces valeurs passent les parseurs mais rien ne les exécute : l'agent recevrait une déclaration "
            "valide et inventerait le client, la mémoire ou le garde-fou manquant. Revenir à une valeur "
            "implémentée (cf. STACK.md.template, « aucun client runtime »), ou écrire la fiche et le module. "
            "Assumer (le client sera écrit à la main) : SDDA_ALLOW_UNTESTED_COMBO=1 (audit-loggué)")
    notes += said

    # La combinaison, reconnue ou non. `comboSignatureFields` et `combos`
    # étaient écrits dans la matrice et lus par personne : sa description
    # annonçait que ce hook « bloque toute combinaison absente », et il ne
    # l'ouvrait même pas.
    mode = _combo_check_mode(root)
    if mode != "off":
        sig = signature(root)
        known, nearest, gaps = recognise(matrix, sig)
        if known is not None:
            notes.append(f"combo {known.get('id')} reconnue ({known.get('status', '?')})")
        else:
            near = (f" — la plus proche, {nearest.get('id')}, diffère sur : {'; '.join(gaps[:4])}"
                    if nearest is not None else "")
            if mode == "strict" and not bypassed("SDDA_ALLOW_UNTESTED_COMBO"):
                return deny(
                    HOOK, "STACK_COMBO_UNLISTED",
                    f"la combinaison activée n'est aucune de registry/compatibility.matrix.json{near}",
                    "une combinaison listée a été pensée d'un bloc (fiches, langage, surface) ; une "
                    "combinaison inédite est un assemblage que personne n'a relu. Revenir à une combo "
                    "listée, l'ajouter à la matrice (même `untested`), ou l'assumer : "
                    "`StackComboCheck: warn` dans `## Project Config`, ou SDDA_ALLOW_UNTESTED_COMBO=1 "
                    "(audit-loggué)")
            notes.append(f"combinaison hors matrice (StackComboCheck: {mode}"
                         + (", SDDA_ALLOW_UNTESTED_COMBO" if mode == "strict" else "") + f"){near}")

    # Les décisions refusées par défaut : un ADR ACCEPTÉ avant que quiconque
    # les implémente. Seuls les agents qui CONSTRUISENT sont retenus — les
    # po-*/architect-* sont précisément ceux qui proposent et rédigent l'ADR ;
    # les bloquer ici rendrait l'ADR impossible à écrire.
    agent = agent_of(data)
    if agent.startswith(BUILD_AGENT_PREFIXES) and (refusal := _adr_refusal(root, agent)) is not None:
        return refusal

    if weak:
        notes.append(f"{len(weak)} composant(s) non mesuré(s) : {', '.join(weak[:4])}"
                     + (" — attendu (frameworkStatus: design-phase)" if design_phase else
                        " — aucun run mesuré ne couvre cette combinaison"))
    if undeclared:
        # Défaut du framework, pas de l'utilisateur : dit, jamais bloquant.
        notes.append(f"{len(undeclared)} fiche(s) sans en-tête `Languages:` — cohérence de langage "
                     f"non vérifiable pour : {', '.join(undeclared[:4])}")

    return allow(" · ".join(notes)) if notes else ALLOW


def main() -> int:
    return run(HOOK, check, WIRING["applies_to"])


if __name__ == "__main__":
    sys.exit(main())
