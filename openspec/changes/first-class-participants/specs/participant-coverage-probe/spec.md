# Participant Coverage Probe Specification

## Purpose

Establish a measure-first gate for `Person`/`Organization` extraction
coverage on the AMI corpus, extending the existing
`run_type_coverage.py` harness rather than building a new one. This probe
supplies the evidence that decides whether the gated phase-2 dedicated
capture pass is opened.

## Requirements

### Requirement: Per-Type Participant Recall Measurement

The probe MUST run the real `extract_concept`/`extract_concept_union` seam
against AMI's `PERSON` and `ORGANIZATION` named-entity ground truth and
report recall for each type, in the same explained-vs-unexplained-absence
shape already used for other types.

#### Scenario: Recall is reported per type

- GIVEN an AMI run against a transcript with annotated `PERSON` and
  `ORGANIZATION` ground truth
- WHEN the probe scores the run
- THEN the report includes a distinct recall figure for `Person` and for
  `Organization`, not a combined or omitted figure

### Requirement: Precision-Side Reporting Alongside Recall

The probe MUST report, alongside recall, the count of admitted
`Person`/`Organization` objects that are not explained by any ground-truth
mention for that source. Recall alone MUST NOT be the sole reported metric.

#### Scenario: Unexplained participant objects are counted

- GIVEN a run that admits three `Person` objects, one of which matches no
  ground-truth mention
- WHEN the probe scores the run
- THEN the report shows recall against ground truth AND a count of one
  unexplained/unmatched `Person` object

### Requirement: Recorded Baseline for Comparison

The probe MUST record a baseline measurement of `Person`/`Organization`
recall and precision-side counts that later runs can be compared against,
following the same recording convention `run_type_coverage.py` already uses
for other types.

#### Scenario: Baseline is available after a probe run

- GIVEN a probe run completes across the configured `--runs` count
- WHEN the results are recorded
- THEN a `Person`/`Organization` baseline entry is present and readable by
  a subsequent probe run for comparison

### Requirement: Probe Result Gates Phase-2 Scoped Pass

A phase-2 dedicated, transcript-scoped capture pass MUST NOT be built or
enabled based on assumption alone. IF the probe's baseline shows
`Person`/`Organization` generation remains zero or near-zero after the
phase-1a exemption-set generalization ships, THEN opening phase-2 work is
justified by that measurement. IF the baseline shows non-zero recall
improvement from phase-1a alone, phase-2 MUST NOT be opened on this
change's evidence.

#### Scenario: Persistent zero generation justifies opening phase 2

- GIVEN a post-phase-1a probe baseline showing zero `Person`/`Organization`
  candidates generated across all runs
- WHEN the phase-2 gate decision is made
- THEN the measurement is recorded as the justification for proposing
  phase-2 work

#### Scenario: Non-zero recall does not justify phase 2

- GIVEN a post-phase-1a probe baseline showing non-zero
  `Person`/`Organization` recall
- WHEN the phase-2 gate decision is made
- THEN phase-2 is not opened on the strength of this change's measurement
  alone

### Requirement: No Per-Type Sensitivity Behavior in Probe Scope

The probe MUST NOT introduce or measure any per-type default-sensitivity
behavior. Sensitivity remains a single workspace-level setting, unaffected
by object type.

#### Scenario: Probe reports coverage without sensitivity branching

- GIVEN a probe run over sources with the workspace default sensitivity
  applied uniformly
- WHEN the probe reports results
- THEN no per-type sensitivity value, override, or branch appears in the
  report or in the extraction path it measures
