# Delta for Curate Command

> **Conditional delta.** Every requirement below describes Part B of
> `auto-merge-safe-class` and is built ONLY if the pre-registered decision
> rule in `design.md` passes (tasks Phase 2). On FAIL this file is deleted
> from the change before archive and `curate-command` is untouched.

## MODIFIED Requirements

### Requirement: Per-Stage Accept-All Is Opt-In And Never Covers Identity

`curate` MUST accept a `--accept STAGES` option taking a comma-separated,
case-insensitive list of stage names whose per-item write prompts are
answered yes without asking. Only stages marked auto-acceptable may be
named; today that is Structure and Metadata.

`--accept identity` MUST be refused with exit 2, and so MUST any name that
is not a stage at all. Both refusals MUST run BEFORE the workspace gate, so
a typo is reported as itself rather than as a missing workspace, and the
refusal MUST name the acceptable stages. Identity is excluded because a
merge absorbs one concept into another and DELETES the absorbed file; no
flag and no config value may apply one unreviewed, with exactly one
exception: `--auto-merge` (requirement "Opt-In Automatic Merge Of The
Measured Class") applies the measured class without per-item consent,
reviewable after the fact (ADR-0034). No config value reaches it, and it
widens nothing outside that class.

Naming a stage in `--accept` IS per-item write consent for that stage, so
an accepted stage MUST also pass the non-TTY write refusal — `curate --auto
--accept structure` on a pipe writes, matching `suggest-relations --auto`.
Identity MUST remain subject to that refusal on every path except the
automatic merges `--auto-merge` applies.
(Previously: no flag and no config value could apply any Identity merge
without per-item consent, and Identity was subject to the non-TTY refusal
on every path.)

#### Scenario: An accepted stage applies without prompting

- GIVEN a Structure queue with two valid suggestions
- WHEN `curate --accept structure` runs
- THEN both suggestions are written, no per-item prompt is printed, and the
  summary reports `applied 2, skipped 0`

#### Scenario: Identity cannot be accepted in bulk

- GIVEN any workspace
- WHEN `curate --accept identity` runs
- THEN the exit code is 2, nothing is written, and stderr names the
  acceptable stages

#### Scenario: An unknown stage name is a usage error

- GIVEN any workspace
- WHEN `curate --accept strcture` runs
- THEN the exit code is 2 and stderr names the offending value

#### Scenario: `--auto-merge` does not make `--accept identity` legal

- GIVEN any workspace
- WHEN `curate --auto-merge --accept identity` runs
- THEN the exit code is 2 and nothing is written

## ADDED Requirements

### Requirement: Opt-In Automatic Merge Of The Measured Class

`curate` MUST accept a `--auto-merge` flag, default off. Without it,
Identity MUST behave exactly as before this change, byte for byte on
stdout and stderr. With it, Identity MUST first apply every pair the
eligibility requirement admits, without a per-item prompt and on a TTY or a
pipe alike, then route every remaining pair through the existing per-item
walk (TTY) or the existing non-TTY refusal and hint.

`--auto-merge` MUST be refused with exit 2, before the workspace gate, when
combined with `--reconcile`. No `openkos.yaml` key MUST enable it, and
`review: false` MUST NOT enable it.

When the resolved adjudication task model is not in
`AUTO_MERGE_MEASURED_MODELS`, `--auto-merge` MUST apply no automatic merge,
MUST print one stderr notice naming the model and the measured set, and
Identity MUST continue exactly as without the flag.

#### Scenario: Off by default

- GIVEN a workspace whose Identity queue holds one eligible pair
- WHEN `curate` runs without `--auto-merge`
- THEN no merge is applied without a prompt, and output matches the
  pre-change behavior

#### Scenario: An eligible pair merges on a pipe

- GIVEN one eligible pair and the measured adjudication model
- WHEN `curate --auto --auto-merge` runs with stdin not a TTY
- THEN the pair is merged, and no per-item prompt is printed

#### Scenario: An unmeasured model applies nothing automatically

- GIVEN one pair that would otherwise be eligible and an adjudication task
  model outside `AUTO_MERGE_MEASURED_MODELS`
- WHEN `curate --auto-merge` runs
- THEN nothing is merged automatically, stderr names the model and the
  measured set, and the pair takes the per-item path

#### Scenario: `--auto-merge --reconcile` is a usage error

- GIVEN any workspace
- WHEN `curate --auto-merge --reconcile` runs
- THEN the exit code is 2 and nothing is written

### Requirement: Automatic Merge Eligibility Is Fail-Closed

A pair MUST be merged automatically only if ALL of the following hold,
evaluated in this order; the first that fails MUST be recorded as the
pair's deferral reason and the pair MUST take the per-item path:

1. its final verdict (after the self-refutation withdrawal) is `SAME`;
2. the group has exactly two members;
3. `cross_type_concern` returns `None` for the pair;
4. its confidence is at or above `AUTO_MERGE_THRESHOLD`;
5. the model that produced the verdict is in `AUTO_MERGE_MEASURED_MODELS`
   (a served verdict with no recorded model fails this check);
6. neither member was already survivor or absorbed in an automatic merge
   earlier in the same run;
7. the prepared merge is not refused by the stacked-body guardrail.

The survivor MUST be chosen by `ordered_merge_pair` and PINNED for the
merge, as `adjudicate --apply-same` pins it. `AUTO_MERGE_THRESHOLD` and
`AUTO_MERGE_MEASURED_MODELS` MUST be code constants set from the recorded
harness verdict, never user configuration. The confidential gate MUST
apply exactly as in Identity without the flag.

#### Scenario: A cross-type pair is never automatic

- GIVEN a `SAME` pair whose members declare different OKF types
- WHEN `curate --auto-merge` runs
- THEN the pair is not merged automatically and takes the per-item path

#### Scenario: A verdict below the threshold is deferred

- GIVEN a `SAME` pair of one type at confidence below
  `AUTO_MERGE_THRESHOLD`
- WHEN `curate --auto-merge` runs
- THEN the pair is not merged automatically

#### Scenario: A served verdict with no recorded model is deferred

- GIVEN a persisted `SAME` verdict written before the model column existed
- WHEN `curate --auto-merge` serves it
- THEN the pair is not merged automatically

#### Scenario: A concept joins at most one automatic merge per run

- GIVEN eligible pairs (A, B) and (B, C) in one run
- WHEN `curate --auto-merge` runs
- THEN exactly one of them is merged automatically and the other is
  deferred with the reason that a member was already merged this run

### Requirement: Automatic Merges Are Mechanical

An automatic merge MUST NOT run the merged-body reconciliation pass,
whatever the stacked share, so its only writes are the ones a manual
`merge --no-reconcile` of the same pinned pair makes, and `unmerge` of it
MUST restore the same state `unmerge` restores after that manual merge.

#### Scenario: An automatic merge round-trips through unmerge

- GIVEN a workspace snapshot, then one automatic merge of (S, A)
- WHEN `openkos unmerge S A` runs
- THEN every concept file, `index.md`, and the ledger sidecar are byte-for-
  byte identical to the snapshot, and `log.md` differs only by the run's
  `**Auto-merge**` bullet and the unmerge audit line

### Requirement: Automatic Merges Are Disclosed

After the automatic pass, `curate` MUST print to stderr one summary line
stating how many pairs were merged automatically, the threshold, and the
model, followed by one line per merge naming survivor, absorbed,
confidence, and the exact `openkos unmerge <survivor> <absorbed>` command
that reverses it. When zero pairs were merged automatically, the summary
line MUST still print, stating zero. The run summary MUST count automatic
merges separately from per-item accepted merges.

#### Scenario: Two automatic merges are each disclosed

- GIVEN two eligible, disjoint pairs
- WHEN `curate --auto-merge` runs
- THEN stderr carries one summary line stating 2, then two lines each
  naming survivor, absorbed, confidence and its `openkos unmerge` command

#### Scenario: A failed automatic pass discloses what it already applied

- GIVEN three eligible pairs where the second merge's write fails
- WHEN `curate --auto-merge` runs
- THEN the first merge is disclosed, committed, and listed in the run's
  `**Auto-merge**` bullet, the failure is reported, and the third pair is
  not attempted
