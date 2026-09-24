# Multi-harness compilation

SDD_Agents is written **once**, in `.sdda/`, and compiled into the facades each
harness knows how to read. The mechanism comes from SDD_Pro, with one extra
constraint that is specific to agentic systems.

Machine SSoT: [`.sdda/capability-matrix.yml`](../capability-matrix.yml).

## 0. Status — read this first

| Harness | Status | Gates blocking at runtime |
|---|---|---|
| **Claude Code** | **supported** — reference harness, level A | yes: `PreToolUse` / `SubagentStop` hooks in `.claude/settings.json` |
| **Codex CLI** | **experimental** — `status: planned`, level B | **no**: deferred to CI and to the deterministic scripts |
| **Gemini CLI** | **experimental** — `status: planned`, level B | **no**: same |

"Experimental" means exactly this: the facade **compiles** and is kept up to
date (`harness-build --check` in CI), but no conformance run has ever executed
it end to end, and **nothing stops, at the moment of the action**, a write
outside ownership, an agent wired before its TOOL GATE, or an inline prompt.
The spawn wrapper this document describes (§2, "`codex exec` / `gemini -p`
wrapper"; `spawn_agent` in the matrix) is **planned, not yet written**: under
Codex or Gemini, the human launches each sub-agent and runs
`python .sdda/sdda.py framework-smoke` before trusting a result.

Codex CLI reads `AGENTS.md` and Gemini CLI reads `GEMINI.md` **at the root of
the repository**, not inside `.codex/` or `.gemini/`. `harness_build` therefore
writes a generated `AGENTS.md` and `GEMINI.md` at the root, which point to the
facade and repeat this status (`tests/test_harness_root_pointers.py`).

The matrix also declares `antigravity` (`planned`, level B). It shares Gemini
CLI's adapter and its `.gemini/` directory; the default build compiles only one
harness per directory, so it is built only on explicit request
(`--harness antigravity`).

---

## 1. The principle

```
.sdda/                          source neutre — la seule chose qu'on écrit
  ├─ agents/*.md
  ├─ commands/*.md
  ├─ rules/*.md
  └─ python/                    indépendant du harnais par construction
        │
        │   harness_build.py
        ▼
  ┌─────────────┬─────────────┬───────────────┐
  │  .claude/   │   .codex/   │   .gemini/    │
  │  CLAUDE.md  │  AGENTS.md  │  GEMINI.md    │
  │  agents/    │  agents/    │  agents-inline│
  │  commands/  │  prompts/   │  commands/    │
  │  settings   │             │  *.toml       │
  └─────────────┴─────────────┴───────────────┘
   + harness-impact.md dans chaque façade
   + AGENTS.md / GEMINI.md racine (pointeurs)
```

**Facades are generated, never edited.** A direct change inside `.claude/` is
overwritten by the next build, and the parity check catches it (§6).

---

## 2. What gets compiled, and how

| Source | Claude Code | Codex | Gemini CLI |
|---|---|---|---|
| `agents/{a}.md` | `.claude/agents/{a}.md` — native sub-agent, `model:` resolved from the tier | `.codex/agents/{a}.md` — tier header + body, to be inlined into the `codex exec` wrapper prompt | `.gemini/agents-inline/{a}.md` — same, for `gemini -p` |
| `commands/{c}.md` | `.claude/commands/{c}.md` | `.codex/prompts/{c}.md` | `.gemini/commands/{c}.toml` |
| `rules/*.md` | `@`-referenced, lazy-loaded | **inlined** (no `@`) | inlined |
| memory file | `.claude/CLAUDE.md` (read natively) | `.codex/AGENTS.md`, pointed to by the root `AGENTS.md` | `.gemini/GEMINI.md`, pointed to by the root `GEMINI.md` |
| hooks | `.claude/settings.json` — **blocking at runtime**, plus native `deny` rules on reading `.env` files | absent → CI (wrapper planned) | absent → CI (wrapper planned) |
| `python/` | as is | as is | as is |

Each harness's memory file **is** the architecture: it is compiled from
`.sdda/ARCHITECTURE.fr.md`, because the agents work in French
(`.sdda/ARCHITECTURE.md` is its English reference, for human readers).

The business body is **identical** everywhere: only the envelope and the
loading policy change. On a harness without lazy loading (`at_include` ≠
`native`), every `@.sdda/{file}` reference is rewritten as
"`` `.sdda/{file}` `` (Read ce fichier avant de poursuivre)". The fallback is
verbose on purpose: a silent reference would produce an agent that believes it
has read a rule it has never seen. `sync_counters` counter markers are stripped
at compile time: an agent pays no tokens for documentation upkeep.

---

## 3. The red line: hooks

This is where portability stops being free.

Under Claude Code, these invariants are enforced **at the moment of the
action**, by a hook that refuses the tool call (exit code 2). Every hook in
`sdda_hooks/` declares its `WIRING`; `harness_build` wires them all, with no
hard-coded table:

| Event / tools | Hooks | Invariants |
|---|---|---|
| `PreToolUse` `Task\|Agent` | `preflight_tool_gate` | `tool-gate-before-agent-wiring`, `tool-side-effect-declared` |
| | `preflight_retrieval_gate` | `retrieval-gate-before-agent` |
| | `preflight_agent_bounds` | `no-unbounded-loop` |
| | `preflight_db_envelope` | `db-safety-envelope-present` |
| | `preflight_cap_gate` | `cap-ac-must-be-evaluable` |
| | `preflight_judge_calibration` | `llm-judge-calibrated` |
| | `preflight_stack_combo` | `activated-stack-is-loadable` |
| | `preflight_agent_budget`, `preflight_cost_cap`, `preflight_instance_bind` | context budget, cost cap, `dev-agent` instance binding |
| `PreToolUse` `Write\|Edit\|MultiEdit\|NotebookEdit` | `preflight_ownership` | `ownership-matrix-enforced` |
| `PreToolUse` `Bash\|PowerShell` | `preflight_bash_ownership` | `ownership-matrix-enforced` (writes and secret reads through the shell) |
| `PreToolUse` `Read\|Glob\|Grep` | `preflight_forbidden_reads` | `ownership-matrix-enforced` (`forbidden_reads`, `[SECRET_READ_FORBIDDEN]`) |
| `SubagentStop` | `postflight_no_inline_prompt` | `prompts-are-files` |
| | `postflight_trace_present` | `trace-emitted-per-run` |

The shell hook covers **PowerShell** with a dedicated dialect, not just Git
Bash. Two settings decide what happens when a hook does not get to judge:

- **`SDDA_PYTHON`** selects the interpreter (`python` as the fallback). A hook
  that fails to start returns a code ∉ {0, 2}, which the harness treats as an
  **allow**: the launch failure is therefore reported on stderr;
- **`SDDA_HOOKS_STRICT=1`** (CI): a hook that crashes or fails to start
  **refuses** (`[HOOK_FAILED]`) instead of letting the call through.

`python .sdda/sdda.py hooks-selfcheck` runs every wired command exactly as the
harness will launch it, with a harmless payload (must return 0) and a payload
that must be refused (must return 2). `framework-smoke` checks that a hook is
wired; only `hooks-selfcheck` proves that it runs.

On a harness without hooks, **they do not disappear — they move** to a pre/post
exec check in the wrapper and to a CI gate. Until the wrapper is written, only
the CI gate and the deterministic enforcers of the mixed invariants remain.

**The consequence must be stated, not dressed up**: between two wrapper runs,
nothing prevents an out-of-scope write. CI catches it — later, after the work
has been done on a false basis.

That is why the impact report is **mandatory on every build**:

> `harness_build.py` refuses to produce a facade without emitting its report
> (`{facade}/harness-impact.md`). Any invariant whose enforcers are **all**
> hooks, on a harness where `runtime_hooks != native`, appears there as
> **"appliqué en différé (CI)"** (enforced later, in CI); an invariant that
> keeps a deterministic enforcer appears as "partiellement différé" (partly
> deferred). Claiming it is enforced at runtime would be exactly the doc-theater
> that `INVARIANTS.yml` exists to prevent.

---

## 4. The agentic-specific constraint: `structured_output`

It has no equivalent in SDD_Pro, and it matters.

Eval reports, gate verdicts and the compiled IR are **JSON**. A harness that
returns approximate JSON forces:

1. defensive parsing everywhere;
2. a repair pass — hence one more model call;
3. a class of false greens: a badly parsed report whose missing fields are read
   as "no finding".

The third point is the real danger. `structured_output: emulated` in the matrix
is not a footnote: it is a risk factor on the reliability of verdicts, and it
must be requalified by **measurement** at the first conformance run — never by
optimism.

---

## 5. What never moves

`.sdda/python/` — validators, `ir_compiler`, `estimate_budget`, `eval_runner`,
graders, security scans, `compute_status`.

That is **80% of the framework's real value**, and it is Python that runs
identically everywhere. A degraded harness loses orchestration comfort and the
immediacy of the gates; it does not lose validation.

This is what makes the multi-harness promise tenable rather than marketing: the
core depends on no harness because it depends on no LLM.

---

## 6. Parity test

`python .sdda/sdda.py harness-build --check`, run in CI, recomputes every facade
without writing anything and compares it byte for byte with the disk:

1. **Coverage** — every agent and every command in `.sdda/` has its counterpart
   in every facade that is built; an expected file that is missing is drift.
2. **Body identity** — the generated content is identical to what the source
   produces. A divergence means a facade was edited by hand, or the source moved
   without a rebuild.
3. **No orphans** — no facade carries an agent or a command that no longer
   exists in the source (`--prune` deletes them).
4. **Impact report present and up to date** for every harness built: it is part
   of the build plan, and therefore of the comparison. So are the root
   `AGENTS.md` / `GEMINI.md` pointers.

Any gap fails with `[HARNESS_PARITY_DRIFT]` and is fixed with
`python .sdda/sdda.py harness-build --prune`.

---

## 7. Adding a harness

1. Declare its mechanisms in `capability-matrix.yml` (`status: planned`).
2. Write its adapter in `harness_build.py`: a subclass of `Adapter`
   (`out_dir`, `render_agent`, `render_command`, and `emit_agents`,
   `emit_settings` where needed), registered in `ADAPTERS`. A harness declared
   without an adapter is reported as `[ skip ]`, never silently built.
3. Build, read the impact report, **fix what moves to CI**.
4. Run a **conformance run**: the same MISSION end to end on this harness and on
   Claude Code, then compare the verdicts.
5. Only then switch to `status: validated` and announce a protection level.

> `status: planned` means "compilable". Never "validated". The distinction is
> the same as for stack combos, and for the same reason: announcing an
> unmeasured compatibility is the easiest false green to produce and the most
> expensive one to undo.
