# Delta for curate-command

## MODIFIED Requirements

### Requirement: Identity Stage Reuses Merge Cores

Identity MUST call `find_candidates` then `adjudicate_candidates`, then
apply each accepted pair through the identity merge of the `curate-decisions`
surface, which runs the same prepare and commit merge cores as `merge`,
auto-committing per merge. A judged-SAME group of more than two members, and
a cross-type pair, MUST NOT be auto-merged; Identity MUST walk it through the
group decision (see "Identity Walks A Group Through The Group Decision").
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
(Previously: a group of more than two members was skipped with the exact
pairwise `openkos merge` commands printed, and a cross-type pair was skipped with
the `openkos merge --include-cross-type` command.)

#### Scenario: Accepted pair is committed per-item

- GIVEN one accepted duplicate pair
- WHEN Identity applies it
- THEN the merge runs through the same prepare and commit merge cores as
  `merge` and the bundle auto-commits before the next item

#### Scenario: A group of three is walked, never auto-merged

- GIVEN a judged-SAME candidate group of 3
- WHEN Identity reaches it on a TTY
- THEN it asks which members are the same instead of printing pairwise
  commands, and performs no merge without the per-group `y`

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

#### Scenario: A drifted target still ends the run

- GIVEN an accepted Identity pair whose member bytes changed between the
  prompt and the write
- WHEN Identity applies it
- THEN nothing is written, the drift is reported, and `curate` exits 3 with no
  later item or stage run, exactly as before the surface existed

#### Scenario: A write failure still exits 1

- GIVEN an accepted Identity pair whose write fails
- WHEN Identity applies it
- THEN `curate` discloses what it already applied and exits 1, exactly as
  before the surface existed

## ADDED Requirements

### Requirement: Identity Walks A Group Through The Group Decision

On a TTY, for each judged-SAME candidate group of more than two members, and each
cross-type pair, whose open identity row offers the merge-group action
(`curate-decisions`), Identity MUST list the members once, numbered in ascending
Concept ID order with each member's title and OKF type, and MUST then ask, in
order:

1. for a group of more than two members only, which members are the same: two or
   more distinct member numbers, `a` (all), `d` (keep all separate) or `s`
   (decide later);
2. only when the marked members declare more than one readable OKF type, what the
   thing is: one choice per type, `d` or `s`; the survivor MUST then be a marked
   member of the chosen type;
3. only when more than one marked member can survive, which one keeps its name:
   one member number or `s`.

The engine MUST NOT choose the members, the type or the survivor. Enter MUST mean
`s` at every question, and an unrecognized answer MUST be re-asked with a notice
naming the accepted answers. Identity MUST then preview the selection through the
decision surface's merge-group preview, render every pairwise step with its type
outcome, the members kept distinct and the cross-source and cross-type notes, and
ask `[y]es / [s]kip` (Enter = `s`). Only `y` writes, through the surface's group
commit against the previewed version. `d` MUST record a keep-distinct ruling over
every member through the existing keep-distinct path; `s` MUST write and record
nothing. A drift MUST exit 3 with nothing written; a write failure MUST exit 1,
and a group stopped after some merges MUST name the merges it kept. A stale or
refused commit MUST be counted as skipped with its reason. A group whose open row
does not offer merge-group, including a group in a workspace with no pending-work
queue, MUST keep the earlier skip line (the pairwise
`openkos merge` commands, or the `openkos merge --include-cross-type` command)
and perform no merge. The walk MUST NOT run without a TTY, and neither
`--auto-merge` nor `--accept` reaches it.

#### Scenario: Two of three marked members are combined and the third is kept distinct

- GIVEN a judged-SAME group A, B, C of one OKF type
- WHEN the person marks 1 and 2, chooses A as the name, and answers `y`
- THEN B is merged into A in one commit, C is untouched, and a keep-distinct
  ruling over A and C is recorded

#### Scenario: A cross-type pair asks what it is and the member of that type survives

- GIVEN a judged-SAME pair of an Event and a Project
- WHEN the person chooses Event and answers `y`
- THEN the Project is merged into the Event, and the Project type is recorded as
  the survivor's type alternative

#### Scenario: Two candidates of the chosen type ask which keeps its name

- GIVEN a judged-SAME group of two Projects and one Event, all marked
- WHEN the person chooses Project
- THEN Identity asks which of the two Projects keeps its name before any preview

#### Scenario: Keep all separate records one ruling and merges nothing

- GIVEN a judged-SAME group of three
- WHEN the person answers `d`
- THEN nothing is merged and a keep-distinct ruling over all three is recorded

#### Scenario: Enter decides later

- GIVEN a judged-SAME group of three
- WHEN the person presses Enter at the first question
- THEN nothing is written or recorded and the group is offered again next run

#### Scenario: A row that does not offer merge-group keeps the skip line

- GIVEN a judged-SAME group of three whose open row does not carry a SAME judgment
- WHEN Identity reaches it
- THEN it prints the pairwise `openkos merge` commands and performs no merge

#### Scenario: Without a queue the group keeps the skip line

- GIVEN a judged-SAME group of three in a workspace with no pending-work queue
- WHEN Identity reaches it
- THEN it prints the pairwise `openkos merge` commands and performs no merge

#### Scenario: A group cut short exits 1 and names what it kept

- GIVEN a group of three marked, whose second merge fails to write
- WHEN Identity commits it
- THEN the first merge is kept, the line names it and the step that failed, and
  `curate` exits 1
