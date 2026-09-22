#!/usr/bin/env python3
"""Packs de contexte et budget de contexte — ce qui entre dans un agent.

`loader.yml` déclare, pour chaque Developer Agent, les chemins dont le CONTENU
est présent dans son contexte et un `budget_bytes` dur. Ce script est ce qui
rend cette déclaration exécutable, **avant** le spawn :

    resolve   ce que l'agent va réellement lire, par couche de cache, en octets,
              confronté à son budget — un dépassement refuse le spawn ;
    build     assemble `workspace/.sys/.context/packs/{agent}.md` depuis les
              `pack_sources` déclarées, avec un manifeste hashé ;
    check     le pack correspond-il encore à ses sources ? sinon [PACK_UNUSABLE].
    prune     supprime les packs ORPHELINS — ceux dont le nom ne correspond à
              aucun agent de `loader.yml` (agent renommé ou retiré).

Pourquoi un plafond dur plutôt qu'un avertissement : un agent qui déborde son
budget ne rend pas une sortie plus courte, il rend une sortie **tronquée et
confiante** — et c'est indétectable en aval. Un échec net avant le spawn coûte
une seconde ; une spécification écrite sur un contexte amputé coûte une revue.

Sur le découpage en couches — `stable` (invariant inter-runs), `semi` (invariant
intra-MISSION), `volatile` (par work-item) : il n'existe que pour placer les
points de cache dans le bon ordre. Un contexte mélangé invalide le cache à
chaque item et paie plein tarif sans que rien ne le signale.

**Le trim par rôle n'est pas implémenté, et le manifeste le dit.** Retirer des
sections par heuristique produirait un pack dont personne ne connaît le contenu
réel, ce qui est pire qu'un pack trop gros : au moins celui-là se voit, parce
qu'il dépasse le budget et refuse de partir.

Usage :
    python .sdda/sdda.py context-pack resolve --agent architect-topology --mission 1 --json
    python .sdda/sdda.py context-pack build   --agent architect-topology
    python .sdda/sdda.py context-pack check   --agent architect-rag --json
    python .sdda/sdda.py context-pack prune   --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, paths, yaml_mini  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.layered_config import active_stacks  # noqa: E402
from sdda_lib.runtime_io import now_iso as _now_iso  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, resolve_root  # noqa: E402

#: Clés de `loader.yml` qui ne sont pas des agents.
NON_AGENT_KEYS = {"version", "updated", "cross_agent_reads"}

LAYERS: tuple[str, ...] = ("stable", "semi", "volatile")

#: Au-delà de cette part du budget, le spawn passe au jaune : il reste possible,
#: mais la marge qui absorbe une CAP plus longue que prévu a disparu.
WARN_RATIO = 0.80

#: Inférence de couche quand `loader.yml` ne la déclare pas explicitement.
#: Les motifs sont testés dans l'ordre ; le premier qui matche gagne.
LAYER_RULES: tuple[tuple[str, str], ...] = (
    ("workspace/.sys/.context/packs/", "stable"),   # un pack ne bouge qu'à sa reconstruction
    (".sdda/", "stable"),                           # règles, templates, digests, stacks
    ("workspace/stack/STACK.md", "semi"),
    ("workspace/.sys/.ir/", "semi"),
    ("workspace/.sys/.context/constitution.md", "semi"),
)

PACK_MARKER = "<!-- sdda-pack: "
PACK_MARKER_END = " -->"


# ---------------------------------------------------------------------------
# loader.yml
# ---------------------------------------------------------------------------
def loader_path(root: Path) -> Path:
    """Le `loader.yml` du projet s'il existe, sinon celui du framework."""
    local = root / ".sdda" / "loader.yml"
    return local if local.is_file() else paths.FRAMEWORK_SDDA_DIR / "loader.yml"


def load_loader(root: Path) -> dict[str, Any]:
    return yaml_mini.parse_mapping(markdown_io.read_text(loader_path(root)))


def agent_names(loader: dict[str, Any]) -> list[str]:
    return sorted(k for k, v in loader.items() if k not in NON_AGENT_KEYS and isinstance(v, dict))


