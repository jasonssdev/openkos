# Proposal: Surface The Confidential-Commit Notice And The Insecure-Key Warning

Tracking issue: #1403. Governing decision: ADR-0060.

## Intent

The CLI tells the user two things the Python API did not carry: that a decision's
local commit holds a confidential concept, and that an `openai-compatible` API key
is about to be sent over plain HTTP to a non-local host. The API prints nothing by
design, so an app built on it lost both advisories.

## Scope

- `Applied` gains `committed_confidential` (default false), set from the shared
  commit core's `CommitOutcome.includes_confidential`.
- `QuoteNotice` gains the code `insecure_key`, carrying the text of the shared
  `insecure_key_warning` predicate, on every kind of quote.
- Both are additions (a defaulted field and a widened `Literal`); `API_VERSION`
  stays 1 and the surface snapshot is regenerated.
- Amend the `stable-python-api` and `judging-application-service` requirements.

## Out of scope

The non-local `https://` key notices (`remote_key_notices`) and the embedding
endpoint, which the CLI also prints; they can follow the same shape.
