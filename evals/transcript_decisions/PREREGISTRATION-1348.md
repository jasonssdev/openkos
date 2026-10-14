# Pre-registration: #1348, the transcript decision pass

**Status: REGISTERED 2026-10-13, approved by the maintainer before the
registered run.** The rules below are fixed for that run. A change after this
point is a new registration, not an edit to this one.

History, disclosed: this file was drafted before any live run. One smoke run
per arm and a 3-run prompt A/B on the same development series were made
before registering (see "Development runs before registration"); the matching
rule, the shared-evidence cap and the prompt revision were all decided after
seeing them.

## Question

On AMI meetings, does the transcript decision pass recover the decisions people
hand-annotated better than today's extraction does, with acceptable precision,
volume, failure rate and cost?

## Data

AMI development series ONLY: ES2015, IS1004, TS3009 (the thesis's ADR-0005
split; `scoring.DEV_SERIES`, enforced in code). The thesis's evaluation series
are out of bounds here. Gold: the thesis's adjudicated decision CSVs
(`final_status` in decision / no_decision / sin_soporte / compuesta / descartar), passed by
flag and not vendored.

Known limits: IS1004 and TS3009 are unannotated, so the registered result rests
on ES2015 alone (four meetings, 34 adjudicated rows, 27 of them `decision`,
adjudication complete, no blank rows). That is thin: it can reject a bad pass
and cannot certify a good one, and a result on it must say so. The prompt was
revised on this same series, so a pass result here is optimistic by
construction.

## Arms

- `baseline`: `extract_concept_union` with `transcript_decisions` off (today's
  default), scored through each Decision's body. It costs the full extraction
  per meeting per run (several calls per window).
- `pass`: the pass alone, scored through its evidence line.

The end-to-end arm (key on) is NOT a separate arm: its pass Decisions are
exactly the `pass` arm's, appended after the main pass's, so `pass` plus the
failure count determine it.

Frozen configuration: model `qwen3:8b`; the production generation ceiling and
context window (the harness's constants); the decision pass prompt as merged in
#1358 (prompt hash pin `6d3962b0dd75650c` in `tests/unit/llm/test_prompts.py`);
`MIN_VALUE_WORDS = 3`; `MAX_PASS_DECISIONS = 25` per source. **15 runs per
arm** (the floor on this repo's harnesses, where one arm spanned 0.25 against
itself at 5).

## Metrics

As defined in the README: recall, the produced split (matched_decision,
matched_non_decision, matched_compuesta, matched_descartar, unadjudicated,
unannotated), precision-on-annotated, duplicate titles across meetings,
windows, calls, failures, wall time; mean and min-max over runs, always
`n of TOTAL`.

## Matching rule (approved)

A produced stream matches a gold act when their longest common contiguous token
run is at least 4 tokens (`MIN_MATCH_TOKENS`) AND covers at least 60% of the
shorter side's tokens (`MIN_MATCH_COVERAGE`). Containment is the 100% case. It
applies identically to BOTH arms.

Disclosed: this replaced a containment-only rule AFTER one smoke run per arm on
ES2015 (pass recall 0.185 against baseline 0.222 under the old rule). The smoke
run showed real hits missed because AMI dialogue acts and rendered transcript
turns are segmented differently. Example, paraphrased: a gold act ending
"... decided on kinetic right" and a produced evidence line beginning "we've
decided on kinetic right okay ..." share a five-token run, yet neither contains
the other. The 60% value is a judgement made after seeing that run, not derived
from data.

## Recall under shared evidence (approved)

An adjudicated `compuesta` row may be split into child rows (`<parent>-1`, ...)
that copy the parent's evidence. Matching is by evidence, so one produced
Decision quoting that line would match every child. Rule: `decision` rows of
one meeting whose normalized evidence acts are identical (by acts, not by id
prefix) form a group; the group's recall credit is min(number of `decision`
rows in the group, number of DISTINCT Decisions produced in that run for that
meeting that matched the group); recall is the sum of credits over the
`decision`-row count. Groups of two or more are reported as
`shared_evidence_groups`. `descartar` rows are excluded from the recall
denominator, and a Decision matching only such rows is `matched_descartar`,
excluded from precision.

## Decision rule (approved)

Adopt the pass, meaning flip the `transcript_decisions` default to on, only if
ALL hold:

- **R** recall: pass mean recall at least baseline mean recall + 0.25, AND that
  gap exceeds the larger of the two arms' own min-max spreads. (With 27 gold
  rows one row moves recall 0.037, so a gap under the spread is noise.)
- **P** precision-on-annotated: pass mean at least 0.60, and no run below 0.50.
  The gold is incomplete, so some real decisions land in `unannotated`; what
  the gold CAN call wrong is `no_decision`/`sin_soporte`.
- **V** volume: pass Decisions per run at most 3 times the series' `decision`
  rows (81 for ES2015), mean and every run. A coarse ceiling: `unannotated`
  cannot be judged item by item against an incomplete gold, and without this
  bar nothing stops a pass that floods the bundle.
- **F** failures: a `failure` set in at most 1 of 20 meeting-runs, and zero
  uncaught errors.
- **D** duplicate titles: Decisions that would merge at ingest at most 20% of
  the produced.
- **C** cost: calls and wall time are reported; the pass adds one call per
  window by construction, which is what the cost gate assumes.

Anything short of that records the result and ships nothing. `unannotated` is
reported and never gates.

## Development runs before registration

All on ES2015, qwen3:8b, not part of the registered result:

| Run | Rule | Recall | Precision | Decisions/run |
|---|---|---|---|---|
| baseline, 1 run | containment | 0.222 | 1.000 | 5 |
| baseline, 1 run | overlap | 0.074 | 1.000 | 7 |
| pass, original prompt, 3 runs | overlap | 0.284 (0.259-0.333) | 1.000 | 40.7 |
| pass, revised prompt (#1358), 3 runs | overlap | 0.407 (0.370-0.444) | 0.866 | 67.3 |

## Not in this registration

Tuning `MAX_PASS_DECISIONS` or `MIN_VALUE_WORDS` (unmeasured placeholders),
further prompt variants (each needs its own registration), and any claim on the
evaluation series.
