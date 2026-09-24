# An agent's data sources — registry, connectors, secrets

Consumed by `architect-data`. Companion to `DATA-ACCESS.md`, which covers the
"the data is in a database" case. This document covers the case that is far
more frequent at the start of a project: **the data is elsewhere, and in
several places at once**.

> A useful reminder before anything else: a source is not a RAG problem because
> it sits in a file. A CSV of orders gets filtered and counted; vectorising it
> produces an agent that can no longer answer "how many". RAG starts where the
> answer is in **prose**, not in records.

> **What runs today.** The model below describes three connectors and about ten
> kinds of stores; the generated runtime (`templates/runtime/python/data/`)
> reads only part of them: the `file` connector, on a `local` store — or on
> **mounted** `smb` / `nfs`, read as a path the OS resolves, mount not checked.
> Everything else is accepted by the parser and **refused at preflight** by
> `preflight_stack_combo` (`[STACK_VALUE_UNIMPLEMENTED]`): the `http-api` and
> `mcp` connectors, and the `s3`, `azure-blob`, `gcs`, `sftp`, `http` and `mcp`
> stores. A valid declaration that nothing executes is exactly the case where
> the agent invents the missing client; the hook stops it before it gets the
> chance. The fiche is `dataaccess/declared-sources.md` (`[python]`).

---

## 1. The model: store, source, manifest

Three objects, and one rule per object.

| Object | Answers | The rule |
|---|---|---|
| **Store** | *where is the data, and with which keys?* | it is the **only** place that touches authentication, and it carries only the **variable name** |
| **Source** | *what is it, and what do we expose of it?* | it carries no URL, no absolute path, no secret — only a `store` and a relative locator |
| **Declaration** | *where is all of this declared?* | inline in `STACK.md ## Active Data Sources`, **versioned** — it carries only variable names; the values live in `workspace/assets/.env`. The only optional manifest: a standard `mcp.json` next to `STACK.md`, imported as is |

This separation is what lets you declare thirty sources without multiplying by
thirty the places where an API key can leak. It is also what makes the egress
allowlist computable: the reachable hosts are exactly those of the stores, and
they can be enumerated without reading a single source.

---

## 2. The connectors

| `connector` | The data comes from | Index possible | Default `trust` | Runtime |
|---|---|---|---|---|
| `file` | an object read from a file store | yes, built at startup | `trusted`, except `free_text` fields | **implemented** |
| `http-api` | a paginated HTTP response | no — every `lookup` is a call | **`untrusted`** | refused at preflight |
| `mcp` | a tool exposed by an MCP server | no | **`untrusted`** | refused at preflight |

The pessimistic default on the last two is not window dressing: an API response
and an MCP tool output are written by a third party, and a string written by a
third party that lands in a model's context is a potential instruction. It is
the attack surface specific to agentic systems (P8), and it exists without RAG.
When these connectors get a client, the IR will say so
(`dataAccess[].connectors`) and the SAFETY GATE will require a non-empty
`egressAllowlist` and an injection suite for them.

### 2.1 File stores

| `kind` | For | What must be checked | Runtime |
|---|---|---|---|
| `local` | a directory on the workstation or in the container | mounted **read-only**; otherwise read-only is only a code convention | **implemented** |
| `smb` | a Windows / CIFS share (`//fs01/ops`) | the mount exists at startup, and survives the night (pitfall no. 7 of the fiche) | read if mounted — reported at preflight, not refused |
| `nfs` | a mounted POSIX share | same | same |
| `s3` | Amazon S3 or compatible (MinIO, Ceph) | `prefix` locked: a bucket without a prefix is a root without a boundary | refused at preflight |
| `azure-blob` | an Azure container | same | refused at preflight |
| `gcs` | a Google bucket | same | refused at preflight |
| `sftp` | a partner's file drop | the host fingerprint is pinned, not accepted on the fly | refused at preflight |

### 2.2 Formats

| Format | What is missing and must be declared |
|---|---|
| `object` / `array` / `jsonl` | the schema — JSON has none |
| `csv` / `tsv` | the schema **and** the types: everything is a string. Plus `delimiter` and `encoding` |
| `xlsx` | the sheet, the header row, and the conversion of Excel dates (numbers) |
| `parquet` | nothing — the schema exists; it is **transcribed** into the frozen schema |

The CSV failure mode deserves to be known by heart: it raises no exception.
`0012345` becomes `12345`, a postcode loses its leading zero, a join finds
nothing any more, and the agent answers "this customer does not exist".

