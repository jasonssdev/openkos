# Delta for stable-python-api

## ADDED Requirements

### Requirement: Group Decisions Extend v1 Additively

The API MUST expose the curate surface's merge-group action within
`API_VERSION` 1, and every change it makes to the public surface MUST be
additive under the compatibility policy, so the golden snapshot is regenerated
and `API_VERSION` is not raised:

- `Action` gains the member `MERGE_GROUP` (`"merge_group"`);
- a public `GroupSelection` dataclass holds `survivor_id` and `member_ids`;
- `DecisionVersion` gains a defaulted `selection: GroupSelection | None`, which
  is `None` on every version except a merge-group's;
- `preview_decision` gains an optional keyword `selection`, defaulting to
  `None`; `apply_decision` takes no new parameter, because the selection travels
  in the version;
- a public `MergeStep` dataclass describes one pairwise step: the survivor, the
  absorbed member, the absorbed member's OKF type, what happens to that type
  (`same_type`, `recorded`, `discarded` or `unknown`) and the number of other
  documents the step rewrites;
- `MergePlan` gains defaulted `steps`, `kept_distinct_ids` and `survivor_type`;
  a merge plan lists its one step, and a merge-group plan lists every step, the
  members it keeps distinct and the survivor's type;
- `Applied` gains defaulted `merged` (the (survivor, absorbed) pairs written) and
  `kept_distinct` (the member sets a ruling was recorded for);
- `NotApplied.reason` gains the value `invalid_selection`;
- a public `MergeGroupInterrupted` exception, a subclass of `WriteFailed`, so
  `except WriteFailed` and `except OpenKosError` keep catching it, carries the
  completed pairs, the commit identity and the failed absorbed member.

A merge-group preview MUST return a `MergePlan` whose `kind` is `merge`, as a
`relate_reversed` preview returns a plan of kind `relate`; the plan type union
of `DecisionPreview` and the `ApplyOutcome` alias MUST NOT be widened. The
merge-group's survivor, absorbed members and kept-distinct members MUST be
reported by Concept ID only; no stored payload and no adjudicator confidence is
exposed.

#### Scenario: The surface diff is additive

- GIVEN the snapshot recorded at the merge base with `origin/main`
- WHEN the snapshot is regenerated after the change
- THEN every difference is classified additive and `API_VERSION` is still 1

#### Scenario: A merge-group preview round-trips through apply

- GIVEN an open identity decision judged SAME over three members
- WHEN `preview_decision` is called with `MERGE_GROUP` and a selection, and
  `apply_decision` with the returned version and `MERGE_GROUP`
- THEN the preview's plan has `kind` `merge` and two steps, and the result is
  `Applied` whose `merged` lists both pairs

#### Scenario: An interrupted group is caught as a write failure

- GIVEN a merge-group whose second step fails
- WHEN it is applied inside `except WriteFailed`
- THEN the handler runs with a `MergeGroupInterrupted` naming the completed pair

#### Scenario: A pairwise merge plan lists its one step

- GIVEN an open identity decision judged SAME over two members of one type
- WHEN it is previewed with `MERGE`
- THEN the plan's `steps` holds one step whose survivor and absorbed member
  equal the plan's `survivor_id` and `absorbed_id`
