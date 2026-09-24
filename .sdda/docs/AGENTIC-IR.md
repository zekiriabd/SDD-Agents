# Agentic IR — intermediate representation

The hinge that makes `STACK.md` genuinely declarative: switching frameworks
changes the generator, not the specification.

---

## 1. Position in the chain

```
 MISSION / CAPS / TOPOLOGY / CONTRACTS        (Markdown — source autoritaire, éditée)
              │
              │  validate-topology   passe complète sur le Markdown (part `topology` de G2)
              │  ir-compiler         (script déterministe, 0 token)
              ▼
 workspace/.sys/.ir/{n}-system.ir.json        (IR — projection compilée, jamais éditée)
              │
              ├── validate_ir.py        schéma, atteignabilité, bornes, refs, scopes  (part `ir`)
              ├── estimate_budget.py    coût et latence estimés sur le graphe         (part `budget`)
              ├── TOPOLOGY GATE         le GRAPHE se juge ICI, pas dans la prose
              │
              ├──► générateur Python / LangGraph               écrit
              ├──► générateur C# / Microsoft Agent Framework   🟡 fiche seule
              ├──► générateur TypeScript / LangGraph.js        🟡 fiche seule
              └──► générateur Kotlin / Spring AI               🟡 fiche seule
```

**Golden rule**: the IR is **regenerable and disposable**. It is never edited by
hand, never committed as a source of truth, and any divergence between the IR and
the Markdown contracts is resolved in favour of the Markdown — then by a
recompilation.

G2 therefore reads two levels, each for what only it carries.
`validate-topology` runs as a complete pass **before** the compiler and judges
what exists only in the Markdown: contracted handoffs, fallback path, contracts
present, CAP ↔ allocation consistency, the reason for each agent beyond the
first. The graph — reachability, cycles, bounds, budget — is judged on the IR.

A generator, today, means the Python path: the `dev-*` agents that read the IR,
plus two deterministic generators, `gen_app_skeleton.py` (the skeleton) and
`gen_source_tools.py` (the tools for declared sources). The other three lines
of the diagram are stack sheets on disk that no generator reads yet; the first
of them will be the test of the IR (ROADMAP, lot 7).

---

## 2. Why an IR, and not just "neutral contracts"

| Without IR | With IR |
|---|---|
| Each generator re-interprets the Markdown **with an LLM** — 3 frameworks = 3 divergent interpretations of the same spec | Interpretation happens **once**, upstream. Generators consume a closed structure |
| The TOPOLOGY GATE has to judge prose | The gate validates a graph: reachability, cycles, bounds, references — deterministic, 0 tokens |
| The estimated budget is an opinion | The budget is **computed** over nodes and edges |
| An architecture change is a diff of paragraphs | A structured, readable, reviewable diff |
| "Does the generated code match the spec?" is a matter of judgement | It is a code ↔ IR comparison, partly automatable (`diff_code_vs_ir.py`, `validate-framework`, `validate-envelope`) |

---

## 3. Shape of the IR

Canonical schema: `.sdda/registry/ir.schema.json`. Structure (excerpt; `…`
marks what is elided):

