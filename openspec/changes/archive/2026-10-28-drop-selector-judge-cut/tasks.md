# Tasks: drop-selector-judge-cut

Refs #1579. ADR-0080. Proposal: `proposal.md`. Specs: `specs/extraction-union-judge`,
`specs/ingestion`, `specs/stable-python-api`.
Strict TDD is ON, runner `uv run pytest`. No test reaches a model.

- [x] 1. [TEST+IMPL] `selector_judge` config key: default off, boolean-only, null falls back.
  RED observed (8 tests failing before the key existed).
- [x] 2. [TEST+IMPL] `extract_concept_union(selector_judge=False)` skips the judge call and
  reports `judge_status="skipped"`; the ceiling of 24 and the backstop of 20 apply. RED observed;
  the mutation `if False and not selector_judge` is killed by 3 tests.
- [x] 3. [TEST+IMPL] `estimate_extraction_calls`, `max_extraction_calls`, the batch cost gate, the
  consent bound and `_settings_digest` follow the key. RED observed.
- [x] 4. [TEST] Existing judge-centred tests opt in with `selector_judge=True`; the documented
  cost table pins the default (one call fewer) and the judge adds exactly one.
- [x] 5. [DOC] Template, reference example `openkos.yaml` (regenerated from a fresh init),
  `docs/cli.md`, CHANGELOG.
- [x] 6. [SPEC] Delta specs for `extraction-union-judge`, `ingestion`, `stable-python-api`;
  archived in this PR.
- [x] 7. [GATE] `ruff format --check .`, `ruff check .`, `mypy .`, eval self-tests, `pytest --cov`
  (observed on the final commit; see the archive report).
