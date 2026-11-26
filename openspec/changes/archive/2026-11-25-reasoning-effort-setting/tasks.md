# Tasks: reasoning-effort-setting

Refs #1800. Strict TDD is ON, runner `uv run pytest`.

- [x] 1. [DOC] Proposal and delta specs.
- [x] 2. [TEST] Config key, ollama refusal, client body, refusal mapping, doctor. RED first.
- [x] 3. [IMPL] `config.py`, `openai_compatible.py`, `backends.py`, `doctor.py`.
- [x] 4. [TEST] Mutation check on the request field and the ollama refusal.
- [x] 5. [DOC] `docs/cli.md`, template and example, CHANGELOG.
- [x] 6. [GATE] ruff check, ruff format --check, mypy ., pytest --cov, evals self-tests (PR #1801 CI 10/10 green).