```jsonc
{
  "irVersion": "1",
  "missionId": "1-SupportAssistant",
  "compiledFrom": {
    "missionHash":    "sha256:…",
    "capHashes":      { "1-1-…": "sha256:…", "1-2-…": "sha256:…" },
    "topologyHash":   "sha256:…",
    "stackHash":      "sha256:…",
    "contractHashes": { "workspace/pipeline/contracts/tools/1-zendesk-create-ticket.tool.md": "sha256:…" },
    "promptHashes":   { "billing-specialist": "sha256:…" },
    "holdoutHash":    "",
    "compiledAt":     "2026-09-24T10:00:00Z"
  },

  "budget": {
    "costPerRunTargetUsd": 0.05,
    "costPerRunHardCapUsd": 0.25,
    "latencyP95TargetMs": 8000,
    "tokenCeilingPerRun": 60000
  },

  "orchestration": {
    "rootPattern": "supervisor",
    "entryNode": "supervisor",
    "maxHops": 8,
    "checkpointing": true,
    "humanInTheLoop": false,
    "nodes": [
      { "id": "supervisor",  "kind": "agent",  "ref": "1-supervisor" },
      { "id": "billing",     "kind": "agent",  "ref": "1-billing-specialist" },
      { "id": "kb_lookup",   "kind": "retriever", "ref": "1-contracts-index" },
      { "id": "finalize",    "kind": "function", "ref": "compose_answer" }
    ],
    "edges": [
      { "from": "supervisor", "to": "billing",  "condition": "intent == 'billing'" },
      { "from": "billing",    "to": "supervisor", "condition": "always", "countsAsHop": true },
      { "from": "supervisor", "to": "finalize", "condition": "resolved || hops >= maxHops" }
    ],
    "terminalNodes": ["finalize"]
  },

  "agents": [
    {
      "id": "1-billing-specialist",
      "servesCaps": ["1-2-ExplainInvoiceLine", "1-4-IssueRefundTicket"],
      "promptRef": "workspace/src/{App}/prompts/billing-specialist.system.md",
      "promptHash": "sha256:…",
      "modelTier": "balanced",
      "tools": ["1-invoice-lookup", "1-zendesk-create-ticket"],
      "skills": ["1-explain-invoice-line"],
      "retrievers": ["1-contracts-index"],
      "memoryScopes": { "read": ["conversation"], "write": [] },
      "inputSchema":  { "$ref": "#/schemas/BillingRequest" },
      "outputSchema": { "$ref": "#/schemas/BillingAnswer" },
      "bounds": {
        "maxIterations": 8, "maxToolCalls": 15,
        "maxDelegationDepth": 1, "timeoutSec": 60, "budgetUsd": 0.08
      },
      "onBoundExceeded": "escalate-human",
      "trustPosture": { "untrustedInputs": ["retrieved_documents", "user_message"],
                        "injectionSuiteRef": "workspace/pipeline/datasets/adversarial/billing-specialist.jsonl" },
      "refusalPolicy": ["ne jamais émettre de remboursement > 500 EUR sans escalade"]
    }
  ],

  "tools": [
    {
      "id": "1-zendesk-create-ticket",
      "name": "zendesk_create_ticket",
      "description": "…",
      "sideEffectClass": "external-side-effect",
      "safetyStrategy": { "idempotency": "natural-key:conversation_id", "dryRunSupported": true,
                          "confirmation": "required-above:0", "cap": { "perRun": 1 } },
      "inputSchema": { "…": "…" },
      "outputSchema": { "…": "…" },
      "errors": [ { "code": "RATE_LIMITED", "agentBehavior": "backoff-then-escalate" } ],
      "authEnv": "ZENDESK_TOKEN",
      "timeoutSec": 10,
      "retryPolicy": "none",
      "trust": "trusted",
      "contractTestsRef": "workspace/pipeline/suites/tool-1-zendesk-create-ticket.yaml"
    }
  ],

  "retrievers": [
    {
      // ── INTENTION : ce qu'on EXIGE, et ce que la RETRIEVAL GATE mesure ──
      "id": "1-contracts-index",
      "pattern": "hybrid",
      "topK": 8,
      "citationMode": "required",
      "identityFilter": "customer_id",
      "indexHash": "sha256:…",
      "gateThresholds": { "recallAtK": 0.80, "ndcg": 0.70,
                          "groundedness": 0.85, "citationResolveRate": 0.98 },

      // ── RÉALISATION : les composants qui l'atteignent, et eux seuls ──
      "binding": {
        "store": "pgvector",
        "embeddingModel": "voyage-3-large",
        "chunk": { "strategy": "recursive-structural", "size": 800, "overlap": 120 },
        "hybridWeights": { "vector": 0.6, "lexical": 0.4 },
        "rerank": null
      }
    }
  ],

  "dataAccess": [
    {
      "id": "1-billing-view",
      "binding": { "strategy": "view-per-agent" },
      "exposedTo": ["1-billing-specialist"],
      "envelope": { "role": "readonly", "statementTimeoutMs": 5000,
                    "maxRows": 500, "schemas": ["billing"],
                    "forbidden": ["DROP","TRUNCATE","ALTER","DELETE","UPDATE","INSERT"] }
    }
  ],

  "memory": { "shortTermPolicy": "sliding-window", "shortTermMaxTurns": 20,
              "longTermEnabled": false, "piiPolicy": "redact-before-write" },

  "guardrails": {
    "input":  [{ "id": "injection-detection", "onTrip": "block-and-log" }],
    "output": [{ "id": "schema-validation",   "onTrip": "block-and-log" }]
  },

  "evaluation": {
    "suites": [
      { "id": "1-2-groundedness", "level": "L4", "capRef": "1-2-ExplainInvoiceLine",
        "dataset": "workspace/pipeline/datasets/golden/billing-v1.jsonl",
        "grader": "llm-judge", "judgeCalibrationRef": "workspace/pipeline/calibration/groundedness.json",
        "threshold": 0.85, "runs": 3 }
    ],
    "holdout": "workspace/pipeline/datasets/holdout/mission-1-v1.jsonl",
    "baselineRef": "workspace/pipeline/baselines/1-system.json"
  },

  "traceability": {
    "1-2-ExplainInvoiceLine": {
      "coversMissionItems": ["BR-3", "AC-1"],
      "implementedBy": { "agents": ["1-billing-specialist"], "tools": ["1-invoice-lookup"] },
      "evaluatedBy": ["1-2-groundedness"]
    }
  }
}
```

