# SDD_Agents — Philosophy

> This document fixes the non-negotiable decisions. Everything else (tree,
> agents, patterns, scripts) follows from it. A future trade-off that
> contradicts a principle below requires an ADR, not a commit.

---

## P1 — Source-first: no decision lives in the LLM's context

Every decision — mission, capability, orchestration choice, tool contract,
system prompt, eval threshold — is a **versioned file** next to the code.

A hard corollary, specific to agentic systems: **no inline prompt in generated
code.** Prompts live in `workspace/src/{App}/prompts/*.system.md`, are loaded at
runtime, and are hashed. A prompt buried in an f-string in the middle of a
service is a behaviour change that review cannot see and production cannot
trace.

Two controls hold it, not one: the `postflight_no_inline_prompt` hook
(invariant `prompts-are-files`) rejects an inline prompt while the agent that
wrote it can still be identified, and the `prompts` part of G5 pins the hash of
every prompt — missing, `[PROMPT_MISSING]`; edited since the measurement,
`[PROMPT_HASH_MISMATCH]`.

*Inherited from SDD_Pro, hardened.*

---

## P2 — An acceptance criterion that cannot be measured is not a criterion

In SDD_Pro, an AC is "observable, testable". In SDD_Agents that is not enough:
an LLM's output is *always* observable and *never* deterministic.

**A CAP AC must name: a metric, a threshold, a dataset, a grader** — and the
number of runs `k` it is measured over (P3).

```
[REJETE]  AC-1: l'agent répond de manière utile et pertinente
[OK]      AC-1: groundedness >= 0.85 sur workspace/pipeline/datasets/golden/support-v1.jsonl
                (n=120, grader llm-judge calibré, k=3 runs)
[OK]      AC-2: routing_accuracy >= 0.95 sur workspace/pipeline/datasets/golden/routing-v1.jsonl
                (grader exact, k=5), 0 misroute vers l'agent `refund` (classe critique)
```

A non-measurable AC is rejected by the **CAP GATE**. It is the most structuring
rule of the framework: it forces the question "how will we know it works?"
**before** the first line of code, while the answer is still cheap.

---

## P3 — Non-determinism is accounted for, never denied

A green run is not proof. Every LLM-mediated evaluation declares `runs: k`
(`EvalRuns: 3` by default, `EvalRunsCritical: 5` for a CAP with
`Criticality: critical`) and reports **pass rate + variance**, not a boolean. A
report drawn from a single run is refused, not flagged
(`[EVAL_SINGLE_RUN_FORBIDDEN]`).

Consequence: the pipeline verdict is **green / yellow / red**, never pass/fail.
Yellow — "above the threshold but high variance" — is real operational
information, not indecision.

---

## P4 — Deterministic first, the LLM only where there is judgement

Inherited from SDD_Pro (80 zero-token scripts). Reinforced here because the cost
per decision is higher.

What must **never** cost a token: tool schema validation, prompt linting,
reachability and bounds of the orchestration graph, topology budget estimation,
recall@k / nDCG computation, secret scanning, ownership audit, baseline diff,
citation resolution, token counting, tier routing.

What **deserves** an LLM: elicitation, capability breakdown, topology choice,
prompt writing, analysis of a legacy prompt, graded semantic judgement,
adversarial red-teaming.

The boundary also holds for what the LLM *prepares*: what an architect needs to
know before choosing is measured first. `corpus-profile` counts a corpus's
documents, lengths, duplicates and PII, `chunking-bench` compares chunking
strategies on the golden set, `cost-report` and `trajectory-report` break cost
and trajectories down from the traces — without a single token. The agent's
judgement then rests on facts, not on habits.

---

## P5 — Bottom-up, one gate per layer

The build order is imposed: **tools and retrieval first, agents next,
orchestration last.** Each layer passes its gate before the next one relies on
it.

