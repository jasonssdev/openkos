# Archive Report: curate-group-walk

**Archived**: 2026-10-16, in the same pull request as the implementation.
**Archive folder**: `openspec/changes/archive/2026-10-16-curate-group-walk/`
**Tracking issue**: #1438 (follows `identity-groups-decision`, design Decision 11)

## Final State At Close

- **Implementation**: `src/openkos/cli/curate.py` gains the Identity group walk
  (`_walk_identity_group`, `_group_selection`, `_ask_numbers`, `_offers_group`).
  It previews through `curate_decisions.preview_decision(..., Action.MERGE_GROUP,
  selection=...)` and commits through `commit_merge_group_decision(...,
  expected=version)`; nothing in `application/` or `openkos.api` changed.
- **Tests**: `tests/unit/cli/test_curate_group_walk.py` (22 cases). The two
  existing tests that pinned the skip lines (`test_identity_n_gt2_group_prints_pairwise_commands_no_merge`,
  `test_identity_walk_skips_a_cross_type_pair`) run without a queue and now pin
  the fallback; their comments say so.
- **Docs**: the Identity paragraph of `docs/cli.md` (the walk's shape changed) and
  one `pending` sentence that said `curate` refuses a cross-type merge; CHANGELOG.
- **No ADR**: design Decision 5.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| curate-command | Updated | 1 MODIFIED requirement replaced in place (Identity Stage Reuses Merge Cores; its scenario "N>2 group prints pairwise commands, never auto-merges" is replaced by "A group of three is walked, never auto-merged"). 1 ADDED requirement appended (Identity Walks A Group Through The Group Decision, 8 scenarios). 30 requirements now. |
| curate-decisions | Updated | 1 MODIFIED requirement replaced in place (Behavior Of The Command Is Unchanged). 22 requirements now. |

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of both deltas was compared
as an exact line against its living spec after the merge: 20 headings
(curate-command 18, curate-decisions 2), 0 missing, 0 non-unique. Before the
merge each MODIFIED heading matched exactly once and the ADDED heading did not
exist. No REMOVED or RENAMED headings.

## Requirement-vs-Code Check

| Requirement | Result |
|-------------|--------|
| curate-command: Identity Stage Reuses Merge Cores | Met. A group of more than two and a cross-type pair go to `_walk_identity_group`; neither the automatic pass nor accept-recommended touches them (both stay pair-only). |
| curate-command: Identity Walks A Group Through The Group Decision | Met. Members listed in ascending Concept ID order; the three questions asked only when needed; the survivor is a marked member of the chosen type; Enter is `s` and unrecognized answers are re-asked; `y` commits through `commit_merge_group_decision` against the previewed version; `d` runs `commit_keep_distinct` over every member; drift exits 3; `MergeGroupInterrupted` names the kept merges and exits 1; Stale and NotApplied count a skip with the reason; a row that does not offer merge-group, or no queue, keeps the earlier skip lines. |
| curate-decisions: Behavior Of The Command Is Unchanged | Met. The characterization goldens (`tests/unit/cli/test_curate_characterization.py`) pass unchanged; none records a group or a cross-type pair. |

## Known Gap

The group decision commits against the group's open row, and `curate` writes
rows only into a queue that already exists (a daemon or app refresh creates it).
In a workspace that has never had a producer pass, the walk keeps the pairwise
commands. Filed as #1489.
