# Tasks: Unattended "what changed" digest (#1268 deliverable 5)

Strict TDD: every task starts with an observed failing test. Runner:
`uv run pytest`. Scope `cli` for the verb output, `docs` for documentation.

## Phase 1: RED / GREEN

- [x] 1.1 `application/digest.py`: concept ids from committed paths, the action
      line, the rendered digest (newest first, singular/plural, empty is
      nothing), and the ledger that wraps an `autocommit` port.
- [x] 1.2 `JobResult.actions`; the commit-retry job reports its commit.
- [x] 1.3 The watch job records each import's commit through the ledger; a
      skipped commit records nothing.
- [x] 1.4 `openkos daemon` prints the digest after each pass's job reports;
      terminal section and wrapping; piped text unchanged.
- [x] 1.5 End to end on real git: two imports, run the printed undo commands in
      order, the bundle is byte-identical to before.

## Phase 2: Proof

- [x] 2.1 Mutation checks: digest drops all but the first job's actions; the
      watch skips the ledger; the retry never reports; the order is not
      reversed. Each fails a test, restored by inverse edit.
- [x] 2.2 `docs/cli.md` (`daemon`), `CHANGELOG.md` Unreleased.
- [x] 2.3 Gates: ruff check, ruff format --check, mypy, pytest --cov, evals
      self-tests.