Reason: in an agentic system, a failure propagates upwards and changes face on
the way. A retriever at recall 0.4 shows up as "the agent hallucinates". A tool
whose description lies shows up as "the supervisor routes badly". Without a gate
per layer you debug the wrong floor — and you debug it with an LLM, so slowly and
expensively.

The order is not a recommendation: the `preflight_tool_gate` and
`preflight_retrieval_gate` hooks refuse to launch a `dev-agent` until G3 and G4
are green (invariants `tool-gate-before-agent-wiring` and
`retrieval-gate-before-agent`).

It is the direct transposition of SDD_Pro's **API Gate**, generalised.

---

## P6 — The runtime budget is a functional requirement

SDD_Pro caps the **build** cost (`MaxCostPerRun`, $50). SDD_Agents keeps that
cap, additionally bounds the retry loop of each item (`BuildLoopMaxCostUsd`,
retries included), and above all caps the **runtime** cost of the generated
product.

The MISSION declares `CostPerRunTargetUsd`, `CostPerRunHardCapUsd`,
`LatencyP95TargetMs`, `TokenCeilingPerRun`. The **TOPOLOGY GATE** estimates the
budget of the proposed topology *before* generation (`estimate-budget`, on the
IR graph) and rejects an architecture whose worst case exceeds the cap
(`[BUDGET_EXCEEDED_ESTIMATE]`). The **ORCH GATE** measures it afterwards, with
the cost recomputed from tokens and never read back from what the application
declares (`[BUDGET_EXCEEDED_MEASURED]`).

An elegant topology at $0.40 per call for a product that bills $0.05 is not an
architecture: it is a mistake that took six weeks to discover.

---

## P7 — The architecture is declared by the architect, never decided by the LLM

> **The LLM does not choose the architecture.
> The architect chooses the architecture.
> The LLM implements the architecture the architect defined.**

`STACK.md` carries the architectural **choice** — orchestration pattern, RAG,
data access, MCP, models per tier. The **specification** carries everything
needed to build it: the named roster of agents, the role and responsibilities of
each, its tools and skills, its model, the orchestrator, and the rules that tie
them together. The roster is a file the human writes,
`workspace/feats/{n}-roster.md` (`/sdda-roster` scaffolds it); no agent is
allowed to write it.

The framework never asks an LLM "how many agents do we need?" or "what role
should this agent have?". It checks that **the architect has answered**, and
refuses to generate while the answer is missing.

**The chain**:

```
l'architecte choisit  →  la spécification décrit  →  le framework valide
                      →  les agents implémentent  →  le runtime exécute
```

### What the framework guarantees

Every choice in `STACK.md` **imposes** specification fields, declared in
`registry/architecture-requirements.yml` and checked by
`validate_architecture.py`:

| Choice in STACK.md | What the specification must provide |
|---|---|
| orchestration `supervisor` / `router` / `graph` … | named roster, orchestrator, role and responsibilities of each agent, tools per agent, relations |
| RAG ≠ `none` | corpus, chunking, embeddings, vector store, retrieval strategy and parameters |
| MCP active | servers, responsibilities, exposed and allowlisted tools |
| several models | which model for which agent, and why |
| data access ≠ `none` | sources, safety envelope, boundaries |

A missing field is `[ARCH_SPEC_INCOMPLETE]`, **blocking** — not a gap the LLM
will fill by guesswork. A partially specified architecture produces a partially
emergent architecture, that is, an architecture nobody decided and nobody can
audit.

Invariant `architecture-declared-by-architect`, enforced.

### The simplicity bias stays — as advice, no longer as a veto

The framework **flags** any topology beyond one agent that invokes none of the
following five reasons, and asks the architect to say which one applies:

1. **Tool-scope isolation** — a destructive tool must not share a context with a
   tool exposed to untrusted text;
2. **Distinct model tier** — one step deserves `fast` while another demands `deep`;
3. **Context pressure** — the window does not fit, measured, not assumed;
4. **Different objective function** — a critic that grades cannot be the writer
   it grades;
