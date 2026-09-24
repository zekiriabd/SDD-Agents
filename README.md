# SDD_Agents — Spec Driven Development for Agentic Applications

> 🇫🇷 [Version française](README.fr.md) — the French page is a translation of this
> one. Technical content (identifiers, `[CLASS]` codes, paths, flags) is identical
> in both. Prompts, agents, commands and rules under `.sdda/` are written in French.

**The framework that refuses to ship an agent nobody has evaluated.**

SDD_Agents turns a **specification** into a **tested agentic application**:
agents, tools, RAG, orchestration, memory, code, evaluations — in the language
and framework declared in a single file.

Sibling of **SDD_Pro**, from which it inherits the
methodology (source-first, deterministic gates, ownership, invariants, stack
catalogue, harness/provider abstraction) — but **not** its target. SDD_Pro
builds classic applications (mobile, desktop, front/back). SDD_Agents builds
systems of LLM agents.

---

## The ladder, in one picture

```
MISSION            versioned business specification (quantified goal, budget, ground truth)
  └─ CAP           capabilities — 1 discrete skill, *evaluable* AC (metric + threshold + dataset)
       └─ TOPOLOGY agents, tools, orchestration pattern, estimated budget
            └─ CONTRACTS   agent / tool / retrieval / memory — framework-neutral
                 └─ CODE   generated in the declared stack
                      └─ EVAL + TESTS   9 levels, from tool schema to adversarial
                           └─ VERDICT   green / yellow / red, measured, never asserted
```

### What "the declared stack" covers today

The IR is designed multi-language and checks it: `validate_ir.py` rejects any
framework API name inside a contract, including `spring-ai`, `langchain4j` and
`vercel-ai-sdk`. But **a generator only exists where the stack sheets exist**,
and the catalogue covers <!--sdda:count stacks-->45<!--/sdda:count--> of the 99 lines that `STACK.md` offers. The gap is
announced line by line — `(fiche absente)`, "sheet missing" — rather than
implied.

| | Language | Framework | RAG · vector · rerank | Serving | State |
|---|---|---|---|---|---|
| **Python** | ✅ | LangChain · LangGraph | ✅ hybrid · pgvector · rerank | cli · fastapi-sse · batch | usable |
| **.NET** | ✅ | Microsoft Agent Framework | ❌ **no sheet** | aspnet-minimal | **no RAG** |
| **TypeScript** | ✅ sheet | LangGraph.js | ❌ | cli-node · express · nestjs (backend) | **sheets only** — no bootstrap combo (eval/observability are `[python]`), no skeleton generator |
| **Kotlin** | ✅ sheet | Spring AI | ❌ | cli-kotlin · spring-boot (backend) | **sheets only** — same reserve; Maven pins unverified |
| **Java** | ❌ | — | ❌ | ❌ | not planned |

Two catalogue families are inherited from SDD_Pro since 2026-09-23: `archi/`
(mvc · ddd · microservice — the architecture of the application **shell**,
selected by `## Active Architecture Pattern`) and `backend/` (python-fastapi ·
node-express · nestjs · kotlin-spring-boot · dotnet-minimalapi — the HTTP house
around the serving surface, active only for `DeliverableType: backend-api`).
They are rewritten for agentic systems, not copied: no ORM, no entity, the
"Model" is derived from the IR.

**.NET cannot do RAG today.** The whole retrieval chain (`rag/hybrid.md`,
`vectorstore/pgvector.md`, `dataaccess/*`) is written in Python and says so
(`Languages: python`). This is not a documentation omission:
`preflight_stack_combo` **refuses** the combination
(`[STACK_LANGUAGE_MISMATCH]`) instead of letting a .NET generator receive
`psycopg` as a reference and improvise a translation. .NET RAG is Lot 7 of the
[ROADMAP](.sdda/docs/ROADMAP.md).

Nothing here is *validated*: `frameworkStatus: design-phase`, and every
component is `untested` until a measured run has taken place (Lot 6).

---

## Where your work lives — the workspace