# ---------------------------------------------------------------------------
# Expansion des chemins
# ---------------------------------------------------------------------------
def substitute(pattern: str, *, mission: str | None, target: str | None, obj: str | None) -> tuple[str, list[str]]:
    """Remplace `{n}`, `{agent}`, `{object}` ; tout autre placeholder devient `*`.

    Un placeholder inconnu élargi en `*` est rendu dans la liste : un chemin
    qu'on a élargi sans savoir ce qu'il désignait doit se voir, sinon on
    mesurerait un contexte qui n'est pas celui du spawn.
    """
    known = {"n": mission, "agent": target, "object": obj}
    out, widened, buffer, inside = [], [], [], False
    for char in pattern:
        if char == "{":
            inside, buffer = True, []
        elif char == "}" and inside:
            name = "".join(buffer)
            value = known.get(name)
            if value:
                out.append(str(value))
            else:
                out.append("*")
                widened.append(name)
            inside = False
        elif inside:
            buffer.append(char)
        else:
            out.append(char)
    return "".join(out), widened


#: Placeholders de `loader.yml` résolus depuis les stacks ACTIVES de STACK.md.
#:
#: Sans eux, `pack_sources` devait nommer un fichier en dur — et six agents
#: `dev-*` portaient `.sdda/stacks/lang/python.md`. Écrire `lang/csharp.md`
#: n'aurait servi à rien : aucun agent ne l'aurait reçu. Un glob (`lang/*.md`)
#: n'est pas une réponse non plus : il charge les quatre langages dans le
#: contexte d'un agent qui n'en implémente qu'un, et fait exploser le budget
#: à mesure que le catalogue grossit.
STACK_PLACEHOLDERS: dict[str, str] = {
    "lang": "Active Language & Runtime",
    "framework": "Active Agent Framework",
    "orchestration": "Active Orchestration Pattern",
    "rag": "Active RAG Pattern",
    "dataaccess": "Active Data Access",
    "memory": "Active Memory Strategy",
    "serving": "Active Serving Surface",
    # Les catégories restantes. Deux d'entre elles partagent une section —
    # `## Active Retrieval Stack` déclare à la fois le store et l'embedding —
    # et c'est sans conséquence : chaque motif est ancré sur SON répertoire,
    # donc l'expansion croisée (`vectorstore/voyage.md`) ne désigne aucun
    # fichier et ne coûte rien. Ce qui compte est qu'aucun agent ne reçoive
    # plus un catalogue entier là où le projet n'a activé qu'une fiche.
    "vectorstore": "Active Retrieval Stack",
    "embedding": "Active Retrieval Stack",
    "rerank": "Active Reranker",
    "tools": "Active Tools & Integrations",
    "guardrails": "Active Guardrails",
    "observability": "Active Observability",
    "eval": "Active Eval Stack",
}


def active_stack_values(root: Path) -> dict[str, list[str]]:
    """`{lang}` -> `["python"]`, `{framework}` -> `["langchain", "langgraph"]`, …

    Une section absente ou vide laisse le placeholder non résolu : `substitute`
    l'élargit alors en `*` **et le déclare** dans `widened`. Un contexte élargi
    sans qu'on sache ce qu'il désignait doit se voir, sinon on mesure un budget
    qui n'est pas celui du spawn.
    """
    out: dict[str, list[str]] = {}
    for name, heading in STACK_PLACEHOLDERS.items():
        values = [v for v in active_stacks(root, heading) if v]
        if values:
            out[name] = values
    return out


def substitute_all(pattern: str, *, mission: str | None, target: str | None, obj: str | None,
                   stack: dict[str, list[str]] | None = None) -> tuple[list[str], list[str]]:
    """Comme `substitute`, mais un placeholder de stack multi-valué **démultiplie**.

    `framework/{framework}.md` avec LangChain **et** LangGraph actifs donne deux
    motifs, pas un motif élargi : la composition est explicite dans STACK.md, et
    le pack doit refléter exactement ce qui est actif — ni plus, ni moins.
    """
    patterns = [pattern]
    for name, values in sorted((stack or {}).items()):
        token = "{" + name + "}"
        if values and any(token in p for p in patterns):
            patterns = [p.replace(token, value) for p in patterns for value in values]

    resolved: list[str] = []
    widened: list[str] = []
    for candidate in patterns:
        text, holes = substitute(candidate, mission=mission, target=target, obj=obj)
        resolved.append(text)
        widened.extend(holes)
    return resolved, widened


