# Planned scripts

> **Generated** by `sdda_admin/planned_scripts.py`. Do not edit by hand.

Inventory of the deterministic scripts that agents, commands and
invariants name before they exist. It is the backlog of the
implementation lots, not a list of bugs.

**Reading**: the number of callers is a signal. A script requested by
six places is an established need; a script requested once may have
been invented in passing and deserves a question before it is written.

- **77** written · **0** to write · **0** cited without declaration

**Declared**: the prompt that calls the script says « Planifié » on the
following line, with what to do while it is missing. A missing script
cited without saying so makes the agent believe it has a tool — and it
invents the output. `framework_smoke` fails on every silent reference.

## Names cited without a path

These scripts are named somewhere, but nothing says where they live.
Attach them to a package, or merge them into an existing script that
already does the job.

| Name | Cited by |
|---|---|
| `base.py` | `agents/dev-orchestration.md` |
| `bootstrap.py` | `commands/sdda-bootstrap.md` |
| `framework_smoke.py` | `INVARIANTS.yml`, `rules/error-classification.md` |
| `router.py` | `agents/dev-orchestration.md` |
| `run_service.py` | `agents/dev-app.md` |
| `sequential.py` | `agents/dev-orchestration.md` |

