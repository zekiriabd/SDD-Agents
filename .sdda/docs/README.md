# 📚 SDD_Agents documentation

> **Spec Driven Development for agentic applications, multi-harness** (Claude
> Code, Codex, Gemini CLI) — a framework that turns a MISSION into a tested
> system of LLM agents through **<!--sdda:count agents-->23<!--/sdda:count--> Developer Agents**, deterministic Python
> orchestration (**0 token**) and **9 gates**.

This is the documentation hub. Each document has one purpose and one audience.
French version: [README.fr.md](README.fr.md).

## Language convention

**English is the default, French is the twin.**

| File | Language | Role |
|---|---|---|
| `NAME.md` | English | the reference page, the one every link points to |
| `NAME.fr.md` | French | translation of the same page, same technical content |

Technical content — identifiers, `[CLASS]` codes, paths, flags, section
numbers, counter markers, code blocks — is identical in both languages, and the
headings are the same, translated, in the same order. An English page links to
English pages; a French page links to the French twins. A file is never renamed
`NAME.en.md`: the English page keeps the name every link already uses.

Two areas are **deliberately French-only** and follow no twin rule: the
prompts consumed by LLMs (`.sdda/agents/`, `.sdda/commands/`, `.sdda/rules/`,
`.sdda/stacks/`, `.sdda/templates/`, `.sdda/digests/`) and the generated
harness facades (`.claude/`, `.codex/`, `.gemini/`). They are operational
content, not documentation, and SDD_Pro applies the same rule.

**The architecture is the one exception that has both.** `harness_build.py`
compiles `.claude/CLAUDE.md` (and the Codex / Gemini memory files) from
`ARCHITECTURE.fr.md`, because the agents read French like the rest of their
prompts. `ARCHITECTURE.md` is the English reference for human readers. The two
are kept in step by the same parity check as every other twin (see below).

---

## 🚀 I want to understand the framework

| Step | Goal | Document | Languages |
|---|---|---|---|
| **1** | The 12 founding principles | [../PHILOSOPHY.md](../PHILOSOPHY.md) | 🇬🇧 EN · 🇫🇷 FR |
| **2** | Tree, pipeline, 9 gates, harness/provider abstraction | [../ARCHITECTURE.md](../ARCHITECTURE.md) | 🇬🇧 EN · 🇫🇷 FR |
| **3** | The closed vocabulary: MISSION, CAP, AGENT, TOOL, RETRIEVER… | [DOMAIN-MODEL.md](DOMAIN-MODEL.md) | 🇬🇧 EN · 🇫🇷 FR |
| **4** | State machine Draft → Approved, derived from the gates | [LIFECYCLE.md](LIFECYCLE.md) | 🇬🇧 EN · 🇫🇷 FR |
| **5** | What is inherited from SDD_Pro, refused, or added | [SDD-PRO-INHERITANCE.md](SDD-PRO-INHERITANCE.md) | 🇬🇧 EN · 🇫🇷 FR |

---

## 🏗 I am designing a system (architects)

| Topic | Consumed by | Document | Languages |
|---|---|---|---|
| Orchestration patterns — catalogue and selection matrix | `architect-topology` | [ORCHESTRATION-PATTERNS.md](ORCHESTRATION-PATTERNS.md) | 🇬🇧 EN · 🇫🇷 FR |
| RAG, search and retrieval patterns — catalogue and gate metrics | `architect-rag` | [RAG-PATTERNS.md](RAG-PATTERNS.md) | 🇬🇧 EN · 🇫🇷 FR |
| Memory patterns — scopes, costs, memory as a persistent attack surface | `architect-memory` | [MEMORY-PATTERNS.md](MEMORY-PATTERNS.md) | 🇬🇧 EN · 🇫🇷 FR |
| Database access from an agent — a security decision | `architect-data` | [DATA-ACCESS.md](DATA-ACCESS.md) | 🇬🇧 EN · 🇫🇷 FR |
| Data sources outside a database — registry, connectors, secrets | `architect-data` | [DATA-SOURCES.md](DATA-SOURCES.md) | 🇬🇧 EN · 🇫🇷 FR |

Machine SSoT behind these catalogues: [../registry/patterns.registry.json](../registry/patterns.registry.json)
and [../registry/compatibility.matrix.json](../registry/compatibility.matrix.json).

---

## ⚙️ I want to know how it is built

| Topic | Document | Languages |
|---|---|---|
| The Agentic IR — the intermediate representation that makes multi-framework deterministic | [AGENTIC-IR.md](AGENTIC-IR.md) | 🇬🇧 EN · 🇫🇷 FR |
| The <!--sdda:count agents-->23<!--/sdda:count--> Developer Agents and their internal orchestration | [AGENT-ROSTER.md](AGENT-ROSTER.md) | 🇬🇧 EN · 🇫🇷 FR |
| Compilation to Claude Code / Codex / Gemini CLI | [MULTI-HARNESS.md](MULTI-HARNESS.md) | 🇬🇧 EN · 🇫🇷 FR |
| Testing and evaluation — the L0→L9 pyramid, *a test asserts, an eval scores* | [TESTING-AND-EVAL.md](TESTING-AND-EVAL.md) | 🇬🇧 EN · 🇫🇷 FR |
| The <!--sdda:count invariants-->21<!--/sdda:count--> load-bearing invariants and their enforcers | [../INVARIANTS.yml](../INVARIANTS.yml) | machine file |
| The deterministic Python layer | [../python/README.md](../python/README.md) | 🇬🇧 EN · 🇫🇷 FR |
| Test fixtures | [../python/tests/fixtures/README.md](../python/tests/fixtures/README.md) | 🇬🇧 EN · 🇫🇷 FR |

---

## 🗺 Where the project is going

| Topic | Document | Languages |
|---|---|---|
| Build order and the MVP — one combination validated end to end before twelve are announced | [ROADMAP.md](ROADMAP.md) | 🇬🇧 EN · 🇫🇷 FR |
| Planned deterministic scripts — **generated** in both languages, do not edit by hand | [PLANNED-SCRIPTS.md](PLANNED-SCRIPTS.md) | 🇬🇧 EN · 🇫🇷 FR |

---

## Checking that this hub is honest

`framework_smoke.py` scans `.sdda/*.md` and `.sdda/docs/*.md`, this file
included: every error class cited in normative prose must have a real emitter,
and every internal reference must resolve. A `.fr.md` twin is scanned under the
same rule.

Its `docs.parity` check holds the twins together: for every `NAME.md` /
`NAME.fr.md` pair, the two pages must cite the same `[CLASS]` codes, carry the
same number of headings in the same order, the same `sdda:count` markers and
the same code blocks. A translation that drops a section, a class or a command
fails the smoke instead of drifting quietly. Run it from the repository root:

```bash
python .sdda/sdda.py framework-smoke
```
