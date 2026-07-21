# Delta for Query Answer

## MODIFIED Requirements

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

### Requirement: AnswerResult Carries Retrieval Metadata

`AnswerResult` MUST additionally carry: `fts_hit_count` (int, raw
`FtsIndex.search` hit count before guarded re-read filtering),
`llm_invoked` (bool), `no_match_cause` (`NoMatchCause = Literal["none",
"empty_query", "zero_hits", "all_unreadable"]`, `"none"` on a successful
answer, else whichever guard tripped), and `skip_notices` (`list[str]`,
copied from `FtsIndex.skipped` for that build) — UNCHANGED from the
existing contract. `AnswerResult` MUST additionally, and PURELY
ADDITIVELY, carry: `dense_hit_count` (int, raw `vector_store.query` hit
count), `fused_count` (int, number of distinct `concept_id`s in the
fused, limit-truncated list), and `dense_degraded` (bool, `True` when
dense retrieval could not proceed this call and FTS-only fusion was used,
`False` otherwise). No existing field is removed or retyped. The module
MUST remain config-free; `Embedder` and `VectorStore` MUST be
caller-supplied.
(Previously: only `fts_hit_count`, `llm_invoked`, `no_match_cause`, and
`skip_notices` existed; no dense/fused counts or degrade signal.)

#### Scenario: Successful answer sets success metadata

- GIVEN a question with readable, matching hits
- WHEN `answer(...)` returns a non-`NO_MATCH` answer
- THEN `llm_invoked` is `True` and `no_match_cause` is `"none"`

#### Scenario: Dense and fused counts reflect retrieval

- GIVEN 3 dense hits and a fused list of 4 distinct `concept_id`s
- WHEN `answer(...)` is called
- THEN `dense_hit_count` equals `3` and `fused_count` equals `4`

#### Scenario: dense_degraded reflects whether dense retrieval ran

- GIVEN dense retrieval completed normally for this call
- WHEN `answer(...)` is called
- THEN `dense_degraded` is `False`
- GIVEN dense retrieval instead fell back to FTS-only for this call
- WHEN `answer(...)` is called
- THEN `dense_degraded` is `True`

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

### Requirement: Empty Query Sets A Distinct No-Match Cause

WHEN `question.strip()` is empty, `answer` MUST short-circuit BEFORE any
retrieval — it MUST NOT call `FtsIndex.search`, `embedder.embed`, or
`vector_store.query` — and MUST NOT invoke the LLM, returning a no-match
`AnswerResult` with `no_match_cause` equal to `"empty_query"`,
distinguishable from `"zero_hits"`.
(Previously: short-circuited before FTS only; dense retrieval did not
exist.)

#### Scenario: Whitespace-only question

- GIVEN `question` is empty or contains only whitespace
- WHEN `answer(...)` is called
- THEN `embedder.embed` and `vector_store.query` are never called,
  `llm.chat` is never invoked, and `no_match_cause` is `"empty_query"`

## ADDED Requirements

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