### 2.3 From frozen schema to tool — what the pipeline generates

A declared source becomes a tool without an LLM writing its contract:

1. **The frozen schema.** `python .sdda/sdda.py gen-source-tools --infer --source {id}`
   proposes it from the data; a human **reviews** it. It lives in
   `workspace/src/{App}/data/schemas/{id}.schema.json` and ships with the
   application: it is a runtime asset, revalidated at startup on
   `SourceSchemaCheckSample` records.
2. **The contracts, in PHASE 2.** `gen-source-tools --write --scope contracts`
   (`/sdda-topology` STEP 4.bis, before IR compilation): the IR and G2 see source
   tools like any other. A source without a frozen schema is
   `[DATA_SOURCE_SCHEMA_MISSING]` — a human STOP; there is nothing to contract.
3. **The code, in PHASE 3.** `gen-source-tools --write --scope code`, right
   before `dev-data` is spawned: wrappers, the `data/` runtime, `sources.json`,
   `tool_specs.json`. `dev-data` completes around it without editing what was
   generated, and ends with `gen-source-tools --check --scope code`. A contract
   missing at this stage is `[DATA_TOOL_MISSING]`: it is never created after the
   fact, since it would describe a tool the IR and G2 never saw.

---

## 3. Decision matrix

| Situation | Answer |
|---|---|
| The data is in a database, known needs | `view-per-agent` (see `DATA-ACCESS.md`) |
| File exports, a directory, known needs | **`declared-sources`**, `file` connector |
| A CRM / ERP behind a governed REST API | `declared-sources`, `http-api` connector — **refused at preflight until it has a runtime client** |
| An internal MCP server that already exposes business **reads** | `declared-sources`, `mcp` connector — **same status** |
| An MCP server that exposes **actions** (create, send, refund) | `tools/mcp.md` + one tool contract per action — not a source |
| All of the above at once | **`declared-sources`**: one registry, N stores, N sources |
| The data is documents, not records | `RAG-PATTERNS.md` |
| Records **and** documents | composition: `declared-sources` **+** RAG, two distinct tools |
| A write is involved | the `repository-tools` strategy (catalogued, no fiche yet) — never this stack, which is read-only by construction |

The "all at once" case is the normal case, not the exception. It is the reason
the registry exists: five integrations written separately yield five different
ideas of safety, and it is always the weakest one that defines the system's
real level. Today this case is built on files; read access to an API or an MCP
server goes, until they have a client, through an export dropped onto a `local`
store.

---

## 4. Secrets: names in STACK.md, values in `assets/.env`

The rule fits in one sentence: **a declaration carries the name of a variable,
a gitignored `.env` file carries its value, and the framework never reads the
value.** The human drops that file where they drop the rest of their inputs,
`workspace/assets/.env`; `python .sdda/sdda.py install-env` **copies** it to
`workspace/src/{App}/.env`, without an LLM, because that is where the
application ships from as an executable or a container (SDD_Pro convention).
`SourceSecretsFile` (default `.env`) resolves relative to
`workspace/src/{App}/`, that is, to this copy. No agent reads either file:
`preflight_forbidden_reads` and `preflight_bash_ownership` refuse with
`[SECRET_READ_FORBIDDEN]`, `architect-data` included.

```yaml
# workspace/stack/STACK.md ## Active Data Sources   <- VERSIONNÉ (noms seulement)
Stores:
  - id: crm_api
    kind: http
    base_url: https://crm.example.com/api/v2
    auth: { mode: api-key, header: X-API-Key, key_env: CRM_API_KEY }
```

```bash
# workspace/assets/.env   <- GITIGNORÉ, jamais commité, jamais lu par un agent
#                           (copié vers workspace/src/{App}/.env par install-env)
CRM_API_KEY=…
```

(The example shows the shape of a key; a `kind: http` store is itself refused
at preflight until it has a client.)

What the validator (`python .sdda/sdda.py validate-data-access`) enforces, and
why:

| Check | Class | The failure mode it prevents |
|---|---|---|
| Every `*_env` key carries a name, not a value | `[DATA_SECRET_INLINE]` | a committed API key, which stays in history after deletion |
| `auth` is mandatory, `mode: none` included | `[DATA_AUTH_INCOMPLETE]` | a store "public" by oversight, which nobody decided should be |
| The application's secrets file exists as soon as a variable is cited | `[DATA_SECRET_FILE_MISSING]` | a forgotten `install-env`: the application ships without any key |
| Every cited variable exists in that file | `[DATA_SECRET_VAR_UNDECLARED]` | an anonymous call that returns `200` and zero rows — the agent answers "I can't find anything" |
| The secrets file is in `.gitignore` | `[DATA_SECRET_FILE_UNIGNORED]` | the commit that arrives three weeks later |
| A literal `env` in an imported `mcp.json` | `[DATA_SECRET_INLINE]` | the most frequent leak in this family of files |

The framework parses the left-hand side of the `=` and nothing else. A value
that never enters memory cannot be copied into a gate report — which, for its
part, is not gitignored everywhere. A value written in clear in STACK.md itself
is `[STACK_SECRET_IN_CLEAR]` at `smoke-check`.

---

## 5. The safety envelope — mandatory as soon as a source is declared

Declared in `STACK.md ## Active Data Sources`, carried in the IR
(`dataAccess[].envelope`), checked by the TOOL GATE and the SAFETY GATE — on
the declaration side by `validate-data-access`, on the code side by
`validate-envelope` (the single envelope `lookup_record` / `search_records` /
`count_records`, identity taken from the execution context, the bounds, and
**no** file write under `data/`). It is the term-for-term transposition of the
DB envelope.

| Key | Default | Reason |
|---|---|---|
| `SourceAgentRole` | `readonly` | the only accepted value; a write belongs to `repository-tools` |
| `SourceReadTimeoutMs` | 5000 | an agent read that lasts is a read that has gone off the rails |
| `SourceMaxRecordsReturned` | 200 | protects the token budget as much as the source; beyond it, `truncated: true` |
| `SourceMaxObjectBytes` | 52428800 | a larger file or response is refused |
| `SourceSchemaCheckSample` | 500 | records revalidated against the frozen schema at startup: drift shows at boot, not in production |
| `SourceAllowedSources` | allowlist of `id`s (empty = all declared sources) | a source outside the list does not exist for the application |
| `SourceAllowedStores` | allowlist of `id`s (empty = all declared stores) | importing an MCP file wires nothing by itself |
| `SourceEgressAllowlist` | allowlist of **hosts** | empty = no network egress, never "everything allowed" |
| `SourceForbiddenOps` | `WRITE, DELETE, EXEC, SYMLINK_FOLLOW, UNDECLARED_EGRESS` | enforced by the runtime, not promised by the prompt |
| `SourceMaxStalenessHours` | 24 | beyond it → data served with `stale: true` and `as_of`, which the agent must state; neither a log nor an exception |
| `SourceQueryLogging` | `full` | without the log, no post-mortem is possible |

The last two values of `SourceForbiddenOps` deserve a second reading:
`SYMLINK_FOLLOW` and `UNDECLARED_EGRESS` are the two boundary bypasses that
**do not look like writes**. A symbolic link inside a root, and a call to a host
nobody declared, both leave the perimeter without ever modifying a byte. The
runtime refuses to follow a symbolic link at indexing time.

**Identity filtering**: if data is partitioned per user or per tenant, the
filter is applied in the source's `required_filter:` or in the parameter
injected by the runtime — never delegated to the model. A post-generation
filter is a leak with one extra step: the model has already seen the row, it is
in the trace, it is in the next turn's context. It is a blocking
`review-safety` finding.

---

## 6. What is dated, and why it is the first cause of wrong answers

A declared source is **always** a snapshot: an export has a date, a cached
response has a date, an MCP call has a date. The tool returns `as_of` in
**every** response, and `stale` when the snapshot exceeds
`max_staleness_hours`.

The textbook case, and a silent one: "my order hasn't arrived for 10 days",
computed on tracking exported 3 days ago, yields a delay that is wrong by 3
days — **in the reassuring direction**. No unit test sees it, no log reports
it, and the customer hangs up satisfied with a wrong answer.

---

## 7. What stays out of this stack's reach

| Need | Where it goes |
|---|---|
| Writing to a source | the `repository-tools` strategy, on a real database, with idempotency and a side-effect class |
| Joining two sources | an SQL view, or an aggregate precomputed at ingestion — never two calls the model stitches together |
| Searching prose | `rag/*.md` |
| More than 200 MB or ~2 M records per source | a database, and `view-per-agent.md` |
| Calling an MCP tool that **acts** | `tools/mcp.md`: an action is a contracted tool, not a source |
