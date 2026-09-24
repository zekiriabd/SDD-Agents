# Lifecycle and state machine

Every specification artefact carries a `Status`. Transitions are **guarded**:
you only move to the next state by passing a gate. The state is a **fact**
derived from the gates passed, never a statement of intent by an agent.

---

## 1. The state machine

```
   Draft ──G0 · G1──► Specified ──G2──► Architected ──(revue de plan)──► Planned
                                                                          │
                                                                          │ G3 · G4
                                                                          ▼
                                                                     Implemented
                                                                          │ G5
                                                                          ▼
                                                                       Tested
                                                                          │ G6 · G7
                                                                          ▼
                                                                      Evaluated
                                                                          │ G8
                                                                          ▼
                                                                       Approved

   Depuis tout état non terminal :  ──► Blocked   (gate rouge, cause [CLASS] portée)
                                    ──► Deferred
                                    ──► Cancelled
```

An edge label names the gate that **grants** the arrival state — the one in the
table below and in `compute_status.py`. The diagram used to shift them by one
(`Specified ──G1──► Architected`): it placed `Architected` after G1, where the
table and the code grant it at G2 — `test_workflow_sync.py` now checks that the
diagram and the code say the same thing. `Planned` has no numbered gate: the
plan review is conditional, and its absence skips the level
(`OPTIONAL_LEVELS`).

**No state skipping.** `Draft -> Implemented` is refused even with a flag: the
bypass exists at the level of a given gate (audit-logged), not at the level of
the machine. `compute_status.py` climbs the ladder level by level and stops at
the first absent or stale gate; on the command side, `/sdda-full --from-phase`
cannot skip a gate that has not been passed (`[STATE_SKIP_FORBIDDEN]`).

---

## 2. The states

| State | Means | Gate that grants it | Carried by |
|---|---|---|---|
| `Draft` | exists, incomplete | — | MISSION, CAP |
| `Specified` | quantified goal, budget, ground truth, trust boundaries present; ACs evaluable; every MISSION item covered by a CAP | **G0** then **G1** (traceability per MISSION, and one G1 per CAP) | MISSION, CAP |
| `Architected` | topology decided, simpler alternative ruled out in writing, contracts written, IR compiled and valid, estimated budget under the cap | **G2** — `topology`, `ir`, `budget` parts; `packaging`, `architecture` and `adr` block when red | MISSION, TOPOLOGY |
| `Planned` | per-layer implementation plans written, build order frozen | plan review (human, conditional) | TOPOLOGY, contracts |
| `Implemented` | tools green + live connectivity, retrieval above thresholds, agents and orchestration materialised | **G3** (`contracts` and `suites` parts, per wired tool) + **G4** (per retriever) | TOOL, RETRIEVER, AGENT |
| `Tested` | agents evaluated in isolation against their CAP ACs (L4, k runs); no judge calibration, prompt pinning or ownership audit in red | **G5**, per CAP — `calibration`, `prompts`, `ownership` parts block when red | CAP, AGENT |
| `Evaluated` | end-to-end evals passed, cost and latency **measured** under budget, adversarial and security suites passed, reviewer reports present | **G6** + **G7** (`suites`, `adversarial`, `verdict` parts) | MISSION |
| `Approved` | quantified goal reached on **holdout**, no regression against the baseline | **G8** (`datasets` and `acceptance` parts) | MISSION |
| `Blocked` | a gate turned red; carries the `[CLASS]` and the report | — | all |
| `Deferred` / `Cancelled` | traced human decision | — | all |

A **mandatory** part that is absent stops the climb; a **contributing** part
(`GATE_PARTS_ADVISORY` in `gate_reports.py`) that is absent does not block, but
red, it blocks. The distinction exists because some checks do not apply to
every project — a `cli` surface publishes no HTTP contract, hence no `api`
part — and a red, for its part, never has an excuse.

---

## 3. Transition rules

