# Test fixtures

`project_ok/` is a complete and VALID project, in the workspace v6 layout: G0,
G1, G2 (topology, IR, budget) and the datasets all pass green, without a single
token.

The other `project_*` folders are **overlays**: they contain only the files that
differ from `project_ok/`. `conftest.py` copies `project_ok/` into a temporary
directory, then lays the overlay on top
(`make_project(tmp_path, "project_unbounded")`). A fixture `.gitignore` is
stored as `_gitignore` and renamed on copy: it is tested content, but git would
honour it and keep the fixture's own files out of the repository.

**Negative** overlays break exactly one thing, and the test checks the exact
`[CLASS]` the validator emits:

| Overlay | What is broken | Expected class |
|---|---|---|
| `project_ac_unmeasurable` | a CAP AC in prose, with no metric/threshold/dataset | `[AC_NOT_EVALUABLE]` |
| `project_unbounded` | the classify <-> billing cycle has only "free" edges (`-.->`) | `[UNBOUNDED_LOOP]` |
| `project_framework_leak` | `StateGraph` / LangGraph inside an agent contract | `[FRAMEWORK_LEAK_IN_CONTRACT]` |
| `project_tool_scope_excess` | the classifier carries `1-invoice-lookup`, required by none of its CAPs | `[TOOL_SCOPE_EXCESS]` |
| `project_side_effect_undeclared` | the `external-side-effect` tool has no safety strategy | `[SIDE_EFFECT_UNDECLARED]` |
| `project_budget_exceeded` | a `CostPerRunHardCapUsd` cap below the worst case | `[BUDGET_EXCEEDED_ESTIMATE]` |
| `project_holdout_overlap` | a holdout item has the same `input` as a golden item | `[HOLDOUT_NOT_DISJOINT]` |
| `project_status_unbacked` | `Status: Tested` written by hand in a CAP | `[STATUS_UNBACKED]` |

One overlay is **positive**: it adds a valid variant rather than a failure.

| Overlay | What it adds | Used by |
|---|---|---|
| `project_declared_sources` | a `declared-sources` `STACK.md`: a `local` store (`workspace/assets/exports`), an `http` store, an `mcp.json` manifest, three sources (`file`, `mcp`, `http-api`), their frozen schemas under `src/SupportAssistant/data/schemas/` and a test `.env` | `corpus-profile`, `gen-app-skeleton`, `adversarial-target-check` |
