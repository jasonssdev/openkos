# Query Command Specification (Delta)

## ADDED Requirements

### Requirement: Stderr Retrieval Summary On Every Run

`query` MUST print a one-line retrieval summary to stderr on every
completed run (successful answer or no-match), stating
`fts_hit_count`, whether the LLM was invoked, and `cited_count`. STDOUT
MUST carry only the answer text and (when present) the `Citations:`
block — unchanged in shape from current behavior.

#### Scenario: Successful answer keeps stdout pipe-clean
- GIVEN a workspace whose bundle answers the question
- WHEN `openkos query "<question>"` is run
- THEN stdout (captured via `capsys`/`capfd`) contains exactly the
  answer text plus the `Citations:` block, with no summary text mixed
  in
- AND stderr (captured separately) contains one line reporting
  `fts_hit_count`, LLM-invoked status, and `cited_count`

#### Scenario: No-match run still emits a stderr summary
- GIVEN `answer()` returns a no-match `AnswerResult`
- WHEN `openkos query "<question>"` is run
- THEN stderr reports the retrieval summary for that run (including a
  `fts_hit_count` of `0` when applicable) and the process exits `0`

### Requirement: Build-Time Skip Notices Surfaced As A Whole-Bundle Signal

WHEN `AnswerResult.skip_notices` is non-empty, `query` MUST print those
notices to stderr, worded as a whole-bundle build diagnostic (e.g.
"N file(s) skipped while building the index"), never implying the
skipped files were candidates for the current query's match.

#### Scenario: Skip notices present alongside a successful answer
- GIVEN `skip_notices` is non-empty and the answer succeeds
- WHEN `openkos query "<question>"` is run
- THEN stderr contains both the retrieval summary and the skip
  notices, worded as build-time diagnostics, not query relevance

#### Scenario: No skip notices
- GIVEN `skip_notices` is empty
- WHEN `openkos query "<question>"` is run
- THEN stderr contains only the retrieval summary line, no skip-notice
  text

## MODIFIED Requirements

### Requirement: No-Match Is Not An Error

WHEN `answer()` returns a no-match `AnswerResult`, `query` MUST print a
stdout message specific to `no_match_cause`, MUST NOT print any
citation lines, and MUST exit `0` — a valid "no answer found" response
is not an error. The three causes MUST render distinct, actionable
stdout text: `"zero_hits"` states nothing matched; `"all_unreadable"`
states matches were found but unreadable and points at possible bundle
corruption (e.g., suggesting `openkos lint`); `"empty_query"` prompts
the user to provide a question.
(Previously: a single canned no-match line covered all three causes
indistinguishably.)

#### Scenario: Zero matching concepts
- GIVEN `no_match_cause` is `"zero_hits"`
- WHEN `openkos query "<question>"` is run
- THEN stdout shows the zero_hits message, no citation lines are
  printed, and the process exits `0`

#### Scenario: Hits found but all unreadable
- GIVEN `no_match_cause` is `"all_unreadable"`
- WHEN `openkos query "<question>"` is run
- THEN stdout shows a message noting matches were found but unusable
  and suggesting a corruption check (e.g. `openkos lint`), and the
  process exits `0`

#### Scenario: Empty or whitespace question
- GIVEN `no_match_cause` is `"empty_query"`
- WHEN `openkos query "<question>"` is run
- THEN stdout prompts the user to provide a question, and the process
  exits `0`
