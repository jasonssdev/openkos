# Delta for Curate Command

The `curate` walk becomes the first client of the `curate-decisions`
application surface. Only the apply half of the three decision walks moves
behind that surface; probes, the cost gate, judging, prompts and the stage
engine stay in the command. Observable behavior is unchanged: every stream,
exit code and consent rule below is preserved verbatim. The consent
requirements ("Per-Stage Cost Gate", "Per-Stage Accept-All Is Opt-In And Never
Covers Identity", "Bulk Acceptance Excludes Asymmetric Relation Types",
"`review: false` Accepts Only The Non-Destructive Stages",
"`--auto-merge` Applies The Measured Identity Class Without A Prompt" and
"Identity Offers Accept-Recommended For In-Class Groups") are intentionally NOT
modified: their text and scenarios stay as they are, and the evaluation of that
policy from data is specified in `curate-decisions`.

## MODIFIED Requirements

### Requirement: Identity Stage Reuses Merge Cores

Identity MUST call `find_candidates` then `adjudicate_candidates`, then
apply each accepted pair through the identity merge of the `curate-decisions`
surface, which runs the same prepare and commit merge cores as `merge`,
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
(Previously: each accepted pair was applied by the walk itself via
`_prepare_one_merge`/`_commit_one_merge`; the sequencing now lives behind the
`curate-decisions` surface and the command renders its returned data.)

#### Scenario: Accepted pair is committed per-item

- GIVEN one accepted duplicate pair
- WHEN Identity applies it
- THEN the merge runs through the same prepare and commit merge cores as
  `merge` and the bundle auto-commits before the next item

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

### Requirement: Structure Stage Writes Through The Relate Core

Structure MUST call `suggest_edge_types` with `on_progress`, and MUST
write each accepted suggestion through the relation apply of the
`curate-decisions` surface, which runs the extracted `relate` core.
Declined and skipped suggestions MUST be skipped without a write.
(Previously: the walk wrote each accepted suggestion by calling the extracted
`relate` core directly.)

#### Scenario: Accepted suggestion writes via the extracted core

- GIVEN one accepted edge-type suggestion
- WHEN Structure applies it
- THEN the write occurs through the extracted `relate` core and matches
  standalone `relate`'s output

#### Scenario: Declined suggestion is skipped

- GIVEN one declined suggestion
- WHEN Structure processes it
- THEN no edge is written

#### Scenario: A skipped suggestion is not listed as declined

- GIVEN one suggestion answered `s`
- WHEN Structure processes it
- THEN no edge is written and the suggestion is counted as skipped, but no
  `declined:` line names it

### Requirement: Metadata Stage Writes Tiers, Reports Sensitivity

Metadata MUST call `suggest_volatility` with `on_progress` and write
accepted tiers through the tier apply of the `curate-decisions` surface,
which runs the extracted `set-volatility` core. Sensitivity
gaps surfaced in the same pass MUST be reported only; Metadata MUST NOT
write sensitivity.
(Previously: the walk wrote accepted tiers by calling the extracted
`set-volatility` core directly.)

#### Scenario: Accepted tier writes via the extracted core

- GIVEN one accepted volatility tier
- WHEN Metadata applies it
- THEN the write occurs through the extracted `set-volatility` core

#### Scenario: Sensitivity gap is reported, never written

- GIVEN a concept with an unset sensitivity level
- WHEN Metadata reports it
- THEN it prints the gap and names `openkos set-sensitivity`, writing
  nothing

### Requirement: Curate Reads And Resolves The Pending Queue

WHEN the pending-work queue exists, each `curate` stage MUST take the open
rows of its kind as its candidates before recomputing, MUST enqueue any
proposal it computes that has no open row, and MUST resolve each row it
presents: `applied` (as proposed or modified) when the item is written,
`declined` when the operator declines it through a surface that records the
decline. A row presented and neither written nor declined MUST return to
`pending`. The Contradictions stage stays report-only: it enqueues and
never applies. A row that a write resolves MUST be resolved by the
`curate-decisions` apply that performed the write, in the same action, not by
a separate step in the command.
(Previously: the requirement did not say where the resolution is performed.)

#### Scenario: An accepted relation resolves its row

- GIVEN an open relation-type row
- WHEN the operator accepts it in the Structure stage
- THEN the relation is written and the row is `applied` as proposed

#### Scenario: A skipped item returns to pending

- GIVEN an open Identity row presented in the walk
- WHEN the operator answers `s`
- THEN nothing is written and the row is `pending` after the run