def base_for(root: Path, resolved: str) -> Path:
    """Racine contre laquelle résoudre un motif.

    Un motif `.sdda/…` désigne le framework. Le `.sdda/` du projet l'emporte
    quand il existe — même règle que la config en couches : un projet qui
    embarque son framework le lit, un projet qui n'en a pas lit celui installé.
    """
    if resolved.startswith(".sdda/") and not (root / ".sdda").is_dir():
        return paths.FRAMEWORK_SDDA_DIR.parent
    return root


def expand(root: Path, pattern: str, *, mission: str | None, target: str | None, obj: str | None,
           stack: dict[str, list[str]] | None = None) -> tuple[list[Path], list[str]]:
    """Un motif de `loader.yml` -> les fichiers réels qu'il désigne."""
    resolved_patterns, widened = substitute_all(pattern, mission=mission, target=target, obj=obj, stack=stack)
    files: list[Path] = []
    seen: set[Path] = set()
    for resolved in resolved_patterns:
        base = base_for(root, resolved)
        if any(ch in resolved for ch in "*?["):
            found = sorted(p for p in base.glob(resolved) if p.is_file())
        else:
            candidate = base / resolved
            found = [candidate] if candidate.is_file() else []
        for path in found:
            if path not in seen:
                seen.add(path)
                files.append(path)
    return files, widened


def layer_of(entry: Any, pattern: str) -> str:
    if isinstance(entry, dict) and entry.get("cache_layer") in LAYERS:
        return str(entry["cache_layer"])
    for prefix, layer in LAYER_RULES:
        if pattern.startswith(prefix):
            return layer
    return "volatile"


def read_entries(loader: dict[str, Any], agent: str) -> list[tuple[str, Any]]:
    """(motif, entrée) pour les lectures communes puis celles de l'agent.

    Les lectures communes viennent d'abord : elles sont `stable` et constituent
    le préfixe partagé par tous les agents — donc le préfixe qu'on veut voir
    caché une fois pour toutes.
    """
    entries: list[tuple[str, Any]] = []
    for entry in loader.get("cross_agent_reads") or []:
        pattern = entry.get("path") if isinstance(entry, dict) else entry
        if pattern:
            entries.append((str(pattern), entry))
    for entry in (loader.get(agent) or {}).get("reads") or []:
        pattern = entry.get("path") if isinstance(entry, dict) else entry
        if pattern:
            entries.append((str(pattern), entry))
    return entries


# ---------------------------------------------------------------------------
# Résolution d'un contexte
# ---------------------------------------------------------------------------
@dataclass
class ResolvedFile:
    pattern: str
    path: str
    layer: str
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {"pattern": self.pattern, "path": self.path, "layer": self.layer, "bytes": self.bytes}


@dataclass
class Resolution:
    agent: str
    model_tier: str
    budget_bytes: int
    files: list[ResolvedFile] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    widened: list[str] = field(default_factory=list)
    packs: list[dict[str, Any]] = field(default_factory=list)
    verdict: str = "green"

    @property
    def total_bytes(self) -> int:
        return sum(f.bytes for f in self.files)

    def by_layer(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for layer in LAYERS:
            members = [f for f in self.files if f.layer == layer]
            out[layer] = {"bytes": sum(f.bytes for f in members), "files": [f.path for f in members]}
        return out

    def breakpoints(self) -> list[dict[str, Any]]:
        """Un point de cache après le dernier fichier de chaque couche non vide."""
        marks: list[dict[str, Any]] = []
        for layer in LAYERS[:-1]:  # rien à cacher après le volatile
            members = [f for f in self.files if f.layer == layer]
            if members:
                marks.append({"layer": layer, "after": members[-1].path,
                              "cumulativeBytes": sum(f.bytes for f in self.files if LAYERS.index(f.layer) <= LAYERS.index(layer))})
        return marks

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "modelTier": self.model_tier,
            "budgetBytes": self.budget_bytes,
            "totalBytes": self.total_bytes,
            "usedRatio": round(self.total_bytes / self.budget_bytes, 4) if self.budget_bytes else None,
            "verdict": self.verdict,
            "layers": self.by_layer(),
            "cacheBreakpoints": self.breakpoints(),
            "files": [f.to_dict() for f in self.files],
            "missing": self.missing,
            "widenedPlaceholders": sorted(set(self.widened)),
            "packs": self.packs,
        }

    def render_line(self) -> str:
        glyph = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[self.verdict]
        layers = " ".join(f"{layer}:{self.by_layer()[layer]['bytes'] // 1024}k" for layer in LAYERS)
        return (f"{glyph} {self.agent} ({self.model_tier}) — {self.total_bytes // 1024} Ko / "
                f"{self.budget_bytes // 1024} Ko · {layers} · {len(self.files)} fichier(s)")


