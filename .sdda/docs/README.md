# 📚 SDD_Agents documentation

> **Spec Driven Development for agentic applications, multi-harness** (Claude
> Code, Codex, Gemini CLI) — a framework that turns a MISSION into a tested
> system of LLM agents through **<!--sdda:count agents-->23<!--/sdda:count--> Developer Agents**, deterministic Python
> orchestration (**0 token**) and **9 gates**.

This is the documentation hub. Each document has one purpose and one audience.

## Language convention

**English is the default, French is the twin.**

| File | Language | Role |
|---|---|---|
| `NAME.md` | English | the reference page, the one every link points to |
| `NAME.fr.md` | French | translation of the same page, same technical content |

Technical content — identifiers, `[CLASS]` codes, paths, flags, section
numbers — is identical in both languages. When a French twin is missing, the
English page is the only reference. When an English page is still marked
*French only* below, the French page is the reference until it is translated.

Two areas are **deliberately French-only** and follow no twin rule: the
prompts consumed by LLMs (`.sdda/agents/`, `.sdda/commands/`, `.sdda/rules/`,
`.sdda/stacks/`, `.sdda/templates/`, `.sdda/digests/`) and the generated
harness facades (`.claude/`, `.codex/`, `.gemini/`). They are operational
content, not documentation, and SDD_Pro applies the same rule.

`ARCHITECTURE.md` is the source `harness_build.py` compiles into
`.claude/CLAUDE.md`, which agents read in French. Its language is therefore
tied to the prompts, not to this convention; the decision is tracked in the
ROADMAP, not made silently.

---

## 🚀 I want to understand the framework

| Step | Goal | Document | Languages |
|---|---|---|---|
| **1** | The 12 founding principles | [../PHILOSOPHY.md](../PHILOSOPHY.md) | 🇫🇷 French only |
| **2** | Tree, pipeline, 9 gates, harness/provider abstraction | [../ARCHITECTURE.md](../ARCHITECTURE.md) | 🇫🇷 French only |
| **3** | The closed vocabulary: MISSION, CAP, AGENT, TOOL, RETRIEVER… | [DOMAIN-MODEL.md](DOMAIN-MODEL.md) | 🇫🇷 French only |
| **4** | State machine Draft → Approved, derived from the gates | [LIFECYCLE.md](LIFECYCLE.md) | 🇫🇷 French only |
| **5** | What is inherited from SDD_Pro, refused, or added | [SDD-PRO-INHERITANCE.md](SDD-PRO-INHERITANCE.md) | 🇫🇷 French only |

---

## 🏗 I am designing a system (architects)

| Topic | Consumed by | Document | Languages |
|---|---|---|---|
| Orchestration patterns — catalogue and selection matrix | `architect-topology` | [ORCHESTRATION-PATTERNS.md](ORCHESTRATION-PATTERNS.md) | 🇫🇷 French only |
| RAG, search and retrieval patterns — catalogue and gate metrics | `architect-rag` | [RAG-PATTERNS.md](RAG-PATTERNS.md) | 🇫🇷 French only |
| Memory patterns — scopes, costs, memory as a persistent attack surface | `architect-memory` | [MEMORY-PATTERNS.md](MEMORY-PATTERNS.md) | 🇫🇷 French only |
| Database access from an agent — a security decision | `architect-data` | [DATA-ACCESS.md](DATA-ACCESS.md) | 🇫🇷 French only |
| Data sources outside a database — registry, connectors, secrets | `architect-data` | [DATA-SOURCES.md](DATA-SOURCES.md) | 🇫🇷 French only |

Machine SSoT behind these catalogues: [../registry/patterns.registry.json](../registry/patterns.registry.json)
and [../registry/compatibility.matrix.json](../registry/compatibility.matrix.json).

---

## ⚙️ I want to know how it is built

| Topic | Document | Languages |
|---|---|---|
| The Agentic IR — the intermediate representation that makes multi-framework deterministic | [AGENTIC-IR.md](AGENTIC-IR.md) | 🇫🇷 French only |
| The <!--sdda:count agents-->23<!--/sdda:count--> Developer Agents and their internal orchestration | [AGENT-ROSTER.md](AGENT-ROSTER.md) | 🇫🇷 French only |
| Compilation to Claude Code / Codex / Gemini CLI | [MULTI-HARNESS.md](MULTI-HARNESS.md) | 🇫🇷 French only |
| Testing and evaluation — the L0→L9 pyramid, *a test asserts, an eval scores* | [TESTING-AND-EVAL.md](TESTING-AND-EVAL.md) | 🇫🇷 French only |
| The <!--sdda:count invariants-->21<!--/sdda:count--> load-bearing invariants and their enforcers | [../INVARIANTS.yml](../INVARIANTS.yml) | machine file |
| The deterministic Python layer | [../python/README.md](../python/README.md) | 🇫🇷 French only |
| Test fixtures | [../python/tests/fixtures/README.md](../python/tests/fixtures/README.md) | 🇫🇷 French only |

---

## 🗺 Where the project is going

| Topic | Document | Languages |
|---|---|---|
| Build order and the MVP — one combination validated end to end before twelve are announced | [ROADMAP.md](ROADMAP.md) | 🇫🇷 French only |
| Planned deterministic scripts — **generated**, do not edit by hand | [PLANNED-SCRIPTS.md](PLANNED-SCRIPTS.md) | 🇫🇷 French only |

---

## Checking that this hub is honest

`framework_smoke.py` scans `.sdda/*.md` and `.sdda/docs/*.md`, this file
included: every error class cited in normative prose must have a real emitter,
and every internal reference must resolve. A `.fr.md` twin is scanned under the
same rule. Run it from the repository root:

```bash
python .sdda/sdda.py framework-smoke
```
