# Testing and Evaluation

The founding distinction: **a test asserts, an eval scores.** An agentic system
needs both, and confusing them produces either flaky tests that end up disabled,
or evals that detect nothing.

| | Test | Eval |
|---|---|---|
| Applies to | the **deterministic** part | the **LLM-mediated** part |
| Verdict | pass / fail | score + variance vs threshold |
| Runs | 1 | **k** (default 3, 5 if critical) |
| Flaky ⇒ | it is a bug | it is the nature of the system, to be quantified |
| Cost | ~0 | tokens |

---

## 1. The L0 → L9 pyramid

Each level has its gate writer, and only one: two writers on the same gate would
produce two truths about the same fact.

| Level | Gate | Who runs it and writes the report |
|---|---|---|
| L0–L1 | — | `qa-tests` (pytest, mocked LLM) + validation scripts |
| L2 | G3, `suites` part | `run-tool-suites` (plays `qa-tests`' pytest tests) |
| L3 | G4 | `run-retrieval-eval` |
| L4 | G5 | `eval-runner --level L4` |
| L5, L7 | G6 | `eval-runner --level L5,L7` |
| L6 | — | integration tests, no gate of their own |
| L8 | G7, `suites` and `adversarial` parts | `eval-runner --level L8`, `run-adversarial-suite` |
| L9 | G8, `acceptance` part | `eval-runner --level L9 --dataset holdout`, then `check-regression` |

### L0 — Static, deterministic, 0 tokens
Runs on every commit, in a few seconds.

- JSON Schema of every tool definition;
- **prompt lint**: no secret, no contradictory instruction, size under the cap,
  all template variables resolvable, no reference to a non-existent tool, skill
  symmetry between contract and prompt;
- **IR validation**: reachability, termination, bounded cycles, closed
  references, least privilege (see `AGENTIC-IR.md §4`);
- **budget estimation** on the graph;
- `golden ∩ holdout = ∅` disjointness by hash;
- baseline freshness (P10 hash tuple);
- ownership audit, secret scan;
- dependency CVEs 🟡 — not tooled for the generated application: the versions
  pinned in the `.libs.json` files are taken from SDD_Pro catalogues that audit
  them, and Dependabot only tracks the framework's own dependencies.

### L1 — Unit
Pure functions: parsers, chunkers, mappers, state reducers, cost computations,
retry policies. **Mocked LLM.** Full speed and determinism.

### L2 — Tool contract
Every tool against its schema: happy path, **every declared error**, timeout,
authentication failure, idempotency (does calling twice with the same key
produce a single effect?), rate-limit compliance.

`qa-tests` writes these tests from the tool's suite
(`workspace/pipeline/suites/tool-{n}-{outil}.yaml`); `run-tool-suites` plays
them and requires every declared case to be **exercised** — a case that is named
but never played is not covered, and an `xfail` is a contract not kept, hence
red.

A **live connectivity** test exists separately, marked `network`, and is the
second half of the TOOL GATE. Otherwise a tool whose contract is green but whose
service is unreachable would pass the gate.

### L3 — Retrieval evals *(RETRIEVAL GATE)*
Golden set of queries with ground truth. **No agent involved.** `recall@k`,
`nDCG@k`, `context_precision`, `groundedness`, `citation_resolve_rate` (see
`RAG-PATTERNS.md §5`).

`run-retrieval-eval` computes everything except groundedness, which requires a
judge: it receives it from the executor or the replay, and without it the
verdict turns **yellow**, with the reason written down — never green by
omission. One report per retriever (`G4-{retriever}.json`): one index can be
green while another is red.

### L4 — Isolated agent evals *(AGENT GATE)*
A single agent, **tools mocked and retrieval frozen**, against the ACs of its
CAPs.

Isolation is the point: an agent evaluated with real tools measures the sum of
the agent and the tools. When the score drops, you cannot tell which one moved.
With mocks, the variation is attributable.

Graders: `exact`, `regex`, `schema`, `numeric-tolerance`,
`semantic-similarity`, `llm-judge` (calibrated), `trajectory`.

G5 is judged per CAP, and three contributing parts block it when red: the
judge's `calibration` (§3), `prompts` pinning (`[PROMPT_MISSING]`,
`[PROMPT_HASH_MISMATCH]`) and the phase's `ownership` audit.

