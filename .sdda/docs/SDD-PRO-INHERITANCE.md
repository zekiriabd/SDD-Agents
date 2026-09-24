# SDD_Pro inheritance — what we keep, what we refuse, what we add

Analysis of the SDD-Pro repository (v7.0.3-dev, 1,387 files, 29 agents,
41 commands, 36 stacks, 31 invariants, ~2,542 Python tests).

---

## 1. What SDD_Pro does, in substance

SDD_Pro forces the opposite trajectory from an ordinary coding assistant. An
assistant starts from the code and works back up to the intent; SDD_Pro starts
from the intent and **locks every step down behind a deterministic gate**:

```
FEAT (spec métier versionnée)
  └─ User Stories (IDs stables, AC traçables)
       └─ Plans techniques (fichiers, couches, contrats preserves/adds)
            └─ Code (backend d'abord → API Gate → frontend)
                 └─ QA + 5 reviewers (code, sécurité, spec, archi, adversarial)
```

The mechanisms that make it hold, rather than being a chain of prompts:

| # | Mechanism | What it concretely prevents |
|---|---|---|
| 1 | **Source-first** — every decision is a versioned `.md`, nothing lives in the context | The spec drifts inside the prompt and is never re-read |
| 2 | **Declarative `stack.md`**, 55 keys, read by every agent on every invocation | The technical choice is renegotiated by each agent |
| 3 | **Stack catalogue**: `.md` (layer mapping, idioms, smoke) + `.libs.json` (pinned versions, CVEs, LTS) | The agent invents a library or a version |
| 4 | **Ownership matrix** — 1 file = 1 owner, or a serialised write mode | Parallel agents silently overwrite each other |
| 5 | **`loader.yml`** — `reads` / `writes` / `forbidden_reads` + token budget + cache layer per agent | An agent reads what is none of its business and blows its budget |
| 6 | **`[CLASS]` taxonomy** — 193 classes, every `ERROR` carries one | Hooks have to interpret free text |
| 7 | **`INVARIANTS.yml`** — every load-bearing contract points to its **enforcer on disk**; a test fails if the enforcer disappears | Doc-theater: a written rule that nothing enforces any more |
| 8 | **Deterministic first** — 80 zero-token scripts | Paying an LLM to count lines |
| 9 | **Tier abstraction** — agents declare `fast/balanced/deep`, the provider resolves | Changing provider = touching 29 agents |
| 10 | **Cost cap** — `MaxCostPerRun` $50, hard stop | The bill is discovered afterwards |
| 11 | **API Gate** — back↔front contract validated in memory before the front | The front calls an endpoint that does not exist |
| 12 | **Two-stage reviewers** — spec-compliance alone, then code/security/architecture in parallel, then adversarial | Aggregating quality findings on code that does not meet the spec |
| 13 | **Multi-harness compilation** — `.sdd/` → `.claude/`, `.codex/`, `.gemini/` | The framework is a prisoner of one tool |
| 14 | **FEAT hash in the US** (`Parent FEAT hash: sha256:…`) | The FEAT moves under the USs and nothing says so |
| 15 | **Facts ≠ hypotheses** — a script writes the facts, an agent writes its hypotheses in a separate file; the two never mix | An LLM hypothesis becomes an acceptance criterion |
| 17 | **Deterministic tier router** — 70-80% of analysed objects at 0 tokens | Paying Opus for a CRUD accessor |
| 18 | **Per-object cache + idempotence** | An interrupted run costs the full price again |
| 19 | **Context packs sliced by role**, which **declare what they removed** | The agent invents what it did not see instead of lowering its confidence |

---

## 2. Taken over in full (same mechanism, wider scope)

1, 2, 4, 5, 6, 7, 8, 9, 10, 13, 18 — taken over **as is**. They are framework
engineering mechanisms, indifferent to the nature of the generated product.

Two are only partly taken over, and that has to be said:

- **15 — facts ≠ hypotheses** is a rule (ARCHITECTURE §5), not yet a mechanism:
  the script that would merge an agent's hypotheses into a separate branch is
  not written 🟡. What holds today is that facts come from scripts
  (`corpus-profile`, `estimate-budget`, `run-retrieval-eval`…) and that gates
  read only their reports.
