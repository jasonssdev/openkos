# Delta for stable-python-api

## ADDED Requirements

### Requirement: Merge History Is Readable

`openkos.api` MUST export `merge_history(workspace, concept_id)`, returning a
tuple of `MergeRecord`, newest merge first, one per entry of the concept's
committed merge ledger. A `MergeRecord` MUST carry `survivor_id`, `absorbed_id`,
`absorbed_title` (the `title` of the absorbed document as the ledger recorded it,
or `None` when it cannot be read) and `merged_at` (the ledger's timestamp). The
read MUST NOT take the workspace lock, build a model client or write anything. An
id that is not a valid Concept ID, or whose concept has no ledger, MUST return an
empty tuple rather than raise.

#### Scenario: A group's merges are listed newest first

- GIVEN a survivor that absorbed `b` and then `c` through a group decision
- WHEN `merge_history` is called for the survivor
- THEN it returns two records, `c` first, each naming the survivor, the absorbed
  id, its recorded title and a timestamp, and the workspace is unchanged

#### Scenario: A concept never merged lists nothing

- GIVEN a concept with no merge ledger, and an id with a `..` segment
- WHEN `merge_history` is called for each
- THEN each returns an empty tuple

### Requirement: Unmerge Is Previewed, Then Applied Against Its Version

`openkos.api` MUST export `preview_unmerge(workspace, survivor_id, absorbed_id,
*, discard_survivor_edits=False)` and `apply_unmerge(workspace, version, *,
wait_seconds=0.0, on_warning=None)`.

The preview MUST plan the unwind of the survivor's merge ledger from the newest
entry down to and including the entry that absorbed `absorbed_id`, and return an
`UnmergePreview` whose `steps` list one `UnmergeStep` per single-step unmerge in
execution order, and whose `version` is an `UnmergeVersion` the apply commits
against. When `absorbed_id` names the newest entry the plan has exactly one step.
Each `UnmergeStep` MUST carry the survivor, the absorbed id, its recorded title,
`merged_at`, the number of other documents the step rewrites, whether the
survivor-edit check is unverifiable for that entry, and whether the entry restores
whole-file catalog snapshots. The preview MUST NOT take the lock, build a model
client or write anything.

The preview MUST return `NotUnmergeable` (never raise) with reason
`survivor_missing` when the survivor's document does not exist (with
`absorbed_into` naming the concept that absorbed it, when a ledger records one),
`not_merged` when the survivor's ledger records no merge of `absorbed_id`, and
`survivor_edited` when restoring any step would discard an edit made to the
survivor after that step's merge and `discard_survivor_edits` is false. With
`discard_survivor_edits` true, the plan proceeds and `survivor_edits_discarded`
reports whether an edit is discarded. Any other refusal of the plan MUST raise
`Refused`.

The version MUST carry the survivor, the absorbed id, `discard_survivor_edits`
and a content digest covering the ledger entries being reversed and the current
bytes of every file the steps restore, rewrite or recreate. The apply MUST hold
the workspace lock (waiting at most `wait_seconds`, 0 to 3600) while it plans the
same unwind again from current bytes and, when the digest still matches, runs
every step, each as the CLI's single-step unmerge committed on its own. It MUST
return `Unmerged` naming the restored ids in execution order and each step's
commit identity (`None` for a commit that could not be made, reported once
through `on_warning`), `UnmergeStale("content_changed")` when the digest differs,
or the same `NotUnmergeable` a preview would return; neither of those writes
anything. A first step that fails MUST raise its own refusal (`Refused` or
`DriftDetected`) with nothing written; a later step that fails MUST raise
`UnmergeInterrupted`, a subclass of `WriteFailed`, carrying the completed
restored ids, their commits and the failed absorbed id, and the completed steps
MUST be kept. The apply MUST NOT reconcile with a model or refresh the derived
indexes.

#### Scenario: A pairwise merge is split again

- GIVEN two concepts merged through a decision
- WHEN the merge is previewed with `preview_unmerge` and applied with the
  returned version
- THEN the result is `Unmerged` with one restored id and one commit, and every
  file of the bundle except `log.md` equals its bytes before the merge

#### Scenario: A group is split again in one plan

- GIVEN a group decision that merged `b` and `c` into `a`
- WHEN `preview_unmerge(a, b)` is called
- THEN its steps are `c` then `b`, and applying its version restores both with
  one commit per step

#### Scenario: A changed bundle makes the version stale

- GIVEN a preview, after which a document the plan restores or rewrites changes
- WHEN the version is applied
- THEN the result is `UnmergeStale("content_changed")` and nothing is written

#### Scenario: An edited survivor is refused unless the caller opts in

- GIVEN a merge whose survivor was edited afterwards
- WHEN it is previewed without and then with `discard_survivor_edits`
- THEN the first result is `NotUnmergeable("survivor_edited")` and the second is
  a preview reporting `survivor_edits_discarded`

#### Scenario: A flipped echoed flag is stale

- GIVEN a preview without `discard_survivor_edits`
- WHEN its version is applied with the flag set to true
- THEN the result is `UnmergeStale` and nothing is written

#### Scenario: A busy workspace writes nothing

- GIVEN the workspace lock is held by another process
- WHEN a version is applied with `wait_seconds` 0
- THEN `WorkspaceBusyError` is raised and nothing is written

### Requirement: Unmerge Extends v1 Additively

The unmerge operations MUST be added within `API_VERSION` 1, and every change to
the public surface MUST be classified additive by the surface classifier, so the
golden snapshot is regenerated and `API_VERSION` is not raised: new names
(`MergeRecord`, `UnmergeStep`, `UnmergeVersion`, `UnmergePreview`,
`NotUnmergeable`, `UnmergeStale`, `Unmerged`, `UnmergeInterrupted`,
`merge_history`, `preview_unmerge`, `apply_unmerge`) and the value `"unmerge"`
added to `WriteFailed.operation`. No existing union, alias or signature changes.

#### Scenario: The surface diff is additive

- GIVEN the snapshot recorded at the merge base with `origin/main`
- WHEN the snapshot is regenerated after the change
- THEN every difference is classified additive and `API_VERSION` is still 1

#### Scenario: An interrupted chain is caught as a write failure

- GIVEN an unwind whose second step fails
- WHEN it is applied inside `except WriteFailed`
- THEN the handler runs with an `UnmergeInterrupted` naming the restored id of
  the first step
