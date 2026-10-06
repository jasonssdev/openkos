# Tasks: The Structure stage is reviewed on request (#1268)

Strict TDD: each behavior starts with an observed failing test. Runner:
`uv run pytest`, with `OLLAMA_HOST=127.0.0.1:9` so nothing can reach a model.

## Phase 1: RED / GREEN

- [x] 1.1 `curate` neither probes, computes nor presents Structure by default
      (counting wrappers on the suggester, the candidate walk and the backend's
      `chat`).
- [x] 1.2 The summary line states how many suggestions wait (supersessions
      excluded) or that none do, and names `openkos curate --structure`.
- [x] 1.3 `--structure` presents the stage exactly as before; `--accept
      structure` implies it; `review: false` and `--accept metadata` do not.
- [x] 1.4 `pending` states the waiting count under its heading; a relation row
      resolves with `openkos curate --structure`.
- [x] 1.5 The daemon digest ends with the waiting count and command, and says
      nothing when none wait.
- [x] 1.6 Messages that sent a person to `openkos curate` to type edges name
      `--structure` (`status`, `contradictions`, the candidate zero state).

## Phase 2: Proof and docs

- [x] 2.1 Mutation checks, each restored by inverse edit with `__pycache__`
      purged: the skip guard; `--accept` implication; `review: false` opt-in;
      the supersession exclusion; the pending line; the digest line.
- [x] 2.2 Existing Structure tests pass `--structure`.
- [x] 2.3 `docs/cli.md` (`curate`, `pending`, `daemon`), `CHANGELOG.md`.
- [x] 2.4 Gates: ruff check, ruff format --check, mypy, pytest --cov, evals
      self-tests.
