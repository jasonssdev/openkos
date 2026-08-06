# Query Answer Specification

## Note (D2 alignment)

Per design D2, staleness detection (bundle-manifest comparison) is
**reindex's exclusive responsibility** — a properly-reindexed handle is
always fresh at query time. `answer()`/`query` MUST NEVER recompute or
compare the current bundle's manifest hash; a full-bundle walk at query time
would reintroduce the exact per-query cost this slice removes. The only
degrade triggers at query time are an **absent** (`None`) handle or a
persisted store that is **unopenable/corrupt**. Edit-staleness ("stale until
the next `reindex`") is captured as reindex's responsibility in
`derived-index-cache`/`reindex-command`, mirroring how dense already
behaves.

## Purpose

`retrieval/answer.py` is a pure library seam answering a natural-language
question from a compiled bundle: it retrieves lexical and dense hits via
`FtsIndex` and `VectorStore`, fuses them into a single ranked list, assembles
matched concept bodies into an LLM context, calls an injected `LLMBackend`,
and returns a cited `AnswerResult`. No CLI, no config wiring; its only
consumer is the `query` command.

## Non-Goals

CLI command; reading/constructing `openkos.config`; context truncation or
token budget beyond `limit`; weighted/normalized score fusion; distance-to-similarity
conversion; graph/link ranking in any position — as a third RRF input or as
an additive reserved-slot channel (see "Answering Reads No Graph"); filing
the answer back as a concept; citation metadata beyond `concept_id` and
`title`.

## Requirements

### Requirement: FTS Retrieval Reads A Persisted, Read-Only Index Handle And Degrades To Empty

`answer` MUST NOT build an FTS index itself. It MUST accept an injected
`fts_index` handle (read-only, opened by the caller against the persisted
on-disk FTS store) and, WHEN that handle is absent (`None`) or its backing
on-disk store is unopenable/corrupt, MUST proceed with an empty FTS hit list
rather than raising or attempting to build one — mirroring the existing
dense-degrade contract. `answer` MUST NOT recompute or compare the current
bundle's manifest hash; that comparison is reindex's exclusive job.

#### Scenario: Absent FTS handle degrades to empty, not raise

- GIVEN `fts_index` is `None` (workspace never ran `reindex`)
- WHEN `answer(...)` is called
- THEN retrieval proceeds using dense hits alone, `fts_hit_count`
  is `0`, and no exception propagates

#### Scenario: Corrupt or unopenable FTS handle degrades to empty

- GIVEN an `fts_index` handle whose backing on-disk store cannot be opened
  (e.g. a corrupt file), and no query-time manifest comparison is performed
- WHEN `answer(...)` is called
- THEN retrieval proceeds as if `fts_index` were absent, and no exception
  propagates

#### Scenario: Successfully opened handle is queried normally

- GIVEN an `fts_index` handle successfully opened against the persisted
  on-disk store that `reindex` wrote for the current bundle (query does not
  itself recompute or compare a manifest hash)
- WHEN `answer(...)` is called
- THEN `fts_index.search(question, limit=pool_limit)` is called and its hits
  feed the fused list as before

### Requirement: Lexical Retrieval Drives Answer Assembly

`answer(question, *, bundle_dir, llm, embedder, vector_store, fts_index,
limit)` MUST retrieve FTS hits via the injected, read-only
`fts_index.search(question, limit=pool_limit)` handle AND dense hits via
`vector_store.query(embedder.embed([question])[0], k=pool_limit)`
(`pool_limit = max(limit, 10)`), fuse both lists via
`retrieval.fusion.fuse(...)` into one ordered `concept_id` list, place each
fused hit's concept body — in fused order, truncated to `limit` — into the
LLM context, call `llm.chat(...)` exactly once, and return an `AnswerResult`
whose `answer` is the LLM's returned text.
(Previously: FTS retrieval built its own `:memory:` index internally via
`fts.build_index(bundle_dir)` on every call; there was no injected FTS
handle. Previously: the signature also took a `graph_index` handle, and the
fused list was topped up by a graph channel before truncation — issue #434
removed both.)

#### Scenario: Matching concepts produce a cited answer

- GIVEN a bundle containing concepts that match the question lexically
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm, embedder=embedder,
  vector_store=vector_store, fts_index=fts_index)` is called
- THEN both `fts_index.search` and `vector_store.query` are called, the
  fused list feeds context assembly, `llm.chat` is called exactly once, and
  `AnswerResult.answer` equals the LLM's response text

#### Scenario: Dense-only match is retrievable

- GIVEN a concept matches the question's meaning but shares no lexical
  tokens with it, so it is absent from FTS hits but present in dense hits
- WHEN `answer(...)` is called
- THEN that concept's body is placed in context via the fused list, and it
  appears in `citations`

### Requirement: Default Retrieval Limit

`limit` MUST default to 5. Each retriever MUST be called with
`pool_limit = max(limit, 10)`; `fuse`'s output MUST be truncated to `limit`
before context assembly.

#### Scenario: Caller omits limit

- GIVEN a caller invokes `answer` without a `limit` argument
- WHEN retrieval executes
- THEN both `FtsIndex.search` and `vector_store.query` are called with
  `pool_limit=10`, and the fused, truncated context contains at most 5
  concepts

### Requirement: Zero Hits Return A Canned No-Match Result

WHEN both `FtsIndex.search` and `vector_store.query` return no hits,
`answer` MUST return an `AnswerResult` with empty `citations` and a stable,
non-empty no-match message, and MUST NOT call `llm.chat`. A hit from either
retriever alone MUST be sufficient to avoid this path.
(Previously: zero hits was determined by FTS alone.)

#### Scenario: No matching concepts found in either list

- GIVEN a question with zero FTS hits and zero dense hits
- WHEN `answer(...)` is called
- THEN `llm.chat` is never invoked, `citations` is empty, and `answer` is a
  non-empty no-match message

#### Scenario: Dense-only hit avoids the zero-hit path

- GIVEN zero FTS hits but at least one dense hit
- WHEN `answer(...)` is called
- THEN `llm.chat` is invoked and `no_match_cause` is not `"zero_hits"`

### Requirement: Guarded Re-Read Skips Unreadable Concepts

If a concept returned by `search` cannot be read or its OKF frontmatter
cannot be parsed at answer time, `answer` MUST skip it — excluding it from
context and citations — rather than raise. WHEN every hit is unreadable,
`answer` MUST degrade to the zero-hit no-match path.

#### Scenario: One hit vanished after indexing

- GIVEN one FTS hit's concept file was deleted after the index build
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN that concept is excluded from context and `citations`, no error is
  raised, and `llm.chat` still runs with the remaining readable concepts

#### Scenario: All hits unreadable

- GIVEN every FTS hit's concept is missing or has unparsable frontmatter
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN `llm.chat` is never invoked and the result matches the zero-hit
  no-match contract

### Requirement: Typed Exceptions Propagate Unswallowed

`answer` MUST NOT catch or suppress `FtsUnavailable` or any `OllamaError`
family member raised by `llm.chat`; these MUST propagate to the caller
unchanged. `OllamaUnavailable`, `OllamaModelNotFound`, and
`OllamaEmbeddingDimensionMismatch` raised while embedding the question
(`embedder.embed([question])`) MUST ALSO propagate unswallowed — the first
two are environment-fatal and the third is a permanent misconfiguration of
the configured embedding model; none of the three is per-question
transient. The GENERIC transient `OllamaError` raised while embedding the
question is the ONLY exception to this rule: it is caught and handled by
the Dense Retrieval Degrades To FTS-Only requirement instead, and MUST NOT
propagate from `answer`.

(Previously: only `OllamaUnavailable` and `OllamaModelNotFound` propagated
from the question-embed step; `OllamaEmbeddingDimensionMismatch` was
swallowed by the generic transient `OllamaError` degrade instead.)

#### Scenario: FTS index unavailable

- GIVEN the bundle's FTS index cannot be built or opened
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN `FtsUnavailable` propagates to the caller

#### Scenario: LLM backend fails

- GIVEN `llm.chat` raises an `OllamaError`-family exception
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN that same exception propagates to the caller unchanged

#### Scenario: Question-embed generic transient failure does not propagate

- GIVEN `embedder.embed([question])` raises the generic transient
  `OllamaError`
- WHEN `answer(...)` is called
- THEN that exception does NOT propagate from `answer`; it is handled per
  the Dense Retrieval Degrades To FTS-Only requirement instead

#### Scenario: Question-embed fatal subclasses still propagate

- GIVEN `embedder.embed([question])` raises `OllamaUnavailable` or
  `OllamaModelNotFound`
- WHEN `answer(...)` is called
- THEN that exception propagates to the caller unchanged, exactly like an
  `OllamaError`-family exception from `llm.chat`

#### Scenario: Question-embed dimension mismatch still propagates

- GIVEN `embedder.embed([question])` raises
  `OllamaEmbeddingDimensionMismatch` (the configured embedding model does
  not emit `EMBED_DIM`-dimensional vectors)
- WHEN `answer(...)` is called
- THEN that exception propagates to the caller unchanged rather than being
  degraded — a wrong dimension is permanent, so no retry and no re-run can
  make dense retrieval possible

### Requirement: Module Is Config-Free And Backend-Injected

`retrieval/answer.py` MUST NOT import `openkos.config`. `LLMBackend`,
`Embedder`, `VectorStore`, and `fts_index` instances MUST all be supplied
by the caller; the module MUST NOT construct, open, or select any of them
itself.
(Previously: `LLMBackend`, `Embedder`, and `VectorStore` were caller-injected;
`fts_index` and `graph_index` did not exist as parameters — the module built
its own FTS index and graph internally. Previously: `graph_index` was a
fourth injected handle; issue #434 removed the stage that read it.)

#### Scenario: Module has no config dependency

- GIVEN a static import check of `retrieval/answer.py`
- WHEN its imports are inspected
- THEN `openkos.config` is absent, and the only sources of `LLMBackend`,
  `Embedder`, `VectorStore`, and `fts_index` are the parameters passed by
  the caller

### Requirement: Citations Reflect Only Context-Included Concepts

Every `Citation(concept_id, title)` in `citations` MUST correspond to a
concept whose body was actually placed in the LLM context for that call.
Concepts skipped under guarded re-read, or never retrieved, MUST NOT
appear in `citations`.

#### Scenario: Citation set matches context set exactly

- GIVEN a mix of readable and unreadable hits returned by `search`
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN `citations` contains exactly one `Citation` per concept placed in
  context, with `title` read from that concept's OKF frontmatter

### Requirement: AnswerResult Carries Retrieval Metadata

`AnswerResult` MUST carry: `fts_hit_count` (int, raw `FtsIndex.search` hit
count before guarded re-read filtering), `llm_invoked` (bool),
`no_match_cause` (`NoMatchCause = Literal["none", "empty_query", "zero_hits",
"all_unreadable"]`, `"none"` on a successful answer, else whichever guard
tripped), and `skip_notices` (`list[str]`, copied from `FtsIndex.skipped` for
that build) — UNCHANGED from the existing contract. `AnswerResult` MUST
additionally, and PURELY ADDITIVELY, carry: `dense_hit_count` (int, raw
`vector_store.query` hit count), `fused_count` (int, number of distinct
`concept_id`s in the FINAL fused, limit-truncated list), and
`dense_degraded` (bool). `AnswerResult` MUST NOT carry any graph metadata:
`graph_hit_count`, `graph_degraded`, and `graph_contributed_count` MUST all
be absent. The module MUST remain config-free.
(Previously: those three fields existed — `graph_hit_count` the raw
personalized-PageRank candidate pool, `graph_degraded` whether the graph
stage could run, and `graph_contributed_count` how many reserved slots the
graph filled with concepts absent from the FTS+dense pool. All three
described a channel issue #434 removed; a field that could only ever report
zero would read as a channel that contributed nothing, rather than one that
is not there.)

#### Scenario: Successful answer sets success metadata

- GIVEN a question with readable, matching hits
- WHEN `answer(...)` returns a non-`NO_MATCH` answer
- THEN `llm_invoked` is `True` and `no_match_cause` is `"none"`

#### Scenario: AnswerResult reports no graph metadata

- GIVEN the `AnswerResult` dataclass
- WHEN its fields are inspected
- THEN `graph_hit_count`, `graph_degraded`, and `graph_contributed_count`
  are all absent

### Requirement: Empty Query Sets A Distinct No-Match Cause

WHEN `question.strip()` is empty, `answer` MUST short-circuit BEFORE any
retrieval — it MUST NOT call `fts_index.search`, `embedder.embed`, or
`vector_store.query` — and MUST NOT invoke the LLM, returning a no-match
`AnswerResult` with `no_match_cause` equal to `"empty_query"`,
distinguishable from `"zero_hits"`. This MUST be provable via test doubles
(spies) on `fts_index`, `embedder`, and `vector_store`, each recording zero
calls for this path.
(Previously: short-circuited before internally-built FTS/dense/graph steps;
there were no injected handles for a test spy to observe, so the strongest
available assertion was that the LLM was never called. Previously: a fourth
spy covered `graph_index`.)

#### Scenario: Whitespace-only question touches no injected handle

- GIVEN `question` is empty or contains only whitespace, and `fts_index`,
  `embedder`, and `vector_store` are all spies
- WHEN `answer(...)` is called
- THEN none of the three spies record any call, `llm.chat` is never invoked,
  and `no_match_cause` is `"empty_query"`

### Requirement: Answering Reads No Graph

`answer` MUST compute its final ranking as `fusion.fuse(hits, vec_hits)`
sliced to `limit`, and feed that list unchanged into `_assemble_context`.
It MUST NOT accept a `graph_index` parameter, MUST NOT import
`openkos.graph` or `retrieval.graph_retrieve`, MUST NOT derive graph seeds
from the fused list, and MUST NOT run any second retrieval stage.

(Previously: `answer` derived SEEDS as the top `min(limit, 5)`
`concept_id`s of an INITIAL `fuse(hits, vec_hits)`, read an injected,
read-only, persisted `graph_index` handle, and ran personalized PageRank
(`nx.pagerank`, `alpha=0.85`, over an undirected view) for a `graph_hits`
pool of size `max(limit, 10)`. A FINAL
`fusion.fuse_with_graph(hits, vec_hits, graph_hits, limit=limit)` then let
that pool fill bounded reserved tail slots with concepts absent from the
FTS+dense pool. The stage degraded rather than raised — an absent handle,
absent seeds, or a PageRank exception yielded `graph_hits = []` and
`graph_degraded=True`, while an edgeless-but-openable projection yielded
`[]` and `graph_degraded=False` — and its ranking was deterministic across
repeated calls.

None of that was wrong as implemented; the stage was correct, bounded and
measurable, which is precisely what allowed it to be judged. Two A/B runs of
10 questions found 7 harmful, 3 neutral and 0 beneficial contributions,
including evicting `sources/mcp-origin` from "When did MCP originate?" and
`sources/10-mcp` from a question about which protocol BigQuery belongs to.
Seeded PPR ranks by GLOBAL CENTRALITY, not by relevance to the question, and
the reserved slot always costs a base hit. Growing the corpus changes which
central node wins the slot and nothing else. The typed graph is retained for
contradiction-candidate derivation, which reads typed edges rather than
centrality; a future graph channel would need a different ranking function —
traversal from the question's own matched concepts — proposed and measured
on its own terms.)

#### Scenario: The graph plays no part in the answer

- GIVEN a bundle whose typed graph strongly connects some concept that
  matches the question neither lexically nor semantically
- WHEN `answer(...)` is called
- THEN that concept is absent from the citations, and the citations are
  exactly the first `limit` entries of `fuse(hits, vec_hits)`

#### Scenario: answer() has no graph seam to inject

- GIVEN the signature of `answer`
- WHEN its parameters are inspected
- THEN there is no `graph_index` parameter, and `retrieval/answer.py`
  imports nothing from `openkos.graph`

#### Scenario: A corrupt or absent graph store cannot affect answering

- GIVEN `.openkos/graph.db` is absent, or present but corrupt
- WHEN `answer(...)` is called
- THEN the answer, its citations, and every `AnswerResult` count are
  byte-identical to the same call against a healthy graph store

### Requirement: Dense Retrieval Degrades To FTS-Only

WHEN dense retrieval cannot proceed — an absent/empty `vectors.db`, a
`VecUnavailable`, a read-path `sqlite3.Error` raised by
`vector_store.query`, OR the GENERIC transient `OllamaError` raised while
embedding the question (`embedder.embed([question])`) — `answer` MUST catch
it, proceed using the FTS list alone as the fused input (equivalent to an
empty dense list), set `dense_degraded=True` on the returned `AnswerResult`,
and MUST NOT raise. `answer` MUST NOT degrade on `OllamaUnavailable` (server
unreachable), `OllamaModelNotFound` (configured embedding model not
installed), or `OllamaEmbeddingDimensionMismatch` (configured embedding
model does not emit `EMBED_DIM`-dimensional vectors) raised from the
question-embed step — these three subclasses are environment-fatal or
permanently misconfigured, not per-question transient, and MUST propagate
unswallowed to the caller so `query` reaches its existing fatal exit-1
ladder. `FtsUnavailable` and any `OllamaError`-family exception raised by
`llm.chat` (the LLM completion path, not the question-embed step) also
remain unaffected and continue to propagate unchanged.

(Previously: only `OllamaUnavailable` and `OllamaModelNotFound` were
excluded from the degrade; `OllamaEmbeddingDimensionMismatch` set
`dense_degraded=True` and produced a silent FTS-only answer.)

#### Scenario: Cold store (never reindexed) degrades cleanly

- GIVEN `vectors.db` does not exist (workspace never ran `reindex`)
- WHEN `answer(...)` is called
- THEN retrieval proceeds using FTS hits alone, `dense_hit_count` is `0`,
  `dense_degraded` is `True`, and no exception propagates

#### Scenario: VecUnavailable degrades to FTS-only

- GIVEN `vector_store.query` raises `VecUnavailable`
- WHEN `answer(...)` is called
- THEN retrieval proceeds using FTS hits alone and no exception propagates

#### Scenario: Read-path sqlite3.Error degrades to FTS-only

- GIVEN `vector_store.query` raises `sqlite3.Error` (e.g. a locked or
  corrupt `vectors.db`)
- WHEN `answer(...)` is called
- THEN retrieval proceeds using FTS hits alone and no exception propagates

#### Scenario: Question-embed generic transient OllamaError degrades to FTS-only, not exit 1

- GIVEN `embedder.embed([question])` raises the generic transient
  `OllamaError` (e.g. the flaky EOF embedding path), not `OllamaUnavailable`
  or `OllamaModelNotFound`
- WHEN `answer(...)` is called
- THEN retrieval proceeds using FTS hits alone, `dense_degraded` is `True`,
  no exception propagates from `answer`, and the caller (`query`) still
  exits 0 with its standard stderr retrieval summary

#### Scenario: Question-embed OllamaUnavailable propagates to query's fatal ladder

- GIVEN `embedder.embed([question])` raises `OllamaUnavailable` (Ollama
  server unreachable)
- WHEN `answer(...)` is called
- THEN that exception propagates from `answer` unswallowed, `dense_degraded`
  is NEVER set, and the caller (`query`) exits 1 via its existing
  server-unreachable message, not a degraded FTS-only answer

#### Scenario: Question-embed OllamaModelNotFound propagates to query's fatal ladder

- GIVEN `embedder.embed([question])` raises `OllamaModelNotFound` (the
  configured embedding model is not installed)
- WHEN `answer(...)` is called
- THEN that exception propagates from `answer` unswallowed, `dense_degraded`
  is NEVER set, and the caller (`query`) exits 1 via its existing
  model-not-installed message, not a degraded FTS-only answer

#### Scenario: Question-embed dimension mismatch propagates to query's fatal ladder

- GIVEN `embedder.embed([question])` raises
  `OllamaEmbeddingDimensionMismatch` (the configured embedding model returns
  wrong-length vectors)
- WHEN `answer(...)` is called
- THEN that exception propagates from `answer` unswallowed, `dense_degraded`
  is NEVER set, and the caller (`query`) exits 1 via its dimension-mismatch
  message, not a degraded FTS-only answer at exit 0

#### Scenario: FtsUnavailable still propagates despite dense degrade logic

- GIVEN the bundle's FTS index cannot be built or opened
- WHEN `answer(...)` is called
- THEN `FtsUnavailable` propagates to the caller unchanged, regardless of
  dense-store or question-embed availability
