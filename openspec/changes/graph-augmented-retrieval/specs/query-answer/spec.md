# Delta for Query Answer

## ADDED Requirements

### Requirement: Graph Retrieval Runs As A Second-Stage, Seeded PRR List

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

## MODIFIED Requirements

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
