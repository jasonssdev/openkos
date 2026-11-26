# Tasks: ingest-token-usage

Refs #1807. Strict TDD is ON, runner `uv run pytest`.

- [x] 1. [DOC] Proposal and delta specs.
- [x] 2. [TEST] `ChatUsage`, `UsageTally`, both clients' sink (success, total fallback, capped, missing, retry, request body unchanged). RED first.
- [x] 3. [IMPL] `llm/base.py`, `llm/openai_compatible.py`, `llm/ollama.py`.
- [x] 4. [TEST] CLI line for single, batch, failed extraction, declined, not reported, partial; resolver forwards the sink only when given. RED first.
- [x] 5. [IMPL] `application/backends.py`, `cli/main.py`.
- [x] 6. [TEST] Mutation check on the sink wiring, the print path and the format branches.
- [x] 7. [DOC] `docs/cli.md`, CHANGELOG.
- [ ] 8. [GATE] ruff check, ruff format --check, mypy ., pytest --cov, evals self-tests.
