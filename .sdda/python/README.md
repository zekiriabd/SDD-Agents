# `.sdda/python` — the deterministic layer of SDD_Agents

Everything here runs **without an LLM and without a network** (PHILOSOPHY P4):
stdlib only (`dependencies = []`), with `pytest`, `ruff` and `mypy` for
development only (`pip install -e ".sdda/python[dev]"`). Python **3.11+**.

The one exception is deliberate and bounded: `sdda_lib/graders/judge_clients.py`
calls a real LLM judge (Anthropic, OpenAI, Gemini, Ollama), in stdlib, for the
eval suites that declare an `llm-judge` grader and for those only; the URL and
the name of the key variable come from `providers/*.yaml`. Validators, the IR
compiler and the hooks never call a model.

## Packages

| Package | Role |
|---|---|
| `sdda_cli.py` | single dispatcher: `python .sdda/sdda.py {cmd}` (or `sdda {cmd}` once installed). The registry is **derived from disk**: every module in `sdda_scripts/`, `sdda_admin/` or `sdda_hooks/` is a sub-command named after its file (`validate_mission.py` -> `validate-mission`) |
| `sdda_lib/` | shared library: `markdown_io` (sections, tables, lists), `yaml_mini` and `jsonschema_mini` (stdlib parsers, tested against PyYAML and jsonschema), `hashing` (sha256 normalised for CRLF/BOM), `graph` (SCC, cycles, reachability, longest path), `mermaid` (flowchart -> nodes/edges), `layered_config` (base < team < project, security-down, validation against `project-config.schema.json`), `pricing` (rates read from `providers/*.yaml`, hard-coded table as fallback), `gate_reports` (`.sys/.validation/` reports, gate parts, bypass audit), `errors` (3-line ERROR blocks with `[CLASS]`), `tracing` (OTel-GenAI spans, written under a lock), `retrieval_metrics`, `eval_stats`, `eval_pinning`, `calibration`, `graders/`, `paths`, `workspace` |
| `sdda_scripts/` | the gates, the IR compiler, the generators and the reports — one script = one sub-command, `--root` and `--json` everywhere, `--no-report` on the gates |
| `sdda_admin/` | health of the framework itself: `framework_smoke`, `sync_error_registry`, `sync_digests`, `sync_counters`, `harness_build`, `hooks_selfcheck`, `planned_scripts`, `command_flags` |
| `sdda_hooks/` | Claude Code's blocking `PreToolUse` / `SubagentStop` hooks; each declares its `WIRING`, which `harness_build` wires into `.claude/settings.json` (see [MULTI-HARNESS.md](../docs/MULTI-HARNESS.md) §3) |
| `tests/` | pytest; `fixtures/project_ok/` is a valid project, the other `fixtures/project_*` are overlays (see [tests/fixtures/README.md](tests/fixtures/README.md)) |

## Running the tests

```bash
# depuis la racine du dépôt
python -m pytest .sdda/python/tests/ -q
```

Each test copies `project_ok/` into a temporary directory: the scripts write
reports and an IR, and may rewrite a `Status:` — never in the source fixture. A
guard (`tests/_workspace_guard.py`) fails any test that writes under the
repository's **real** `workspace/`, including from a subprocess. Tests favour
**negative** cases: a validator that can only say OK is useless.

CI also runs `ruff` and `mypy` (configuration in `pyproject.toml`) and coverage
with a floor (`fail_under`).

## Running the gates

All of them accept `--root <project>` (default: detect the folder that contains
`workspace/`), `--json` (machine output) and `--no-report` (do not write into
`workspace/.sys/.validation/`). Exit `0` = OK, `1` = at least one blocking error.
`python .sdda/sdda.py --help` lists every sub-command.

