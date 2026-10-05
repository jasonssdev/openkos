# Delta for Curate Command

## ADDED Requirements

### Requirement: `--auto-merge` Applies The Measured Identity Class Without A Prompt

`curate` MUST accept a boolean `--auto-merge` flag, off by default and
meaningful only for the run it is passed to. When set and the run is
eligible (`identity-auto-merge`: Run Eligibility Is Checked Once And Every
Failure Is Reported), Identity MUST run an automatic pass before its
per-item walk that merges the groups meeting `identity-auto-merge`:
Per-Group Eligibility, without a per-item prompt. Every group the pass does
not merge MUST continue through the normal Identity flow.

`--auto-merge` MUST NOT imply `--auto`. Unattended use is `curate --auto
--auto-merge`. With `--auto-merge` alone the Identity cost gate MUST behave
as it does without the flag: it prompts on a TTY and, on a non-TTY, declines
before any model call, so the automatic pass makes no judgment and no merge.
The automatic pass MUST run only after the Identity cost gate has been
accepted.

`--auto-merge` together with `--reconcile` MUST be refused with exit 2,
naming both flags, before any workspace gate or read, with no write and no
model call.

On a non-TTY, `--auto-merge` MUST exempt only the automatic pass from the
Identity non-TTY write refusal. The interactive remainder MUST still be
declined on a non-TTY exactly as today, with the standalone-verb pointer, and
MUST NOT reach a prompt on a pipe.

#### Scenario: Unattended run merges an eligible in-class pair

- GIVEN stdin is not a TTY, an eligible run, and one in-class `same` group at
  confidence 0.95
- WHEN `curate --auto --auto-merge` runs
- THEN the pair is merged, the disclosure block names it with
  `openkos unmerge <survivor>`, and no prompt is printed

#### Scenario: `--auto-merge` without `--auto` still hits the cost gate on a TTY

- GIVEN a TTY and an in-class `same` group
- WHEN `curate --auto-merge` runs
- THEN the Identity cost line is printed and a confirmation is asked before
  any model call

#### Scenario: `--auto-merge` without `--auto` on a non-TTY spends nothing

- GIVEN stdin is not a TTY and an in-class `same` group
- WHEN `curate --auto-merge` runs without `--auto`
- THEN Identity declines before any model call, no group is merged, and no
  prompt is printed

#### Scenario: `--auto-merge` with `--reconcile` is refused

- GIVEN any workspace
- WHEN `curate --auto-merge --reconcile` runs
- THEN the exit code is 2, stderr names both flags, and nothing is read,
  written or sent to a model

#### Scenario: The non-TTY remainder is still declined

- GIVEN stdin is not a TTY, one eligible in-class group and one out-of-class
  `same` group
- WHEN `curate --auto --auto-merge` runs
- THEN the in-class group is merged, the out-of-class group is not merged,
  and the standalone-verb pointer is printed for it

#### Scenario: The flag does not carry over

- GIVEN a run that used `--auto-merge`
- WHEN `curate` runs again without the flag
- THEN no group is merged without a per-item answer

#### Scenario: `--accept identity` stays refused alongside the flag

- GIVEN any workspace
- WHEN `curate --auto-merge --accept identity` runs
- THEN the exit code is 2 and nothing is written

### Requirement: Identity Offers Accept-Recommended For In-Class Groups

