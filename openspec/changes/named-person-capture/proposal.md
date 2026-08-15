# Proposal: Always Identify Named People (#712)

## Intent

A person who is only named is dropped today by two prompt instructions and
one deterministic gate. Owner ruling (#712): they must still become a
`Person`. #706 measured that the gate never fired — the prompts are the
active suppressor. Nobody has measured how many merely-named people a real
transcript yields once the anchor demand is gone, so any budget number
chosen before that measurement would be invented.

## Scope

### In Scope (ordered slices, auto-chain)

1. **Measure first (prerequisite).** New eval directory measuring
   merely-named `Person` volume per transcript under an anchor-free capture
   prompt, on ES and AMI fixtures. Sets the participant-lane capacity.
2. **Reverse the stub rule.** Rewrite `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT`'s
   "a name alone is NOT a valid answer" instruction; remove the
   `_has_participant_anchor` conjunct at judge re-admission.
3. **Two-lane budget.** A separate participant capacity beside
   `_UNION_BACKSTOP` (20, which stays the SUBJECT ceiling), with its own
   report field and its own CLI notice — mirroring
   `participant_anchorless_discarded_titles` + `cli/main.py:3055`, never
   folded into `_extraction_cap_notice` (`cli/main.py:3072`).
4. **Name grounding (measurement-gated).** Advisory, report-only first: flag
   a proposed name absent from `source_text`, do not reject. Must be
   inapplicable when the source is label-only (AMI `A:`/`B:`), or it rejects
   every AMI participant. Promotion to a rejecting filter requires its own
   measurement.

### Out of Scope

- Any `_SYSTEM_PROMPT` edit (pinned verbatim, `test_concept.py:1488`).
- Identity/merge design — the #668 D8 seam stays deferred, as #668 deferred
  it; this change makes it bind sooner.
- Lifecycle shape: merely-named persons get the SAME lifecycle as speakers
  (owner ruling) — no lighter object shape.
- Rewriting `evals/participant_anchor/`'s historical report.
- Picking the capacity number here; slice 1 sets it.

## Capabilities

### New Capabilities

- `participant-name-grounding`: advisory source-mention signal for proposed
  person names, with a label-only-corpus exemption.

### Modified Capabilities

- `extraction-union-judge`: `Stub Rejection at Judge Re-Admission` (lines
  221-242) is reversed/removed; the anchor wording in
  `Judge Re-Admission Set Extended to Person/Organization` (line 193) and
  `Judge Re-Admission Scoped to Meeting-Shaped Sources` (line 255) is
  re-worded; participant lane added.
- `ingestion`: participant-lane truncation disclosure, distinct wording from
  the cap, judge, and pre-judge notices.

## Approach

Lever the capture prompt and the deterministic conjunct only. Slice the
budget per lane, disclose per lane. Ship nothing prompt-level before slice 1
reports (#613/#622/#630/#706 precedent: two measured treatments rejected).

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/extraction/concept.py` | Modified | Capture prompt, re-admission conjunct, participant backstop |
| `src/openkos/cli/main.py` | Modified | New participant-lane notice near `:3055` |
| `openspec/specs/extraction-union-judge/spec.md` | Modified | Delta: reverse stub rejection, re-word two adjacent requirements |
| `openspec/specs/ingestion/spec.md` | Modified | Delta: lane disclosure |
| `tests/unit/extraction/test_concept.py` | Modified | Rewrite anchor-gate tests (`:2887`, `:2925`); do NOT touch `:1488` |
| `evals/` | New | Volume eval; `participant_anchor/` untouched |
| `docs/` | Modified | Grep stale "Person needs an anchor" prose |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Person volume floods retrieval with thin stubs | High | Slice 1 measures it before any prompt ships; participant lane caps it |
| Anchor-free prompt regresses subject recall | Med | Eval arms compare subject recall, not only person count |
| Grounding check rejects AMI participants | High | Advisory-only first; label-only corpora exempt |
| ADR-0015 `{Person: 1}` sensitivity at higher volume | Med | No ADR change; flag consequence at higher Person counts |

## Rollback Plan

Each slice reverts independently. Slice 2 restores the capture prompt string
and the `meeting_shaped and _has_participant_anchor(c)` conjunct; slice 3
restores the single `retained = kept[:_UNION_BACKSTOP]` slice plus drops the
new field and notice. Slice 1 is eval-only.

## Dependencies

- Slice 1 gates slices 2-4; Ollama-backed eval runtime for slice 1.

## Success Criteria

- [ ] Volume measurement published in a NEW eval directory, with a number
      for participant-lane capacity.
- [ ] A merely-named, never-speaking person becomes a `Person` on a
      meeting-shaped source.
- [ ] Participant-lane truncation is disclosed in wording distinct from the
      subject cap notice.
- [ ] `_SYSTEM_PROMPT` and `test_concept.py:1488` unchanged.
- [ ] `evals/participant_anchor/report.md` unchanged.
