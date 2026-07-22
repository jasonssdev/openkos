# Delta for Query Answer

## MODIFIED Requirements

### Requirement: Dense Retrieval Degrades To FTS-Only

WHEN dense retrieval cannot proceed — an absent/empty `vectors.db`, a
`VecUnavailable`, a read-path `sqlite3.Error` raised by
`vector_store.query`, OR an `OllamaError`-family exception raised while
embedding the question (`embedder.embed([question])`) — `answer` MUST catch
it, proceed using the FTS list alone as the fused input (equivalent to an
empty dense list), set `dense_degraded=True` on the returned `AnswerResult`,
and MUST NOT raise. `FtsUnavailable` and any `OllamaError`-family exception
raised by `llm.chat` (the LLM completion path, not the question-embed step)
remain unaffected and continue to propagate unchanged.
(Previously: only `VecUnavailable` and a read-path `sqlite3.Error` from
`vector_store.query` triggered this degrade; an `OllamaError` raised while
embedding the question propagated uncaught, aborting the whole `query` call.)

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

#### Scenario: Question-embed OllamaError degrades to FTS-only, not exit 1

- GIVEN `embedder.embed([question])` raises an `OllamaError` (e.g. the
  flaky embedding path)
- WHEN `answer(...)` is called
- THEN retrieval proceeds using FTS hits alone, `dense_degraded` is `True`,
  no exception propagates from `answer`, and the caller (`query`) still
  exits 0 with its standard stderr retrieval summary

#### Scenario: FtsUnavailable still propagates despite dense degrade logic

- GIVEN the bundle's FTS index cannot be built or opened
- WHEN `answer(...)` is called
- THEN `FtsUnavailable` propagates to the caller unchanged, regardless of
  dense-store or question-embed availability

### Requirement: Typed Exceptions Propagate Unswallowed

`answer` MUST NOT catch or suppress `FtsUnavailable` or any `OllamaError`
family member raised by `llm.chat`; these MUST propagate to the caller
unchanged. An `OllamaError`-family exception raised while embedding the
question (`embedder.embed([question])`) is the one exception to this rule —
it is caught and handled by the Dense Retrieval Degrades To FTS-Only
requirement instead, and MUST NOT propagate from `answer`.
(Previously: every `OllamaError`-family exception, from any call site
including the question-embed step, propagated unchanged.)

#### Scenario: FTS index unavailable

- GIVEN the bundle's FTS index cannot be built or opened
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN `FtsUnavailable` propagates to the caller

#### Scenario: LLM backend fails

- GIVEN `llm.chat` raises an `OllamaError`-family exception
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN that same exception propagates to the caller unchanged

#### Scenario: Question-embed failure does not propagate

- GIVEN `embedder.embed([question])` raises an `OllamaError`-family
  exception
- WHEN `answer(...)` is called
- THEN that exception does NOT propagate from `answer`; it is handled per
  the Dense Retrieval Degrades To FTS-Only requirement instead
