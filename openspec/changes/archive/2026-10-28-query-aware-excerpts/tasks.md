# Tasks: query-aware-excerpts

Refs #1647. Proposal: `proposal.md`. Spec: `specs/query-answer`.
Strict TDD, runner `uv run pytest`. No test reaches a model.

- [x] 1. [TEST] `bounded_text(query=...)`: needle survives, fits unchanged, deterministic, ties, fallbacks. RED observed.
- [x] 2. [IMPL] BM25 window choice in `prompt_budget`.
- [x] 3. [TEST] `query_aware_excerpts` config key, template, `run_query` threading, `answer` default and both bound paths. RED observed.
- [x] 4. [IMPL] config, `application/query.py`, `retrieval/answer.py`, template, `docs/cli.md`.
- [x] 5. Mutation checks: tie-break, and the query pass-through on both paths.
