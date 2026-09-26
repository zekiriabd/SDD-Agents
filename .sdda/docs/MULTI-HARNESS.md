# Multi-harness compilation

SDD_Agents is written **once**, in `.sdda/`, and compiled into the facades each
harness knows how to read. The mechanism comes from SDD_Pro, with one extra
constraint that is specific to agentic systems.

Machine SSoT: [`.sdda/capability-matrix.yml`](../capability-matrix.yml).
Every location, format and limit below comes from the harness's **official**
documentation (URLs in the matrix and next to the constants of
`harness_build.py`). What that documentation does not say is marked **not
verified** and is not coded.

## 0. Status — read this first

| Harness | Status | Gates blocking at runtime |
|---|---|---|
| **Claude Code** | **supported** — reference harness, level A | yes: `PreToolUse` / `SubagentStop` hooks in `.claude/settings.json` |
| **Codex CLI** | **experimental** — `status: planned`, level B | **partly** (`runtime_hooks: partial`): `apply_patch` refused in protected zones; the rest in CI |
| **Gemini CLI** | **experimental** — `status: planned`, level B | **partly** (`runtime_hooks: partial`): `write_file`/`replace` in protected zones, and the ten spawn gates (not verified by a run); the rest in CI |
| **Antigravity** | **experimental** — `status: planned`, level B | **no** (`ci_fallback`): hooks exist but their payload is undocumented |

"Experimental" means exactly this: the facade **compiles**, is kept up to date
(`harness-build --check`) and is re-read by its harness's parser
(`framework-smoke`, check `facades.harnesses`), but no conformance run has ever
executed it end to end. Whatever no hook judges on a harness — an
out-of-ownership write by a given agent, an agent wired before its TOOL GATE
under Codex, an inline prompt — is stopped by **nothing** at the moment of the
action: run `python .sdda/sdda.py framework-smoke` before trusting a result.

**The shared limit, stated.** Neither Codex nor Gemini CLI passes the hook the
**identity of the agent** making the call (an `agent_type` equivalent: not
documented). Everything judged "per agent" — the ownership matrix,
`forbidden_reads`, `.env` reads, the shell — therefore cannot be carried over
at runtime; only what is judged regardless of the author can: the **protected
zones** (gate reports, baselines, audit). This is not a missing setting; it is
information the harness does not give.

