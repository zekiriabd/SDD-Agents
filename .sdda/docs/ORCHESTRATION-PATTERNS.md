# Orchestration patterns — catalogue and selection matrix

Read by the architect who declares the roster, and by `architect-topology`,
which materialises it. Machine SSoT: `.sdda/registry/patterns.registry.json`.

> **The default is `single-agent`.** The root pattern is the **architect's**
> choice, declared in `STACK.md` (`## Active Orchestration Pattern`), and the
> roster that embodies it is declared in `workspace/feats/{n}-roster.md` (P7):
> the framework chooses neither the pattern nor the number of agents. It
> requires the declaration to be **complete** — each pattern imposes its fields
> (`registry/architecture-requirements.yml`), and a missing field is
> `[ARCH_SPEC_INCOMPLETE]`, blocking at G2 (`validate-architecture`). It
> **flags**, without a veto, a choice heavier than necessary: a missing or
> unargued "Alternative plus simple considérée" section in
> `pipeline/topology/{n}-topology.md` is `[TOPOLOGY_SIMPLICITY_ADVISORY]`. This
> document gives the criteria that make that argument verifiable rather than
> aesthetic.

**What loads today.** Only `single-agent`, `router` and `sequential` have a card
under `.sdda/stacks/orchestration/`; they are the only ones the generated
runtime can carry. The other patterns in this catalogue are **documented
intentions**: activated in `STACK.md`, they load nothing and are refused at
preflight (`[STACK_COMBO_UNLOADABLE]`). A pattern nested inside the topology
(§3) does not need a root card, but it must be materialised by the IR graph.

---

## 1. The catalogue

### `single-agent` — one agent, some tools
One model, one loop, N tools. The agent decides what to call.

- **When**: ≤ ~8 tools, a single domain, a single output posture.
- **Cost**: 1 × (turns). The cheapest, the fastest, the most debuggable.
- **Degrades when**: beyond ~10-15 tools, tool selection gets noisy; beyond ~2
  contradictory personas in one prompt, the instructions cancel each other out.
- **Signal that you should escalate**: the tool-call confusion matrix shows
  systematic errors between neighbouring tools — **measured**, not suspected.

### `router` — classify, then delegate
A cheap classification node routes to a specialist.

- **When**: disjoint intents handled very differently.
- **Cost**: 1 `fast` classification + 1 specialist. Often **cheaper** than
  single-agent, because each specialist has a short prompt.
- **Dominant failure mode**: the **silent, unrecoverable misroute**. Once the
  request has gone to the wrong specialist, nothing brings it back.
- **Obligations**: a confidence threshold + a fallback path (`fallback` or
  `clarify`) — without a fallback, `[ROUTER_NO_FALLBACK]`; a routing golden set
  with **per-class accuracy**, not global — a global accuracy of 0.95 can hide
  0.40 on the critical class. `trajectory-report` flags a fallback that is never
  taken (`[ROUTER_FALLBACK_UNTESTED]`).

### `sequential` — pipeline of ordered steps *(SeqAgent)*
Fixed, known steps, each refining the previous one.

- **When**: the order is a property of the business (extract → normalise →
  validate → draft).
- **Cost**: the sum of the steps. Predictable, linear.
- **Dominant failure mode**: **errors compound**. An extraction at 0.9 followed
  by a normalisation at 0.9 gives 0.81, and nobody saw it go by.
- **Obligations**: per-step validation (schema or grader), and one eval per step
  on top of the end-to-end eval. Otherwise you debug a final result without
  knowing which step damaged it.

### `parallel` — fan-out / gather
N independent sub-tasks launched together, then merged.

- **When**: latency is constrained and the sub-tasks are genuinely independent.
- **Cost**: N × tokens, 1 × latency. You buy time with money.
- **Dominant failure mode**: **the merge step**, systematically underestimated.
  Reconciling contradictory outputs is the real work.
- **Obligations**: the merge strategy is specified (vote, priority, LLM
  synthesis, fail on divergence) and evaluated **separately**.

### `supervisor` — hierarchical, dynamic delegation
A supervisor decomposes, delegates to specialists, collects, and decides whether
to continue or conclude.

- **When**: heterogeneous tasks, decomposition not known in advance.
- **Cost**: supervisor overhead **on every hop**, and the context grows with
  every return. It is the most expensive pattern and the one that drifts
  fastest.
- **Dominant failure mode**: supervisor ↔ specialist **ping-pong**, which burns
  the budget without making progress.
- **Obligations**: a **hard** `maxHops`; handoffs carry an explicit contract
  (what state passes, what return condition); a trajectory eval checks the
  distribution of the number of hops, not just the final answer.

### `graph` — explicit state machine
Nodes, conditional edges, bounded cycles, persisted state, resumability, human
interruption.

- **When**: you need controlled cycles, human-in-the-loop, resumption after an
  interruption, or durability.
- **Cost**: explicit and computable — that is its main advantage.
- **Dominant failure mode**: **design complexity**. A 15-node graph that nobody
  ever drew is unmanageable.
