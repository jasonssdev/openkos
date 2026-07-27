# Delta for Reindex Command

## MODIFIED Requirements

### Requirement: Per-Doc Embed Failure Is Isolated, Not Fatal

`reindex` MUST embed each queued document individually (per-doc grain,
`embedder.embed([text])` per doc) rather than as one whole-batch call. WHEN
an individual doc's embed call raises the generic transient `OllamaError`
(the HTTP-400 EOF class) and the retry budget (`llm-client`) is exhausted,
`reindex` MUST catch that failure and increment a dedicated
`ReindexReport.embed_failed` tally — never embedded, never pruned this run,
and DISTINCT from the existing `ReindexReport.skipped` count (which stays
reserved for permanent read/parse/decode failures; the two counts MUST NOT
be conflated) — then continue processing every remaining queued doc. A
transient embed failure MUST NOT abort the run: `reindex` MUST still
perform its single end-of-run commit (covering every successfully embedded
doc plus any pruning, unchanged atomic-commit contract) and MUST exit 0.

WHEN an individual doc's embed call instead raises `OllamaUnavailable`
(server unreachable), `OllamaModelNotFound` (configured model not
installed), OR `OllamaEmbeddingDimensionMismatch` (the configured
embedding model returns vectors of the wrong length — a permanent,
non-healing misconfiguration, not a transient per-input failure),
`reindex` MUST NOT treat it as a per-doc failure. `reindex` MUST re-raise
it and let it propagate to the existing "Error Ladder Mirrors `query`"
requirement unchanged: a clear stderr message and exit 1, with no further
queued docs processed after the raise. `OllamaEmbeddingDimensionMismatch`
MUST be checked ahead of the generic `except OllamaError` catch, alongside
`OllamaUnavailable`/`OllamaModelNotFound` — a bare `except OllamaError`
here would silently swallow it as a transient per-doc skip and reproduce
the exact misclassification this requirement closes. Per-doc isolation
applies ONLY to the generic transient `OllamaError`, never to these three
fatal subclasses.

`ReindexReport.embedded` MUST equal the count of docs successfully embedded
this run.
(Previously: only `OllamaUnavailable`/`OllamaModelNotFound` were checked
ahead of the generic `except OllamaError` catch; a wrong-dimension row
raised the generic `OllamaError` and was silently absorbed into
`embed_failed`, so the "will retry next run" notice fired for a failure
that retrying can never fix.)

#### Scenario: One poison doc among many survives as a partial-progress run

- GIVEN a batch of 10 queued docs where doc #4's embed call raises the
  transient generic `OllamaError` after exhausting the retry budget and the
  other 9 succeed
- WHEN `openkos reindex` runs
- THEN the 9 survivors are embedded, upserted, and committed;
  `ReindexReport.embedded` is `9`; `ReindexReport.embed_failed` is `1`; and
  the process exits `0`

#### Scenario: Survivors are committed and immediately queryable

- GIVEN the same partial-failure run as above
- WHEN `openkos query "<question>"` runs immediately afterward against one
  of the 9 successfully embedded concepts
- THEN that concept is retrievable via dense search — its vector was part
  of the run's single end-of-run commit, not discarded

#### Scenario: Every queued doc transiently fails leaves an empty embed pass, not a crash

- GIVEN every queued doc's embed call raises the transient generic
  `OllamaError` after exhausting the retry budget
- WHEN `openkos reindex` runs
- THEN `ReindexReport.embedded` is `0`, `ReindexReport.embed_failed` equals
  the number of queued docs, no exception propagates, and the process
  exits `0`

#### Scenario: Unreachable Ollama mid-embed-loop is fatal, not a per-doc skip

- GIVEN Ollama becomes unreachable partway through the per-doc embed loop
  (some docs already embedded successfully, `OllamaUnavailable` raised on
  the next queued doc)
- WHEN `openkos reindex` runs
- THEN it does NOT count that doc as `embed_failed`, does NOT proceed to
  the remaining queued docs, prints the existing clear stderr message, and
  exits `1` — exactly the "Error Ladder Mirrors `query`" behavior for an
  unreachable server, unaffected by per-doc isolation

#### Scenario: Missing embedding model mid-embed-loop is fatal, not a per-doc skip

- GIVEN the configured embedding model is not installed and
  `OllamaModelNotFound` is raised while embedding a queued doc