**R1 — The state is derived, not declared.**
`sdda_scripts/compute_status.py` computes the state from the gate reports on
disk. An agent that writes `Status: Tested` in a file without a matching report
emits `[STATUS_UNBACKED]` and the script overwrites it. The correction works in
both directions: a `Status: Blocked` that no red report backs any more is
rewritten too, otherwise an unblocked artefact would stay `Blocked` forever in
its header. A self-proclaimed state is the mechanism by which an agentic
pipeline declares itself green.

**R2 — State regression is automatic and silent.**
Every gate report pins the hashes of what it judged (MISSION, CAP, topology, IR,
`STACK.md`, prompts, datasets — P10). If one moves, the report is **stale**: the
gate is no longer passed, the artefact drops below the level it granted, and
`compute-status` says so (`[STATUS_PINNED_HASH_MOVED]`). No human validation is
required to drop: it is a fact, not a judgement call.

Pinning follows what each gate judges, and nothing more. G1 pins the CAP
**without** its `## Allocated To` section (`capspec:` key): PHASE 2 fills that
section, and it must not make PHASE 1 stale. G2 and the IR pin the whole CAP
(`cap:` key): a reallocation does make the topology stale.

**R3 — A parent's state is the minimum of its children's.**
A MISSION is `Evaluated` when **all** its CAPs are. A single `Blocked` CAP makes
the MISSION `Blocked`. No average, no progress percentage that hides a gap.

**R4 — Confidence never rises as you climb the ladder.**
Inherited from SDD_Pro. A CAP derived from a MISSION at `medium` confidence
cannot be `high` (`[CONFIDENCE_ESCALATION]`). An agent serving a CAP at `medium`
confidence inherits the ceiling.

**R5 — A bypass is named, bounded and audited.**
A gate is bypassed through an explicit bypass (`SDDA_BYPASS_{GATE}=1` or a
command flag) — not every gate: some classes have none, for example
`[UNBOUNDED_LOOP]`, `[INJECTION_SUCCEEDED]` or `[SECRET_LEAK]`. A bypass
requires a reason (`SDDA_BYPASS_REASON`; missing or filler,
`[BYPASS_REASON_MISSING]`) and is written to
`workspace/.sys/.audit/bypasses.jsonl` with the timestamp, the operator and the
reason — even when it is allowed: a legitimate bypass that leaves no trace
produces the same record as a concealed one. Accumulating ≥ 2 bypasses (or
`--force` + a bypass) on the same run is itself blocking
(`preflight_force_cumul`, inherited from SDD_Pro), unless `SDDA_ALLOW_FORCE=1`,
which is traced in turn.

**R6 — Resuming means replaying the gates, not trusting them.**
`/sdda-full {n} --resume` opens a run linked to the previous one
(`resumedFrom`) and skips the lineage's `pass` phases — but it **replays** their
gates (0 tokens), because a hash may have moved in the meantime (R2). A state
remains a fact computed when it is read, never a memory of the previous run.

---

## 4. What the console shows

A state is only worth something if it can be read at a glance. Today,
`python .sdda/sdda.py compute-status` (or `/sdda-status`) shows the state tree:
MISSION, CAPs, the verdict of every gate, the classes carried, stale hashes,
audited bypasses. The measurements live alongside — eval reports under
`workspace/.sys/reports/`, `cost-report`, `trajectory-report`. The combined view
below is the **target** of the validation console 🟡: each of its lines exists
in a report, no screen brings them together yet.

```
MISSION 1-SupportAssistant                                        Evaluated  🟡
  budget      $0.041/run (cible $0.05)   p95 6.2s (cible 8s)      ✅
  holdout     objectif 0.90 → mesuré 0.88                          🟡  G8 non franchie
  CAP 1-1 ClassifyIntent          Approved   0.97 ±0.01  (k=5)     ✅
  CAP 1-2 ExplainInvoiceLine      Evaluated  0.86 ±0.14  (k=3)     🟡  variance > 15%
  CAP 1-3 RouteByIntent           Approved   0.96 ±0.02  (k=5)     ✅
  CAP 1-4 IssueRefundTicket       Blocked    [INJECTION_SUCCEEDED]         🔴
```

The yellow on CAP 1-2 is real information: the score clears the threshold but
the variance says the next run may not. A pipeline that only shows a boolean
would have shown green.
