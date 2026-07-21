# Query Answer Specification

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
conversion; graph/link ranking as a third input; filing the answer back as a
concept; citation metadata beyond `concept_id` and `title`.

## Requirements

### Requirement: Lexical Retrieval Drives Answer Assembly

`answer(question, *, bundle_dir, llm, embedder, vector_store, limit)` MUST
retrieve FTS hits via `FtsIndex.search(question, limit=pool_limit)` AND dense
hits via `vector_store.query(embedder.embed([question])[0], k=pool_limit)`
(`pool_limit = max(limit, 10)`), fuse both lists via
`retrieval.fusion.fuse(...)` into one ordered `concept_id` list, place each
fused hit's concept body — in fused order, truncated to `limit` — into the
LLM context, call `llm.chat(...)` exactly once, and return an `AnswerResult`
whose `answer` is the LLM's returned text.
(Previously: retrieval was FTS-only; dense retrieval and fusion did not
exist.)

#### Scenario: Matching concepts produce a cited answer

- GIVEN a bundle containing concepts that match the question lexically
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm, embedder=embedder,
  vector_store=vector_store)` is called
- THEN both `FtsIndex.search` and `vector_store.query` are called, the
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
family member (`OllamaUnavailable`, `OllamaModelNotFound`, `OllamaError`);
these MUST propagate to the caller unchanged.

#### Scenario: FTS index unavailable

- GIVEN the bundle's FTS index cannot be built or opened
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN `FtsUnavailable` propagates to the caller

#### Scenario: LLM backend fails

- GIVEN `llm.chat` raises an `OllamaError`-family exception
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN that same exception propagates to the caller unchanged

### Requirement: Module Is Config-Free And Backend-Injected

`retrieval/answer.py` MUST NOT import `openkos.config`. `LLMBackend`,
`Embedder`, and `VectorStore` instances MUST all be supplied by the
caller; the module MUST NOT construct or select any of them itself.
(Previously: only `LLMBackend` was caller-injected; `Embedder` and
`VectorStore` did not exist as parameters.)

#### Scenario: Module has no config dependency

- GIVEN a static import check of `retrieval/answer.py`
- WHEN its imports are inspected
- THEN `openkos.config` is absent, and the only sources of `LLMBackend`,
  `Embedder`, and `VectorStore` are the `llm`, `embedder`, and
  `vector_store` parameters passed by the caller

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
`concept_id`s in the FINAL fused, limit-truncated list), `dense_degraded`
(bool), `graph_hit_count` (int, raw personalized-PageRank pool size before
final fusion, default `0`), and `graph_degraded` (bool, `True` when graph
retrieval could not proceed this call, default `False`). No existing field is
removed or retyped. The module MUST remain config-free.
(Previously: no `graph_hit_count`/`graph_degraded` fields; `fused_count`
reflected only a two-list fusion.)

#### Scenario: Successful answer sets success metadata

- GIVEN a question with readable, matching hits
- WHEN `answer(...)` returns a non-`NO_MATCH` answer
- THEN `llm_invoked` is `True` and `no_match_cause` is `"none"`

#### Scenario: Graph counts reflect retrieval

- GIVEN a graph pool of 6 `concept_id`s contributed to the final fusion
- WHEN `answer(...)` is called
- THEN `graph_hit_count` equals `6`

#### Scenario: graph_degraded reflects whether graph retrieval ran

- GIVEN graph retrieval completed normally for this call
- WHEN `answer(...)` is called
- THEN `graph_degraded` is `False`
- GIVEN graph retrieval instead fell back to an empty list for this call
- WHEN `answer(...)` is called
- THEN `graph_degraded` is `True`

### Requirement: Empty Query Sets A Distinct No-Match Cause

WHEN `question.strip()` is empty, `answer` MUST short-circuit BEFORE any
retrieval — it MUST NOT call `FtsIndex.search`, `embedder.embed`,
`vector_store.query`, `build_graph`, or personalized PageRank — and MUST NOT
invoke the LLM, returning a no-match `AnswerResult` with `no_match_cause`
equal to `"empty_query"`, distinguishable from `"zero_hits"`.
(Previously: short-circuited before FTS, dense, and no graph step existed.)

#### Scenario: Whitespace-only question

- GIVEN `question` is empty or contains only whitespace
- WHEN `answer(...)` is called
- THEN `embedder.embed`, `vector_store.query`, and `build_graph` are never
  called, `llm.chat` is never invoked, and `no_match_cause` is
  `"empty_query"`

### Requirement: Graph Retrieval Runs As A Second-Stage, Seeded PPR List

`answer` MUST, after the initial `fuse(hits, vec_hits)`, derive SEEDS as the
top `min(limit, 5)` `concept_id`s of that initial fused list, build an
in-process graph via `build_graph(bundle_dir)` (config-free, rebuilt per
call), and run personalized PageRank (`nx.pagerank(to_digraph(store),
personalization=seeds, alpha=0.85)` over an undirected view) to produce a
`graph_hits` pool of size `max(limit, 10)`. The system MUST then compute a
FINAL fusion `fusion.fuse(hits, vec_hits, graph_hits)[:limit]` and feed that
truncated list, unchanged, into `_assemble_context`. Seeds MUST come from the
initial fused ranking, never from a raw union of FTS and dense hits.

#### Scenario: Graph contributes a concept absent from FTS and dense hits

- GIVEN a concept is reachable via graph proximity to the top fused hits but
  matches the question neither lexically nor semantically
- WHEN `answer(...)` is called
- THEN that concept can appear in the final fused, limit-truncated list via
  its `graph_hits` rank

#### Scenario: Seeds come from the initial fuse, not a raw union

- GIVEN an initial `fuse(hits, vec_hits)` whose top-ranked `concept_id`s
  differ from the raw union of FTS-only and dense-only top hits
- WHEN `answer(...)` runs personalized PageRank
- THEN the seed set passed as `personalization` equals the top
  `min(limit, 5)` `concept_id`s of the INITIAL fused list, not the raw union

### Requirement: Graph Retrieval Degrades To An Empty List On Failure

WHEN graph construction (`build_graph`) or PageRank computation raises any
exception, `answer` MUST catch it, proceed with `graph_hits = []` for the
final fusion (equivalent to two-list fusion), set `graph_degraded=True` on
the returned `AnswerResult`, and MUST NOT raise. FTS and dense retrieval
MUST remain unaffected — this mirrors the existing dense-degrade contract.
Graph retrieval has no cold-start precondition (unlike dense, which needs a
prior `reindex`): a bundle readable by `build_graph` is always eligible, but
one with zero edges yields an empty or degraded graph list gracefully rather
than raising.

#### Scenario: Graph build failure degrades cleanly

- GIVEN `build_graph(bundle_dir)` raises an exception
- WHEN `answer(...)` is called
- THEN `graph_degraded` is `True`, `graph_hit_count` is `0`, no exception
  propagates, and the answer is produced from FTS + dense fusion alone

#### Scenario: Edgeless bundle yields an empty graph list, not a failure

- GIVEN the bundle's graph projection has nodes but zero edges
- WHEN `answer(...)` is called
- THEN `graph_hits` is empty, `graph_degraded` is `False` (the build itself
  succeeded), and FTS/dense retrieval still produce a final answer

### Requirement: Personalized PageRank Is Deterministic

WHEN given a fixed bundle, a fixed question, and fixed seeds, personalized
PageRank MUST produce the same ranking across repeated calls.

#### Scenario: Same bundle and question produce the same graph ranking

- GIVEN a fixed compiled bundle and a fixed question
- WHEN `answer(...)` is called twice
- THEN both calls produce identical `graph_hits` ordering and an identical
  final fused, limit-truncated `concept_id` list

### Requirement: Dense Retrieval Degrades To FTS-Only

WHEN dense retrieval cannot proceed — an absent/empty `vectors.db`, a
`VecUnavailable`, or a read-path `sqlite3.Error` raised by
`vector_store.query` — `answer` MUST catch it, proceed using the FTS list
alone as the fused input (equivalent to an empty dense list), set
`dense_degraded=True` on the returned `AnswerResult`, and MUST NOT raise.
`FtsUnavailable` and `OllamaError`-family exceptions from the LLM path
remain unaffected and continue to propagate unchanged.

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

#### Scenario: FtsUnavailable still propagates despite dense degrade logic

- GIVEN the bundle's FTS index cannot be built or opened
- WHEN `answer(...)` is called
- THEN `FtsUnavailable` propagates to the caller unchanged, regardless of
  dense-store availability
