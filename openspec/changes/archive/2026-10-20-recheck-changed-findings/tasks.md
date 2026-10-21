# Tasks: recheck-changed-findings

Refs #1523. Proposal: `proposal.md`. Specs: `specs/judging-application-service`,
`specs/stable-python-api`, `specs/local-http-api`, `specs/desktop-review`.
Strict TDD is ON, runner `uv run pytest`; desktop `pnpm test`. No test reaches a
model.

- [x] 1. [REFACTOR] The contradiction input digests and their persist move to
  `application/finding_inputs` (the CLI re-exports them).
- [x] 2. [TEST+IMPL] `pending_queue.retire_open_by_key`. RED observed.
- [x] 3. [TEST+IMPL] `finding_recheck` for a contradiction: quote, run, replace,
  retire, drop, settle, stale, busy, stop. RED observed; mutations killed.
- [x] 4. [TEST+IMPL] `finding_recheck` for a revision. RED observed; mutations
  killed.
- [x] 5. [TEST+IMPL] `quote_finding_recheck`, the run dispatch, `QuoteRequest.
  decision_key`; surface snapshot, OpenAPI snapshot, `schema.d.ts` regenerated.
- [x] 6. [TEST+IMPL] To review: Check again, consent and result for a changed
  finding. RED observed; mutations killed.
- [x] 7. [DOC] ADR-0077 and its index row, `docs/decision-wording.md`, CHANGELOG.
- [x] 8. [GATE] ruff check, ruff format --check, mypy, pytest --cov, eval
  self-tests, desktop tests and typecheck.
