# SDD_Agents — Architecture

Reference document: tree, layers, pipeline, gates, harness/provider
abstraction. It follows from [PHILOSOPHY.md](PHILOSOPHY.md).

> French twin: [ARCHITECTURE.fr.md](ARCHITECTURE.fr.md). It carries the same
> technical content, and **it** is what `harness_build.py` compiles into the
> harness memory file (`.claude/CLAUDE.md`, `.codex/AGENTS.md`,
> `.gemini/GEMINI.md`): the Developer Agents read French prompts, and serving
> them the architecture in another language would give one rule two
> vocabularies. If the twin is missing, the build compiles this page and says
> so.

---

## 1. The three layers

The same in spirit as SDD_Pro, adapted to agentic systems.

| Layer | Location | Nature | Read by |
|---|---|---|---|
| **Framework** | `.sdda/` | Neutral source: agents, commands, rules, stacks, templates, invariants | Compiled into the harness facades |
| **Harness facades** | `.claude/`, `.codex/`, `.gemini/` | Generated from `.sdda/` by `harness_build.py` | The active harness |
| **Workspace** | `workspace/` | The user's project: specifications, contracts, prompts, datasets, generated code | The agents, at runtime |

> `.sdda/` (not `.sdd/`): a deliberately short name — it is referenced hundreds
> of times across <!--sdda:count agents-->23<!--/sdda:count--> agent prompts; two characters fewer are tokens
> saved on every invocation. Distinct from `.sdd/` so that SDD_Pro and
> SDD_Agents can be vendored into the same repository.

---

## 2. Tree

