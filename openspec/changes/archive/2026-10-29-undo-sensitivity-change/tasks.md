# Tasks: undo-sensitivity-change

Closes #1706. Proposal: `proposal.md`. Specs: `specs/stable-python-api`,
`specs/local-http-api`, `specs/sensitivity-config`, `specs/desktop-review`.

Strict TDD is ON, runner `uv run pytest` and `pnpm test`.

## Delivered

- [x] 1. [TEST+IMPL] The log line records what a change raised; the reader of it.
  RED observed (module and names missing) then GREEN.
- [x] 2. [TEST+IMPL] `sensitivity_undo_service` and `openkos.api` undo, the
  surface snapshot. The high-water guard is mutation-checked: dropping the
  required-level fold, the unresolved-citation guard and the never-raise cap each
  fail a named test.
- [x] 3. [TEST+IMPL] The two routes, the error rows, the OpenAPI snapshot.
- [x] 4. [TEST+IMPL] Client methods, regenerated schema (`pnpm check:schema`).
- [x] 5. [TEST+IMPL] Model, wording, render and `docs/decision-wording.md`: Undo
  this change.