What you provide sits at the root; what the framework produces sits under
`pipeline/`. Up to v5, `feats/` mixed your brief and roster with the missions,
capabilities and contracts the agents generate, and `proof/` mixed your ground
truth with the eval sets: opening either, you could not tell what was yours to
fill.

```
workspace/
│ ── what YOU provide ───────────────────────────────────────────────────────
├── stack/     STACK.md, alone, versioned — technical choices, names of variables only
├── feats/     your specs, Markdown only, flat — {n}-{Name}.md (the brief) · {n}-roster.md (the roster)
├── assets/    your data (root of `kind: local` stores) and .env — the secret VALUES of the generated app
├── seed/      your ground truth — annotated scenarios, labels
│ ── what the FRAMEWORK produces ─────────────────────────────────────────────
├── pipeline/  missions · caps · topology · contracts · decisions (ADR) · datasets · suites · baselines · calibration
├── src/       the generated application, prompts and frozen schemas included; .env copied from assets/
└── .sys/      internal state and run output — IR, validation, reports, traces (regenerable)
```

You provide four things: `stack/STACK.md` (language, framework, pattern, data
sources, API URLs, MCP servers), Markdown files under `feats/` (what the system
must do, then the roster that says how), your data and `.env` under `assets/`,
and your ground truth under `seed/`. `python .sdda/sdda.py install-env` copies
`assets/.env` into `src/{App}/.env` without an LLM: the generated application
reads it, the build harness never does, and no agent may read it — the read
hooks refuse `[SECRET_READ_FORBIDDEN]`. **No `dev-*` agent may ever write under
`pipeline/datasets`, `suites`, `baselines` or `calibration`**: the agent that
writes the code cannot touch the dataset that grades it, nor the baseline its
regression is measured against. That is the one boundary of the framework with
no exception, and it is enforced at runtime by the ownership hook, not by
convention.

An older workspace moves to this layout with
`python .sdda/sdda.py migrate-workspace`, which relocates content instead of
creating the new tree next to the old one.

---

## The structural inversion

SDD_Pro builds **backend-first**: `dev-backend` (all user stories) → **API Gate**
→ `dev-frontend`. The gate removes contract drift between the two layers.

SDD_Agents builds **bottom-up, one gate per layer**:

```
TOOLS + RETRIEVAL + DATA ACCESS          ← the layers that touch the real world
   ├─ TOOL GATE       green contracts + live connectivity verified
   └─ RETRIEVAL GATE  recall@k / groundedness / citations on the golden set
        └─ AGENTS (isolated, mocked tools)
             └─ AGENT GATE       every CAP AC evaluated on its agent alone
                  └─ ORCHESTRATION
                       └─ ORCH GATE   trajectories, hop bounds, measured cost/latency
                            └─ SAFETY GATE   injection, tool scope, secrets, PII
                                 └─ ACCEPTANCE GATE   quantified goal on holdout
```

**Why**: without these gates you end up debugging a "bad orchestration" that is
really a bad retriever, or an "agent that hallucinates" that is really a tool
whose schema lies. Each layer proves it works before the next one leans on it.

---

## STACK.md — one file, the whole technical architecture

The declarative configuration file read by **every** agent on **every**
invocation. Switching framework, orchestration pattern, RAG type or database
access strategy means editing one line, not rewriting the code.

```markdown
## Active Language & Runtime
 - .sdda/stacks/lang/python.md

## Active Agent Framework
 - .sdda/stacks/framework/langgraph.md        # langchain alone? +langgraph? +langsmith?

## Active Orchestration Pattern
 - .sdda/stacks/orchestration/supervisor.md   # single-agent | router | sequential | …

## Active RAG Pattern
 - .sdda/stacks/rag/hybrid.md                 # none | classic | agentic | self-rag | …

## Active Retrieval Stack
 - .sdda/stacks/vectorstore/pgvector.md
 - .sdda/stacks/embedding/voyage.md
VectorStoreConnection:                        # WHERE the index lives, and with which keys.
  Mode: same-as-database                      # Distinct from the DB_* block below:
  Endpoint:                                   # that one describes the BUSINESS database,
  Collection:                                 # this one the VECTOR index. Mixing them
  ApiKeyEnv:                                  # up goes unnoticed as long as the store
  Dimensions: 1024                            # is pgvector — it then lives in the same
                                              # database, by coincidence.

## Active Reranker
 - .sdda/stacks/rerank/none.md                # none | cohere-rerank | bge-reranker-local
                                              # Decided on a measurement: good recall@25
                                              # + mediocre nDCG@5 = the reranker's case.

## Active Data Access
 - .sdda/stacks/dataaccess/view-per-agent.md  # view-per-agent | text-to-sql | repository-tools | …
```

