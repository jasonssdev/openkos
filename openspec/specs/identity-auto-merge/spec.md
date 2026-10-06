# Identity Auto-Merge Specification

## Purpose

Defines the one class of Identity duplicates that `openkos curate` may merge
without a per-item answer, and the conditions, evidence, records and
reversibility that make that safe (ADR-0034, ADR-0044, ADR-0049; measured by
`evals/auto_merge/PREREGISTRATION-1298.md`). The class is the structural
base/`-N` pair, judged `same` at high confidence by the measured model. It is
opt-in per run, never default, never in the daemon, and every merge it makes
is reversible with `openkos unmerge <survivor>`. The same class defines the
set that Identity's "accept recommended" answer may offer. Everything outside
the class keeps its per-item prompt.

## Requirements

### Requirement: The Structural Class Predicate

A candidate group MUST be in the class if and only if all of the following
hold: its tier is HIGH; it has exactly two members; both members have the
same single concept type; that type is not one of the attach-excluded types
(`ATTACH_EXCLUDED_TYPES`, today Event and Person); neither member is an
imported concept (one adopted by `openkos import`); and one member's Concept
ID is the other's ID plus a `-N` ingest-time disambiguator (digits only, same
directory, the `is_suffix_family` relation, in either order). The predicate
MUST be structural only: it MUST NOT read a verdict or a confidence. Whether
a member is imported is a property of the concept's own recorded identity,
and the predicate MUST NOT depend on a model. Production and the
`evals/auto_merge` harness MUST share the one predicate; no second copy may
exist. A group the predicate refuses for an imported member MUST remain
visible to `duplicates`, `adjudicate`, `merge` and curate Identity's
per-item prompt, but, being out of class, it is neither merged
automatically nor offered through accept-recommended.

#### Scenario: A base/`-N` pair of one allowed type is in class

- GIVEN a HIGH-tier two-member group `concepts/foo` and `concepts/foo-2`,
  both of type `Concept`
- WHEN the predicate is evaluated
- THEN the group is in class

#### Scenario: Each structural disqualifier takes a group out of class

- GIVEN a group that is otherwise in class but differs in exactly one
  respect: a tier below HIGH; three members; two different concept types; an
  attach-excluded type (Event, then Person); or ids that are a `-a`/`-b`,
  `-1`/`-2` or unrelated-slug shape instead of base/`-N`
- WHEN the predicate is evaluated for each variant
- THEN every variant is out of class

#### Scenario: A group with an imported member is out of class

- GIVEN a HIGH-tier base/`-N` pair of one allowed type in which both members
  are imported concepts (a base/`-N` pair shares one directory, so a pair
  with exactly one imported member is never in that shape; the predicate
  still refuses on either member alone)
- WHEN the predicate is evaluated
- THEN the pair is out of class, and the same shape between two local
  concepts stays in class

#### Scenario: An imported group is still offered to a human

- GIVEN a HIGH-tier pair with an imported member that the predicate refuses
- WHEN curate Identity runs interactively
- THEN the group is prompted individually, and is not listed in any
  accept-recommended offer

#### Scenario: The predicate reads no verdict and no model

- GIVEN an in-class group
- WHEN the predicate is evaluated
- THEN it returns in class without a verdict, a confidence or a model call

#### Scenario: The eval harness uses the production predicate

- GIVEN the `evals/auto_merge` structural-class harness
- WHEN its `--self-test` runs
- THEN it exercises the production predicate and passes, and the harness
  defines no predicate of its own

### Requirement: Measured Constants Are Code Constants

The measured configuration MUST be fixed constants in the engine, not
configuration read from the workspace: the model tag `gemma4:26b-a4b`, that
model's full digest as recorded in the committed measurement run files, the
confidence threshold `t*` = 0.90, the measured prompt and rubric identity, and
the measured `context_window` 12288 and `max_generation_tokens` 8192. No key
in `openkos.yaml`, environment variable or flag MUST change any of them.

#### Scenario: The threshold is not configurable

