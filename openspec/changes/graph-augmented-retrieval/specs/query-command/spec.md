# Delta for Query Command

## MODIFIED Requirements

### Requirement: Stderr Retrieval Summary On Every Run

`query` MUST print a one-line retrieval summary to stderr on every
completed run (successful answer or no-match), stating `fts_hit_count`,
`dense_hit_count`, `fused_count`, `graph_hit_count`, whether the LLM was
invoked, and the count of rendered citations. WHEN `graph_degraded` is
`True`, the summary MUST additionally note that graph retrieval degraded
for this run. STDOUT MUST carry only the answer text and (when present) the
`Citations:` block — unchanged in shape from current behavior.
(Previously: the stderr line reported `fts_hit_count`, `dense_hit_count`,
and `fused_count` only, with no graph count or graph-degrade note.)

#### Scenario: Successful answer keeps stdout pipe-clean

- GIVEN a workspace whose bundle answers the question
- WHEN `openkos query "<question>"` is run
- THEN stdout (captured via `capsys`/`capfd`) contains exactly the answer
  text plus the `Citations:` block, with no summary text mixed in
- AND stderr (captured separately) contains one line reporting
  `fts_hit_count`, `dense_hit_count`, `fused_count`, `graph_hit_count`,
  LLM-invoked status, and the citation count

#### Scenario: No-match run still emits an extended stderr summary

- GIVEN `answer()` returns a no-match `AnswerResult`
- WHEN `openkos query "<question>"` is run
- THEN stderr reports the extended retrieval summary (including
  `graph_hit_count`, zero where applicable) for that run and the process
  exits `0`

#### Scenario: Graph degrade is noted alongside the summary

- GIVEN `answer()` returns an `AnswerResult` with `graph_degraded=True`
- WHEN `openkos query "<question>"` is run
- THEN the stderr retrieval summary includes a note that graph retrieval
  degraded for this run, and stdout is unaffected
