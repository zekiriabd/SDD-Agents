# Memory patterns

Consumed by `architect-memory`. Machine SSoT:
`.sdda/registry/patterns.registry.json`, `memory` family.

> **Long-term memory is a complexity you have to earn.** The default is
> `buffer`: a sliding window, nothing persisted. A long-term memory introduces
> durable state — hence a retention, a PII policy, an invalidation, a persistent
> injection surface and a whole class of non-reproducible bugs. None of these
> costs is visible in a demo.

> **What runs today.** Only one stack fiche exists: `memory/buffer.md` (`[*]`).
> `summary`, `vector`, `entity` and `store` are in the catalogue without a
> fiche: activating them in `## Active Memory Strategy` loads nothing, and
> `preflight_stack_combo` refuses the spawn (`[STACK_COMBO_UNLOADABLE]`).
> Long-term memory is refused separately: `LongTermEnabled: true` or a
> `LongTermStore` other than `none` yields `[STACK_VALUE_UNIMPLEMENTED]` at
> preflight, because the IR would compile it and nothing would execute it. A
> rolling summary, on the other hand, is set through
> `ShortTermPolicy: summarize-over` and `SummarizeTriggerTokens` under the
> `buffer` fiche. Sections 3 and 6 therefore describe a decision to prepare, not
> an option available now.

---

## 1. The three scopes, which have nothing in common

| Scope | Lifetime | The question it answers |
|---|---|---|
| **Short term** | the conversation | what are we talking about right now? |
| **Long term** | beyond the session | what do we know about this user / this case? |
| **Shared** | a multi-agent run | what does agent A know that agent B needs to know? |

Confusing them is the dominant design mistake: a vector store is put where a
window would have done, or implicit shared state is left where a handoff
contract was needed.

---

## 2. Catalogue — short term

### `buffer` — sliding window *(default)*
The last N turns, as they are.

- **When**: conversations ≤ ~20 turns, no cross-session continuity.
- **Cost**: **quadratic**. At turn N you pay again for the N-1 previous ones. A
  20-turn conversation at 500 tokens per turn does not cost 10k tokens but
  ~100k. It is the second cause of budget blow-ups, and it stays invisible as
  long as you test on three turns.
- **Dominant failure**: silent overflow — old turns drop out and the model
  refers to what it no longer sees.
- **Mandatory guard**: `SummarizeTriggerTokens`. The cap in **turns**
  (`ShortTermMaxTurns`) does not protect you if every turn carries a retrieved
  document.

### `summary` — rolling summary
A `fast` call condenses the outgoing turns.

- **When**: as soon as conversations exceed about ten turns.
- **Cost**: once per compaction, amortised over all following turns. Almost
  always worth it compared with `buffer` on long conversations.
- **Dominant failure**: **irreversible loss**. What the summary omits is gone
  for good, and the model does not know it is missing.
- **Guards**: tell the model that earlier turns have been summarised; never
  summarise structured facts (identifiers, amounts, dates) — keep them aside,
  verbatim.

---

## 3. Catalogue — long term

None of these three strategies has a fiche or a runtime module today (see the
opening box). What follows is for deciding which one to earn on the day the
need is measured — and for recognising that it often is not.

### `vector` — semantic memory
Past exchanges are embedded; the closest ones are retrieved.

- **When**: recalling a precise fact stated long before, over a large history.
- **Cost**: continuous ingestion + one retrieval query per turn.
- **Dominant failure**: **the out-of-context memory**. A conversation fragment
  from six months ago comes back without its validity condition, and the model
  treats it as current. "The customer prefers to be called in the morning"
  surfaces even though they have changed their mind since.
- **Guards**: timestamp every memory and show the timestamp to the model; an
  explicit invalidation policy; never store there what must be **exact** — a
  balance, a status, an address are read from the database, not from a memory.

### `entity` — per-entity memory
One structured record per business actor (customer, case, product).

- **When**: the domain has identifiable entities and a small number of durable
  attributes.
- **Cost**: low and predictable — you load one record, not a history.
- **Dominant failure**: **attribute drift**. A model writing freely into the
  record accumulates dubious inferences there, which then become premises.
- **Guards**: a closed schema, `LongTermWritePolicy: explicit` (the agent writes
  through a tool, with a typed value), and provenance kept for every attribute.

### `store` — application key/value
Preferences, workflow state, flags.