```
SDD-Agents/
├── README.md                          # English by default; twin README.fr.md
├── README.fr.md                       #   (convention: .sdda/docs/README.md)
├── CHANGELOG.md                       # Keep a Changelog; a release = a v* tag
├── LICENSE                            # MIT — covers .sdda/ and the sdda package
├── AGENTS.md · GEMINI.md              # GENERATED: pointers to .codex/ and .gemini/,
│                                      #   which Codex CLI and Gemini CLI read at the root
├── bootstrap.py                       # interactive: STACK.md + workspace + smoke
├── plugin.json                        # 🟡 planned — marketplace discovery (as in SDD_Pro)
├── .github/
│   ├── workflows/ci.yml               # smoke, generators' --check, pytest Linux+Windows,
│   │                                  #   lint (ruff + mypy), coverage with a floor
│   ├── workflows/release.yml          # v* tag -> wheel + sdist; actions pinned by SHA
│   └── dependabot.yml
│
├── .sdda/                             # ── FRAMEWORK (neutral source) ─────────
│   ├── PHILOSOPHY.md · PHILOSOPHY.fr.md
│   ├── ARCHITECTURE.md · ARCHITECTURE.fr.md   # the .fr.md is the memory files' source
│   ├── INVARIANTS.yml                 # load-bearing contracts + on-disk enforcer (count: sync_counters)
│   ├── config.base.yml                # layer 1/3 of the Project Config
│   ├── loader.yml                     # reads/writes/forbidden_reads + budget + cache per agent
│   ├── agent-bounds.yaml              # tier_default / floor / ceiling per agent
│   ├── capability-matrix.yml          # harnesses x mechanisms; tier -> BUILD model
│   ├── agents/                        # <!--sdda:count agents-->23<!--/sdda:count--> Developer Agents (see docs/AGENT-ROSTER.md)
│   ├── commands/                      # <!--sdda:count commands-->11<!--/sdda:count--> slash commands
│   ├── rules/                         # operational rules
│   │   ├── ownership.md               # write matrix (inherited from SDD_Pro)
│   │   ├── output-protocol.md
│   │   ├── error-classification.md    # agentic [CLASS] taxonomy
│   │   ├── eval-protocol.md           # k runs, variance, calibration, baselines
│   │   ├── prompt-authoring.md        # how a contract becomes a prompt
│   │   ├── agent-safety.md            # injection, scopes, side effects
│   │   └── budget-and-loop.md         # bounds, cost, escalation
│   ├── skills/                        # 🟡 planned — auto-triggered skills of the
│   │                                  #   Developer Agents. Not to be confused with
│   │                                  #   the skills of the PRODUCT's agents, which
│   │                                  #   live in §5 of the contracts and in the
│   │                                  #   prompts (see §7, rules/ownership.md §2.2)
│   ├── providers/                     # anthropic · openai · google · azure-openai · local-ollama
│   │                                  #   rates, URLs, key variable — read by pricing and the judge
│   ├── stacks/                        # ── THE CATALOGUE — <!--sdda:count stacks-->45<!--/sdda:count--> sheets on disk ─
│   │   │                    # Every sheet declares `Languages:` (one language,
│   │   │                    # several, or `*` when it assumes none).
│   │   │                    # This is the SSoT of the coupling: preflight_stack_combo
│   │   │                    # refuses a sheet for another runtime than the
│   │   │                    # active language -> [STACK_LANGUAGE_MISMATCH].
│   │   ├── lang/            python.md · csharp.md · typescript.md · kotlin.md
│   │   │                    # typescript and kotlin: sheets present, no bootstrap
│   │   │                    # combo (eval/ and observability/ are [python])
│   │   ├── archi/           mvc.md · ddd.md · microservice.md     [*]
│   │   │                    # inherited from SDD_Pro: the architecture of the SHELL
│   │   │                    # (entry, composition, config, Domain) — the engine
│   │   │                    # keeps its split by ownership
│   │   ├── backend/         python-fastapi.md · node-express.md · nestjs.md
│   │   │                    kotlin-spring-boot.md · dotnet-minimalapi.md
│   │   │                    # inherited from SDD_Pro: the HTTP house around the
│   │   │                    # surface, active only for backend-api; python/csharp
│   │   │                    # pins in serving/*.libs.json
│   │   ├── framework/       langchain.md · langgraph.md · ms-agent-framework.md
│   │   │                    langgraph-js.md [typescript] · spring-ai.md [kotlin]
│   │   │                      (+ .libs.json each)
│   │   ├── orchestration/   single-agent.md · router.md · sequential.md
│   │   ├── rag/             none.md · hybrid.md            [python except none]
│   │   ├── vectorstore/     pgvector.md (+ .libs.json)     [python]
│   │   ├── embedding/       voyage.md · bge-local.md
│   │   ├── rerank/          none.md · cohere-rerank.md
│   │   │                    bge-reranker-local.md (+ .libs.json)
│   │   ├── dataaccess/      view-per-agent.md · declared-sources.md · none.md
│   │   ├── memory/          buffer.md
│   │   ├── tools/           mcp.md
│   │   ├── eval/            pytest-eval.md (+ .libs.json)
│   │   ├── observability/   otel-genai.md (+ .libs.json)
│   │   ├── guardrails/      schema-validation.md · pii-redaction.md
│   │   │                    injection-detection.md
│   │   └── serving/         cli.md · cli-dotnet.md · cli-node.md · cli-kotlin.md
│   │                        fastapi-sse.md · aspnet-minimal.md · batch.md
│   │                        # surface = WHERE YOU ENTER; the DELIVERABLE
│   │                        # (DeliverableType) lives in ## Project Config
│   ├── registry/                      # ── MACHINE REGISTRIES ────────────────
│   │   ├── patterns.registry.json     # every pattern: id, family, criteria, cost, risks
│   │   ├── compatibility.matrix.json  # lang x framework x pattern x provider x store; combos
│   │   ├── architecture-requirements.yml  # P7: what each stack choice imposes
│   │   ├── adr-requirements.yml       # decisions that require an accepted ADR (G2 `adr` part)
│   │   └── ir.schema.json             # schema of the Agentic IR (see docs/AGENTIC-IR.md)
│   ├── templates/
│   │   ├── STACK.md.template
│   │   ├── mission.template.md
│   │   ├── capability.template.md
│   │   ├── topology.template.md
│   │   ├── agent-contract.template.md
│   │   ├── tool-contract.template.md
│   │   ├── retrieval-contract.template.md
│   │   ├── memory-contract.template.md
│   │   ├── eval-suite.template.md
│   │   ├── roster.template.md         # roster declared by the architect (P7) — Markdown, yaml block
│   │   ├── golden-set.schema.json
│   │   ├── tool-schema.schema.json
│   │   ├── project-config.schema.json # validates STACK.md VALUES (x-stackSections, x-readBy)
│   │   ├── libs-catalog.schema.json   # schema of the stacks/ .libs.json files
│   │   ├── prompt.template.md
│   │   ├── adr.template.md
│   │   ├── datasets/
│   │   │   └── adversarial-seed.jsonl # hand-written seed set that qa-evals copies and extends
│   │   └── runtime/python/            # skeleton of the generated application (gen-app-skeleton)
│   │       ├── app/                   #   entry, config, bounds, traces, orchestration, serving…
│   │       │   └── guardrails/        #   injection, PII, output schema — IN CODE, per
│   │       │                          #   ## Active Guardrails
│   │       └── data/ · tools/         #   runtime of the declared sources
│   │       # (stack combinations live in registry/compatibility.matrix.json)
│   ├── digests/                       # per-agent slices of the taxonomy
│   ├── sdda.py                        # launcher: `python .sdda/sdda.py {cmd}` —
│   │                                  #   works from a bare clone, no pip install
│   └── python/                        # deterministic 0-token tooling
│       ├── sdda_cli.py                # dispatcher of the <!--sdda:count subcommands-->76<!--/sdda:count--> subcommands; registry
│       │                              #   DERIVED from disk, also read by the scanners
│       ├── sdda_lib/                  # config, markdown_io, hashing, pricing, traces,
│       │                              #   graders (including judge_clients: the real LLM judge)
│       ├── sdda_scripts/              # validate_*, estimate_budget, eval_runner, audit_ownership, …
│       ├── sdda_admin/                # harness_build, framework_smoke, sync_*, planned_scripts,
│       │                              #   command_flags, hooks_selfcheck
│       ├── sdda_hooks/                # <!--sdda:count hooks-->15<!--/sdda:count--> blocking PreToolUse / SubagentStop hooks
│       │                              #   each declares its WIRING; harness_build
│       │                              #   wires them ALL, no hardcoded table
│       └── tests/
│
└── workspace/                         # ── THE PROJECT — the human provides, the framework produces (§2.ter)
    │
    │   ═══ WHAT THE HUMAN PROVIDES ════════════════════════════════════════
    ├── stack/
    │   ├── STACK.md                   # VERSIONED — technical choices, variable NAMES
    │   │                              #   (${LLM_API_KEY}), never a value. Inline sources, APIs, MCP.
    │   └── mcp.json                   # optional — standard MCP config imported as is
    ├── feats/                         # their specifications — MARKDOWN, flat
    │   ├── {n}-{Name}.md              #   the brief (--from-brief)
    │   └── {n}-roster.md              #   the ROSTER: how many agents, which ones, who carries what (P7)
    ├── assets/                        # the data (root of `kind: local` stores)
    │   └── .env                       #   the VALUES of the runtime secrets — gitignored, read by NO agent
    ├── seed/                          # the ground truth: annotated scenarios, labels
    │
    │   ═══ WHAT THE FRAMEWORK PRODUCES ════════════════════════════════════
    ├── pipeline/                      # everything the pipeline generates before and around the code
    │   ├── missions/    {n}-{Name}.md          # po-elicitor
    │   ├── caps/        {n}-{m}-{Name}.md      # po-capabilities
    │   ├── topology/    {n}-topology.md        # architect-topology, Mermaid graph included
    │   ├── contracts/   agents/ · tools/ · retrieval/ · memory/   # the architects
    │   ├── decisions/   ADR-{ts}-{slug}.md     # ONE place (see §2.ter)
    │   ├── datasets/    golden/ · holdout/ · calibration/ · adversarial/   ┐ what JUDGES:
    │   ├── suites/      the evaluation suites                              │ qa-evals and the
    │   ├── baselines/   the non-regression reference                       │ scripts, NEVER
    │   ├── calibration/ κ of every LLM judge                               │ a `dev-*`
    │   └── fixtures/    tools/ · frozen retrieval — the L4 isolation doubles ┘
    │
    ├── src/
    │   └── {AppName}/                       # the generated agentic application — FLAT layout (SDD_Pro):
    │       │                                #   this directory IS the package, one level
    │       ├── pyproject.toml · README.md   # the project (dev-backend)
    │       ├── .env                         # copied from assets/.env when the project is created, no LLM
    │       ├── app/                         # composition, config, Domain (dev-backend)
    │       ├── shared/                      # shared types, laid down by the prepass (dev-orchestration)
    │       ├── agents/{agent}/              # one product agent (dev-agent, one instance per agent)
    │       ├── prompts/{agent}.system.md    # the hashed executable of each agent (dev-prompt)
    │       ├── skills/ · rules/             # what the agent CAN DO / MUST DO, one fragment per slug (dev-prompt)
    │       ├── tools/ · data/ · retrieval/  # the foundation (dev-tools, dev-data, dev-retrieval)
    │       ├── memory/                      # interface (prepass) then implementation (dev-orchestration)
    │       ├── orchestration/ · serving/    # graph and surface (dev-orchestration, dev-api)
    │       ├── data/schemas/                # frozen source schemas — a RUNTIME asset
    │       └── **/tests/                    # L0→L2 (qa-tests), next to what they test
    │
    └── .sys/                          # ── INTERNAL STATE AND RUN OUTPUT ───────
        ├── .ir/         {n}-system.ir.json  # Agentic IR compiled from the contracts
        ├── .context/ · .state/ · .validation/ · .audit/
        ├── reports/     {n}-{run-id}.json   # eval reports; runs/: recorded executions
        ├── traces/runs/ {run-id}.jsonl
        └── workspace.json                   # workspaceVersion — written by bootstrap,
                                             #   upgraded by sdda_scripts/migrate_workspace.py
```

