# Apply Progress: stable-python-api (condensed)

**Source**: Engram `sdd/stable-python-api/apply-progress` (#3583), plus its addendum `sdd/stable-python-api/apply-progress-s6b-followup` (#3588). This file is a condensed copy, not verbatim. The authoritative task state is `tasks.md` in this folder.

**Status of this snapshot**: an intermediate record. #3583 was written when S7 was partial and still lists S7.3, S7.6 and S7.7 as remaining. Those items closed in the archive phase. The final state is in `archive-report.md`.

## Slices Implemented

| Slice | Content | Feature branch | Merged PR | Main commit |
|-------|---------|----------------|-----------|-------------|
| Planning | change artifacts | (docs) | #1393 | `015090b4` |
| S1 | identity stage lift into `application/` | `feat/identity-stage-lift` | #1394 | `ebb97668` |
| S2 | serving predicate, store-only builders, `enqueue_stored_*` | `feat/stored-verdict-builders` | #1395 | `515f8bcd` |
| S3 | `errors.py` leaf and `queue_refresh.py` | `feat/queue-refresh-service` | #1397 | `56902000` |
| S4 | print-free `vcs/autocommit.py` and CLI delegators | `feat/print-free-autocommit` | #1396 | `cc1f904c` |
| S5 | ADR-0060 Proposed, error root, `openkos.api` skeleton, snapshot and guards | `feat/python-api-skeleton` | #1398 | `200f0164` |
| S6a | `preview_decision`, `apply_decision`, commit wiring | `feat/python-api-decisions-apply` | #1399 | `d5b93c11` |
| S6b | judging quote and run, default chat wiring, refresh export | `feat/python-api-judging-refresh` | #1400 | `9c28872b` |
| S7 | docs, spec merge, ADR-0060 Accepted, archive | `docs/python-api-archive` | this archive PR | `a4c8364d` on the branch |

S6b has a second feature-branch commit, `2306f168`, which is tests only (`tests/unit/api/test_refresh_public_path.py`, three tests). It closed three test gaps found in review. It is part of #1400 and is not a separate main commit.

## Task State At Last Apply Snapshot

- S1 to S6b: all tasks checked, each with its mutation evidence recorded in #3583 and #3588.
- S7: S7.1, S7.2, S7.4, S7.5 and S7.8 done at the snapshot. S7.3, S7.6 and S7.7 remained. Their closure is recorded in `archive-report.md`, not here.

## Gate Evidence At Last Apply Snapshot

Per #3588, the latest recorded run was on `2306f168`: format, ruff, mypy (633 files) and `pytest --cov` (12830 passed, 96.73% coverage) all exit 0, and evals were 57/57. #3583 recorded 12827 passed for S6b before the review follow-up. The later figure is the one carried here.

## Deviations Recorded During Apply

1. `commit_paths` exists in both `vcs/git.py` and `vcs/autocommit.py`. The guard pins exactly those two.
2. S6a tasks said a resolved decision returns `NotApplied`. The shipped behaviour returns `Stale("row_resolved")`, which is also returned for a key that never existed. The reason text is misleading for that second case.
3. S3: the default candidate walk was bound at import time, despite its docstring. This was fixed with a RED-first test before merge.
4. S6b: two tasks had their code written before their tests ran. The RED was a collection `ImportError`. Mutation checks compensated.
5. S2, S3, S4, S5, S6a and S6b each exceeded the advisory ~400 authored-line budget, mostly because of tests.

## Mutation And Review Evidence

- S1, S2, S4, S5, S6a and S6b each recorded mutation runs where every mutant was caught (see #3583 for per-slice counts).
- S6b review follow-up (#3588): a mapping mutation (`suppressed=result.retired`, `amended=result.unchanged`) survived count-only assertions. The end-to-end test `tests/unit/api/test_refresh_public_path.py` now fails on both.
- Every code slice had an independent verifier. S1, S4, S5, S6a and S6b returned PASS. S6b's three test gaps were closed in `2306f168`.
