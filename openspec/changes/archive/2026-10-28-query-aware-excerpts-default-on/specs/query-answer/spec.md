# Query Answer Specification (delta)

## MODIFIED Requirements

### Requirement: Context Excerpts May Be Chosen By Similarity To The Question

`answer` MUST accept a keyword `query_aware_excerpts`, defaulting to `False`
(a library or eval caller keeps the even-coverage bound unless it opts in), and
`run_query` MUST pass `cfg.query_aware_excerpts` (the `openkos.yaml` key of the
same name, a boolean defaulting to `true`; a non-boolean MUST be refused).

WHEN it is `False`, the assembled prompt MUST be even-coverage, byte-identical
to a call made before the parameter existed.

WHEN it is `True` and a retrieved document exceeds its share of the context
window, the windows kept for that document MUST be those with the highest
Okapi BM25 score against the question, computed over that document's own
windows without any model call, chosen within the same budget and the same
`fair_shares` split, ordered as in the document, and joined with the same
elision marker across gaps and a newline between adjacent windows. Ties MUST
resolve to the earlier window. A document that fits its share MUST be sent
unchanged. WHEN the question has no usable term, or matches no window, the
excerpt MUST be the even-coverage one.

#### Scenario: The flag down changes nothing

- GIVEN a document larger than its share whose answer lies mid-document
- WHEN `answer` runs with `query_aware_excerpts` omitted or `False`
- THEN the prompt equals the even-coverage prompt

#### Scenario: A workspace that never set the key gets the query-aware excerpt

- GIVEN an `openkos.yaml` without `query_aware_excerpts`
- WHEN `run_query` runs
- THEN it passes `query_aware_excerpts=True` to `answer`
- AND `query_aware_excerpts: false` in the file passes `False`

#### Scenario: The window with the question's terms survives

- GIVEN the same document and `query_aware_excerpts=True`
- WHEN `answer` runs
- THEN the prompt contains the window holding the question's terms, in
  document order, with the elision marker across gaps

#### Scenario: A fitting document is untouched

- GIVEN a document within its share
- WHEN `answer` runs with `query_aware_excerpts=True`
- THEN the document is sent byte-identical and not marked excerpted

#### Scenario: No signal falls back to even coverage

- GIVEN a question with no term in any window
- WHEN `answer` runs with `query_aware_excerpts=True`
- THEN the excerpt equals the even-coverage excerpt
