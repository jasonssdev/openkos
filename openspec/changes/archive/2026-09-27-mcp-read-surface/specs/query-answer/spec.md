# Delta for Query Answer

## ADDED Requirements

### Requirement: An Optional Progress Callback Reports Phases

`answer()` MUST accept an optional, keyword-only `progress` parameter,
defaulting to `None`. WHEN `progress` is `None` (the default), `answer()`'s
behavior, return value, and every side effect (including its calls to
`fts_index`, `vector_store`, `embedder`, and `llm`) MUST be byte-identical
to its contract before this parameter existed. This parameter MUST NOT be
sourced from `openkos.config`; the module remains config-free.

WHEN a `progress` callable is supplied, `answer()` MUST invoke it with
exactly one of four phase names, in this order as each is reached —
`"retrieving"`, `"assembling"`, `"checking"`, `"synthesizing"` — together
with a `completed` count and a `total`. `"checking"` MUST be emitted only
WHEN the sufficiency check is enabled for that call; `total` MUST be `4`
when the sufficiency check is enabled and `3` otherwise, so `"checking"`'s
presence and `total`'s value always agree. `completed` MUST start at `0`
and strictly increase within a single call. The empty-question
short-circuit MUST emit no progress call at all; a call that short-circuits
after context assembly (for example, the zero-hit no-match path) MUST
simply stop emitting further phases rather than emit a final one out of
order.

#### Scenario: Omitting progress is byte-identical to today's contract

- GIVEN a caller invokes `answer(...)` without a `progress` argument
- WHEN it runs
- THEN its return value and its calls to every injected dependency are
  unchanged from `answer()`'s contract before this parameter existed

#### Scenario: A supplied callback observes the four phases in order

- GIVEN a caller supplies a `progress` callback and the sufficiency check is
  enabled
- WHEN `answer(...)` runs to completion on a successful answer
- THEN the callback is invoked with `"retrieving"`, `"assembling"`,
  `"checking"`, and `"synthesizing"`, in that order, `completed` strictly
  increasing, and `total` equal to `4` on every call

#### Scenario: Without the sufficiency check, checking is absent and total is 3

- GIVEN a caller supplies a `progress` callback and the sufficiency check is
  disabled
- WHEN `answer(...)` runs to completion on a successful answer
- THEN the callback is never invoked with `"checking"`, and every call
  reports `total` equal to `3`

#### Scenario: The empty-question short-circuit emits no progress

- GIVEN a caller supplies a `progress` callback and an empty or
  whitespace-only question
- WHEN `answer(...)` is called
- THEN the callback is never invoked

#### Scenario: progress is not read from configuration

- GIVEN a static import check of `retrieval/answer.py`
- WHEN its imports and the `progress` parameter are inspected
- THEN `openkos.config` is absent, and `progress` is supplied only by the
  caller

### Requirement: AnswerResult Carries Id Lists Aligned With Its Title Lists

`AnswerResult` MUST additionally, and purely additively, carry
`excerpted_ids`, `omitted_ids`, and `history_truncated_ids`
(`list[str]`, `field(default_factory=list)`), each index-aligned one-to-one
with its corresponding existing title list (`excerpted_titles`,
`omitted_titles`, `history_truncated_titles`): the id at position `n` in an
id list MUST name the same concept as the title at position `n` in its
paired title list. This lets a consumer that must scrub a title by
disclosure policy identify precisely which concept a given title names,
without inferring it from the title text, which is not guaranteed unique.

#### Scenario: Each id list stays index-aligned with its title list

- GIVEN an answer whose context assembly excerpts one document, omits
  another for want of budget, and truncates one successor's attached
  history
- WHEN `answer(...)` returns
- THEN `excerpted_ids[n]`, `omitted_ids[n]`, and `history_truncated_ids[n]`
  each name the same concept as the title at the same index in
  `excerpted_titles`, `omitted_titles`, and `history_truncated_titles`
  respectively

#### Scenario: An empty title list pairs with an empty id list

- GIVEN an answer whose context assembly excerpts, omits, and truncates
  nothing
- WHEN `answer(...)` returns
- THEN `excerpted_ids`, `omitted_ids`, and `history_truncated_ids` are all
  empty, exactly like their paired title lists

### Requirement: AnswerResult Names Every Object That Entered The Prompt, Independent Of Citation

`AnswerResult` MUST additionally, and purely additively, carry
`context_ids` (`list[str]`, `field(default_factory=list)`): the concept id
of every object whose content was actually placed in a context block sent
to the model, index-aligned one-to-one with `context_block_count`, and
captured BEFORE any later step narrows `citations` down to a
model-reported subset. `context_ids` MUST name an object regardless of
whether the model's reply goes on to cite it, since a consumer that must
decide whether an answer is safe to disclose needs to know everything the
wording MAY have drawn on, not only what the model chose to acknowledge.

#### Scenario: context_ids names an object the model does not cite

- GIVEN an answer whose context assembly places two documents in the
  prompt, and whose model reply reports drawing on only one of them
- WHEN `answer(...)` returns
- THEN `citations` names only the one reported document, but `context_ids`
  names both documents placed in the prompt

#### Scenario: context_ids stays index-aligned with context_block_count

- GIVEN any successful answer
- WHEN `answer(...)` returns
- THEN `len(context_ids) == context_block_count`
