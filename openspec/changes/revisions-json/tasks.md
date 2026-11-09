# Tasks: revisions-json

Refs #1739. Proposal: `proposal.md`. Spec: `specs/decision-revision-detection`.
Strict TDD is ON, runner `uv run pytest`. No test reaches Ollama.

- [x] 1. [DOC] Proposal and delta spec.
- [x] 2. [TEST] `revisions_json_report` pure builder: shape, ordering,
  relation, direction, served/judged, `--all` widening, empty. RED observed.
- [x] 3. [IMPL] Builder in `application/revisions_report.py`, sharing the
  report's view, filter and grouping.
- [x] 4. [TEST] CLI `--json`: one object, empty and non-completed statuses,
  partial batch, confidential gating, prompt on stderr, human output
  byte-unchanged. RED observed.
- [x] 5. [IMPL] `--json` in `cli/main.py`.
- [x] 6. [DOC] `docs/cli.md`, CHANGELOG.
- [ ] 7. [GATE] ruff check, ruff format --check, mypy ., pytest --cov.