| Gate | Command | Report written |
|---|---|---|
| G0 MISSION | `python .sdda/sdda.py validate-mission` | `G0-{mission}.json` |
| G1 CAP | `python .sdda/sdda.py validate-cap` | `G1-{cap}.json`, `G1-{mission}.json` |
| G2 TOPOLOGY (Markdown) | `python .sdda/sdda.py validate-topology` | `G2-{mission}.topology.json` (not with `--pre`) |
| 2.9 IR compilation | `python .sdda/sdda.py ir-compiler --mission 1` | `workspace/.sys/.ir/1-system.ir.json` |
| G2 TOPOLOGY (IR) | `python .sdda/sdda.py validate-ir --mission 1` | `G2-{mission}.ir.json` |
| G2 TOPOLOGY (budget) | `python .sdda/sdda.py estimate-budget --mission 1` | `G2-{mission}.budget.json` + `budget.estimated` in the IR |
| G2 (contributing) | `validate-architecture`, `validate-packaging`, `validate-adr` | `G2-{mission}.{architecture\|packaging\|adr}.json` |
| datasets (part of G8) | `python .sdda/sdda.py validate-datasets` | `G8-{mission}.datasets.json` |
| derived state | `python .sdda/sdda.py compute-status [--require-gate G1]` | — (may rewrite an unbacked `Status:`) |

G2 is passed only when its **three** mandatory parts (`topology`, `ir`,
`budget`) are green; its contributing parts (`architecture`, `packaging`, `adr`)
do not block by being absent, but their **red** blocks. G8 requires `datasets`
**and** `acceptance`. The full table of parts lives in
`sdda_lib/gate_reports.py` (`GATE_PARTS`, `GATE_PARTS_ADVISORY`).

`validate-topology` runs its **complete** pass before `ir-compiler`: it is the
only one that writes G2's `topology` part; `--pre` is a quick check that writes
no report.

### The IR

`ir_compiler.py` projects MISSION + CAPs + TOPOLOGY (Mermaid graph included) +
`contracts/**` + `STACK.md` into a JSON document that conforms to
`registry/ir.schema.json`. It is **deterministic** (sorted keys, LF,
`compiledAt` kept as long as the content does not change) and **invents
nothing**: a missing field produces `[IR_COMPILE_FAILED]` with `file:section`.
The only derivations are conventions listed in the script header (suite id
`{n}-{m}-{metric}`, default calibration
`workspace/pipeline/calibration/{metric}.json`, injection suite
`{agent}-injection`, dotted Mermaid edge `-.->` = an edge that does not count
as a hop).

`validate_ir.py` applies the 11 checks of [AGENTIC-IR.md](../docs/AGENTIC-IR.md)
§4: schema, closed references, reachability, bounded cycles
(`[UNBOUNDED_LOOP]`, no bypass), Σ budgets over the longest path, CAP coverage,
least privilege (`[TOOL_SCOPE_EXCESS]`), side effects
(`[SIDE_EFFECT_UNDECLARED]`), injection suites, framework neutrality
(`[FRAMEWORK_LEAK_IN_CONTRACT]`), evaluability and judge calibration
(`[JUDGE_UNCALIBRATED]`). It also refuses an IR that is stale with respect to
its sources (`[IR_STALE]`).

`estimate_budget.py` computes a **nominal** path (each node once) and a **worst
case** (`maxHops` reached, agents at `maxIterations`, tools at their timeout),
with planning assumptions visible at the top of the script — G6 measures, here
we estimate. Worst case > `CostPerRunHardCapUsd` =>
`[BUDGET_EXCEEDED_ESTIMATE]`. Audited bypass: `SDDA_BYPASS_BUDGET_ESTIMATE=1`
+ `SDDA_BYPASS_REASON=…`.

### The state machine

`compute_status.py` never reads the `Status:` line to decide: it derives the
state from the gate reports that are present and fresh (LIFECYCLE R1), expires a
report whose pinned hash has moved (R2), takes the minimum of the CAPs for the
MISSION (R3), flags confidence that goes up (R4) and lists the bypasses (R5). A
`Status:` declared higher than the computed state is `[STATUS_UNBACKED]` and is
**overwritten** (`--no-write` to report only).

## Framework health

```bash
python .sdda/sdda.py framework-smoke            # cohérence de .sdda/
python .sdda/sdda.py sync-error-registry --check # toute [CLASS] émise est enregistrée
```

Before a commit, the full chain regenerates whatever is derived and then checks:
`sync-error-registry` → `sync-digests` → `sync-counters --write` →
`harness-build --prune` → `framework-smoke` → pytest. CI replays each step with
`--check`, plus `hooks-selfcheck` (every wired hook starts and judges, in strict
mode, `SDDA_HOOKS_STRICT=1`).
