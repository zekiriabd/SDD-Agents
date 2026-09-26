#!/usr/bin/env python3
"""G2 (côté IR) — TOPOLOGY GATE sur `workspace/.sys/.ir/{n}-system.ir.json`.

Les 11 contrôles d'AGENTIC-IR.md §4, déterministes, 0 token, chacun avec sa
classe d'erreur :

     1. schéma `ir.schema.json`                              [IR_INVALID]
     2. références closes (nœuds, outils, retrievers, CAPs)  [IR_DANGLING_REF]
     3. atteignabilité depuis `entryNode`, chemin vers un
        terminal depuis tout nœud                            [GRAPH_UNREACHABLE]
     4. tout cycle coupé par une borne                       [UNBOUNDED_LOOP]   (bloquant, sans bypass)
     5. Σ budgetUsd sur le plus long chemin <= hard cap      [BUDGET_EXCEEDED_ESTIMATE]
     6. toute CAP implémentée et tracée                      [CAP_NOT_IMPLEMENTED] / [TRACEABILITY_GAP]
     7. moindre privilège : outil câblé <=> exigé par une CAP [TOOL_SCOPE_EXCESS]
     8. effet de bord non read-only => safetyStrategy        [SIDE_EFFECT_UNDECLARED] (+ [TOOL_RETRY_UNSAFE])
     9. entrée non maîtrisée => suite d'injection            [INJECTION_SUITE_MISSING]
    10. neutralité framework                                 [FRAMEWORK_LEAK_IN_CONTRACT]
   10bis. neutralité d'infrastructure : aucun composant
        (store, embedding, reranker, SGBD) hors de `binding` [INFRA_LEAK_IN_INTENT]
    11. évaluabilité : dataset/grader/threshold/runs, juge
        LLM calibré                                          [AC_NOT_EVALUABLE] / [JUDGE_UNCALIBRATED]

Plus : IR périmé par rapport aux sources ([IR_STALE]), routeur sans repli
([ROUTER_NO_FALLBACK]), critique confondu avec le rédacteur dans un pattern
`reflection` ([REFLECTION_SELF_GRADING]), agent déclaré mais absent du graphe
([AGENT_NOT_IN_IR], WARN).

Sémantique du contrôle 4 — un cycle est BORNÉ si :
  (a) au moins une paire d'arêtes du cycle compte comme hop (`countsAsHop`
      != false pour TOUTES les arêtes entre ces deux nœuds) : `maxHops` le coupe ;
  (b) ou une condition d'arête référence un compteur décrémentant
      (hops, iterations, attempts, retries, remaining…) avec une comparaison ;
  (c) ou c'est une auto-boucle d'un nœud agent : ses `bounds.maxIterations`
      la coupent.
Sinon il est non borné : `[UNBOUNDED_LOOP]`, bloquant, sans bypass (P12).

Usage :
    python .sdda/sdda.py validate-ir --ir workspace/.sys/.ir/1-system.ir.json [--json]
    python .sdda/sdda.py validate-ir --mission 1
    python .sdda/sdda.py validate-ir                 # tous les IR compilés

Rapport : `G2-{missionId}.ir.json`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import calibration, hashing, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import write_gate_report  # noqa: E402
from sdda_lib.graph import Graph  # noqa: E402
from sdda_lib.jsonschema_mini import SchemaValidator  # noqa: E402
from sdda_lib.layered_config import LayeredConfig  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, finish, load_config, resolve_root  # noqa: E402

#: Identifiants d'API de framework (sensibles à la casse, mot entier).
#:
#: La liste couvre les quatre langages du catalogue, pas seulement Python : une
#: fuite de framework dans un contrat est la même faute en C# qu'en Python, et
#: une détection qui ne connaît qu'un écosystème donne un faux vert aux trois
#: autres. Seuls des identifiants **distinctifs** y entrent : `ChatClient` ou
#: `createTool` sont trop génériques, et un faux positif bloque un contrat juste.
FRAMEWORK_API_TOKENS = (
    # Python — LangChain / LangGraph / CrewAI / AutoGen
    "StateGraph", "MessageGraph", "CompiledGraph", "CompiledStateGraph",
    "AgentExecutor", "AgentType", "Runnable", "RunnableSequence", "LLMChain",
    "ConversationChain", "CrewAI", "AssistantAgent", "GroupChat",
    # .NET — Semantic Kernel puis Microsoft Agent Framework
    "Kernel", "KernelFunction", "KernelPlugin", "ChatCompletionAgent",
    "AIAgent", "ChatClientAgent", "AgentThread",
    # Python — Google ADK
    "LlmAgent",
    # Java — LangChain4j
    "AiServices",
    # TypeScript — Vercel AI SDK
    "generateText", "streamText", "generateObject", "streamObject",
)
#: Noms de frameworks (insensibles à la casse).
FRAMEWORK_NAMES = (
    "langgraph", "langchain", "crewai", "autogen", "semantic-kernel", "semantic_kernel", "semantickernel",
    "pydantic-ai", "pydantic_ai", "llamaindex", "llama_index", "llama-index", "spring-ai", "langchain4j", "mastra",
    "vercel-ai-sdk", "ms-agent-framework", "agent-framework", "agno", "google-adk",
)
#: Identifiants d'INFRASTRUCTURE — stores, modèles d'embedding, rerankers,
#: SGBD, pilotes. Ils n'ont rien à faire hors de la branche `binding`.
#:
#: La neutralité framework (P11) ne suffisait pas : elle ne cherche que des
#: noms d'API (`StateGraph`, `Kernel`). `store: "pgvector"` passait, alors
#: qu'il couple exactement de la même façon — un générateur C# qui lit
#: `pgvector` cherche une fiche qui n'existe pas dans son runtime, et la même
#: décision vivait deux fois, dans l'IR et dans `STACK.md`.
INFRA_NAMES = (
    # vector stores
    "pgvector", "qdrant", "weaviate", "milvus", "pinecone", "chroma", "chromadb",
    "faiss", "lancedb", "elasticsearch", "opensearch", "azure-ai-search",
    # modèles et fournisseurs d'embedding
    "voyage", "voyage-3", "text-embedding-3", "text-embedding-ada", "bge-m3",
    "bge-large", "cohere-embed", "nomic-embed", "e5-large",
    # rerankers
    "cohere-rerank", "bge-reranker", "jina-reranker",
    # SGBD et pilotes
    "postgres", "postgresql", "mysql", "mariadb", "sqlserver", "oracle",
    "sqlite", "mongodb", "snowflake", "bigquery", "databricks",
    "psycopg", "asyncpg", "npgsql", "sqlalchemy", "pyodbc",
)
_API_RE = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(FRAMEWORK_API_TOKENS) + r")(?![A-Za-z0-9_])")
_NAME_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(n) for n in FRAMEWORK_NAMES) + r")(?![A-Za-z0-9])", re.IGNORECASE)
_INFRA_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(n) for n in INFRA_NAMES) + r")(?![A-Za-z0-9])", re.IGNORECASE)
#: Condition d'arête « décrémentante » : un compteur ET une comparaison.
_COUNTER_RE = re.compile(r"\b(hops?|iterations?|attempts?|retries|remaining|tries|rounds?|depth|budget|maxHops|maxIterations)\b", re.IGNORECASE)
_COMPARE_RE = re.compile(r"(<=|>=|==|<|>|!=)")
GRADERS = ("exact", "regex", "schema", "numeric-tolerance", "semantic-similarity", "llm-judge", "trajectory", "cost", "latency")


def walk_strings(obj: Any, path: str = "$", skip_keys: frozenset[str] = frozenset()) -> Iterator[tuple[str, str]]:
    """Toutes les chaînes de l'IR (clés ET valeurs) avec leur chemin.

    `skip_keys` élague des sous-arbres entiers : c'est ainsi que le contrôle
    d'infrastructure ignore `binding`, la seule branche où un composant a le
    droit d'être nommé.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip_keys:
                continue
            yield f"{path}.{k} (clé)", str(k)
            yield from walk_strings(v, f"{path}.{k}", skip_keys)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_strings(v, f"{path}[{i}]", skip_keys)
    elif isinstance(obj, str):
        yield path, obj