### 2.ter What the human provides, what the framework produces

Up to v5, `feats/` put the brief and roster the human writes next to the
MISSION, CAPs and contracts the agents generate; `proof/` put the human's
ground truth next to the `qa-evals` sets. Opening either one, the human could
not tell what was theirs to fill and what to leave to the framework. v6 says it
with the tree.

| Entry | Nature | Written by | Regenerable |
|---|---|---|---|
| `stack/` | the technical choices — `STACK.md`, alone, versioned | **the human** | no |
| `feats/` | their specifications — brief and roster, **Markdown only**, flat | **the human** | no |
| `assets/` | the data (root of `kind: local` stores, SDD_Pro's `assets/`) and `.env` | **the human** | no |
| `seed/` | the ground truth — annotated scenarios, labels | **the human** | no |
| `pipeline/` | everything the pipeline generates: MISSION, CAPs, topology, contracts, ADRs, sets, suites, baselines, calibration | the `po-*`, `architect-*` and `qa-evals` agents, the scripts — **never a `dev-*`** under datasets/suites/baselines/calibration | partly |
| `src/` | the generated application, prompts and frozen schemas included | the eight `dev-*`, `qa-tests`, the generators | yes |
| `.sys/` | internal state and run output | the scripts | yes |

**The user's input fits in four places**, deliberately: `STACK.md` (the
technical choices — language, framework, pattern, data sources, API URLs, MCP
servers), their Markdown files under `feats/` (what the system must do, then
the roster that says how), their data and `.env` under `assets/`, and their
ground truth under `seed/`. The `.env` carries the key of the *Runtime Models*
(§6): the generated application reads it, the build harness never does — it
pays for its tokens with its own account. It is dropped in `assets/` and
**copied** to `src/{AppName}/.env` by the step that creates the project —
`gen-app-skeleton --write`, run by `dev-backend` in PHASE 3.0 — without an LLM,
because `src/{AppName}/` is where the application leaves from as an executable
or a container. The human has no command to run; the `install-env` command
remains to re-copy a key changed after the build. No agent reads either file:
`preflight_forbidden_reads` and `preflight_bash_ownership` refuse
`[SECRET_READ_FORBIDDEN]`, `architect-data` included, although it walks
`assets/` to infer schemas — whatever spelling opens the file (`.ENV`, `.env.`,
the `.env::$DATA` stream, a Grep filtered on `.env`), and `.claude/settings.json`
backs these hooks with native read `deny` rules. Three rules hold the input,
checked by `smoke-check` rather than told: `feats/` contains Markdown only
(`[FEATS_NOT_MARKDOWN]`), `stack/` contains `STACK.md` only
(`[STACK_DIR_UNEXPECTED_FILE]`), no secret value enters STACK.md
(`[STACK_SECRET_IN_CLEAR]`). A workspace from an earlier version is carried
forward by `python .sdda/sdda.py migrate-workspace`, which puts every file in
its place and rewrites the references that cited it.

**The only boundary with no exception is the judgement boundary.** The agent
that writes the code can touch neither the set that grades it nor the
reference its regression is measured against: `pipeline/datasets/`, `suites/`,
`baselines/`, `calibration/` and `fixtures/` are write-forbidden to every `dev-*`. Merging
the old `feats/` and the old `proof/` under `pipeline/` does not loosen it: it
holds by the zones of the ownership matrix, not by the name of the parent
directory. Putting these sets under `src/`, on the other hand, would drop them
into the developer agents' write zone.

Two consequences can be read directly in the tree:

- **Prompts live INSIDE the application**, `src/{App}/prompts/`, because a
  system prompt is a runtime asset. Stored alongside the specs, it does not ship
  with the code; stored under `src/` but next to the application, it does not
  either — the executable or container built from `src/{App}/` looked for its
  prompts in a directory left behind in the repository. Same logic for
  `skills/` (what the agent can do, one fragment per skill), `rules/` (what it
  must or must never do) and `memory/` (the implementation of the memory
  contract): an agentic application reads in its own tree — agents, prompts,
  skills, rules, tools, memory, orchestration — not in the framework that
  produced it.
- **ADRs have ONE location**, `pipeline/decisions/`. They used to have two —
  `docs/adr/`, cited by the template, and `.sys/.context/adrs/`, declared by the
  ownership matrix — and the human-tasks script looked in both. The question
  "has this ADR been written?" therefore had two possible answers, and the one
  nobody reread was the one that governed.

A workspace from an earlier version is carried forward by
`python .sdda/sdda.py migrate-workspace`, which **moves** the content instead of
creating the new tree next to the old one.

**This tree describes the disk, not the intent.** 🟡 marks the only accepted
gap: announced, not written yet — `plugin.json` and `.sdda/skills/`, nothing
else. The rule matters most for `stacks/` —
<!--sdda:count stacks-->45<!--/sdda:count--> sheets exist, while the target
catalogue has three times as many. This is not a gap to close before
announcing: it is the sequence of [docs/ROADMAP.md](docs/ROADMAP.md), which
delivers **one combination validated end to end** (C1) before announcing
twelve. The target catalogue and each component's validation level live in
`registry/compatibility.matrix.json` (`componentLevels`,
`catalogDiscrepancies`) and `registry/patterns.registry.json` — machine
registries, hence checkable, where a prose tree is not.

