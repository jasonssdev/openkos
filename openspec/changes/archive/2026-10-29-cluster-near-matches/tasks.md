# Tasks: cluster-near-matches

Refs #1701. Proposal: `proposal.md`. Specs: `specs/entity-resolution`,
`specs/curate-decisions`, `specs/desktop-review`. Strict TDD, runners
`uv run pytest` and `pnpm test`.

- [x] 1. [TEST+IMPL] Candidates: LOW components of 3 to 8 become one group;
  pairs, bound, HIGH/ACRONYM overlap and cross-type behaviour pinned; model-free
  before/after count (9 pairs to 3 questions). RED observed.
- [x] 2. [TEST+IMPL] Engine: a LOW cluster lists merge-group against a doubt;
  exact-title groups and unjudged clusters keep their guards. RED observed.
- [x] 3. [TEST+IMPL] Review wording: the doubt line for a group. RED observed.
- [x] 4. [DOC] `docs/decision-wording.md`, CHANGELOG; deltas merged into the
  living specs.
