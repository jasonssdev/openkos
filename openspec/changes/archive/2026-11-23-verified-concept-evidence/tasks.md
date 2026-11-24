# Tasks: verified-concept-evidence

Refs #1768. Strict TDD is ON, runner `uv run pytest`.

- [x] 1. [DOC] Proposal, delta spec, design.
- [x] 2. [TEST] `verified_quote` and `_validate`/`_unevidenced_titles` cases. RED first.
- [x] 3. [IMPL] `evidence.verified_quote`, `ExtractionResult.evidence`, the check.
- [x] 4. [TEST] Mutation check on the verbatim line.
- [x] 5. [DOC] CHANGELOG.
- [ ] 6. [GATE] ruff check, ruff format --check, mypy ., pytest --cov, evals self-tests.