- GIVEN a workspace whose `openkos.yaml` carries any key that names a
  confidence threshold for auto-merge
- WHEN `curate --auto-merge` runs
- THEN the threshold applied is 0.90 and the key has no effect

### Requirement: Run Eligibility Is Checked Once And Every Failure Is Reported

Before the automatic pass makes any judgment, the engine MUST check run
eligibility once. The class is ineligible for the run when any of these
holds: the model resolved for Identity adjudication differs from the measured
tag; the installed model's digest differs from the measured digest; the
backend reports no digest for the model (digest unknown); the engine's
current prompt and rubric identity differs from the measured identity; the
workspace overrides `context_window` away from 12288; or the workspace
overrides `max_generation_tokens` away from 8192.

An ineligible run MUST merge nothing automatically, MUST NOT make the pass's
fresh in-class judgments, and MUST print on a visible stream a line naming
every failed check (model tag, digest, rubric identity, `context_window`,
`max_generation_tokens`) with the expected and observed values where known.
It MUST NOT be a silent no-op. Identity MUST then proceed exactly as it does
without the flag. The same eligibility applies to offering accept-recommended,
which reports nothing when the flag was not passed (see `curate-command`).

#### Scenario: A different adjudication model makes the run ineligible

- GIVEN the Identity stage resolves to a model whose tag is not
  `gemma4:26b-a4b`
- WHEN `curate --auto-merge` reaches Identity
- THEN no group is auto-merged, no fresh pass judgment is made, and a line
  names the model tag as the failed check with the observed tag

#### Scenario: A different digest makes the run ineligible

- GIVEN the model tag matches but the installed digest differs from the
  measured digest
- WHEN `curate --auto-merge` reaches Identity
- THEN nothing is auto-merged and a line names the digest as the failed check

#### Scenario: An unknown digest makes the run ineligible

- GIVEN the model tag matches but the backend reports no digest (for example
  an `openai-compatible` backend)
- WHEN `curate --auto-merge` reaches Identity
- THEN nothing is auto-merged and a line states that the digest is unknown

#### Scenario: A different rubric identity makes the run ineligible

- GIVEN the engine's current prompt and rubric identity differs from the
  measured one
- WHEN `curate --auto-merge` reaches Identity
- THEN nothing is auto-merged and a line names the rubric identity as the
  failed check

#### Scenario: A settings override makes the run ineligible

- GIVEN `openkos.yaml` overrides `context_window` or `max_generation_tokens`
  to a value other than 12288 or 8192
- WHEN `curate --auto-merge` reaches Identity
- THEN nothing is auto-merged and a line names the overridden setting, its
  configured value and the measured value

#### Scenario: Several failures are all named

- GIVEN a run where the model tag differs and `context_window` is overridden
- WHEN `curate --auto-merge` reaches Identity
- THEN one report names both failed checks

#### Scenario: An ineligible run falls back to today's Identity

- GIVEN an ineligible run on a TTY with one in-class `same` group
- WHEN Identity proceeds
- THEN the group is offered through the normal per-item prompt and is merged
  only if the operator answers `y`

#### Scenario: An eligible run says nothing about ineligibility

- GIVEN a run whose model, digest, rubric identity and settings all match
- WHEN `curate --auto-merge` reaches Identity
- THEN no ineligibility line is printed

### Requirement: Per-Group Eligibility

A group MUST be merged automatically only when all of these hold: the run is
eligible; the group is in the class; its `cross_type_concern` is `None`; no
member is confidential or LLM-blocked; the stacked-body guardrail does not
refuse its merge plan; its verdict is `same` with confidence greater than or
equal to 0.90, from a judgment made in this run by the measured model; and its
survivor has not already been auto-merged in this run. A group that fails any
condition MUST NOT be merged automatically and MUST remain available to the
normal per-item flow (or be left unmerged on a non-TTY). Out-of-class groups
(including groups of three or more) MUST never be auto-merged.

#### Scenario: An eligible group is merged without a prompt

- GIVEN an eligible run and an in-class group judged `same` at confidence 0.92
- WHEN the automatic pass runs
- THEN the group is merged and no prompt is shown for it