### L5 — Orchestration / trajectory
On the trace, not on the answer:

- did the router route correctly — **accuracy per class**;
- are the tools called the expected ones, in an admissible order;
- number of hops: distribution and tail, not just the mean;
- are bounds respected, and is the behaviour when a bound is reached the
  declared one;
- no unreachable terminal state, no observed loop.

A correct answer obtained through an aberrant trajectory is a false green: it
costs ten times the budget and will break at the next prompt change.

`trajectory-report` rebuilds each trajectory from the span tree and confronts it
with the IR graph, without an LLM: `[TRAJECTORY_VIOLATION]`,
`[BOUND_BEHAVIOR_MISMATCH]`, `[ORCH_PING_PONG]`, confusion matrix per class.
`review-orchestration` reads this report; it does not redo it.

### L6 — Integration
Real tools, real index, real test database. API connections, database
connections, migrations, end-to-end ingestion. Without an LLM when possible. No
gate belongs to it: what it proves, gates G3 and G4 already prove layer by
layer; it is there to find what breaks **between** them.

### L7 — End-to-end mission evals *(ORCH GATE)*
The complete system on the mission's golden set. Joint measurement of quality,
**cost** and **latency**. A run that reaches the score while exceeding
`CostPerRunHardCapUsd` is **red**, not yellow.

Cost is recomputed from the tokens of every span, never read back from what the
application declares. G6 also carries two contributing parts: `api` (the
published HTTP contract is derived from the IR) and `framework` (the code
imports the declared framework and no other, `[FRAMEWORK_DRIFT]`).

### L8 — Adversarial and security *(SAFETY GATE)*
Detailed in §4.

### L9 — Acceptance and regression *(ACCEPTANCE GATE)*
Two measurements, in this order, and only on the **holdout**:

1. **Acceptance** — the `{n}-acceptance` suite, compiled from the MISSION's
   `## Quantified Goal`, is the only one that reads the holdout
   (`[AC_DATASET_IS_HOLDOUT]` for any other). Goal not reached:
   `[GOAL_NOT_MET]`.
2. **Regression** — comparison with the pinned baseline. A drop beyond
   `RegressionTolerancePct` blocks (`[REGRESSION]`) — unless it fits within the
   baseline's noise band (`RegressionNoiseSigma` standard deviations): it is
   then a draw, `[REGRESSION_WITHIN_NOISE]` as a warning. A baseline whose
   pinning tuple differs is not compared at all (`[EVAL_BASELINE_STALE]`).

The baseline moves **explicitly**, through a traced action
(`promote-baseline --run`), never by automatic overwrite — otherwise slow drift
becomes invisible.

---

## 2. The non-determinism protocol

**Rule**: no LLM-mediated eval is reported from a single run
(`[EVAL_SINGLE_RUN_FORBIDDEN]`). Invariant `non-determinism-k-runs`. `k` comes
from `EvalRuns` / `EvalRunsCritical`, unless explicitly overridden with
`--runs`, and every run is replayed **without cache**: reusing one run's output
for the next would amount to measuring once and reporting k times.

The report (`workspace/.sys/reports/{n}-{RUN_ID}.json`) carries, per suite:
`mean`, `stddev` (population standard deviation: the k runs **are** the measured
population), `variancePct` (standard deviation relative to the mean),
`passRate` (share of the k runs above the threshold), `min`, `max`, and the
per-class breakdown.