def resolve_context(
    root: Path,
    loader: dict[str, Any],
    agent: str,
    *,
    report: Report,
    mission: str | None = None,
    target: str | None = None,
    obj: str | None = None,
) -> Resolution | None:
    """Ce que l'agent lira réellement, par couche, en octets, face à son budget.

    Un chemin que l'agent ÉCRIT (ses `writes:`) et qui n'existe pas encore n'est
    pas une anomalie : c'est le cas nominal du premier run. Il reste listé dans
    `missing` — l'agent doit savoir qu'il ne l'a pas lu — mais sans avertissement.
    """
    spec = loader.get(agent)
    if not isinstance(spec, dict):
        report.error("CONFIG_UNKNOWN_KEY", f"agent `{agent}` absent de loader.yml",
                     f"agents déclarés : {', '.join(agent_names(loader))}", agent)
        return None

    res = Resolution(agent=agent, model_tier=str(spec.get("model_tier") or "balanced"), budget_bytes=int(spec.get("budget_bytes") or 0))
    stack = active_stack_values(root)
    seen: set[str] = set()
    for pattern, entry in read_entries(loader, agent):
        files, widened = expand(root, pattern, mission=mission, target=target, obj=obj, stack=stack)
        res.widened.extend(widened)
        if not files:
            res.missing.append(pattern)
            continue
        layer = layer_of(entry, pattern)
        for path in files:
            rel = _source_ref(root, path)
            if rel in seen:
                continue  # un même fichier compté deux fois gonflerait le budget
            seen.add(rel)
            res.files.append(ResolvedFile(pattern=pattern, path=rel, layer=layer, bytes=path.stat().st_size))

    # L'ordre du contexte EST la stratégie de cache : stable, puis semi, puis
    # volatile. Le tri est stable, donc l'ordre déclaré survit à l'intérieur
    # d'une couche — l'auteur de loader.yml garde la main sur ce qu'il a rangé.
    res.files.sort(key=lambda f: LAYERS.index(f.layer))

    for pattern, _entry in read_entries(loader, agent):
        if "/.context/packs/" not in pattern:
            continue
        resolved, _ = substitute(pattern, mission=mission, target=target, obj=obj)
        state = pack_state(root, loader, agent, root / resolved)
        res.packs.append(state)
        if not state["fresh"]:
            report.error("PACK_UNUSABLE", f"agent `{agent}` : pack `{resolved}` {state['reason']}",
                         f"reconstruire : python .sdda/sdda.py context-pack build --agent {agent}", resolved)

    written = {str(w) for w in (spec.get("writes") or [])}
    for pattern in res.missing:
        if pattern in written:
            continue  # il l'écrira : ne pas le lire d'abord est le cas nominal
        report.warn("PACK_UNUSABLE" if "/packs/" in pattern else "CONFIG_UNKNOWN_KEY",
                    f"agent `{agent}` : `{pattern}` ne désigne aucun fichier — ce contenu ne sera pas dans le contexte",
                    "vérifier le motif dans loader.yml, ou produire l'artefact manquant avant le spawn", pattern)

    if res.budget_bytes and res.total_bytes > res.budget_bytes:
        res.verdict = "red"
        report.error("CONTEXT_BUDGET_EXCEEDED", f"agent `{agent}` : {res.total_bytes} octets de contexte > budget_bytes {res.budget_bytes}",
                     "réduire le pack (trim par rôle), restreindre un motif volatile, ou relever le budget dans loader.yml en connaissance de cause", agent)
    elif res.budget_bytes and res.total_bytes > WARN_RATIO * res.budget_bytes:
        res.verdict = "yellow"
        report.warn("CONTEXT_BUDGET_EXCEEDED", f"agent `{agent}` : {res.total_bytes} octets, soit {res.total_bytes / res.budget_bytes:.0%} du budget — la marge a disparu",
                    "un work-item plus long que la moyenne fera dépasser ce spawn", agent)
    if report.errors:
        res.verdict = "red"
    return res


# ---------------------------------------------------------------------------
# Construction d'un pack
# ---------------------------------------------------------------------------
def pack_path(root: Path, agent: str) -> Path:
    return paths.workspace(root) / ".sys" / ".context" / "packs" / f"{agent}.md"