Root files: Codex CLI reads `AGENTS.md`, Gemini CLI reads `GEMINI.md`,
Antigravity reads **both**. `harness_build` writes the same neutral pointer into
them, which names each harness's facade and repeats this status
(`tests/test_harness_root_pointers.py`); `GEMINI.md` **imports**
`.gemini/GEMINI.md` (`@./.gemini/GEMINI.md`), which Gemini CLI then loads
natively.

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
  ┌────────────┬──────────────┬──────────────┬───────────────────┐
  │  .claude/  │   .codex/    │   .gemini/   │   .agents/        │
  │  CLAUDE.md │  AGENTS.md   │  GEMINI.md   │  rules/*.md       │
  │  agents/   │  agents/     │  agents/     │  agents/          │
  │   *.md     │   *.toml     │   *.md       │   *.md            │
  │  commands/ │  hooks.json  │  commands/   │  skills/ ◄─ Codex │
  │  settings  │              │   *.toml     │   aussi           │
  │            │              │  settings    │                   │
  └────────────┴──────────────┴──────────────┴───────────────────┘
   + harness-impact.md dans chaque façade
   + AGENTS.md / GEMINI.md racine (pointeurs)
```

**Facades are generated, never edited.** A direct change inside `.claude/` is
overwritten by the next build, and the parity check catches it (§6).

---

## 2. What gets compiled, and how

| Source | Claude Code | Codex CLI | Gemini CLI | Antigravity |
|---|---|---|---|---|
| `agents/{a}.md` | `.claude/agents/{a}.md` — `model:` resolved from the tier | `.codex/agents/{a}.toml` — `name`, `description`, `developer_instructions`, `model`, `sandbox_mode` | `.gemini/agents/{a}.md` — translated `tools` (`read_file`, `write_file`, `replace`…), `model` | `.agents/agents/{a}.md` — `model` (`pro`/`flash`), `subagent: true`, **no** `tools` |
| `commands/{c}.md` | `.claude/commands/{c}.md` | skill `.agents/skills/{c}/SKILL.md` (`$sdda-…`) | `.gemini/commands/{c}.toml` | skill `.agents/skills/{c}/SKILL.md` (`/sdda-…`) — the same as Codex |
| `rules/*.md` | `@`-referenced | inlined ("Read ce fichier") | inlined | inlined |
| memory file | `.claude/CLAUDE.md` | `.codex/AGENTS.md`, pointed to by `AGENTS.md` | `.gemini/GEMINI.md`, **imported** by `GEMINI.md` | `.agents/rules/sdda-architecture-*.md`, ≤ 24,000 bytes each, `trigger: model_decision` |
| hooks | `.claude/settings.json` — all of them, blocking, plus native `deny` rules on reading `.env` files | `.codex/hooks.json` — `preflight_ownership` on `apply_patch` | `.gemini/settings.json` — `BeforeTool`: ownership on `write_file`/`replace`, spawn gates on each agent's tool | none |
| models (`tier_models`) | `opus` / `sonnet` / `haiku` | `gpt-6-astra` / `gpt-6-sol` / `gpt-6-luna` | `gemini-3-pro-preview` (deep, balanced) / `gemini-3-flash-preview` | `pro` (deep, balanced) / `flash` |

Why these choices, and what they fix:

- **Codex** reads custom prompts only under `~/.codex/prompts` and deprecates
  them: the former `.codex/prompts/` facade was read by nobody. Skills in
  `.agents/skills/` are looked up from the current directory to the repository
  root. `AGENTS.md` is silently truncated beyond 32 KiB
  (`project_doc_max_bytes`): the pointer stays small, the facade (59 KB) is read
  on instruction.
- **Gemini CLI** executes `@{…}` (file injection) and `!{…}` (shell) in a
  command prompt: `recall@{k}` read the file `k` on every `/sdda-build`. The
  compiler inserts a space (`@ {k}`) — no escape is documented. Sub-agents are
  native (`.gemini/agents/`), exposed to the main agent as a tool of the same
  name.
- **Antigravity** read nothing of `.gemini/`. Workflows are retired on
  1 November 2026: commands are skills, which Antigravity shares with Codex (same
  file, byte for byte). The list of tool names is not published and a wrong
  name can hang a sub-agent: `tools` is not emitted, the allowed tools are
  written in the body as an instruction.
- **Claude Code**: `$0`, `$1`… in a command are argument placeholders; the
  sample amounts (`$0.09`) are escaped (`\$0.09`), otherwise the first argument
  replaced them and the following ones (`--resume`) never reached the model. A
  command does not accept `name:`.

Each harness's memory file **is** the architecture: it is compiled from
`.sdda/ARCHITECTURE.fr.md`, because the agents work in French
(`.sdda/ARCHITECTURE.md` is its English reference, for human readers).

The business body is **identical** everywhere: only the envelope and the
loading policy change. On a harness without lazy loading (`at_include` ≠
`native`), every `@.sdda/{file}` reference is rewritten as
"`` `.sdda/{file}` `` (Read ce fichier avant de poursuivre)". The fallback is
verbose on purpose: a silent reference would produce an agent that believes it
has read a rule it has never seen. `sync_counters` counter markers are stripped
at compile time, for every harness: an agent pays no tokens for documentation
upkeep.

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

**On the other harnesses, what carries over and what does not.** Gemini CLI
payloads (`write_file`, `grep_search`, `run_shell_command`, a sub-agent's tool)
and Codex payloads (`apply_patch`, whose `*** Add/Update/Delete File:` grammar
gives the files touched) are translated into the Claude Code shape by
`_hook.foreign_payloads`, switched on by the `SDDA_HARNESS` variable the
generated command sets. Then:

| Hook | Codex CLI | Gemini CLI | Antigravity |
|---|---|---|---|
| `preflight_ownership` | **wired** (`^apply_patch$`) — protected zones only | **wired** (`write_file\|replace`) — protected zones only | no |
| the ten spawn hooks | no — the `spawn_agent` field naming the launched agent is undocumented | **wired** on each agent's tool — firing and parameters not verified | no |
| `preflight_bash_ownership`, `preflight_forbidden_reads` | no — author identity missing | no — author identity missing | no |
| `SubagentStop` | no — agent and effect of exit code 2 undocumented | no — no such event | no |
| common reason | | | `toolCall.args` arguments and exit codes undocumented; refusal through a JSON `decision` on stdout |

Other **not verified** points: the shell in which Codex and Gemini CLI launch a
hook command on Windows (the generated commands are POSIX); the characters
allowed in a Codex agent `name` (the official examples use snake_case). Under
Codex, project hooks load only once the `.codex/` layer is trusted; Gemini CLI
fingerprints its hooks and asks for confirmation whenever a command changes.

The shell hook covers **PowerShell** with a dedicated dialect, not just Git
Bash. Two settings decide what happens when a hook does not get to judge:

- **`SDDA_PYTHON`** selects the interpreter (`python` as the fallback). A hook
  that fails to start returns a code ∉ {0, 2}, which the harness treats as an
  **allow**: the launch failure is therefore reported on stderr;
- **`SDDA_HOOKS_STRICT=1`** (CI): a hook that crashes or fails to start
  **refuses** (`[HOOK_FAILED]`) instead of letting the call through.

`python .sdda/sdda.py hooks-selfcheck` runs every wired command — from
`.claude/settings.json`, `.gemini/settings.json` and `.codex/hooks.json` —
exactly as the harness will launch it, with a harmless payload (must return 0)
and, for the ownership hooks, a payload that must be refused (must return 2) in
the harness's own dialect. `framework-smoke` checks that a hook is wired; only
`hooks-selfcheck` proves that it runs.

Where a hook is not wired, **it does not disappear — it moves** to the CI gate
and to the deterministic enforcers of the mixed invariants.

**The consequence must be stated, not dressed up**: whatever no hook judges is
stopped by nothing at the moment of the action. CI catches it — later, after
the work has been done on a false basis.

That is why the impact report is **mandatory on every build**:

> `harness_build.py` refuses to produce a facade without emitting its report
> (`{facade}/harness-impact.md`), which lists the fate of every hook on the
> harness (wired, degraded, absent — and why). Any invariant whose enforcers are
> **all** unwired hooks appears there as **"appliqué en différé (CI)"**
> (enforced later, in CI); an invariant with a degraded wired hook, as "en
> partie au runtime" (partly at runtime); an invariant that keeps a
> deterministic enforcer, as "partiellement différé" (partly deferred).
> Claiming it is enforced at runtime would be exactly the doc-theater that
> `INVARIANTS.yml` exists to prevent.

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
   exists in the source (`--prune` deletes them). In a directory the human may
   also fill (`.agents/skills/`, `.agents/rules/`), only a file carrying the
   generation mark is judged: a hand-written skill is never deleted.
4. **Impact report present and up to date** for every harness built: it is part
   of the build plan, and therefore of the comparison. So are the root
   `AGENTS.md` / `GEMINI.md` pointers.

`framework-smoke` adds what parity does not prove: that each facade is
**re-read** by its harness (check `facades.harnesses`) — Codex and Gemini TOML
re-read by `tomllib`, frontmatters in strict YAML, Gemini names and tools,
`trigger` and size of the Antigravity rules, size of the root pointers, no
`@{…}`/`!{…}` in a Gemini command, no stray `@` import in `.gemini/GEMINI.md`.

Any gap fails with `[HARNESS_PARITY_DRIFT]` and is fixed with
`python .sdda/sdda.py harness-build --prune`.

---

## 7. Adding a harness

1. Declare its mechanisms in `capability-matrix.yml` (`status: planned`),
   each value sourced from the harness's official documentation.
2. Write its adapter in `harness_build.py`: a subclass of `Adapter`
   (`out_dir`, `root_pointers`, `managed`, `render_agent_file`,
   `render_command`, and `emit_commands`, `emit_memory_file`, `emit_settings`
   where needed), registered in `ADAPTERS`. A harness declared without an
   adapter is reported as `[ skip ]`, never silently built.
3. For its hooks: translate its payload in `_hook.foreign_payloads`, wire only
   what that payload lets a hook judge, and give a reason for every missing hook
   (`HookPort`) — it ends up in the impact report.
4. Build, read the impact report, **fix what moves to CI**.
5. Run a **conformance run**: the same MISSION end to end on this harness and on
   Claude Code, then compare the verdicts.
6. Only then switch to `status: validated` and announce a protection level.

> `status: planned` means "compilable". Never "validated". The distinction is
> the same as for stack combos, and for the same reason: announcing an
> unmeasured compatibility is the easiest false green to produce and the most
> expensive one to undo.
