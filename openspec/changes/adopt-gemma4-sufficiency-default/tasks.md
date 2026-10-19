# Tasks: adopt-gemma4-sufficiency-default

Refs #1560. Proposal: `proposal.md`. Specs: `specs/query-answer`,
`specs/query-command`, `specs/doctor-command`. ADR-0076 (Proposed).

Strict TDD is ON, runner `uv run pytest`. No test reaches Ollama.

- [x] 1. [TEST] `sufficiency` is an accepted `models:` key; it resolves to the
  packaged tag on `ollama`, is skipped on `openai-compatible`, and an explicit
  null follows `model:`. RED observed.
- [x] 2. [IMPL] `TASK_MODEL_KEYS`, `DEFAULT_TASK_MODELS`, docstrings.
- [x] 3. [TEST] `answer()` runs the check on `sufficiency_llm` and synthesis on
  `llm`; a missing check model fails open and sets `sufficiency_model_missing`;
  an unreachable server and a missing shared-client model stay fatal. RED
  observed.
- [x] 4. [IMPL] `answer()` / `_context_holds_the_answer`.
- [x] 5. [TEST] `build_sufficiency_client`, `resolve_local_exemption_all`,
  `run_query` threading. RED observed.
- [x] 6. [IMPL] `application/query.py`, `application/backends.py`.
- [x] 7. [TEST] CLI, MCP and API build the check client on the task model, share
  the synthesis client on one tag, and withhold the exemption when the check
  client is not local. RED observed.
- [x] 8. [IMPL] `cli/main.py`, `mcp/tools.py`, `mcp/server.py`, `api/_ask.py`.
- [x] 9. [TEST] The missing-model notice names the model, `ollama pull <tag>`
  and the opt-out; the generic degraded notice is unchanged. `--json` names
  `sufficiency_model`.
- [x] 10. [DOC] ADR-0076 and its index row, `docs/cli.md`, README, template,
  canonical example, `docs/tech_stack.md`.
- [x] 11. [GATE] ruff check, ruff format --check, mypy, pytest --cov, eval
  self-tests.