def framework_leaks(obj: Any) -> list[tuple[str, str]]:
    """[(chemin, identifiant)] pour chaque fuite d'un nom ou d'une API de framework."""
    out = []
    for path, s in walk_strings(obj):
        for m in _API_RE.finditer(s):
            out.append((path, m.group(1)))
        for m in _NAME_RE.finditer(s):
            out.append((path, m.group(1)))
    return out


def infra_leaks(ir: dict[str, Any]) -> list[tuple[str, str]]:
    """[(chemin, identifiant)] pour chaque composant d'infra hors de `binding`.

    Le contrôle ne porte QUE sur les branches déjà scindées — `retrievers[]` et
    `dataAccess[]`. `memory.longTermStore` nomme lui aussi un composant, et
    c'est délibérément hors périmètre : la scission intent/binding de la
    mémoire n'a pas été faite. Étendre le contrôle à une branche non scindée
    produirait un refus que rien ne permet de corriger — et on apprendrait à le
    contourner.
    """
    out = []
    # `id` est élagué aussi : c'est le nom de FICHIER du contrat
    # (`1-data-sqlite-tickets`), choisi pour qu'un humain le retrouve, pas une
    # intention — le refuser imposait de renommer un contrat juste.
    for branch in ("retrievers", "dataAccess"):
        for path, s in walk_strings(ir.get(branch), f"$.{branch}", frozenset({"binding", "id"})):
            for m in _INFRA_RE.finditer(s):
                out.append((path, m.group(1)))
    return out


def load_schema(root: Path | None) -> dict[str, Any]:
    return json.loads(paths.ir_schema_path(root).read_text(encoding="utf-8-sig"))


def source_pins(root: Path, ir: dict[str, Any]) -> dict[str, str]:
    """Hashes épinglés par les rapports G2.ir / G2.budget (fraîcheur, LIFECYCLE R2)."""
    n = int(str(ir.get("missionId", "0")).split("-", 1)[0] or 0)
    pins: dict[str, str] = {"ir": ir_compiler.ir_identity_hash(ir)}
    md = paths.topology_dir(root) / f"{n}-topology.md"
    stack = paths.stack_md_path(root)
    missions = sorted(paths.missions_dir(root).glob(f"{n}-*.md"))
    # Spécifications : hashées sans `Status:` (hashing.spec_text), comme compute_status les relit.
    if missions:
        pins["mission"] = hashing.sha256_spec_file(missions[0])
    for cap in sorted(paths.caps_dir(root).glob(f"{n}-*.md")):
        pins[f"cap:{cap.stem}"] = hashing.sha256_spec_file(cap)
    if md.is_file():
        pins["topology"] = hashing.sha256_spec_file(md)   # le graphe est dedans (bloc ```mermaid)
    if stack.is_file():
        pins["stack"] = hashing.sha256_file(stack)
    return pins


# --------------------------------------------------------------------------
# Les contrôles
# --------------------------------------------------------------------------
def _edge_bounded_by_condition(edge: dict[str, Any]) -> bool:
    cond = str(edge.get("condition", ""))
    return bool(_COUNTER_RE.search(cond) and _COMPARE_RE.search(cond))


