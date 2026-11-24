# Design: verified-concept-evidence

## D1. Verify and count; do not persist

The quote only feeds the ingest notice. The field lives on `ExtractionResult`
as raw model output and is never written to the body, description or
frontmatter, so the OKF output is byte-identical and no extension field is
added. Appending a `> quote` to the body (as the decision pass does) would also
make `lint` and `run_duplicates`, which recompute `evidence_line` from stored
bodies, agree with ingest; it was rejected as a behaviour change to stored
objects that the issue's option B only offered, not required.

Known consequence: `lint` names derived objects by re-running `evidence_line`
over stored bodies, so when a Source carries the notice because of one object,
lint may also list an object that was cleared at ingest by its quote. The
Source-level finding is unaffected, since it keys off the stored notice.

## D2. Same rule as the decision pass, plus the word floor

`decision_pass.quoted_verbatim` has no length floor because its quote is
anchored to an item the pass already isolated. Here the quote is the only
evidence for an object, so a one- or two-word quote would verify nearly
anything; `verified_quote` reuses `_MIN_EVIDENCE_WORDS` (4). Containment is
one-directional: a quote that extends a source line copied nothing, unlike
`evidence_line` arm (b).

## D3. Fail-closed parsing

`evidence` is advisory like `type_alternative`: a non-string degrades to "no
field" and never drops the candidate. `llm/parsing.py` is untouched; the extra
key passes through the existing JSON extraction.
