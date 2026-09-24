# Changelog

All notable changes to SDD_Agents are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the `sdda`
package under `.sdda/python/` follows [Semantic Versioning](https://semver.org/).
The framework is in `design-phase`: no stack combination has been validated end
to end (`registry/compatibility.matrix.json`), and minor versions may still
break the workspace layout — `python .sdda/sdda.py migrate-workspace` carries a
workspace forward.

A release is cut by pushing a `v*` tag whose version equals
`.sdda/python/pyproject.toml` and `sdda_lib.__version__`
(`.github/workflows/release.yml` refuses anything else).

## [Unreleased]

### Added

- `LICENSE` (MIT) at the repository root; the `sdda` wheel and sdist embed it,
  and `pyproject.toml` declares the SPDX expression `MIT`.
- `CHANGELOG.md`, a tag-triggered release workflow (wheel + sdist attached to a
  GitHub Release, no PyPI upload) and Dependabot for GitHub Actions and pip.

### Changed

- CI actions are pinned by commit SHA, and CI exports `SDDA_HOOKS_STRICT=1`
  (no effect until the hooks read it).

### Known debt

- Framework documentation under `.sdda/docs/` is still largely French-only;
  the English/French twin convention (`.sdda/docs/README.md`) is applied to the
  hub and the root READMEs, the remaining documents are a separate
  translation lot.

## [0.1.0] - 2026-09-24

First version of the framework. Not tagged yet: it describes the state of the
`next` branch the day the release tooling arrived.

### Added

- **Framework layer** (`.sdda/`): 23 Developer Agents, 11 slash commands,
  operational rules, 21 load-bearing invariants with their on-disk enforcers,
  a closed `[CLASS]` error taxonomy regenerated from real emitters, and a stack
  catalogue of 45 sheets (Python, .NET, TypeScript, Kotlin; `archi/` and
  `backend/` families inherited from SDD_Pro).
- **Harness facades** compiled from `.sdda/` by `harness_build`: Claude Code
  (reference, blocking runtime hooks), Codex and Gemini CLI (compilable,
  experimental, gates deferred to CI).
- **Deterministic layer** (`.sdda/python/`, stdlib only): one `sdda` entry point
  whose subcommands are derived from disk; the Agentic IR compiler and its
  validator; gates G0 to G8 (mission, capabilities, topology with budget
  estimate and packaging, tool contracts, retrieval, agent, orchestration with
  its API part, safety, acceptance on a disjoint holdout); the eval engine
  (k runs, variance, three-colour verdict, judge calibration, pinned
  baselines); OTel-GenAI span traces with cost recomputed from tokens, build
  cost traced per agent; run state with resume at agent granularity; context
  packs checked against each agent's byte budget.
- **Runtime skeleton** for generated Python applications (console surface,
  tiered LLM client, bounds in code, tracing, source tools with the caller's
  identity imposed at the source), clean under `mypy --strict`.
- **Workspace v6**: what the human provides at the root (`stack/`, `feats/`,
  `assets/`, `seed/`), what the framework produces under `pipeline/`, `src/`
  and `.sys/`; `migrate-workspace` moves older layouts in place.
- **CI**: `framework-smoke`, the `--check` modes of the generators and the
  pytest suite on Linux and Windows.

### Fixed (highlights of the audit lots)

- Ownership hooks read the sub-agent identity from the right payload slot, spawn
  hooks match `Agent` as well as `Task`, facades carry a real `model:`, and hook
  commands are anchored on `$CLAUDE_PROJECT_DIR`.
- The pipeline deadlocks found by the audits (holdout required before it could
  exist, unreachable `Tested` state, IR freshness blind to contract edits).
- Error classes announced in prose and emitted by nothing now fail
  `framework-smoke` (`errors.documented`).

[Unreleased]: https://github.com/zekiriabd/SDD-Agents/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/zekiriabd/SDD-Agents/releases/tag/v0.1.0