def cycle_bound(cycle: list[str], edges_by_pair: dict[tuple[str, str], list[dict[str, Any]]], nodes: dict[str, dict[str, Any]], agents: dict[str, dict[str, Any]], max_hops: Any) -> str | None:
    """Nom de la borne qui coupe le cycle, ou None s'il est non borné."""
    pairs = Graph.cycle_edges(cycle)
    if isinstance(max_hops, int) and max_hops >= 1:
        for pair in pairs:
            if all(e.get("countsAsHop", True) is not False for e in edges_by_pair.get(pair, [])):
                return "maxHops"
    for pair in pairs:
        if any(_edge_bounded_by_condition(e) for e in edges_by_pair.get(pair, [])):
            return "condition décrémentante"
    if len(cycle) == 1:
        node = nodes.get(cycle[0], {})
        agent = agents.get(str(node.get("ref", "")))
        if node.get("kind") == "agent" and agent and isinstance(agent.get("bounds", {}).get("maxIterations"), int):
            return "maxIterations"
    return None


def validate_ir_data(ir: dict[str, Any], *, root: Path | None = None, config: LayeredConfig | None = None, schema: dict[str, Any] | None = None, ir_location: str = "<ir>") -> Report:
    """Les 11 contrôles sur une IR déjà chargée. `root` active les contrôles sur disque."""
    mid = str(ir.get("missionId", "")) if isinstance(ir, dict) else ""
    report = Report(name="G2.ir", target=mid or ir_location)
    loc = ir_location

    # 1. Schéma ----------------------------------------------------------------
    schema = schema or load_schema(root)
    violations = SchemaValidator(schema).validate(ir)
    for v in violations[:50]:
        report.error("IR_INVALID", v, "l'IR ne s'édite pas : corriger le contrat source puis recompiler (ir_compiler.py)", loc)
    if len(violations) > 50:
        report.error("IR_INVALID", f"… et {len(violations) - 50} autre(s) violation(s) de schéma", "", loc)
    if not isinstance(ir, dict):
        return report

    orch = ir.get("orchestration") or {}
    nodes_list = orch.get("nodes") or []
    edges = orch.get("edges") or []
    nodes: dict[str, dict[str, Any]] = {str(n.get("id")): n for n in nodes_list if isinstance(n, dict)}
    agents: dict[str, dict[str, Any]] = {str(a.get("id")): a for a in (ir.get("agents") or []) if isinstance(a, dict)}
    tools: dict[str, dict[str, Any]] = {str(t.get("id")): t for t in (ir.get("tools") or []) if isinstance(t, dict)}
    retrievers: dict[str, dict[str, Any]] = {str(r.get("id")): r for r in (ir.get("retrievers") or []) if isinstance(r, dict)}
    traceability: dict[str, Any] = ir.get("traceability") or {}
    cap_ids = sorted(set(traceability) | set((ir.get("compiledFrom") or {}).get("capHashes") or {}))
    suites: list[dict[str, Any]] = [s for s in ((ir.get("evaluation") or {}).get("suites") or []) if isinstance(s, dict)]
    suite_ids = {str(s.get("id")) for s in suites}
    entry = str(orch.get("entryNode", ""))
    terminals = [str(t) for t in (orch.get("terminalNodes") or [])]
    max_hops = orch.get("maxHops")

    # IR périmé ? --------------------------------------------------------------
    if root is not None and mid:
        n = int(mid.split("-", 1)[0]) if mid.split("-", 1)[0].isdigit() else 0
        current = ir_compiler.source_hashes(root, n)
        compiled = dict(ir.get("compiledFrom") or {})
        compiled.pop("compiledAt", None)
        compiled = ir_compiler.legacy_contract_hashes(root, compiled, current)
        if compiled and compiled != current:
            moved = sorted(k for k in current if compiled.get(k) != current[k])
            report.error("IR_STALE", f"l'IR ne reflète plus les sources ({', '.join(moved)} ont bougé)",
                         f"recompiler : python .sdda/sdda.py ir-compiler --mission {n}", loc)

    # 2. Références closes -----------------------------------------------------
    def dangling(what: str, ref: str, fix: str) -> None:
        report.error("IR_DANGLING_REF", f"{what} référence `{ref}`, non déclaré", fix, loc)

    for nid, node in sorted(nodes.items()):
        kind, ref = node.get("kind"), str(node.get("ref", ""))
        pool = {"agent": agents, "tool": tools, "retriever": retrievers}.get(str(kind))
        if pool is not None and ref not in pool:
            dangling(f"nœud `{nid}` ({kind})", ref, f"écrire le contrat de `{ref}` ou corriger le graphe Mermaid")
    if entry and entry not in nodes:
        dangling("`entryNode`", entry, "le nœud d'entrée doit être un nœud du graphe")
    for t in terminals:
        if t not in nodes:
            dangling("`terminalNodes`", t, "chaque terminal doit être un nœud du graphe")
    for e in edges:
        for end in ("from", "to"):
            if str(e.get(end)) not in nodes:
                dangling(f"arête `{e.get('from')}` -> `{e.get('to')}` ({end})", str(e.get(end)), "corriger le graphe Mermaid")
    for aid, a in sorted(agents.items()):
        for t in a.get("tools") or []:
            if t not in tools:
                dangling(f"agent `{aid}` (tools)", str(t), "écrire le contrat d'outil ou retirer la ligne de `## 4. Outils`")
        for r in a.get("retrievers") or []:
            if r not in retrievers:
                dangling(f"agent `{aid}` (retrievers)", str(r), "écrire le contrat de retrieval ou retirer la ligne")
        for c in a.get("servesCaps") or []:
            if c not in cap_ids:
                dangling(f"agent `{aid}` (servesCaps)", str(c), "la CAP doit exister dans workspace/pipeline/caps/")
        for h in a.get("handoffs") or []:
            to = str(h.get("to", ""))
            if to and to not in nodes and to not in agents:
                report.warn("HANDOFF_UNCONTRACTED", f"agent `{aid}` : handoff vers `{to}` qui n'est ni un nœud ni un agent", "", loc)
    for cid, tr in sorted(traceability.items()):
        impl = tr.get("implementedBy") or {}
        for kind, pool in (("agents", agents), ("tools", tools), ("retrievers", retrievers)):
            for i in impl.get(kind) or []:
                if i not in pool:
                    dangling(f"traceability[{cid}].implementedBy.{kind}", str(i), "corriger `## Allocated To` de la CAP")
        for s in tr.get("evaluatedBy") or []:
            if s not in suite_ids:
                dangling(f"traceability[{cid}].evaluatedBy", str(s), "chaque suite citée doit exister dans evaluation.suites")
    for s in suites:
        if s.get("capRef") and s["capRef"] not in cap_ids:
            dangling(f"suite `{s.get('id')}` (capRef)", str(s["capRef"]), "corriger la référence de CAP")
        if s.get("agentRef") and s["agentRef"] not in agents:
            dangling(f"suite `{s.get('id')}` (agentRef)", str(s["agentRef"]), "corriger la référence d'agent")
    for da in ir.get("dataAccess") or []:
        for a in da.get("exposedTo") or []:
            if a not in agents:
                dangling(f"dataAccess `{da.get('id')}` (exposedTo)", str(a), "corriger la liste d'agents exposés")

    # 3. Atteignabilité ---------------------------------------------------------
    g = Graph(sorted(nodes), [(str(e.get("from")), str(e.get("to"))) for e in edges if str(e.get("from")) in nodes and str(e.get("to")) in nodes])
    if entry in nodes:
        for n in g.unreachable_from(entry):
            report.error("GRAPH_UNREACHABLE", f"nœud `{n}` inatteignable depuis `{entry}`", "ajouter l'arête qui y mène, ou retirer le nœud : un nœud mort est une intention non implémentée", loc)
        live_terminals = [t for t in terminals if t in nodes]
        for n in g.nodes_without_path_to(live_terminals):
            report.error("GRAPH_UNREACHABLE", f"aucun chemin de `{n}` vers un nœud terminal {live_terminals}", "tout chemin doit se terminer : ajouter une arête de sortie ou déclarer le nœud terminal", loc)
    for t in terminals:
        if t in nodes and g.adj.get(t):
            report.warn("TRAJECTORY_DEAD_END", f"le terminal `{t}` a des arêtes sortantes {g.adj[t]} : est-il vraiment terminal ?", "", loc)

    # 4. Bornes des cycles (P12) ----------------------------------------------
    edges_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in edges:
        edges_by_pair.setdefault((str(e.get("from")), str(e.get("to"))), []).append(e)
    bounded_by: dict[str, str] = {}
    for cycle in g.elementary_cycles():
        label = " -> ".join(cycle + [cycle[0]])
        bound = cycle_bound(cycle, edges_by_pair, nodes, agents, max_hops)
        if bound is None:
            report.error("UNBOUNDED_LOOP", f"cycle `{label}` : aucune borne ne le coupe (arêtes gratuites, pas de condition décrémentante, pas d'auto-boucle d'agent)",
                         "faire compter au moins une arête du cycle comme hop (`maxHops`), ou conditionner l'arête sur un compteur — un cycle non borné est un while(true) qui facture (P12)", loc)
        else:
            bounded_by[label] = bound
    report.data["cyclesBoundedBy"] = bounded_by

    # 5. Cohérence des bornes : Σ budgetUsd sur le plus long chemin ---------------
    hard_cap = (ir.get("budget") or {}).get("costPerRunHardCapUsd")
    if isinstance(hard_cap, (int, float)) and entry in nodes:
        weights = {nid: float((agents.get(str(n.get("ref")), {}).get("bounds") or {}).get("budgetUsd") or 0.0) if n.get("kind") == "agent" else 0.0 for nid, n in nodes.items()}
        dag, mapping = g.condensation()
        scc_weight: dict[str, float] = {}
        for nid, rep in mapping.items():
            scc_weight[rep] = scc_weight.get(rep, 0.0) + weights[nid]
        total, path = dag.longest_path(scc_weight, start=mapping[entry])
        report.data["budgetUsdLongestPath"] = {"sum": round(total, 6), "path": path}
        if total > hard_cap + 1e-9:
            report.error("BUDGET_EXCEEDED_ESTIMATE", f"Σ budgetUsd des agents sur le plus long chemin = {total:.4f} USD > costPerRunHardCapUsd {hard_cap}",
                         "resserrer les `budget_usd` par agent, retirer un agent, ou revoir le plafond de la MISSION (P6)", loc)

    # 6. Couverture des CAPs ---------------------------------------------------------
    served = {c for a in agents.values() for c in (a.get("servesCaps") or [])}
    for cid in cap_ids:
        tr = traceability.get(cid)
        if tr is None:
            report.error("TRACEABILITY_GAP", f"CAP `{cid}` absente de `traceability`", "chaque CAP est tracée vers ce qui l'implémente et ce qui la mesure", loc)
            continue
        impl = tr.get("implementedBy") or {}
        if cid not in served and not impl.get("tools") and not impl.get("retrievers"):
            report.error("CAP_NOT_IMPLEMENTED", f"CAP `{cid}` n'est servie par aucun agent ni implémentée par un outil/retriever", "l'allouer (`## Allocated To`) et la lister dans `## 2. Capabilities servies` d'un agent", loc)
        if not tr.get("evaluatedBy"):
            report.error("AC_NOT_EVALUABLE", f"CAP `{cid}` : `evaluatedBy` vide", "un AC = une suite ; sans suite, rien ne mesure la CAP (P2)", loc)

    # 7. Moindre privilège (P8) ----------------------------------------------------
    for aid, a in sorted(agents.items()):
        required: set[str] = set()
        for c in a.get("servesCaps") or []:
            required.update((traceability.get(c, {}).get("implementedBy") or {}).get("tools") or [])
        excess = sorted(set(a.get("tools") or []) - required)
        if excess:
            report.error("TOOL_SCOPE_EXCESS", f"agent `{aid}` porte {excess}, exigé(s) par aucune de ses CAPs {sorted(a.get('servesCaps') or [])}",
                         "retirer l'outil du contrat d'agent, ou l'exiger dans `## Allocated To` de la CAP — un agent ne reçoit que ce que ses CAPs exigent", loc)
    used_tools = {t for a in agents.values() for t in (a.get("tools") or [])}
    for tid in sorted(set(tools) - used_tools):
        report.warn("TOOL_SCOPE_EXCESS", f"outil `{tid}` déclaré mais câblé à aucun agent", "", loc)

    # 8. Effets de bord (P8) --------------------------------------------------------
    for tid, t in sorted(tools.items()):
        cls = t.get("sideEffectClass")
        strategy = t.get("safetyStrategy") or {}
        if cls != "read-only" and not strategy:
            report.error("SIDE_EFFECT_UNDECLARED", f"outil `{tid}` ({cls}) sans `safetyStrategy`",
                         "remplir `## 3. Stratégie de sûreté` : idempotence, dry-run, confirmation, plafond, allowlist", loc)
        if cls != "read-only" and str(strategy.get("idempotency", "none")).lower() == "none" and str(t.get("retryPolicy", "none")).lower() not in ("none", "no-retry"):
            report.error("TOOL_RETRY_UNSAFE", f"outil `{tid}` non idempotent avec `retryPolicy: {t.get('retryPolicy')}`",
                         "un retry sur un outil d'écriture non idempotent crée trois tickets : `retry_policy: none` ou une clé d'idempotence", loc)

    # 8.bis Cohabitation outil destructif <-> entree non maitrisee (P8) --------------
    # `agent-safety.md` §7 declare cette situation bloquante SANS bypass, et la
    # taxonomie la liste parmi les 7 classes sans contournement. Elle n'etait
    # appliquee par rien : l'IR porte pourtant tout ce qu'il faut pour la voir
    # mecaniquement, AVANT la premiere ligne de code.
    #
    # Le danger : un agent expose a du texte hostile ET porteur d'un outil qui
    # supprime, paie ou ecrit chez un tiers. Une injection reussie n'y produit
    # plus une mauvaise reponse mais un degat reel sur des donnees reelles.
    # La parade est architecturale (deux agents, scopes disjoints) et c'est la
    # raison n°1 de la liste close qui autorise un agent supplementaire (P7).
    # Gradient de risque, et non interdit absolu : ce qui compte est la
    # REVERSIBILITE du degat qu'une injection reussie peut declencher.
    #   - `write-destructive` : irreversible. Cohabitation TOUJOURS bloquante.
    #   - `external-side-effect` : le degat existe mais peut etre BORNE. Une
    #     creation de ticket idempotente sur la conversation et plafonnee a 1
    #     par run ne donne rien de plus a un attaquant que ce que l'agent
    #     ferait de toute facon. Bloquante seulement si rien ne la borne.
    # Sans ce gradient, la classe serait contournee partout — et une regle que
    # tout le monde contourne ne protege plus personne.
    def _damage_is_bounded(strategy: dict[str, Any]) -> bool:
        idempotent = str(strategy.get("idempotency", "none")).lower() not in ("", "none")
        capped = bool(strategy.get("cap"))
        confirmed = str(strategy.get("confirmation", "never")).lower() != "never"
        return idempotent and (capped or confirmed)

    for aid, a in sorted(agents.items()):
        untrusted = (a.get("trustPosture") or {}).get("untrustedInputs") or []
        if not untrusted:
            continue
        for tid in sorted(a.get("tools") or []):
            tool = tools.get(tid) or {}
            cls = tool.get("sideEffectClass")
            if cls == "write-destructive":
                report.error(
                    "UNSAFE_TOOL_COHABITATION",
                    f"agent `{aid}` consomme {list(untrusted)} ET porte `{tid}` (write-destructive)",
                    "separer en deux agents aux scopes disjoints — raison n°1 de P7 "
                    "(isolation de scope d'outils). Aucun bypass : le degat est irreversible",
                    loc,
                )
            elif cls == "external-side-effect" and not _damage_is_bounded(tool.get("safetyStrategy") or {}):
                report.error(
                    "UNSAFE_TOOL_COHABITATION",
                    f"agent `{aid}` consomme {list(untrusted)} ET porte `{tid}` "
                    "(external-side-effect) sans strategie qui borne le degat",
                    "soit borner : `idempotency` + (`cap` ou `confirmation`) dans "
                    "`## 3. Strategie de surete` du tool-contract ; soit separer en deux agents",
                    loc,
                )

    # 9. Posture de confiance (P8) ---------------------------------------------------
    injection_agents = {str(s.get("agentRef")) for s in suites if str(s.get("dataset", "")).startswith("workspace/pipeline/datasets/adversarial/") or s.get("level") == "L8"}
    for aid, a in sorted(agents.items()):
        posture = a.get("trustPosture") or {}
        if posture.get("untrustedInputs") and aid not in injection_agents:
            report.error("INJECTION_SUITE_MISSING", f"agent `{aid}` consomme {posture['untrustedInputs']} sans suite d'injection dans evaluation.suites",
                         "déclarer `- **Suite d'injection** : workspace/pipeline/datasets/adversarial/{slug}.jsonl` dans le contrat (invariant injection-suite-mandatory)", loc)
        ref = posture.get("injectionSuiteRef")
        if root is not None and ref and not paths.resolve_rel(root, str(ref)).is_file():
            report.warn("EVAL_DATASET_MISSING", f"agent `{aid}` : suite d'injection `{ref}` absente sur disque (exigée avant G7)", "", loc)

    # 10. Neutralité framework (P11) --------------------------------------------------
    for path, token in framework_leaks(ir):
        report.error("FRAMEWORK_LEAK_IN_CONTRACT", f"`{token}` à {path}",
                     "l'IR décrit QUOI, jamais avec quelle API : retirer l'identifiant du contrat source ; le framework vit dans .sdda/stacks/framework/", loc)

    # 10.bis Neutralité d'INFRASTRUCTURE — la branche `intent` ne nomme aucun composant
    for path, token in infra_leaks(ir):
        report.error("INFRA_LEAK_IN_INTENT", f"`{token}` à {path}, hors de `binding`",
                     "déplacer l'identifiant dans `binding` (la seule branche où un composant se nomme) : "
                     "hors d'elle, l'IR décrit ce qu'on EXIGE, jamais avec quel produit on l'atteint (P11). "
                     "Un générateur écrit son plan contre l'intention et ne résout `binding` qu'à l'émission", loc)

    # 11. Évaluabilité (P2, P3, P9) ----------------------------------------------------
    min_kappa = config.get_float("JudgeCalibrationMinKappa", 0.6) if config else 0.6
    min_items = config.get_int("JudgeCalibrationMinItems", 50) if config else 50
    for s in suites:
        sid = str(s.get("id"))
        ds = str(s.get("dataset", ""))
        if not ds.startswith("workspace/pipeline/datasets/"):
            report.error("AC_NOT_EVALUABLE", f"suite `{sid}` : dataset `{ds}` hors de workspace/pipeline/datasets/", "un dataset est un fichier versionné sous workspace/pipeline/datasets/", loc)
        elif ds.startswith("workspace/pipeline/datasets/holdout/") and s.get("level") != "L9":
            report.error("AC_DATASET_IS_HOLDOUT", f"suite `{sid}` itère sur le holdout `{ds}`", "le holdout rend le verdict (G8) : on n'ajuste jamais contre lui", loc)
        if s.get("grader") not in GRADERS:
            report.error("AC_NOT_EVALUABLE", f"suite `{sid}` : grader `{s.get('grader')}` hors liste close", "", loc)
        if not isinstance(s.get("runs"), int) or s.get("runs", 0) < 1:
            report.error("AC_NOT_EVALUABLE", f"suite `{sid}` : `runs` doit être un entier >= 1", "k runs, toujours (P3)", loc)
        if s.get("grader") == "llm-judge" and not s.get("advisory"):
            # G2 exige que la calibration soit DÉCLARÉE, pas qu'elle soit FAITE.
            #
            # Le jeu de calibration naît en PHASE 6a, lancée par `/sdda-build`,
            # qui refuse de démarrer sans G2 : exiger ici le fichier et son kappa
            # fermait la boucle sur elle-même — la même impasse que le holdout,
            # et que la fixture ne voyait pas parce qu'elle livre son fichier.
            # Le verdict sur l'accord appartient à G5 (`calibrate_judge`, part
            # `calibration`). Ici, un fichier présent est relu par la MÊME
            # lecture que G5 (`calibration.load_calibration_set`) : l'ancienne
            # lecture maison refusait la forme inline (60 paires d'accord total
            # rendaient « accord None ») et croyait un kappa recopié à la main.
            cal = s.get("judgeCalibrationRef")
            if not cal:
                report.error("JUDGE_UNCALIBRATED", f"suite `{sid}` : `llm-judge` bloquant sans `judgeCalibrationRef`", "calibrer le juge (kappa >= seuil) ou le marquer `advisory: true` (P9)", loc)
            elif root is not None:
                p = paths.resolve_rel(root, str(cal))
                dataset = calibration.load_calibration_set(p) if p.is_file() else None
                if dataset is None:
                    report.warn("JUDGE_UNCALIBRATED", f"suite `{sid}` : jeu de calibration `{cal}` absent ou illisible — attendu avant G5, pas avant G2",
                                "qa-evals le produit en PHASE 6a ; G5 (calibrate-judge) rendra le verdict, advisory s'il reste absent", loc)
                else:
                    if dataset.declared_only:
                        result_reason, ok = "accord déclaré sans labels résolvables — rien n'a été recalculé", False
                    else:
                        result = calibration.calibrate(dataset.grader, dataset.human, dataset.judge, min_items=min_items,
                                                       min_agreement=min_kappa, scale=dataset.scale,
                                                       labels_are_synthetic=dataset.labels_are_synthetic)
                        result_reason, ok = result.reason, result.calibrated
                    if not ok:
                        report.warn("JUDGE_UNCALIBRATED", f"suite `{sid}` : calibration non acquise ({result_reason})",
                                    "G5 rendra le juge advisory tant qu'elle ne l'est pas : retravailler la grille, ou labelliser davantage", loc)
        if root is not None and ds.startswith("workspace/pipeline/datasets/") and not paths.resolve_rel(root, ds).is_file():
            report.warn("EVAL_DATASET_MISSING", f"suite `{sid}` : dataset `{ds}` absent sur disque", "", loc)

    # Le holdout sans la suite qui le mesure ---------------------------------------------
    #
    # `holdout` est optionnel dans l'IR parce que le jeu de verdict naît en
    # PHASE 6a, après la compilation. Mais dès qu'il existe, il doit être MESURÉ
    # par une suite L9 : c'est la seule que `eval_runner` mappe sur la part
    # `acceptance` de G8. Un IR qui nomme le holdout sans porter la suite
    # laisserait G8 sans rien à exécuter, et une gate qu'aucune exécution ne
    # peut rendre verte bloque le pipeline sans jamais dire pourquoi.
    #
    # L'inverse — une suite L9 sans holdout — est déjà refusé par le contrôle de
    # dataset ci-dessus, qui exige un chemin sous `workspace/pipeline/datasets/`.
    holdout = str(ir.get("evaluation", {}).get("holdout") or "")
    if holdout and not any(str(s.get("level")) == "L9" for s in suites):
        report.error(
            "ACCEPTANCE_SUITE_MISSING",
            f"le holdout `{holdout}` est déclaré, aucune suite `L9` ne le mesure",
            "déclarer `- Grader: <grader>` dans `## Quantified Goal` de la MISSION puis recompiler l'IR : "
            "c'est de là que naît la suite d'acceptation. Sans elle, G8 n'a aucune exécution à rendre verte",
            loc,
        )

    # Routeur sans repli ; agents hors graphe --------------------------------------------
    # Le routeur d'un pattern `router` n'est pas forcément le nœud d'entrée : le
    # gabarit dessine `entry([entrée]) --> router{…}`, et c'est `entry` qui est
    # déclaré nœud d'entrée (tout doit être atteignable depuis lui). On suit donc
    # les passages obligés — un nœud non-agent avec une seule arête sortante
    # inconditionnelle — jusqu'au premier nœud qui BRANCHE : c'est lui qui doit
    # porter le repli. Exiger `isFallback` sur `entry` rendait rouge une
    # topologie correcte (premier run réel).
    router_node = entry
    seen: set[str] = set()
    while router_node and router_node not in seen:
        seen.add(router_node)
        out = [e for e in edges if str(e.get("from")) == router_node]
        node = nodes.get(router_node) or {}
        if len(out) == 1 and node.get("kind") != "agent" and str(out[0].get("condition", "always")).strip().lower() in ("", "always"):
            router_node = str(out[0].get("to"))
            continue
        break
    for nid, node in sorted(nodes.items()):
        is_router = node.get("kind") == "router" or (orch.get("rootPattern") == "router" and nid == router_node)
        if is_router and not any(e.get("isFallback") for e in edges if str(e.get("from")) == nid):
            report.error("ROUTER_NO_FALLBACK", f"routeur `{nid}` sans arête `isFallback`", "déclarer `- **Chemin de repli** : `label` -> `cible`` dans la topologie : le cas « aucune branche » doit être pensé", loc)
    in_graph = {str(n.get("ref")) for n in nodes.values() if n.get("kind") == "agent"}
    for aid in sorted(set(agents) - in_graph):
        report.warn("AGENT_NOT_IN_IR", f"agent `{aid}` a un contrat mais n'apparaît dans aucun nœud du graphe", "", loc)

    # Reflection : le critique n'est pas le rédacteur ------------------------------------
    #
    # ORCHESTRATION-PATTERNS.md §4 range « critique = rédacteur » parmi les
    # anti-patterns REFUSÉS par la TOPOLOGY GATE, et P7 raison 4 en donne la
    # raison : un modèle qui note sa propre sortie mesure sa complaisance
    # envers lui-même. L'économie est apparente — c'est précisément ce qui la
    # rend tentante, et c'est pourquoi le refus est mécanique et non un
    # conseil de revue.
    if orch.get("rootPattern") == "reflection":
        agent_refs = {str(n.get("ref")) for n in nodes.values() if n.get("kind") == "agent"}
        if len(agent_refs) < 2:
            only = next(iter(agent_refs), "?")
            report.error(
                "REFLECTION_SELF_GRADING",
                f"pattern `reflection` avec un seul agent (`{only}`) : le critique est le rédacteur",
                "déclarer un agent critique DISTINCT du rédacteur qu'il note (P7 raison 4), ou changer de pattern",
                loc,
            )
        for e in edges:
            src, dst = str(e.get("from")), str(e.get("to"))
            if src == dst and nodes.get(src, {}).get("kind") == "agent":
                report.error(
                    "REFLECTION_SELF_GRADING",
                    f"boucle de reflection sur le nœud `{src}` lui-même : l'agent se note",
                    "router la critique vers un nœud agent distinct ; un auto-arc ne peut pas porter une fonction objectif différente",
                    loc,
                )
    for nid, node in sorted(nodes.items()):
        if node.get("nestedPattern") == "reflection" and node.get("kind") == "agent":
            report.error(
                "REFLECTION_SELF_GRADING",
                f"nœud `{nid}` imbrique un `reflection` dans un agent unique (`{node.get('ref')}`)",
                "sortir le critique en nœud agent distinct : imbriquée dans un seul agent, la réflexion est un auto-jugement",
                loc,
            )

    # Roster <-> IR (P7) -------------------------------------------------------------
    if root is not None and mid:
        check_roster(root, mid, agents, tools, traceability, report, loc)

    report.data.update({"missionId": mid, "nodes": len(nodes), "edges": len(edges), "agents": len(agents), "tools": len(tools), "suites": len(suites)})
    return report