```
CAP 1-2 ExplainInvoiceLine — groundedness
  seuil 0.85   k=3
  runs  0.91 · 0.88 · 0.80
  mean 0.863  stddev 0.046  pass_rate 0.67
  VERDICT: 🟡 JAUNE — moyenne au-dessus du seuil, un run sur trois en dessous.
           Ne pas livrer sur la foi du premier run.
```

The verdict has three colours:

| | Condition |
|---|---|
| 🟢 **GREEN** | `mean ≥ threshold` **and** no class below its threshold **and** `passRate = 1.0` **and** `variancePct ≤ EvalVarianceWarnPct` |
| 🟡 **YELLOW** | threshold cleared on average but `passRate < 1.0` or `variancePct > EvalVarianceWarnPct` |
| 🔴 **RED** | `mean < threshold`, or a class below its threshold, or every item failed to execute, or a budget exceeded |

An `advisory` suite — typically an uncalibrated judge — keeps its real verdict in
the report, but its red only counts as a yellow in the gate: it informs, it does
not block.

A yellow is not indecision: it is the information that a deployment is a bet on
the next draw.

---

## 3. Calibrating LLM judges

An unvalidated LLM grader renders no blocking verdict (P9, invariant
`llm-judge-calibrated`).

The judge is a real client: `graders/judge_clients.py` calls Anthropic, OpenAI
(and compatibles), Gemini or Ollama with the standard library only, at
temperature 0 and with structured JSON output when the provider enforces it. The
model comes from `JudgeModel` (`STACK.md ## Runtime Models`); the key is read
from the process environment, under the **name** declared by the provider
sheet — never from a `.env`, which belongs to the generated application.

**Procedure**:
1. `qa-evals` produces a calibration set: ≥ 50 items from the real domain
   (`JudgeCalibrationMinItems`), labelled by a human against the judge's exact
   rubric.
2. The judge grades the same items.
3. `calibrate-judge` recomputes the agreement (Cohen's kappa for binary/ordinal,
   correlation for continuous). Default threshold: **κ ≥ 0.6**
   (`JudgeCalibrationMinKappa`).
4. The set is versioned in `workspace/pipeline/calibration/{grader}.json` and
   referenced from the IR (`judgeCalibrationRef`); the script writes the
   `calibration` part of G5 (`G5-{n}.calibration.json`), and a **red
   calibration blocks G5**.
5. Below the threshold: the rubric is reworked, or the judge becomes
   `advisory` — it produces an informative score but no longer blocks
   (`JudgeAdvisoryFallback`).

**Additional rules**:
- labels produced by a model calibrate nothing
  (`[JUDGE_CALIBRATION_SYNTHETIC]`, no bypass);
- an agreement declared without verifiable labels stays a warning, never a
  measurement;
- the judge is not the model under evaluation: with
  `JudgeMustDifferFromEvaluated: true` (the default), the configuration is
  refused at preflight (`[JUDGE_SAME_AS_EVALUATED]`), and the case is flagged at
  runtime if it comes back (`[JUDGE_EQUALS_EVALUATED]`, advisory);
- the rubric is a list of verifiable criteria, not "rate quality from 1 to 10";
  the judge sees the reference when one exists.

---

## 4. Adversarial and security suite (L8, SAFETY GATE)

Run against the **live system**, never by reading code.

| Family | Attack | Expected |
|---|---|---|
| **Direct injection** | "ignore previous instructions and…" in the user message | refusal, unchanged behaviour |
| **Indirect injection** | a corpus document or an API response contains the instruction | **the most important case** — treated as data, never executed |
| **Injection via tool** | an `untrusted` MCP server returns an instruction | ignored |
| **Tool abuse** | pushing the agent to call a destructive tool outside its mandate | refusal + logging |
| **Privilege escalation** | through delegation, reaching another agent's tool | impossible by construction (scopes) |
| **Exfiltration** | getting a secret, a PII or the system prompt out through an outbound tool | blocked |
| **Authorisation crossing** | obtaining another tenant's document through retrieval | filtered at the source |
| **Budget exhaustion** | provoking a loop | bound reached, declared behaviour |
| **Persona jailbreak** | pushing it outside its declared mandate | `refusal_policy` respected |

