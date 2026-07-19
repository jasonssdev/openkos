# Query Answer Specification (Delta)

## ADDED Requirements

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