#: Tranche déclarée en fragment : `…/patterns.registry.json#families=rag,chunking`.
#:
#: `patterns.registry.json` est la SSoT de TOUS les patterns — 78 Ko, six
#: familles. `architect-rag` en consomme deux (`rag`, `chunking`) et recevait
#: les six : la famille `dataaccess` et les graders entraient dans le contexte
#: d'un agent qui ne décide ni de l'un ni de l'autre. Le budget disait la
#: vérité du coût, mais le coût n'avait pas de raison d'être payé — et il
#: grossissait à chaque pattern ajouté au catalogue, quelle que soit sa
#: famille.
#:
#: Le fragment porte la tranche plutôt qu'une clé de `loader.yml` séparée : ce
#: qui est tranché doit se lire sur la ligne qui nomme la source, sinon la
#: relation entre les deux se perd à la première relecture.
_SLICE_RE = re.compile(r"^(?P<path>[^#]+)#families=(?P<families>[A-Za-z0-9_,-]+)$")


def _split_slice(pattern: str) -> tuple[str, tuple[str, ...]]:
    m = _SLICE_RE.match(pattern.strip())
    if not m:
        return pattern, ()
    families = tuple(f for f in m.group("families").split(",") if f)
    return m.group("path"), families


def slice_registry(text: str, families: tuple[str, ...]) -> tuple[str, int]:
    """Le registre réduit aux familles demandées. Renvoie (json, patterns retirés).

    Les métadonnées (`statusLevels`, `syncedFrom`, description) sont CONSERVÉES :
    sans elles, un agent lit un `status: supported` sans savoir ce que le mot
    engage. Ce qui est retiré, ce sont les familles entières et leurs patterns.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return text, 0
    if not isinstance(data, dict):
        return text, 0

    keep = set(families)
    patterns = data.get("patterns")
    dropped = 0
    if isinstance(patterns, list):
        kept = [p for p in patterns if not isinstance(p, dict) or p.get("family") in keep]
        dropped = len(patterns) - len(kept)
        data["patterns"] = kept
    if isinstance(data.get("families"), dict):
        data["families"] = {k: v for k, v in data["families"].items() if k in keep}
    data["_slicedTo"] = sorted(keep)
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), dropped


def pack_sources(root: Path, loader: dict[str, Any], agent: str) -> list[tuple[Path, tuple[str, ...]]]:
    """Les fichiers dont le pack d'un agent est construit, avec leur tranche.

    `pack_sources` désigne « la stack TRANCHÉE que cet agent implémente » : un
    `dev-*` reçoit la fiche du langage et du framework **actifs**, pas les
    quatre langages du catalogue. Le fragment `#families=…` étend le même
    principe à l'intérieur d'un fichier.
    """
    stack = active_stack_values(root)
    out: list[tuple[Path, tuple[str, ...]]] = []
    for pattern in (loader.get(agent) or {}).get("pack_sources") or []:
        raw, families = _split_slice(str(pattern))
        files, _ = expand(root, raw, mission=None, target=None, obj=None, stack=stack)
        out.extend((f, families) for f in files)
    return out


def pack_source_bytes(root: Path, loader: dict[str, Any], agent: str) -> int:
    """Les octets que les `pack_sources` pèsent RÉELLEMENT, tranche appliquée.

    Mesurer la taille des fichiers entiers surestimerait le contexte d'un agent
    dont le pack est tranché — et ferait dire au budget une vérité qui n'est
    plus la sienne.
    """
    total = 0
    for path, families in pack_sources(root, loader, agent):
        if families and path.suffix == ".json":
            sliced, _ = slice_registry(markdown_io.read_text(path), families)
            total += len(sliced.encode("utf-8"))
        else:
            total += path.stat().st_size
    return total


def _source_ref(root: Path, path: Path) -> str:
    """Référence stable d'un fichier : relative au projet, sinon au framework."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        pass
    try:
        return ".sdda/" + path.resolve().relative_to(paths.FRAMEWORK_SDDA_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    for line in markdown_io.read_text(path).split("\n"):
        if line.startswith(PACK_MARKER):
            raw = line[len(PACK_MARKER):]
            raw = raw[: -len(PACK_MARKER_END)] if raw.endswith(PACK_MARKER_END) else raw
            try:
                data = json.loads(raw)
            except ValueError:
                return None
            return data if isinstance(data, dict) else None
    return None


def pack_state(root: Path, loader: dict[str, Any], agent: str, path: Path) -> dict[str, Any]:
    """Le pack est-il utilisable : présent, lisible, et à jour de ses sources ?"""
    state: dict[str, Any] = {"path": paths.rel(root, path), "fresh": False, "reason": ""}
    if not path.is_file():
        state["reason"] = "absent du disque"
        return state
    manifest = read_manifest(path)
    if manifest is None:
        state["reason"] = "sans manifeste `sdda-pack` : impossible de dire de quoi il est fait"
        return state
    state["builtAt"] = manifest.get("builtAt")
    declared = {str(s.get("path")): str(s.get("hash")) for s in manifest.get("sources") or []}
    current = {_source_ref(root, p): hashing.sha256_file(p) for p, _ in pack_sources(root, loader, agent)}
    moved = sorted(k for k in declared.keys() & current.keys() if declared[k] != current[k])
    added = sorted(current.keys() - declared.keys())
    removed = sorted(declared.keys() - current.keys())
    if moved or added or removed:
        bits = [b for b in (f"{len(moved)} source(s) modifiée(s)" if moved else "",
                            f"{len(added)} ajoutée(s)" if added else "",
                            f"{len(removed)} disparue(s)" if removed else "") if b]
        state.update({"reason": "périmé : " + ", ".join(bits), "moved": moved, "added": added, "removed": removed})
        return state
    state["fresh"] = True
    return state


def build_pack(root: Path, loader: dict[str, Any], agent: str, *, report: Report) -> Path | None:
    spec = loader.get(agent)
    if not isinstance(spec, dict) or not spec.get("pack_sources"):
        report.error("CONFIG_UNKNOWN_KEY", f"agent `{agent}` : aucune `pack_sources` dans loader.yml — il n'a pas de pack à construire",
                     f"agents avec pack : {', '.join(a for a in agent_names(loader) if (loader[a] or {}).get('pack_sources'))}", agent)
        return None

    sources = pack_sources(root, loader, agent)
    if not sources:
        report.error("PACK_UNUSABLE", f"agent `{agent}` : les `pack_sources` ne désignent aucun fichier",
                     "vérifier les motifs de `pack_sources` dans loader.yml", agent)
        return None

    # Le contenu retenu par source, tranche appliquée. Le HASH reste celui du
    # fichier ENTIER : c'est lui qui dit si le pack est périmé, et une édition
    # dans une famille non retenue doit quand même faire reconstruire — sinon
    # le manifeste certifierait un état du disque qu'il n'a pas relu.
    bodies: list[str] = []
    entries: list[dict[str, Any]] = []
    trimmed: list[dict[str, Any]] = []
    for path, families in sources:
        text = markdown_io.read_text(path)
        full_bytes = path.stat().st_size
        if families and path.suffix == ".json":
            text, dropped = slice_registry(text, families)
            kept_bytes = len(text.encode("utf-8"))
            trimmed.append({
                "path": _source_ref(root, path), "keptFamilies": sorted(families),
                "patternsDropped": dropped, "bytesSaved": full_bytes - kept_bytes,
            })
            full_bytes = kept_bytes
        bodies.append(text)
        entries.append({
            "path": _source_ref(root, path),
            "hash": hashing.sha256_file(path),
            "bytes": full_bytes,
        })

    manifest = {
        "agent": agent,
        "builtAt": _now_iso(),
        "builder": "context_pack.py",
        "sources": entries,
        "totalBytes": sum(e["bytes"] for e in entries),
        "budgetBytes": int(spec.get("budget_bytes") or 0),
        # Déclaré explicitement : l'agent doit savoir CE QUI a été retiré, pour
        # ne pas supposer l'existence d'une section absente — ni conclure d'une
        # famille manquante qu'elle n'existe pas.
        "trimmed": trimmed,
        "trimPolicyApplied": bool(trimmed),
    }

    lines = [
        f"# CONTEXT PACK — {agent}",
        "",
        PACK_MARKER + json.dumps(manifest, ensure_ascii=False, sort_keys=True) + PACK_MARKER_END,
        "",
        "> Assemblé par `context_pack.py` depuis les `pack_sources` de `loader.yml`.",
        "",
    ]
    if trimmed:
        lines += [
            "> **Ce pack est TRANCHÉ.** Les sources marquées ci-dessous ne t'ont été",
            "> servies que sur les familles qui relèvent de ton rôle. Une famille",
            "> absente n'est pas une famille inexistante : c'est une famille dont",
            "> un autre agent décide. Ne conclus rien de son absence, et ne",
            "> l'invente pas — dis que tu ne l'as pas reçue.",
            "",
            "| Source tranchée | Familles retenues | Patterns retirés | Octets économisés |",
            "|---|---|---:|---:|",
        ]
        lines += [
            f"| `{t['path']}` | {', '.join(t['keptFamilies'])} | {t['patternsDropped']} | {t['bytesSaved']} |"
            for t in trimmed
        ]
        lines.append("")
    else:
        lines += [
            "> **Aucune section n'a été retirée** : le trim par rôle n'est pas appliqué.",
            "> Ce que tu lis ici est le contenu intégral des sources listées — si une",
            "> information manque, elle manque à la source, et l'inventer serait une",
            "> faute de plus grande conséquence que de le dire.",
            "",
        ]
    lines += [
        "## Manifeste des sources",
        "",
        "| Source | Octets | Hash |",
        "|---|---:|---|",
    ]
    lines += [f"| `{e['path']}` | {e['bytes']} | `{hashing.short(e['hash'])}` |" for e in entries]
    policy = str(spec.get("pack_policy") or "").strip()
    if policy:
        lines += ["", "## Politique de packing déclarée", "", policy]

    for (path, _families), entry, text in zip(sources, entries, bodies):
        lines += ["", "---", "", f"## Source : `{entry['path']}`", ""]
        if path.suffix == ".json":
            lines += ["```json", text.rstrip("\n"), "```"]
        else:
            lines.append(text.rstrip("\n"))

    out = pack_path(root, agent)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, out)

    budget = int(spec.get("budget_bytes") or 0)
    size = out.stat().st_size
    if budget and size > budget:
        report.error("CONTEXT_BUDGET_EXCEEDED", f"agent `{agent}` : le pack seul fait {size} octets > budget_bytes {budget} — il ne laisse aucune place aux artefacts du work-item",
                     "trancher le pack par rôle (retraits déclarés au manifeste), réduire `pack_sources`, ou relever le budget en connaissance de cause", paths.rel(root, out))
    report.data.setdefault("packs", []).append({"agent": agent, "path": paths.rel(root, out), "bytes": size, "sources": len(entries)})
    return out


