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

> **Status: design phase.** `frameworkStatus: design-phase` in
> [`registry/compatibility.matrix.json`](.sdda/registry/compatibility.matrix.json):
> every gate, script and agent sheet exists, and **no stack combination has
> been validated end to end yet**, C1 included. See [Status](#status).

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
| **Python** | ✅ | LangChain · LangGraph | ✅ hybrid · pgvector · rerank | cli · fastapi-sse · batch | usable — the only runtime with a skeleton generator |
| **.NET** | ✅ | Microsoft Agent Framework | ❌ **no sheet** | cli-dotnet · aspnet-minimal | **no RAG** |
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

### Which harness runs the build

| Harness | Status | Blocking gates at runtime |
|---|---|---|
| **Claude Code** | **supported** — the reference harness | yes — hooks in `.claude/settings.json` refuse the tool call |
| **Codex CLI** | **experimental** — compiled to `.codex/`, never validated by a conformance run | **no** — deferred to CI and the deterministic scripts |
| **Gemini CLI** | **experimental** — compiled to `.gemini/`, same reserve | **no** — same |

Under Codex or Gemini CLI nothing stops an out-of-ownership write or an agent
wired before its TOOL GATE at the moment it happens; CI catches it later. The
spawn wrapper those harnesses would need is planned, not written. The root
`AGENTS.md` and `GEMINI.md` are generated pointers to the facades — they are
what Codex and Gemini CLI actually read. Build models come from the harness
(`capability-matrix.yml`, `tier_models`), never from `STACK.md`. Details:
[MULTI-HARNESS.md](.sdda/docs/MULTI-HARNESS.md).

---

## Quickstart

> **Design phase.** `frameworkStatus: design-phase` in
> [`registry/compatibility.matrix.json`](.sdda/registry/compatibility.matrix.json):
> **no stack combination has been validated end to end**, C1 included. The steps
> below run the pipeline; they do not promise a green verdict. Harness: Claude
> Code (Codex and Gemini CLI are experimental, see above).

1. **Clone** — Python 3.11+ only, nothing to install:
   `git clone https://github.com/zekiriabd/SDD-Agents.git && cd SDD-Agents`
2. **Bootstrap** — `python bootstrap.py` (interactive), or
   `python bootstrap.py --combo c1 --app-name SupportDesk --auto`. It writes
   `workspace/stack/STACK.md`, `workspace/assets/.env` and the workspace tree,
   then runs a smoke check. No LLM call.
3. **Fill `workspace/stack/STACK.md`** — above all `## Project Config`:
   `CostPerRunTargetUsd` and `LatencyP95TargetMs` have no framework default and
   the MISSION GATE requires them. Names of variables only, never a secret value.
   Values are validated against the schema at `smoke-check` and at preflight.
4. **Secrets** — put the values in `workspace/assets/.env` (`LLM_API_KEY`,
   `DB_*` if a database). Nothing else to run: the step that creates the
   application (`dev-backend`, PHASE 3.0) copies the file into it. No agent ever
   reads either file.
5. **Your inputs** — the brief as `workspace/feats/1-{Name}.md` (Markdown only),
   your data under `workspace/assets/`, your ground truth (annotated scenarios,
   labels) under `workspace/seed/`.
6. **Open Claude Code at the repository root**, then elicit MISSION 1 from the
   brief: `/sdda-mission {Name} --from-brief workspace/feats/1-{Name}.md`. Every
   `<à préciser>` left open blocks G0 — answer it, or edit the MISSION.
7. **Run the pipeline** — `/sdda-full 1`. It stops cleanly on any decision that
   belongs to you, first of all the roster: `/sdda-roster 1` writes a pre-filled
   `workspace/feats/1-roster.md`, you complete it, then `/sdda-full 1 --resume`.
8. **Read the state** — `/sdda-status 1` (add `--gates` for the check-by-check
   detail). The state is derived from gate reports, never declared.

`/sdda-help` says what to do next from the derived state.

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
and your ground truth under `seed/`. The step that creates the application
copies `assets/.env` into `src/{App}/.env` without an LLM: the generated application
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
             └─ AGENT GATE       every CAP AC evaluated on its agent alone, calibrated judges, pinned prompts
                  └─ ORCHESTRATION
                       └─ ORCH GATE   trajectories, hop bounds, measured cost/latency, API and framework drift
                            └─ SAFETY GATE   injection, adversarial set played live, tool scope, secrets, PII
                                 └─ ACCEPTANCE GATE   quantified goal on holdout
```

**Why**: without these gates you end up debugging a "bad orchestration" that is
really a bad retriever, or an "agent that hallucinates" that is really a tool
whose schema lies. Each layer proves it works before the next one leans on it.

Upstream, the TOPOLOGY GATE (G2) settles what must be settled before a line of
code: the IR, the estimated budget, the packaging, the completeness of the
declared roster, and — through `registry/adr-requirements.yml` — every decision
that contradicts a safe default and therefore needs an accepted ADR
(`[ADR_MISSING]`). The nine gates and their parts:
[ARCHITECTURE.md §4](.sdda/ARCHITECTURE.md).

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

The file is governed, not just read. Every value is validated against
`templates/project-config.schema.json` (`[CONFIG_VALUE_INVALID]`,
`[CONFIG_KEY_CONFLICT]`, `[CONFIG_KEY_MISPLACED]`), every template key names the
script or agent that reads it, and keys nobody read have been removed. A value
the parsers accept but nothing implements is refused
(`[STACK_VALUE_UNIMPLEMENTED]`). The active combination is looked up by its
signature in `registry/compatibility.matrix.json`; an unlisted one is governed by
`StackComboCheck: strict|warn|off` (`[STACK_COMBO_UNLISTED]`).

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

## Security model

Two systems need protecting, and the framework treats them separately: the
**build** (Developer Agents writing into your repository) and the **product**
(the agentic application they generate).

**During the build** — enforced at runtime under Claude Code, deferred to CI
elsewhere:

- **Ownership.** Every write is checked against `loader.yml` by segment-aware
  globs; each `dev-agent` instance is bound to its own `agents/{agent}/`
  directory; every write wave is bracketed by an `audit-ownership` snapshot that
  can revoke what a phase wrote outside its zone. Gate reports cannot be written
  by any agent, through the editor or the shell.
- **Secrets.** No agent reads `assets/.env` or `src/{App}/.env`, whatever
  spelling opens it (`[SECRET_READ_FORBIDDEN]`), and `.claude/settings.json`
  adds native read `deny` rules. `STACK.md` carries variable names, never values
  (`[STACK_SECRET_IN_CLEAR]`).
- **Shell.** The shell hook resolves what a Bash or PowerShell command writes —
  `bash -c`, `eval`, `$(…)`, `-EncodedCommand`, heredocs, Windows paths — and
  refuses what it cannot name without running it (`[OWNERSHIP_SHELL_OPAQUE]`).
- **Hooks that fail.** With `SDDA_HOOKS_STRICT=1` (CI), a crashing hook refuses
  instead of allowing (`[HOOK_FAILED]`), and `hooks-selfcheck` actually executes
  every wired hook: a hook that never starts would otherwise read as green.

**In the product** — measured by the gates, not promised:

- **Guardrails in code.** The Python runtime skeleton ships injection detection,
  PII redaction and output schema validation
  (`.sdda/templates/runtime/python/app/guardrails/`), wired where text enters
  and leaves, and active according to `## Active Guardrails`.
- **Attacked before release.** `qa-evals` starts from a hand-written adversarial
  seed set (`.sdda/templates/datasets/adversarial-seed.jsonl`); G7 plays the
  versioned set live against the delivered surface, and every successful attack
  found in review becomes a permanent item. A missing mandatory reviewer report
  is `[SAFETY_REVIEW_REPORT_MISSING]`, never "zero findings".
- **Judges that cannot grade themselves.** A judge model that the product also
  runs is refused at preflight (`[JUDGE_SAME_AS_EVALUATED]`); an uncalibrated
  judge is advisory only, and a red calibration blocks G5.
- **Unsafe defaults require a decision.** Disabling TLS verification, giving an
  agent write access to a database, keeping raw PII in traces or exposing a
  network surface without caller identity each require an accepted ADR — G2
  stays red without it.

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
| [DATA-SOURCES.md](.sdda/docs/DATA-SOURCES.md) | Data sources outside a database — registry, connectors, secrets |
| [MULTI-HARNESS.md](.sdda/docs/MULTI-HARNESS.md) | Compilation to Claude Code / Codex / Gemini CLI |
| [TESTING-AND-EVAL.md](.sdda/docs/TESTING-AND-EVAL.md) | The L0→L9 pyramid |
| [INVARIANTS.yml](.sdda/INVARIANTS.yml) | The <!--sdda:count invariants-->21<!--/sdda:count--> load-bearing contracts + their enforcer |
| [ROADMAP.md](.sdda/docs/ROADMAP.md) | Build order + the MVP |
| [PLANNED-SCRIPTS.md](.sdda/docs/PLANNED-SCRIPTS.md) | The deterministic backlog, generated — who asks for what |
| [python/README.md](.sdda/python/README.md) | The deterministic Python layer |
| [CHANGELOG.md](CHANGELOG.md) | What changed, release by release |

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
class reciprocity, error classes cited in prose that nothing emits, up-to-date
digests and counters, internal references, CLI options cited by prompts,
English/French twin parity (`docs.parity`), and **catalogue honesty** — no stack
sheet can declare itself validated unless a run has measured it.

```bash
python .sdda/sdda.py sync-error-registry --check   # taxonomy
python .sdda/sdda.py sync-digests --check          # per-agent digests
python .sdda/sdda.py sync-counters --check         # figures quoted in the prose
python .sdda/sdda.py harness-build --check         # facades vs source
python .sdda/sdda.py hooks-selfcheck               # every wired hook, executed
python -m pytest .sdda/python/tests/ -q                         # deterministic layer
```

---

## Status

**Design phase.** <!--sdda:count agents-->23<!--/sdda:count--> Developer Agents,
<!--sdda:count commands-->11<!--/sdda:count--> commands,
<!--sdda:count invariants-->21<!--/sdda:count--> invariants,
<!--sdda:count stacks-->45<!--/sdda:count--> stack sheets,
<!--sdda:count classes-->440<!--/sdda:count--> error classes,
<!--sdda:count hooks-->15<!--/sdda:count--> hooks and
<!--sdda:count subcommands-->76<!--/sdda:count--> deterministic subcommands exist on disk and
are tested (<!--sdda:count tests-->1505<!--/sdda:count--> test functions). No script
cited by a prompt is missing ([PLANNED-SCRIPTS.md](.sdda/docs/PLANNED-SCRIPTS.md)
is empty). What does **not** exist yet is the proof: no pipeline has run end to
end on a real product, so no combination is validated. That is Lot 6 of the
[ROADMAP](.sdda/docs/ROADMAP.md), and nothing below replaces it.

### The last lot — closing what was announced but not held

- **Workflow.** Source-tool contracts are generated in PHASE 2 before the IR,
  their code in PHASE 3; the full `validate-topology` pass runs before
  `ir-compiler` and alone writes G2's `topology` part; a new step 4.0 prepass
  lays down `shared/` types and the memory interface, frozen during phases 4 and
  5; `--resume` follows the run lineage and `BuildLoopMaxCostUsd` bounds one
  item's loop; G1 no longer goes stale when PHASE 2 fills `## Allocated To`.
- **Gates.** G2 gains an `adr` part (`registry/adr-requirements.yml`, covered
  only by an accepted ADR naming key and value); G5 blocks on a red judge
  calibration and pins every prompt hash (`[PROMPT_MISSING]`,
  `[PROMPT_HASH_MISMATCH]`); G6 gains a `framework` part (`[FRAMEWORK_DRIFT]`);
  G7 plays the adversarial set live and treats a missing reviewer report as an
  error.
- **Ownership and hooks.** Globs match by segment, real zone overlaps are
  detected and resolved, `dev-agent` instances are bound to their directory,
  phases are audited against a snapshot and can be revoked; the shell hook was
  rewritten and covers PowerShell; strict mode and `hooks-selfcheck`.
- **STACK.md governance.** Values validated against the schema, dead keys
  removed (build tiers now come from `capability-matrix.yml`), combos looked up
  in the matrix, unimplemented values refused, judge ≠ evaluated model.
- **Runtime.** A real stdlib LLM judge (Anthropic, OpenAI, Gemini, Ollama),
  pricing read from the provider sheets, guardrails in code, a 31-item
  adversarial seed set, traces written under an exclusive lock.
- **Industrialisation.** MIT `LICENSE`, `CHANGELOG.md`, tag-triggered release
  workflow, Dependabot, SHA-pinned actions, `lint` job (ruff + mypy), coverage
  floor, root `AGENTS.md` / `GEMINI.md`; ten new scripts (`corpus-profile`,
  `chunking-bench`, `validate-envelope`, `cost-report`, `trajectory-report`,
  `adversarial-target-check`, `promote-adversarial-findings`, `validate-adr`,
  `validate-framework`, `hooks-selfcheck`); every document now has an English
  reference and a French twin.

Details: [CHANGELOG.md](CHANGELOG.md).

### How it got here

**Lots 1 and 2 — the deterministic base and the evaluation engine:**

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

The gate scripts call no LLM themselves: they receive an injected executor or
replay recorded runs, and only the `llm-judge` grader calls a model. This is
deliberate — a gate that judged with an unpinned model would not be
deterministic.

**Lot 3 — the junction between agent sheets and the pipeline:**

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

**Lot 4 — the generation gates.** `validate_tool_contract.py` writes the
`contracts` part of the TOOL GATE (G3): meta-schema of tool schemas,
`required` ⊆ `properties`, safety strategy consistency, contract ↔ IR ↔ **code**
confrontation, DB envelope. `check_ir_freshness.py` refuses to generate from an
IR that no longer describes its sources. Two fundamental defects were found
while wiring them:

- `compute_status` looked for `G3-{tool}`, `G4-{retriever}`, `G5-{cap}`, while
  `eval_runner` wrote everything under `{mission}`. Reports were green in files
  the state machine never opened: the `Tested` state was **unreachable**.
  Granularity now follows the LIFECYCLE;
- `compiledFrom` did not hash the contracts. Editing a tool schema left the IR
  declaring itself fresh while describing something else — exactly the case
  `/sdda-topology --recompile-only` exists to handle. `contractHashes` entered
  the IR and its schema.

**The API GATE exists.** `validate_api_contract.py` writes the `api` part of G6:
published schemas confronted with the IR's `inputSchema` / `outputSchema` in
both directions, routes confronted with the active surface's contract, HTTP
statuses confronted with the `[CLASS]` → code table. As long as no
`openapi.json` is published, the part is **not applicable**: it writes no
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
`STACK.md ## Active Retrieval Stack`: two truths about the same fact, that
nothing confronted. The IR now carries the **intent** (`pattern`, `topK`,
`citationMode`, `identityFilter`, `gateThresholds`, `envelope`, `exposedTo`) and
the **`binding`** (`store`, `embeddingModel`, `chunk`, `hybridWeights`,
`rerank`, `strategy`), held by three mechanisms:

- `[INFRA_LEAK_IN_INTENT]` — any component name outside `binding` is refused;
- `[RETRIEVAL_BINDING_MISMATCH]` — the `binding` is **reconciled** with the
  active stacks at compile time, on the *family* (`voyage` covers
  `voyage-3-large`); the version itself stays pinned by `indexHash`;
- a test that simulates the **second generator**: it derives a complete call
  plan without ever reading `binding`, and that plan is identical for
  `pgvector` and for any other store.

**Audit lot — the pipeline could not complete once, and five controls were
inert.** `ir_compiler` demanded a holdout that only exists three phases later;
no L9 suite was ever compiled; nine CLI options were cited by prompts and
existed in no script (now caught by `refs.flags`); the ownership hooks read the
sub-agent identity in the wrong payload slot, the spawn hooks matched `Task`
only, the facades carried no `model:`, `MaxCostPerRun` had no writer, and hook
commands were relative to the current directory. All fixed; the lot also
delivered the Python runtime skeleton (`gen-app-skeleton --write`), parallel
eval items, context packs sliced to the active sheets, and the workspace
migration.

**Honest maturity.** The deterministic layer is beta. The generation layer —
eight `dev-*` agents, the Python skeleton — and the review layer — six reviewers
— are written and wired to their gates, and **have never been run end to end**.
Python is the only runtime with a skeleton generator, .NET has no retrieval
chain, TypeScript and Kotlin have sheets but no generator, Java has nothing.

**No stack combination is announced as validated**, because none has yet been
measured by a real run. C1 is the MVP target, in `design-phase`. Announcing
anything else would be precisely the false green this framework exists to
prevent.

---

## License

MIT — see [LICENSE](LICENSE). The same terms cover the framework sources under
`.sdda/` and the `sdda` Python package built from `.sdda/python/`.
