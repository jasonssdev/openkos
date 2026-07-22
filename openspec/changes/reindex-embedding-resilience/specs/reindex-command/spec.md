# Delta for Reindex Command

## ADDED Requirements

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
(server unreachable) or `OllamaModelNotFound` (configured model not
installed), `reindex` MUST NOT treat it as a per-doc failure — an
environment that cannot serve any embed is not a transient per-input
failure. `reindex` MUST re-raise it and let it propagate to the existing
"Error Ladder Mirrors `query`" requirement unchanged: a clear stderr
message and exit 1, with no further queued docs processed after the raise.
Per-doc isolation applies ONLY to the generic transient `OllamaError`,
never to these two fatal subclasses.

`ReindexReport.embedded` MUST equal the count of docs successfully embedded
this run.

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
(`OllamaUnavailable`/`OllamaModelNotFound`, see the Per-Doc Embed Failure Is
Isolated, Not Fatal requirement) exits 1 before reaching this notice — the
notice applies only to a run that completes with exit 0.

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

## MODIFIED Requirements

### Requirement: Embedding-Model Tag Gate Forces Full Re-Embed On Mismatch

At the start of the vector reindex pass, `reindex()` MUST read the stored
`embedding_model` tag from `vectors.db`'s `meta` table and compare it against
the explicit `model_tag` param passed in for this run. If the stored tag is
absent OR differs from `model_tag`, the vector-store pass for this run MUST
behave as if `force=True` (bypass the content_hash cache gate; every
discovered, readable doc is queued for re-embedding via the existing
`upsert_many` DELETE+INSERT path — no vec0 DROP), and after the embed pass
completes, the new `model_tag` MUST be persisted as the stored tag ONLY WHEN
this run's `skipped` count is `0` AND its `embed_failed` count is `0`. WHEN
one or more queued docs were left un-(re)embedded this run — via a
permanent `skipped` (unreadable/parse/decode) OR a transient `embed_failed`
(embed EOF exhausted retries; see the Per-Doc Embed Failure Is Isolated,
Not Fatal requirement) — the tag MUST NOT be persisted, so the NEXT run
with the same `model_tag` still sees the mismatch and re-forces the full
re-embed, giving the previously-unhealed doc(s) another chance; this
repeats until one run finally reaches `skipped == 0 AND embed_failed == 0`,
at which point the tag is persisted and the store is no longer transiently
mixed-model. This gate is independent of the `--force` CLI flag (either can
trigger the same force-mode behavior) and MUST NOT affect the
`_reindex_fts`/graph pass, which stays gated solely by the bundle-manifest
hash.
(Previously: the tag was described as persisted unconditionally after the
embed batch completed, with no accounting for a skipped or transiently
embed-failed doc during a model-change run.)

#### Scenario: Model mismatch forces full re-embed regardless of content_hash

- GIVEN a `vectors.db` with a stored tag `'model-a'` and every doc's
  content_hash already matching `vector_meta`
- WHEN `reindex()` runs with `model_tag='model-b'`
- THEN every discovered doc is re-embedded and upserted, and the stored tag
  becomes `'model-b'`

#### Scenario: Absent tag (pre-slice vectors.db) forces one re-embed then self-heals

- GIVEN a `vectors.db` created before this change, with no `meta` table row
  for `embedding_model`
- WHEN `reindex()` runs once with `model_tag='model-a'`
- THEN every discovered doc is re-embedded this run, the stored tag becomes
  `'model-a'`, and the NEXT `reindex()` run with the same `model_tag` is
  purely incremental (content_hash gate governs normally)

#### Scenario: Matching tag leaves the content_hash gate unchanged

- GIVEN a stored tag equal to the current `model_tag`
- WHEN `reindex()` runs
- THEN cache-hit/changed/new classification for each doc follows the
  existing content_hash comparison exactly as before this change

#### Scenario: Model-tag mismatch does not trigger an FTS/graph rebuild

- GIVEN a stored tag that differs from `model_tag`, and a bundle whose
  documents are otherwise unchanged
- WHEN `reindex()` runs
- THEN the FTS and graph derived indexes are NOT rebuilt by this gate (only
  the bundle-manifest hash, unaffected by the model tag, governs their
  rebuild)

#### Scenario: Any left-behind doc during a model-change run withholds the tag and self-heals

- GIVEN a model-change run where one doc is left un-embedded this run —
  either a permanent `skipped` (unreadable/parse/decode) or a transient
  `embed_failed` (embed EOF exhausted retries) — and the rest succeed
- WHEN `reindex()` completes this run
- THEN the stored tag remains the OLD (or absent) value, NOT `model_tag`,
  and the NEXT `reindex()` call with the same `model_tag` re-forces a full
  re-embed of every doc (`model_changed` stays `True`) until a run finally
  reaches `skipped == 0 AND embed_failed == 0`, at which point the tag is
  persisted

#### Scenario: Partial embed failure during a model change leaves a transient mixed-model store

- GIVEN a model-change run where some docs succeed on the new model and one
  transiently fails (`embed_failed`) and keeps its old-model vector
- WHEN that run completes
- THEN the store transiently contains both new-model (survivor) and
  old-model (failed) vectors simultaneously, `query` retrieval is
  unaffected (dense search does not depend on the stored tag), and the
  mixed state is surfaced to the user via the actionable re-run notice
  rather than left silent