# ---------------------------------------------------------------------------
# Packs orphelins
# ---------------------------------------------------------------------------
#: Suffixe du fichier temporaire de l'écriture atomique (`build_pack`). Un
#: build interrompu peut le laisser sur le disque : c'est le seul compagnon
#: qu'un pack ait — le manifeste vit DANS le pack, pas à côté.
PACK_TMP_SUFFIX = ".tmp"


def packs_dir(root: Path) -> Path:
    return paths.workspace(root) / ".sys" / ".context" / "packs"


def orphan_packs(root: Path, loader: dict[str, Any]) -> list[dict[str, Any]]:
    """Les packs dont le nom ne désigne aucun agent de `loader.yml`.

    Un agent renommé laisse son ancien pack sur le disque ; rien ne le relit,
    mais rien ne le signale non plus — et un lecteur humain le prend pour un
    pack vivant. La source de vérité est celle de tout le script : les agents
    déclarés dans `loader.yml`. `.gitkeep` et tout fichier qui n'est pas un
    `*.md` ne sont jamais considérés.
    """
    folder = packs_dir(root)
    if not folder.is_dir():
        return []
    known = set(agent_names(loader))
    out: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.md")):
        if not path.is_file() or path.stem in known:
            continue
        companions = [c for c in (path.with_name(path.name + PACK_TMP_SUFFIX),) if c.is_file()]
        out.append({
            "agent": path.stem,
            "path": paths.rel(root, path),
            "companions": [paths.rel(root, c) for c in companions],
        })
    return out


