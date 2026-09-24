# Build roadmap

The implementation order of SDD_Agents itself. The principle that governs it is
the one the framework imposes on its users: **one combination validated end to
end is worth more than twelve announced.**

---

## Where we stand

🟡 is an honesty rule here, not decoration: it marks what is **written but not
yet proven**. A lot is ✅ only if what it delivers is written **and** checked by
tests that run in CI; it is validated in this framework's sense — measured on a
real product — only at lot 6.

| Lot | Content | Status |
|---|---|---|
| 1 | Deterministic skeleton | ✅ written and tested |
| 2 | Evaluation engine | ✅ written and tested — real LLM judge included |
| 3 | Developer Agents on the critical path | 🟡 written, not validated by a complete real run |
| 4 | Python / LangGraph generator | 🟡 written, not validated by a complete real run |
| 5 | Review and acceptance | 🟡 written, not validated by a complete real run |
| 6 | End-to-end validation | ⬜ not started |
| 7.0 | .NET RAG | ⬜ next |
| 7.1 | TypeScript generator | ⬜ next framework axis |

A direct consequence, carried by `registry/compatibility.matrix.json`: the
target combination **C1** is in `design-phase`, the others in `untested`, and
**no combination is validated end to end**. Until lot 6 has published its
numbers, no line of this document is a commitment.

---

## The MVP: a single combination, measured

| Axis | Choice | Why this one |
|---|---|---|
| Language | **Python 3.12** | the agentic ecosystem is most mature there; the framework's deterministic tooling is already written in it (SDD_Pro) |
| Framework | **LangChain + LangGraph** | LangChain alone neither bounds loops nor persists state — yet P12 and resumption are structural. LangGraph brings bounded cycles, checkpointing and human-in-the-loop. **LangSmith stays optional**: tracing goes through the framework's observability layer, not through a vendor dependency |
| Orchestration | **`router` + `sequential`** | the two patterns that cover the most real cases at the most predictable cost. `single-agent` is the default and needs almost nothing to implement |
| RAG | **`hybrid`** (BM25 + vector, RRF) | almost always better than `classic` for a small extra cost; serves as the reference against which every other pattern is measured |
| Vector store | **pgvector** | a single database to operate, transactions with business data, and the RETRIEVAL GATE needs nothing more |
| Reranking | **`none`** | a lever decided on a measurement, not by reflex: good `recall@25` + poor `nDCG@5` is the only case that justifies it. The `rerank/` category exists (`cohere-rerank`, `bge-reranker-local`) so that on the day the measurement calls for it, the `RerankEnabled` key finally loads something — it used to load nothing |
| Database | **PostgreSQL** | same |
| Data access | **`view-per-agent`** | the safest, and the one that gives the best results with the least prompt |
| Tools | **MCP** + repository tools | MCP is the integration standard; repository tools cover the rest |
| Serving | **CLI** then **FastAPI + SSE** | the CLI is enough to validate everything and costs almost nothing |
| Eval | **pytest-eval** + in-house graders | no dependency on a platform until the protocol (k runs, variance, calibration) is proven |
| Observability | **OTel-GenAI** | open standard, no vendor lock-in |

This combination becomes the **C1 combo under SLA commitment** — at lot 6, and
not before. Everything else is `experimental` until measured — and will say so.

---

## Lot 1 — The deterministic skeleton *(no LLM)* — ✅ written and tested

The bet: **everything that can be proven without an LLM must exist before the
first LLM call.** That is what makes the rest debuggable.

1. `bootstrap.py` — interactive: `STACK.md` + `workspace/` tree + smoke.
2. `sdda_lib/` — 3-layer config reading, `markdown_io` (sections,
   frontmatter), hashing, pricing, tracing.
3. JSON schemas: `ir.schema.json`, `golden-set.schema.json`,
   `tool-schema.schema.json`, `project-config.schema.json`.
4. Validators: `validate_mission.py`, `validate_cap.py`,
   `validate_topology.py`, `validate_tool_contract.py`, `validate_datasets.py`.
5. `ir_compiler.py` + `validate_ir.py` — **the core**: the 11 checks of
   `AGENTIC-IR.md §4`.
6. `estimate_budget.py` — cost and latency on the graph.
7. `compute_status.py` — the state machine derived from gates (LIFECYCLE R1).
8. Python tests on all of the above.

