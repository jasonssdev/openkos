# Delta for judging-application-service

## ADDED Requirements

### Requirement: A Changed Finding Is Re-Checked For Its One Pair

A `contradiction` or `revision` queue row whose pages changed MUST be re-checked
through the same quote, consent and run protocol, for that one pair only.
`finding_recheck.quote_finding(root, kind, decision_key)` MUST return a
`JudgingQuote` of that kind whose work is the one pair behind the open row,
bound to the pair's current input digests, with `calls` 1, and MUST make no
model call. A row that is not open, or a pair the detection no longer keeps (a
contradiction the candidate search drops, a revision with no current vector),
MUST quote no work. A `decision_key` that does not name a decision of `kind`
MUST be refused with `ValueError`.

`finding_recheck.run_finding` MUST judge only the quoted pair, through the
existing detection over a plan narrowed to that candidate (`find_contradictions`
for a contradiction, `judge_revisions` for a revision), and MUST NOT detect over
the rest of the workspace. A pair whose pages changed since the quote, or whose
row is no longer open, MUST be `settled_since_quote` and MUST NOT be judged. The
verdict MUST be stored only when the pair's pages are as they were when the call
began, for both kinds: the inputs are pinned before the call, a contradiction's
through `persist_findings` and a revision's inside `judge_revisions` (which the
`revisions` verb and the daemon share), and a verdict whose inputs moved is
dropped, never stored against content nobody judged. When it is stored and still holds, the open row MUST be replaced by a new
row (the old one retired as stale); when it no longer holds, the open row MUST
be retired as stale; when it is not stored, the row MUST be left as it was. A
retired row MUST NOT be counted as a resolution.

#### Scenario: Only the named pair is judged and its row replaced

- GIVEN two changed contradiction rows
- WHEN one is quoted and run
- THEN one model call is made, the quoted row is replaced by a current one, and
  the other row is untouched

#### Scenario: A disagreement that no longer holds retires the row

- GIVEN a changed contradiction row
- WHEN the run's verdict is `consistent`
- THEN the open row is retired as stale and no row replaces it

#### Scenario: A page edited during the call stores nothing

- GIVEN a quoted pair
- WHEN a page is edited while the model is called
- THEN the verdict is dropped, the row is left as it was, and the outcome counts
  one dropped verdict

#### Scenario: A revision judged on pages edited during the call leaves no finding

- GIVEN a quoted revision pair
- WHEN a decision page is edited while the model is called
- THEN `revision_findings` holds no row for the pair, so no later read treats the
  verdict as fresh, and the open row is left as it was

#### Scenario: A pair edited after the quote is not judged

- GIVEN a quote for a changed pair
- WHEN a page of the pair changes before the run
- THEN no call is made and the outcome counts one `settled_since_quote`
