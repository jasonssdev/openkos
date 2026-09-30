# Design note: choosing the proximity floor (#1052)

Written, with the rule below, before the calibration fixture was scored.

## What the floor trades

Proximity only NOMINATES: a candidate pair becomes an untyped edge that
`suggest-relations` asks an LLM to type and a human must accept, and that
`contradictions` checks. A false positive therefore costs review time and
LLM calls, and — because `graph/sqlite_graph.py` keeps at most
`_MAX_CANDIDATE_EDGES` candidates per build, ranked by distance — it can
crowd a genuine candidate out of the capped slice. A false negative costs a
suggestion the user never sees, which is silent but recoverable by hand
(`relate`). The rule is conservative about change rather than about either
error: 0.70 holds unless the evidence for moving is complete, exposed to
hard negatives, and worth a material recall gain, because a floor tuned on a
hand-written set can only be trusted inside the region that set probed.

## What each gate protects against

- **Completeness (`n of TOTAL`).** A filtered probe hides its complement: a
  pair that failed to embed must never vanish silently from a class.
- **Fixture size.** Nine pairs were a smoke check; the gate names the size
  below which a calibration is refused rather than reported.
- **Hard negatives must be harder.** A zero-false-positive result is
  vacuous when no negative could have exceeded the floor. Hard negatives are
  same-domain, different-subject pairs (often sharing tags or a title word)
  and must score measurably above the cross-domain easy negatives.
- **Exposure at a lower floor.** Lowering the floor is only safe if
  negatives were observed close below it; otherwise the fixture never
  tested the region the new floor opens.
- **Lowest related against highest unrelated.** Reported beside the verdict
  as the descriptive gap (or overlap) between the two classes.

## Labelling criteria

- **Related**: a curator reviewing the pair would plausibly record a
  relation (same subject, a part and its whole, a mechanism and its
  instance, the same concept in another language).
- **Unrelated**: a curator would dismiss the nomination. **Hard** when both
  documents share a domain (and often tags or a title word) but not a
  subject; **easy** when they share neither.
- **Hard positives** are related pairs worded differently: cross-lingual
  (English/Spanish — `bge-m3` is multilingual, ADR-0006), cross-domain, or
  linked by mechanism rather than vocabulary.

## What this does not model

`TOP_K` (5 neighbors per anchor) and `_MAX_CANDIDATE_EDGES` also limit
nomination in production. Pairwise cosine is measured without them, so the
harness calibrates the floor alone; the caps can only remove candidates the
floor admits, never add one.

## Decision rule

<!-- rule:begin -->
**Pre-registered decision rule (#1052).** Frozen before the first live run
of the calibration fixture; the harness applies it mechanically and never
substitutes another.

A pair is *nominated* at floor `t` when its document-vector cosine is
`>= t` (production keeps a neighbor whose L2 distance is
`<= MAX_NEIGHBOR_DISTANCE`, the same condition). Candidate floors are the
grid `t = 0.40, 0.41, ..., 0.90`. `H` is the number of hard negatives.
`recall(t)` is the share of ALL related pairs nominated at `t`.

1. **INCOMPLETE** if any labelled pair was not scored (`n of TOTAL` must
   be equal for related, unrelated, hard and easy pairs). No conclusion.
2. **INVALID_FIXTURE** if the fixture has fewer than 40 related pairs,
   40 unrelated pairs, 20 hard negatives or 10 hard positives. No
   conclusion.
3. **INSUFFICIENT_EXPOSURE** if `median(hard negatives) <= median(easy
   negatives)`: the "hard" negatives are not measurably harder, so any
   zero-false-positive result would be vacuous. Keep 0.70.
4. A floor `t` is *admissible* when at most `floor(0.05 * H)` hard
   negatives AND zero easy negatives are nominated at `t`. `t_min` is the
   lowest admissible grid floor, and the candidate is
   `t* = t_min + 0.02` (safety margin: a hand-written negative set
   under-samples the negative tail).
5. **OVERLAP** if no grid floor is admissible, if `t* > 0.90`, or if
   `recall(t*) < 0.50`: no single floor both respects the false-nomination
   budget and keeps most related pairs. Keep 0.70; cosine proximity
   alone cannot serve, which is a design question, not a constant.
6. **MOVE (raise) to `t*`** if 0.70 itself is not admissible: today's
   floor already over-nominates hard negatives.
7. **KEEP 0.70** if 0.70 is admissible and `t* >= 0.70`.
8. If `t* < 0.70`: **KEEP 0.70** when `recall(t*) - recall(0.70) < 0.10`
   (the gain does not justify the churn); otherwise
   **INSUFFICIENT_EXPOSURE** when fewer than 5 hard negatives score
   `>= t* - 0.10` (nothing tested the new floor from below, so its
   zero-false-positive claim would be vacuous; keep 0.70 and extend the
   hard negatives); otherwise **MOVE (lower) to `t*`**.

A MOVE verdict supports changing `CANDIDATE_SIMILARITY_THRESHOLD`; the
change itself lands in its own reviewed PR, never in the measurement's.
Labels are frozen with this rule: a label changed after scores are seen
must be reported with the verdict under BOTH label sets.
<!-- rule:end -->
