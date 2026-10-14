# Pre-registration (DRAFT): #1348, the transcript decision pass

**Status: DRAFT. Every number marked PROPOSED is to be fixed by the maintainer
before the first treatment run.** Nothing below is agreed until then. Written
before any live run of `run_transcript_decisions.py`.

## Question

On AMI meetings, does the transcript decision pass recover the decisions people
hand-annotated better than today's extraction does, with acceptable precision,
failure rate and cost?

## Data

AMI development series ONLY: ES2015, IS1004, TS3009 (the thesis's ADR-0005
split; `scoring.DEV_SERIES`, enforced in code). The thesis's evaluation series
are out of bounds here. Gold: the thesis's adjudicated decision CSVs
(`final_status` in decision / no_decision / sin_soporte / compuesta / descartar), passed by
flag and not vendored.

Known limits, stated now: IS1004 and TS3009 are unannotated at the time of
writing, so the registered result rests on ES2015 (four meetings, 29 gold rows,
adjudication unfinished). That is thin: it can reject a bad pass and cannot
certify a good one, and a result on it must say so. The registered run waits
for adjudication to finish (a `PARTIAL` run is never the registered result).

## Arms

- `baseline`: `extract_concept_union` with `transcript_decisions` off (today's
  default), scored through each Decision's body. Implemented, because "better
  than main" needs a number for main under the same matching. It costs the full
  extraction per meeting per run (several calls per window), so it is run once
  at the registered `--runs`, not iterated on.
- `pass`: the pass alone, scored through its evidence line.

The end-to-end arm (key on: the pass replaces the main pass's Decisions) is NOT
a separate arm: its Decisions are exactly the `pass` arm's whenever the pass
yields at least one, and the main pass's otherwise, so `pass` plus the failure
count determine it.

Model `qwen3:8b`, the production generation ceiling and context window (the
harness's constants). Runs: 5 for development iterations; the registered
comparison uses 15 per arm (the floor on this repo's harnesses, where one arm
spanned 0.25 against itself at 5).

## Metrics

As defined in the README: recall, the produced split (matched_decision,
matched_non_decision, matched_compuesta, matched_descartar, unadjudicated,
unannotated),
precision-on-annotated, duplicate titles across meetings, windows, calls,
failures, wall time; mean and min-max over runs, always `n of TOTAL`.

## Recall under shared evidence -- PROPOSED, awaiting maintainer approval

An adjudicated `compuesta` row may be split into child rows (`<parent>-1`, ...)
that copy the parent's evidence. Matching is by evidence, so a single produced
Decision quoting that line matches every child and would recall all of them,
inflating recall. PROPOSED rule: `decision` rows of one meeting whose normalized
evidence acts are identical (by acts, not by id prefix) form a group; the group's
recall credit is min(number of `decision` rows in the group, number of DISTINCT
Decisions produced in that run for that meeting that matched the group); recall
is the sum of credits over the `decision`-row count. Singleton rows are
unchanged; groups of two or more are reported as `shared_evidence_groups`.
Companion definitions: `descartar` rows are excluded from the recall
denominator and a Decision matching only such rows is `matched_descartar`,
excluded from precision. Not in force until the maintainer approves it.

## Decision rule -- PROPOSED, to be fixed by the maintainer before the first treatment run

Adopt the pass (consider flipping the default in a later PR) only if ALL hold:

- **R** recall: pass mean recall at least baseline mean recall + 0.25, AND that
  gap exceeds the larger of the two arms' own min-max spreads. (The shape of the
  bar #1231 used; with 13 to 29 gold rows one row moves recall 0.03 to 0.08, so a
  gap under the spread is noise.)
- **P** precision-on-annotated: pass mean at least 0.60, and no run below 0.50.
  Reasoning: the gold is incomplete, so some real decisions land in
  `unannotated`; what the gold CAN call wrong is `no_decision`/`sin_soporte`.
  Chosen against a coin-flip, not derived from data. REVISE once the first
  development run shows where the pass sits.
- **F** failures: no meeting-run with `failure` set in more than 1 of 20
  meeting-runs, and zero uncaught errors.
- **D** duplicate titles: reported, gating only at the extreme: Decisions that
  would merge at ingest at most 20% of the produced. It is a proxy for the
  attach-merge limit, which has its own follow-up.
- **C** cost: calls and wall time are reported; the pass must add no more than
  one call per window (it is one by construction), which is what the cost gate
  assumes.

Anything short of that records the result and ships nothing. `unannotated` is
reported and never gates.

## Matching-rule change (PROPOSED, pending maintainer approval of this registration)

The matching rule was changed AFTER inspecting one smoke run per arm on the
DEVELOPMENT series ES2015, so it is disclosed here rather than presented as
fixed in advance. Under the original rule (one token sequence must contain the
other, shorter side at least 4 tokens) that smoke run scored pass recall 0.185
against baseline recall 0.222.

Why: the smoke run showed real hits missed because AMI dialogue acts and
rendered transcript turns are segmented differently. Example, paraphrased: a
gold act ending "... decided on kinetic right" and a produced evidence line
beginning "we've decided on kinetic right okay ..." share a five-token run, yet
neither contains the other.

New rule: a produced stream matches a gold act when their longest common
contiguous token run is at least 4 tokens (`MIN_MATCH_TOKENS`, unchanged) AND
covers at least 60% of the shorter side's tokens (`MIN_MATCH_COVERAGE`).
Containment stays a special case. Tokenisation, the `unrecallable` definition
(no act of 4 or more tokens) and the shared-evidence recall cap are unchanged.
It applies identically to BOTH arms. The 60% value is a judgement made after
seeing the smoke run, not derived from data. Until the maintainer approves it,
any number scored under this rule is a development number.

## Not in this registration

Tuning `MAX_PASS_DECISIONS` (an unmeasured placeholder of 25; the produced
counts here are the evidence for revising it), prompt variants (each needs its
own registration), and any claim on the evaluation series.