`compiledFrom` is what makes the IR **able to go stale**: it carries the
fingerprint of every source — including every contract, every prompt present
and the holdout. If a single one moves, the IR declares itself stale
(`[IR_STALE]`) instead of going on describing a specification that no longer
exists. Tracking prompts and the holdout is not a detail: it is what triggers
the recompilation that pins `promptHash` once `dev-prompt` has written, and the
one that emits the L9 acceptance suite as soon as the verdict set exists.

---

## 4. What `validate_ir.py` checks (0 tokens, blocking)

Report: `G2-{missionId}.ir.json`, the `ir` part of G2.

1. **Schema** — conformance to `ir.schema.json` (`[IR_INVALID]`).
2. **Closed references** — every `tools[]`, `retrievers[]`, node `ref` points to
   a declared entity. No phantom reference (`[IR_DANGLING_REF]`).
3. **Reachability** — every node is reachable from `entryNode`; every path
   reaches a `terminalNode` (`[GRAPH_UNREACHABLE]`).
4. **Bounds** — every cycle in the graph is cut by a bound (`maxHops`, a
   decrementing condition, or `maxIterations`). An unbounded cycle is a blocking
   error, not a warning, and has no bypass (`[UNBOUNDED_LOOP]`, P12).
5. **Bound consistency** — the sum of per-agent `budgetUsd` along the longest
   path does not exceed `costPerRunHardCapUsd` (`[BUDGET_EXCEEDED_ESTIMATE]`).
6. **CAP coverage** — every CAP is in the `servesCaps` of at least one agent or
   implemented by a tool/retriever, and appears in `traceability`
   (`[CAP_NOT_IMPLEMENTED]`, `[TRACEABILITY_GAP]`).
7. **Least privilege** — every tool wired to an agent is required by at least
   one of its CAPs. The excess is a `[TOOL_SCOPE_EXCESS]` finding.
8. **Side effects** — every non-`read-only` tool carries a `safetyStrategy`
   (`[SIDE_EFFECT_UNDECLARED]`), and a non-idempotent write tool carries
   `retryPolicy: none` (`[TOOL_RETRY_UNSAFE]` — one retry creates three
   tickets).
8bis. **Cohabitation** — an agent exposed to uncontrolled input carries no
   `write-destructive` tool (irreversible, no bypass), and only carries an
   `external-side-effect` tool if its strategy **bounds the damage**
   (`idempotency` and `cap` or `confirmation`) — otherwise
   `[UNSAFE_TOOL_COHABITATION]`. The gradient and its rationale:
   `rules/agent-safety.md` §7.
9. **Trust posture** — every agent with an `untrusted` input has an injection
   suite declared in `evaluation.suites` (`[INJECTION_SUITE_MISSING]`).
10. **Framework neutrality** — no framework identifier in the IR (`StateGraph`,
    `Kernel`, `AgentExecutor`…), in any of the catalogue's four languages
    (`[FRAMEWORK_LEAK_IN_CONTRACT]`). The IR describes *what*, never *with which
    API* (P11).
