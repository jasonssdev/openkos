# Curate Command Specification

## Purpose

`openkos curate` is a single interactive session that walks the five kinds
of pending human judgment — identity, structure, metadata, sensitivity,
contradictions — in the one order that is safe (ADR-0005/ADR-0011), gating
each stage's model cost and never letting a decline abort the rest.

## Requirements

### Requirement: Stage Order Is A Product Invariant

`curate` MUST run stages in exactly this order: Preconditions, Identity,
Structure, Metadata, Contradictions. A declined, unavailable, or skipped
stage MUST NOT abort or skip any later stage.

#### Scenario: Full run visits stages in order

- GIVEN a bundle with pending findings in all five categories
- WHEN `openkos curate` runs
- THEN Preconditions, Identity, Structure, Metadata, Contradictions are
  visited in that exact order

#### Scenario: Declining one stage does not abort later stages

- GIVEN the operator declines the Structure stage's cost gate
- WHEN `curate` continues
- THEN Structure records a decline notice and Metadata and Contradictions
  still run

### Requirement: Per-Stage Cost Gate

Each stage descriptor carries a `writes` capability field. Every stage
that would call an LLM MUST print its item count and resulting LLM-call
count before contacting the model, then confirm unless `--auto` is
passed. Without `--auto`, in a non-TTY session, EVERY LLM-costing stage
MUST decline before any model call — no model spend without consent. With
`--auto`, cost gates are auto-accepted; a read-only stage (Contradictions)
MUST then run and report, while a write stage (`writes: true`) MUST
decline its per-item write walk — because per-item write confirmation
cannot happen without a TTY — and MUST print a pointer to the
corresponding standalone verb (e.g. `adjudicate --apply-same
--confirm-count` for Identity).

#### Scenario: Gate states cost before any model call

- GIVEN 6 untyped edge candidates
- WHEN the Structure stage gate prints
- THEN it reads `6 untyped edge(s) -> 6 LLM call(s)` before any model
  call occurs

#### Scenario: --auto accepts every gate

- GIVEN `--auto` is passed
- WHEN `curate` reaches any stage's gate
- THEN the gate is accepted without a prompt

#### Scenario: Non-TTY without --auto declines every LLM-costing stage

- GIVEN stdin is not a TTY and `--auto` is not passed
- WHEN `curate` reaches any LLM-costing stage
- THEN that stage declines before any model call, with no exception for
  read-only stages

#### Scenario: Non-TTY with --auto runs read-only stages, declines writes

- GIVEN stdin is not a TTY and `--auto` is passed
- WHEN `curate` reaches Identity (a write stage) and then Contradictions
  (a read-only stage)
- THEN Identity declines its write walk and prints the pointer to
  `adjudicate --apply-same --confirm-count`, and Contradictions runs and
  reports its findings

### Requirement: Preconditions Stage Halts The Run

`curate` MUST probe `vectors.db` via the existing degrade seam before
Identity. A missing or empty index MUST print the consequence (starved
candidate edges) and a pointer to `openkos reindex`, then MUST exit 0
without running Identity or any later stage.

#### Scenario: Missing vectors.db halts before Identity

- GIVEN no `vectors.db` exists
- WHEN `curate` runs Preconditions
- THEN it prints the starved-candidate-edges consequence and the
  `openkos reindex` pointer, exits 0, and no later stage runs

### Requirement: Identity Stage Reuses Merge Cores

Identity MUST call `find_candidates` then `adjudicate_candidates`, then
apply each accepted pair via `_prepare_one_merge`/`_commit_one_merge`,
auto-committing per merge. N>2 groups MUST NOT be auto-merged; `curate`
MUST print the exact pairwise `openkos merge` commands per group.

#### Scenario: Accepted pair is committed per-item

- GIVEN one accepted duplicate pair
- WHEN Identity applies it
- THEN `_prepare_one_merge`/`_commit_one_merge` run and the bundle
  auto-commits before the next item

#### Scenario: N>2 group prints pairwise commands, never auto-merges

- GIVEN a candidate group of 3
- WHEN Identity reaches it
- THEN it prints the exact pairwise `openkos merge` commands and performs
  no merge

### Requirement: Structure Stage Writes Through The Relate Core

Structure MUST call `suggest_edge_types` with `on_progress`, and MUST
write each accepted suggestion through the extracted `relate` core.
Declined suggestions MUST be skipped without a write.

#### Scenario: Accepted suggestion writes via the extracted core

- GIVEN one accepted edge-type suggestion
- WHEN Structure applies it
- THEN the write occurs through the extracted `relate` core and matches
  standalone `relate`'s output