Every activated line must point to a sheet **that exists** and **that speaks the
active language** — otherwise `preflight_stack_combo` refuses the spawn
(`[STACK_COMBO_UNLOADABLE]`, `[STACK_LANGUAGE_MISMATCH]`). An activated line
for a missing sheet loads nothing: the agent works without a layer mapping,
without idioms and without `.libs.json`, so it invents.

Full specification: [.sdda/templates/STACK.md.template](.sdda/templates/STACK.md.template).

---

## What really changes compared to a classic app

| Classic application | Agentic application |
|---|---|
| `assertEquals(f(x), y)` | distribution of outputs → **eval over k runs**, success rate + variance |
| The spec produces code | **Part of the spec *is* the prompt** — it has to survive compilation |
| Performance is an ops topic | **Cost and latency are functional** — 7 hops at $0.40/call kill the product |
| Stack trace | **Span trace** (agent turn, tool call, retrieval, tokens, cost) |
| A regression breaks a test | A regression **lowers a score** — you need a versioned baseline |
| The attack surface is the API | The attack surface is **every retrieved document** (indirect injection) |
| Refactor = the compiler catches you | Edit a prompt = **nothing catches you** → hash pinning + mandatory re-eval |

---

## Design documentation

Documentation is English by default; French twins carry the `.fr.md` suffix.
The hub [.sdda/docs/README.md](.sdda/docs/README.md) lists every document and
its available languages.

| Document | Purpose |
|---|---|
| [PHILOSOPHY.md](.sdda/PHILOSOPHY.md) | The 12 founding principles |
| [ARCHITECTURE.md](.sdda/ARCHITECTURE.md) | Tree, pipeline, 9 gates, harness/provider abstraction |
| [SDD-PRO-INHERITANCE.md](.sdda/docs/SDD-PRO-INHERITANCE.md) | What we inherit, what we refuse, what we add |
| [DOMAIN-MODEL.md](.sdda/docs/DOMAIN-MODEL.md) | The closed vocabulary: MISSION, CAP, AGENT, TOOL, RETRIEVER… |
| [AGENTIC-IR.md](.sdda/docs/AGENTIC-IR.md) | The intermediate representation that makes multi-framework deterministic |
| [LIFECYCLE.md](.sdda/docs/LIFECYCLE.md) | State machine Draft → Approved, derived from the gates |
| [AGENT-ROSTER.md](.sdda/docs/AGENT-ROSTER.md) | The <!--sdda:count agents-->23<!--/sdda:count--> Developer Agents and their internal orchestration |
| [ORCHESTRATION-PATTERNS.md](.sdda/docs/ORCHESTRATION-PATTERNS.md) | Catalogue + selection matrix |
| [RAG-PATTERNS.md](.sdda/docs/RAG-PATTERNS.md) | Catalogue + gate metrics |
| [MEMORY-PATTERNS.md](.sdda/docs/MEMORY-PATTERNS.md) | Scopes, costs, and memory as a persistent attack surface |
| [DATA-ACCESS.md](.sdda/docs/DATA-ACCESS.md) | Database access strategies for agents |
| [MULTI-HARNESS.md](.sdda/docs/MULTI-HARNESS.md) | Compilation to Claude Code / Codex / Gemini CLI |
| [TESTING-AND-EVAL.md](.sdda/docs/TESTING-AND-EVAL.md) | The L0→L9 pyramid |
| [INVARIANTS.yml](.sdda/INVARIANTS.yml) | The <!--sdda:count invariants-->21<!--/sdda:count--> load-bearing contracts + their enforcer |
| [ROADMAP.md](.sdda/docs/ROADMAP.md) | Build order + the MVP |
| [PLANNED-SCRIPTS.md](.sdda/docs/PLANNED-SCRIPTS.md) | The deterministic backlog, generated — who asks for what |

