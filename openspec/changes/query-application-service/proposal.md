# Proposal: Query Application Service

## Intent

Give the query bounded context a home outside the CLI, so the MVP 3 `api` and
`mcp` adapters can answer a question and file the result without importing from
`openkos.cli`.

**Correcting the premise of issue #918.** The issue frames goal 1 as building a
"query service — retrieval + generation + citation assembly behind one
function". That already exists: `retrieval/answer.py:answer()` (line 921) owns
FTS term extraction, dense search, sensitivity filtering, RRF fusion, context
assembly, citation building, the sufficiency gate and attribution parsing, and
it already has callers outside the CLI. Restating the issue's framing would
overstate the gap.

The real gap is narrower and has two parts:

1. **Orchestration around `answer()`** — config and workspace gating, LLM and
   embedder construction, store opening with degrade handling, staleness
   checks, and the exception-ordering that maps to exit codes. Today this lives
   only inside the `query` Typer command.
2. **The entire `--save` staging and filing flow**, which has no non-CLI home at
   all.

## Scope

### In Scope

- A new top-level `src/openkos/application/query.py` holding the query bounded
  context's composition and application rules.
- Extracting query orchestration and the `--save` filing domain logic out of
  `cli/main.py`, leaving a thin Typer adapter.
- An ADR recording the introduction of the `application/` layer.

### Out of Scope

- Ingest and lifecycle services — separate changes, and no stub packages land
  here (`AGENTS.md`: create a package when its code arrives).
- The headless-consent protocol. It binds when the lifecycle service lands; the
  two query-path confirm gates that will need it are recorded in the
  exploration as known future work.
- The `api` and `mcp` adapters themselves.
- Any change to `answer()`'s contract, the knowledge model, the on-disk format,
  or the CLI surface.

## Placement decision

`application/query.py`, chosen over two alternatives:

| Option | Why it loses |
| --- | --- |
| `cli/query_service.py` | Makes MVP 3 adapters import `openkos.cli.*` — the defect #918 exists to fix, one file lower. |
| inside `retrieval/` | Makes a documented derived, rebuildable layer write canonical state. |

Granularity is **per bounded context**, not per verb. Extraction lands before
the first MVP 3 adapter slice.

## The `--save` split

| Concern | Home | Evidence |
| --- | --- | --- |
| Title-derivation cascade, sensitivity floor, duplicate-scan orchestration, synthesis-share threshold | Query service | Operates only on `AnswerResult` / `Citation`; meaningless without a query having just run. |
| `_reject_drifted_targets`, `_autocommit`, `_refresh_derived_after_write` | Stay shared infrastructure, called through | 17 / 24 / 17 call sites across 10–20 commands; must not be duplicated or owned by one context. |
| Confirm / TTY gate | CLI adapter | Interaction, not domain. |

## Contract: behavior preservation

This is a **pure composition refactor**. No change to the knowledge model, the
on-disk format, local-first behavior, or the CLI surface. The 161 CLI tests
(`test_query.py` 58 + `test_query_save.py` 103), which assert only on
`exit_code`, `stdout` and `stderr`, are the guard.

## Capabilities

### New Capabilities

- `query-application-service`: the query bounded context's service contract —
  what it composes, what it returns, and what stays with the adapter.

### Modified Capabilities

- `query-command`: its Purpose currently describes the Typer command as the
  thing that gates the workspace, builds the client, calls `answer()` and
  renders. That composition moves behind the service; the command becomes a
  thin adapter over it.

Expect a **thin** delta. A composition refactor introduces few new
requirements; the existing suite is the real behavior contract.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| **Test-injection seam breaks.** 123 `monkeypatch.setattr("openkos.cli.main.answer", ...)` occurrences (across 5 test files, not only the 2 query ones) and 118 for `"openkos.cli.main.OllamaClient"`. If the service calls those names from its own namespace, the patches become silent no-ops and the real networked `answer()` runs. | High | Decide the strategy in design before implementation: (a) inject callables the CLI still owns and passes by name, preserving `openkos.cli.main.X` patchability, or (b) migrate the patch targets to the service module. Not settled here. |
| Sizing exceeds the review budget | High | See Delivery shape. |
| Layering violation goes uncaught | Medium | `docs/architecture.md` states layering is an unenforced convention; the placement decision avoids the violation by construction rather than relying on a guard. |
| Shared write helpers fork | Medium | They are injected or called through, never copied into the service. |

## ADR

Required. Introducing the `application/` layer decides a pattern and is
hard-to-reverse — both arms of the ADR gate in `openspec/config.yaml`. It is
written with the design, at status **Proposed**, and flipped to **Accepted**
only at archive.

## Delivery shape

Estimated **2,500–3,000 changed lines** against a 2,000-line budget, so chaining
is expected. Proposed slice boundaries, as input to the task breakdown rather
than a settled plan:

1. Read path — setup, the `answer()` call, all rendering except `--save`.
2. `--save` filing domain logic.
3. Optional — test patch-target migration, if design chooses that strategy.

## Rollback

The change is additive-then-subtractive and touches no data. Reverting the
merge commit restores `cli/main.py` in full; no migration, no on-disk state, and
no derived store is affected. If a slice lands and the next does not, the CLI
remains fully functional because each slice keeps the command working end to
end.

## Success Criteria

- [ ] The 161 query CLI tests pass unmodified, or with only patch-target changes
      if design chooses migration.
- [ ] `openkos query` and `openkos query --save` produce byte-identical stdout,
      stderr and exit codes for equivalent inputs.
- [ ] A caller outside `openkos.cli` can answer a question and file the result
      without importing anything from `openkos.cli`.
- [ ] `_reject_drifted_targets`, `_autocommit` and `_refresh_derived_after_write`
      retain a single definition each.
- [ ] No `ingest` or `lifecycle` package is created.
- [ ] The ADR exists at status Proposed with alternatives recorded.
- [ ] Lint, format, types and the 90% branch-coverage gate stay green.
