# Query Answer Specification

## Purpose

`retrieval/answer.py` is a pure library seam answering a natural-language
question from a compiled bundle: it retrieves lexical hits via `FtsIndex`,
assembles matched concept bodies into an LLM context, calls an injected
`LLMBackend`, and returns a cited `AnswerResult`. No CLI, no config wiring;
its only future consumer is the `query` command.

## Non-Goals

CLI command; reading/constructing `openkos.config`; context truncation or
token budget beyond `limit`; vector/semantic retrieval; filing the answer
back as a concept; citation metadata beyond `concept_id` and `title`.

## Requirements

### Requirement: Lexical Retrieval Drives Answer Assembly

`answer(question, *, bundle_dir, llm, limit)` MUST retrieve hits via
`FtsIndex.search(question, limit=limit)`, place each retrievable hit's
concept body into the LLM context, call `llm.chat(...)` exactly once, and
return an `AnswerResult` whose `answer` is the LLM's returned text.

#### Scenario: Matching concepts produce a cited answer

- GIVEN a bundle containing concepts that match the question
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN `FtsIndex.search` is called with the question and resolved limit
- AND `llm.chat` is called exactly once with the matched concept bodies
- AND `AnswerResult.answer` equals the LLM's response text

### Requirement: Default Retrieval Limit

`limit` MUST default to 5 and MUST be forwarded unchanged to
`FtsIndex.search`.

#### Scenario: Caller omits limit

- GIVEN a caller invokes `answer` without a `limit` argument
- WHEN retrieval executes
- THEN `FtsIndex.search` is called with `limit=5`

### Requirement: Zero Hits Return A Canned No-Match Result

WHEN `FtsIndex.search` returns no hits, `answer` MUST return an
`AnswerResult` with empty `citations` and a stable, non-empty no-match
message, and MUST NOT call `llm.chat`.

#### Scenario: No matching concepts found

- GIVEN a question with zero FTS hits
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN `llm.chat` is never invoked
- AND `citations` is empty and `answer` is a non-empty no-match message

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

`retrieval/answer.py` MUST NOT import `openkos.config`. The `LLMBackend`
instance MUST be supplied by the caller; the module MUST NOT construct or
select an LLM backend itself.

#### Scenario: Module has no config dependency

- GIVEN a static import check of `retrieval/answer.py`
- WHEN its imports are inspected
- THEN `openkos.config` is absent, and the only `LLMBackend` source is the
  `llm` parameter passed by the caller

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

`AnswerResult` MUST additionally carry: `fts_hit_count` (int, raw
`FtsIndex.search` hit count before guarded re-read filtering),
`llm_invoked` (bool), `cited_count` (int, equals `len(citations)`),
`no_match_cause` (`None` on a successful answer, else one of
`"zero_hits" | "all_unreadable" | "empty_query"`), and `skip_notices`
(`list[str]`, copied from `FtsIndex.skipped` for that build). The module
MUST remain config-free and backend-injected (unchanged from the existing
requirement); `test_answer_module_does_not_import_config` MUST still pass.

#### Scenario: Successful answer sets success metadata
- GIVEN a question with readable, matching hits
- WHEN `answer(...)` returns a non-`NO_MATCH` answer
- THEN `llm_invoked` is `True`, `no_match_cause` is `None`, and
  `cited_count` equals `len(citations)`

#### Scenario: Zero hits set zero_hits cause
- GIVEN a question with zero FTS hits
- WHEN `answer(...)` is called
- THEN `fts_hit_count` is `0`, `llm_invoked` is `False`, and
  `no_match_cause` is `"zero_hits"`

#### Scenario: All hits unreadable set all_unreadable cause
- GIVEN FTS returns hits but every hit fails guarded re-read/parse
- WHEN `answer(...)` is called
- THEN `fts_hit_count` is greater than `0`, `cited_count` is `0`,
  `llm_invoked` is `False`, and `no_match_cause` is `"all_unreadable"`

#### Scenario: Skip notices carried regardless of match outcome
- GIVEN the build produces a non-empty `FtsIndex.skipped`
- WHEN `answer(...)` is called, on either a matched or no-match path
- THEN `AnswerResult.skip_notices` equals that build's skip notices

### Requirement: Empty Query Sets A Distinct No-Match Cause

WHEN `question.strip()` is empty, `answer` MUST NOT invoke the LLM and
MUST return a no-match `AnswerResult` with `no_match_cause` equal to
`"empty_query"`, distinguishable from `"zero_hits"`.

#### Scenario: Whitespace-only question
- GIVEN `question` is empty or contains only whitespace
- WHEN `answer(question, bundle_dir=bundle_dir, llm=llm)` is called
- THEN `llm.chat` is never invoked and `no_match_cause` is
  `"empty_query"`
