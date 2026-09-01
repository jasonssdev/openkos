# Query Command Specification

## Purpose

The `openkos query "<question>"` Typer command is the CLI entry point for
the MVP-1 query chain: it gates the workspace, reads the configuration and
builds the LLM/embedder seams, then delegates to the query application
service, which opens the indexes with degrade handling, calls the
`retrieval.answer()` library seam, and computes the `--save` filing plan.
`query` itself owns only argument parsing, workspace and client setup,
interactive confirmation, exit-code mapping, and rendering the answer plus
citations as plain text to stdout.

## Non-Goals

`--no-color`/`NO_COLOR`/ANSI color rendering; streaming output; automated
re-filing without `--save`; LLM-generated titles; mandatory `--title`;
weighted/normalized fusion; any change to `answer()`'s signature beyond its new
optional embedder/vector_store parameters.

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

### Requirement: FTS-Unavailable Runs Degrade And Hint At Reindex

WHEN the persisted FTS derived index is absent or its on-disk store is
unopenable/corrupt (the same condition `answer()` degrades on), `query`
MUST still complete using whichever retrieval lists remain available, exit
`0`, and print an additional stderr hint telling the user to run
`openkos reindex`. STDOUT MUST remain unaffected — answer text and citations
only, computed from whatever lists were available. `query` MUST NOT recompute
or compare the current bundle's manifest hash to reach this decision — per
design D2, staleness detection is reindex's exclusive job; a properly-
reindexed handle is always treated as fresh at query time. This mirrors the
existing dense-unavailable hint.

An absent or corrupt `graph.db` MUST NOT trigger this hint, and MUST NOT
change the answer, the citations, or any count in the retrieval summary.

(Previously: the graph derived index was covered by this requirement too —
`query` opened `.openkos/graph.db` read-only, degraded to `graph_index=None`
when it was absent or corrupt, and printed the same reindex hint. Issue #434
removed the retrieval consumer, so `query` no longer opens the store at all.
`reindex` still writes `graph.db` and the shared stale-index advisory still
names it, because contradiction-candidate derivation still reads the typed
projection.)

#### Scenario: Never-reindexed workspace hints at reindex for FTS too

- GIVEN a workspace that has never run `reindex` (no persisted FTS index
  exists)
- WHEN `openkos query "<question>"` is run
- THEN the process exits 0, stdout renders whatever answer the remaining
  retrieval lists support, and stderr includes a hint to run
  `openkos reindex`

#### Scenario: Corrupt or unopenable FTS index degrades with the same hint

- GIVEN a persisted FTS index whose on-disk store cannot be opened
  (e.g. a corrupt file), and no query-time manifest comparison is performed
- WHEN `openkos query "<question>"` is run
- THEN the process exits 0 on the remaining available lists, and stderr
  includes the reindex hint

#### Scenario: A corrupt graph store is not a retrieval degrade

- GIVEN `.openkos/graph.db` is absent, or present but corrupt
- WHEN `openkos query "<question>"` is run
- THEN no `graph_index` is passed to `answer()`, stderr carries neither the
  derived-index-unavailable hint nor a graph-degrade note on its account,
  and the answer is unchanged

### Requirement: Docstring No Longer Claims No Persisted State

The `query` command's docstring MUST no longer state that retrieval carries
"no persisted state, no CLI-level graph command"; it MUST describe the FTS
and dense channels as reading persisted, `reindex`-written on-disk indexes.

(Previously: this named graph retrieval alongside FTS as a persisted-index
reader. Issue #434 removed the graph channel from retrieval; the docstring
now explains that removal instead.)

#### Scenario: Docstring reflects the persisted-index contract

- GIVEN `cli/main.py`'s `query` command docstring
- WHEN a reader reviews it after this change
- THEN it states that FTS and dense retrieval read persisted on-disk indexes
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
traceback reaching the user. The stderr message MUST be actionable for each
of the three enumerated causes below and MUST remain generic for all other
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
- WHEN the raised exception is `OllamaEmbeddingDimensionMismatch`, the stderr
  message MUST identify the failure as a PERMANENT dimension mismatch caused
  by the configured embedding model, and MUST name restoring the working
  `embedding_model` value in `openkos.yaml` as the remedy. It MUST NOT be
  worded as transient or self-healing (never "will retry next run"), and
  MUST NOT point the user at `openkos reindex` as the remedy — `reindex`
  fails with this same permanent error until `openkos.yaml` is fixed. The
  reindex hint of the Dense-Unavailable Runs Degrade And Hint At Reindex
  requirement MUST NOT be printed for this cause: `answer()` propagates
  instead of setting `dense_degraded`, so a run that hits this error never
  reaches that hint.