- **19 — context packs**: per-agent taxonomy slices exist (`.sdda/digests/`),
  and `context-pack` assembles the context by cache layer under a budget capped
  per tier. **Role-based trimming** is not implemented, and the pack manifest
  says so — an oversized pack refuses to leave, which is better than a pack
  whose real content nobody knows.

Three deserve a note on how they were widened:

- **`stack.md` → `STACK.md`**: 55 keys become ~90
  (`templates/project-config.schema.json`), organised in agentic blocks
  (framework, orchestration, RAG, retrieval, data access, memory, tools,
  guardrails, observability, eval, serving). The mechanism is identical: a
  declarative file, read by everyone, that makes the technical architecture
  editable without touching the code. Two differences: `workspace/stack/STACK.md`
  is **versioned**, because it carries only variable **names**
  (`${LLM_API_KEY}`), never a value — the values live in `workspace/assets/.env`,
  gitignored and read by no agent; and every value is validated against the
  schema (`[CONFIG_VALUE_INVALID]`, blocking at smoke-check and at preflight).

- **Stack catalogue**: the `{stack}.md` + `{stack}.libs.json` pair is taken over
  unchanged. The `.md` carries the layer mapping, the idioms and the smoke
  command; the `.libs.json` carries the pinned versions. The categories change
  (language, shell architecture, HTTP backend, agentic framework, orchestration
  pattern, RAG pattern, vector store, embedding, reranking, data access, memory,
  tools, guardrails, observability, eval, serving), and every card declares its
  `Languages:`: a card for a runtime other than the active language is refused
  (`[STACK_LANGUAGE_MISMATCH]`).

- **Ownership matrix**: taken over, with **one new, non-negotiable rule** —
  `dev-agent` has no write access to `workspace/pipeline/datasets/` or
  `workspace/src/{App}/prompts/`. The agent that writes the code can neither
  modify the set that judges it nor rewrite the prompt it is supposed to
  implement. It is the agentic counterpart of `[QA_OWNERSHIP_VIOLATION]`, and it
  matters more here: without this barrier, self-confirmation is not a risk, it
  is the default outcome.

---

## 3. Transposed (same idea, different form)

| SDD_Pro | SDD_Agents | Why the transposition |
|---|---|---|
| **FEAT** | **MISSION** | Adds `budget`, `ground_truth`, `trust_boundaries`, `failure_policy` — four fields with no classical equivalent, each mandatory |
| **User Story** | **CAPABILITY** | An "observable and testable" AC becomes "metric + threshold + dataset + k runs". An LLM output is always observable and never deterministic |
| **Technical plans** | **CONTRACTS** (agent / tool / retrieval / memory) **+ Agentic IR** | The plan becomes a machine structure, validatable and diffable, consumed by N framework generators |
| **API Gate** (back↔front) | **TOOL GATE + RETRIEVAL GATE + AGENT GATE + ORCH GATE** | One seam in SDD_Pro; four here, because each layer fails by disguising itself as the layer above |
| **`Parent FEAT hash`** | **Eval pinning** `(prompt, model, index, tool_schema, dataset)` | Editing a prompt is the only behaviour change no compiler catches |
| **QA (tests)** | **TESTS + EVALS** (L0→L9) | `assertEquals` only applies to the deterministic half of the system |
| **5 reviewers** | **6 reviewers**: `review-spec`, `review-safety`, `review-cost`, `review-orchestration`, `review-rag`, `review-adversarial` | `security-reviewer` becomes `review-safety` (injection, tool scopes, exfiltration); `review-cost` is new and turns red on `[BUDGET_EXCEEDED_MEASURED]` |
| **Waves + topological sort** over the SQL call graph | **Waves** over the agent graph (`/sdda-build`, ≤ `MaxParallel` instances) | Same property: leaf agents are built before their supervisor |
| **Tier router** on the complexity of a SQL body | **Per-agent tier**, bounded by `agent-bounds.yaml` (default / floor / ceiling) and resolved by `spawn-brief` | The principle is taken over, the routing not yet: choosing the tier from a CAP's complexity 🟡 is not written |
| **`MaxCostPerRun`** (build) | **+ `CostPerRunHardCapUsd`** (product execution) | SDD_Pro caps what the build costs. In agentic systems, what the **product** costs is a functional requirement |

---

## 4. What we refuse to take over

**The front/back vocabulary.** `dev-frontend` and `UI mockups` do not
transpose: an agentic system has no interface to mock up, and its critical seam
is not between two application layers.

