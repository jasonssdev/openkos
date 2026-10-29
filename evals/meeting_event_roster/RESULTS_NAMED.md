# Results: the named-speaker roster rule (#1689, task T5a), LICENSED

Registration: `PREREGISTRATION_NAMED.md` (PR #1722), run unchanged. Stamp:
harness commit `01309eebd6a4a9e6c2c3bde470682cc81609fe53` (main after PR #1722).
The check is model-free: no model was called and no GPU was used. The raw
`named` output stays outside the repository.

## Verdict

**LICENSED: N1 to N6 all hold.** The command ran once, after the registration
merged; nothing was re-run, subset or extended.

## Phase 0

`--self-test`: 30 of 30 passed.

## Bars

| Bar | Rule | Observed | Result |
|---|---|---|---|
| N1 precision | equal to 1.0 over the positive fixtures | 1.0 (14 positives) | pass |
| N2 recall | at least 0.95 over the positive fixtures | 1.0 | pass |
| N3 exact rosters | at least 0.90 of positive fixtures | 14 of 14 (1.0) | pass |
| N4 negatives | 0 labels on the negative fixtures | 0 | pass |
| N5 real (medium-10) | precision 1.0, recall at least 0.95 | 1.0 and 1.0, exact 1 of 1 | pass |
| N6 AMI unchanged | `roster_of_named(text) == roster_of(text)` on all 5 sources | `changed: []` | pass |

No fixture was wrong (`wrong: []`), and no negative fixture produced a roster
(`negatives_with_a_roster: {}`).

## The closing rule

LICENSED: per the registration, the roster rule is generalized as
`roster_of_named`, and the OpenSpec change for the roster-grounded Event body
may proceed with it (and with the retry arm's re-ask, as that arm's closing rule
says). Nothing is adopted by this file; production code and defaults are
unchanged, and #1689 stays open.

## Cost

Zero GPU and a few seconds of CPU, as forecast.

## Deviations

None. The command ran once, after the registration merged to `main`; nothing was
re-run, subset or extended after a number was seen. The registration's owner
confirmation was given under a standing instruction, not item by item, as the
registration itself records.

## Limits

Stated plainly, as registered:

- The 14 positive and 8 negative fixtures were written together with the rule,
  by the same author, and adjusted after the rule failed on them, so a pass on
  them is weak evidence.
- The only real named-speaker transcript is one fictional committed meeting
  (medium-10); the AMI sources are a no-regression control, not named-speaker
  evidence. Exports with `Name (role):`, `Name - text`, `<Name>`, all-caps
  names, lowercase handles or non-Latin scripts are not measured.
- Limits inherited from production's speaker grammar are not fixed here: a name
  of four or more words, a recurring heading label, and an all-capitals label of
  3 or more characters outside the AMI union.
- Roster extraction only: whether the model uses the list well on named
  speakers is an unmeasured expectation; no model arm was registered.
