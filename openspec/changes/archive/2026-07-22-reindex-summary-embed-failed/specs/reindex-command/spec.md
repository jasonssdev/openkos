# Delta for reindex-command

## MODIFIED Requirements

### Requirement: CLI Verb Is Thin Wiring

`openkos reindex` MUST: run `require_workspace`, read config, open the
on-disk `vectors.db`, FTS, and graph derived stores under `.openkos/`
(`open_vector_store` plus the FTS/graph store openers), invoke the
`state/reindex.py` orchestrator to write all three, then print a summary of
embedded/cache-hit/pruned/skipped/embed-failed counts — including whether the
prune pass was skipped due to a walk error — and exit 0.
(Previously: the summary line enumerated embedded/cache-hit/pruned/skipped
only; `embed_failed` was already tracked by the orchestrator but surfaced
solely via the stderr re-run notice, not the primary stdout tally.)

#### Scenario: Successful run prints a summary and exits 0

- GIVEN an initialized workspace with a reachable Ollama server
- WHEN `openkos reindex` runs
- THEN it prints embedded/cache-hit/pruned/skipped/embed-failed counts and
  exits 0

#### Scenario: Run outside a workspace refuses

- GIVEN a directory that is not an initialized OpenKOS workspace
- WHEN `openkos reindex` runs
- THEN it exits non-zero with a clear stderr message and no raw traceback

#### Scenario: Summary reports when the prune pass was skipped

- GIVEN a bundle subtree that raises a directory-scan error during this run
- WHEN `openkos reindex` runs
- THEN the printed summary states that the prune pass was skipped for this
  run, distinct from a run where zero concepts qualified for pruning

#### Scenario: Zero embed failures still show the counter

- GIVEN a run where every document embeds successfully (embed_failed == 0)
- WHEN `openkos reindex` prints its stdout summary
- THEN the summary includes `0 embed-failed`, matching the always-shown
  convention already used for `0 skipped`

#### Scenario: Nonzero embed failures surface in both the stdout tally and the stderr notice

- GIVEN a run where one or more documents fail to embed (embed_failed > 0)
- WHEN `openkos reindex` completes
- THEN the stdout summary reports the nonzero `embed-failed` count as part of
  the complete tally
- AND the stderr re-run call-to-action notice is printed separately and
  unchanged, so the two signals remain distinct — a factual stdout count vs.
  an actionable stderr prompt

## Non-Goals

This change does not alter `embed_failed` computation semantics, the
orchestrator's counter logic, the stderr re-run notice's content or
condition, or the success/exit-0 gate. It only widens what the stdout
summary line reports.
