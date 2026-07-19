# Query Command Specification

## Purpose

The `openkos query "<question>"` Typer command is the CLI entry point for the
MVP-1 query chain: it gates on an initialized workspace, builds an
`OllamaClient` from config, calls the `retrieval.answer()` library seam, and
renders the answer plus citations as plain text to stdout.

## Non-Goals

`--no-color`/`NO_COLOR`/ANSI color rendering; streaming output; automated
re-filing of an answer back as a concept; semantic/vector retrieval (lexical
FTS5 only, inherited from `answer()`); any change to `answer()`'s signature
or to `query-answer` requirements.

## Requirements

### Requirement: Workspace Gate

`query` MUST call the same shared `config.require_workspace(root)` check used
by `ingest`/`status`/`lint`. WHEN the current directory is not an initialized
workspace, `query` MUST print a refusal message to stderr and exit 1 without
calling `answer()`.

#### Scenario: Run outside a workspace

- GIVEN the current directory is not an initialized workspace
- WHEN `openkos query "<question>"` is run
- THEN a refusal message is printed to stderr and the process exits 1
- AND `answer()` is never called

#### Scenario: Run inside a workspace

- GIVEN the current directory is an initialized workspace
- WHEN `openkos query "<question>"` is run
- THEN `require_workspace` returns no refusal and `query` proceeds to build
  the LLM client and call `answer()`

### Requirement: Happy-Path Answer Rendering

Given a workspace whose bundle answers the question, `query` MUST read the
configured model via `read_config(root).model`, build an `OllamaClient`, call
`retrieval.answer(question, bundle_dir=layout.bundle_dir, llm=client,
limit=n)`, and render to stdout the answer text followed by each citation as
`concept_id` and `title`. The process MUST exit 0.

#### Scenario: Matching answer with citations

- GIVEN a workspace whose bundle contains concepts matching the question
- WHEN `openkos query "<question>"` is run
- THEN stdout contains the returned answer text followed by one line per
  citation showing that citation's `concept_id` and `title`
- AND the process exits 0

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

### Requirement: `--limit` Option

`query` MUST accept an optional `--limit <n>` argument defaulting to 5 and
MUST forward it unchanged as `answer(..., limit=n)`.

#### Scenario: Caller overrides the default limit

- GIVEN `openkos query "<question>" --limit 3` is run
- WHEN `query` invokes `answer()`
- THEN `answer()` is called with `limit=3`

#### Scenario: Caller omits `--limit`

- GIVEN `openkos query "<question>"` is run without `--limit`
- WHEN `query` invokes `answer()`
- THEN `answer()` is called with `limit=5`

### Requirement: LLM And Index Errors Map To Exit 1

WHEN `answer()` raises an `OllamaError`-family exception or `FtsUnavailable`,
`query` MUST catch it, print a message to stderr, and exit 1 with no raw
traceback reaching the user. The stderr message MUST be actionable for the
two most common first-run causes and MUST remain generic for all other
cases:

- WHEN the raised exception is `OllamaUnavailable`, the stderr message MUST
  state that Ollama is not responding, MUST include the Ollama host it tried
  to reach, and MUST tell the user to start Ollama, referencing the
  `ollama serve` command.
- WHEN the raised exception is `OllamaModelNotFound`, the stderr message MUST
  name the configured model that could not be found, and MUST tell the user
  how to install it, referencing the `ollama pull <model>` command with the
  configured model name.
- WHEN the raised exception is any other `OllamaError` or `FtsUnavailable`,
  `query` MUST print a friendly (non-actionable-specific) failure message to
  stderr — unchanged from prior behavior.

(Previously: all `OllamaError`-family exceptions and `FtsUnavailable` were
caught in a single combined handler that printed one generic friendly
message with no cause-specific remediation.)

#### Scenario: Ollama backend unreachable

- GIVEN `answer()` raises `OllamaUnavailable` because Ollama is not running
  or not reachable at the configured host
- WHEN `openkos query "<question>"` is run
- THEN stderr states that Ollama is not responding, names the host it tried
  to reach, and tells the user to run `ollama serve`
- AND the process exits 1 with no raw traceback shown

#### Scenario: Configured model not installed

- GIVEN `answer()` raises `OllamaModelNotFound` because the configured model
  has not been pulled
- WHEN `openkos query "<question>"` is run
- THEN stderr names the configured model and tells the user to run
  `ollama pull <model>` with that model's name
- AND the process exits 1 with no raw traceback shown

#### Scenario: Other Ollama error

- GIVEN `answer()` raises an `OllamaError`-family exception that is neither
  `OllamaUnavailable` nor `OllamaModelNotFound`
- WHEN `openkos query "<question>"` is run
- THEN a friendly failure message is printed to stderr and the process exits
  1, with no raw traceback shown

#### Scenario: FTS index unavailable

- GIVEN `answer()` raises `FtsUnavailable`
- WHEN `openkos query "<question>"` is run
- THEN a friendly failure message is printed to stderr and the process exits
  1

### Requirement: Citations Reflect The Answer Exactly

The rendered citations MUST be exactly `AnswerResult.citations` — same
members, same order (hit-rank) — with each line showing that citation's
`concept_id` and `title`, and no other content.

#### Scenario: Citation order matches the answer

- GIVEN `answer()` returns citations in hit-rank order `[C1, C2]`
- WHEN `openkos query "<question>"` renders its output
- THEN the citation lines appear in the order `C1` then `C2`, each showing
  its `concept_id` and `title`

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

## Note

This change also includes two test/doc-only follow-ups to the already-merged
`query-answer` capability — a `_SYSTEM_PROMPT` docstring and a multi-survivor
citation-ordering test. Neither alters any `query-answer` requirement, so
`query-answer/spec.md` is unchanged.
