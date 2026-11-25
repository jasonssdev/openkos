# Tasks: evidence-quote-setting

Refs #1768. Strict TDD is ON, runner `uv run pytest`.

- [x] 1. [DOC] Proposal and delta spec.
- [x] 2. [TEST] Config key, prompt default, registered hash, wiring. RED first.
- [x] 3. [IMPL] `evidence_quote` module, `_build_messages` and extractor threading, config key, ingest.
- [x] 4. [TEST] Mutation check on the default.
- [x] 5. [DOC] `docs/cli.md`, template, hosted providers how-to, CHANGELOG.
- [ ] 6. [GATE] ruff check, ruff format --check, mypy ., pytest --cov, evals self-tests.