#### Scenario: Declined suggestion is skipped

- GIVEN one declined suggestion
- WHEN Structure processes it
- THEN no edge is written

### Requirement: Metadata Stage Writes Tiers, Reports Sensitivity

Metadata MUST call `suggest_volatility` with `on_progress` and write
accepted tiers through the extracted `set-volatility` core. Sensitivity
gaps surfaced in the same pass MUST be reported only; Metadata MUST NOT
write sensitivity.

#### Scenario: Accepted tier writes via the extracted core

- GIVEN one accepted volatility tier
- WHEN Metadata applies it
- THEN the write occurs through the extracted `set-volatility` core

#### Scenario: Sensitivity gap is reported, never written

- GIVEN a concept with an unset sensitivity level
- WHEN Metadata reports it
- THEN it prints the gap and names `openkos set-sensitivity`, writing
  nothing

### Requirement: Contradictions Stage Is Report-Only And Last

Contradictions MUST run last, MUST call `find_contradictions` with
`on_progress`, and MUST NOT propose or perform any write.

#### Scenario: Contradictions never writes

- GIVEN pending contradictions exist
- WHEN the Contradictions stage runs
- THEN it prints them and the run ends with no write hint

### Requirement: Resumability By Construction

`curate` MUST NOT persist any queue or checkpoint file. Interrupting
mid-run and re-invoking MUST re-derive every stage's queue from current
bundle state and MUST NOT replay an already-committed decision.

#### Scenario: Interrupted run resumes from bundle state

- GIVEN Identity committed one merge before interruption
- WHEN `curate` is re-invoked
- THEN it re-derives candidates from post-merge state and does not
  reprocess the committed pair

### Requirement: Sensitivity Threading Is Fail-Closed

`--include-confidential` and `--include-deprecated` MUST be forwarded to
every stage's underlying call. Omitting them MUST exclude confidential
and deprecated content by default.

#### Scenario: Confidential content excluded by default

- GIVEN confidential concepts exist and `--include-confidential` is
  omitted
- WHEN any stage runs
- THEN confidential concepts are excluded from that stage's input

### Requirement: Output Discipline And Summary

`curate` MUST honor `NO_COLOR` and non-TTY output using only the existing
`observability` progress helpers, and MUST print a summary line naming
each stage's outcome at the end of the run.

#### Scenario: Piped output stays clean

- GIVEN stdout is piped and `NO_COLOR=1`
- WHEN `curate` runs
- THEN no ANSI color codes or interactive prompts appear in the output

#### Scenario: Summary line names every stage outcome

- GIVEN a completed run
- WHEN `curate` finishes
- THEN one summary line lists each of the five stages with its outcome

### Requirement: Exit Codes Match Existing Verb Conventions

`curate` MUST exit 0 on a completed or declined run (including a
Preconditions halt), 1 on failure, 2 on usage error, and 3 on a drift
refusal, consistent with other verbs (#319).

#### Scenario: Declined stages still exit zero

- GIVEN every stage is declined
- WHEN `curate` finishes
- THEN it exits 0

#### Scenario: Drift refusal exits three

- GIVEN a write stage detects a drifted target during its write walk
- WHEN `curate` refuses that write
- THEN it exits 3

### Requirement: Extracted Cores Preserve Standalone Behavior

The `relate` and `set-volatility` extraction into pure Phase-A/Phase-B
cores MUST NOT change either verb's observable output; their existing
test suites MUST pass unedited.

#### Scenario: Standalone relate output is unchanged

- GIVEN the same inputs as before extraction
- WHEN `openkos relate` runs standalone
- THEN its output is byte-identical to pre-extraction behavior

### Requirement: Slice Boundary

Slice 1 MUST declare all five stages in the runtime `_STAGES` sequence and
MUST implement the stage framework, cost-gate/decline machinery, and
Preconditions + Identity fully; it MUST be independently shippable.
Structure, Metadata, and Contradictions MUST appear in `_STAGES` at
runtime but MUST be skipped without prompting or any model call, and MUST
appear in the end-of-run summary labeled "not yet available in this
version". Slice 2 MUST replace that label with full behavior for
Structure, Metadata, and Contradictions, plus the two core extractions.

#### Scenario: Slice 1 declares five stages, three not-yet-available

- GIVEN only slice 1 is merged
- WHEN `curate` runs
- THEN Preconditions and Identity execute fully, Structure, Metadata, and
  Contradictions are skipped without any prompt or model call, and the
  summary lists all five stages with the latter three marked "not yet
  available in this version"
</content>
