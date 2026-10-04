# Tasks: adopt-gemma4-judge-defaults

Refs #1269. Proposal: `proposal.md`. Specs: `specs/curate-command`,
`specs/doctor-command`, `specs/contradiction-detection`,
`specs/entity-resolution-adjudication`. ADR-0047 (Proposed).

Strict TDD is ON, runner `uv run pytest`. No test reaches Ollama.

- [x] 1. [TEST] `DEFAULT_TASK_MODELS` names the judge tag for the two roles;
  an unconfigured workspace resolves them to it; `query` (`task=None`) and
  every other task stay on `model:`. RED observed.
- [x] 2. [IMPL] `config.DEFAULT_JUDGE_MODEL`, the packaged map, the
  `ollama`-only gate in `resolve_task_model`.
- [x] 3. [TEST] On `openai-compatible` the packaged rung is skipped; an
  explicit entry still wins; an explicit null declines the default.
- [x] 4. [MUT] Mutate the tag and the backend gate; each goes RED.
- [x] 5. [TEST] `contradictions` and `adjudicate` name the resolved task model
  and its pull command in the not-installed and partial-batch paths; the
  client is built on the judge model. RED observed.
- [x] 6. [IMPL] Resolve the task model in `contradictions_service` and
  `cli/main.py` (adjudicate).
- [x] 7. [DOC] README, `docs/cli.md`, `docs/faq.md`, `docs/tech_stack.md`,
  template, canonical example, ADR-0047 and its index row.
- [x] 8. [GATE] ruff check, ruff format --check, mypy, pytest --cov, eval
  self-tests.
