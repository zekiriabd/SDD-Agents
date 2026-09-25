# The SDD_Agents Developer Agents

<!--sdda:count agents-->24<!--/sdda:count--> specialised agents. **These are the agents that *build*** — not to be
confused with the agents of the generated product, described in
`workspace/pipeline/contracts/agents/`.

Internal orchestration rule inherited from SDD_Pro: **no agent spawns another
agent.** The command orchestrates, the agent executes. That is what keeps the
build bill predictable and parallelism boundable (`MaxParallel`).

## 0. The naming convention — `{trade}-{domain}`

Five prefixes, one per trade, in pipeline order:

| Prefix | Trade | Phase | Agents |
|---|---|:---:|---|
| `po-` | product owner — the need and how it is broken down | 0-1 | `po-elicitor`, `po-capabilities` |
| `architect-` | architecture — materialising, contracting and costing the declared structure | 2 | `architect-topology`, `architect-rag`, `architect-data`, `architect-memory`, `architect-tools` |
| `dev-` | implementation | 3-5 | `dev-backend` (the shell), `dev-tools`, `dev-retrieval`, `dev-data`, `dev-prompt`, `dev-agent`, `dev-orchestration`, `dev-api`; `dev-app` (profile `poc` only) |
| `qa-` | what proves | 6a, 6 | `qa-tests`, `qa-evals` |
| `review-` | what contests | 7 | `review-spec`, `review-safety`, `review-cost`, `review-orchestration`, `review-rag`, `review-adversarial` |

This is not cosmetic. The original naming called **five** agents `*-architect`
when none of them decides the architecture in the sense an architect means it:
the **human architect** declares it in the roster
(`workspace/feats/{n}-roster.md`, PHILOSOPHY P7), `architect-topology`
materialises and costs it, and the other four write contracts inside the scope
it has drawn. And the functional phase — the one where we ask what the system
must do and for whom — had no name to designate it, although it carries two
agents.

An `ls .sdda/agents/` now sorts by trade, then by domain: the roster reads like
a team, in the order it steps in, without documentation.

**Why two PO agents rather than one.** `po-elicitor` gathers, then
`po-capabilities` splits into measurable CAPs. Merging them would remove the
barrier that makes `qa-evals`'s veto possible: a CAP whose AC is not measurable
is sent back with `[AC_NOT_EVALUABLE]`, and it must go back to someone whose
only job that is — not to the agent that also wrote the MISSION it derives from.

> **CAP = user story.** The breakdown `po-capabilities` performs is the one
> SDD_Pro performs into User Stories, with four extra requirements: metric,
> threshold, dataset, k runs (see `SDD-PRO-INHERITANCE.md §3`). There is no
> intermediate level between MISSION and CAP — adding one would duplicate
> traceability without measuring anything more.

---

## 1. Dashboard

The "Writes to" column is a summary; the source of truth is each agent's
`writes:` key in `.sdda/loader.yml`, which the hooks enforce.