> **Lot exit criterion**: you can hand-write a MISSION, CAPs, a TOPOLOGY and
> contracts, compile the IR, and watch gates G0/G1/G2 turn red on deliberately
> defective specifications. Without a single token.

Beyond its initial scope, the lot gained what usage demanded: `STACK.md`
governance (values validated against `project-config.schema.json`,
`[CONFIG_VALUE_INVALID]`, combo signature looked up in the matrix), the
`registry/adr-requirements.yml` registry and `validate_adr.py` (`adr` part of
G2), and ownership hooks that fail closed in strict mode (`SDDA_HOOKS_STRICT=1`,
checked by `hooks-selfcheck`).

## Lot 2 — The evaluation engine *(the differentiating value)* — ✅ written and tested

Before the code generators. Deliberately: a framework that generates before it
knows how to measure produces unverifiable volume.

1. `eval_runner.py` — k-run protocol, mean, standard deviation, pass rate,
   three-colour verdict.
2. Graders: `exact`, `regex`, `schema`, `numeric-tolerance`,
   `semantic-similarity`, `trajectory`, `cost`, `latency`.
3. `llm-judge` + `calibrate_judge.py` (Cohen's kappa, threshold, switch to
   `advisory`). The judge is real: `graders/judge_clients.py` calls Anthropic,
   OpenAI, Gemini or Ollama with the standard library only.
4. `eval_pinning.py` + `check_baseline_freshness.py` — the P10 tuple.
5. `run_retrieval_eval.py` — recall@k, nDCG, context precision, groundedness,
   citation resolve rate.
6. `run_adversarial_suite.py` — direct and indirect injection, played live
   against the delivered surface and replayable (`--replay`).

"Tested" means here: every script has its tests, against simulated executors
and fake judge clients. None of these numbers has yet been produced on a real
system — that is lot 6.

## Lot 3 — The Developer Agents on the critical path — 🟡 written, not validated by a complete real run

Six agents only, in pipeline order:

`po-elicitor` · `po-capabilities` · `architect-topology` ·
`architect-tools` · `architect-rag` · `dev-prompt`

Plus `loader.yml`, `agent-bounds.yaml`, `ownership.md`, `output-protocol.md`,
the `[CLASS]` taxonomy.

> **Exit criterion**: a natural-language specification produces a MISSION,
> CAPs, a TOPOLOGY and contracts that pass G0→G2. Still zero lines of generated
> application code.

The six agents exist, along with the rest of the roster; this criterion has not
yet been met on a real MISSION carried through from start to finish.

## Lot 4 — The Python / LangGraph generator — 🟡 written, not validated by a complete real run

`dev-tools` · `dev-retrieval` · `dev-data` · `dev-agent` · `dev-orchestration` ·
`dev-api`, and the stacks `python.md`, `langgraph.md`, `pgvector.md`,
`hybrid.md`, `view-per-agent.md`, `mcp.md`, `otel-genai.md` (+ `.libs.json`).

To these is added `dev-backend`, the seventh `dev-*`, inherited from SDD_Pro:
the application SHELL (project, composition, config, Domain, packaging), never
the engine. It reads the `archi/` sheets (mvc, ddd, microservice — selected by
`## Active Architecture Pattern`) and, if `DeliverableType: backend-api`, the
`backend/` sheets (python-fastapi, node-express, nestjs, kotlin-spring-boot,
dotnet-minimalapi). These sheets are rewritten for agentic use, not copied: no
ORM, no entity, the "Model" is derived from the IR.

The deterministic part of the generator is written: `gen_app_skeleton.py`
produces the Python skeleton (versions pinned from the active `.libs.json`,
guardrails as code under `templates/runtime/python/app/guardrails/`),
`gen_source_tools.py` the tools for the declared sources.

Gates G3 to G6 wired.

## Lot 5 — Review and acceptance — 🟡 written, not validated by a complete real run

The six reviewers, stages A / B / C, G7 and G8, the validation console (cost per
CAP, score drift, trajectory distribution).

Written: the six reviewers and the three stages of `/sdda-review`, G7 (`suites`,
`adversarial`, `verdict` parts, mandatory reviewer reports) and G8 (holdout +
non-regression). The console does not yet exist as a single screen 🟡: its
measurements do — `cost-report` (cost per run, agent, CAP, node, tool),
`trajectory-report` (trajectories against the IR graph) and `compute-status`
(state derived from the gates).

