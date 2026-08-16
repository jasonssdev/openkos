# Archive Report: Named Person Capture (every named person is identified)

**Change**: named-person-capture
**Issue**: #712 (closed), with slice 1 gating on #715/#714
**Archived**: 2026-08-16
**Artifact Store**: hybrid (Engram + filesystem)
**Status**: Complete — 3 slices shipped, 1 slice closed UNSHIPPED

## Summary

Owner ruling #712: people must ALWAYS be identified, including the
merely-named who never speak, and a merely-named person carries the same full
lifecycle as any other object. The change retired the participant anchor gate
that was rejecting name-only candidates, replaced it with an ADVISORY grounding
signal that reads the SOURCE instead of the model's own paraphrase, and
measured — rather than assumed — every other lever it proposed.

Two of its five proposed levers were closed unshipped by their own gates. That
is the change's main result, not a shortfall: both were rejected on measurement
this repo can reproduce.

## What Shipped

- **PR #716** (`ab33970`) — slice 1, `evals/named_person_volume/`. Its Phase 0.2
  gate returned **REJECT** on the D2 capture-prompt rewrite (latency 1.92x,
  104.7s vs 54.6s; merely-named count 0 vs 0). Per D2 a rejection ships nothing
  prompt-level, so **task 2.4 is closed unshipped**. The rewrite survives as a
  reproducible monkeypatch in the harness.
- **PR #719** (`f838854`) — slices 2 and 4. The anchor gate is deleted from the
  judge re-admission conjunct; `_has_participant_anchor` and
  `_PARTICIPANT_ANCHOR_RE` survive as exports ONLY so
  `evals/participant_anchor --rescore` can still re-derive #706's verdict from
  its stored runs (design D6). `_names_absent_from_source` grounds a proposed
  participant against `source_text`, advisory-only.
- **PR #720** (`ca84660`) — the four-lens findings on that grounding: word
  boundaries instead of a raw substring (`Ana` was grounding inside `mañana`),
  and a half-majority label-only exemption instead of `all()`, which let a
  single `Presenter:` line disable the exemption for a whole AMI transcript.

Both #719 and #720 were HIGH-risk canonical 4R reviews with valid receipts.
#690 was verified closed against this code end to end: 4 `Person` objects,
exactly the four participants, with `Germán Vega / Representative from Vega
Ingeniería` — the case the retired gate killed 3/3 — surviving at
`sensitivity: confidential`, the first time ADR-0015 reached personal data.

## What Did Not Ship, and Why

- **Task 2.4** (D2 capture-prompt rewrite) — REJECTED by slice 1's own gate.
- **Slice 3** (two-lane participant budget) — **closed UNSHIPPED 2026-08-16**
  by owner ruling, on evidence. D4's reopen trigger is "a stored run whose
  participant lane actually truncates"; a sweep of every stored run in every
  participant-bearing harness found `_UNION_BACKSTOP = 20` has never bound.
  Largest retained set on record: 9 objects (`stage_attrition`, 45 runs), 7
  with `--participants` on (`participant_anchor`, 9 runs), at most 5
  participant candidates ever produced, `p_max` = 3. The full table and the
  reasoning are in `STATUS.md`; the capacity derivation is annotated in
  `evals/named_person_volume/report.md`.

## Spec Merge — what was merged, dropped, and corrected

Delta specs do not merge themselves, and a spec that overstates the code is a
defect. Every heading was name-matched against `openspec/specs/` and against
shipped code before merging:

**Merged into `openspec/specs/extraction-union-judge/spec.md`:**
- MODIFIED `Judge Re-Admission Set Extended to Person/Organization (Additive
  Only)` — the re-admission scenario no longer requires a participant anchor.
- MODIFIED `Judge Re-Admission Scoped to Meeting-Shaped Sources` — the shape
  test is `_is_meeting_shaped` (title OR content), not
  `_MEETING_SHAPED_TITLE_RE`. That name had been stale since #673; carrying it
  forward would have re-merged the staleness.
- REMOVED `Stub Rejection at Judge Re-Admission`. **This requirement was
  contradicting shipped code on `main` from the moment #719 merged** — the
  gate it mandates was deleted, and the spec still required it. The archive is
  what closes that window.

**Created `openspec/specs/participant-name-grounding/spec.md`** (the capability
did not exist), with two corrections made during the merge audit:
- The delta said the exemption applies to a source "whose meeting-shaped
  detection matched **solely** via the single/two-letter speaker-label path".
  The shipped code (`concept.py:1301-1308`) does not consult `_is_meeting_shaped`
  at all: it derives labels from `source_text` and exempts when **at least half**
  the DISTINCT labels are ≤ 2 characters. The delta's second scenario ("detected
  via title regex ⇒ advisory applies normally") was false under that rule and is
  replaced by two accurate scenarios, one of them pinning the `Presenter:` case
  PR #720's review fixed.
- Added the word-boundary matching property, also from #720's review, so the
  spec states the behavior that shipped rather than the substring test that
  did not survive review.

**Dropped rather than merged** (slice 3 behavior that does not exist):
- `Participant Budget Lane Separate From the Subject Backstop` (ADDED, from the
  extraction-union-judge delta).
- The entire ingestion delta, `Participant-Lane Truncation Is Disclosed`.

## Stale prose corrected at archive

`_PARTICIPANT_CAPTURE_SYSTEM_PROMPT`'s docstring still claimed its candidates
"are gated on exactly like every other Person/Organization candidate reaching
judge re-admission" and that a name-only answer "would only be discarded
downstream". Slice 2 retired that gate; both sentences were false on `main`.
Task 2.9 grepped `docs/` for this prose and did not grep `src/`. The prompt
still asks for an anchor — that request is unchanged — but its rationale is now
honest: a quality request, never a precondition, and nothing may be inferred
from its absence.

## Verify

No separate `sdd-verify` pass ran for this change. Each slice shipped through
its own PR with CI green and, for #719/#720, a canonical 4R review with a valid
receipt; the archive's verification was the spec-to-code audit recorded above,
which found and fixed three mismatches (the stale `Stub Rejection` requirement,
the label-only exemption's wording, and the capture-prompt docstring).

## Follow-ups

None filed. D4's reopen trigger for slice 3 remains the standing condition that
would revive it, and is restated in `STATUS.md`.