| Agent | Phase | Tier | Writes to | The question it asks |
|---|:---:|:---:|---|---|
| **`po-elicitor`** | 0 | balanced | `pipeline/missions/` | What is the truth we will be judged against, and what does the system do when it does not know? |
| **`po-capabilities`** | 1 | balanced | `pipeline/caps/` | Which discrete capabilities, and how is each one measured? |
| **`architect-topology`** | 2 | **deep** | `pipeline/topology/`, `pipeline/contracts/agents/`, the CAPs' `## Allocated To`, ADRs | Is the declared roster complete for the active pattern, and what does it cost in the worst case? |
| **`architect-tools`** | 2 | balanced | `pipeline/contracts/tools/` | Which contract, which side effects, which safety? |
| **`architect-rag`** | 2 | **deep** | `pipeline/contracts/retrieval/` | Which corpus, which chunking, which strategy, which golden set? |
| **`architect-data`** | 2 | balanced | `pipeline/contracts/tools/{n}-data-*`, ADRs | How does the agent touch the data without being able to harm it? |
| **`architect-memory`** | 2 | balanced | `pipeline/contracts/memory/` | What persists, for how long, with which PII? |
| **`dev-backend`** | 3.0, 5 | balanced | `src/{App}/*`, `src/**/app/` | — the shell: project, composition, config, computable rules (3.0), then packaging (5) — inherited from SDD_Pro, IN ADDITION to the six engine agents |
| **`dev-app`** | 3-5, `poc` only | balanced | `src/{App}/**` except `prompts/`, `skills/`, `rules/`, the context file | — the whole application in one agent under `Profile: poc`, after `dev-prompt`; replaces the seven agents above and never runs alongside them (`exclusive-by-profile`) |
| **`dev-tools`** | 3 | balanced | `src/**/tools/` | — implements the tools |
| **`dev-retrieval`** | 3 | balanced | `src/**/retrieval/` | — implements ingestion + retriever |
| **`dev-data`** | 3 | balanced | `src/**/data/` | — implements views, repositories, envelope |
| **`dev-prompt`** | 4 | **deep** | `src/{App}/prompts/`, `skills/`, `rules/` | How does this contract become a prompt that holds under adversity — tools, **skills and rules** included? |
| **`dev-agent`** | 4 | **deep** | `src/**/agents/{agent}/` | — implements one agent (1 bound instance per agent) |
| **`dev-orchestration`** | 4.0, 5 | **deep** | `src/**/shared/`, `src/**/memory/`, `src/**/orchestration/` | — prepass (shared types + memory interface), then graph/supervisor/router and memory |
| **`dev-api`** | 5 | balanced | `src/**/serving/` | — implements the exposure surface |
| **`qa-evals`** | 6a, 6 | **deep** | `pipeline/datasets/`, `pipeline/suites/`, `pipeline/calibration/` | Which set, which grader, which threshold, calibrated how? (6a: the sets, BEFORE the code; 6: suites completed) |
| **`qa-tests`** | 6 | balanced | `src/**/tests/` | — deterministic tests L0→L2, one invocation per layer |
| **`review-spec`** | 7A | balanced | `.sys/.validation/reports/spec-compliance-{n}.md` | Does every CAP AC have an eval that really covers it? |
| **`review-safety`** | 7B | **deep** | `.sys/.validation/reports/agent-safety-{n}.md` | Where does hostile text flow, and what can it trigger? |
| **`review-cost`** | 7B | fast | `.sys/.validation/reports/cost-latency-{n}.md`, `cost-{n}.json` | What does it really cost, and where does the money go? |
| **`review-orchestration`** | 7B | balanced | `.sys/.validation/reports/orchestration-{n}.md`, `trajectories-{n}.json` | Useless hops, loops, dead ends, uncontracted handoffs? |
| **`review-rag`** | 7B | balanced | `.sys/.validation/reports/rag-quality-{n}.md` | Does retrieval hold, or is the agent compensating? |
| **`review-adversarial`** | 7C | **deep** | `.sys/.validation/reports/adversarial-{n}.md`, `adversarial-findings/{n}.jsonl` | How do I break this system now that it runs? |

Per-agent `tier_floor` / `tier_ceiling` bounds: `.sdda/agent-bounds.yaml`. They
are quality invariants and cannot be overridden by the Project Config.

Reviewers write **exactly** the paths in their `writes:` under
`workspace/.sys/.validation/`: that is where `validate-safety-gate` reads them
back, and an invented report name would be a report the gate never finds. A
**gate** report (`G{k}-*.json`) stays forbidden to them, through the editor as
through the shell: only the gate's script writes it.

`dev-prompt` carries a second responsibility that the "Writes to" column only
half shows: it is the **implementation owner of the skills and rules** of the
product's agents. `architect-topology` *declares* them in the agent contract
(copied from the roster), `dev-prompt` *implements* them in
`prompts/{agent}.system.md` (`## Compétences`, `## Règles`) and in one fragment
per slug (`skills/{slug}.md`, `rules/{slug}.md`), and neither can write in the
other's files — so neither can resolve a disagreement alone. `lint_prompts.py`
checks the correspondence both ways: `[SKILL_NOT_IMPLEMENTED]`,
`[SKILL_UNDECLARED]`, `[RULE_NOT_IMPLEMENTED]`, `[RULE_UNDECLARED]`. Since a
skill or a rule has neither schema nor side effect, this is the only possible
check: no gate can execute it to judge it. Details: `rules/ownership.md §2.2`.