10bis. **Infrastructure neutrality** — no component identifier (`pgvector`,
    `voyage`, `postgres`, `psycopg`…) **outside `binding`**
    (`[INFRA_LEAK_IN_INTENT]`). Framework neutrality was not enough: it only
    looks for API names, and `store: "pgvector"` got through — although it
    couples in exactly the same way. The check covers `retrievers[]` and
    `dataAccess[]`, the two split branches; `memory.longTermStore` stays out of
    scope until memory is split in turn.
11. **Evaluability** — every suite declares `dataset`, `grader`, `threshold`,
    `runs`; the dataset lives under `workspace/pipeline/datasets/`, and only the
    L9 acceptance suite may read the holdout (`[AC_NOT_EVALUABLE]`,
    `[AC_DATASET_IS_HOLDOUT]`). Every blocking `llm-judge` has a resolvable
    `judgeCalibrationRef` whose agreement and item count reach
    `JudgeCalibrationMinKappa` and `JudgeCalibrationMinItems` — otherwise
    `[JUDGE_UNCALIBRATED]`, or the judge is declared `advisory` (P9).

On top of these come checks that carry no number: an IR older than its sources
(`[IR_STALE]`), a router without a fallback edge (`[ROUTER_NO_FALLBACK]`), a
critic conflated with the writer in a `reflection` pattern
(`[REFLECTION_SELF_GRADING]`), a holdout declared without the L9 suite that
measures it (`[ACCEPTANCE_SUITE_MISSING]` — G8 would have nothing to run), and,
as warnings, an agent under contract missing from the graph
(`[AGENT_NOT_IN_IR]`) or a handoff without a contract (`[HANDOFF_UNCONTRACTED]`).

---

## 4.bis The `intent` / `binding` boundary

Two branches, and that is what makes the IR genuinely multi-language.

| | **Intent** (outside `binding`) | **Realisation** (`binding`) |
|---|---|---|
| Answers | what the architect **requires** | how it is **achieved** |
| Retrieval | `pattern`, `topK`, `citationMode`, `identityFilter`, `maxRetrievalCalls`, `gateThresholds`, `indexHash` | `store`, `embeddingModel`, `chunk`, `hybridWeights`, `rerank` |
| Data access | `exposedTo`, `envelope`, `connectors` | `strategy` |
| Read by | the **gates** — this is what gets measured | the **generators**, when emitting code |
| Component name | **forbidden** (`[INFRA_LEAK_IN_INTENT]`) | the only place it is allowed |

**Why this boundary is not cosmetic.** Before it, `store: "pgvector"` and
`embeddingModel` were **mandatory** IR fields — and the same decision already
lived in `STACK.md ## Active Retrieval Stack`. Two truths about the same fact,
which nothing confronted. A C# generator reading `pgvector` looked for a sheet
that does not exist in its runtime; the ROADMAP planned to learn this at Lot 7,
on the second generator, that is, after writing the first one against a
contract known to be wrong.

**Reconciliation replaces duplication.** At compile time, `binding` is checked
against the active stacks: a disagreement is `[RETRIEVAL_BINDING_MISMATCH]`,
never a silent arbitration. The comparison is made on the **family** (`voyage`
covers `voyage-3-large`), because a sheet describes a family and a contract
names a model; the version number itself is pinned by `indexHash` (P10).

**The criterion that says whether the boundary holds**: a generator must be able
to derive its complete call plan **without ever reading `binding`**, and that
plan must be identical for `pgvector` and for any other store. It is a test
(`test_ir_compiler.py`), not an intention.

---

## 5. What the IR does **not** contain

- No framework class, method or API name.
- No prompt text — only a **reference** and a **hash**. The prompt remains a
  readable, reviewable file (P1).
- No secret — only environment variable names.
- No measurement result — the IR describes intent; results live in
  `workspace/.sys/reports/` (eval reports) and `workspace/.sys/.validation/`
  (gate reports). The one exception, and it is not a measurement:
  `budget.estimated`, which only `estimate-budget` is allowed to write — a
  planning estimate that `cost-report` then confronts with the measured cost
  (`[BUDGET_ESTIMATE_DRIFT]` beyond a 25 % gap).