**Specification templates**: [mission](.sdda/templates/mission.template.md) ·
[capability](.sdda/templates/capability.template.md) ·
[topology](.sdda/templates/topology.template.md) ·
[agent-contract](.sdda/templates/agent-contract.template.md) ·
[tool-contract](.sdda/templates/tool-contract.template.md) ·
[STACK.md](.sdda/templates/STACK.md.template)

---

## Checking the real state

The framework carries its own anti-rot device — the one that refuses to let a
written rule go unenforced:

```bash
python .sdda/sdda.py framework-smoke
```

It checks: agents ↔ tier bounds parity, invariants ↔ enforcers on disk, error
class reciprocity, up-to-date digests, internal references, and **catalogue
honesty** — no stack sheet can declare itself validated unless a run has
measured it.

```bash
python .sdda/sdda.py sync-error-registry --check   # taxonomy
python .sdda/sdda.py sync-digests --check          # per-agent digests
python -m pytest .sdda/python/tests/ -q                         # deterministic layer
```

---

## Status

**Lots 1 and 2 written.** The deterministic base and the evaluation engine exist
and are tested (<!--sdda:count tests-->1214<!--/sdda:count--> test functions):

- `bootstrap.py` end to end; G0 (mission), G1 (capabilities) and G2 (topology,
  IR, budget) actually **refuse** a defective specification — a non-measurable
  acceptance criterion exits 1 with `[AC_NOT_EVALUABLE]`;
- `ir_compiler.py` + `validate_ir.py` and their checks, `estimate_budget.py`,
  `compute_status.py`;
