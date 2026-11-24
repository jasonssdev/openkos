# Proposal: verified-concept-evidence

Refs #1768 (tracking #1770).

## Intent

The ingest notice `objects-without-evidence` flags a derived object whose
written text shares no line with its source. A model that paraphrases faithfully
is flagged although it understood the source (#1767: hosted models 19/27 to
156/156 flagged, qwen3:8b 5/50), because the extraction reply has no place to
carry a quote. This lets a concept item carry an optional verbatim `evidence`
quote that the engine verifies against the source.

Success: an item whose `evidence` occurs verbatim in the source is not named by
the notice; an absent or unverifiable `evidence` leaves behaviour exactly as it
is today.

## Scope

### In Scope

1. `extraction/concept.py`: `_validate` reads an optional `evidence` string onto
   `ExtractionResult`; `_unevidenced_titles` accepts a verified quote.
2. `extraction/evidence.py`: `verified_quote`, the verbatim check.
3. CHANGELOG.

### Out of Scope

- Any change to `prompts/extraction/*.md`: nothing asks the model for the field
  yet. Whether to ask is option A, measured by a separate pre-registered A/B.
- Persisting the quote or appending it to the body.
- Query-time use of the quote.

## Approach

`verified_quote(quote, source)` applies the decision pass's
`quoted_verbatim` rule (casefold, whitespace collapse, trailing punctuation
trimmed from the quote, substring of the normalized source) plus the
existing four-word floor. `_unevidenced_titles` skips an object when its quote
verifies, and otherwise runs the existing `evidence_line` check unchanged.

## Risks

Low: with no `evidence` key nothing changes. A model that invents a quote gains
nothing, since the quote must occur in the source.