#### Scenario: Confidence exactly at the threshold merges

- GIVEN an in-class group judged `same` at confidence 0.90
- WHEN the automatic pass runs
- THEN the group is merged

#### Scenario: Confidence below the threshold does not merge

- GIVEN an in-class group judged `same` at confidence 0.89
- WHEN the automatic pass runs
- THEN the group is not merged and remains available to the per-item flow

#### Scenario: A non-`same` verdict does not merge

- GIVEN an in-class group judged `distinct` or `uncertain` at confidence 0.99
- WHEN the automatic pass runs
- THEN the group is not merged

#### Scenario: A confidential member blocks the merge

- GIVEN an in-class group in which one member's sensitivity is `confidential`
- WHEN the automatic pass runs
- THEN the group is not merged and the model is not shown that member's body

#### Scenario: An LLM-blocked member blocks the merge

- GIVEN an in-class group in which one member is blocked from LLM use by the
  sensitivity rules
- WHEN the automatic pass runs
- THEN the group is not merged

#### Scenario: A cross-type concern blocks the merge

- GIVEN a group whose `cross_type_concern` is not `None`
- WHEN the automatic pass runs
- THEN the group is not merged

#### Scenario: The guardrail refusal blocks the merge

- GIVEN an in-class `same` group at confidence 0.95 whose merge plan the
  stacked-body guardrail refuses
- WHEN the automatic pass runs
- THEN the group is not merged, nothing is written for it, and the report
  names the guardrail as the reason

#### Scenario: A group of three is never auto-merged

- GIVEN a three-member candidate group judged `same` at confidence 0.99
- WHEN the automatic pass runs
- THEN the group is not merged and Identity prints the pairwise
  `openkos merge` commands for it as today

### Requirement: One Automatic Merge Per Survivor Per Run

Within one run, at most one automatic merge MUST be applied onto any given
survivor. When two candidate groups name the same survivor (for example
`foo` with `foo-2` and `foo` with `foo-3`), the first in candidate order MUST
be merged and the second MUST NOT be auto-merged in that run; it MUST remain
available to the per-item flow and MUST be reachable by a later run.

#### Scenario: Two candidates for one survivor merge once

- GIVEN eligible in-class `same` groups (`foo`, `foo-2`) and (`foo`, `foo-3`)
- WHEN the automatic pass runs
- THEN exactly one of them is merged, the other is not merged in this run,
  and the report says that a merge onto that survivor had already been
  applied

#### Scenario: The second candidate is merged by a later run

- GIVEN the previous scenario has completed and the second pair is still
  present and eligible
- WHEN `curate --auto --auto-merge` runs again
- THEN the second pair is merged

### Requirement: Verdicts Are Judged Fresh In The Run

The automatic pass MUST act only on a verdict judged in this run by the
measured model. It MUST NOT act on a verdict served from the persisted
`adjudications` store, a verdict carried by a pending-work queue row, or a
verdict from any earlier run. A cached or queued verdict MAY be displayed
where it is displayed today but MUST NOT be the basis for an automatic merge.

#### Scenario: A cached `same` verdict is not acted on

- GIVEN a persisted `same` verdict at confidence 0.99 for an in-class group
  and an eligible run
- WHEN the automatic pass runs
- THEN the group is judged again with the measured model and the merge
  decision uses that new verdict

#### Scenario: A cached verdict that the fresh judgment contradicts

- GIVEN a persisted `same` verdict for an in-class group, and a fresh
  judgment in this run of `uncertain`
- WHEN the automatic pass runs
- THEN the group is not merged

#### Scenario: A queued verdict is not acted on

- GIVEN a pending-work row carrying a `same` verdict for an in-class group
- WHEN the automatic pass runs
- THEN the group is judged fresh and the queued verdict is not used to merge

### Requirement: Automatic Merges Are Mechanical And Reversible

