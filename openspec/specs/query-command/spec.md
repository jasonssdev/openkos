# Query Command Specification

## Purpose

The `openkos query "<question>"` Typer command is the CLI entry point for the
MVP-1 query chain: it gates on an initialized workspace, builds an
`OllamaClient` from config, calls the `retrieval.answer()` library seam, and
renders the answer plus citations as plain text to stdout.

## Non-Goals

`--no-color`/`NO_COLOR`/ANSI color rendering; streaming output; automated
re-filing of an answer back as a concept; weighted/normalized fusion; any
change to `answer()`'s signature beyond its new optional embedder/vector_store
parameters.

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
configured model via `read_config(root).model`, build an `OllamaClient` for
chat, build an `Embedder` (`OllamaClient(cfg.embedding_model)`) and open the
vector store via `open_vector_store(layout.vectors_db_path)`, call
`retrieval.answer(question, bundle_dir=layout.bundle_dir, llm=client,
embedder=embedder, vector_store=vector_store, limit=n)`, and render to
stdout the answer text followed by each citation as `concept_id` and
`title`. The process MUST exit 0.
(Previously: only the chat `OllamaClient` was built; no dense seams were
constructed or injected.)

#### Scenario: Matching answer with citations

- GIVEN a workspace whose bundle contains concepts matching the question
- WHEN `openkos query "<question>"` is run
- THEN `query` builds and injects both the `Embedder` and the vector store,
  stdout contains the returned answer text followed by one line per
  citation showing that citation's `concept_id` and `title`, and the
  process exits 0

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

### Requirement: FTS/Graph-Unavailable Runs Degrade And Hint At Reindex

WHEN the persisted FTS or graph derived index is absent or its on-disk store
is unopenable/corrupt (the same condition `answer()` degrades on), `query`
MUST still complete using whichever retrieval lists remain available, exit
`0`, and print an additional stderr hint telling the user to run
`openkos reindex`. STDOUT MUST remain unaffected — answer text and citations
only, computed from whatever lists were available. `query` MUST NOT recompute
or compare the current bundle's manifest hash to reach this decision — per
design D2, staleness detection is reindex's exclusive job; a properly-
reindexed handle is always treated as fresh at query time. This mirrors the
existing dense-unavailable hint.

#### Scenario: Never-reindexed workspace hints at reindex for FTS/graph too

- GIVEN a workspace that has never run `reindex` (no persisted FTS or graph
  index exists)
- WHEN `openkos query "<question>"` is run
- THEN the process exits 0, stdout renders whatever answer the remaining
  retrieval lists support, and stderr includes a hint to run
  `openkos reindex`

#### Scenario: Corrupt or unopenable FTS/graph index degrades with the same hint

- GIVEN a persisted FTS or graph index whose on-disk store cannot be opened
  (e.g. a corrupt file), and no query-time manifest comparison is performed
- WHEN `openkos query "<question>"` is run
- THEN the process exits 0 on the remaining available lists, and stderr
  includes the reindex hint

### Requirement: Docstring No Longer Claims No Persisted State

The `query` command's docstring (`cli/main.py:2226`) MUST no longer state
that graph/FTS retrieval carries "no persisted state, no CLI-level graph
command"; it MUST describe graph and FTS retrieval as reading persisted,
`reindex`-written on-disk indexes.

#### Scenario: Docstring reflects the persisted-index contract

- GIVEN `cli/main.py`'s `query` command docstring
- WHEN a reader reviews it after this change
- THEN it states that graph and FTS retrieval read persisted on-disk indexes
  maintained by `reindex`, and no longer claims no persisted state exists

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
  to reach, MUST tell the user to start Ollama, referencing the
  `ollama serve` command, and MUST additionally point to `openkos doctor`
  to diagnose the environment.
- WHEN the raised exception is `OllamaModelNotFound`, the stderr message MUST
  name the configured model that could not be found, and MUST tell the user
  how to install it, referencing the `ollama pull <model>` command with the
  configured model name.
- WHEN the raised exception is any other `OllamaError` or `FtsUnavailable`,
  `query` MUST print a friendly (non-actionable-specific) failure message to
  stderr — unchanged from prior behavior.

(Previously: the `OllamaUnavailable` message told the user to run
`ollama serve` with no additional pointer to `openkos doctor`.)

#### Scenario: Ollama backend unreachable

- GIVEN `answer()` raises `OllamaUnavailable` because Ollama is not running
  or not reachable at the configured host
- WHEN `openkos query "<question>"` is run
- THEN stderr states that Ollama is not responding, names the host it tried
  to reach, tells the user to run `ollama serve`, and also names
  `openkos doctor` to diagnose the environment
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

### Requirement: Dense-Unavailable Runs Degrade And Hint At Reindex

WHEN dense retrieval degrades (absent/empty `vectors.db`, `VecUnavailable`,
or a read-path `sqlite3.Error`), `query` MUST still complete on the
FTS-only fused result, exit `0`, and print an additional stderr hint
telling the user to run `openkos reindex` to enable semantic retrieval.
STDOUT MUST remain unaffected — answer text and citations only, computed
from FTS-only fusion.

#### Scenario: Cold store (never reindexed) hints at reindex

- GIVEN `vectors.db` does not exist under the current workspace
- WHEN `openkos query "<question>"` is run
- THEN the process exits 0, stdout renders the FTS-only answer and
  citations unaffected, and stderr includes a hint to run
  `openkos reindex`

#### Scenario: Locked or corrupt vectors.db degrades with the same hint

- GIVEN `vector_store.query` raises a read-path `sqlite3.Error` or
  `VecUnavailable`
- WHEN `openkos query "<question>"` is run
- THEN the process exits 0 on the FTS-only fused result, and stderr
  includes the same reindex hint

## Note

This change also includes two test/doc-only follow-ups to the already-merged
`query-answer` capability — a `_SYSTEM_PROMPT` docstring and a multi-survivor
citation-ordering test. Neither alters any `query-answer` requirement, so
`query-answer/spec.md` is unchanged.
