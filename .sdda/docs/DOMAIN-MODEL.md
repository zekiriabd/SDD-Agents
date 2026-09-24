# SDD_Agents — Domain Model

The framework's closed vocabulary. Every entity handled by an agent, a script,
a template or a gate is one of these. A concept absent from this document does
not exist in SDD_Agents.

---

## 1. Overview

```
MISSION  1 ─── n  CAPABILITY
   │                  │
   │                  ├── n  ACCEPTANCE CRITERION  (métrique + seuil + DATASET)
   │                  └── allouée à ──► AGENT  et/ou  TOOL  et/ou  RETRIEVER
   │
   ├── 1 ─── 1  ROSTER  (déclaré par l'architecte)
   │
   └── 1 ─── 1  TOPOLOGY
                  ├── n  AGENT ─── n  TOOL
                  │        ├── 1  PROMPT
                  │        ├── n  SKILL · n  RULE
                  │        ├── 1  MODEL BINDING (tier)
                  │        ├── 1  BOUNDS
                  │        └── n  MEMORY SCOPE
                  ├── n  RETRIEVER ─── 1  INDEX ─── 1  CORPUS
                  ├── n  DATA ACCESS
                  ├── 1  ORCHESTRATION PATTERN
                  └── n  GUARDRAIL
                            │
                  EVAL SUITE ─── n  EVALUATION ─── 1  GRADER
                            └── 1  BASELINE
                  RUN ─── n  TRACE SPAN
                  ADR ─── couvre ──► une décision refusée par défaut
```

---

## 2. Specification entities

### MISSION
*The equivalent of SDD_Pro's FEAT.* What the agentic system must accomplish,
in business terms.

| Field | Nature | Mandatory |
|---|---|:---:|
| `id` | `{n}-{Name}` — stable, never renumbered | yes |
| `context` / `objective` | bounded prose | yes |
| `quantified_goal` | metric + target + deadline | yes (G0) |
| `budget` | `cost_per_run`, `latency_p95`, `token_ceiling` | yes (G0) |
| `actors` | humans and systems interacting | yes |
| `ground_truth` | where the truth we evaluate against comes from | yes (G0) |
| `trust_boundaries` | which sources are uncontrolled | yes |
| `business_rules` | `BR-{i}` | yes |
| `acceptance_criteria` | system-level `AC-{i}` | yes |
| `out_of_scope` | explicit | yes |
| `failure_policy` | what the system does when it does not know | yes |

> `failure_policy` has no SDD_Pro equivalent and is mandatory here: an agentic
> system *will* meet cases outside its competence. Not deciding what to do with
> them is deciding it will make things up.

### CAPABILITY (CAP)
*The equivalent of the User Story.* **One** discrete, evaluable capability.

| Field | Nature |
|---|---|
| `id` | `{n}-{m}-{Name}` — stable |
| `parent_mission_hash` | `sha256:…` — detects a MISSION modified underneath |
| `statement` | "The system must be able to <observable action>" |
| `acceptance_criteria` | each: **metric + threshold + dataset + k runs** |
| `covers` | the MISSION's `BR-i`, `AC-i` — upward traceability |
| `inputs` / `outputs` | schemas, not prose |
| `allocated_to` | agent(s), tool(s), retriever(s) — filled in PHASE 2 by `architect-topology` |
| `criticality` | `normal` \| `critical` — drives k runs and thresholds |
| `failure_behavior` | what the system does when this CAP fails |

**A CAP is not an agent.** The CAP -> agent allocation is an architecture
decision taken in PHASE 2, revisable without touching the CAPs. Confusing the
two is the mistake that freezes a topology before it has been thought through.

The separation shows even in the hashes. G1 pins the CAP **without** its
`## Allocated To` section (key `capspec:`): PHASE 2 writes the allocation, and it
must not make PHASE 1 stale. G2 and the IR pin the **whole** CAP (key `cap:`): a
reallocation, on the other hand, does make the topology stale.

---

## 3. Architecture entities

### ROSTER
The architect's declaration, **human** (PHILOSOPHY P7): how many agents, which
ones, their roles, their tools, their tiers, their skills and their rules.
Written in `workspace/feats/{n}-roster.md` (the first `yaml` block is
authoritative), validated by `python .sdda/sdda.py roster validate`, never
written by an agent. `architect-topology` copies it verbatim into the topology
and materialises it; it does not modify it (`[ARCH_ROSTER_MUTATED]`).

### TOPOLOGY
The materialisation of the declared architecture: CAPs allocated to the roster,
the graph, the bounds, the **estimated budget** — and, as advice, the **simpler
topology considered** and how much less it would have cost.

### AGENT (an entity of the generated product — not to be confused with the Developer Agents)

| Field | Nature |
|---|---|
| `id`, `role` | identity |
| `serves_caps` | the CAPs it is responsible for (at least one) |
| `prompt_ref` | `workspace/src/{App}/prompts/{agent}.system.md` + its hash |
| `model_tier` | `fast` \| `balanced` \| `deep` — never a model name (P11) |
| `tools` | TOOL references — what it has the **right to call**, the minimum its CAPs require (P8) |
| `skills` | what it **knows how to do** — a named competence, with no schema and no side effect, carried by the prompt |
| `rules` | what it **must respect** — a named constraint, carried by the prompt, twin of `skills` |
| `retrievers` | RETRIEVER references |
| `memory_scopes` | what it reads and writes in memory |
| `input_schema` / `output_schema` | structured contracts |
| `bounds` | `max_iterations`, `max_tool_calls`, `max_delegation_depth`, `timeout_s`, `budget_usd` (P12) |
| `on_bound_exceeded` | `fail-explicit` \| `degrade` \| `escalate-human` |
| `handoff_contract` | whom it hands over to, with which state, under which condition |
| `refusal_policy` | what it refuses to do, explicitly |
| `trust_posture` | which inputs it treats as hostile |