- WHEN `openkos reindex` runs
- THEN it does NOT count that doc as `embed_failed`, prints the existing
  clear stderr message, and exits `1`, exactly as the existing "Error
  Ladder Mirrors `query`" requirement specifies for a missing model

#### Scenario: Dimension mismatch mid-embed-loop is fatal, not a per-doc skip

- GIVEN the configured embedding model returns a wrong-length vector row
  and `OllamaEmbeddingDimensionMismatch` is raised while embedding a
  queued doc
- WHEN `openkos reindex` runs
- THEN it does NOT count that doc as `embed_failed`, does NOT proceed to
  the remaining queued docs, prints a clear stderr message identifying the
  failure as a permanent dimension mismatch (not "will retry next run"),
  and exits `1`

### Requirement: Reindex Surfaces An Actionable Re-Run Notice On Embed-Failure Skips

WHEN `ReindexReport.embed_failed > 0` — one or more docs were skipped
specifically because their embed call transiently failed (the generic
`OllamaError` EOF class) after the retry budget was exhausted — `reindex`
MUST print a distinct, actionable stderr notice stating that this run is
INCOMPLETE and advising the user to run `openkos reindex` again to
complete it. This notice keys ONLY on `embed_failed`, NEVER on the existing
`skipped` count (permanent unreadable/parse/decode failures): an
`embed_failed` doc is transient and self-healing (a re-run gives it another
chance once Ollama recovers), whereas a `skipped` doc is not (re-running
without fixing the source file does not help), and the two MUST NOT be
conflated in this notice. The same notice MUST also fire when a partial
embed failure occurs during a model-change run (see the Embedding-Model Tag
Gate requirement below): the store then transiently holds a mix of
new-model (survivor) and old-model (failed) vectors until a later run
reaches `skipped == 0 AND embed_failed == 0`; the user MUST be told the
reindex is incomplete rather than left to discover the mixed state
silently. A run whose embed loop instead hits a FATAL error
(`OllamaUnavailable`/`OllamaModelNotFound`/`OllamaEmbeddingDimensionMismatch`,
see the Per-Doc Embed Failure Is Isolated, Not Fatal requirement) exits 1
before reaching this notice — the notice applies only to a run that
completes with exit 0. A dimension-mismatch exit MUST NOT be worded as
"will retry next run"; that phrasing is reserved for the transient
`embed_failed` case this notice covers.
(Previously: the fatal-exit exclusion named only `OllamaUnavailable`/
`OllamaModelNotFound`; a dimension mismatch reached this transient notice
instead, because it was misclassified as `embed_failed` rather than
exiting 1 first.)

#### Scenario: Embed-failure skip prints the actionable re-run notice

- GIVEN a run where `ReindexReport.embed_failed >= 1` after exhausting
  retries on at least one doc, and every other queued doc succeeded
- WHEN `openkos reindex` completes
- THEN stderr contains a notice stating the run is incomplete and advising
  the user to run `openkos reindex` again

#### Scenario: An ordinary unreadable-file skip does not print the embed-failure notice

- GIVEN a run where `ReindexReport.skipped >= 1` (a doc could not be read
  or its frontmatter could not be parsed) and `ReindexReport.embed_failed`
  is `0`
- WHEN `openkos reindex` completes
- THEN stderr does NOT contain the embed-failure re-run notice (the
  existing unreadable-file diagnostics remain unchanged)

#### Scenario: Model-switch run with a partial embed failure prints the same notice

- GIVEN a model-change run (`model_tag` differs from the stored tag) where
  one doc's embed transiently fails after retries and the rest succeed
- WHEN `openkos reindex` completes
- THEN the new `model_tag` is NOT persisted this run (per the
  Embedding-Model Tag Gate requirement), survivors are committed on the new
  model, the failed doc's vector remains on the old model, and stderr
  contains the same actionable incomplete-run notice

#### Scenario: Dimension mismatch never reaches the transient re-run notice

- GIVEN a run whose embed loop raises `OllamaEmbeddingDimensionMismatch`
  on some queued doc
- WHEN `openkos reindex` exits 1
- THEN the transient "will retry next run" notice is NOT printed for that
  doc; the permanent dimension-mismatch stderr message (see Per-Doc Embed
  Failure Is Isolated, Not Fatal) is the only failure message shown
