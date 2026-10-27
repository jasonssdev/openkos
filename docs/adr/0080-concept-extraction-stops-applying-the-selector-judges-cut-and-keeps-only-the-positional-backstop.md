---
type: Decision
title: "ADR-0080: Concept extraction stops applying the selector judge's cut and keeps only the positional backstop"
description: Proposes that the union extractor skip the selector-judge call by default and keep the pre-judge ceiling of 24 and the positional backstop of 20, because the pre-registered backstop-only comparison came back ADOPT-CANDIDATE while the cut showed no measured benefit. It prices what moves downstream and what the evidence cannot show.
status: Proposed
date: 2026-10-27
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-27T00:00:00Z
sensitivity: public
---

# ADR-0080: Concept extraction stops applying the selector judge's cut and keeps only the positional backstop

- **Status:** Proposed
- **Date:** 2026-10-27

## Context

The union extractor (`extract_concept_union` in `extraction/concept.py`) runs
the source twice below the chunk threshold (once per window above it), merges
the candidates, cuts the merged list to a ceiling of 24 round-robin across
windows (`_MAX_JUDGE_CANDIDATES`), and then asks a **selector judge**
(`extraction/judge.py`, one call, at most `JUDGE_ATTEMPTS` = 2) which
candidates to keep. Person, Organization and Procedure candidates are
re-admitted after the judge, and a positional backstop of 20
(`_UNION_BACKSTOP`) is applied last. If the judge fails, the backstop is
skipped (`cap_applies = judge_status != "failed"`, #754), so the set stays
bounded by the ceiling of 24.

No ADR introduced this. It came from the `union-judge-extraction` change
(archived 2026-08-07, issue #456, design D3, D8 and D9), and its contract lives
in `openspec/specs/extraction-union-judge/spec.md`: "Judge Selection Over a
Closed Candidate List", "Judge Failure Fails Closed to the Backstopped Union",
"Backstop Cap Applied Once, After Judge Selection" and "Opt-Out Configuration
Flag". The packaged default is `DEFAULT_UNION_JUDGE = True`. Issue #564 later
raised the backstop from 12 to 20.

Issue #1579 asked what that cut still buys. Epic #1556's rules (pre-register,
never adopt in a results PR, one ADR per adoption) were followed, on the
development-series meetings only:

- **Cost of the cut** (`STAGE_LOSS_RESULTS.md`, `JUDGE_STUDY_RESULTS.md`):
  window lift -0.0161 [-0.0276, -0.0081]; 38 of 450 gold sentences lost;
  5 of 5 models at the -0.005 bar.
- **Benefit of the cut, three axes** (`CUT_PRECISION_RESULTS.md`,
  `CUT_REDUNDANCY_RESULTS.md`, `LABEL_STUDY_RESULTS.md`): cut objects are as
  supported as kept ones (0.928 vs 0.927, self arm), about 94% are not
  near-duplicates of a kept object, and 81.9% to 83.2% are labelled worth a
  page. All three are inconclusive by their registered rules; the point
  estimates are near zero. The label study's validity control passed only at
  its bar (8 of 11).
- **Downstream price** (`DOWNSTREAM_COST.md`), model-free: removing the cut
  adds 115 to 120 objects and embedding calls over 25 sources, and 8 to 10
  identity review rows.
- **The registered comparison** (`BACKSTOP_ONLY_PREREGISTRATION.md`,
  `BACKSTOP_ONLY_AMENDMENT-1.md`, `BACKSTOP_ONLY_RESULTS.md`): backstop-only
  against the judge's set, paired per window. **ADOPT-CANDIDATE; every
  registered gate held in both arms.** `NEXT_STEP.md` lays out the options
  this follows (its option D).

| Gate | Arm | Point | One-sided bound | Margin |
|---|---|---|---|---|
| P1 window lift | self / fixed | +0.0153 / +0.0118 | +0.0069 / +0.0039 | -0.005 |
| P2 strict G1 precision | self / fixed | +0.0001 / +0.0097 | -0.0269 / -0.0190 | -0.05 |
| C1 objects, embedding calls | self / fixed | 1.537x / 1.574x | n/a | at most 1.65x |
| C1 identity rows per 25 sources | self / fixed | +8 / +10 | n/a | at most +12 |

The positional backstop alone cut 23 of 352 objects (13 of 303 windowed) and
gave up 0.0009 of lift. The confirmation set (six independent free-pool
meetings, `qwen3:8b`, 18 meeting-runs) agreed in sign: +0.0120 (self) and
+0.0157 (fixed) against a not-below -0.005 rule, with 64 of 69 restored
objects supported.

## Decision

Proposed: **the union extractor stops applying the selector judge's cut. It
keeps the pre-judge ceiling of 24 and the positional backstop of 20, and makes
no judge call by default.**

What would change:

- **A new config key `selector_judge`, default `false`.** The packaged default
  moves from "union and judge" to "union, no judge". `union_judge` keeps its
  name and its default `True`, and now means the union-of-runs extractor
  (two passes below the chunk threshold, one per window above it, participant
  capture, the opt-in decision pass). `union_judge: false` still restores the
  single-run `extract_concept` path unchanged.
- **`extract_concept_union` skips the judge when `selector_judge` is off.**
  `judge_status` takes the value it already has for "judge not run",
  `"skipped"`. That value already means the backstop applies, so the cut to 20
  is positional over the ceiling-bounded set, exactly the arm that was measured
  (the `ceiling` stage with the backstop applied last).
- **Calls per source fall by one** on the ordinary path, and by up to two on
  the bound a consent screen quotes (`JUDGE_ATTEMPTS`):
  `estimate_extraction_calls`, `max_extraction_calls`, the batch cost gate in
  `cli/main.py`, and `_settings_digest` in `application/ingest_consent.py`
  (which must include `selector_judge`) all follow the key.

What the judge still does after the change:

- **By default, nothing.** It makes no call and cuts nothing. It is not used
  for any other purpose today: `judge.select` returns titles to keep, and its
  only consumers are the cut, the Person/Organization/Procedure re-admission
  that corrects it, and the bookkeeping on `ExtractionReport`. With no cut
  there is nothing to re-admit (no Person, Organization or Procedure object was
  cut in either arm).
- **With `selector_judge: true`, exactly what it does today**: the same prompt,
  the same closed-list selection, the same fail-closed behaviour. The code is
  not deleted by this ADR. Removing it would be a later decision, after the
  default has run for a release.

This would supersede the part of the `union-judge-extraction` design that makes
the judge the primary selection mechanism, and the spec requirements named in
Context. There is no ADR to supersede; the archived design is not edited, and
the spec is changed through the OpenSpec change below, not here.

## Consequences

Costs, from `BACKSTOP_ONLY_RESULTS.md` and `DOWNSTREAM_COST.md` (25 meeting-runs,
5 extractors, 5 meetings, English AMI):

- **About 1.54x to 1.57x the objects and embedding calls** (329 vs 214 / 209).
  The fixed arm sat closest to its budgets: 1.574 of 1.65.
- **8 to 10 more identity review rows per 25 sources** (12 vs 4 / 2), and 19 to
  23 more per workspace of five meetings (26 vs 7 / 3). The cap of 50 never
  bound (at most 8 produced). Each row is one adjudication call and one row a
  person reviews.
- **A larger revision pool, unmeasured after the similarity threshold.** The
  similarity-blind upper bound is 1308 vs 481 (2.72x, self) and 1308 vs 692
  (1.89x, fixed) per five-meeting workspace. It tracks the square of the
  Decision ratio and keeps growing with every meeting added. How many of those
  pairs pass the threshold, and so how many review rows and calls they cost,
  has not been measured. Edge typing, contradictions and proximity edges were
  zero by construction in the measurement and are not priced.
- **S1 clutter caveat.** The share of restored objects a weak LLM label calls
  clutter was +0.0165 (self) and +0.0042 (fixed), with one-sided upper bounds
  +0.0749 and +0.0487. S1 did not gate. Had it gated at +0.05, the self arm
  would not have cleared it and the outcome would have been INCONCLUSIVE, as
  the registration expected. The control behind that label passed at its bar
  (8 of 11), so S1 is a weak signal either way: the extra objects may be
  somewhat more clutter, and nothing measured shows they are not.
- **Positional loss on large sources is unbounded.** The backstop binds in 8 of
  25 sources and is positional. The loss on sources with many more than 20
  objects was not measured, and prose notes and non-English text were not
  tested at all.
- **Decisions and Projects are no longer thinned.** The judge cut 44% and 32%
  of Decisions (57 and 41 of 129) and 70% to 90% of Projects; backstop-only
  keeps them, which is where most of the object growth comes from.

Benefits:

- **One model call fewer per source.** In the #1571 replays the fixed judge
  took about 3.4 s per call (350 s over 102 new calls,
  `JUDGE_STUDY_RESULTS.md`), so about 3.4 s saved per source on the packaged
  judge. The self judges, which run on the extractor's own model, took 926 s
  over 60 calls (about 15 s per call, with four model loads in that block).
  Production sends the whole (bounded) source to the judge, so a long source
  can cost more than the replay's figure; no production latency was measured.
  A source below the chunk threshold falls from 3 calls to 2.
- **The cut's measured price goes away:** the 0.0161 of window lift and the 38
  of 450 gold sentences. The positional backstop alone gives up 0.0009.
- **The fail-open path disappears from the default.** Today a failed judge
  leaves the set uncapped (#754); with no judge there is no failed judge.
- **A smaller consent bound** for the worst-case call count that ADR-0073
  quotes before an ingest.

Evidence limits, stated so an Accepted decision is not read as stronger than
the evidence:

- **The primary population generated the hypothesis.** Steps 1 to 3 and the
  cost measurement read the same 25 meeting-runs that suggested it. The primary
  gates are a re-analysis, not an independent test. The independent weight sits
  in the confirmation set: 6 meetings, one extractor, a lift interval about
  0.02 wide, and 69 labelled restored objects. The two populations agree in
  sign; neither alone is strong. A harness-validation peek on the primary
  population is disclosed in the results.
- **P1 is close to guaranteed by construction** (adding objects cannot lower
  coverage much). The informative gates are P2 and C1.
- **No human curator labelled anything.** P2 is source support from an LLM
  label, and "worth a page" is a weak LLM label. Whether a person wants the
  restored objects is open.

Implementation needs an OpenSpec change (not written here), for example
`drop-selector-judge-cut`, with delta specs for:

- `extraction-union-judge`: MODIFIED "Judge Selection Over a Closed Candidate
  List", "Judge Failure Fails Closed to the Backstopped Union", "Backstop Cap
  Applied Once, After Judge Selection", "Run and Judge Bookkeeping on the
  Extraction Report", "Opt-Out Configuration Flag", the two judge re-admission
  requirements, and "Extraction Exposes an Upper Bound on Its Calls"; ADDED a
  requirement for the `selector_judge` key and its default.
- `ingestion`: the batch cost estimate and the consent bound, which count the
  judge call.
- Code and docs it touches: `config.py` (key, default, validation),
  `openkos.yaml.template`, `extraction/concept.py`, `application/ingest.py`,
  `application/ingest_consent.py`, `cli/main.py`, `docs/cli.md`, plus tests and
  the eval harness self-tests. `docs/` changes only where the general shape does.

## Alternatives considered

- **Keep the cut (status quo).** Costs 0.0161 of window lift and a call per
  source, and showed no measured benefit on support, redundancy or standalone
  value. But those three studies were inconclusive, not negative, and the cut
  keeps bundles about a third smaller. It is the right answer if the owner finds
  the downstream review load above unacceptable.
- **Make the judge opt-in without changing the default.** The same
  `selector_judge` key, defaulting `true`. No user sees a change and no cost
  appears, but the default keeps paying a call and a measured lift loss for a
  benefit nobody measured. This ADR takes the key with the default flipped; the
  unchanged default is the fallback if the owner will not move it.
- **Raise the backstop** (say, from 20 to 30) **and keep the judge.** The
  backstop was not the problem: it gave up 0.0009 of lift and cut 23 of 352
  objects, and the judge's cut comes before it. Raising it changes nothing the
  judge removes.
- **Drop-only judge** (remove container, unsupported or duplicate objects, keep
  the rest; option C of `NEXT_STEP.md`) **or a judge that caps only above some
  N** (option B). Both keep one call per source and need a new prompt, a new
  pre-registration and a new spec. They remain open if the downstream review
  load proves unwelcome; nothing measured asks for them.