def _roster_list(value: Any) -> list[str] | None:
    """`[a, b]` -> noms ; clé absente ou `<à préciser>` -> None (rien à confronter)."""
    if value is None:
        return None
    items = value if isinstance(value, list) else [v for v in str(value).split(",")]
    out = [str(v).strip() for v in items if str(v).strip() and str(v).strip().lower() not in ("aucun", "none", "[]")]
    return None if any(v.startswith("<") for v in out) else out


def check_roster(root: Path, mid: str, agents: dict[str, dict[str, Any]], tools: dict[str, dict[str, Any]],
                 traceability: dict[str, Any], report: Report, loc: str) -> None:
    """Ce que l'IR décrit est-il ce que l'ARCHITECTE a déclaré au roster ?

    `architect-topology` MATÉRIALISE le roster (P7) ; rien ne vérifiait qu'il
    l'avait fait. `validate_architecture` et `roster validate` jugent la
    déclaration complète ; personne ne confrontait le système compilé à la
    déclaration. Un agent ajouté, un tier relevé, un outil de plus ou une CAP
    réallouée par le LLM passaient la TOPOLOGY GATE — exactement l'architecture
    émergente que `architecture-declared-by-architect` existe pour refuser.

    Sans roster `feats/{n}-roster.md` (repli `## 2. Roster déclaré`), rien
    n'est confronté ici : la section vit dans la topologie, qui EST la source.
    """
    from sdda_scripts import validate_architecture  # noqa: PLC0415 — même lecture que G2

    head = mid.split("-", 1)[0]
    path = paths.roster_path(root, head)
    if not path.is_file():
        return
    try:
        data = validate_architecture.read_roster_yaml(path)
    except Exception:  # noqa: BLE001 — l'illisibilité est jugée par `roster validate`
        return
    where = paths.rel(root, path)
    fix = ("l'IR décrit ce que le roster déclare, rien d'autre : aligner la topologie et les contrats sur le roster "
           "(architect-topology le matérialise, il ne le modifie pas), ou faire modifier le roster par l'architecte, puis recompiler")
    declared: dict[str, dict[str, Any]] = {}
    orch = data.get("orchestrator") if isinstance(data.get("orchestrator"), dict) else {}
    for entry in [orch, *[s for s in data.get("subagents") or [] if isinstance(s, dict)]]:
        slug = str(entry.get("id") or "").strip()
        if slug and not slug.startswith("<"):
            declared[slug if re.match(r"^\d+-", slug) else f"{head}-{slug}"] = entry
    if not declared:
        return
    for aid in sorted(set(declared) - set(agents)):
        report.error("ARCH_ROSTER_INCOHERENT", f"agent `{aid}` déclaré au roster `{where}`, absent de l'IR", fix, loc)
    for aid in sorted(set(agents) - set(declared)):
        report.error("ARCH_ROSTER_INCOHERENT", f"agent `{aid}` dans l'IR, que le roster `{where}` ne déclare pas", fix, loc)
    tool_names = {tid: str(t.get("name") or "") for tid, t in tools.items()}
    for aid in sorted(set(declared) & set(agents)):
        entry, agent = declared[aid], agents[aid]
        tier = str(entry.get("tier") or "").strip().lower()
        if tier in ("fast", "balanced", "deep") and agent.get("modelTier") != tier:
            report.error("ARCH_ROSTER_INCOHERENT", f"agent `{aid}` : roster `tier: {tier}`, IR `modelTier: {agent.get('modelTier')}`", fix, loc)
        wanted = _roster_list(entry.get("tools")) if "tools" in entry else None
        if wanted is not None:
            wired = {tool_names.get(t) or t for t in agent.get("tools") or []}
            norm = {w.replace("-", "_") for w in wanted}
            extra = sorted(w for w in wired if w.replace("-", "_") not in norm)
            if extra:
                report.error("ARCH_ROSTER_INCOHERENT", f"agent `{aid}` porte {extra}, que le roster ne lui donne pas", fix, loc)
    for item in data.get("allocation") or []:
        if not isinstance(item, dict):
            continue
        cap = str(item.get("cap") or "").strip()
        owner = str(item.get("agent") or "").strip()
        if not cap or not owner or owner.startswith("<") or cap not in traceability:
            continue
        owner_id = owner if re.match(r"^\d+-", owner) else f"{head}-{owner}"
        impl = (traceability[cap].get("implementedBy") or {}).get("agents") or []
        if owner_id not in impl:
            report.error("ARCH_ROSTER_INCOHERENT", f"CAP `{cap}` allouée à `{owner_id}` au roster, portée par {impl or 'aucun agent'} dans l'IR", fix, loc)