The operational consequence, which has to be said: **a line activated in
`STACK.md` for a component with no sheet on disk loads nothing.** The announced
catalogue is not a loadable catalogue. A value the parsers accept but nothing
implements — long-term memory, a remote store, an `http-api` or `mcp`
connector, a guardrail with no sheet, human-in-the-loop outside langgraph — is
refused at preflight (`[STACK_VALUE_UNIMPLEMENTED]`) instead of being swallowed.

### 2.ante One entry point for the tooling

The <!--sdda:count subcommands-->76<!--/sdda:count--> deterministic subcommands are called in a single form:

```bash
python .sdda/sdda.py validate-mission --mission 1     # from a bare clone
sdda validate-mission --mission 1                     # after `pip install -e .sdda/python`
```

The first assumes **no installation**, and it is the one the
<!--sdda:count agents-->23<!--/sdda:count--> agent sheets and the <!--sdda:count commands-->11<!--/sdda:count--> commands write. A framework whose prompts
require a prior `pip install` fails on the first clone — and the agent that gets
`command not found` invents the script's output instead of stopping.

The subcommand registry is **derived from disk** (`sdda_cli.discover()`): every
module of `sdda_scripts/`, `sdda_admin/` or `sdda_hooks/` is a subcommand named
after its file (`validate_mission.py` -> `validate-mission`). No table to
maintain, hence no table to let drift — the same reason as for the error
registry (§9).

It is not only a writing convenience. `sdda_cli.resolve()` is the **SSoT the
scanners read**: `planned_scripts.py` and `framework_smoke.py` resolve
`python .sdda/sdda.py {cmd}` to its module so that they keep detecting a script
a prompt announces without anyone having written it. Moving the prose to the
short form without teaching them to read it would have silenced that check —
that is, reopened exactly the door `refs.planned.undeclared` closed. Today the
inventory is empty: no cited script is missing from disk
(`.sdda/docs/PLANNED-SCRIPTS.md`, regenerated by `planned-scripts --write`).

### 2.bis The Agentic IR — the hinge of multi-framework

Between the Markdown contracts (readable, debatable, versioned) and the
generated code (LangGraph, Semantic Kernel, Pydantic-AI…) sits a **machine
intermediate representation**: `workspace/.sys/.ir/{n}-system.ir.json`.

```
Markdown contracts --(ir-compiler, deterministic, 0 token)-->  system.ir.json
                                                                    |
                        +-------------------------+-----------------+
                        v                         v                 v
                 Python generator          C# generator       TS generator
                 (LangGraph)               (Semantic Kernel)  (LangChain.js)
```

Why this is structural, and not one more layer:

- **Multi-framework becomes deterministic.** Without an IR, every generator
  re-interprets the Markdown with an LLM — hence three divergent
  interpretations of the same specification. With an IR, interpretation happens
  **once**, upstream, and the generators consume a closed structure.
- **The IR can be validated without an LLM**: JSON schema, graph reachability,
  bounds present, referenced tools existing, scope consistency. The TOPOLOGY
  GATE runs on the IR, not on prose.
- **The IR is diffable.** An architecture change becomes a structured, readable
  diff, not a diff of paragraphs.
- **The IR carries the estimated budget.** Cost and latency are computed on the
  graph, not on an intention.

The IR does not replace the Markdown contracts: the contracts remain the
authoritative source edited by humans and agents. The IR is their compiled
projection, regenerable, never edited by hand. Details and schema:
[docs/AGENTIC-IR.md](docs/AGENTIC-IR.md).

---

## 3. The forward pipeline

```
 PHASE 0   ELICITATION        po-elicitor            -> pipeline/missions/{n}-{Name}.md
   |                                                         [MISSION GATE]
 PHASE 1   CAPABILITIES       po-capabilities        -> pipeline/caps/{n}-{m}-*.md
   |                                                         [CAP GATE]
   |       ROSTER             roster validate --if-present (script, 0 token, BEFORE the architect)
 PHASE 2   TOPOLOGY           architect-topology (alone)  -> pipeline/topology/{n}-topology.md
   |                          gen-source-tools --scope contracts (script, if declared-sources)
   |                          then architect-tools || architect-rag ||
   |                               architect-data || architect-memory  (parallel)
   |                                                     -> pipeline/contracts/**
   | PHASE 2.9 IR COMPILATION validate-topology (full pass), then
   |                          ir-compiler (script, 0 token) -> .sys/.ir/{n}-system.ir.json
   |                                                         [TOPOLOGY GATE]  (runs on the IR)
 PHASE 6a  DATASETS           qa-evals --datasets-only: golden, calibration — BEFORE the code
   |
 PHASE 3   FOUNDATION         dev-backend (skeleton: project, composition, config, Domain — alone, first)
   |                          then dev-tools || dev-retrieval || dev-data     (parallel)
   |                          gen-source-tools --scope code right before dev-data
   |                                                         [TOOL GATE] [RETRIEVAL GATE]
 PHASE 4   PROMPTS + AGENTS   4.0 dev-orchestration --prepass (shared/ + memory/interface, frozen)
   |                          4.1 dev-prompt (alone) -> 4.2 dev-agent  (parallel, 1 instance per agent)
   |                                                         [AGENT GATE]
 PHASE 5   ORCHESTRATION      dev-orchestration -> dev-api -> dev-backend (packaging)   (sequential)
   |                                                         [ORCH GATE]
 PHASE 6   EVAL + TESTS       qa-evals || qa-tests
   |
 PHASE 7   REVIEW             Stage A: spec-compliance alone
   |                          secret + PII scans, once (0 token)
   |                          Stage B: agent-safety || cost-latency ||
   |                                   orchestration || rag-quality   (parallel)
   |                          Stage C: review-adversarial (live system)
   |                          + the versioned adversarial set, played LIVE
   |                                                         [SAFETY GATE]
 PHASE 8   ACCEPTANCE         measure of the quantified goal on holdout
                              + non-regression vs baseline
                                                             [ACCEPTANCE GATE]
                                                          -> VERDICT green/yellow/red
```

**Pure delegation** (inherited from SDD_Pro): the orchestrating command
`/sdda-full` invokes no agent directly — it chains commands. **No agent spawns
another agent**: orchestration belongs to the command, so the bill stays
predictable.