## Lot 6 — End-to-end validation — ⬜ not started

Build **a real product** with the framework, on a real corpus, with a real
ground truth. Measure. Publish the numbers, including the bad ones. It is this
lot that turns C1 into a combo under SLA — not a declaration.

## Lot 7 — Broadening

One axis at a time, each validated before the next. The next two are the .NET
RAG (7.0) and the TypeScript generator (7.1).

0. **The .NET RAG** — ⬜ next. A prerequisite to the frameworks, and the most
   urgent because it is the only gap today that is *enforced*:
   `preflight_stack_combo` rejects `csharp` + `rag/hybrid` +
   `vectorstore/pgvector` (`[STACK_LANGUAGE_MISMATCH]`), since the whole
   retrieval chain declares `Languages: python`. Exactly four things are
   missing: `vectorstore/pgvector` in an Npgsql variant, the RRF fusion of
   `rag/hybrid` in C#, a .NET `eval/` sheet (xunit.v3 is already pinned) and a
   .NET observability sheet — the .NET OTel packages are already pinned in
   `serving/aspnet-minimal.libs.json`. This gap is what got the `dotnet-api`
   combo removed from the bootstrap: it activated Python sheets on a C# project,
   and nothing said so.
1. **Frameworks**: TypeScript (⬜ next) then .NET — they are what proves the IR
   keeps its promise. If a second generator requires changing the contracts,
   the IR has failed and must be fixed.
   TypeScript note: the target is **LangGraph.js**, not LangChain.js. The
   argument that ruled out LangChain alone in Python (MVP table above) holds
   word for word in TypeScript: it neither bounds loops nor persists state, yet
   P12 and resumption are structural. Announcing LangChain.js contradicted that
   table in the same document. The `lang/typescript.md`,
   `framework/langgraph-js.md` and `serving/cli-node.md` sheets exist, in
   design-phase: they describe, no generator reads them yet, and the bootstrap
   offers no TypeScript combo.
   .NET note: `framework/ms-agent-framework.md` already exists and covers the
   Semantic Kernel + AutoGen convergence, which is Microsoft's current .NET
   target — the axis is therefore about the GENERATOR, not the sheet. Writing
   `framework/semantic-kernel.md` would be building on the branch the vendor no
   longer moves forward.
   Java/Kotlin note: neither candidate brings an equivalent of LangGraph.
   Between Spring AI and Semantic Kernel Java, it is Spring AI — Microsoft is
   not advancing the Java port. But the absence of a bounded graph with
   checkpointing shifts the load onto `dev-orchestration`, and that is a cost
   `framework/spring-ai.md` declares, not one to discover at the first unbounded
   cycle. Same status as TypeScript: `lang/kotlin.md`, `spring-ai.md` and
   `serving/cli-kotlin.md` are on disk, no bootstrap combo.
2. **Orchestration patterns**: `supervisor`, `graph`, `plan-execute`,
   `reflection` — ⬜; only `single-agent`, `router` and `sequential` have a
   sheet.
3. **RAG patterns**: `contextual`, `agentic`, `self-rag`, `corrective-rag` — ⬜;
   only `none` and `hybrid` have a sheet.
4. **Multi-harness**: compiling `.sdda/` → `.codex/`, `.gemini/` — 🟡 the
   compilation is written (`harness_build.py`, `AGENTS.md` and `GEMINI.md`
   pointers at the root), but Codex CLI and Gemini CLI remain
   **experimental**: no blocking gate at runtime, no conformance run. Claude
   Code is the only supported harness.

---

## What we will not do in this order, and why

**Do not start with the code generators.** It is the natural temptation and it
is the mistake: you quickly get something that runs and that you cannot judge.
The evaluation engine before the generators is the most important sequencing
decision of this roadmap.

**Do not support three frameworks at lot 4.** One generator, complete and
measured. The second serves as **a test of the IR**: if it forces changes to the
contracts, the abstraction is wrong — and it is better to learn that at lot 7 on
two generators than at lot 4 on three.

**Do not announce a catalogue before measuring it.** SDD_Pro shows 36 stacks, 8
of them explicitly 🟡. That honesty is what makes the other 28 credible.
Announcing twelve agentic frameworks at launch would produce exactly the false
green this framework exists to prevent.