> **Revision of 21/09/2026 — the API Gate is taken over.** The original
> position went further: it also listed `dev-backend` and the `API Gate` among
> the refusals, and called the exposure surface "a detail of `## Active Serving
> Surface`, not a structuring axis". That was wrong on two counts, and one of
> them was expensive.
>
> First, **an enterprise agentic system is almost always delivered as a
> back-end**. Refusing it as an axis left the question "so what do we ship?"
> unanswered in the configuration — hence settled by an agent, hence
> differently on every run. That is what `DeliverableType` (`## Project
> Config`) fixed: it says what gets **installed**, where `## Active Serving
> Surface` says how you **get in**.
>
> Second, **the API Gate does have an equivalent, and it was missing**. In
> SDD_Pro it validates the back↔front contract before generating the front.
> Here the seam is between the **IR** and the outside world: the published
> OpenAPI must be derived from the IR's `inputSchema` / `outputSchema` and
> checked against them (`[API_CONTRACT_DRIFT]`, `[API_ROUTE_UNBACKED]`,
> `[API_STATUS_UNMAPPED]` — `stacks/serving/fastapi-sse.md §6`). The four gates
> in the row above cover the internal layers; none of them looked at what the
> system exposes.
>
> Finally, **`dev-backend` is back**, in a narrower form: the seventh `dev-*`
> builds the application's **shell** — project, composition, configuration by
> variable names, Domain, packaging — and nothing of the engine. The
> `stacks/archi/` cards (`mvc`, `ddd`, `microservice`) and `stacks/backend/` are
> taken over from SDD_Pro for it.
>
> What remains refused from the original line: `dev-frontend`, mockups, and the
> idea that an exposure surface dictates the pipeline. The pipeline stays
> bottom-up by layer; the surface is its last layer, not its first.

**The backend-first flow.** Replaced by bottom-up by layer (P5). The order is
not "data first" but "whatever touches the real world first, and proves itself
before anything leans on it".

**Binary pass/fail.** Replaced by green/yellow/red with variance. A score above
the threshold with a 12% standard deviation is not the same information as a
score above the threshold with 1%. Flattening both into "green" is the most
common way to ship an agent that breaks in production.

**Volume before validation.** SDD_Pro carries 36 stacks, only 8 of them 🟡, and
says so honestly. SDD_Agents aims for **one** combination validated end to end
(C1, see [ROADMAP.md](ROADMAP.md), MVP) before announcing others, and says so
just as honestly: the framework is in `design-phase`, no combination is
validated yet, and each component's level is read from
`registry/compatibility.matrix.json` (`componentLevels`). A combination outside
the matrix is refused at preflight (`[STACK_COMBO_UNLISTED]`, unless
`StackComboCheck: warn|off`). Announcing twelve supported frameworks at launch
would produce exactly the "false green" this framework exists to prevent.

---

## 5. What is entirely new

With no precedent in SDD_Pro:

1. **Agentic IR** — a machine intermediate layer between spec and code, which
   makes multi-framework generation deterministic
   ([AGENTIC-IR.md](AGENTIC-IR.md)).
2. **Execution budget as a functional requirement** — estimated at G2, measured
   at G6, blocking at both (P6).
3. **Accounting for non-determinism** — k runs, variance, three-colour verdict
   (P3).
4. **LLM judge calibration** — a grader not validated against human labels does
   not return a blocking verdict (P9).
5. **Golden / holdout disjointness checked by hash** — you do not optimise
   against the set that returns the verdict.
6. **Mandatory injection suites** — direct and indirect, as soon as an
   untrusted source exists (P8).
7. **Side-effect classes on tools** + a required safety strategy.
8. **Mandatory loop bounds** on every generated agent (P12).
9. **Architecture declared by the architect** — the roster
   (`feats/{n}-roster.md`) and the root pattern are human decisions; the
   framework requires them to be complete (`[ARCH_SPEC_INCOMPLETE]`, blocking)
   and flags, without a veto, an agent justified outside the closed list of
   reasons (`[TOPOLOGY_SIMPLICITY_ADVISORY]`) (P7).
10. **Span traces as a first-class artefact** — there is no stack trace in a
    non-deterministic system.
11. **State machine derived from the gates** — state is computed from the
    reports on disk, never declared by an agent ([LIFECYCLE.md](LIFECYCLE.md),
    R1).