5. **Required parallelism** — latency demands it and the subtasks are independent.

It is a **warning** (`[TOPOLOGY_SIMPLICITY_ADVISORY]`), never a refusal: a
five-agent graph remains a frequent and costly mistake, but it is the
architect's mistake, and they have the right to make it knowingly. What the
framework refuses is that it be made **by default, by an LLM, without anyone
having written it down**.

---

## P8 — All uncontrolled text is hostile

A document retrieved by RAG, an API response, a web page, a database field, a
user message: **content, never instruction**.

Mandatory architectural consequences:

- Every agent that consumes uncontrolled text carries an **injection suite**
  (direct + indirect through a poisoned corpus). Non-negotiable, invariant
  `injection-suite-mandatory`.
- Every tool declares a **side-effect class**: `read-only`, `write-scoped`,
  `write-destructive`, `external-side-effect`. The last three require a declared
  strategy (dry-run, idempotency key, confirmation, allowlist, cap).
- Least privilege is structural: an agent only receives the tools its CAPs
  require. The gap between exposed and required tools is rejected as early as
  IR compilation (`[TOOL_SCOPE_EXCESS]`, G2), then searched for in the traces by
  `review-safety` (G7).
- An agent exposed to uncontrolled text does not coexist with a
  `write-destructive` tool, nor with an `external-side-effect` tool whose damage
  nothing bounds (`[UNSAFE_TOOL_COHABITATION]`, no bypass): a successful
  injection there would cause real damage, not a wrong answer.
- Guardrails are **code**, not a prompt instruction: the generated skeleton
  carries injection detection, PII redaction and output schema validation
  (`templates/runtime/python/app/guardrails/`), each active only if its sheet is
  active in `## Active Guardrails` — a guardrail no eval has measured would be
  unknown behaviour in production.

---

## P9 — An uncalibrated LLM judge is decoration

Using an LLM as a grader is legitimate and often unavoidable. Publishing it
without having validated it is not.