What the diagram does not say on its own, and was added because its absence
cost something:

- **The roster is checked before the architect.** `architect-topology`
  materialises a DECLARED roster, it does not invent one (P7). Without this
  0-token check, `/sdda-full` paid the most expensive agent of the pipeline to
  fail at post-check — or to fill in, itself, the declaration nobody had made.
- **Declared-source contracts are born in PHASE 2, before the IR**
  (`gen-source-tools --scope contracts`), and their code in PHASE 3 right before
  `dev-data` (`--scope code`). A contract created after the IR would describe a
  tool G2 never saw.
- **`validate-topology` runs its full pass before `ir-compiler`**, and it alone
  writes the `topology` part of G2: an IR is not compiled against a missing
  contract (`[TOPOLOGY_CONTRACT_MISSING]`, `[AGENT_CONTRACT_MISSING]`). The
  `--pre` pass no longer writes a report — it granted a green part without any
  contract having been checked.
- **Sets before code (PHASE 6a).** G4 requires the retrieval golden, G5 the CAP
  goldens and the calibration sets; producing them before the code is also what
  keeps the code from influencing the set that will judge it.
- **Step 4.0 — the prepass.** `dev-orchestration --prepass` lays down the shared
  types (`shared/`) and the memory **interface** before the `dev-agent`
  instances run in parallel. Without it, every instance invented its own handoff
  types and coded against a memory written after it. These zones are then
  **frozen** — `shared/**` and `memory/**` during phase 4, `shared/**` and
  `memory/interface.*` during phase 5 — and the audit checks it on disk
  (`[OWNERSHIP_FROZEN_ZONE_CHANGED]`).
- **Every write wave is bracketed by a snapshot.** `audit-ownership snapshot`
  before phases 3, 4.0, 4 and 5, then `audit-ownership --since-snapshot` after,
  which judges the files the phase REALLY wrote (`--instances` for the
  `dev-agent` instances, `--frozen` for the frozen zones) and can revoke them
  (`--restore`). What the hooks do not see — a script writing from inside — shows
  on disk.
- **Phase 5 is sequential**: orchestration, then surface, then packaging. The
  surface attaches to the graph, packaging to the surface.
- **Resume follows the lineage.** `/sdda-full {n} --resume` opens a run linked to
  the previous one (`resumedFrom`); skip, retry and the attempt counter are read
  over the whole lineage, at item granularity (`--inputs-hash`), and
  `BuildLoopMaxCostUsd` bounds the fix loop of ONE item, resumes included.
  `set-phase --phase acceptance` closes the lineage.

---

## 4. The nine gates

Every gate is **deterministic** (0 token) unless stated otherwise. Every gate
has an on-disk enforcer declared in `INVARIANTS.yml`.

| # | Gate | Checks | Blocking on |
|---|---|---|---|
| G0 | **MISSION** | quantified goal present, budget declared (cost/latency/tokens), ground truth identified, no residual `<à préciser>` | `[MISSION_INCOMPLETE]` |
| G1 | **CAP** | every AC names metric + threshold + dataset; every MISSION element covered by >= 1 CAP | `[AC_NOT_EVALUABLE]`, `[TRACEABILITY_GAP]` |
| G2 | **TOPOLOGY** | pattern justified + simpler alternative explicitly ruled out; estimated budget <= declared budget; every loop bounded; every agent has a contract; graph reachable with no unbounded cycle; **parts** `packaging`, `architecture`, `adr` | `[TOPOLOGY_UNJUSTIFIED]`, `[BUDGET_EXCEEDED_ESTIMATE]`, `[UNBOUNDED_LOOP]`, `[TOPOLOGY_CONTRACT_MISSING]`, `[ADR_MISSING]` |
| G3 | **TOOL** | valid schema; green contract tests (happy + every declared error + timeout + auth failure); live connectivity verified; side-effect class declared; safety strategy present if destructive | `[TOOL_CONTRACT_FAILED]`, `[SIDE_EFFECT_UNDECLARED]` |
| G4 | **RETRIEVAL** | golden set present (>= n queries); recall@k, nDCG, groundedness, resolved-citation rate above thresholds | `[RETRIEVAL_BELOW_THRESHOLD]` |
| G5 | **AGENT** | every agent evaluated **in isolation** (mocked tools, frozen retrieval) against its CAP ACs, over k runs; **parts** `calibration` (a red calibration blocks), `prompts` (pinned hash of every prompt the IR expects), `ownership` | `[AGENT_EVAL_FAILED]`, `[PROMPT_MISSING]`, `[PROMPT_HASH_MISMATCH]` |
| G6 | **ORCH** | end-to-end evals on the golden mission; conforming trajectories; hops <= ceiling; **measured** cost and latency <= declared budget; **`api` part**: the exposed contract is derived from the IR and matches it; **`framework` part**: the code imports the declared framework, where its sheet puts it, and no competitor | `[TRAJECTORY_VIOLATION]`, `[BUDGET_EXCEEDED_MEASURED]`, `[API_CONTRACT_DRIFT]`, `[API_ROUTE_UNBACKED]`, `[FRAMEWORK_DRIFT]` |
| G7 | **SAFETY** | injection suite (direct + indirect); versioned adversarial set played live; tool scope audit; secret scan of prompts/traces/datasets; PII scan of the vector store; every mandatory reviewer report present | `[INJECTION_SUCCEEDED]`, `[TOOL_SCOPE_EXCESS]`, `[SECRET_LEAK]`, `[PII_IN_INDEX]`, `[SAFETY_REVIEW_REPORT_MISSING]` |
| G8 | **ACCEPTANCE** | the MISSION's quantified goal met on the **holdout** (never on the training golden); non-regression vs baseline beyond tolerance | `[GOAL_NOT_MET]`, `[REGRESSION]` |

**Holdout rule**: the tuning datasets (`golden/`) and the verdict datasets
(`holdout/`) are disjoint, and the pipeline checks it by hash. Optimising
prompts against the set that delivers the verdict is the agentic way of lying to
oneself.