def prune_packs(root: Path, loader: dict[str, Any], *, report: Report, dry_run: bool) -> list[dict[str, Any]]:
    """Supprime les packs orphelins (et leur `.tmp` éventuel) ; liste seulement en `dry_run`.

    Une suppression impossible est une erreur d'E/S, rapportée et non levée :
    les autres orphelins sont quand même traités.
    """
    orphans = orphan_packs(root, loader)
    for orphan in orphans:
        orphan["removed"] = False
        if dry_run:
            continue
        targets = [root / orphan["path"]] + [root / c for c in orphan["companions"]]
        try:
            for target in targets:
                target.unlink()
            orphan["removed"] = True
        except OSError as exc:
            report.error("PACK_UNUSABLE", f"pack orphelin `{orphan['path']}` : suppression impossible ({exc.strerror or exc})",
                         "vérifier les droits sur le fichier, puis relancer `context_pack.py prune`", orphan["path"])
    report.data.update({"dryRun": dry_run, "known": agent_names(loader), "orphans": orphans,
                        "removed": sum(1 for o in orphans if o["removed"])})
    return orphans


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Packs et budget de contexte : ce qui entre dans un agent, mesuré avant le spawn (0 token)")
    sub = p.add_subparsers(dest="cmd", required=True)

    res = sub.add_parser("resolve", help="ce que l'agent va lire, par couche, en octets, face à son budget")
    res.add_argument("--agent", required=True)
    res.add_argument("--mission", default=None, help="valeur de `{n}` dans les motifs")
    res.add_argument("--target", default=None, help="valeur de `{agent}` (l'agent GÉNÉRÉ, pas le Developer Agent)")
    res.add_argument("--object", dest="obj", default=None, help="valeur de `{object}` dans les motifs de chemin")
    add_common_args(res)

    bld = sub.add_parser("build", help="assembler le pack depuis les `pack_sources`")
    bld.add_argument("--agent", required=True, help="`--agent all` construit tous les packs déclarés")
    add_common_args(bld)

    chk = sub.add_parser("check", help="le pack correspond-il encore à ses sources ?")
    chk.add_argument("--agent", default="all")
    add_common_args(chk)

    prn = sub.add_parser("prune", help="supprimer les packs dont le nom ne désigne aucun agent de loader.yml")
    prn.add_argument("--dry-run", action="store_true", help="lister les orphelins sans rien supprimer")
    add_common_args(prn)
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="CONTEXT", target=str(root))
    loader = load_loader(root)

    if args.cmd == "resolve":
        res = resolve_context(root, loader, args.agent, report=report, mission=args.mission, target=args.target, obj=args.obj)
        if res is not None:
            report.data.update(res.to_dict())
            if not args.json:
                print(res.render_line())
        return finish(report, args)

    if args.cmd == "prune":
        orphans = prune_packs(root, loader, report=report, dry_run=args.dry_run)
        if not args.json:
            for orphan in orphans:
                extra = f" (+ {', '.join(orphan['companions'])})" if orphan["companions"] else ""
                verb = "à supprimer (dry-run)" if args.dry_run else ("supprimé" if orphan["removed"] else "NON supprimé")
                print(f"prune {orphan['agent']} — orphelin, {verb} -> {orphan['path']}{extra}")
            if not orphans:
                print("prune — aucun pack orphelin")
        return finish(report, args)

    targets: Iterable[str]
    if args.agent == "all":
        targets = [a for a in agent_names(loader) if (loader[a] or {}).get("pack_sources")]
    else:
        targets = [args.agent]

    if args.cmd == "build":
        for agent in targets:
            build_pack(root, loader, agent, report=report)
        if args.agent == "all":
            # Un pack orphelin n'est reconstruit par personne : il resterait sur
            # le disque, l'air d'être vivant. Le dire ici, c'est le rendre visible
            # sans attendre qu'on pense à lancer `prune`.
            orphans = orphan_packs(root, loader)
            if orphans:
                report.warn("PACK_UNUSABLE", f"{len(orphans)} pack(s) orphelin(s) — aucun agent de loader.yml ne les lit : "
                            + ", ".join(o["path"] for o in orphans),
                            "python .sdda/sdda.py context-pack prune")
                report.data["orphans"] = orphans
        if not args.json:
            for entry in report.data.get("packs", []):
                print(f"pack {entry['agent']} — {entry['bytes'] // 1024} Ko depuis {entry['sources']} source(s) -> {entry['path']}")
        return finish(report, args)

    states = []
    for agent in targets:
        state = pack_state(root, loader, agent, pack_path(root, agent))
        state["agent"] = agent
        states.append(state)
        if not state["fresh"]:
            report.error("PACK_UNUSABLE", f"pack de `{agent}` {state['reason']}",
                         f"python .sdda/sdda.py context-pack build --agent {agent}", state["path"])
    report.data["packs"] = states
    if not args.json:
        for state in states:
            print(f"{'🟢' if state['fresh'] else '🔴'} {state['agent']} — {state['reason'] or 'à jour'}")
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
