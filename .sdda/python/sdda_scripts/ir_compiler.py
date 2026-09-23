#!/usr/bin/env python3
"""Phase 2.9 — compilation de l'Agentic IR (0 token, déterministe).

Projette MISSION + CAPs + TOPOLOGY (graphe Mermaid compris) + `contracts/**` + STACK.md vers
`workspace/.sys/.ir/{n}-system.ir.json`, conforme à `registry/ir.schema.json`.

Trois règles non négociables (AGENTIC-IR.md §1, §5) :

1. **Déterministe et reproductible.** Même entrée => même sortie octet pour
   octet : clés triées, LF, UTF-8, aucune donnée volatile hors `compiledAt`.
   `compiledAt` est conservé d'une compilation à l'autre tant que le contenu de
   l'IR ne change pas (sinon chaque recompilation périmerait les hashes épinglés).
2. **Rien n'est inventé.** Un champ obligatoire absent d'un contrat produit
   `[IR_COMPILE_FAILED]` avec `fichier:section`, jamais un défaut fabriqué.
   Les seules dérivations admises sont des CONVENTIONS documentées ici :
   - id de suite d'AC : `{n}-{m}-{metric}` ; niveau L4 si la CAP est portée par
     un agent, L3 par un retriever seul, L2 par un outil seul ;
   - `judgeCalibrationRef` d'un `llm-judge` sans `calibration:` :
     `workspace/proof/calibration/{metric}.json` (annoncé par validate_cap.py) ;
   - suite d'injection d'un agent à entrées non maîtrisées :
     `{agentId}-injection`, L8, grader `trajectory`, seuil 1.0 (toute attaque
     réussie est bloquante), `runs` = `EvalRunsCritical` ;
   - `baselineRef` : `workspace/proof/baselines/{n}-system.json` ;
   - `holdout` : l'unique fichier `workspace/proof/datasets/holdout/mission-{n}-*.jsonl` ;
   - `promptHash` : hash du fichier prompt s'il existe, sinon le hash épinglé
     dans le contrat (`- Hash :`) ; ni l'un ni l'autre => erreur ;
   - `onBoundExceeded` d'un agent : le comportement majoritaire de sa table de
     bornes (les cinq bornes du contrat portent chacune le leur) ;
   - une arête Mermaid pointillée (`-.->`) est une arête « gratuite »
     (`countsAsHop: false`) ; une arête pleine compte comme hop.
3. **Neutre framework.** Le compilateur ne connaît ni LangGraph ni Semantic
   Kernel : il décrit *quoi*. La fuite d'un identifiant de framework est
   détectée par validate_ir.py (`[FRAMEWORK_LEAK_IN_CONTRACT]`).

Usage :
    python .sdda/sdda.py ir-compiler --mission 1 [--out workspace/.sys/.ir/1-system.ir.json]
    python .sdda/sdda.py ir-compiler                # toutes les missions du workspace
    python .sdda/sdda.py ir-compiler --mission 1 --compiled-at 2026-09-20T10:00:00Z

Exit : 0 = IR écrit · 1 = au moins un [IR_COMPILE_FAILED] (rien n'est écrit).
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, mermaid, paths, yaml_mini  # noqa: E402
from sdda_lib.errors import Report, emit  # noqa: E402
from sdda_lib.layered_config import LayeredConfig, app_name, read_stack_section_kv  # noqa: E402
from sdda_scripts._common import add_common_args, load_config, resolve_root  # noqa: E402
from sdda_scripts.validate_cap import CapSpec, load_caps_for_mission  # noqa: E402
from sdda_scripts.validate_mission import MissionSpec, load_mission  # noqa: E402
from sdda_scripts.validate_topology import TopologySpec, load_mermaid, parse_topology  # noqa: E402

IR_VERSION = "1"
TIERS = ("fast", "balanced", "deep")
SIDE_EFFECTS = ("read-only", "write-scoped", "write-destructive", "external-side-effect")
BOUND_BEHAVIORS = ("fail-explicit", "degrade", "escalate-human")
GRADERS = ("exact", "regex", "schema", "numeric-tolerance", "semantic-similarity", "llm-judge", "trajectory", "cost", "latency")
RETRIEVAL_PATTERNS = ("classic", "hybrid", "contextual", "hyde", "sequential-multihop", "agentic", "self-rag", "corrective-rag", "graph-rag", "raptor")
CHUNK_STRATEGIES = ("fixed", "recursive-structural", "semantic", "document-aware", "parent-child")

#: Borne du contrat -> clé de l'IR.
BOUND_KEYS = {
    "max_iterations": "maxIterations",
    "max_tool_calls": "maxToolCalls",
    "max_delegation_depth": "maxDelegationDepth",
    "timeout_s": "timeoutSec",
    "budget_usd": "budgetUsd",
}
#: Situation de dégradation (contrat, FR) -> clé de l'IR (exemples d'AGENTIC-IR.md).
DEGRADATION_KEYS = {
    "outil indisponible": "toolUnavailable",
    "retrieval vide": "retrievalEmpty",
    "confiance basse": "lowConfidence",
    "borne atteinte": "boundExceeded",
}
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_H1_ID_RE = re.compile(r"^#\s+[A-Z ]+CONTRACT:\s*(\S+)\s*$", re.MULTILINE)


class CompileError(Exception):
    """Levée quand la compilation ne peut pas produire un IR honnête."""

    def __init__(self, report: Report):
        super().__init__(f"{len(report.errors)} erreur(s) de compilation")
        self.report = report


@dataclass
class CompileContext:
    root: Path
    number: int
    report: Report
    config: LayeredConfig | None = None
    agent_ids: set[str] = field(default_factory=set)
    tool_ids: set[str] = field(default_factory=set)
    tool_names: dict[str, str] = field(default_factory=dict)   # nom appelé par le modèle -> id de contrat
    retriever_ids: set[str] = field(default_factory=set)

    def fail(self, what: str, fix: str, location: str) -> None:
        self.report.error("IR_COMPILE_FAILED", what, fix, location, title="compilation IR impossible")

    def qualified(self, slug: str) -> str:
        """`billing-specialist` -> `1-billing-specialist` (id qualifié par la mission)."""
        s = markdown_io.strip_code(slug)
        return s if re.match(r"^\d+-", s) else f"{self.number}-{s}"

    def canonical_tool(self, slug: str) -> str:
        """Le contrat d'outil que désigne `slug`, quelle que soit la forme employée.

        Un outil a DEUX identifiants : le NOM que le modèle appelle
        (`refunds_search`, §1 du contrat, celui que roster, topologie et CAPs
        emploient) et l'ID de son contrat (`1-refunds-search`, kebab, celui du
        fichier). `gen_source_tools` dérive le second du premier en remplaçant
        `_` par `-`. Ne résoudre que par id rendait `## Allocated To` et le §4 des
        contrats d'agents incompilables dès qu'ils parlaient la langue du
        modèle — 21 outils sur 21 au premier run réel. Ordre : id exact, puis
        nom déclaré, puis id kebab dérivé du nom. Non résolu -> l'id qualifié
        tel quel, et l'appelant émet `sans contrat` comme avant.
        """
        s = markdown_io.strip_code(slug)
        q = self.qualified(s)
        if q in self.tool_ids:
            return q
        by_name = self.tool_names.get(s)
        if by_name:
            return by_name
        alt = self.qualified(s.replace("_", "-"))
        return alt if alt in self.tool_ids else q


# --------------------------------------------------------------------------
# Utilitaires de lecture
# --------------------------------------------------------------------------
def _norm_key(k: str) -> str:
    """Clé insensible à la casse, aux espaces, aux accents et aux backticks."""
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFD", markdown_io.strip_code(k)) if unicodedata.category(c) != "Mn")
    return re.sub(r"[\s_\-*']+", "", s).lower()


def _two_col_rows(body: str) -> list[tuple[str, str]]:
    """Toutes les lignes `| a | b |` d'un corps, séparateurs exclus, en-tête inclus."""
    rows: list[tuple[str, str]] = []
    for line in body.split("\n"):
        s = line.strip()
        if not s.startswith("|") or re.match(r"^\|?\s*:?-{3,}", s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 2:
            rows.append((cells[0], cells[1]))
    return rows


def _collect_kv(text: str) -> dict[str, str]:
    """Toutes les paires clé/valeur d'un document (en-tête, puces, tables 2 colonnes), clés normalisées."""
    out: dict[str, str] = {}
    for k, v in markdown_io.parse_header_fields(text).items():
        out.setdefault(_norm_key(k), v.strip())
    for k, v in markdown_io.parse_kv_list(text).items():
        out.setdefault(_norm_key(k), v.strip())
    for a, b in _two_col_rows(text):
        ka = _norm_key(a)
        if ka and not markdown_io.is_placeholder(markdown_io.strip_code(b)):
            out.setdefault(ka, b.strip())
    return out


def _lookup(kv: dict[str, str], *aliases: str) -> str | None:
    for a in aliases:
        v = kv.get(_norm_key(a))
        if v is not None and not markdown_io.is_placeholder(markdown_io.strip_code(v)):
            return markdown_io.strip_code(v)
    return None


def _to_int(value: Any) -> int | None:
    """`" 12 "`, `12`, `"12 jours"` -> 12 ; `None` ou sans chiffre -> None.

    Les sections de STACK.md arrivent déjà typées par le lecteur YAML : un
    `LongTermRetentionDays: 0` est un `int`, pas une chaîne, et `.replace`
    plantait le compilateur entier.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    m = re.search(r"-?\d+", str(value).replace(" ", ""))
    return int(m.group(0)) if m else None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    m = _NUM_RE.search(value.replace(",", "."))
    return float(m.group(0)) if m else None


def _to_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    v = markdown_io.strip_code(value).lower()
    if v in ("oui", "yes", "true", "on"):
        return True
    if v in ("non", "no", "false", "off"):
        return False
    return None


def _json_value(raw: str) -> Any:
    return json.loads(markdown_io.strip_code(raw))


def _contract_id(text: str, path: Path, suffix: str) -> str:
    m = _H1_ID_RE.search(text)
    if m:
        return m.group(1).strip()
    return path.name[: -len(suffix)] if path.name.endswith(suffix) else path.stem


def _paragraph(body: str | None) -> str:
    lines = [l.strip() for l in (body or "").split("\n") if l.strip() and not l.strip().startswith((">", "|", "#"))]
    return " ".join(lines)


def _behavior(text: str) -> str | None:
    t = text.lower()
    for b in BOUND_BEHAVIORS:
        if b in t:
            return b
    return None


def _slug(text: str) -> str:
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    words = re.findall(r"[A-Za-z0-9]+", s)
    if not words:
        return "other"
    return words[0].lower() + "".join(w.capitalize() for w in words[1:])


# --------------------------------------------------------------------------
# Contrats d'agents
# --------------------------------------------------------------------------
def compile_agent(ctx: CompileContext, path: Path) -> dict[str, Any] | None:
    text = markdown_io.read_text(path)
    loc = paths.rel(ctx.root, path)
    aid = _contract_id(text, path, ".agent.md")
    header = markdown_io.parse_header_fields(text)

    def sec(title: str) -> str | None:
        return markdown_io.section_body(text, title)

    agent: dict[str, Any] = {"id": aid}
    role = _paragraph(sec("Rôle"))
    if role:
        agent["role"] = role

    # §2 Capabilities servies ------------------------------------------------
    caps = [markdown_io.strip_code(r.get("CAP", "")) for r in markdown_io.parse_table(sec("Capabilities servies") or "")]
    caps = sorted({c for c in caps if c and not markdown_io.is_placeholder(c)})
    if not caps:
        ctx.fail(f"agent `{aid}` : `## 2. Capabilities servies` vide — un agent qui ne sert aucune CAP est [AGENT_SERVES_NO_CAP]",
                 "lister au moins une CAP dans la table §2", f"{loc}:2")
    agent["servesCaps"] = caps

    # §3 Prompt --------------------------------------------------------------
    prompt_kv = markdown_io.parse_kv_list(sec("Prompt") or "")
    prompt_ref = markdown_io.strip_code(prompt_kv.get("Fichier", ""))
    # Le prompt vit DANS l'application : `workspace/src/{App}/prompts/{slug}.system.md`.
    expected_dir = paths.rel(ctx.root, paths.prompts_dir(ctx.root, app_name(ctx.root))) + "/"
    if markdown_io.is_placeholder(prompt_ref) or not prompt_ref.startswith(expected_dir) or not prompt_ref.endswith(".system.md"):
        ctx.fail(f"agent `{aid}` : `## 3. Prompt` ne nomme pas de fichier `{expected_dir}….system.md`",
                 f"écrire `- Fichier : `{expected_dir}{{slug}}.system.md`` — le prompt part avec l'application", f"{loc}:3")
    else:
        agent["promptRef"] = prompt_ref
        prompt_path = paths.resolve_rel(ctx.root, prompt_ref)
        pinned = markdown_io.strip_code(prompt_kv.get("Hash", ""))
        if prompt_path.is_file():
            agent["promptHash"] = hashing.sha256_file(prompt_path)
        elif hashing.is_hash_ref(pinned):
            agent["promptHash"] = pinned
        # Sinon : pas de `promptHash`, et ce n'est pas une faute — c'est la PHASE 2.
        #
        # L'IR se compile après les architectes ; les prompts naissent en PHASE 4,
        # chez `dev-prompt`, qui lit l'IR pour savoir quoi écrire. Exiger ici le
        # hash d'un fichier qui n'existe pas encore fermait la boucle sur
        # elle-même : aucune MISSION neuve ne compilait, et le contrat au gabarit
        # (`Hash : sha256:…`) était le premier à tomber. La fixture de référence
        # livre ses prompts, donc aucun test ne voyait le cas d'un workspace vide.
        #
        # Ce qui remplace l'exigence, et la rend tenable :
        #   - `source_hashes` suit les prompts : l'IR redevient périmé dès qu'ils
        #     apparaissent, et la recompilation épingle le hash ;
        #   - `preflight_agent_bounds` refuse de lancer `dev-agent` sur un IR dont
        #     un agent n'a pas de `promptHash` — c'est là, et seulement là, qu'un
        #     prompt non épinglé coûte quelque chose : implémenter un prompt qui
        #     n'existe pas.
        # Un hash ne s'invente toujours pas (P10) ; il est simplement exigé au
        # moment où il peut exister.

    # Tier -------------------------------------------------------------------
    tier = header.get("Model Tier", "").strip().lower()
    if tier not in TIERS:
        ctx.fail(f"agent `{aid}` : `Model Tier: {tier or '<absent>'}` hors de {list(TIERS)}", "déclarer un TIER, jamais un nom de modèle (P11)", f"{loc}:header")
    else:
        agent["modelTier"] = tier

    # §4 Outils, §5 Retrievers -------------------------------------------------
    tools = [ctx.qualified(r.get("Outil", "")) for r in markdown_io.parse_table(sec("Outils") or "") if not markdown_io.is_placeholder(markdown_io.strip_code(r.get("Outil", "")))]
    if tools:
        agent["tools"] = sorted(set(tools))
    # Les skills ne sont PAS des outils : elles disent ce que l'agent sait faire,
    # pas ce qu'il a le droit d'appeler. Elles n'ont donc ni schéma ni gate propre
    # — mais elles doivent survivre à la compilation, sinon `dev-prompt` ne
    # les voit jamais et la déclaration de l'architecte s'évapore en silence.
    # Le roster les nomme comme le modèle les lit (`classify_intent`, snake_case) ;
    # l'IR les porte en `slugId` kebab (`1-classify-intent`) — même passage
    # `_` -> `-` que `gen_source_tools` pour les outils. `lint_prompts` compare
    # les deux formes après normalisation ; le schéma, lui, n'en accepte qu'une.
    skills = [ctx.qualified(markdown_io.strip_code(r.get("Skill", "")).replace("_", "-"))
              for r in markdown_io.parse_table(sec("Skills") or "")
              if not markdown_io.is_placeholder(markdown_io.strip_code(r.get("Skill", "")))]
    if skills:
        agent["skills"] = sorted(set(skills))

    # Les rules sont les jumelles des skills : une contrainte de comportement
    # nommée, sans schéma ni effet de bord. Elles suivent le MÊME chemin —
    # déclarées par l'architecte, compilées ici, implémentées par `dev-prompt`,
    # vérifiées par symétrie dans `lint_prompts.py`. Les traiter autrement
    # (répertoire dédié, fichier par règle) produirait des fichiers que rien ne
    # charge : une règle qui n'entre pas dans le prompt n'existe pas.
    rules = [ctx.qualified(markdown_io.strip_code(r.get("Règle", "") or r.get("Regle", "") or r.get("Rule", "")).replace("_", "-"))
             for r in markdown_io.parse_table(sec("Règles") or "")
             if not markdown_io.is_placeholder(markdown_io.strip_code(
                 r.get("Règle", "") or r.get("Regle", "") or r.get("Rule", "")))]
    if rules:
        agent["rules"] = sorted(set(rules))
    retrievers = [ctx.qualified(r.get("Retriever", "")) for r in markdown_io.parse_table(sec("Retrievers") or "") if not markdown_io.is_placeholder(markdown_io.strip_code(r.get("Retriever", "")))]
    if retrievers:
        agent["retrievers"] = sorted(set(retrievers))

    # §6 Schémas -------------------------------------------------------------
    schemas = markdown_io.parse_kv_list(sec("Schémas") or "")
    for key, ir_key in (("Entrée", "inputSchema"), ("Sortie", "outputSchema")):
        raw = schemas.get(key)
        if raw is None or markdown_io.is_placeholder(markdown_io.strip_code(raw)):
            continue
        try:
            value = _json_value(raw)
            if not isinstance(value, dict):
                raise ValueError("pas un objet")
            agent[ir_key] = value
        except ValueError as exc:
            ctx.fail(f"agent `{aid}` : schéma `{key}` illisible ({exc})", "écrire un JSON Schema inline ou un `{\"$ref\": …}`", f"{loc}:6")

    # §7 Bornes (P12) ----------------------------------------------------------
    bounds: dict[str, Any] = {}
    behaviors: list[str] = []
    for row in markdown_io.parse_table(sec("Bornes") or ""):
        name = markdown_io.strip_code(row.get("Borne", "")).lower()
        if name not in BOUND_KEYS:
            continue
        raw = markdown_io.strip_code(row.get("Valeur", ""))
        num = _to_float(raw) if name == "budget_usd" else _to_int(raw)
        if num is None:
            ctx.fail(f"agent `{aid}` : borne `{name}` sans valeur numérique (`{raw}`)", "chaque borne porte un nombre (P12)", f"{loc}:7")
            continue
        bounds[BOUND_KEYS[name]] = num
        b = _behavior(row.get("Comportement à l'atteinte", "") or row.get("Comportement", ""))
        if b:
            behaviors.append(b)
    missing = [k for k, v in BOUND_KEYS.items() if v not in bounds]
    if missing:
        ctx.fail(f"agent `{aid}` : borne(s) manquante(s) : {', '.join(missing)}",
                 "les cinq bornes sont obligatoires, toutes (P12, invariant no-unbounded-loop)", f"{loc}:7")
    agent["bounds"] = bounds
    if behaviors:
        # Majorité ; à égalité, la première déclarée (ordre des lignes, stable).
        counts = Counter(behaviors)
        best = max(counts.values())
        agent["onBoundExceeded"] = next(b for b in behaviors if counts[b] == best)
    else:
        ctx.fail(f"agent `{aid}` : aucun comportement à l'atteinte d'une borne", "écrire fail-explicit | degrade | escalate-human dans la colonne « Comportement »", f"{loc}:7")

    # §8 Posture de confiance (P8) ----------------------------------------------
    trust_kv = markdown_io.parse_kv_list(sec("Posture de confiance") or "")
    untrusted_raw = trust_kv.get("Entrées non maîtrisées")
    if untrusted_raw is None:
        ctx.fail(f"agent `{aid}` : `## 8. Posture de confiance` ne déclare pas `Entrées non maîtrisées`",
                 "lister les entrées non maîtrisées, ou écrire `aucune`", f"{loc}:8")
        untrusted: list[str] = []
    else:
        untrusted = sorted(set(markdown_io.split_code_list(untrusted_raw)))
    posture: dict[str, Any] = {"untrustedInputs": untrusted}
    suite_ref = markdown_io.first_code_span(trust_kv.get("Suite d'injection", ""))
    if suite_ref and not markdown_io.is_placeholder(suite_ref):
        posture["injectionSuiteRef"] = suite_ref
    elif untrusted:
        ctx.fail(f"agent `{aid}` : entrées non maîtrisées {untrusted} sans `Suite d'injection`",
                 "déclarer `- **Suite d'injection** : workspace/proof/datasets/adversarial/{slug}.jsonl` (invariant injection-suite-mandatory)", f"{loc}:8")
    agent["trustPosture"] = posture

    # §9 Politique de refus ---------------------------------------------------------
    refusals = [r for r in markdown_io.parse_bullets(sec("Politique de refus") or "") if not markdown_io.is_placeholder(r)]
    if refusals:
        agent["refusalPolicy"] = refusals

    # §10 Mémoire ---------------------------------------------------------------------
    scopes: dict[str, list[str]] = {"read": [], "write": []}
    for row in markdown_io.parse_table(sec("Mémoire") or ""):
        scope = markdown_io.strip_code(row.get("Scope", ""))
        if not scope or markdown_io.is_placeholder(scope):
            continue
        if _to_bool(row.get("Lecture", "")):
            scopes["read"].append(scope)
        if _to_bool(row.get("Écriture", "")):
            scopes["write"].append(scope)
    if scopes["read"] or scopes["write"]:
        agent["memoryScopes"] = {"read": sorted(scopes["read"]), "write": sorted(scopes["write"])}

    # §11 Handoffs -----------------------------------------------------------------------
    handoffs = []
    for row in markdown_io.parse_table(sec("Handoffs") or ""):
        to = markdown_io.strip_code(row.get("Vers", ""))
        if not to or markdown_io.is_placeholder(to):
            continue
        h: dict[str, Any] = {"to": to, "condition": markdown_io.strip_code(row.get("Condition", ""))}
        for col, key in (("État transmis", "stateContract"), ("Retour attendu", "expectedReturn")):
            raw = row.get(col, "")
            try:
                val = _json_value(raw)
                if isinstance(val, dict):
                    h[key] = val
                elif key == "stateContract":
                    raise ValueError("pas un objet")
            except ValueError:
                if key == "stateContract":
                    ctx.fail(f"agent `{aid}` : handoff vers `{to}` — `État transmis` n'est pas un objet JSON (`{markdown_io.strip_code(raw)}`)",
                             "« le contexte suit » n'est pas un contrat : écrire l'état transmis en JSON", f"{loc}:11")
        if "stateContract" in h:
            handoffs.append(h)
    if handoffs:
        agent["handoffs"] = handoffs

    # §12 Dégradation ------------------------------------------------------------------------
    degradation: dict[str, str] = {}
    for row in markdown_io.parse_table(sec("Comportement de dégradation") or ""):
        situation = row.get("Situation", "").strip()
        behavior = row.get("Comportement", "").strip()
        if not situation or markdown_io.is_placeholder(behavior):
            continue
        degradation[DEGRADATION_KEYS.get(situation.lower(), _slug(situation))] = behavior
    if degradation:
        agent["degradation"] = dict(sorted(degradation.items()))
    return agent


# --------------------------------------------------------------------------
# Contrats d'outils
# --------------------------------------------------------------------------
def compile_tool(ctx: CompileContext, path: Path) -> dict[str, Any]:
    text = markdown_io.read_text(path)
    loc = paths.rel(ctx.root, path)
    tid = _contract_id(text, path, ".tool.md")
    header = markdown_io.parse_header_fields(text)

    def sec(title: str) -> str | None:
        return markdown_io.section_body(text, title)

    tool: dict[str, Any] = {"id": tid}
    naming = sec("Nom et description") or ""
    name = markdown_io.strip_code(markdown_io.parse_kv_list(naming).get("name", ""))
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        ctx.fail(f"outil `{tid}` : `name` absent ou invalide (`{name}`)", "écrire `- **name** : `snake_case`` — c'est ce que le modèle verra", f"{loc}:1")
    else:
        tool["name"] = name
    fences = markdown_io.fenced_blocks(naming)
    description = " ".join(l.strip() for l in (fences[0] if fences else "").split("\n") if l.strip())
    if len(description) < 20 or markdown_io.is_placeholder(description):
        ctx.fail(f"outil `{tid}` : description absente ou trop courte ({len(description)} car.)",
                 "la description est du prompt engineering : dire QUAND l'utiliser, quand NE PAS l'utiliser, ce qu'il retourne", f"{loc}:1")
    else:
        tool["description"] = description

    sec_class = header.get("Side Effect Class", "").strip().lower()
    if sec_class not in SIDE_EFFECTS:
        ctx.fail(f"outil `{tid}` : `Side Effect Class: {sec_class or '<absent>'}` hors de {list(SIDE_EFFECTS)}", "déclarer la classe d'effet de bord (P8)", f"{loc}:header")
    else:
        tool["sideEffectClass"] = sec_class
    trust = header.get("Trust", "").strip().lower()
    if trust not in ("trusted", "untrusted"):
        ctx.fail(f"outil `{tid}` : `Trust: {trust or '<absent>'}` attendu trusted|untrusted", "la sortie de l'outil est-elle du texte hostile ?", f"{loc}:header")
    else:
        tool["trust"] = trust

    # §2 Schémas ---------------------------------------------------------------
    blocks = markdown_io.fenced_blocks(sec("Schémas") or "", "json")
    for i, key in enumerate(("inputSchema", "outputSchema")):
        if i >= len(blocks):
            ctx.fail(f"outil `{tid}` : bloc ```json n°{i + 1} ({key}) absent de `## 2. Schémas`", "écrire les deux schémas (entrée puis sortie)", f"{loc}:2")
            continue
        try:
            val = json.loads(blocks[i])
            if not isinstance(val, dict):
                raise ValueError("pas un objet")
            tool[key] = val
        except ValueError as exc:
            ctx.fail(f"outil `{tid}` : {key} illisible ({exc})", "corriger le JSON Schema", f"{loc}:2")

    # §3 Stratégie de sûreté ----------------------------------------------------
    if sec_class in SIDE_EFFECTS and sec_class != "read-only":
        strategy: dict[str, Any] = {}
        for aspect, value in _two_col_rows(sec("Stratégie de sûreté") or ""):
            a = _norm_key(aspect)
            v = markdown_io.strip_code(value)
            if markdown_io.is_placeholder(v):
                continue
            if a == "idempotence":
                strategy["idempotency"] = v
            elif a.startswith("dryrun"):
                b = _to_bool(v)
                if b is not None:
                    strategy["dryRunSupported"] = b
            elif a == "confirmation":
                strategy["confirmation"] = v
            elif a.startswith("plafond"):
                n = _to_float(v)
                if n is not None:
                    strategy["cap"] = {"perRun": n}
            elif a == "allowlist":
                strategy["allowlist"] = [p.strip() for p in re.split(r"[;,]", v) if p.strip()]
        if strategy:
            tool["safetyStrategy"] = strategy
        # Absente : on ne fabrique rien — validate_ir.py émettra [SIDE_EFFECT_UNDECLARED].

    # §4 Erreurs déclarées ------------------------------------------------------------
    errors = []
    for row in markdown_io.parse_table(sec("Erreurs déclarées") or ""):
        code = markdown_io.strip_code(row.get("Code", ""))
        behavior = row.get("Comportement attendu de l'agent", "").strip()
        if not code or markdown_io.is_placeholder(code) or markdown_io.is_placeholder(behavior):
            continue
        entry = {"code": code, "agentBehavior": behavior}
        meaning = row.get("Signification", "").strip()
        if meaning and not markdown_io.is_placeholder(meaning):
            entry["meaning"] = meaning
        errors.append(entry)
    if errors:
        tool["errors"] = errors

    # §5 Bornes techniques ---------------------------------------------------------------
    tech = {_norm_key(a): markdown_io.strip_code(b) for a, b in _two_col_rows(sec("Bornes techniques") or "")}
    timeout = _to_int(tech.get("timeouts"))
    if timeout is None or timeout < 1:
        ctx.fail(f"outil `{tid}` : `timeout_s` absent ou non positif", "déclarer `timeout_s` dans `## 5. Bornes techniques`", f"{loc}:5")
    else:
        tool["timeoutSec"] = timeout
    rpm = _to_int(tech.get("ratelimitrpm"))
    if rpm:
        tool["rateLimitRpm"] = rpm
    retry = tech.get("retrypolicy")
    if not retry or markdown_io.is_placeholder(retry):
        ctx.fail(f"outil `{tid}` : `retry_policy` absente", "écrire `none` (obligatoire si non idempotent) ou `exponential:{n}`", f"{loc}:5")
    else:
        tool["retryPolicy"] = retry
    mrb = _to_int(tech.get("maxresponsebytes"))
    if mrb:
        tool["maxResponseBytes"] = mrb

    # §6 Authentification -------------------------------------------------------------------
    auth = markdown_io.strip_code(markdown_io.parse_kv_list(sec("Authentification") or "").get("Variable d'environnement", ""))
    if auth and not markdown_io.is_placeholder(auth):
        tool["authEnv"] = auth

    # §8 Tests de contrat ---------------------------------------------------------------------
    m = re.search(r"Fichier\s*:\s*`([^`]+)`", sec("Tests de contrat (L2)") or sec("Tests de contrat") or "")
    if not m or markdown_io.is_placeholder(m.group(1)):
        ctx.fail(f"outil `{tid}` : `## 8. Tests de contrat (L2)` ne nomme pas de fichier", "écrire `Fichier : `workspace/proof/suites/tool-{id}.yaml``", f"{loc}:8")
    else:
        tool["contractTestsRef"] = m.group(1).strip()
    return tool


# --------------------------------------------------------------------------
# Contrats de retrieval
# --------------------------------------------------------------------------
def _active_by_category(root: Path, heading: str) -> dict[str, list[str]]:
    """`## Section` -> {catégorie: [fiches]} depuis les lignes ` - .sdda/stacks/{cat}/{nom}.md`.

    `active_stacks()` aplatit la catégorie, or c'est elle qui dit si `pgvector`
    est le store ou le modèle d'embedding. Une section de retrieval en porte
    deux : sans la catégorie, on ne sait pas quoi confronter à quoi.
    """
    out: dict[str, list[str]] = {}
    stack = paths.stack_md_path(root)
    if not stack.is_file():
        return out
    body = markdown_io.section_body(markdown_io.read_text(stack), heading)
    for line in (body or "").split("\n"):
        s = line.strip()
        if not s.startswith("- .sdda/stacks/"):
            continue
        parts = s.removeprefix("- .sdda/stacks/").split("/")
        if len(parts) == 2:
            out.setdefault(parts[0], []).append(parts[1].removesuffix(".md"))
    return out


def _stack_family(name: str) -> str:
    """`voyage-3-large` -> `voyage` · `bge-local` -> `bge` · `pgvector` -> `pgvector`."""
    return markdown_io.strip_code(str(name)).strip().lower().split("-", 1)[0]


def _reconcile_binding(ctx: CompileContext, rid: str, binding: dict[str, Any], loc: str) -> None:
    """Le `binding` du contrat dit-il la même chose que les stacks ACTIVES ?

    `Store:` dans le contrat de retrieval et `## Active Retrieval Stack` dans
    STACK.md décrivaient la même décision à deux endroits, et rien ne les
    confrontait : on pouvait générer du `pgvector` sur une stack qui avait
    basculé ailleurs, et ne l'apprendre qu'à l'exécution. La scission
    intent/binding déplace la décision dans une seule branche ; cette
    réconciliation la rend vérifiable.

    Silencieux si STACK.md ne déclare rien : un projet en cours de rédaction
    n'est pas un projet incohérent.
    """
    active = _active_by_category(ctx.root, "Active Retrieval Stack")
    for category, key in (("vectorstore", "store"), ("embedding", "embeddingModel")):
        declared = active.get(category) or []
        value = str(binding.get(key) or "")
        if not declared or not value:
            continue
        # Comparaison sur la FAMILLE, pas sur l'identifiant exact : une fiche
        # couvre une famille (`voyage`, `bge-local`) quand le contrat nomme un
        # modèle de cette famille (`voyage-3-large`, `bge-m3`). Exiger l'égalité
        # refuserait toute déclaration correcte ; ne rien exiger laisserait
        # passer un contrat `voyage` sur une stack `openai`. C'est le
        # changement de FOURNISSEUR que ce contrôle attrape, pas un numéro de
        # version — celui-là est épinglé par `indexHash` (P10).
        if _stack_family(value) in {_stack_family(d) for d in declared}:
            continue
        ctx.report.error(
            "RETRIEVAL_BINDING_MISMATCH",
            f"retriever `{rid}` : le contrat déclare `{key}: {value}`, "
            f"`STACK.md ## Active Retrieval Stack` active `{'`, `'.join(declared)}`",
            "aligner le contrat sur la stack active, ou changer la stack — mais "
            "une décision d'infrastructure ne peut pas valoir deux valeurs à la fois",
            f"{loc}:header", title="binding du retriever hors de la stack active")

    # Le reranker se confronte sur la PRÉSENCE, pas sur le nom : la fiche
    # s'appelle `cohere-rerank`, le contrat nomme un modèle (`rerank-3`). Ce
    # qui se compare est « y a-t-il un reranker ou non » — et c'est justement
    # la question que `RerankEnabled` posait sans que rien n'y réponde.
    reranker = [n for n in (_active_by_category(ctx.root, "Active Reranker").get("rerank") or []) if n != "none"]
    if reranker and binding.get("rerank") is None:
        ctx.report.error(
            "RETRIEVAL_BINDING_MISMATCH",
            f"retriever `{rid}` : `STACK.md ## Active Reranker` active `{reranker[0]}`, le contrat n'en déclare aucun",
            "déclarer `rerank:` dans le contrat, ou activer `rerank/none.md` — un reranker "
            "actif que le contrat ignore n'est pas câblé et son coût n'est pas estimé",
            f"{loc}:header", title="reranker actif absent du contrat")


def compile_retriever(ctx: CompileContext, path: Path) -> dict[str, Any]:
    text = markdown_io.read_text(path)
    loc = paths.rel(ctx.root, path)
    rid = _contract_id(text, path, ".retrieval.md")
    kv = _collect_kv(text)
    r: dict[str, Any] = {"id": rid}

    pattern = (_lookup(kv, "Pattern", "RAG Pattern") or "").lower()
    if pattern not in RETRIEVAL_PATTERNS:
        ctx.fail(f"retriever `{rid}` : `Pattern: {pattern or '<absent>'}` hors de {list(RETRIEVAL_PATTERNS)}", "déclarer le pattern RAG", f"{loc}:header")
    else:
        r["pattern"] = pattern
    # `binding` — la branche RÉALISATION. Elle est la seule de l'IR où un
    # composant d'infrastructure peut être nommé, et elle est RÉCONCILIÉE avec
    # les stacks actives : `Store:` et `STACK.md ## Active Retrieval Stack`
    # décrivaient la même décision à deux endroits, et rien ne les confrontait.
    # Deux vérités sur le même fait, c'est zéro vérité vérifiable.
    binding: dict[str, Any] = {}
    for key, aliases in (("store", ("Store",)), ("embeddingModel", ("Embedding Model", "Modèle d'embedding"))):
        v = _lookup(kv, *aliases)
        if not v:
            ctx.fail(f"retriever `{rid}` : `{aliases[0]}` absent", f"déclarer `{aliases[0]}:` dans l'en-tête", f"{loc}:header")
        else:
            binding[key] = v

    chunk: dict[str, Any] = {}
    strat = (_lookup(kv, "strategy", "ChunkStrategy") or "").lower()
    if strat not in CHUNK_STRATEGIES:
        ctx.fail(f"retriever `{rid}` : stratégie de chunking `{strat or '<absent>'}` hors de {list(CHUNK_STRATEGIES)}", "déclarer `strategy:` dans `## Chunking`", f"{loc}:Chunking")
    else:
        chunk["strategy"] = strat
    for key, aliases in (("size", ("size", "ChunkSize")), ("overlap", ("overlap", "ChunkOverlap"))):
        n = _to_int(_lookup(kv, *aliases))
        if n is None:
            ctx.fail(f"retriever `{rid}` : `{aliases[0]}` de chunking absent", "choisir 512/50 par défaut n'est pas une décision : mesurer deux configurations", f"{loc}:Chunking")
        else:
            chunk[key] = n
    ref = _lookup(kv, "comparativeMeasureRef", "Fichier de mesure")
    if ref:
        chunk["comparativeMeasureRef"] = ref
    binding["chunk"] = chunk

    topk = _to_int(_lookup(kv, "topK", "RetrievalTopK"))
    if topk is None:
        ctx.fail(f"retriever `{rid}` : `topK` absent", "déclarer `topK:` dans `## Retrieval`", f"{loc}:Retrieval")
    else:
        r["topK"] = topk
    hw = _lookup(kv, "hybridWeights", "HybridWeights")
    if hw:
        parsed = yaml_mini.parse_scalar(hw)
        if isinstance(parsed, dict):
            binding["hybridWeights"] = {k: float(v) for k, v in parsed.items() if k in ("vector", "lexical") and isinstance(v, (int, float))}
    rerank = _lookup(kv, "rerank", "RerankModel")
    binding["rerank"] = None if (not rerank or rerank.lower() in ("none", "false", "non", "aucun")) else {"model": rerank}
    _reconcile_binding(ctx, rid, binding, loc)
    r["binding"] = binding
    mrc = _to_int(_lookup(kv, "maxRetrievalCalls", "max_retrieval_calls"))
    if mrc:
        r["maxRetrievalCalls"] = mrc
    elif pattern == "agentic":
        ctx.fail(f"retriever `{rid}` : pattern `agentic` sans `maxRetrievalCalls`", "le retrieval agentique a un coût variable par nature : le plafonner (P12)", f"{loc}:Retrieval")
    cit = (_lookup(kv, "Citation Mode", "CitationMode", "citationMode") or "").lower()
    if cit not in ("required", "optional", "none"):
        ctx.fail(f"retriever `{rid}` : `Citation Mode: {cit or '<absent>'}` attendu required|optional|none", "déclarer le mode de citation", f"{loc}:header")
    else:
        r["citationMode"] = cit
    idf = _lookup(kv, "identityFilter", "Métadonnée de filtre")
    if idf:
        r["identityFilter"] = None if idf.lower() in ("none", "aucun", "aucune") else idf
    ih = _lookup(kv, "indexHash", "Index Hash")
    if ih and hashing.is_hash_ref(ih):
        r["indexHash"] = ih

    thresholds: dict[str, float] = {}
    for key, aliases in (("recallAtK", ("recallAtK", "recall@k")), ("ndcg", ("ndcg", "nDCG@k", "ndcg@k")), ("contextPrecision", ("contextPrecision", "context_precision")),
                         ("groundedness", ("groundedness",)), ("answerRelevance", ("answerRelevance", "answer_relevance")), ("citationResolveRate", ("citationResolveRate", "citation_resolve_rate"))):
        v = _to_float(_lookup(kv, *aliases))
        if v is not None:
            thresholds[key] = v
    for req in ("recallAtK", "groundedness", "citationResolveRate"):
        if req not in thresholds:
            ctx.fail(f"retriever `{rid}` : seuil `{req}` absent de `## Gate Thresholds`", "déclarer les seuils de la RETRIEVAL GATE (G4)", f"{loc}:Gate Thresholds")
    r["gateThresholds"] = thresholds
    return r


# --------------------------------------------------------------------------
# Orchestration (graphe Mermaid -> nœuds / arêtes)
# --------------------------------------------------------------------------
def _resolve_node(ctx: CompileContext, node: mermaid.MermaidNode) -> tuple[str, str]:
    """(kind, ref) : le label puis l'id sont cherchés parmi agents, outils, retrievers."""
    for candidate in (node.label, node.id):
        q = ctx.qualified(candidate)
        if q in ctx.agent_ids:
            return "agent", q
        t = ctx.canonical_tool(candidate)
        if t in ctx.tool_ids:
            return "tool", t
        if q in ctx.retriever_ids:
            return "retriever", q
    label = node.label.lower()
    if "human" in label or "humain" in label:
        return "human", node.label
    if node.shape == "diamond":
        return "router", node.label
    return "function", node.label


def _names(value: str | None) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", value or "")


def compile_orchestration(ctx: CompileContext, topo: TopologySpec, mmd_text: str) -> dict[str, Any]:
    loc = f"workspace/feats/topology/{ctx.number}-topology.md"
    orch: dict[str, Any] = {}
    if topo.root_pattern:
        orch["rootPattern"] = topo.root_pattern
    else:
        ctx.fail("topologie : `Root Pattern:` absent", "déclarer le pattern racine", f"{loc}:header")

    entry = _names(topo.graph_meta.get("noeud d'entree"))
    terminals = _names(topo.graph_meta.get("noeuds terminaux"))
    max_hops = _to_int(topo.graph_meta.get("maxhops"))
    if not entry:
        ctx.fail("topologie : `Nœud d'entrée` non renseigné", "écrire `- **Nœud d'entrée** : `id``", f"{loc}:3")
    else:
        orch["entryNode"] = entry[0]
    if not terminals:
        ctx.fail("topologie : `Nœuds terminaux` non renseignés", "écrire `- **Nœuds terminaux** : `id`, …`", f"{loc}:3")
    else:
        orch["terminalNodes"] = sorted(set(terminals))
    if max_hops is None or max_hops < 1:
        ctx.fail("topologie : `maxHops` absent ou non positif", "déclarer la borne dure du graphe (P12)", f"{loc}:3")
    else:
        orch["maxHops"] = max_hops

    if not mmd_text.strip():
        ctx.fail("topologie : aucun graphe (pas de bloc ```mermaid dans `## 4. Le graphe`)", "dessiner le graphe : c'est lui qui est compilé", f"{loc}:3")
        return orch
    mg = mermaid.parse(mmd_text)
    nodes = []
    for nid in sorted(mg.nodes):
        kind, ref = _resolve_node(ctx, mg.nodes[nid])
        nodes.append({"id": nid, "kind": kind, "ref": ref})
    orch["nodes"] = nodes

    # Arête de repli : `- **Chemin de repli** : `label` -> `cible`, …` — ou
    # `source -> cible`, ou avec la flèche typographique `→` qu'un architecte
    # écrit spontanément : la cible suffit à marquer l'arête, le libellé n'est
    # qu'un second critère.
    fb_label, fb_target = "", ""
    fb = topo.graph_meta.get("chemin de repli", "").replace("→", "->")
    m = re.match(r"^\s*`?([^`>]+?)`?\s*->\s*`?([A-Za-z_][A-Za-z0-9_-]*)", fb)
    if m:
        fb_label, fb_target = m.group(1).strip().lower(), m.group(2).strip()
    edges = []
    for e in mg.edges:
        edge: dict[str, Any] = {"from": e.src, "to": e.dst, "condition": e.label.strip() or "always"}
        if e.free:
            edge["countsAsHop"] = False
        dst = mg.nodes.get(e.dst)
        if fb_label and (edge["condition"].lower() == fb_label or e.dst == fb_target or (dst and dst.label == fb_target)):
            edge["isFallback"] = True
        edges.append(edge)
    orch["edges"] = sorted(edges, key=lambda x: (x["from"], x["to"], x["condition"]))
    return orch


# --------------------------------------------------------------------------
# Évaluation et traçabilité
# --------------------------------------------------------------------------
def _suite_id(cap: CapSpec, metric: str) -> str:
    return f"{cap.number}-{cap.index}-{metric}"


def compile_acceptance_suite(ctx: CompileContext, mission: Any, holdout: str, runs: int) -> dict[str, Any] | None:
    """La suite L9 : l'objectif chiffré de la MISSION, mesuré sur le holdout.

    C'est la seule suite que G8 sait lire (`LEVEL_GATE["L9"] -> G8.acceptance`),
    et rien ne la produisait. `eval_runner` connaissait le niveau, la gate
    l'attendait, et aucun IR n'en contenait jamais une : la part `acceptance`
    de G8 ne pouvait donc être franchie par aucun chemin. Une gate qu'aucune
    exécution ne peut rendre verte n'est pas stricte, elle est morte — et c'est
    la dernière du pipeline, celle qui décide si le produit est livrable.

    Le grader vient de `## Quantified Goal`, jamais d'un défaut : deviner
    comment on mesure l'objectif d'une mission, c'est choisir son verdict à sa
    place. Absent, la suite n'est pas émise et `validate_ir` le refuse avec le
    chemin exact à corriger.
    """
    goal = dict(getattr(mission, "goal", {}) or {})
    # La valeur est le PREMIER mot : une puce peut continuer sur les lignes
    # suivantes avec son explication (« regex (motifs déterministes…) »).
    grader = (str(goal.get("Grader", "")).strip().lower().split() or [""])[0].strip("`*,;:.")
    mloc = f"workspace/feats/missions/{getattr(mission, 'id', ctx.number)}.md"
    if not grader or markdown_io.is_placeholder(grader):
        return None
    if grader not in GRADERS:
        ctx.fail(f"MISSION : `Quantified Goal: Grader: {grader}` inconnu",
                 f"graders admis : {', '.join(GRADERS)}", f"{mloc}:Quantified Goal")
        return None

    threshold = None
    raw_target = str(goal.get("Target", ""))
    match = re.search(r"-?\d+(?:[.,]\d+)?", raw_target.replace(",", "."))
    if match:
        threshold = float(match.group(0))
    if threshold is None:
        ctx.fail(f"MISSION : `Quantified Goal: Target: {raw_target or '<absent>'}` ne porte aucun seuil chiffré",
                 "écrire une cible mesurable, ex. `>= 0.75 sur le holdout` — G0 le refuse aussi",
                 f"{mloc}:Quantified Goal")
        return None

    suite: dict[str, Any] = {
        "id": f"{ctx.number}-acceptance", "level": "L9", "dataset": holdout,
        "grader": grader, "threshold": threshold, "runs": runs,
    }
    if grader == "llm-judge":
        cal = str(goal.get("Calibration", "")).strip()
        suite["judgeCalibrationRef"] = cal if cal and not markdown_io.is_placeholder(cal) else f"workspace/proof/calibration/{ctx.number}-acceptance.json"
    return suite


def compile_evaluation(ctx: CompileContext, caps: list[CapSpec], agents: list[dict[str, Any]], tools: list[dict[str, Any]], retrievers: list[dict[str, Any]], mission: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
    suites: list[dict[str, Any]] = []
    traceability: dict[str, Any] = {}
    agents_by_cap: dict[str, list[str]] = {}
    for a in agents:
        for c in a.get("servesCaps", []):
            agents_by_cap.setdefault(c, []).append(a["id"])
    critical_runs = ctx.config.get_int("EvalRunsCritical", 5) if ctx.config else 5

    for cap in caps:
        loc = f"workspace/feats/caps/{cap.id}.md"
        alloc_agents = sorted({ctx.qualified(a) for a in cap.allocated.get("agents", [])} | set(agents_by_cap.get(cap.id, [])))
        alloc_tools = sorted({ctx.canonical_tool(t) for t in cap.allocated.get("tools", [])})
        alloc_retr = sorted({ctx.qualified(r) for r in cap.allocated.get("retrievers", [])})
        for kind, ids, known in (("agent", alloc_agents, ctx.agent_ids), ("outil", alloc_tools, ctx.tool_ids), ("retriever", alloc_retr, ctx.retriever_ids)):
            for i in ids:
                if i not in known:
                    ctx.fail(f"CAP `{cap.id}` : `## Allocated To` référence le {kind} `{i}` sans contrat", f"écrire le contrat `workspace/feats/contracts/…/{i}.*.md` ou corriger l'allocation", f"{loc}:Allocated To")
        level = "L4" if alloc_agents else ("L3" if alloc_retr else "L2")
        evaluated_by: list[str] = []
        for ac in cap.acs:
            f = ac.fields
            metric = f.get("metric", "").strip()
            grader = f.get("grader", "").strip().lower()
            threshold = ac.threshold_value
            runs = ac.runs
            dataset = f.get("dataset", "").strip()
            if not metric or grader not in GRADERS or threshold is None or runs is None or not dataset:
                ctx.fail(f"CAP `{cap.id}` {ac.id} : AC non évaluable (metric/threshold/dataset/grader/runs)", "corriger l'AC — G1 doit être verte avant de compiler", f"{loc}:Acceptance Criteria")
                continue
            suite: dict[str, Any] = {
                "id": _suite_id(cap, metric), "level": level, "capRef": cap.id,
                "dataset": dataset, "grader": grader, "threshold": threshold, "runs": runs,
            }
            if len(alloc_agents) == 1:
                suite["agentRef"] = alloc_agents[0]
            if grader == "llm-judge":
                cal = f.get("calibration", "").strip()
                suite["judgeCalibrationRef"] = cal if cal and not markdown_io.is_placeholder(cal) else f"workspace/proof/calibration/{metric}.json"
                if f.get("advisory", "").strip().lower() in ("true", "oui", "yes"):
                    suite["advisory"] = True
            suites.append(suite)
            evaluated_by.append(suite["id"])
        if not cap.covers:
            ctx.fail(f"CAP `{cap.id}` : `## Covers` vide", "lister les BR-i / AC-i de la MISSION couverts", f"{loc}:Covers")
        if not evaluated_by:
            ctx.fail(f"CAP `{cap.id}` : aucune suite d'évaluation dérivable (aucun AC valide)", "un AC = une suite ; sans AC, rien ne mesure la CAP", f"{loc}:Acceptance Criteria")
        implemented: dict[str, list[str]] = {}
        if alloc_agents:
            implemented["agents"] = alloc_agents
        if alloc_tools:
            implemented["tools"] = alloc_tools
        if alloc_retr:
            implemented["retrievers"] = alloc_retr
        traceability[cap.id] = {"coversMissionItems": list(cap.covers), "implementedBy": implemented, "evaluatedBy": evaluated_by}

    # Suites d'injection (P8) — une par agent à entrées non maîtrisées.
    for a in agents:
        posture = a.get("trustPosture", {})
        ref = posture.get("injectionSuiteRef")
        if posture.get("untrustedInputs") and ref:
            suites.append({"id": f"{a['id']}-injection", "level": "L8", "agentRef": a["id"], "dataset": ref,
                           "grader": "trajectory", "threshold": 1.0, "runs": critical_runs})

    # Le holdout et la suite d'acceptation ------------------------------------
    #
    # Le holdout est OPTIONNEL à la compilation, et c'est un ordre de pipeline,
    # pas une tolérance : l'IR se compile en PHASE 2, `qa-evals` ne produit les
    # jeux qu'en PHASE 6a, et `qa-evals` lit l'IR pour savoir quoi produire.
    # Exiger le jeu de verdict ici fermait la boucle sur elle-même — aucune
    # mission neuve ne pouvait franchir G2, donc aucune ne pouvait atteindre la
    # phase qui aurait produit le fichier réclamé.
    #
    # Ce qui remplace l'exigence, et la rend tenable :
    #   - `source_hashes` suit désormais le holdout, donc l'IR redevient périmé
    #     dès que le jeu apparaît et la recompilation émet la suite L9 ;
    #   - `validate_datasets` exige le holdout de la mission (part `datasets`
    #     de G8), au moment où il doit exister ;
    #   - `validate_ir` refuse un holdout sans suite d'acceptation.
    # L'exigence n'est pas levée : elle est déplacée là où elle est actionnable.
    holdouts = sorted(paths.datasets_dir(ctx.root, "holdout").glob(f"mission-{ctx.number}-*.jsonl"))
    evaluation: dict[str, Any] = {"suites": sorted(suites, key=lambda s: s["id"]), "baselineRef": f"workspace/proof/baselines/{ctx.number}-system.json"}
    if len(holdouts) > 1:
        ctx.fail(f"{len(holdouts)} holdouts candidats pour la mission {ctx.number} : {[p.name for p in holdouts]}", "un seul `mission-{n}-v*.jsonl` par mission", "workspace/proof/datasets/holdout/")
    elif len(holdouts) == 1:
        evaluation["holdout"] = paths.rel(ctx.root, holdouts[0])
        acceptance = compile_acceptance_suite(ctx, mission, evaluation["holdout"], critical_runs)
        if acceptance is not None:
            suites.append(acceptance)
            evaluation["suites"] = sorted(suites, key=lambda s: s["id"])
    # Suites SYSTÈME (L5 trajectoires, L7 bout en bout) : elles ne naissent
    # d'aucune CAP — elles mesurent la MISSION entière (AC système, budget) — et
    # le compilateur n'en émettait donc aucune. Or `eval-runner` ne lit que
    # `evaluation.suites` de l'IR : G6 (ORCH) n'avait AUCUNE exécution à rendre
    # verte, sur aucune mission. Leur auteur est `qa-evals`
    # (`workspace/proof/suites/{n}-*.yaml`, niveau L5/L7) ; l'IR les projette.
    existing = {s["id"] for s in suites}
    for path in sorted(paths.proof_dir(ctx.root).joinpath("suites").glob(f"{ctx.number}-*.yaml")):
        try:
            spec = yaml_mini.parse_mapping(markdown_io.read_text(path))
        except (yaml_mini.YamlMiniError, OSError):
            continue
        level = str(spec.get("level") or "")
        sid = str(spec.get("id") or path.stem)
        if level not in ("L5", "L7") or sid in existing:
            continue
        grader = str(spec.get("grader") or "")
        threshold = spec.get("threshold")
        runs = spec.get("runs")
        dataset = str(spec.get("dataset") or "")
        if grader not in GRADERS or not isinstance(threshold, (int, float)) or not isinstance(runs, int) or not dataset:
            ctx.report.warn("EVAL_SUITE_INCOMPLETE", f"suite système `{sid}` ({level}) ignorée : grader, threshold, runs ou dataset manquant",
                            "compléter la suite (qa-evals) puis recompiler l'IR", paths.rel(ctx.root, path))
            continue
        suites.append({"id": sid, "level": level, "dataset": dataset, "grader": grader,
                       "threshold": float(threshold), "runs": runs})
        existing.add(sid)
    evaluation["suites"] = sorted(suites, key=lambda s: s["id"])

    adv = sorted({s["dataset"] for s in suites if s["level"] == "L8"})
    if len(adv) == 1:
        evaluation["adversarial"] = adv[0]
    return evaluation, dict(sorted(traceability.items()))


# --------------------------------------------------------------------------
# STACK.md -> mémoire, guardrails
# --------------------------------------------------------------------------
def compile_memory(root: Path) -> dict[str, Any] | None:
    kv = read_stack_section_kv(root, "Active Memory Strategy")
    mapping = {"ShortTermPolicy": "shortTermPolicy", "ShortTermMaxTurns": "shortTermMaxTurns", "SummarizeTriggerTokens": "summarizeTriggerTokens",
               "LongTermEnabled": "longTermEnabled", "LongTermStore": "longTermStore", "LongTermWritePolicy": "longTermWritePolicy",
               "LongTermRetentionDays": "longTermRetentionDays", "MemoryPIIPolicy": "piiPolicy", "CrossAgentSharedState": "crossAgentSharedState"}
    out = {ir: kv[k] for k, ir in mapping.items() if kv.get(k) is not None}
    # Sans mémoire longue, la rétention n'a pas d'objet : STACK.md écrit
    # `LongTermRetentionDays: 0` (le gabarit le propose), et le schéma exige
    # `>= 1` pour une rétention qui EXISTE. Porter 0 dans l'IR faisait échouer
    # G2 sur la MISSION la plus simple — celle sans mémoire longue. On omet la
    # clé ; `longTermEnabled: false` dit déjà tout.
    enabled = str(out.get("longTermEnabled", "")).strip().lower()
    if enabled in ("false", "no", "0", "non", "") and _to_int(out.get("longTermRetentionDays")) in (None, 0):
        out.pop("longTermRetentionDays", None)
    return out or None


def compile_guardrails(root: Path) -> dict[str, Any] | None:
    kv = read_stack_section_kv(root, "Active Guardrails")
    on_trip = kv.get("OnGuardrailTrip")
    out: dict[str, Any] = {}
    for key, ir in (("InputGuardrails", "input"), ("OutputGuardrails", "output")):
        ids = kv.get(key)
        if isinstance(ids, str):
            ids = [i.strip() for i in ids.strip("[]").split(",") if i.strip()]
        if isinstance(ids, list) and ids and on_trip:
            out[ir] = [{"id": str(i), "onTrip": str(on_trip)} for i in ids]
    return out or None


# --------------------------------------------------------------------------
# Hashes des sources et identité de l'IR
# --------------------------------------------------------------------------
def topology_source_hash(root: Path, number: int) -> str:
    """Hash de `{n}-topology.md` — le graphe est une section du fichier, donc déjà dedans.

    La forme `sha256_struct({"md": …})` est conservée : elle a porté un second
    membre `mmd` tant que le graphe vivait dans un fichier à côté, et changer
    la forme du hash ferait passer pour périmé tout IR compilé avant.
    """
    md = paths.topology_dir(root) / f"{number}-topology.md"
    return hashing.sha256_struct({"md": hashing.sha256_spec_file(md) if md.is_file() else ""})


def contract_source_hashes(root: Path, number: int) -> dict[str, str]:
    """Empreinte de chaque contrat compilé, par chemin relatif.

    Les contrats sont des sources de l'IR au même titre que la topologie. Sans
    eux dans `compiledFrom`, éditer un contrat d'outil laisse l'IR se déclarer
    frais alors qu'il ne reflète plus sa source — et c'est exactement le cas que
    `/sdda-topology --recompile-only` existe pour traiter.
    """
    out: dict[str, str] = {}
    for kind in ("agents", "tools", "retrieval", "memory"):
        directory = paths.contracts_dir(root, kind)
        if not directory.is_dir():
            continue
        for p in sorted(directory.glob(f"{number}-*.md")):
            out[paths.rel(root, p)] = hashing.sha256_file(p)
    return out


def source_hashes(root: Path, number: int) -> dict[str, Any]:
    """`compiledFrom` sans `compiledAt` — recalculable par tout validateur (fraîcheur de l'IR)."""
    missions = sorted(paths.missions_dir(root).glob(f"{number}-*.md"))
    caps = sorted(paths.caps_dir(root).glob(f"{number}-*.md"))
    stack = paths.stack_md_path(root)
    holdouts = sorted(paths.datasets_dir(root, "holdout").glob(f"mission-{number}-*.jsonl"))
    return {
        # Spécifications : `Status:` exclu du hash (hashing.spec_text) — l'état
        # dérivé que compute_status réécrit ne doit pas périmer l'IR.
        "missionHash": hashing.sha256_spec_file(missions[0]) if missions else "",
        "capHashes": {p.stem: hashing.sha256_spec_file(p) for p in caps},
        "topologyHash": topology_source_hash(root, number),
        "stackHash": hashing.sha256_file(stack) if stack.is_file() else "",
        "contractHashes": contract_source_hashes(root, number),
        # Le holdout est une SOURCE de l'IR, pas un simple fichier voisin : il
        # fixe le chemin du jeu de verdict et il fait naître la suite
        # d'acceptation L9. L'IR compilé en PHASE 2, avant que `qa-evals` ne
        # produise le jeu, doit donc redevenir périmé dès que ce jeu apparaît —
        # sinon il se déclarerait frais en restant sans rien à mesurer en G8.
        "holdoutHash": hashing.sha256_file(holdouts[0]) if len(holdouts) == 1 else "",
        # Les prompts sont une SOURCE de l'IR au même titre : compilé en PHASE 2
        # sans eux, il doit redevenir périmé quand `dev-prompt` les écrit, sinon
        # `agents[].promptHash` reste absent pour toujours et G5 n'a rien à
        # épingler. Clé par slug de prompt, comme `capHashes` par CAP.
        "promptHashes": {p.stem.removesuffix(".system"): hashing.sha256_file(p)
                         for p in sorted(paths.prompts_dir(root, app_name(root)).glob("*.system.md"))},
    }


def ir_identity(ir: dict[str, Any]) -> dict[str, Any]:
    """L'IR sans ses champs volatils (`compiledAt`, `budget.estimated`)."""
    out = copy.deepcopy(ir)
    out.get("compiledFrom", {}).pop("compiledAt", None)
    out.get("budget", {}).pop("estimated", None)
    return out


def ir_identity_hash(ir: dict[str, Any]) -> str:
    return hashing.sha256_struct(ir_identity(ir))


def dump_ir(ir: dict[str, Any]) -> bytes:
    """Sérialisation canonique : clés triées, indentation 2, LF, UTF-8, `\\n` final."""
    return (json.dumps(ir, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def load_ir(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def default_compiled_at() -> str:
    """`SOURCE_DATE_EPOCH` (builds reproductibles) sinon l'horloge UTC, à la seconde."""
    from sdda_lib.runtime_io import now_iso

    return now_iso()


# --------------------------------------------------------------------------
# Compilation d'une mission
# --------------------------------------------------------------------------
def compile_mission(root: Path, number: int, *, config: LayeredConfig | None = None, compiled_at: str | None = None, previous: dict[str, Any] | None = None) -> tuple[dict[str, Any], Report]:
    """Compile l'IR de la mission `number`. Lève `CompileError` si un champ manque."""
    report = Report(name="IR", target=str(number))
    ctx = CompileContext(root=root, number=number, report=report, config=config)

    missions = sorted(paths.missions_dir(root).glob(f"{number}-*.md"))
    if not missions:
        ctx.fail(f"aucune MISSION `workspace/feats/missions/{number}-*.md`", "créer la MISSION (G0) avant de compiler", str(paths.missions_dir(root)))
        raise CompileError(report)
    mission: MissionSpec = load_mission(root, missions[0].stem)  # type: ignore[assignment]
    report.target = mission.id
    mloc = f"workspace/feats/missions/{mission.id}.md"
    for key in ("CostPerRunTargetUsd", "CostPerRunHardCapUsd", "LatencyP95TargetMs", "TokenCeilingPerRun"):
        if mission.budget.get(key) is None:
            ctx.fail(f"MISSION `{mission.id}` : `{key}` absent de `## Execution Budget`", "déclarer le budget d'exécution (P6) — G0 le refuse aussi", f"{mloc}:Execution Budget")
    budget: dict[str, Any] = {}
    if len(mission.budget) == 4:
        budget = {
            "costPerRunTargetUsd": float(mission.budget["CostPerRunTargetUsd"]),
            "costPerRunHardCapUsd": float(mission.budget["CostPerRunHardCapUsd"]),
            "latencyP95TargetMs": int(mission.budget["LatencyP95TargetMs"]),
            "tokenCeilingPerRun": int(mission.budget["TokenCeilingPerRun"]),
        }

    caps = load_caps_for_mission(root, number)
    if not caps:
        ctx.fail(f"aucune CAP `workspace/feats/caps/{number}-*.md`", "produire les CAPs (G1) avant la topologie", str(paths.caps_dir(root)))

    agents = [a for a in (compile_agent(ctx, p) for p in sorted(paths.contracts_dir(root, "agents").glob(f"{number}-*.agent.md"))) if a]
    tools = [compile_tool(ctx, p) for p in sorted(paths.contracts_dir(root, "tools").glob(f"{number}-*.tool.md"))]
    retrievers = [compile_retriever(ctx, p) for p in sorted(paths.contracts_dir(root, "retrieval").glob(f"{number}-*.retrieval.md"))]
    if not agents:
        ctx.fail(f"aucun contrat d'agent `workspace/feats/contracts/agents/{number}-*.agent.md`", "la topologie produit au moins un contrat d'agent", str(paths.contracts_dir(root, "agents")))
    ctx.agent_ids = {a["id"] for a in agents}
    ctx.tool_ids = {t["id"] for t in tools}
    ctx.tool_names = {str(t["name"]): t["id"] for t in tools if t.get("name")}
    ctx.retriever_ids = {r["id"] for r in retrievers}
    for a in agents:
        # Le §4 d'un contrat d'agent nomme les outils comme le modèle les appelle ;
        # l'IR les porte par id de contrat (cf. `CompileContext.canonical_tool`).
        a["tools"] = sorted({ctx.canonical_tool(i) for i in a.get("tools", [])})
        for kind, ids, known in (("outil", a.get("tools", []), ctx.tool_ids), ("retriever", a.get("retrievers", []), ctx.retriever_ids)):
            for i in ids:
                if i not in known:
                    ctx.fail(f"agent `{a['id']}` : {kind} `{i}` câblé sans contrat", f"écrire le contrat de `{i}` ou retirer la ligne", f"workspace/feats/contracts/agents/{a['id']}.agent.md")

    topo_path = paths.topology_dir(root) / f"{number}-topology.md"
    if not topo_path.is_file():
        ctx.fail(f"topologie `{paths.rel(root, topo_path)}` absente", "produire la TOPOLOGY (architect-topology)", paths.rel(root, topo_path))
        raise CompileError(report)
    topo = parse_topology(markdown_io.read_text(topo_path), topo_path)
    orchestration = compile_orchestration(ctx, topo, load_mermaid(root, topo))
    evaluation, traceability = compile_evaluation(ctx, caps, agents, tools, retrievers, mission)

    # L'IR décrit le SYSTÈME : un outil qu'aucun agent n'appelle, qu'aucune CAP
    # n'alloue et qu'aucun nœud ne référence n'en fait pas partie. Les sources
    # déclarées génèrent trois outils par source (lookup / search / count) ; un
    # roster de moindre privilège en câble une fraction. Porter les autres dans
    # l'IR (4 Ko chacun : schémas, description, erreurs) doublait sa taille et
    # faisait déborder le budget de contexte de chaque agent de construction
    # — 14 outils sur 21 au premier run réel. Leur contrat et leur wrapper
    # restent sur disque ; ils entreront dans l'IR le jour où un agent les câble.
    wired = {t for a in agents for t in a.get("tools", [])}
    wired |= {ctx.canonical_tool(t) for cap in caps for t in cap.allocated.get("tools", [])}
    wired |= {str(n.get("ref")) for n in orchestration.get("nodes", []) if n.get("kind") == "tool"}
    unwired = sorted(t["id"] for t in tools if t["id"] not in wired)
    if unwired:
        report.warn("TOOL_SCOPE_EXCESS",
                    f"{len(unwired)} outil(s) sous contrat mais câblé(s) à aucun agent, exclus de l'IR : {', '.join(unwired[:6])}{' …' if len(unwired) > 6 else ''}",
                    "rien à faire si c'est voulu (moindre privilège) ; sinon câbler l'outil dans le roster et la CAP qui l'exige",
                    paths.rel(root, paths.contracts_dir(root, "tools")))
        report.data.setdefault(str(number), {})["unwiredTools"] = unwired
    tools = [t for t in tools if t["id"] in wired]

    ir: dict[str, Any] = {
        "irVersion": IR_VERSION,
        "missionId": mission.id,
        "compiledFrom": source_hashes(root, number),
        "budget": budget,
        "orchestration": orchestration,
        "agents": sorted(agents, key=lambda a: a["id"]),
        "tools": sorted(tools, key=lambda t: t["id"]),
        "evaluation": evaluation,
        "traceability": traceability,
    }
    if retrievers:
        ir["retrievers"] = sorted(retrievers, key=lambda r: r["id"])
    memory = compile_memory(root)
    if memory:
        ir["memory"] = memory
    guardrails = compile_guardrails(root)
    if guardrails:
        ir["guardrails"] = guardrails
    serving = read_stack_section_kv(root, "Active Serving Surface")
    if isinstance(serving.get("HumanInTheLoopEnabled"), bool):
        ir["orchestration"]["humanInTheLoop"] = serving["HumanInTheLoopEnabled"]

    if report.errors:
        raise CompileError(report)

    # `compiledAt` : conservé si le contenu n'a pas bougé (idempotence), sinon fixé.
    if compiled_at:
        ir["compiledFrom"]["compiledAt"] = compiled_at
    elif previous and ir_identity(previous) == ir_identity(ir) and previous.get("compiledFrom", {}).get("compiledAt"):
        ir["compiledFrom"]["compiledAt"] = previous["compiledFrom"]["compiledAt"]
    else:
        ir["compiledFrom"]["compiledAt"] = default_compiled_at()
    report.data = {"missionId": mission.id, "nodes": len(orchestration.get("nodes", [])), "edges": len(orchestration.get("edges", [])),
                   "agents": len(agents), "tools": len(tools), "retrievers": len(retrievers), "suites": len(evaluation["suites"]), "identityHash": ir_identity_hash(ir)}
    return ir, report


def compile_to_file(root: Path, number: int, *, out: Path | None = None, config: LayeredConfig | None = None, compiled_at: str | None = None) -> tuple[Path, Report]:
    target = out or paths.ir_path(root, number)
    previous = load_ir(target) if target.is_file() else None
    ir, report = compile_mission(root, number, config=config, compiled_at=compiled_at, previous=previous)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(dump_ir(ir))
    report.data["path"] = paths.rel(root, target)
    return target, report


def mission_numbers(root: Path) -> list[int]:
    out = set()
    for p in paths.missions_dir(root).glob("*.md"):
        m = re.match(r"^(\d+)-", p.stem)
        if m:
            out.add(int(m.group(1)))
    return sorted(out)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compile l'Agentic IR depuis les contrats Markdown (déterministe, 0 token)")
    p.add_argument("--mission", type=int, default=None, help="numéro de mission ; défaut : toutes")
    p.add_argument("--out", type=Path, default=None, help="fichier de sortie (une seule mission) ; défaut : workspace/.sys/.ir/{n}-system.ir.json")
    p.add_argument("--compiled-at", default=None, help="horodatage ISO à inscrire (builds reproductibles) ; défaut : conservé si l'IR n'a pas changé")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    combined = Report(name="IR", target=str(root))
    config = load_config(root, combined)
    numbers = [args.mission] if args.mission is not None else mission_numbers(root)
    if not numbers:
        combined.error("IR_COMPILE_FAILED", "aucune MISSION dans workspace/feats/missions/", "créer une MISSION avant de compiler", str(paths.missions_dir(root)))
    if args.out and len(numbers) != 1:
        combined.error("INVALID_ARG", "`--out` exige `--mission {n}`", "préciser la mission")
        return emit(combined, args.json)
    out_path = None
    if args.out:
        out_path = args.out if args.out.is_absolute() else root / args.out
    for n in numbers:
        try:
            path, rep = compile_to_file(root, n, out=out_path, config=config, compiled_at=args.compiled_at)
            combined.extend(rep)
            combined.data[str(n)] = rep.data
        except CompileError as exc:
            combined.extend(exc.report)
    return emit(combined, args.json)


if __name__ == "__main__":
    sys.exit(main())
