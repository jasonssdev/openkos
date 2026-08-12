# Delta for Curate Command

## MODIFIED Requirements

### Requirement: Contradictions Stage Is Report-Only And Last

Contradictions MUST run last, MUST call `find_contradictions` with
`on_progress`, and MUST NOT propose or perform any write to the knowledge
bundle. Persisting the verdicts the stage already computed to the
pending-work store (`.openkos/`) is NOT such a write: it records the
stage's own completed output, proposes nothing to the operator, changes no
file under `bundle/`, and requires no prompt.
(Previously: "MUST NOT propose or perform any write", unqualified — did not
distinguish a bundle write from recording computed output.)

#### Scenario: Contradictions never writes to the bundle

- GIVEN pending contradictions exist
- WHEN the Contradictions stage runs
- THEN it prints them, persists each verdict to the pending-work store, and
  the run ends with no write to `bundle/` and no write hint

#### Scenario: Persisting a finding is not a bundle write

- GIVEN the Contradictions stage produces one `CONTRADICTS` verdict
- WHEN the stage records that verdict under `.openkos/`
- THEN no file under `bundle/` changes and no `[y/N]` prompt is shown

### Requirement: Resumability By Construction

`curate` MUST NOT persist any queue or checkpoint file. Interrupting
mid-run and re-invoking MUST re-derive every stage's queue from current
bundle state and MUST NOT replay an already-committed decision. Persisting
contradiction findings and operator decisions is NOT persisting a queue or
checkpoint: each stage's candidate queue is still re-derived from current
bundle state on every run, and no run-scoped progress marker is written —
the pending-work store records completed output and operator judgment,
never "where the last run left off".
(Previously: silent on findings/decisions persistence — no distinction
existed between run-scoped progress and durable output.)

#### Scenario: Interrupted run resumes from bundle state

- GIVEN Identity committed one merge before interruption
- WHEN `curate` is re-invoked
- THEN it re-derives candidates from post-merge state and does not
  reprocess the committed pair

#### Scenario: A persisted finding is not a resume checkpoint

- GIVEN a prior run persisted one contradiction finding
- WHEN `curate` is re-invoked
- THEN Contradictions re-derives its candidate pairs from current bundle
  state, independent of whether a finding for a pair already exists