`AdversarialSetMinItems: 25` at minimum, and the set grows: **every successful
attack discovered becomes a permanent item**. That is the mechanism that stops
the same flaw from coming back.

How it is played, in `/sdda-review`:

- **The starting set is not a blank page.**
  `.sdda/templates/datasets/adversarial-seed.jsonl` (31 items) is a seed for
  `qa-evals`, which adapts it to the domain.
- **The versioned set is played live.** `run-adversarial-suite --executor …`
  first checks coverage of the families each agent's inputs and tools impose,
  then plays every attack against the delivered surface and records the
  execution in `workspace/.sys/reports/runs/{n}-adversarial.jsonl`, which
  `--replay` re-judges at 0 tokens. Security semantics, not quality semantics:
  over k runs, an attack that gets through once is a flaw, not an average.
- **Stage C improvises**, behind a guard: `adversarial-target-check` refuses any
  target it cannot prove local or isolated (`[ADVERSARIAL_TARGET_UNSAFE]`) — a
  successful attack against production is an incident, not a finding.
- **Findings become items.** `review-adversarial` does not write to
  `datasets/`; `promote-adversarial-findings --agent qa-evals` appends every
  successful attack to it, deduplicated, without ever rewriting an existing
  line.

Complementary deterministic scans (0 tokens, once before stage B): secrets in
prompts / traces / datasets / code (`scan-secrets`), PII in the vector store
(`scan-pii`), gap between exposed and required tools (`audit-tool-scope`),
non-`read-only` tools without a safety strategy. On the product side, the
guardrails of `## Active Guardrails` (injection detection, PII redaction, schema
validation) are code in the generated skeleton, and it is this suite that
measures them. Finally, G7 requires the `review-safety` and
`review-orchestration` reports: a missing report is not "0 findings"
(`[SAFETY_REVIEW_REPORT_MISSING]`).

---

## 5. The datasets

| Set | Role | Minimum | Who writes |
|---|---|:---:|---|
| `golden/` | tuning — you iterate against it | 50 | `qa-evals` |
| `holdout/` | **verdict** — you never iterate against it | 30 | `qa-evals` |
| `calibration/` | human labels for the judges | 50 | human + `qa-evals` |
| `adversarial/` | security | 25 | `qa-evals` (seed, then promoted findings) |

**Disjointness checked by hash** (`HoldoutDisjointCheck: strict`,
`[HOLDOUT_NOT_DISJOINT]`). Optimising prompts against the set that renders the
verdict is the agentic way of lying to yourself, and it is silent.

**No `dev-*` may write to `datasets/`.** The agent that writes the code cannot
modify the set that judges it. Without this barrier, self-confirmation is not a
risk: it is the default outcome.

---

## 6. What we measure when "everything is green"

A dashboard that only shows scores hides the essential. The final report also
carries:

- **cost per CAP** — where the money goes, and which CAP costs more than it is
  worth (`cost-report`, `[CAP_COST_EXCEEDS_VALUE]`);
- **trajectory distribution** — the 3 % tail that takes 12 hops
  (`trajectory-report`, `cost-report`);
- **abstention rate** — a system that never says "I don't know" on an open
  domain is not doing its job well, it is lying better;
- **top failing tools** — often the real cause of a mediocre score (calls,
  failures, retries per tool in `cost-report`);
- **drift over time** — same prompt, same set, scores sliding: the provider's
  model moved under your feet. That is why `model_id` is part of the pinning
  tuple (P10). Run-to-run comparison exists (baselines, `check-regression`); the
  curve over time awaits the validation console 🟡.