---

## 2. The agents that carry the framework's value

### `architect-topology` — costing before building

It has no equivalent in SDD_Pro. It does **not choose** the architecture — the
number of agents, their roles, their tools and the pattern are declared by the
architect (roster and `STACK.md`, PHILOSOPHY P7). What it contributes, nobody
else does: it is the only point in the pipeline where a topology at $0.40 per
call, for a product that bills $0.05, becomes visible **before** everything has
been built on top of it.

**Mandatory procedure**, in this order:

1. Read the declared roster (`workspace/feats/{n}-roster.md`, validated by
   `python .sdda/sdda.py roster validate` before it is spawned) and copy it
   **verbatim** into the topology's `## 2. Roster déclaré`. It reads the roster;
   it never writes it.
2. Run `python .sdda/sdda.py validate-architecture --mission {n}`: is the
   declaration complete for the active pattern
   (`registry/architecture-requirements.yml`)? Red → STOP; it names the missing
   fields and does not guess them.
3. Allocate each CAP to the roster, asking two questions first: does a
   deterministic tool suffice? (Often yes: a tool beats an agent on every
   criterion — cost, latency, testability, debuggability.) Does retrieval
   suffice? A CAP given to an agent when a tool would have done is **reported**;
   it does not remove the agent.
4. For every agent beyond the first, look for one of P7's five closed reasons —
   tool-scope isolation, distinct tier, measured context pressure, different
   objective function, required parallelism. None → `[TOPOLOGY_SIMPLICITY_ADVISORY]`
   with the cost that agent adds: a figure, not an opinion. The **"Simpler
   alternative considered"** section is advisory in the same way: what an N-1
   agent design would have been, and how much less it would have cost.
5. Produce the graph (the ```mermaid block of `## 4. Le graphe`, inside the
   topology itself), each agent's five bounds with their behaviour when reached,
   and the **estimated budget** per path — nominal and worst case. Worst case
   above the MISSION's cap → `[BUDGET_EXCEEDED_ESTIMATE]`: it hands back with the
   figure and the three levers, without picking any of them.

Its estimate is a **hypothesis** (ARCHITECTURE §5): the fact comes after it,
from `estimate-budget` on the compiled IR, which writes the `budget` part of G2.

It **writes no** prompt, **chooses no** model (it chooses a tier), **names no**
framework API, and never renames, adds or removes a roster agent
(`[ARCH_ROSTER_MUTATED]`).

### `qa-evals` — without it, nothing is proven

The only agent allowed to write to `workspace/pipeline/datasets/`. It runs
twice: in **6a**, before the code (`/sdda-eval {n} --datasets-only`), so that
the sets exist before any `dev-*` can tune against them; then in **6**, to
complete the suites. It produces:

- the **golden set** (tuning) and the **holdout** (verdict), disjoint, checked by hash;
- the **calibration set**: ≥ 50 human-labelled items
  (`JudgeCalibrationMinItems`), to validate every LLM judge before it renders a
  blocking verdict (P9) — an uncalibrated judge becomes advisory;
- the **adversarial set**: direct and indirect injections, jailbreaks, tool-abuse
  attempts, exfiltration;
- the eval **suites**, their **graders** and their thresholds.

It does not write the **baselines** (P10): `workspace/pipeline/baselines/**` is
reserved to the `python .sdda/sdda.py promote-baseline` script. A baseline moves
through a traced action, never by overwrite.

It holds a veto: a CAP whose AC is not measurable comes back to it, and it sends
it back to `po-capabilities` with `[AC_NOT_EVALUABLE]`.

### `review-safety`

Replaces and widens SDD_Pro's `security-reviewer`. Its grid: direct and
**indirect** injection (poisoned corpus, API response, web page), tool scope in
excess of the CAPs, destructive operations without a safety strategy, secrets
leaking into prompts/traces/datasets, PII in the vector store, privilege
escalation through delegation, exfiltration through an outbound tool. It
**reads** the reports of the deterministic scans (`scan-secrets`, `scan-pii`,
`audit-tool-scope`), run once before stage B; it does not rerun them. Its
`[SAFETY_*]` classes are blocking. `AgentSafetyMode: off` is refused on a
production or CI run.