Each automatic merge MUST use the survivor chosen by the existing ordering
for a base/`-N` pair, which is the un-suffixed base Concept ID, and MUST
stack the absorbed body under the existing merged-content delimiter without
any model rewrite of the merged body (the semantics of `--no-reconcile`). It
MUST go through the same merge core as `openkos merge`, so that the ledger
entry, link rewrites, relation rewrites, provenance rewiring and sensitivity
recomputation are written exactly as for a manual merge. Each merge MUST be
reversible with `openkos unmerge <survivor>` and MUST restore both documents
byte-for-byte.

#### Scenario: The base id survives and the body is stacked

- GIVEN an in-class pair `foo` and `foo-2` where `foo-2` has the longer body
- WHEN the automatic pass merges it
- THEN `foo` is the survivor, `foo-2` is absorbed, the absorbed body is
  stacked under `## Merged content (foo-2)`, and no reconciliation model call
  is made

#### Scenario: An automatic merge unmerges to byte parity

- GIVEN a pair merged by the automatic pass
- WHEN `openkos unmerge <survivor>` is run and confirmed
- THEN survivor and absorbed are restored byte-for-byte and that merge's own
  `log.md` bullet is removed

#### Scenario: Reconcile is never planned for an automatic merge

- GIVEN an in-class pair whose merged body clears the reconciliation
  thresholds
- WHEN the automatic pass merges it
- THEN no reconciliation pass is planned, disclosed or applied

### Requirement: One Commit Per Run, One Log Bullet Per Merge

All automatic merges applied in one run MUST land in a single git commit
whose pathspec is the union of the paths those merges wrote, staged with the
scoped staging the other verbs use. Each merge MUST keep its own `log.md`
bullet, so that `unmerge` still removes a merge by the exact text of that
bullet. When no merge was applied, the pass MUST make no commit. When the
workspace has no git identity or no repository, the pass MUST degrade as
other auto-commits do (one non-fatal warning, no commit) and its merges still
stand.

#### Scenario: Three merges produce one commit

- GIVEN three eligible in-class groups with distinct survivors
- WHEN the automatic pass runs
- THEN exactly one commit is created by the pass and it contains all three
  merges

#### Scenario: Each merge keeps its own log bullet

- GIVEN the previous scenario
- WHEN `bundle/log.md` is read
- THEN it contains three separate merge bullets, one per merge, each
  removable by `unmerge` of its survivor

#### Scenario: Unmerging one merge leaves the others

- GIVEN a run that auto-merged `foo` and `bar`
- WHEN `openkos unmerge foo` is confirmed
- THEN the `foo` pair is restored and its bullet removed, and the `bar`
  merge and its bullet are untouched

#### Scenario: No eligible group makes no commit

- GIVEN an eligible run in which no group passes per-group eligibility
- WHEN the automatic pass runs
- THEN no commit is created and no merge disclosure block is printed

#### Scenario: A workspace without git identity merges without a commit

- GIVEN a workspace with no git identity
- WHEN the automatic pass applies a merge
- THEN the merge stands, one non-fatal warning is printed, and no commit line
  is printed

### Requirement: The Run Discloses Every Automatic Merge And Its Undo

After the pass the run MUST print a disclosure block with one line per
automatic merge naming the survivor, the absorbed id, the judged confidence,
and the command `openkos unmerge <survivor>`. When the pass's commit returned
a sha, the block MUST also name that commit and the `git revert` line from
the shared commit-disclosure helper, with its matching rule that revert
undoes the commit only while it is the latest. When a later stage of the same
run edited a survivor that was auto-merged, the run MUST also state that
`unmerge` then needs `--discard-survivor-edits` for that survivor.

#### Scenario: One line per merge with its undo

- GIVEN two automatic merges
- WHEN the run completes
- THEN the output has two lines, each naming survivor, absorbed, confidence
  and `openkos unmerge <survivor>`, and one commit line with the sha

#### Scenario: A later edit to a survivor adds the caveat

- GIVEN an auto-merged survivor that the Structure stage then edits in the
  same run
- WHEN the run completes
- THEN the output states that `openkos unmerge` of that survivor needs
  `--discard-survivor-edits`

