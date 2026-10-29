# Tasks: merge-against-verdict

Refs #1699. Proposal: `proposal.md`. Specs: `specs/curate-decisions`,
`specs/desktop-review`. Strict TDD, runners `uv run pytest` and `pnpm test`.

- [x] 1. [TEST+IMPL] Engine: merge offered on judged uncertain/different pairs;
  apply goes through; pending, cross-type and three-member stay withheld. RED
  observed.
- [x] 2. [TEST+IMPL] Review wording: the doubt and "you can still combine"
  line. RED observed.
- [x] 3. [DOC] `docs/decision-wording.md`, CHANGELOG; deltas merged into the
  living specs.