**Mandatory parts, contributing parts.** A gate aggregates partial reports
(`sdda_lib/gate_reports.py`). A missing **mandatory** part leaves the gate open;
a missing **contributing** part does not block, but its RED always does. G2
requires `topology`, `ir` and `budget`, and reads `packaging`, `architecture` and
`adr`; G5 reads `calibration`, `ownership` and `prompts`; G6 reads `api` and
`framework`; G7 requires `suites`, `adversarial` and `verdict`, and reads
`secrets`, `pii` and `toolscope`. Contributing rather than mandatory because a
project with no decision that requires an ADR, or a `cli` surface with no HTTP
contract, has nothing to write — requiring it would fail deliverables that do
not have the object. Red, on the other hand, never has an excuse.

**G2's `adr` part — a decision that contradicts a default is made in writing.**
`registry/adr-requirements.yml` declares the decisions that require an ADR (a
database in write mode for an agent, `ApiContractFirst: false`,
`TlsVerify: false`, PII in memory or raw in traces, a network surface without
identity, `StackComboCheck: off`, the `network` pattern); `validate-adr` writes
the part. An ADR covers a requirement only if it is `Status: Accepted` and
**names** it in a `Covers: Key=value` line: an ADR that wrote "false" anywhere
used to cover every boolean decision of the project.

**Judge calibration is a part of G5, not a warning.** The
`G5-{n}.calibration.json` report is filed under the MISSION number, hence read
by the gate: a red calibration blocks G5. An uncalibrated judge turns its grader
`advisory` (informative score, non-blocking), and a CAP whose graders are all
advisory cannot be green. A judge that is one of the models the product runs is
refused at preflight (`[JUDGE_SAME_AS_EVALUATED]`); found at runtime, it yields
an advisory verdict (`[JUDGE_EQUALS_EVALUATED]`). The judge is real:
`graders/judge_clients.py` calls Anthropic, OpenAI, Gemini or Ollama with the
stdlib, URL and key variable read from the provider sheet.

**G7 plays, it does not reread.** `/sdda-review` plays the versioned adversarial
set against the delivered surface (`run-adversarial-suite --executor … --run-id`),
and the script records every execution itself in
`.sys/reports/runs/{n}-adversarial.jsonl`, which `--replay` can re-judge. The
scans (`scan-secrets`, `scan-pii`) run once, before stage B. A missing mandatory
reviewer report is `[SAFETY_REVIEW_REPORT_MISSING]`, not "zero findings": a
reviewer that wrote nothing checked nothing.

**The API Gate is a part of G6, not a tenth gate.** It transposes SDD_Pro's
`API Gate`, which validated the back↔front contract before generating the
front. Here the seam is between the **IR** and the outside world: the published
OpenAPI is **derived** from the IR's `inputSchema` / `outputSchema`, never
written by hand, and a deterministic test (0 token) confronts the two. Placing
it in G6 rather than as a separate gate is deliberate: `dev-api` works in PHASE 5
alongside `dev-orchestration`, and a tenth lock for a single question would
dilute the reading of the other nine. Details:
`stacks/serving/fastapi-sse.md §6`. It can be disabled with
`ApiContractFirst: false`, which requires an ADR. The `framework` part follows
the same logic: an architecture that the sheet read at review no longer
describes is drift, not a detail.

**`STACK.md` is validated before any spend.** Its values are checked against
`templates/project-config.schema.json`, section by section
(`[CONFIG_VALUE_INVALID]`, `[CONFIG_KEY_CONFLICT]`, `[CONFIG_KEY_MISPLACED]`),
blocking at `smoke-check` and at preflight; every template key declares the
script or agent that reads it (`x-readBy`), and the keys nobody read have been
removed. The active combination is recognised by its signature in
`registry/compatibility.matrix.json`; an unlisted combination is governed by
`StackComboCheck: strict|warn|off` (`[STACK_COMBO_UNLISTED]`).

**The deliverable is declared, not inferred.** `DeliverableType`
(`## Project Config`) says what gets **installed** — `cli-exe`, `backend-api`,
`library`, `batch-job`, `container`, `mcp-server` — while
`## Active Serving Surface` says where you **enter**. The two are independent:
the same `RunService` is exposed over HTTP or in batch, and shipped as a
container or an executable. Their consistency (deliverable x language x surface
x caller identity) is checked by `validate_packaging.py`, as the **`packaging`
part of G2**: it is an architecture decision, and it must be settled before a
line of code depends on it.

**The default is `cli-exe`, in all four languages.** An agentic system ships
first as a program you run: one input, one output, one exit code. Nothing to
deploy, nothing to authenticate, and it is the surface the eval runner invokes
at L4-L7 — so what is measured is what is shipped. Each language has its console
sheet (`serving/cli.md` in Python, `serving/cli-dotnet.md` in C#,
`serving/cli-node.md` in TypeScript, `serving/cli-kotlin.md` in Kotlin); a
default a language cannot honour shows at preflight
(`[STACK_LANGUAGE_MISMATCH]`), not silently.

**`backend-api` is not a presentation variant, it is a change of nature.** The
agentic engine stops being a program someone runs and becomes a **service
another application calls**: it sends a request, the engine executes the
MISSION, and returns the answer and the events. You choose it when the caller is
software — a front end, a business back end, a scheduler — never to look
cleaner. It then makes three things mandatory that do not exist in `cli-exe`: an
`ApiFramework` consistent with the language (`fastapi`, `aspnet-minimal`,
`spring-boot`, `express`…), a caller identity established at the transport
(`ApiAuthMode`, without which all filtering at the source can be bypassed), and
a public contract derived from the IR (`ApiContractFirst`) — a caller you do not
control cannot be fixed after the fact. The CLI is still generated: it carries
the smoke check and the evals.

---

## 5. Facts vs hypotheses contract

Inherited from SDD_Pro, generalised to the whole pipeline.

- **FACTS** — produced by deterministic scripts: tool schemas, call graph,
  retrieval metrics, measured costs, corpus sizes, test results. **Can** become
  acceptance criteria.
- **HYPOTHESES** — produced by LLM agents: capability split, topology choice,
  business glossary, risk areas. **Can never** become acceptance criteria
  without human validation or measurement.

The separation is **structural**: the agent writes into a separate file that a
script merges into the `hypotheses` branch only. It cannot overwrite a fact,
even if it tries.

---

## 6. Harness / provider / tier abstraction

SDD_Pro's mechanism taken over in full, with one additional distinction that is
mandatory for agentic systems:

| Notion | Who executes | Declared in |
|---|---|---|
| **Harness** | where the *build* orchestration runs (Claude Code, Codex, Gemini CLI…) | `STACK.md ## Active Harness` |
| **Build models** | which models pay for the *build* tokens (the <!--sdda:count agents-->23<!--/sdda:count--> Developer Agents) | `capability-matrix.yml` > `harnesses.{Harness}.tier_models` |
| **Runtime models** | which models the **generated application** runs | `STACK.md ## Runtime Models` |

The three are independent. Building with Claude Code + Opus an application that
runs on GPT-4-mini is a nominal case, not an exception. Confusing the last two
is the most frequent mistake of competing frameworks: it makes the runtime
budget impossible to compute.

**One harness is supported: Claude Code.** It is the only one where hooks refuse
the tool call at the moment it happens. Codex CLI and Gemini CLI are
**experimental**: their facades compile, no conformance run has validated them,
and they have **no blocking gate at runtime** — what the hooks enforce is
deferred to CI and the deterministic scripts. The root `AGENTS.md` and
`GEMINI.md` files are generated pointers to their facade. Details:
[docs/MULTI-HARNESS.md](docs/MULTI-HARNESS.md).

**Agents declare a tier** (`fast` / `balanced` / `deep`), never a model name.
Two resolutions, two sources:

- **Build** — the harness resolves it: `capability-matrix.yml` >
  `harnesses.{Harness}.tier_models`, which `harness_build` compiles into the
  `model:` of every agent facade. `## Build Models` no longer carries any key:
  `Provider`, `Endpoint`, `TierMap` and `Mode` were read by nobody, and changing
  `TierMap` changed no call. A key you edit to no effect is worse than a missing
  key — it makes you believe in a setting.
- **Application** — `## Runtime Models` (`RuntimeProvider`, `RuntimeTierMap`),
  which `layered_config.read_runtime_tier_map` and the generated skeleton
  consume.

The `.sdda/providers/*.yaml` sheets are the per-provider **catalogue** (model
identifiers, rates, default URL, key variable), and they are read:
`pricing.py` takes its rates from them (the hardcoded table is now only a tested
fallback), the LLM judge takes `default_base_url`, `api_prefix` and `auth_env`
from them, and `gen-app-skeleton` pins the runtime provider's SDK in the
generated project. Adding a provider touches no agent. The `tier_floor` /
`tier_ceiling` bounds in `agent-bounds.yaml` are quality invariants: the Project
Config cannot relax them. Each agent's context budget (`budget_bytes` in
`loader.yml`) is itself capped per tier — 60 % of a 200 k-token window, i.e.
≈ 480 KB — and a budget declared beyond it refuses the spawn.

---

## 7. Ownership and parallelism

SDD_Pro's ownership matrix is taken over as is and extended to agentic
artefacts. Excerpt (the source is `loader.yml`, `writes:` keys):

| Path | Exclusive owner | Mode |
|---|---|---|
| `workspace/pipeline/missions/{n}-*.md` | `po-elicitor` | Create then append-only |
| `workspace/pipeline/caps/{n}-{m}-*.md` | `po-capabilities` | Exclusive create (1 file = 1 CAP); `architect-topology` only fills `## Allocated To` |
| `workspace/pipeline/topology/{n}-*.md` | `architect-topology` | Exclusive create |
| `workspace/pipeline/contracts/tools/{n}-*.tool.md` | `architect-tools` | Exclusive create |
| `workspace/pipeline/contracts/retrieval/{n}-*.retrieval.md` | `architect-rag` | Exclusive create |
| `workspace/src/*/prompts/{agent}.system.md` | `dev-prompt` | Exclusive create + edit |
| `workspace/src/**/agents/{agent}/**` | `dev-agent` (1 instance per agent, bound to its directory) | Exclusive edit-augment |
| `workspace/src/**/tools/**` | `dev-tools` | Exclusive edit-augment |
| `workspace/src/**/retrieval/**` | `dev-retrieval` | Exclusive edit-augment |
| `workspace/src/**/orchestration/**` · `memory/**` · `shared/**` | `dev-orchestration` | Exclusive create + edit; `shared/` and the memory interface in the prepass |
| `workspace/src/**/serving/**` | `dev-api` | Exclusive edit-augment |
| `workspace/src/*/*` · `workspace/src/**/app/**` | `dev-backend` | the shell: project, composition, config, Domain, packaging — nothing of the engine |
| `workspace/src/**/tests/**` | `qa-tests` (zone shared with the `dev-*`, by layer) | Edit-augment |
| `workspace/pipeline/datasets/**` | `qa-evals` | Exclusive create; **never** `dev-*` |
| `workspace/pipeline/baselines/**` | deterministic script only | Atomic write |

> **Critical rule, specific to agentic systems**: `dev-agent` has **no** write
> right on `workspace/pipeline/datasets/` nor on `workspace/src/{App}/prompts/`.
> The agent that writes the code can neither modify the set that judges it nor
> rewrite the prompt it is supposed to implement. Without this separation,
> self-confirmation is guaranteed — it is the agentic counterpart of SDD_Pro's
> `[QA_OWNERSHIP_VIOLATION]`.

**Patterns are read by segment.** `*` covers one path segment, `**` several:
`workspace/src/*/*` is the root of the generated project, not all of `src/`.
Before, a star crossed `/`, and `dev-backend`'s zone silently overlapped every
other `dev-*`. Overlap detection now compares the REAL zones, not the strings:
two patterns that designate the same files are an overlap, which is either
resolved (the outermost layer wins, `architect-tools` forbids itself the `data-`
contracts `architect-data` writes) or **declared** in `shared_writes` with its
mode (`serialized`, `append-only`, `disjoint-by-layer`…) and its reason.

**One instance, one directory.** `dev-agent` runs as N parallel instances, and
its `agents/{agent}/**` zone only makes sense if we know WHICH instance writes.
`/sdda-build` writes `SDDA-INSTANCE: {agent}` into the prompt;
`preflight_instance_bind` records it at spawn, and the instance binding is
reserved at its first write. An instance writing into another one's directory is
refused (`[OWNERSHIP_INSTANCE_ESCAPE]`).

**Gate reports cannot be forged.** Reviewers write exactly the `writes:` that
`loader.yml` gives them under `.sys/.validation`; a GATE `.json` report remains
forbidden to Write/Edit AND to the shell. A verdict an agent can write is not a
verdict.