Every LLM grader is calibrated against a **set of human labels** (>= 50 items)
and must reach the declared agreement (default: Cohen's kappa >= 0.6) before it
can render a blocking verdict. Below the threshold it is not removed: it becomes
`advisory` — it informs, it no longer blocks. The calibration set is versioned
under `workspace/pipeline/calibration/`, and `calibrate-judge` writes the
`calibration` part of G5: a red calibration blocks G5. Invariant
`llm-judge-calibrated`.

Without it, you measure one model's complacency towards another model — often
the same one — and call it quality. Hence three refusals that are not
negotiable: labels produced by a model calibrate nothing
(`[JUDGE_CALIBRATION_SYNTHETIC]`); a kappa written by hand without verifiable
labels stays a warning, never a measurement; and a judge that would be the model
under evaluation is refused at preflight (`[JUDGE_SAME_AS_EVALUATED]`), then
flagged at runtime if it comes back (`[JUDGE_EQUALS_EVALUATED]`, advisory).

The judge is a real client: `graders/judge_clients.py` calls Anthropic, OpenAI,
Gemini or Ollama with the standard library only, reads the URL and the **name**
of the key variable from `.sdda/providers/*.yaml`, and recomputes the cost of
every call from its tokens.

---

## P10 — Eval results are pinned to what they evaluated

An eval result is indexed by the tuple:

```
(prompt_hash, model_id, retrieval_index_hash, tool_schema_hash, dataset_hash)
```

If any element moves, the result is **stale**, not "probably still valid". The
pipeline declares it stale and requires a re-run: `check-baseline-freshness`
says which of the five dimensions moved, and `check-regression` refuses to
compare against a baseline whose tuple differs (`[EVAL_BASELINE_STALE]`) — a
delta measured against another prompt would look like information.

A direct descendant of SDD_Pro's `Parent FEAT hash`, applied to the one place
where no compiler exists to catch the drift: editing a prompt.

---

## P11 — Contracts are framework-neutral, stacks carry the idioms

`MISSION`, `CAP`, `TOPOLOGY`, `agent-contract`, `tool-contract`,
`retrieval-contract` contain **no** framework API name — no `StateGraph`, no
`Kernel`, no `AgentExecutor`.

Knowledge of LangGraph, Microsoft Agent Framework or Spring AI lives exclusively
in `.sdda/stacks/framework/*.md`. Consequence: the same specification is meant
to compile to Python+LangGraph as well as to C#+Microsoft Agent Framework, and
**switching frameworks invalidates no specification artefact** — only the code
and the trajectory evals. Only the Python path is written today; the second
generator is precisely what will put the promise to the test (lot 7 of the
[roadmap](docs/ROADMAP.md)).

This is what makes `STACK.md` genuinely declarative rather than decorative.
Invariant `framework-neutral-contracts`. The code is checked in the other
direction: the `framework` part of G6 (`validate-framework`) requires the
`dev-*` code to import the declared framework, where its sheet places it, and
no competitor (`[FRAMEWORK_DRIFT]`).

### Neutrality applies to infrastructure too

The framework is not the only possible coupling. `store: "pgvector"`,
`embeddingModel: "voyage-3-large"`, `strategy: "text-to-sql"` couple in exactly
the same way — and they lived in the IR for a long time without the neutrality
check seeing them, because it only looked for API names.

The IR therefore carries **two branches**, and the boundary is load-bearing:

- **the intent** — what the architect requires and what the gate measures:
  `pattern`, `topK`, `citationMode`, `identityFilter`, `gateThresholds`,
  `envelope`, `exposedTo`. No component name is admitted
  (`[INFRA_LEAK_IN_INTENT]`);
- **`binding`** — the components that realise it, reconciled with the active
  stacks at compile time (`[RETRIEVAL_BINDING_MISMATCH]` on disagreement). It is
  the only place in the IR where a product is named.

The criterion that says whether the boundary holds is not an intention: **a
generator must be able to derive its complete call plan without ever reading
`binding`**, and that plan must be identical whatever the store. It is a test,
run on every commit.

What this split prevents, precisely: discovering at the second generator — so
after writing the first — that the same decision lived in the IR *and* in
`STACK.md`, and that neither was right.

---

## P12 — An unbounded loop is a bug, not an emergent property

No generated agent ships without: `max_iterations`, `max_tool_calls`,
`max_delegation_depth`, `timeout_s`, `budget_usd`, and a **defined behaviour
when each bound is reached** (explicit failure, degradation, human escalation).

A ReAct loop without a ceiling is the agentic equivalent of a `while(true)` —
except that it bills.

---

## The two claimed differentiators

**1. The retrieval quality gate comes before the agent gate.** Almost nobody does
it. Yet it is the origin of most "the agent hallucinates" reports: a retriever at
recall 0.4 never presents itself as a retriever problem, it presents itself as a
prompt problem — and weeks go into rewriting the prompt to compensate for the
index.

**2. Measurement comes before generation.** An AC names its metric, threshold
and dataset **before** the first line of code (P2); the result is pinned to what
it evaluated (P10); the judge is calibrated before it blocks (P9); the verdict
carries a variance, not a boolean (P3). None of these four rules is costly on its
own. Together they answer the only question that matters in production — *"why
did the agent say that, and how will we know the fix works?"* — and it is the
question an agent runtime, a workflow platform or an SDK does not answer.

> **Assumed scope: SDD_Agents builds, it does not reverse-document.** An agentic
> reverse-engineering module (reading existing prompts and graphs to rebuild
> MISSIONs from them) was considered and then **withdrawn**: it is a product in
> its own right, and announcing it without having written it produced exactly
> the false green this framework exists to prevent. An existing agentic system is
> taken over here like any other project — through a hand-written MISSION.