- WHEN the raised exception is any other `OllamaError` or `FtsUnavailable`,
  `query` MUST print a friendly (non-actionable-specific) failure message to
  stderr — unchanged from prior behavior.

For `OllamaEmbeddingDimensionMismatch` the exit-1 refusal MUST be
UNCONDITIONAL. `answer()` runs lexical retrieval BEFORE dense retrieval, so
the mismatch surfaces only after FTS retrieval has already succeeded and may
already hold hits that would have grounded a citable answer. `query` MUST
still exit 1 and print nothing on stdout, discarding that already-successful
retrieval work: it MUST NOT print an FTS-only answer, MUST NOT offer any flag
that forces one (no `--fts-only`, no `--allow-degraded`), and MUST NOT make
the refusal conditional on whether FTS found hits. The accepted cost is
denying the user even the answers FTS could have grounded; the ONLY remedy is
restoring the working `embedding_model` in `openkos.yaml`, not a CLI flag.

(Previously: the `OllamaUnavailable` message told the user to run
`ollama serve` with no additional pointer to `openkos doctor`.)
(Previously: `OllamaEmbeddingDimensionMismatch` never reached this ladder —
`answer()` swallowed it into `dense_degraded`, so `query` printed a
successful FTS-only answer at exit 0, plus the misleading
`openkos reindex` hint, and never reported the misconfiguration.)
(Previously: the FTS hits gathered before the mismatch were still fused,
cited, and printed; this requirement deliberately discards them.)

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

#### Scenario: Embedding model returns wrong-dimension vectors

- GIVEN `answer()` raises `OllamaEmbeddingDimensionMismatch` because the
  configured `embedding_model` does not emit `EMBED_DIM`-dimensional vectors
- WHEN `openkos query "<question>"` is run
- THEN stderr identifies the failure as a permanent dimension mismatch and
  tells the user to restore the working `embedding_model` value in
  `openkos.yaml`
- AND stderr does NOT suggest running `openkos reindex` and is never worded
  as transient ("will retry next run")
- AND the process exits 1 with no answer on stdout and no raw traceback shown

#### Scenario: Refusal stands even when FTS retrieval already succeeded

- GIVEN a workspace whose FTS index matches the question, so the same run
  would have printed a cited FTS-only answer at exit 0 had the embedding
  model been healthy
- AND the configured `embedding_model` returns a wrong-dimension embedding,
  so `answer()` raises `OllamaEmbeddingDimensionMismatch` AFTER those FTS
  hits were already retrieved
- WHEN `openkos query "<question>"` is run
- THEN the process exits 1 with nothing on stdout — no answer, no citation,
  and no flag exists that would force the degraded FTS-only answer instead