When the run is eligible and, after the automatic pass (if any), at least one
remaining Identity group belongs to the recommended set
(`identity-auto-merge`: The Recommended Set For Accept-Recommended), Identity
MUST, before its per-item walk, offer one accept-recommended question on a
TTY. The offer MUST list every group of the set with its survivor, its
absorbed id and the undo command `openkos unmerge <survivor>`. If the
operator accepts, each listed group MUST be applied as an ordinary per-item
merge with its own commit (#800) and its own `log.md` bullet. If the operator
declines, every group MUST proceed to the per-item walk unchanged.

Accept-recommended MUST be a separate pre-pass question, not an answer on the
per-item prompt. It MUST NOT be offered when the set is empty, when the run
is ineligible, or on a non-TTY. Groups outside the set MUST keep their
per-item prompt whatever the operator answers. It MUST NOT be added to
`adjudicate --apply` or `adjudicate --apply-same`.

#### Scenario: The offer lists survivor, absorbed and undo per item

- GIVEN a TTY and two in-class groups judged `same`, at confidences 0.95 and
  0.60
- WHEN Identity reaches the accept-recommended offer
- THEN both groups are listed, each with survivor, absorbed and `openkos
  unmerge <survivor>`

#### Scenario: Accepting applies per-merge commits

- GIVEN the previous scenario and the operator accepts
- WHEN Identity applies the set
- THEN two merges are applied with two separate commits

#### Scenario: Declining falls through to per-item prompts

- GIVEN the same offer and the operator declines
- WHEN Identity continues
- THEN both groups are prompted individually and none is merged without `y`

#### Scenario: An out-of-set group keeps its prompt after accepting

- GIVEN an offer for one in-class group and a separate out-of-class `same`
  group, and the operator accepts
- WHEN Identity continues
- THEN the in-class group is merged and the out-of-class group is prompted
  individually

#### Scenario: No offer for an empty set

- GIVEN no remaining group is in the recommended set
- WHEN Identity runs
- THEN no accept-recommended question is printed

#### Scenario: No offer on a non-TTY

- GIVEN stdin is not a TTY and a group in the recommended set
- WHEN `curate --auto` runs without `--auto-merge`
- THEN no accept-recommended question is printed and no group is merged

#### Scenario: Groups already merged by the pass are not offered again

- GIVEN `--auto-merge` merged one in-class group in this run
- WHEN Identity reaches the accept-recommended offer
- THEN the merged group is not listed

## MODIFIED Requirements

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
--confirm-count` for Identity). The one exception is Identity's automatic
pass under `--auto-merge`, which is not a per-item write walk and which
MUST run on a non-TTY once the gate is accepted by `--auto`; the pass's
spend is part of the Identity gate's disclosed call count, and the per-item
remainder still declines with the pointer.
(Previously: a write stage declined its per-item write walk on a non-TTY
with no exception.)

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

#### Scenario: Non-TTY with --auto and --auto-merge runs only the automatic pass

- GIVEN stdin is not a TTY, `--auto` and `--auto-merge` are passed, and
  Identity has an eligible in-class group and an out-of-class group
- WHEN `curate` reaches Identity
- THEN the automatic pass runs and merges the eligible group, the per-item
  write walk is declined with the pointer, and the Identity cost line's call
  count covered the pass's judgments

### Requirement: Identity Stage Reuses Merge Cores

Identity MUST call `find_candidates` then `adjudicate_candidates`, then
apply each accepted pair via `_prepare_one_merge`/`_commit_one_merge`,
auto-committing per merge. N>2 groups MUST NOT be auto-merged; `curate`
MUST print the exact pairwise `openkos merge` commands per group.
When `--auto-merge` is passed and the run is eligible, Identity MUST run the
automatic pass (`identity-auto-merge`) before the per-item walk, through the
same merge cores with no reconcile, and MUST NOT offer a group again that
the pass merged. The pass's merges are committed once per run
(`workspace-autocommit`) rather than per merge; every other merge in Identity
keeps its per-merge commit.
Because `find_candidates` bounds and ranks its output before any
adjudication call (`entity-resolution`: Bounded Candidate-Group
Output Per Call), the number of `CandidateGroup`s Identity's probe
(`_identity_probe` in `cli/curate.py`) queues, and therefore the
number of adjudication calls `_identity_run` issues, MUST never exceed
`_MAX_CANDIDATE_GROUPS` regardless of corpus size — the SAME sequencer
that already gates Identity's cost line and consent flow (curate-command:
Per-Stage Cost Gate) is unchanged; only the upstream group count it reads
from `probe.llm_calls` is bounded. A group beyond that cap is not seen by the
automatic pass in that run, and the existing cap disclosure still applies.
(Previously: every Identity merge was prompted and committed per item;
there was no automatic pass.)

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

#### Scenario: Identity's adjudication call count stays capped on a large corpus

- GIVEN a bundle whose Identity queue would otherwise total 150
  `CandidateGroup`s (an uncapped `find_candidates` result)
- WHEN `curate` runs the Identity stage with `--auto`
- THEN the printed cost line's call count and the number of adjudication
  calls actually issued both stay at or below `_MAX_CANDIDATE_GROUPS`

#### Scenario: The automatic pass precedes the per-item walk

- GIVEN `--auto-merge`, an eligible run, one in-class group the pass merges
  and one out-of-class group
- WHEN Identity runs on a TTY
- THEN the in-class group is merged first, without a prompt, and the
  out-of-class group is then prompted individually

#### Scenario: A group the pass declines is still prompted

- GIVEN `--auto-merge`, an in-class group judged `same` at confidence 0.80
- WHEN Identity runs on a TTY
- THEN the pass does not merge it and the normal per-item prompt offers it

#### Scenario: A group beyond the cap is not seen by the pass

- GIVEN more candidate groups than `_MAX_CANDIDATE_GROUPS`, with an
  eligible in-class group beyond the cap
- WHEN `curate --auto --auto-merge` runs
- THEN that group is not merged in this run and the cap disclosure is printed

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
flag and no config value may apply one unreviewed, with one exception: the
per-run `--auto-merge` flag, which applies only the measured structural class
under `identity-auto-merge` and leaves every other Identity group to its
per-item prompt. `--auto-merge` is not an `--accept` stage name and does not
widen `--accept`.

Naming a stage in `--accept` IS per-item write consent for that stage, so
an accepted stage MUST also pass the non-TTY write refusal — `curate --auto
--accept structure` on a pipe writes, matching `suggest-relations --auto`.
Identity MUST remain subject to that refusal on every path, apart from the
automatic pass under `--auto-merge`.
(Previously: no flag and no config value could apply an Identity merge
unreviewed, with no exception.)

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

#### Scenario: `--auto-merge` does not make Identity acceptable in bulk

- GIVEN an in-class group the pass merges and an out-of-class `same` group
- WHEN `curate --auto-merge` runs on a TTY
- THEN only the in-class group is merged without a prompt and the other is
  prompted individually

### Requirement: `review: false` Accepts Only The Non-Destructive Stages

When `--accept` is absent, `review: false` in `openkos.yaml` MUST accept
every auto-acceptable stage — the knob already means "do not confirm before
saving" for the standalone verbs, and `curate` MUST stop ignoring it.

It MUST NOT reach Identity. A value set for the standalone verbs cannot
become retroactive authorization to delete a concept, so every merge still
prompts, and on a non-TTY run Identity still refuses its write walk and
prints the standalone-verb hint. `review: false` MUST neither enable the
automatic pass nor make Identity's accept-recommended offer unnecessary; both
remain governed by their own per-run inputs.

An explicit `--accept` MUST override `review` and name the exact accepted
set rather than widening it, so an operator running with `review: false`
can still re-review a single stage without editing the config file.
(Previously: the requirement did not mention the automatic pass or
accept-recommended.)

#### Scenario: `review: false` accepts Structure but never Identity

- GIVEN `review: false` and both an Identity and a Structure queue
- WHEN `curate` runs
- THEN Structure applies without per-item prompts
- AND every Identity merge is still prompted individually

#### Scenario: An explicit `--accept` narrows `review: false`

- GIVEN `review: false`, a Structure queue and a Metadata queue
- WHEN `curate --accept structure` runs
- THEN Structure applies silently and Metadata prompts per item

#### Scenario: `review: false` does not enable the automatic pass

- GIVEN `review: false` and an eligible in-class `same` group
- WHEN `curate` runs without `--auto-merge`
- THEN the group is not merged without a per-item answer

### Requirement: Accept-Remaining Answer On Non-Destructive Stages

The Structure and Metadata per-item prompts MUST accept `a`/`all`, which
accepts the item and every remaining item of that stage that is acceptable in
bulk, for the rest of the run -- the same set `--accept` applies. Structure's
asymmetric relation types MUST still be asked per item (`a` on a symmetric
prompt never applies one; the asymmetric prompt's own `a` keeps its per-type
meaning), and `a` MUST NOT be offered on the Identity per-item prompt. Identity's
accept-recommended is a separate pre-pass question over the recommended set
only, not an accept-remaining answer. Before applying items without asking,
the stage MUST print a one-line notice on stderr that it is doing so. `a` is
offered only on the prompt of a bulk-acceptable item.
(Previously: Identity's accept-recommended path was stated as not defined.)

#### Scenario: `a` accepts the rest of the Metadata stage

- GIVEN three Metadata suggestions and `input="a\n"`
- WHEN Metadata runs
- THEN all three tiers are written and only the first was asked

#### Scenario: `a` on a symmetric relation still asks asymmetric ones

- GIVEN a `related_to` suggestion followed by a `part_of` suggestion
- WHEN the operator answers `a` to the first
- THEN the `related_to` is written and the `part_of` is still prompted

#### Scenario: Identity does not offer `a`

- GIVEN one Identity pair
- WHEN the operator answers `a`
- THEN the answer is unrecognized and the prompt is asked again