#### Scenario: Nothing is silent

- GIVEN a run that auto-merged one pair and left another in-class pair
  unmerged because of the guardrail
- WHEN the run completes
- THEN the output reports the merge and also reports the unmerged pair with
  its reason

### Requirement: A Mid-Run Failure Keeps What Landed

If a merge fails partway through the automatic pass, the pass MUST stop and
MUST NOT attempt any further automatic merge. The merges that had already
landed MUST be committed in the run's single commit. The run MUST report which
merges were applied, which were not applied, and the failure. The remaining
Identity groups MUST then be handled as for any group the pass did not merge.

#### Scenario: The second of three merges fails

- GIVEN three eligible groups and a write failure on the second
- WHEN the automatic pass runs
- THEN the first merge is committed, the third is not attempted, and the
  report lists the first as applied, the second and third as not applied, and
  names the failure

#### Scenario: A failure on the first merge commits nothing

- GIVEN a write failure on the first merge
- WHEN the automatic pass runs
- THEN no commit is created and the report lists every group as not applied

### Requirement: Auto-Merge Is Opt-In Per Run And Never Unattended By Default

Automatic merging MUST happen only in a `curate` run that was passed
`--auto-merge`. No configuration key, environment variable or default MUST
enable it, and neither `review: false` nor `--accept` MUST enable it. The
daemon and the pending-work maintenance pass MUST NEVER auto-merge.

#### Scenario: Without the flag nothing is merged automatically

- GIVEN a workspace with an eligible in-class `same` group
- WHEN `curate` runs without `--auto-merge`, including with `--auto`
- THEN no group is merged without a per-item answer

#### Scenario: No configuration key turns it on

- GIVEN `openkos.yaml` with `review: false` and any other key
- WHEN `curate` runs without `--auto-merge`
- THEN no group is merged without a per-item answer

### Requirement: The Recommended Set For Accept-Recommended

The set that Identity's accept-recommended answer may offer MUST be exactly
the groups that satisfy: the run is eligible; the group is in the class (so
no member is an imported concept); its
`cross_type_concern` is `None`; no member is confidential or LLM-blocked; and
the measured model judged it `same` in this run at any confidence. Confidence
below 0.90 MUST NOT exclude a group from the recommended set. A group outside
the set MUST NOT be offered through accept-recommended and MUST keep its
per-item prompt. A group with an imported member is outside the measured
population (ingest-time `-N` siblings) and is therefore outside the set,
whatever its verdict and confidence.

#### Scenario: A lower-confidence in-class `same` group is recommended

- GIVEN an in-class group judged `same` at confidence 0.60 in an eligible run
- WHEN the recommended set is built
- THEN the group is in the set

#### Scenario: An out-of-class `same` group is not recommended

- GIVEN a three-member group, or a two-member Person pair, judged `same`
- WHEN the recommended set is built
- THEN the group is not in the set

#### Scenario: A non-`same` in-class group is not recommended

- GIVEN an in-class group judged `distinct` or `uncertain`
- WHEN the recommended set is built
- THEN the group is not in the set

#### Scenario: A confidential or LLM-blocked member excludes the group

- GIVEN an in-class group judged `same` with a confidential member
- WHEN the recommended set is built
- THEN the group is not in the set and keeps its per-item prompt

#### Scenario: An ineligible run has an empty set

- GIVEN a run whose model digest differs from the measured digest
- WHEN the recommended set is built
- THEN it is empty

#### Scenario: A `same` group with an imported member is not recommended

- GIVEN a HIGH-tier base/`-N` pair of one allowed type, such as
  `imports/acme/concepts/python` and `imports/acme/concepts/python-3`,
  judged `same` at high confidence in an eligible run
- WHEN the recommended set is built
- THEN the group is not in the set and keeps its per-item prompt

#### Scenario: A mixed local and imported group is not recommended

- GIVEN a `same` group with one local and one imported member
- WHEN the recommended set is built
- THEN the group is not in the set and keeps its per-item prompt