- **Obligations**: the graph is drawn (the ```mermaid block of
  `{n}-topology.md` §4) and validated deterministically on the IR —
  reachability, termination, bounded cycles.

### `plan-execute` — explicit plan, then execution
One `deep` call produces a plan; `fast` calls execute it step by step.

- **When**: long horizon, many steps, the plan has value for the human
  (auditability).
- **Cost**: 1 expensive + N cheap. Often the best quality/price ratio on long
  tasks.
- **Dominant failure mode**: the **stale plan** — reality diverges and the
  executor follows it anyway.
- **Obligations**: a specified **replanning** condition (step failure, a
  discovery that contradicts a premise), and a cap on replannings.

### `reflection` — writer / critic
One agent produces, a critic scores it against a rubric, and you iterate.

- **When**: quality comes first, and there is an **explicit rubric** — not an
  "it's better".
- **Cost**: × 2 to × 4.
- **Dominant failure mode**: **endless polishing**, or worse, drift where each
  iteration makes things worse.
- **Obligations**: `max_reflections` **and** a stopping criterion on the
  improvement *delta* (stop if the gain < threshold). The critic cannot be the
  same agent as the writer (P7, reason 4) — `[REFLECTION_SELF_GRADING]`,
  including when the reflection is nested inside a single agent node.

### `blackboard` — shared state
Several agents contribute to a common artefact.

- **When**: collaborative elaboration of a document or a plan.
- **Cost**: grows with the size of the state — every agent re-reads everything.
- **Dominant failure mode**: **write conflicts** and overwrites.
- **Obligations**: an ownership matrix over the sections of the state — the
  mechanism is exactly SDD_Pro's ownership matrix, applied at runtime.

### `network` — free peer-to-peer handoff
Any agent can hand over to any agent.

- **Status: refused by default.** Unbounded cost, unpredictable trajectories,
  near-impossible evaluation. `Root Pattern: network` is
  `[TOPOLOGY_PATTERN_REFUSED]`; an accepted ADR is required to enable it
  (`registry/adr-requirements.yml`), and it must show that no `graph` fits.

---

## 2. Selection matrix

| Criterion | single | router | sequential | parallel | supervisor | graph | plan-exec | reflection |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Disjoint intents | ○ | **●** | ○ | ○ | ◐ | ◐ | ○ | ○ |
| Order imposed by the business | ○ | ○ | **●** | ○ | ○ | ● | ◐ | ○ |
| Decomposition unknown in advance | ○ | ○ | ○ | ○ | **●** | ◐ | ● | ○ |
| Constrained latency | ● | ● | ◐ | **●** | ○ | ◐ | ◐ | ○ |
| Tight budget | **●** | ● | ◐ | ○ | ○ | ◐ | ● | ○ |
| Cycles required | ○ | ○ | ○ | ○ | ◐ | **●** | ◐ | ● |
| Human-in-the-loop | ○ | ○ | ◐ | ○ | ◐ | **●** | ● | ○ |
| Resumption after interruption | ○ | ○ | ◐ | ○ | ○ | **●** | ● | ○ |
| Quality > cost, explicit rubric | ○ | ○ | ○ | ○ | ◐ | ● | ◐ | **●** |
| Auditability of reasoning | ◐ | ● | ● | ◐ | ◐ | ● | **●** | ● |
| Ease of evaluation | **●** | ● | ● | ◐ | ○ | ◐ | ● | ◐ |

● suited · ◐ possible · ○ unsuited

The matrix says what a pattern **can** do, not what loads: a column without a
stack card (everything except `single`, `router`, `sequential`) stays refused at
preflight until its card is written.

---

## 3. Composition

A pattern can nest another: a node of a `graph` can be a `reflection`, a branch
of a `router` can be a `sequential`. The **root pattern** is declared in
`STACK.md`; nestings live in `pipeline/topology/{n}-topology.md` (§8) and show
up in the IR (`nestedPattern`).

**Hard limit**: nesting depth ≤ 2. Beyond that, nobody can reason about cost or
trajectories any more — and an architecture nobody can reason about cannot be
evaluated. This limit is a design rule: no script measures it on the IR yet, so
holding it is the topology review's job.

---

## 4. Anti-patterns flagged or refused

Since P7, the TOPOLOGY GATE no longer judges the **size** of a declared
architecture — that is the architect's decision, and they are entitled to make
it knowingly. It still refuses whatever makes the architecture **unevaluable**
or **unbounded**.

| Anti-pattern | Why it appears | Class | Effect |
|---|---|---|---|
| **One agent per tool** | Confusing "separation of concerns" with topology. A tool is a tool | `[TOPOLOGY_SIMPLICITY_ADVISORY]` | advisory (`validate-topology`) |
| **Supervisor with a single specialist** | Leftover of an abandoned design. A hop paid for nothing | `[TOPOLOGY_REDUNDANT_HOP]` | advisory, **measured** on trajectories (`trajectory-report`) |
| **Loop without a hop cap** | "The agent will stop when it's done" | `[UNBOUNDED_LOOP]` | blocking at G2 (`validate-topology`, `validate-ir`) |
| **Router without a fallback** | The "no class matches" case was never thought through | `[ROUTER_NO_FALLBACK]` | blocking at G2 |
| **Critic = writer** | Apparent saving. The model validates its own output | `[REFLECTION_SELF_GRADING]` | blocking at G2 (`validate-ir`) |
| **Handoff without a state contract** | Assuming "the context follows" | `[HANDOFF_UNCONTRACTED]` | blocking at G2 (`validate-topology`) |
| **Agent without a CAP** | Added "for structure" | `[AGENT_SERVES_NO_CAP]` | blocking: `ir-compiler` refuses to compile |
| **Agent with tools none of its CAPs requires** | Copy of a global tool list | `[TOOL_SCOPE_EXCESS]` | blocking at G2 on the declaration (`validate-ir`), replayed at G7 on the live system (`audit-tool-scope`) |