def validate_ir_file(path: Path, root: Path, config: LayeredConfig | None, *, write_report: bool = True) -> Report:
    loc = paths.rel(root, path)
    if not path.is_file():
        r = Report(name="G2.ir", target=loc)
        r.error("IR_NOT_FOUND", f"IR `{loc}` introuvable", "compiler : python .sdda/sdda.py ir-compiler --mission {n}", loc)
        return r
    try:
        ir = ir_compiler.load_ir(path)
    except ValueError as exc:
        r = Report(name="G2.ir", target=loc)
        r.error("IR_INVALID", f"JSON illisible : {exc}", "recompiler l'IR ; il ne s'édite pas à la main", loc)
        return r
    report = validate_ir_data(ir, root=root, config=config, ir_location=loc)
    if write_report and isinstance(ir, dict) and ir.get("missionId"):
        write_gate_report(root, "G2", str(ir["missionId"]), report, source_pins(root, ir), part="ir")
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="G2 (IR) — les 11 contrôles d'AGENTIC-IR.md §4, déterministes, 0 token")
    p.add_argument("--ir", type=Path, default=None, help="fichier IR ; défaut : tous les IR de workspace/.sys/.ir/")
    p.add_argument("--mission", type=int, default=None, help="numéro de mission (IR par défaut : workspace/.sys/.ir/{n}-system.ir.json)")
    # Positionnel, comme `validate-topology` et `validate-mission` : une fiche
    # d'agent qui écrit `validate-ir {chemin}` sortait en `usage:` argparse
    # (exit 2), et l'agent lisait un refus d'argument comme un verdict.
    p.add_argument("files", nargs="*", type=Path, help="fichier(s) IR ; équivalent de --ir")
    add_common_args(p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    combined = Report(name="G2.ir", target=str(root))
    config = load_config(root, combined)
    if args.ir or args.files:
        files = [f if f.is_absolute() else root / f for f in ([args.ir] if args.ir else []) + list(args.files)]
    elif args.mission is not None:
        files = [paths.ir_path(root, args.mission)]
    else:
        files = sorted(paths.ir_dir(root).glob("*-system.ir.json"))
    if not files:
        combined.error("IR_NOT_FOUND", "aucun IR compilé dans workspace/.sys/.ir/", "compiler : python .sdda/sdda.py ir-compiler", str(paths.ir_dir(root)))
    for f in files:
        combined.extend(validate_ir_file(f, root, config, write_report=not args.no_report))
    return finish(combined, args)


if __name__ == "__main__":
    sys.exit(main())
