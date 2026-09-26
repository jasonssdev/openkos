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

This spec DOES define a character budget and context truncation: `limit`
bounds how many documents are retrieved, but `_assemble_context` separately
plans a character budget against the backend's context window and clips
assembled bodies to fit it, and a `Citation` DOES carry metadata beyond
`concept_id`/`title`/`confidential` — whether the document was sent as a
partial excerpt. What this spec does not define is:

- **A CLI command** (`query-command`).
- **Reading/constructing `openkos.config`.** `retrieval/answer.py` MUST NOT
  import `openkos.config`; `LLMBackend` and friends are injected.
- **Weighted/normalized score fusion.**
- **Distance-to-similarity conversion.**
- **Graph/link ranking in any position** — as a third RRF input or as an
  additive reserved-slot channel (see "Answering Reads No Graph").
- **Filing the answer back as a concept** (`query-command`'s `--save`
  composes that; this module only produces the `AnswerResult`).

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

`answer()` MUST additionally accept a keyword-only `revision_history: bool
= False` parameter controlling whether it walks and attaches revision
history. This parameter MUST NOT be sourced from `openkos.config` inside
the module — the caller (`run_query`) resolves it from configuration and
passes it explicitly, keeping the module itself config-free.
(Previously: `LLMBackend`, `Embedder`, and `VectorStore` were caller-injected;
`fts_index` and `graph_index` did not exist as parameters — the module built
its own FTS index and graph internally. Previously: `graph_index` was a
fourth injected handle; issue #434 removed the stage that read it.)
(Previously: `answer()` had no `revision_history` parameter.)

#### Scenario: Module has no config dependency

- GIVEN a static import check of `retrieval/answer.py`
- WHEN its imports are inspected
- THEN `openkos.config` is absent, and the only sources of `LLMBackend`,
  `Embedder`, `VectorStore`, and `fts_index` are the parameters passed by
  the caller

#### Scenario: revision_history is caller-supplied, not config-read

- GIVEN a static import check of `retrieval/answer.py` and the signature of
  `answer()`
- WHEN both are inspected
- THEN `openkos.config` is absent, and `revision_history` is a
  keyword-only parameter with no internal config read
### Requirement: Citations Reflect Only Context-Included Concepts

Every `Citation(concept_id, title, confidential)` in `citations` MUST
correspond to a concept whose body was actually placed in the LLM context
for that call. Concepts skipped under guarded re-read, or never retrieved,
MUST NOT appear in `citations`. This is a necessary condition, not a
sufficient one: `citations` is a SUBSET of the context-included concepts,
narrowed by the attribution requirement below (issue #753). The
`confidential` flag (issue #569) MUST be `True` exactly when the concept's
freshly re-read frontmatter EXPLICITLY carries the top sensitivity rank —
transparency for the CLI's disclosure, mirroring the commit path's
explicit-value-only posture, never the fail-closed gate's — and MUST
default to `False`.

#### Scenario: Citations never exceed the context set

- GIVEN a mix of readable and unreadable hits returned by `search`
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN every `Citation` corresponds to a concept placed in context, with
  `title` read from that concept's OKF frontmatter, and no concept skipped
  under guarded re-read appears

### Requirement: A Sufficiency Check May Refuse Before Synthesis

WHEN `sufficiency_check` is enabled, `answer` MUST ask one model call, over
the SAME assembled context and question string synthesis would receive,
whether the context contains an answer — and MUST return
`no_match_cause == "insufficient_context"` with empty `citations` and
`llm_invoked` `False`, WITHOUT calling synthesis, when it does not (#760).

The check MUST be evidence-first: it asks for the sentence that answers and
treats a refusal sentinel as the negative, rather than asking for a verdict.
A verdict formulation was measured alongside it and false-refused a question
the bundle answers.

The refusal sentinel MUST be matched as the WHOLE reply, modulo surrounding
whitespace, punctuation and case — never as a substring. The check asks the
model to quote corpus text, so the sentinel can legitimately appear inside an
answer-bearing quotation.

`sufficiency_check` MUST default to `False` in `answer` itself, so every
library and eval caller keeps byte-identical behavior, and the product-ON
default MUST live in the workspace config alone.

A transient backend error in the check MUST fall through to synthesis: an
error is not evidence of insufficiency. The FATAL `OllamaError` subclasses
MUST still propagate, so an unreachable backend never becomes an answered
question. The check MUST NOT run when no context was assembled.

#### Scenario: An unanswerable context refuses without synthesising

- GIVEN context was assembled and the check reports no answering sentence
- WHEN `answer(..., sufficiency_check=True)` is called
- THEN exactly one chat call is made, `no_match_cause` is
  `"insufficient_context"`, `citations` is empty, and `llm_invoked` is `False`

#### Scenario: A quotation lets the answer through

- GIVEN the check quotes a context sentence
- WHEN `answer(..., sufficiency_check=True)` is called
- THEN synthesis runs and the answer is produced normally

#### Scenario: A quotation containing the sentinel is not a refusal

- GIVEN the check returns a quotation that contains the sentinel word
- WHEN `answer(..., sufficiency_check=True)` is called
- THEN synthesis still runs

`AnswerResult.sufficiency_degraded` MUST report that the check was REQUESTED
and could not run (#764). It MUST be `False` when the check was not
requested, and `False` when it ran and allowed the answer through: the flag
means "could not run", so a notice built on it fires only when the configured
guard is actually missing.

#### Scenario: A degraded check is reported

- GIVEN the check raises a non-fatal backend error
- WHEN `answer(..., sufficiency_check=True)` is called
- THEN the answer is produced and `sufficiency_degraded` is `True`

#### Scenario: A check that ran is not reported as degraded

- GIVEN the check returns a verdict
- WHEN `answer(..., sufficiency_check=True)` is called
- THEN `sufficiency_degraded` is `False`

#### Scenario: A transient check failure still answers

- GIVEN the check raises a non-fatal backend error
- WHEN `answer(..., sufficiency_check=True)` is called
- THEN synthesis runs and the answer is produced

### Requirement: Citations Are Decided By What The Answer Reports Using

`answer` MUST determine `citations` from the reply, not from retrieval
alone (issue #753). The context blocks presented to the model MUST be
numbered from 1, and the model MUST be instructed to close its reply with a
single line naming the block numbers its answer draws on. `answer` MUST
strip that line from the returned prose — `query --save` files the prose as
a permanent bundle concept, so a surviving marker would be written into the
bundle rather than merely shown.

`AnswerResult.attribution` MUST report how the list was decided:

- `"reported"` — a usable line was present; `citations` is exactly the
  blocks it named, in fused-rank order, and MAY be empty when the reply
  reports drawing on none of them.
- `"absent"` — no line was present; `citations` is every context-included
  concept, which is the pre-#753 behavior.
- `"unparsed"` — a line was present but named no in-range block; the same
  fallback applies.

`AnswerResult.context_block_count` MUST report how many context blocks were
actually sent to the model — the count AFTER the guarded re-read skip guard,
never the fused count, which is taken before it. Any message telling a user
how many concepts an answer could have drawn on MUST use this number: a
concept skipped between the fuse and the send was never shown to the model,
so naming it would overstate what the answer declined.

Out-of-range block numbers MUST be dropped rather than clamped, and
filtering MUST preserve fused-rank order rather than the order the model
listed. The fallback for `"absent"`/`"unparsed"` MUST NOT be an empty
citation list: emptying them would turn a non-compliant backend into a
silent loss of provenance rather than a visible one.

#### Scenario: A reported subset narrows the citation list

- GIVEN three concepts placed in context
- WHEN the reply names only the first and third blocks
- THEN `citations` holds exactly those two, in fused-rank order, and
  `attribution` is `"reported"`

#### Scenario: An answer reporting no support cites nothing

- GIVEN concepts placed in context
- WHEN the reply reports drawing on none of them
- THEN `citations` is empty and `attribution` is `"reported"`, so
  `query --save` refuses the filing for want of provenance

#### Scenario: The block count excludes a concept skipped at re-read

- GIVEN two concepts surviving the fuse, one of which is unreadable when
  `_assemble_context` re-reads it
- WHEN `answer(...)` is called
- THEN `fused_count` is `2` and `context_block_count` is `1`

#### Scenario: A reply with no marker keeps every citation

- GIVEN concepts placed in context
- WHEN the reply carries no attribution line at all
- THEN `citations` holds one `Citation` per context-included concept and
  `attribution` is `"absent"`

#### Scenario: Only an explicit confidential value sets the flag

- GIVEN one cited concept explicitly marked `sensitivity: confidential`
  and one with no `sensitivity` field at all
- WHEN `answer(...)` is called with those concepts admitted to context
- THEN the first citation's `confidential` is `True` and the second's is
  `False`

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

WHEN `revision_history` is enabled, `context_block_count` MUST count every
context block actually sent to the model, INCLUDING attached history
blocks; `fused_count`, `fts_hit_count`, and `dense_hit_count` MUST continue
to count only ordinary hits and MUST NOT include history blocks.
`AnswerResult` MUST additionally carry `history_truncated_titles: list[str]
= field(default_factory=list)`, holding the TITLE of every retrieved
successor whose OWN attached-history chain was cut short — whether by the
per-successor block cap, by the depth bound, or by both — listed in fused
order. A successor whose full reachable chain within the depth bound was
shown in full MUST NOT appear in this list. WHEN `revision_history` is
disabled (the default), `history_truncated_titles` MUST be `[]`, and
`context_block_count` MUST be unaffected by this requirement.
(Previously: those three fields existed — `graph_hit_count` the raw
personalized-PageRank candidate pool, `graph_degraded` whether the graph
stage could run, and `graph_contributed_count` how many reserved slots the
graph filled with concepts absent from the FTS+dense pool. All three
described a channel issue #434 removed; a field that could only ever report
zero would read as a channel that contributed nothing, rather than one that
is not there.)
(Previously: `context_block_count` counted only ordinary hit blocks, and
there was no `history_truncated_titles` field, because history blocks did
not exist.)

#### Scenario: Successful answer sets success metadata

- GIVEN a question with readable, matching hits
- WHEN `answer(...)` returns a non-`NO_MATCH` answer
- THEN `llm_invoked` is `True` and `no_match_cause` is `"none"`

#### Scenario: AnswerResult reports no graph metadata

- GIVEN the `AnswerResult` dataclass
- WHEN its fields are inspected
- THEN `graph_hit_count`, `graph_degraded`, and `graph_contributed_count`
  are all absent

#### Scenario: context_block_count includes attached history blocks

- GIVEN `revision_history` is enabled and a successor with 2 attached
  history blocks among otherwise ordinary hits
- WHEN `answer(...)` returns
- THEN `context_block_count` equals the hit-block count plus 2, while
  `fused_count`, `fts_hit_count`, and `dense_hit_count` are unaffected by
  the attached history

#### Scenario: The truncation signal names the capped successor's title

- GIVEN a successor S, titled "T", whose reachable history within depth 3
  exceeds 3 predecessors, with `revision_history` enabled
- WHEN `answer(...)` returns
- THEN `history_truncated_titles` contains "T"

#### Scenario: A chain cut by the depth bound also sets the truncation signal

- GIVEN a successor S, titled "T", whose `supersedes`/`revises` chain
  continues past depth 3 with no other truncation cause
- WHEN `answer(...)` returns with `revision_history` enabled
- THEN `history_truncated_titles` contains "T", even though fewer than 3
  history blocks were attached to S

#### Scenario: Disabled by default reports no truncation

- GIVEN `revision_history` is `False` (the default)
- WHEN `answer(...)` returns
- THEN `history_truncated_titles` is `[]`, and `context_block_count` is
  unaffected by this requirement

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

### Requirement: Chunk-Backed Dense Retrieval Reaches A Document's Tail

WHEN a document exceeds the embedder's chunking window, dense retrieval
MUST be able to retrieve that document for a question answerable only from
content past the window boundary (the document's tail), via
`vector_store.query()`'s document-level `VecHit`s.

#### Scenario: A tail-only question retrieves a long document with FTS disabled

- GIVEN a long document whose only content answering a given question sits
  past the point where earlier truncated embeddings would have stopped, and
  `fts_index` is `None`
- WHEN `answer(...)` is called with that question
- THEN the document appears in `citations` via the dense path alone

### Requirement: Chunk Collapse Is Invisible To Citation, Attribution, And Save Provenance

Because `VectorStoreDB.query()` collapses chunk hits to one `VecHit` per
`concept_id` before returning, `answer`'s citation identity,
`_split_attribution`'s positional block-to-citation mapping, and
`query --save`'s `concept_id`-list provenance MUST remain byte-identical in
shape to their pre-chunking contract: one context block and one `Citation`
per document, never per chunk.

#### Scenario: A multi-chunk document yields exactly one citation

- GIVEN a document whose best dense match came from a non-first chunk
- WHEN it is fused, placed in context, and cited
- THEN exactly one `Citation` for that document's `concept_id` appears,
  never one per chunk

#### Scenario: Save provenance is unaffected by chunking

- GIVEN an answer citing a chunked document
- WHEN `query --save` files provenance
- THEN the provenance list is `concept_id`s exactly as before chunking,
  with no chunk identity present

### Requirement: The Sensitivity Re-Check Still Runs Before Any Chunk's Content Reaches The LLM

`_assemble_context`'s per-document fail-closed sensitivity re-read MUST
still run, over the whole document body freshly re-read from disk, before
any of that document's content — chunked or not — reaches the LLM context.
The vector store MUST continue to return only `(concept_id, distance)`,
never document text.

#### Scenario: A confidential chunked document is still excluded

- GIVEN a document whose freshly re-read frontmatter marks it confidential,
  and it is a top dense hit assembled from chunk vectors
- WHEN `answer(...)` is called
- THEN it is excluded from context and citations exactly as it would be for
  a non-chunked document

### Requirement: The Assembled Context Is Bounded By The Backend's Context Window

`_assemble_context` MUST plan a character budget against the context window
the backend will actually enforce, and MUST clip the assembled bodies to fit
it before either the sufficiency check or synthesis is sent. A context that
already fits MUST be sent byte-identical.

Ollama does not raise on an oversized prompt: it discards the overflow and
returns a normal reply, measured at `prompt_eval_count: 6146` for a
184,000-char prompt against `num_ctx: 12288`. `OllamaGenerationCapped`
cannot catch this — it fires when GENERATION stops for length, while here
generation finishes normally — so an unbounded prompt is silently truncated
with no error anywhere.

The budget MUST be planned against the backend's advertised
`context_window` when it exposes a usable one, and against a packaged
default otherwise; a backend that advertises nothing, or whose property
raises, MUST still be bounded rather than sent an unplanned prompt.

The blocks share ONE window, so the budget MUST be distributed such that a
block needing less than an equal share releases the remainder to the blocks
that need more.

#### Scenario: A fitting context is unchanged

- GIVEN retrieved documents whose combined bodies fit the context window
- WHEN `answer(...)` assembles the prompt
- THEN the bodies are sent byte-identical, no elision marker appears, and
  no citation is marked partial

#### Scenario: An oversized document is excerpted rather than truncated

- GIVEN a retrieved `Source` document whose body exceeds the window
- WHEN `answer(...)` assembles the prompt
- THEN the model receives an even-coverage excerpt that fits the budget,
  retaining both the first and last windows, with each elision marked

#### Scenario: Unused room goes to the documents that need it

- GIVEN four small documents and one document far larger than the window
- WHEN the shared budget is distributed
- THEN the small documents are sent whole and the large one receives the
  room they did not use, rather than an equal fifth of the budget

#### Scenario: A backend advertising no window is still bounded

- GIVEN a backend exposing no `context_window`, or one whose property raises
- WHEN `answer(...)` assembles an oversized context
- THEN the context is still bounded, planned against the packaged default

### Requirement: A Partially Read Document Is Disclosed And Never Cited As Fully Read

When a document was sent as an excerpt, its `Citation` MUST record that, and
`AnswerResult` MUST carry the titles of every excerpted block in fused-rank
order. `query` MUST disclose the clipping on stderr, naming the documents,
and MUST mark those citations distinctly in the rendered citation list.
`query --save` MUST disclose it in the plan before the confirmation gate.

`--save` files citations as the filed insight's provenance, so a citation
that did not record the clipping became a false provenance claim on disk
that no surface — not `query`, not `lint`, not `status` — could reveal. The
`--save` unverified-grounding gate does not cover this: that gate reports
whether the attribution line was absent, which is a different failure, and a
partially read document can be filed under a perfectly `reported`
attribution.

The disclosure MUST describe what was SENT, not what was cited: the
attribution filter narrows `citations` to the blocks the model reported
using, so a disclosure derived from them would vanish exactly when the
answer cited nothing.

#### Scenario: The clipping notice names the documents

- GIVEN an answer assembled from a context that had to be clipped
- WHEN `query` renders its retrieval summary
- THEN stderr names each clipped document and points at `context_window`

#### Scenario: The notice is silent when nothing was clipped

- GIVEN an answer whose context fitted whole
- WHEN `query` renders its retrieval summary
- THEN no clipping notice appears

#### Scenario: A partially read citation is marked in the list

- GIVEN an answer citing one excerpted and one whole document
- WHEN `query` renders the citation list
- THEN only the excerpted document's line carries the partial marker

#### Scenario: The save plan discloses partial reads before the gate

- GIVEN `query --save` about to file an answer citing an excerpted document
- WHEN the plan is rendered
- THEN it names the partially read document before the confirmation gate

#### Scenario: The disclosure survives an answer that cited nothing

- GIVEN a clipped context and a model that reported using no block
- WHEN `answer(...)` returns
- THEN `citations` is empty and the excerpted titles are still reported

### Requirement: A Document The Model Was Shown None Of Is Dropped, Not Cited

When the shared budget leaves a retrieved document's block no room at all,
that block MUST be dropped from the prompt AND from `citations` entirely,
and its title MUST be reported separately from the partially read ones.
`query` MUST disclose the omission on stderr, including when the resulting
context is empty and the call returns a no-match.

A zero-share block would otherwise still contribute its
`[concept_id: … — Title]` label, and a label is a numbered block the model
can cite — provenance for a document it was shown nothing of, which is this
issue's defect in its purest form. Reporting it apart from a partial read
matters because the two mean different things to a reader: a partially read
document still contributed, an omitted one did not, and its absence may be
why the answer is thin.

Carrying the disclosure on the no-match return is part of the requirement,
not an extra: without it, a context that was entirely dropped reads to the
operator as "the bundle has nothing to say", which is the loudest possible
form of the silent overflow this capability exists to end. The
`no_match_cause` vocabulary MUST NOT be widened to express it — the fused
hits were readable, so the honest cause is unchanged, and other surfaces
already branch on those values.

#### Scenario: A zero-share document leaves the prompt and the citations

- GIVEN a retrieved document whose share of the budget is zero characters
- WHEN `answer(...)` assembles the context
- THEN neither its body nor its label reaches the prompt, it appears in no
  citation, and its title is reported as omitted rather than excerpted

#### Scenario: The omission is disclosed on an empty-context no-match

- GIVEN every retrieved block was dropped for want of budget
- WHEN `query` reports the no-match
- THEN stderr names the omitted documents and points at `context_window`
### Requirement: Revision History Is Attached To A Retrieved Concept When Requested

WHEN `revision_history` is enabled, `_assemble_context` MUST, for each
successor concept placed in context, walk that concept's outbound
`supersedes` and `revises` edges — read from its already-loaded frontmatter
via `okf.decode_relations` — breadth-first to attach its predecessors as
separate history blocks. The walk MUST NOT read the bundle-wide graph and
MUST NOT perform any full-bundle scan; it re-reads only the chain members it
visits.

The walk MUST be bounded: depth 3 hops from the successor, a `visited` set
guarding against cycles, and at most 3 history blocks attached per
successor. Truncation MUST be disclosed, via
`AnswerResult.history_truncated_titles` (see "AnswerResult Carries Retrieval
Metadata"), whenever either bound is what stops a reachable predecessor from
being shown: WHEN a successor's reachable-predecessor set within depth 3
exceeds 3, `answer` MUST select the first 3 in the deterministic order below
and disclose the truncation; WHEN a node reached exactly at depth 3 itself
has an outbound `supersedes`/`revises` edge whose target was not already
visited or skipped, `answer` MUST also disclose the truncation, even though
that depth-3 node's own block IS attached. Truncation MUST be determined
without any additional read beyond the edges already present in the
frontmatter of nodes already visited.

At each node, targets MUST be visited in a deterministic order: `supersedes`
edges before `revises` edges, and within the same relation, ascending by
concept id. This order MUST be identical across repeated calls against the
same bundle state.

WHEN `revision_history` is `False` (the default), `answer` MUST perform zero
additional reads beyond the existing per-hit re-read, and the assembled
context — and therefore the prompt sent to the LLM — MUST be byte-identical
to the pre-history-feature behavior.

#### Scenario: A supersedes predecessor is attached as a history block

- GIVEN `revision_history` is enabled and a retrieved successor concept S
  holds an outbound `supersedes` edge to predecessor P
- WHEN `answer(...)` assembles context for S
- THEN P is re-read and attached as its own separately numbered history
  block

#### Scenario: A 4-long chain stops at depth 3 and a cycle terminates

- GIVEN a chain of 4 successive `supersedes` edges from the successor, and
  separately a bundle where two concepts `supersedes` each other
- WHEN `answer(...)` walks each with `revision_history` enabled
- THEN the 4-long chain attaches history only through depth 3 and discloses
  the successor's truncation via `history_truncated_titles`, while the
  mutual-cycle walk terminates via the `visited` guard rather than looping

#### Scenario: More than 3 predecessors truncates deterministically

- GIVEN a successor with more than 3 reachable predecessors within depth 3
- WHEN `answer(...)` attaches history blocks
- THEN exactly 3 are attached, chosen by the `supersedes`-before-`revises`,
  then-concept-id order, and the truncation is disclosed via
  `history_truncated_titles`

#### Scenario: Disabled by default makes zero extra reads

- GIVEN `revision_history` is not passed (defaults to `False`)
- WHEN `answer(...)` is called against a bundle containing `supersedes` and
  `revises` chains
- THEN no predecessor is re-read, no history block is attached, and the
  assembled context is byte-identical to the pre-history-feature behavior

### Requirement: Revision History Deduplication And Send-Time Guards

A `revises` predecessor that already appears as an ordinary hit in the same
`answer()` call MUST NOT be attached again as a history block. A
predecessor reachable from more than one successor in the fused list MUST
be attached exactly once, to the first successor that reaches it in fused
order.

Every existing send-time guard that a hit must pass MUST also gate a
predecessor re-read before it is attached: the `blocked` concept-id set, the
`sensitivity.should_block` re-check, and the skip-on-unreadable rule. A
predecessor that fails any of these guards MUST be omitted from the history
blocks without raising, exactly as a hit failing the same guard is omitted
from context. A refused predecessor MUST be a dead end: the walk MUST NOT
read or enqueue that node's own outbound `supersedes`/`revises` edges, so no
later history label can ever name a node that was never admitted.

#### Scenario: A revises predecessor already a hit is not repeated

- GIVEN predecessor P is both an ordinary fused hit and reachable from
  successor S via a `revises` edge, with `revision_history` enabled
- WHEN `answer(...)` assembles context
- THEN P appears exactly once, as its ordinary hit block, and is not also
  attached as a history block

#### Scenario: A predecessor shared by two successors is attached once

- GIVEN predecessor P is reachable from both successor S1 and successor S2,
  which both appear in the fused list with S1 ranked before S2
- WHEN `answer(...)` attaches history with `revision_history` enabled
- THEN P is attached exactly once, as history under S1

#### Scenario: A confidential predecessor is excluded like a hit

- GIVEN a predecessor whose freshly re-read frontmatter marks it
  confidential, and the sending guard would block a hit with that mark
- WHEN `answer(...)` attaches history with `revision_history` enabled
- THEN that predecessor is omitted from history blocks and from context,
  exactly as a confidential hit would be

#### Scenario: An unreadable predecessor is skipped without raising

- GIVEN a predecessor file referenced by a `supersedes`/`revises` edge is
  missing or has unparsable frontmatter
- WHEN `answer(...)` attaches history with `revision_history` enabled
- THEN that predecessor is silently omitted and no exception propagates

#### Scenario: A refused predecessor is a dead end, never traversed

- GIVEN a predecessor P fails a send-time guard (blocked, confidential, or
  unreadable), and P itself holds an outbound `supersedes` edge to another
  predecessor Q
- WHEN `answer(...)` walks with `revision_history` enabled
- THEN Q is never read and never attached — the walk does not follow P's
  edges because P was never admitted

### Requirement: Revision History Block Labels Name The Relation, Edge Holder, And Event Date

Each attached predecessor MUST be rendered as its own separately numbered
context block — never spliced into the successor's body — with a label of
the exact shape: `(earlier version, superseded|refined by concept_id: <H>;
<date phrase>; no longer current|still current)`.

`<H>` MUST be the concept id of the EDGE HOLDER that reached this
predecessor — the concept whose own outbound `supersedes` or `revises` edge
the walk followed to attach it — and NOT necessarily the top-level retrieved
successor. For a predecessor at depth 1, the holder IS the retrieved
successor. For a predecessor reached at depth 2 or deeper, the holder is the
intermediate predecessor whose edge reached it, not the successor at the top
of the chain.

The relation MUST render as `superseded` for a `supersedes` edge and
`refined` for a `revises` edge.

The currency clause MUST render as `no longer current` for a `supersedes`
edge — a `supersedes` target is always deprecated. For a `revises` edge, the
currency clause MUST render as `still current` UNLESS that predecessor is
ITSELF, independently of this edge, in the bundle's deprecated set (i.e. it
is superseded elsewhere), in which case it MUST render as `no longer
current`. Currency is decided by the predecessor's own deprecated-set
membership, never by the traversing edge's role alone.

`<date phrase>` MUST be one of: `event date <D>` when the predecessor's
event-date resolution is unambiguous; `event dates <D1> to <D2>` (earliest
to latest, ISO) when multiple distinct dates were found across the chain
member's provenance; or `event date unknown` when resolution is missing or
unreached. The label MUST NEVER substitute the concept's ingest timestamp
for an unresolved event date. An event date is resolved by reading only the
predecessor's own already-admitted `provenance:` entries and, at most one
hop further, the `provenance:` of any non-Source entry among them; it MUST
NOT depend on any read outside that bound.

The successor's own label MUST remain byte-identical to its non-history
label; the history relationship is carried only on the predecessor's block.
Adding history blocks MUST NOT change the system prompt text.

#### Scenario: A depth-1 superseded predecessor's label names the retrieved successor as holder

- GIVEN a predecessor P superseded by retrieved successor S, with a single
  resolved event date
- WHEN `answer(...)` renders P's history block
- THEN its label reads `(earlier version, superseded by concept_id: S;
  event date <D>; no longer current)`, naming S because P sits at depth 1

#### Scenario: A depth-2 predecessor's label names its immediate holder, not the top-level successor

- GIVEN retrieved successor S supersedes P, and P separately revises Q, with
  `revision_history` enabled
- WHEN `answer(...)` renders Q's history block
- THEN Q's label names P's concept id as the holder (`refined by
  concept_id: P`), not S's

#### Scenario: A refined predecessor not deprecated elsewhere states it is still current

- GIVEN a predecessor P revised by successor S, and P is not itself in the
  bundle's deprecated set
- WHEN `answer(...)` renders P's history block
- THEN its label names the `revises` relation, S's concept id as holder, and
  states P is still current

#### Scenario: A refined predecessor that is also deprecated elsewhere states it is no longer current

- GIVEN a predecessor P revised by successor S, and P is separately
  superseded by some other concept in the bundle, placing P in the
  deprecated set
- WHEN `answer(...)` renders P's history block
- THEN its label names the `revises` relation but states P is no longer
  current, even though the edge that reached it was `revises`

#### Scenario: An unresolved date renders as unknown, never ingest time

- GIVEN a predecessor whose event-date resolution is missing or unreached
- WHEN `answer(...)` renders its history block label
- THEN the label reads `event date unknown`, and never substitutes that
  predecessor's ingest timestamp

#### Scenario: A confidential Source's date resolves as unknown, without excluding the predecessor's own block

- GIVEN a predecessor P whose `provenance:` names only a Source that the
  send-time sensitivity gate would refuse to admit (for example
  confidential, with `include_confidential` off), while P's own frontmatter
  and body pass their own guards
- WHEN `answer(...)` resolves P's event date
- THEN the date resolution reads as missing and P's label shows
  `event date unknown` — the Source's refusal affects only the date lookup,
  and P's own history block is still attached

#### Scenario: Multiple distinct dates render as an earliest-to-latest range

- GIVEN a predecessor whose provenance resolves to more than one distinct
  event date
- WHEN `answer(...)` renders its history block label
- THEN the label reads `event dates <earliest> to <latest>`

#### Scenario: The successor's own label is unaffected

- GIVEN a successor with one or more attached history blocks
- WHEN `answer(...)` renders the successor's own context block label
- THEN it is byte-identical to the label it would carry with
  `revision_history` disabled

### Requirement: Revision History Budget Is A Nested Split Of The Successor's Share Plus Unspent Budget

WHEN one or more history blocks are attached to a successor, the character
budget MUST be allocated in two stages:

1. **Outer stage (unchanged).** The share computation MUST first compute
   each hit block's own share exactly as it does without history, over the
   same hit sizes and the same total budget as today (`fair_shares` over the
   hit sizes). An unrelated hit's share, and therefore its bounded body,
   MUST be byte-identical to what it would be without the history feature —
   the cost of history is never charged to a hit that does not carry it.
2. **Slack stage.** Whatever budget the outer stage leaves unspent (the
   total budget minus the sum of outer shares) MUST be made available, via a
   further fair split across every successor carrying history, IN ADDITION
   to that successor's own outer share. A successor's nested pool for
   `[successor, *its history blocks]` is its own outer share PLUS its fair
   slice of this unspent budget, minus the frame overhead its history adds
   to the prompt.

Within a successor's nested pool, the split across `[successor, *its
history blocks]` MUST use a fair split (`prompt_budget.nested_shares`). This
split MUST NEVER reduce the successor's own body to zero, to make room for
its history, when the successor's outer share was non-zero. WHEN the nested
pool cannot give the successor a non-zero share under this constraint, ALL
of that successor's history for this pool MUST be dropped instead, and the
successor's share reverts to its unchanged outer share. A dropped group's
history blocks MUST NOT appear in the prompt or in citations.

The existing excerpt-and-omission disclosure (issue #882) MUST apply
unchanged to a history block within a group that is not dropped as a whole:
a history block that must be excerpted to fit its sub-share MUST be marked
partial exactly like a hit, and a history block left zero room within an
otherwise kept group MUST be dropped from the prompt and disclosed as
omitted, exactly like a hit. Every disclosed history title — excerpted,
omitted individually, or omitted because its whole group was dropped — MUST
carry the suffix ` (earlier version)`.

Across every hit and history body combined, the total spent MUST NEVER push
`len(user_content) + max(system prompt lengths)` above the bound that today's
plan already allows, computed over the same hit labels and the same budget.

#### Scenario: Adding history to one successor leaves other hits' bodies unchanged

- GIVEN four hits, one of which is a successor with attached history blocks
- WHEN the shared budget is distributed
- THEN the three unrelated hits' bounded bodies are byte-identical to what
  they would be without history, and the successor's own nested pool is its
  outer share plus a slice of unspent budget

#### Scenario: Unspent budget lets a small history block fit without excerpting

- GIVEN a window where every hit's outer share already comfortably fits its
  body, leaving unspent budget, and one successor carries one small history
  block
- WHEN `answer(...)` assembles the prompt
- THEN that history block is sent in full, unexcerpted, funded from the
  unspent budget rather than carved out of the successor's own outer share

#### Scenario: A fully-spent window still splits within the successor's own share

- GIVEN a window where every hit's outer share is fully spent, leaving no
  unspent budget
- WHEN a successor carries history
- THEN its nested pool equals exactly its own outer share minus the history
  frame overhead, split across `[successor, *history]`

#### Scenario: An oversized history block is excerpted, not dropped

- GIVEN a history block whose body exceeds its computed sub-share, within a
  group that is not dropped as a whole
- WHEN `answer(...)` assembles the prompt
- THEN it receives an even-coverage excerpt of its sub-share, its citation
  is marked partial, and its title carries the ` (earlier version)` suffix

#### Scenario: A zero-share history block within a kept group is dropped and disclosed as omitted

- GIVEN a history block whose sub-share of the successor's nested split
  leaves it zero characters, while the successor itself keeps a non-zero
  share
- WHEN `answer(...)` assembles the prompt
- THEN it is dropped from the prompt and from citations, and its title,
  suffixed ` (earlier version)`, is reported as omitted exactly as a
  zero-share hit would be

#### Scenario: A history group that would cost its successor its whole body is dropped entirely instead

- GIVEN a successor whose nested pool, if split across itself and its
  history, would leave the successor's own share at zero even though its
  outer share was non-zero
- WHEN `answer(...)` assembles the prompt
- THEN none of that successor's history blocks are sent, the successor's
  share reverts to its unchanged outer share, and every dropped history
  title is disclosed as omitted, suffixed ` (earlier version)`

### Requirement: Citation.history Marks An Attributed Revision History Block

`Citation` MUST gain a field `history: Literal["superseded", "refined"] |
None = None`, mirroring the shape of `excerpted` and `confidential`. It
MUST be `"superseded"` for a citation produced from a `supersedes` history
block, `"refined"` for one produced from a `revises` history block, and
`None` for every ordinary hit citation.

`_split_attribution` and the issue #753 subset rule MUST apply to history
citations unchanged: a history block is cited only when the model's
attribution line names its block number, `citations` remains a subset of
the sent blocks narrowed by what the reply reports using, and the
`"absent"`/`"unparsed"` fallback (every context-included block, hit or
history) applies identically to history blocks.

#### Scenario: A reported attribution naming a history block sets history

- GIVEN a superseded history block sent as block N, and the reply's
  attribution line names block N
- WHEN `answer(...)` returns
- THEN `citations` includes a `Citation` for that predecessor with
  `history="superseded"`

#### Scenario: An ordinary hit citation always carries history=None

- GIVEN an ordinary hit with no revision history attached
- WHEN `answer(...)` returns
- THEN its `Citation.history` is `None`

#### Scenario: Absent attribution keeps every included block, hit or history

- GIVEN a reply with no attribution line, and context including both hit
  and history blocks
- WHEN `answer(...)` returns
- THEN `citations` holds one `Citation` per context-included block —
  ordinary hits with `history=None` and history blocks with their relation
  — and `attribution` is `"absent"`

