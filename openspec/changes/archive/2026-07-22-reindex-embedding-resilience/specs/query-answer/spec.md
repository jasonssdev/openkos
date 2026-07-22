# Delta for Query Answer

## MODIFIED Requirements

### Requirement: Dense Retrieval Degrades To FTS-Only

WHEN dense retrieval cannot proceed — an absent/empty `vectors.db`, a
`VecUnavailable`, a read-path `sqlite3.Error` raised by
`vector_store.query`, OR the GENERIC transient `OllamaError` raised while
embedding the question (`embedder.embed([question])`) — `answer` MUST catch
it, proceed using the FTS list alone as the fused input (equivalent to an
empty dense list), set `dense_degraded=True` on the returned `AnswerResult`,
and MUST NOT raise. `answer` MUST NOT degrade on `OllamaUnavailable` (server
unreachable) or `OllamaModelNotFound` (configured embedding model not
installed) raised from the question-embed step — these two subclasses are
environment-fatal, not per-question transient, and MUST propagate
unswallowed to the caller so `query` reaches its existing fatal exit-1
ladder. `FtsUnavailable` and any `OllamaError`-family exception raised by
`llm.chat` (the LLM completion path, not the question-embed step) also
remain unaffected and continue to propagate unchanged.
(Previously: only `VecUnavailable` and a read-path `sqlite3.Error` from
`vector_store.query` triggered this degrade; an `OllamaError` raised while
embedding the question propagated uncaught, aborting the whole `query`
call. A later revision qualified the degrade to the generic transient
`OllamaError` only, re-raising `OllamaUnavailable`/`OllamaModelNotFound`
before the degrade catch — mirroring the reindex-side fatal-vs-transient
split.)

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

#### Scenario: FtsUnavailable still propagates despite dense degrade logic

- GIVEN the bundle's FTS index cannot be built or opened
- WHEN `answer(...)` is called
- THEN `FtsUnavailable` propagates to the caller unchanged, regardless of
  dense-store or question-embed availability

### Requirement: Typed Exceptions Propagate Unswallowed

`answer` MUST NOT catch or suppress `FtsUnavailable` or any `OllamaError`
family member raised by `llm.chat`; these MUST propagate to the caller
unchanged. `OllamaUnavailable` and `OllamaModelNotFound` raised while
embedding the question (`embedder.embed([question])`) MUST ALSO propagate
unswallowed — they are environment-fatal, not per-question transient. The
GENERIC transient `OllamaError` raised while embedding the question is the
ONLY exception to this rule: it is caught and handled by the Dense
Retrieval Degrades To FTS-Only requirement instead, and MUST NOT propagate
from `answer`.
(Previously: every `OllamaError`-family exception, from any call site
including the question-embed step, propagated unchanged. A later revision
made the generic transient `OllamaError` from the question-embed step the
sole degrade case, while re-affirming that its `OllamaUnavailable`/
`OllamaModelNotFound` subclasses still propagate unswallowed.)

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
