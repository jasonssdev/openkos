# Delta for extraction-union-judge

## ADDED Requirements

### Requirement: Extraction Exposes An Upper Bound On Its Calls

`extraction.concept` MUST expose `max_extraction_calls(source_text, *,
union_judge, transcript_decisions)`, a pure function that returns a number of
chat calls no extraction of `source_text` can exceed, whatever the model
replies and whatever title the source is given. It MUST count each mechanism at
its maximum: two calls per window on a chunked source (the window's retry), two
below the threshold (the union path's two passes, or the single-run path's
retry of an empty reply), one re-ask, and on the union path one participant
pass for a meeting-shaped source, two calls per window (one window below the
threshold) for the decision pass when `transcript_decisions` is on and the
source is meeting-shaped, and `JUDGE_ATTEMPTS` judge calls. Because the title
decides whether a source reads as a meeting, which moves the chunking threshold
and the meeting-only passes, it MUST return the larger of the two readings.
Blank text MUST return 0. It MUST never be below `estimate_extraction_calls`
for the same text and settings.

#### Scenario: Every mechanism at its maximum on a short source

- GIVEN a short source, the union path and the decision pass on
- WHEN the bound is computed
- THEN it is two passes, one re-ask, one participant pass, two decision calls
  and every judge attempt

#### Scenario: Adversarial replies stay within the bound

- GIVEN a chunked source and replies that fail each window's first attempt and
  never give the judge or the decision pass a usable reply
- WHEN `extract_concept_union` runs
- THEN the calls made are at most the bound and at least two per window
