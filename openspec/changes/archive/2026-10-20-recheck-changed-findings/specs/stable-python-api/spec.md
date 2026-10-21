# Delta for stable-python-api

## ADDED Requirements

### Requirement: A Changed Finding Is Re-Checked Through A Quote By Its Decision Key

`openkos.api.quote_finding_recheck(workspace, kind, decision_key, *,
include_confidential=False, chat_client=None)` MUST return a `JudgingQuote` for
re-checking the one pair behind the open `contradiction` or `revision` decision
`decision_key`, with no model call, and `run_judging` MUST run its token like any
other, judging that pair only. `FindingKind` (`contradiction`, `revision`) and
`BatchJudgingKind` (`identity`, `relation_type`, `volatility`) MUST be exported;
`quote_judging` MUST keep accepting the batch kinds only, so its signature is
unchanged. `JudgingKind` and the `kind` of `JudgingQuote`, `QuoteBinding`,
`QuoteStale` and `JudgingOutcome` MUST include the two finding kinds. A `kind`
that is not a finding kind, or a `decision_key` that does not name a decision of
`kind`, MUST raise `ValueError`.

The change is additive: `API_VERSION` is unchanged and the surface snapshot
classifies it as additive only.

#### Scenario: A contradiction is quoted by its key and run

- GIVEN a changed contradiction decision
- WHEN `quote_finding_recheck(workspace, "contradiction", key)` and then
  `run_judging(workspace, quote.token)` are called
- THEN the outcome's kind is `contradiction` and the decision is current

#### Scenario: The batch quote does not take a finding kind

- GIVEN `quote_judging(workspace, "contradiction")`
- WHEN it is called
- THEN `ValueError` is raised and no client is built