**The hooks.** <!--sdda:count hooks-->15<!--/sdda:count--> hooks are wired in
`.claude/settings.json`, each declaring its `WIRING`: write
(`Write|Edit|MultiEdit|NotebookEdit`), read (`Read|Glob|Grep`), shell
(`Bash|PowerShell`), spawn (`Task|Agent`) and sub-agent stop. The shell hook
analyses what a command writes — current directory, variables, wildcards,
`bash -c`, `eval`, `$(…)`, `-EncodedCommand`, heredocs, Windows case, `/g/…`
paths — and refuses what it cannot name without executing it
(`[OWNERSHIP_SHELL_OPAQUE]`); PowerShell has its own dialect. A hook's command
is `${SDDA_PYTHON:-python}`, anchored on `$CLAUDE_PROJECT_DIR`. By default, a
hook that crashes lets the action through and says so; with
`SDDA_HOOKS_STRICT=1` (CI), it REFUSES (`[HOOK_FAILED]`) — a hook that does not
start returns a code the harness treats as an authorisation. And
`python .sdda/sdda.py hooks-selfcheck` **executes** every wired hook, with a
harmless payload and a payload to refuse: the only proof a hook holds is to run
it. `preflight_stack_combo` only judges pipeline agents: a sub-agent outside the
pipeline is not blocked by a red `STACK.md`.

**The case of skills — two owners, no single authority.** A product agent's
skill crosses two files that are already owned: `architect-topology`
**declares** it in §5 of the agent contract, `dev-prompt` **implements** it in
`prompts/{agent}.system.md` (`## Compétences`). Neither can write into the
other's file, so neither can resolve on its own a disagreement between
declaration and implementation — `lint_prompts.py` detects it both ways
(`[SKILL_NOT_IMPLEMENTED]`, `[SKILL_UNDECLARED]`). It is the only possible check:
a tool has a schema a gate can execute, a skill has neither schema nor side
effect. A tool is what the agent is **allowed to call**; a skill is what it
**knows how to do**. Details: `rules/ownership.md §2.2`.

---

## 8. Observability: a first-class artefact

Every run — build as well as product execution — emits an **OTel-GenAI** span
trace in `workspace/.sys/traces/runs/{run-id}.jsonl`, one line per span: agent
turn, tool call (redacted args), retrieval query (+ returned documents +
scores), LLM call (model, tokens in/out/cache, cost, latency), gate crossing.
Each span is written **whole, under an exclusive lock**: parallel evals used to
lose lines.

**One format, and it is the span.** Every line carries `run_id`, `trace_id`,
`span_id` and `parent_span_id`: the last one is what gives the format its value.
Delegation depth and the agent responsible for a tool call are **read** from the
tree, where a flat sequence of events forced one to guess "the last agent seen"
— wrong as soon as two agents work in parallel, which is precisely when tool
scope matters.

**Cost is recomputed from tokens**, never reread from the `sdda.cost.usd`
attribute the application declares. A figure reread without being recomputed is
not a measurement, it is a declaration; the gap between the two is reported,
because that kind of gap is what lets a run pass under a ceiling it exceeds.
`cost-report` and `trajectory-report` write the measurements `review-cost` and
`review-orchestration` read.

**The build leaves its own trace**, in the same file and the same format: one
`sdda.build.agent {agent}` span per Developer Agent invocation (billed cost,
latency, `build_loop` turns, context loaded vs `loader.yml`'s `budget_bytes`),
one `sdda.gate {gate}` span per crossing, and a root span written by
`sdda_state end-run`, the only one that knows the start, the end and the run
total. It is the bill the user sees first, and the only one `MaxCostPerRun`
claims to cap.

That build cost is **declared by the harness**, not recomputed: we do not see a
sub-agent's tokens. It therefore stays in a field distinct from the product's.
Adding them up would pass an unverifiable figure off as a measurement — exactly
the confusion §6 already forbids between *build models* and *runtime models*.

Without a trace, a non-deterministic system cannot be debugged: there is no
stack trace to read. Invariant `trace-emitted-per-run`.

Traces feed the validation console (same principle as SDD_Pro's SQLite
console): cost per CAP, score drift over time, trajectory distribution, top
failing tools.

---

## 9. `[CLASS]` error taxonomy

Inherited from SDD_Pro (193 classes): every ERROR block carries a `[CLASS]` code
in its `CAUSE:`, so that hooks, retry loops and dashboards classify without
interpreting text. SDD_Agents carries **<!--sdda:count classes-->440<!--/sdda:count-->**, a closed list regenerated from
the real emitters by `sdda_admin/sync_error_registry.py` — writing the list by
hand would let it drift both ways (`rules/error-classification.md §6`). The
figure above is itself regenerated (`sync-counters`), not copied.
Families specific to SDD_Agents:

`[MISSION_*]` · `[CAP_*]` · `[TOPOLOGY_*]` · `[AGENT_*]` · `[TOOL_*]` ·
`[RETRIEVAL_*]` · `[MEMORY_*]` · `[PROMPT_*]` · `[EVAL_*]` · `[JUDGE_*]` ·
`[BUDGET_*]` · `[SAFETY_*]` · `[TRACE_*]` · `[API_*]`

**A class cited here must have an emitter.** `sync_error_registry.py`
regenerates the registry from the **real** emitters — so a class that lives only
in this document never enters it, and the registry declares itself "up to date"
without it. That is how `[API_CONTRACT_DRIFT]` and `[API_ROUTE_UNBACKED]` could
be announced as blocking in §4 for a whole lot without any script emitting them.
The `errors.documented` check of `framework_smoke.py` closes that door: any
class cited in normative prose (`.sdda/*.md`, `.sdda/docs/*.md`, `.fr.md` twins
included) and emitted by nothing is a **failure**, not a warning.
`docs.parity` adds the twin rule: a page and its `.fr.md` cite the same classes,
carry the same number of headings per level, the same counter markers and the
same commands.

---

## 10. What SDD_Agents will not do

Stated up front, so that the promise stays tenable:

- **No training or fine-tuning.** The framework composes existing models; it
  produces no weights.
- **No guarantee that the generated product is correct.** It guarantees that the
  product has been *measured* against declared thresholds, on declared sets. A
  threshold set too low remains a threshold set too low.
- **No hosting or operations.** It produces code, evals and CI; it does not run
  production.
- **No model choice on your behalf on criteria it does not measure.** Tiers are
  declared, resolution belongs to the provider.