- `eval_runner.py` (k runs, variance, three-colour verdict), the graders,
  `calibrate_judge.py` (Cohen's κ), pinning and regression detection;
- `run_retrieval_eval.py` (G4 — recall@k, nDCG, context precision, citations,
  **without any agent**) and `run_adversarial_suite.py` (G7 — attack family
  coverage and replay of the versioned set).

**None of these scripts calls an LLM**: they receive an injected executor or
replay recorded runs. This is deliberate — today the framework knows how to
*judge* a specification and a measurement; it does not yet know how to
*produce* one.

**Lot 3 in progress** — the junction between agent sheets and the pipeline:

- `sdda_state.py` — run journal: identity, phases, resume (`--resume`),
  bypasses. It computes no artefact state: that remains `compute_status.py`,
  and two truths about the same fact make zero verifiable truth;
- `context_pack.py` — `loader.yml` made executable: what each agent will
  actually read, per cache layer, in bytes, confronted with its `budget_bytes`.
  An overrun or a stale pack **refuses the spawn**. `bootstrap.py` builds the
  packs when the workspace is created;
- `spawn_brief.py` — the invocation prompt is no longer copied into every
  command: it is assembled. Sheet + hash, tier resolved against
  `agent-bounds.yaml` (`tier_floor` does not yield), resolved context, write
  scope with placeholders resolved, and the **injected facts** — what an agent
  must know but that ownership forbids it from reading.

**The first agent sheet has been executed.** `po-elicitor` produced a MISSION
from a client brief, in non-interactive mode: it left 19 `<à préciser>`
placeholders, refused to convert 0.05 EUR to USD without a declared parity,
refused to invent the Failure Policy the brief said was undecided — and G0
blocked, as designed. The sheet holds as a prompt.

What the measurement and that execution found, now fixed:

- six `dev-*` agents read a pack that nothing knew how to build;
- the `architect-rag` budget was untenable (171 KB measured against 150 KB
  declared — 75 KB of it for `patterns.registry.json` alone);
- `validate_mission`, `validate_cap` and `validate_topology` did not accept the
  `--mission {n}` that the commands have written since day one;
- `po-elicitor` had to fill `## Required Stack` without being allowed to read
  `STACK.md`: the sheet was untenable until the brief injected the active stacks;
- `{m}` meant two things in `loader.yml` (the CAP index and "another MISSION"),
  which prevented `po-capabilities` from reading its own.

Still open, unresolved: the budget is expressed in USD (`CostPerRunTargetUsd`)
while briefs arrive in euros, with no parity declared anywhere.

**Lot 4 started — the generation gates.** `validate_tool_contract.py` writes
the `contracts` part of the TOOL GATE (G3): meta-schema of tool schemas,
`required` ⊆ `properties`, safety strategy consistency, contract ↔ IR ↔ **code**
confrontation, DB envelope. `check_ir_freshness.py` refuses to generate from an
IR that no longer describes its sources.

Two fundamental defects found while wiring them:

- `compute_status` looked for `G3-{tool}`, `G4-{retriever}`, `G5-{cap}`, while
  `eval_runner` wrote everything under `{mission}`. Reports were green in files
  the state machine never opened: the `Tested` state was **unreachable**.
  Granularity now follows the LIFECYCLE;
- `compiledFrom` did not hash the contracts. Editing a tool schema left the IR
  declaring itself fresh while describing something else — exactly the case
  `/sdda-topology --recompile-only` exists to handle. `contractHashes` entered
  the IR and its schema.

**The API GATE finally exists.** `validate_api_contract.py` writes the `api`
part of G6: published schemas confronted with the IR's `inputSchema` /
`outputSchema` in both directions, routes confronted with the active surface's
contract, HTTP statuses confronted with the `[CLASS]` → code table. As long as
no `openapi.json` is published, the part is **not applicable**: it writes no
report, hence grants no green.

What an audit of the anti-rot device found, now fixed:

- `[API_CONTRACT_DRIFT]` and `[API_ROUTE_UNBACKED]` were **announced as
  blocking in `ARCHITECTURE.md §4` and emitted by nothing**. The canonical
  registry called itself "up to date" because it is regenerated from the
  *real* emitters: a class that lives only in prose never enters it, and its
  absence goes unseen. `framework_smoke.py` now carries `errors.documented`,
  which **fails** on any class cited in normative prose and emitted by nobody.
  Same story for `[REFLECTION_SELF_GRADING]`, the "critic = author"
  anti-pattern refused by the TOPOLOGY GATE — on paper — since day one;
- the agent contract template numbered `## 7. Règles` then `## 7. Retrievers`,
  with no `## 6`: five sheets read `§12 handoffs` where the template wrote
  `§13`, and `lint_prompts.py` looked for a `## 6. Règles` that did not exist.
  Two new checks close the door — `templates.numbering` (contiguous sequence,
  no duplicate) and `refs.sections` (every `§n` cited next to an artefact
  designates a section that exists);
- **the skills/rules mechanism could not work end to end**: `lint_prompts.py`
  looks for `## Compétences` and `## Règles` as H2, while `prompt.template.md`
  wrote its titles as H1 and carried neither. The three divergent definitions
  of a prompt's structure (rule, template, `dev-prompt` sheet) are aligned on a
  single one, and `prompt-authoring.md §2` is its SSoT;
- the per-role context trim, announced in a `loader.yml` comment, now exists:
  `pack_sources` accepts `#families=…`. `architect-rag` receives the two
  registry families it decides on instead of the catalogue's six — pack from
  161 KB to 107 KB, budget from 92 % to 71 %, **without raising the ceiling**.
  Removals are declared in the pack manifest, so that an absent family does not
  read as a non-existent one.

**The IR is split into `intent` / `binding`** — before Lot 4, deliberately.

`store: "pgvector"` and `embeddingModel: "voyage-3-large"` were **mandatory**
IR fields, while the same decision already lived in
`STACK.md ## Active Retrieval Stack`. Two truths about the same fact, that
nothing confronted — and the neutrality check did not see them, because it only
looked for framework **API** names. A C# generator reading `pgvector` would have
gone looking for a sheet that does not exist in its runtime.

The IR now carries two branches: the **intent** (`pattern`, `topK`,
`citationMode`, `identityFilter`, `gateThresholds`, `envelope`, `exposedTo`) and
the **`binding`** (`store`, `embeddingModel`, `chunk`, `hybridWeights`,
`rerank`, `strategy`). Three mechanisms hold it:

- `[INFRA_LEAK_IN_INTENT]` — any component name outside `binding` is refused;
- `[RETRIEVAL_BINDING_MISMATCH]` — the `binding` is **reconciled** with the
  active stacks at compile time, on the *family* (`voyage` covers
  `voyage-3-large`); the version itself stays pinned by `indexHash`;
- a test that simulates the **second generator**: it derives a complete call
  plan without ever reading `binding`, and that plan is identical for
  `pgvector` and for any other store.

The ROADMAP planned to learn about a possible failure of the IR at Lot 7, on the
second generator. Doing it now costs a schema, a compiler and a validator;
doing it after Lot 4 would have cost six generators already written against a
contract known to be wrong. `memory.longTermStore` also names a component and
stays out of scope: its own split is not done, and the check says so rather
than staying silent.

**Audit lot — the pipeline could not complete once, and five controls were
inert.** A full source-by-source audit, then every finding fixed. What it found:

- `ir_compiler` demanded the holdout that `qa-evals` only produces three phases
  later, so no fresh mission ever passed G2. The requirement moved to
  `validate_datasets` (G8), where it is actionable, and the holdout became a
  compiled source so its arrival stales the IR;
- **no L9 suite was ever compiled**: G8's `acceptance` part had no execution
  able to turn it green. It is now derived from `Grader:` in the mission's
  `## Quantified Goal`;
- nine CLI options were cited by prompts and existed in no script (`--pre`,
  `--static`, `--isolated`, `--dataset`, `--require`, `--min-items`, …). argparse
  answered with a `usage:` instead of an `ERROR/CAUSE/FIX` block, so the
  orchestrating model concluded the check "had nothing to say". A new scanner,
  `command-flags`, resolves every cited option against the real parser and
  runs in `framework-smoke` as `refs.flags`;
- five controls declared themselves active and never ran: the ownership hooks
  read the sub-agent identity in the wrong payload slot (every sub-agent write
  was allowed); the spawn hooks matched `Task` only, never `Agent`; the facades
  carried `model_tier` but no `model:` (all 22 agents inherited the parent
  model); `MaxCostPerRun` had no writer feeding the cumulative cost; the
  `build_loop` bounds lived only in the prompt they were meant to bound;
- hook commands were relative to the current directory: one `cd` disarmed all
  fourteen. They are anchored on `$CLAUDE_PROJECT_DIR`.

What it delivered:

- **a Python runtime skeleton** (`.sdda/templates/runtime/python/app/`, 18
  files): entry point, tiered LLM client, bounds in code, OTel-GenAI tracing,
  orchestration loop, console surface, and the two eval executors G5/G6/G8
  needed. `python .sdda/sdda.py gen-app-skeleton --write` materialises it; the
  generated console starts, resolves its tiers, returns one exit code per error
  class, and emits a trace the framework reader parses;
- evaluation items measured in parallel (`EvalMaxParallel`, order preserved);
  context packs sliced to the *active* stack sheets — the most saturated agent
  went from 93 % to 61 % of its budget with no ceiling raised;
- the four-entry workspace (`feats/ · stack/ · src/ · proof/ · .sys/`) and a
  migration that moves existing content.

Honest maturity after this lot: the deterministic layer is beta; the generation
layer is a skeleton plus six agent prompts that **have still never been run end
to end**. Nothing above changes the language table: Python is the only runtime
with a skeleton, .NET has no retrieval chain, TypeScript and Java have no sheet.

Remaining: the six generator agents and their stacks (Lot 4), then review
(Lot 5). Order and reasons: [ROADMAP.md](.sdda/docs/ROADMAP.md).

**No stack combination is announced as validated**, because none has yet been
measured by a real run. C1 is the MVP target, in `design-phase`. Announcing
anything else would be precisely the false green this framework exists to
prevent.
