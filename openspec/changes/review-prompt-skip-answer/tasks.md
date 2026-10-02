# Tasks: Review Prompts Get A Skip Answer (#1264)

## Phase 1: RED / GREEN

- [ ] 1.1 Identity prompt (`adjudicate --apply`): tests for `y`/`s`/`n`/Enter/`d`, prompt text, unrecognized re-ask, summary `left pending`; implement.
- [ ] 1.2 `curate` Identity stage: same answers, `declined:` names only `d`, skip leaves the pending row open.
- [ ] 1.3 Structure and Metadata `[y/N/s]`; asymmetric `[y/N/s/a/r]`.
- [ ] 1.4 `a` accept-remaining on Structure/Metadata with the stderr notice; not on Identity.
- [ ] 1.5 `suggest-relations --apply` `[y/N/s]` through the observer port.

## Phase 2: Proof

- [ ] 2.1 Mutation check on the `d`-only persistence, restored by inverse edit.
- [ ] 2.2 `docs/cli.md`.
- [ ] 2.3 Gates: ruff check, ruff format --check, mypy, pytest --cov, evals self-tests.
