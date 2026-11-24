# Archive Report: verified-concept-evidence

**Archived**: 2026-11-23
**Archive folder**: `openspec/changes/archive/2026-11-23-verified-concept-evidence/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1768.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| ingestion | Updated | 1 ADDED requirement appended: A Verified Evidence Quote Counts As An Object's Evidence (4 scenarios). No MODIFIED, REMOVED or RENAMED headings. |

## Requirement-vs-Code Check

Checked against `extraction/evidence.py` (`verified_quote`, `_normalize`, `_MIN_EVIDENCE_WORDS`, `_NEEDLE_TRAILING_PUNCTUATION`) and `extraction/concept.py` (`_validate`, `_unevidenced_titles`). No mismatch: the four-word floor, casefold and whitespace-collapse normalization, trailing-punctuation trim on the quote only, the non-string degrade to no field, and the check order (verified quote first, then `evidence_line` over the written text) all match. The value is carried only on `ExtractionResult.evidence` and is not written by the document writers.