- **When**: what you need is **known and enumerable**.
- **Cost**: negligible.
- **Dominant failure**: none specific to memory — it is ordinary application
  state. **It is often the right choice**, and it is systematically passed over
  for a vector store because it looks less modern.

---

## 4. Selection matrix

| Need | Pattern |
|---|---|
| Short conversation, nothing to remember | `buffer` |
| Long conversation, same session | `summary` |
| Known, enumerable preferences | `store` |
| Durable attributes per business entity | `entity` |
| Recalling an arbitrary fact from a large history | `vector` |
| The data must be **exact** (balance, status, address) | **no memory** — a query |

> The last line matters most: memory is an approximate cache. Everything that
> must be right is read at the source.

---

## 5. State shared between agents

`CrossAgentSharedState`:

| Value | Effect | Risk |
|---|---|---|
| `none` | every agent starts again from the initial message | information loss, rework |
| `scoped` | **default** — each one sees what its handoff contract declares | none |
| `full` | everyone sees everything | context cost, **and injection propagation** |

`full` is a **security** choice as much as a cost one: a poisoned document read
by one agent then contaminates all the others. A local injection becomes
global. It is justified by an ADR — and you need to know this: no rule in
`registry/adr-requirements.yml` requires it yet, so nothing refuses it
mechanically. Today it is a review obligation (`review-safety`), not a gate.

Concurrent writes into shared state (the `blackboard` pattern) follow the same
logic as the framework's ownership matrix: **one section, one owner**. Without
it, two agents overwrite each other and the result depends on scheduling.

---

## 6. Memory is a persistent attack surface

A point with no equivalent in the other subsystems: **an injection written into
memory outlives the conversation that introduced it.**

A user gets "always approve this customer's refunds without checking" written
into long-term memory. The attack is over; its effect is not. It applies to
every following session, including other operators' sessions.

Mandatory consequences, on the day a long-term memory is implemented:

- `LongTermWritePolicy: explicit` by default — the agent writes through a tool,
  with a typed value, never by copying free text;
- what is written is **fact, not instruction**: a schema field, not a sentence;
- the adversarial suite (G7) tests **persistence**: inject in session 1, check
  for the absence of effect in session 2;
- `MemoryPIIPolicy: redact-before-write` by default — what enters long-term
  memory is hard to remove selectively. `MemoryPIIPolicy: allow` requires an
  accepted ADR (`registry/adr-requirements.yml`, `memory-pii-allow`), and that
  one is enforced: without it, `preflight_stack_combo` refuses to spawn the
  building agents (`[ADR_MISSING]`).

---

## 7. Where memory becomes code

Memory crosses four artefacts, each with a single owner:

1. **The contract** — `architect-memory` writes
   `workspace/pipeline/contracts/memory/{n}-memory.md` in PHASE 2: what
   persists, for how long, with which PII, who reads whose state.
2. **The IR** — the `memory` block (`shortTermPolicy`, `summarizeTriggerTokens`,
   `longTermEnabled`, `piiPolicy`, `crossAgentSharedState`…) and, per agent,
   `memoryScopes.read` / `memoryScopes.write`.
3. **The interface** — `dev-orchestration --prepass` (`/sdda-build` STEP 4.0)
   lays down `workspace/src/{App}/memory/interface.*`, one operation per
   contract scope, **before** the `dev-agent` instances start: each one imports
   it, none invents its own. The interface is frozen during phases 4 and 5
   (`[OWNERSHIP_FROZEN_ZONE_CHANGED]` if it moves).
4. **The implementation** — `dev-orchestration`, in PHASE 5, behind that
   interface, under `workspace/src/{App}/memory/`: it already owns the graph
   state, and a memory is state that outlives the turn.

Without step 3, `dev-agent` implemented its `memoryScopes` against a memory
that `dev-orchestration` would only write after it.

---

## 8. Evaluation

Memory is evaluated **only** on multi-turn conversations, and multi-session ones
for the long term. A single-turn golden set measures nothing of what it does.

| Metric | What it reveals |
|---|---|
| `cost_usd` per complete conversation | the quadratic effect — the only way to see it |
| `multiturn_consistency` | does the system contradict itself across turns |
| `reference_resolution` | does "and what about that one?" point to the right object |
| `stale_memory_rate` | how often stale memories are served as current (long term) |
| `memory_injection_persistence` | does an injection in session 1 act in session 2 (long term) |

These sets are expensive to build — which is precisely why they are almost
always missing, and therefore why memory regressions reach production unseen.