> **A skill or a rule is not a tool.** Least privilege is never inferred from a
> skill: only `tools` grants a right. Since neither has a schema, no gate can
> execute them; their only check is contract ↔ prompt symmetry
> (`lint_prompts.py`: `[SKILL_NOT_IMPLEMENTED]`, `[SKILL_UNDECLARED]`,
> `[RULE_NOT_IMPLEMENTED]`, `[RULE_UNDECLARED]`), and their effect is measured in
> G5, through the ACs of the CAPs served.

### TOOL

| Field | Nature |
|---|---|
| `name`, `description` | **the description is prompt**: it is what the model decides on whether to call it |
| `input_schema` / `output_schema` | JSON Schema |
| `side_effect_class` | `read-only` \| `write-scoped` \| `write-destructive` \| `external-side-effect` |
| `safety_strategy` | mandatory if != `read-only`: dry-run, idempotency key, confirmation, allowlist, cap |
| `errors` | every declared error, with its expected behaviour on the agent side |
| `auth` | an env reference, never the value |
| `rate_limit`, `timeout_s`, `retry_policy` | bounds |
| `trust` | `trusted` \| `untrusted` — is the output hostile text (P8) |
| `contract_tests` | reference to the L2 suite |

> **A tool's `description` is a prompt-engineering artefact**, not
> documentation. A vague description is the first cause of "the agent doesn't
> call the right tool". It is reviewed like prompt, hashed like prompt.

### RETRIEVER / INDEX / CORPUS
`CORPUS` (the sources) -> ingestion + chunking -> `INDEX` (the store) ->
`RETRIEVER` (the query strategy). The three are distinct because they fail
differently and are measured separately: an incomplete corpus, a badly chunked
index and an ill-suited query strategy produce the same symptom.

### DATA ACCESS
How an agent touches structured data: `view-per-agent`, `repository-tools`,
`semantic-layer`, `text-to-sql`, `graphql` on a database (`DATA-ACCESS.md`), or
`declared-sources` on files, APIs and MCP servers (`DATA-SOURCES.md`). It
always carries its **safety envelope** (role, timeout, row cap, allowed schemas
or sources, forbidden operations). `dataaccess/none` produces no entry: absence
is its representation. Only `view-per-agent` and `declared-sources` have a
stack fiche today; the others are in the catalogue.

### MEMORY SCOPE
`short-term` (window + summary policy) · `long-term` (store, write policy,
retention, PII) · `shared` (inter-agent state, who reads what). The `long-term`
scope exists in the vocabulary and in the IR, but nothing executes it yet:
`LongTermEnabled: true` is refused at preflight (`[STACK_VALUE_UNIMPLEMENTED]`).
Details: `MEMORY-PATTERNS.md`.

### GUARDRAIL
An inline check at input or output: schema validation, injection detection, PII
redaction, quality judge. Carries an `on_trip`. The first three exist in code
(`templates/runtime/python/app/guardrails/`), active according to
`## Active Guardrails`; a guardrail named without an active fiche is refused at
preflight.

---

## 4. Evaluation entities

### DATASET
`golden/` (tuning) · `holdout/` (verdict, disjoint by hash) · `calibration/`
(human labels for judges) · `adversarial/` (security), under
`workspace/pipeline/datasets/`. Owned by `qa-evals` — **never** writable by the
`dev-*` agents (P2, ARCHITECTURE §7).

### EVALUATION
`(dataset, grader, threshold, k runs)` -> `(score, variance, verdict)`.

### GRADER
`exact` · `regex` · `schema` · `numeric-tolerance` · `semantic-similarity` ·
`llm-judge` (requires calibration, P9) · `trajectory` (the order and nature of
calls) · `cost` · `latency`.

### BASELINE
A reference result pinned to the tuple
`(prompt_hash, model_id, index_hash, tool_schema_hash, dataset_hash)` (P10).
Written **only** by the `promote-baseline` script, under
`workspace/pipeline/baselines/`: a baseline moves through a traced action, never
by overwrite, and no agent touches it.

### RUN / TRACE SPAN
An execution and its spans: agent turn, tool call, retrieval query, LLM call
(tokens, cost, latency), gate crossing.

### ADR
A refused-by-default decision, assumed in writing
(`workspace/pipeline/decisions/ADR-{timestamp}-{slug}.md`). The decisions that
require one are listed in `registry/adr-requirements.yml`. An ADR only counts
if it carries `Status: Accepted` and a `Covers: Key=value` line naming the
decision; a `Proposed` ADR authorises nothing.

---

## 5. Identifier convention

Stable, never renumbered, never reordered. Adding = a new index at the end of
the list. All upward traceability depends on it.

| Entity | Form | Example |
|---|---|---|
| Mission | `{n}-{Name}` | `1-SupportAssistant` |
| Capability | `{n}-{m}-{Name}` | `1-3-RouteByIntent` |
| Business rule | `BR-{i}` | `BR-4` |
| Acceptance criterion | `AC-{i}` | `AC-2` |
| Agent | `{n}-{agent-slug}` | `1-billing-specialist` |
| Tool | `{n}-{tool-slug}` | `1-zendesk-create-ticket` |
| Retriever | `{n}-{index-slug}` | `1-contracts-index` |
| Eval suite | `{n}-{m}-{grader}` | `1-3-routing-accuracy` |
| ADR | `ADR-{timestamp}-{slug}` | `ADR-20260920T1412-supervisor-over-router` |