---

## 3. Review stages — two stages, then adversarial

Inherited from SDD_Pro (`AuditorBatchMode: two-stage`), adapted:

```
Stage A   review-spec  (ALONE)
          → is every CAP AC covered by an eval that really measures it,
            rather than by an eval that sidesteps it?
          RED verdict → stages B and C do not start.

Scans     scan-secrets · scan-pii · audit-tool-scope   (0 token, once)

Stage B   agent-safety · cost-latency · orchestration · rag-quality   (PARALLEL)

Stage C   review-adversarial  (on the LIVE system, not on the code)
          → real attacks, unexpected trajectories, edge inputs
          then run-adversarial-suite: the versioned set, played LIVE
```

Why stage A runs alone: aggregating quality, cost and security findings on a
system that does not do what the spec asks is waste. First we check we are
looking at the right system.

Why the scans run before stage B: one measurement, one producer, one moment —
before the readers. Run twice, the second execution overwrote the part the
first had made the reviewer read.

Why stage C is separate: adversarial testing only makes sense when executed.
Reading code to look for an injection flaw yields an opinion; firing 40
injections at the system yields a fact. `review-adversarial` improvises and
records every successful attack as a **finding**; the versioned set, for its
part, is played by a script (`python .sdda/sdda.py run-adversarial-suite`),
which records every execution itself and writes the `adversarial` part of G7. A
missing mandatory reviewer report is not "zero findings": it is
`[SAFETY_REVIEW_REPORT_MISSING]`.

---

## 4. Internal orchestration — parallelism and what bounds it

| Phase | Parallelisable | Barrier |
|---|---|---|
| 2 — architecture | `architect-tools` ∥ `architect-rag` ∥ `architect-data` ∥ `architect-memory` | `architect-topology` first (it sets the scope), declared-source contracts generated by script (`gen-source-tools --scope contracts`), then IR compilation |
| 6a — datasets | `qa-evals` alone | G2 green; the sets exist before the code |
| 3 — foundation | `dev-tools` ∥ `dev-retrieval` ∥ `dev-data` | `dev-backend` (skeleton) alone first; source code generated (`gen-source-tools --scope code`) before `dev-data`; TOOL GATE + RETRIEVAL GATE before phase 4 |
| 4 — agents | 1 `dev-agent` per agent, in parallel waves | `dev-orchestration --prepass` (4.0) then `dev-prompt` (4.1), each alone; AGENT GATE after |
| 5 — orchestration | none: sequential | `dev-orchestration` → `dev-api` → `dev-backend` (packaging); ORCH GATE after |
| 6 — eval/tests | `qa-evals` ∥ `qa-tests` (one invocation per layer) | datasets frozen before evals run |
| 7B — review | 4 reviewers, in waves ≤ `MaxParallel` | stage A green, scans run |

Parallelism is bounded by `MaxParallel` and made safe by the ownership matrix,
and that safety is checked at three moments rather than assumed:

- **At spawn and on every write.** Ownership globs are segmented (`*` stays
  within one segment, `**` spans several) and real overlaps between zones are
  computed (`audit-ownership --declared-only`). Every `dev-agent` instance
  carries `SDDA-INSTANCE: {agent}` as the first line of its prompt: the
  `preflight_instance_bind` hook declares it, its first write binds it to its
  agent, and a write under `agents/{other}/` is refused
  (`[OWNERSHIP_INSTANCE_ESCAPE]`). Without that line, the spawn is refused
  (`[OWNERSHIP_INSTANCE_UNDECLARED]`).
- **On disk, after every wave.** `audit-ownership snapshot` before the wave,
  `audit-ownership --since-snapshot` after it (phases 3, 4.0, 4 and 5): what the
  hooks cannot see — a script writing from the inside — shows up in the diff. An
  out-of-zone write is revoked with `--restore`.
- **On frozen zones.** The shared types and the memory interface laid down by
  the 4.0 prepass are frozen during phases 4 and 5; touching them is
  `[OWNERSHIP_FROZEN_ZONE_CHANGED]`.

Two concurrent `dev-agent` instances thus write into disjoint
`src/**/agents/{agent}/` directories and share no file for writing — not
because they were asked to, but because the hook and the audit observe it.