- AND the only remedy is restoring the working `embedding_model` value in
  `openkos.yaml`

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
`concept_id` and `title`, plus a trailing `[confidential]` marker on
exactly the citations whose `confidential` flag is set (issue #569), and
no other content.

#### Scenario: Citation order matches the answer

- GIVEN `answer()` returns citations in hit-rank order `[C1, C2]`
- WHEN `openkos query "<question>"` renders its output
- THEN the citation lines appear in the order `C1` then `C2`, each showing
  its `concept_id` and `title`

### Requirement: Confidential Citations Are Disclosed

The read path MUST disclose what the write path already discloses (issue
#569): when any rendered citation carries the `confidential` flag, each
such citation line MUST end with a `[confidential]` marker, and `query`
MUST print ONE stderr NOTICE — equivalent to the commit-path confidential
NOTICE — stating the answer cites content marked
`sensitivity: confidential` and that sharing it forward moves that content
off this machine. This is transparency, never enforcement: admission was
already decided by the fail-closed gate (the confidential local exemption
or `--include-confidential` let those concepts in by design). WHEN no
rendered citation is confidential, NO marker and NO notice may appear —
the disclosure must carry signal, not noise.

#### Scenario: Confidential citation carries the marker and the notice

- GIVEN the confidential local exemption is active and the answer cites a
  concept explicitly marked `sensitivity: confidential`
- WHEN `openkos query "<question>"` renders its output
- THEN that citation line ends with `[confidential]`, unmarked citations do
  not, and stderr carries one `openkos: NOTICE` line naming
  `'sensitivity: confidential'`

#### Scenario: No confidential citation, no disclosure noise

- GIVEN an answer whose citations are all non-confidential
- WHEN `openkos query "<question>"` renders its output
- THEN no `[confidential]` marker and no NOTICE appears

### Requirement: Stderr Retrieval Summary On Every Run

`query` MUST print a one-line retrieval summary to stderr on every
completed run (successful answer or no-match), stating `fts_hit_count`,
`dense_hit_count`, `fused_count`, whether the LLM was invoked, and the count
of rendered citations. The summary MUST carry NO graph term, and `query`
MUST NOT print a graph-degrade note on any run. STDOUT MUST carry only the
answer text and (when present) the `Citations:` block — unchanged in shape
from current behavior.

(Previously: the line carried a third retrieval term, `<n> graph-added` from
`AnswerResult.graph_contributed_count` — how many reserved slots the seeded
personalized-PageRank channel filled with concepts FTS and dense never found
— plus a separate note whenever `graph_degraded` was `True`. That term was
itself a correction of an earlier one reporting `graph_hit_count`, the raw
candidate pool, which printed `10 graph` on a workspace with zero typed
edges. Issue #434 removed the channel the term described: measured over 10
questions the slot it claimed was 7 times harmful, 3 times neutral and never
beneficial, because seeded PageRank ranks by global centrality — a property
of the corpus, not of the question — and the slot always cost a real hit.)

#### Scenario: Successful answer keeps stdout pipe-clean

- GIVEN a workspace whose bundle answers the question
- WHEN `openkos query "<question>"` is run
- THEN stdout (captured via `capsys`/`capfd`) contains exactly the answer
  text plus the `Citations:` block, with no summary text mixed in
- AND stderr (captured separately) contains one line reporting
  `fts_hit_count`, `dense_hit_count`, `fused_count`, LLM-invoked status,
  and the citation count

#### Scenario: No-match run still emits an extended stderr summary

- GIVEN `answer()` returns a no-match `AnswerResult`
- WHEN `openkos query "<question>"` is run
- THEN stderr reports the extended retrieval summary (zero where applicable)
  for that run and the process exits `0`

#### Scenario: The summary names no graph term

- GIVEN any completed `query` run, healthy or degraded
- WHEN its stderr is read
- THEN the retrieval line contains no `graph-added` term and no
  graph-degrade note

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

### Requirement: Read-Only Purity Without `--save`

`query` MUST default `--save` to OFF. WHEN `--save` is not passed, `query`
MUST behave byte-identically to its pre-existing read-only path: no bundle
file is created, no index or log entry is written, and no confirmation
prompt is shown.

#### Scenario: Query without `--save` is unchanged

- GIVEN a workspace and a question with a matching answer
- WHEN `openkos query "<question>"` is run without `--save`
- THEN stdout/stderr output is identical to the pre-`--save` behavior
- AND no new file, index entry, or log entry is created

### Requirement: `--save` Files The Cited Answer As An Insight

WHEN `--save` is passed and `answer()` returns a matched result, `query`
MUST, after rendering the answer, build a new document via the ingest
builder with: body = the rendered answer text; title = the first rung of the
TITLE LADDER that resolves -- the answer's first sentence as a DECLARATIVE
title, else a definitional question's own SUBJECT (issue #646), else that
first sentence's opening CLAUSE when it was refused for LENGTH alone (issue
#696), else the question verbatim -- or `--title` when given; description =
the question, or `--description` when given; type
= `"Insight"` (the filed-synthesis type, issue #570), or `--type` when
given (any buildable type); provenance = the cited concepts' ids
(`result.citations`).

The type distinction is truth-decay (issue #570): an extracted `Concept`
depends on an immutable `Source`; a filed synthesis depends on the MUTABLE
bundle, so every ingest, merge, or correction can invalidate it. `Insight`
therefore defaults to the `volatile` tier, is never emitted by the LLM
classifier (`BUILDER_ONLY_TYPES`), files under `bundle/insights/`, and its
slug -- the permanent Concept ID -- is declarative rather than an
interrogative sentence.

#### Scenario: Default filing is a declaratively-titled Insight

- GIVEN `openkos query "<question>" --save` is run and the answer matches
  with a usable first sentence
- WHEN the document is built
- THEN body is the rendered answer, the title is the answer's first
  sentence, the description is the question, the type is `"Insight"` under
  `bundle/insights/`, and provenance lists the cited concept ids

#### Scenario: A definitional question titles the filing by its subject

- GIVEN the answer's first sentence is unusable AND the question is a
  recognized definitional scaffold (`¿qué es el Model Context Protocol?`)
- WHEN the document is built
- THEN the title is the question's subject (`Model Context Protocol`),
  never the clause rung below it

#### Scenario: An over-long first sentence titles the filing by its clause

- GIVEN the answer's first sentence is refused for LENGTH ALONE and the
  question is not a recognized definitional scaffold (`¿por qué es
  importante la trazabilidad en un sistema de conocimiento?`)
- WHEN the document is built
- THEN the title is that sentence cut at its first clause boundary, so the
  permanent Concept ID is declarative rather than interrogative

#### Scenario: An unusable first sentence falls back to the question title

- GIVEN the answer's first sentence is shorter than the declarative
  minimum, or itself a question, or is over-long with no clause boundary to
  cut at, AND the question names no recognizable subject
- WHEN the document is built
- THEN the title falls back to the question (the pre-#570 default)

#### Scenario: `--title`, `--description`, `--type` override defaults

- GIVEN `openkos query "<question>" --save --title "T" --type "Procedure"`
- WHEN the document is built
- THEN title is `"T"` and type is `"Procedure"`, overriding the derived
  title and `"Insight"` defaults

### Requirement: Cited Syntheses Are Marked

`query` MUST render a cited `Insight` distinctly in the citation list (a
`[synthesis]` marker) and MUST label an `Insight` context block as model
output in the synthesizer's prompt, so neither the reader nor the model can
mistake an earlier synthesis for source-backed knowledge. WHEN every
citation of an answer is itself an `Insight`, `query` MUST warn on stderr
that nothing beneath the answer reaches a `Source`.

#### Scenario: A cited Insight carries the synthesis marker

- GIVEN an answer citing one `Insight` and one `Concept`
- WHEN `openkos query "<question>"` renders its citations
- THEN the `Insight` line carries `[synthesis]`, the `Concept` line does
  not, and no all-synthesis warning is printed

#### Scenario: An all-synthesis answer warns

- GIVEN an answer whose every citation is an `Insight`
- WHEN `openkos query "<question>"` completes
- THEN a stderr warning states that every citation is itself a filed
  synthesis

### Requirement: `--save` Discloses A Possible Duplicate Before Confirming

BEFORE the `--save` confirmation gate, `query` MUST disclose already-filed
insights whose SOURCE QUESTION resembles the question being filed (#762),
one line per candidate, most-similar first.

The lookup MUST run on the question, never on the answer body or the derived
title: both were measured and OVERLAP, with title similarity scoring a
perfect match on a pair of unrelated subjects.

The disclosure MUST be advisory. `query` MUST NOT merge, rename, refuse or
otherwise alter the filing because of it, and an unreachable embedding
backend MUST disclose nothing rather than fail the save.

#### Scenario: A resembling filing is disclosed and the save still writes

- GIVEN an insight already filed from a resembling question
- WHEN `openkos query "<question>" --save --auto` runs
- THEN a possible-duplicate line names that insight and its source question,
  and the new insight is still written

WHEN the lookup could not run — the embedding backend failed, or returned a
malformed batch — `query` MUST say so on stderr rather than rendering the
same silence as a scan that ran and found nothing (#764). Having nothing to
compare against is NOT such a case: that scan ran correctly.

`query` MUST likewise announce on stderr when the pre-synthesis sufficiency
check was requested and could not run, so an answer produced without the
configured guard is distinguishable from one the guard allowed.

#### Scenario: An unavailable lookup is announced and the save still writes

- GIVEN the embedding backend fails during `--save`
- WHEN `openkos query "<question>" --save --auto` runs
- THEN stderr says the question could not be checked against filed insights,
  and the new insight is still written

#### Scenario: No candidate, no line

- GIVEN no filed insight resembles the question
- WHEN `openkos query "<question>" --save --auto` runs
- THEN no possible-duplicate line appears

The lookup MUST compare against EVERY comparable already-filed insight, and
MUST report that it could not run rather than comparing only some of them
(#764). A partial comparison that renders like a complete one is the failure
to avoid, and once the scan promises all of them there is no count left to
disclose a shortfall with.

To make that affordable, `query` MUST cache each filed insight's source-question
embedding and re-embed only questions it has not seen, or whose text changed.
Cached vectors MUST be keyed by embedding model, MUST be dropped when their
insight leaves the bundle, and MUST be treated as a rebuildable cache: losing
the store costs re-embedding, never correctness.

WHEN the cache is unavailable, `query` MUST report the lookup as one that could
not run. It MUST NOT fall back to embedding every filed question: that is the
cost this design removes, and it would stall the confirmation gate with nothing
on screen explaining the wait.

#### Scenario: Every filed insight is compared, including the oldest

- GIVEN a bundle with more filed insights than any previous bound allowed
- WHEN `openkos query "<question>" --save --auto` runs
- THEN the oldest filing is eligible for disclosure on the same terms as the
  newest, and no line claims a partial comparison

#### Scenario: A warm cache re-embeds nothing

- GIVEN a save that already embedded every filed insight's question
- WHEN a second `openkos query "<question>" --save --auto` runs
- THEN no already-cached question is embedded again

#### Scenario: No cache is a lookup that could not run

- GIVEN the question-vector cache cannot be opened
- WHEN `openkos query "<question>" --save --auto` runs
- THEN stderr says the question could not be checked, and the new insight is
  still written

WHEN the embedding host is NOT this machine, `--save` MUST announce — before
the send — that already-filed source questions are transmitted too, naming
the ceiling (#764). The standing `OLLAMA_HOST` advisory covers the question
just typed; a save additionally ships other filings' questions, which is a
different disclosure rather than a louder one. A `query` without `--save`
MUST NOT print it: no filed question is sent there.

#### Scenario: A remote embedding host is told what a save sends

- GIVEN `OLLAMA_HOST` names a host that is not this machine
- WHEN `openkos query "<question>" --save --auto` runs
- THEN stderr announces that already-filed source questions are sent, with
  the ceiling, and the credentialed host value stays redacted

### Requirement: An Answer Standing On Nothing Says So

WHEN `AnswerResult.attribution` is `"reported"` and `citations` is empty —
the answer itself reported drawing on none of the concepts retrieved for it
(issue #753) — `query` MUST print one stderr warning saying the answer
stands on nothing in the bundle. It MUST NOT print that warning when
`attribution` is `"absent"` or `"unparsed"`: there the citation list was not
decided by the answer, so its emptiness is a retrieval fact rather than a
finding about support, and warning on it would fire on every backend that
ignores the attribution instruction.

The warning is stderr-only and MUST NOT change the exit code. The answer
text still prints: a reply the model wrote from its own knowledge is not an
error, it is a reply whose authority must not be borrowed from the bundle.

#### Scenario: An unsupported answer announces itself

- GIVEN an answer with `attribution` `"reported"` and no citations
- WHEN `openkos query "<question>"` completes
- THEN stdout carries the answer text with no `Citations:` block, stderr
  carries the warning naming `context_block_count` — never `fused_count`,
  which counts concepts the model may never have been shown — and the exit
  code is `0`

#### Scenario: A non-reporting backend prints no such warning

- GIVEN an answer with `attribution` `"absent"` and no citations
- WHEN `openkos query "<question>"` completes
- THEN no such warning appears on stderr

### Requirement: Sensitivity Is The High-Water-Mark Of Cited Concepts

WHEN filing via `--save`, `query` MUST re-read each cited concept's
frontmatter and set the filed concept's sensitivity to the high-water-mark
(`okf.combine_sensitivity`) across them, seeded at `cfg.default_sensitivity`.
An unreadable OR unparseable cited concept MUST fold the running floor to
`confidential` -- the most-restrictive level, NOT be skipped -- fail-closed,
consistent with the project's pervasive "cannot verify sensitivity ->
confidential" stance (`okf._rank`, `sensitivity.blocks_llm_send`). WHEN
`--include-confidential` caused a confidential cited concept to be used, the
filed concept's sensitivity MUST be confidential. WHEN there are zero
citations, `query` MUST REFUSE to file, exit non-zero, and leave the bundle
unchanged -- `build_concept` requires non-empty provenance, and a sourceless
"derived" concept is not a real derived node. WHEN the filed concept's OKF
`--type` has a configured per-type sensitivity offset
(`type-sensitivity-defaults`), the cited-concept high-water-mark computed
above is a floor, not the final value: the filed sensitivity is
`combine_sensitivity(cited_high_water_mark, raise_by(cfg.default_sensitivity,
offset))`, so a type-defaulted filed answer may be saved strictly above the
cited high-water-mark, never below it. The success message MUST carry the
born-above-floor advisory (`type-sensitivity-defaults`) whenever this raise
applies.

#### Scenario: Confidential citation propagates confidentiality

- GIVEN `--include-confidential` is set and one cited concept is
  confidential
- WHEN `openkos query "<question>" --save` files the answer
- THEN the filed concept's sensitivity is confidential

#### Scenario: A type-defaulted filed answer is saved above the cited high-water-mark

- GIVEN `openkos query "<question>" --save --type Person` where every cited
  concept's high-water-mark resolves to `public`, and a per-type sensitivity
  offset configured for `Person` that raises the workspace floor to
  `private`
- WHEN the answer is filed
- THEN the saved concept's `sensitivity` is `private`, strictly above the
  cited high-water-mark, and the success message carries the
  born-above-floor advisory naming it

#### Scenario: Unreadable or unparseable citation folds to confidential

- GIVEN a cited concept's file is missing, or its frontmatter is
  unparseable, at save time
- WHEN `openkos query "<question>" --save` files the answer
- THEN the filed concept's sensitivity is confidential, not the seeded
  default

#### Scenario: Zero citations refuse to file

- GIVEN zero readable citations
- WHEN `openkos query "<question>" --save` is run
- THEN `query` refuses, exits non-zero, and the bundle is unchanged

#### Scenario: A raised high-water mark is disclosed in the preview

- GIVEN the fold raises the filed concept's sensitivity above
  `cfg.default_sensitivity`
- WHEN the `--save` proposed-changes preview is printed
- THEN the concept line names the inherited level (e.g.
  `(sensitivity: confidential, inherited from citations)`), so the user
  consents knowing what will be written (issue #569)

#### Scenario: A fold landing on the default stays undisclosed

- GIVEN the fold lands exactly on `cfg.default_sensitivity`
- WHEN the `--save` proposed-changes preview is printed
- THEN the concept line carries no sensitivity annotation

### Requirement: Preview, Confirm, And Non-TTY Gate For `--save`

`--save` MUST reuse ingest's stage → preview → confirm → write pipeline:
`query` MUST show a preview of the additions (new bundle file, `index.md`,
`log.md`) before writing. WHEN running on a TTY with review enabled, `query`
MUST prompt for confirmation unless `--auto` is passed. WHEN running
non-interactively (no TTY) with review enabled and `--auto` is absent,
`query` MUST refuse to write and exit non-zero, leaving the bundle
unchanged.

#### Scenario: TTY confirms before writing

- GIVEN an interactive TTY and review enabled
- WHEN `openkos query "<question>" --save` is run without `--auto`
- THEN a preview of the new file, index, and log changes is shown and the
  write proceeds only after confirmation

#### Scenario: `--auto` or `review: false` bypasses the prompt

- GIVEN `--auto` is passed, or config sets `review: false`
- WHEN `openkos query "<question>" --save` is run
- THEN the preview is shown and the write proceeds without prompting

#### Scenario: Non-TTY without `--auto` refuses to write

- GIVEN no TTY is attached, review is enabled, and `--auto` is absent
- WHEN `openkos query "<question>" --save` is run
- THEN `query` refuses to write, exits non-zero, and the bundle is
  unchanged

### Requirement: Filed Concept Is Not Auto-Reindexed

`--save` MUST NOT trigger `reindex`. After a successful write, `query` MUST
print a distinct "filed answer" log entry and a stdout/stderr hint telling
the user to run `openkos reindex` so the new concept becomes retrievable.

#### Scenario: Successful filing hints at reindex

- GIVEN `openkos query "<question>" --save` files a concept successfully
- WHEN the write completes
- THEN a "filed answer" log entry is recorded and output hints at running
  `openkos reindex`

## Note

This change also includes two test/doc-only follow-ups to the already-merged
`query-answer` capability — a `_SYSTEM_PROMPT` docstring and a multi-survivor
citation-ordering test. Neither alters any `query-answer` requirement, so
`query-answer/spec.md` is unchanged.
