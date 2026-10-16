# Delta for curate-decisions

## MODIFIED Requirements

### Requirement: Each Kind Carries Its Own Action Set

Items of kinds `identity`, `relation_type` and `volatility` MUST carry
actions, and items of kinds `contradiction`, `revision` and `watch_refusal`
MUST be listed with an empty action set. The actions MUST be:

- identity: merge, keep-distinct, skip; and merge-group in place of merge for a group of more than two members or of members whose OKF types differ (see "Only Judged-SAME Identity Items Are Mergeable");
- relation_type: relate as proposed, relate reversed (only for an asymmetric relation type), skip; a row whose suggested type is `supersedes` carries no action and resolves through `openkos relate`;
- volatility: set the proposed tier, skip.

Skip MUST write nothing and MUST leave the row `pending`. An item with an
empty action set MUST name where it resolves: a `contradiction` through
`openkos contradictions --decline`, `openkos reconcile` or `openkos unmerge`;
a `revision` by its producer, which retires it; a `watch_refusal` through
`openkos ingest`.
(Previously: identity items carried only merge, keep-distinct and skip, so a group of more than two members or a cross-type pair had no merging action.)

#### Scenario: An identity item carries merge, keep-distinct and skip

- GIVEN an open identity item judged SAME over two members of one OKF type
- WHEN a client lists it
- THEN its actions are merge, keep-distinct and skip, and it does not carry
  merge-group

#### Scenario: A judged SAME group of three carries merge-group

- GIVEN an open identity item judged SAME over three members
- WHEN a client lists it
- THEN its actions are merge-group, keep-distinct and skip, and merge is
  withheld with the reason `more_than_two_members`

#### Scenario: Reversal is offered only for asymmetric types

- GIVEN one open relation_type item with a symmetric type and one with an
  asymmetric type
- WHEN a client lists them
- THEN only the asymmetric item offers relate reversed

#### Scenario: An action-less kind says where it resolves

- GIVEN an open contradiction item
- WHEN a client lists it
- THEN its action set is empty and it names `openkos contradictions --decline`,
  `openkos reconcile` and `openkos unmerge` as the places it resolves

#### Scenario: Revision and watch_refusal are listed without actions

- GIVEN an open revision item and an open watch_refusal item
- WHEN a client lists them
- THEN each has an empty action set, the revision names its producer and the
  watch_refusal names `openkos ingest`

#### Scenario: Skip leaves the row pending

- GIVEN an open identity item
- WHEN a client applies skip
- THEN nothing is written and the row is still `pending`

### Requirement: Only Judged-SAME Identity Items Are Mergeable

An identity item MUST carry the merge action only when it holds a persisted SAME judgment over exactly two members that pass the cross-type check. A judged-DIFFERENT item, a judged-SAME item with more than two members and a cross-type pair carry no merge action and name the reason; every identity item keeps keep-distinct and skip. An identity item whose adjudication is null MUST be listed as
pending evaluation and MUST carry no merge action. An identity item whose persisted judgment is UNCERTAIN, including one produced by a malformed model reply, MUST be listed as `judged_uncertain`, MUST carry keep-distinct and skip, MUST carry no merge action naming the reason `judged_uncertain`, and MUST NOT be listed as pending evaluation. The surface MUST NOT merge
an unjudged item on a caller's decision alone, and MUST NOT call a model to
judge it.

The merge-group action MUST be carried exactly by a judged-SAME identity item whose merge is withheld for `more_than_two_members` or `cross_type`; merge stays withheld there, with the same reason. An identity item whose group has more than two members, or whose members' OKF types differ, and whose judgment is not SAME MUST list merge-group as withheld with the same reason as merge (`pending_evaluation`, `judged_different` or `judged_uncertain`). An identity item of two members of one OKF type MUST NOT carry or withhold merge-group. A merge-group request against an item that does not carry it MUST be the typed not-applied result `not_mergeable` when the item withholds it and `not_offered` otherwise, and MUST write nothing.
(Previously: a judged-SAME group of more than two members and a cross-type pair carried no merging action at all, and resolved only through the CLI.)

#### Scenario: A judged SAME item is mergeable

- GIVEN an open identity item with a SAME judgment over two members of one OKF
  type
- WHEN a client lists it
- THEN it carries the merge action

#### Scenario: An unjudged item is pending evaluation

- GIVEN an open identity item with a null adjudication, as the unattended
  daemon enqueues
- WHEN a client lists it
- THEN it is marked pending evaluation and carries no merge action

#### Scenario: A merge against an unjudged item is not applied

- GIVEN an open identity item with a null adjudication
- WHEN a client requests a merge for its `decision_key`
- THEN the typed not-applied result with reason `not_mergeable` is returned
  (returned, not raised: it is not an error) and nothing is written

#### Scenario: An uncertain item is judged uncertain, not pending

- GIVEN an open identity item whose persisted judgment is UNCERTAIN
- WHEN a client lists it
- THEN its evaluation is `judged_uncertain`, its actions are keep-distinct and
  skip, its withheld merge names the reason `judged_uncertain`, and it is not
  marked pending evaluation

#### Scenario: A merge against an uncertain item is not applied

- GIVEN an open identity item judged UNCERTAIN
- WHEN a client requests a merge for its `decision_key`
- THEN the typed not-applied result with reason `not_mergeable` is returned and
  nothing is written

#### Scenario: A judged SAME cross-type pair carries merge-group

- GIVEN an open identity item judged SAME over two members whose OKF types
  differ
- WHEN a client lists it
- THEN its actions are merge-group, keep-distinct and skip, and merge is
  withheld with the reason `cross_type`

#### Scenario: An unjudged group withholds merge-group

- GIVEN an open identity item over three members with a null adjudication
- WHEN a client lists it
- THEN it is pending evaluation, and both merge and merge-group are withheld
  with the reason `pending_evaluation`

#### Scenario: A merge-group against an unjudged group is not applied

- GIVEN an open identity item over three members with a null adjudication
- WHEN a client previews or applies merge-group for its `decision_key`
- THEN the typed not-applied result with reason `not_mergeable` is returned and
  nothing is written

### Requirement: Apply Is A Two-Phase Preview And Commit Against A Version Token

Applying a write action MUST be two phases. The preview phase MUST return,
without writing, what the action would do, plus a version token for the item.
The commit phase MUST take the token the caller holds and MUST re-validate it before any write: the content half against a re-preview from current bytes before the commit section opens (a mismatch is the typed Stale result `content_changed`), and the row half re-read inside the commit section, where the workspace lock is held (`row_resolved`, `row_superseded`, `row_inputs_changed`). A change that lands between the two is raised as drift, not returned as Stale. When a reconciliation is planned, a row-level Stale can be returned after the reconciliation port has run.
The token MUST be derived from the item's payload digest plus its input
digests; for a volatility item, which carries no input digests, it MUST cover the proposed tier and the bytes of `openkos.yaml`, which determine the currently effective tier, so any configuration change makes it Stale; inside the commit section the row's recorded tier is also compared with the effective one. A caller MUST be able to treat
the token as opaque. No lock MUST be held between the two phases.

If the token no longer matches, the commit MUST return the typed Stale result
and MUST write nothing. A caller that holds no state other than the token and
the `decision_key` MUST be able to complete a commit: every other input the
re-preview needs MUST come from the open row's payload, from the group selection the token carries, or from a default policy the caller MAY override, never from state the caller must remember. A token
whose payload digest is absent, because no queue row backs the item, MUST be
rejected as Stale on this path.

A merge-group token MUST carry the group selection it was previewed with, and its content half MUST cover that selection, so a token whose selection was altered after the preview is the typed Stale result `content_changed`. A token for any other action MUST carry no selection.

A decision key that no queue row of any status ever carried MUST be reported as
the typed Stale result `unknown_key`, in both the preview and the commit phase;
`row_resolved` is reserved for a key whose row was settled or retired. When the
queue exists but cannot be read, the key MUST NOT be reported as never having
existed.
(Previously: every re-preview input came from the row's payload or a default policy; no token carried a caller's choice.)

#### Scenario: A current token commits

- GIVEN a preview of an identity merge and no change since
- WHEN the client commits with the previewed token
- THEN the merge is written and the row is resolved

#### Scenario: A superseded token returns Stale and writes nothing

- GIVEN a preview of an identity merge, and then a member concept edited
- WHEN the client commits with the previewed token
- THEN the typed Stale result is returned, no concept or queue row changes,
  and no commit is made

#### Scenario: A volatility item goes stale when the effective tier changes

- GIVEN a preview of a tier change and then the effective tier changed
  elsewhere
- WHEN the client commits with the previewed token
- THEN the typed Stale result is returned and `openkos.yaml` is unchanged

#### Scenario: Preview writes nothing

- GIVEN any write action
- WHEN the client previews it
- THEN no bundle file, sidecar, queue row or configuration changes

#### Scenario: A key that never existed is Stale as unknown

- GIVEN a decision key that no queue row ever carried
- WHEN the client previews or commits it
- THEN the typed Stale result `unknown_key` is returned and nothing is written

#### Scenario: An altered group selection is Stale

- GIVEN a merge-group preview of a group of three, marking two members
- WHEN the client commits a token whose selection marks all three
- THEN the typed Stale result `content_changed` is returned and nothing is
  written

## ADDED Requirements

### Requirement: A Group Selection Names The Marked Members And The Survivor

A merge-group preview MUST take a group selection: the survivor's Concept ID and
the Concept IDs of the members marked the same, the survivor included. The
selection MUST be valid only when every marked member and the survivor are
members of the item's group, the survivor is among the marked members, and at
least two distinct members are marked. The marked members MUST be compared as a
set: their order and a repeated id do not change the selection, and the token
carries them in ascending order. A missing selection on a merge-group request,
an invalid one, and a selection supplied with any other action MUST each be the
typed not-applied result `invalid_selection`, returned, not raised, writing
nothing. The surface MUST NOT choose a survivor for a merge-group.

#### Scenario: A survivor outside the marked members is invalid

- GIVEN a judged SAME group of A, B and C
- WHEN a client previews merge-group with survivor C and marked members A and B
- THEN the typed not-applied result `invalid_selection` is returned

#### Scenario: A member outside the group is invalid

- GIVEN a judged SAME group of A, B and C
- WHEN a client previews merge-group marking A and D
- THEN the typed not-applied result `invalid_selection` is returned

#### Scenario: One marked member is invalid

- GIVEN a judged SAME group of A, B and C
- WHEN a client previews merge-group marking only A, with A as survivor
- THEN the typed not-applied result `invalid_selection` is returned

#### Scenario: A selection with merge is invalid

- GIVEN a judged SAME pair of one OKF type
- WHEN a client previews merge with a group selection
- THEN the typed not-applied result `invalid_selection` is returned

#### Scenario: Order does not change the selection

- GIVEN a judged SAME group of A, B and C
- WHEN a client previews merge-group marking C, A, A with survivor A, and again
  marking A, C with survivor A
- THEN both previews carry the same token

### Requirement: A Group Decision Merges The Marked Members Pairwise Into The Survivor

Committing a merge-group MUST merge each marked member other than the survivor
into the survivor, one pairwise merge per absorbed member, in ascending Concept
ID order of the absorbed members, through the same merge write a pairwise merge
uses. Each step MUST write its own merge ledger entry, so each step is
reversible with `unmerge`, and `unmerge --to` the first absorbed member unwinds
the whole group, together with any later merge recorded on the survivor. The preview MUST state the survivor, its OKF type, and, per
step, the absorbed member, its OKF type, the number of other documents the step
rewrites, and what happens to the absorbed type: the same type as the survivor;
recorded as the survivor's `type_alternative`; discarded because the survivor
already records one; or unknown because a type cannot be read. Type outcomes
MUST account for the earlier steps of the same group, and MUST equal what the
commit writes. The preview's per-step document counts MAY be computed against
the bundle before the first step. Members not marked MUST NOT be written by any
step.

The group's queue row MUST resolve `applied` once the first step has been
written: as proposed when every member of the group was marked, modified
otherwise. It MUST NOT be left `stale` by the first step's retirement of rows
naming the absorbed member. A token replayed after a commit MUST be the typed
Stale result `row_resolved`.

#### Scenario: Three marked members merge in two steps

- GIVEN a judged SAME group of A, B and C, all marked, with survivor B
- WHEN the client commits the previewed token
- THEN A is merged into B and then C is merged into B, B holds two ledger
  entries, and A and C no longer exist

#### Scenario: Each step is reversible

- GIVEN a committed merge-group of A, B and C into B
- WHEN the operator runs `openkos unmerge B C` and then `openkos unmerge B A`
- THEN A, B and C are restored to their bytes before the group was committed

#### Scenario: The group row resolves as modified for a partial selection

- GIVEN an open identity row over A, B, C and D, judged SAME
- WHEN a client commits merge-group marking A, B and C with survivor A
- THEN the row is `applied` as modified, not `stale`

#### Scenario: The group row resolves as proposed when every member is marked

- GIVEN an open identity row over A, B and C, judged SAME
- WHEN a client commits merge-group marking all three
- THEN the row is `applied` as proposed

#### Scenario: A replayed token is stale

- GIVEN a committed merge-group
- WHEN the client commits the same token again
- THEN the typed Stale result `row_resolved` is returned and nothing is written

#### Scenario: The previewed type outcome is the written one

- GIVEN a group of A (Person), B (Organization) and C (Place), all marked, with
  survivor A, which records no `type_alternative`
- WHEN the client previews and then commits merge-group
- THEN the preview says B's type is recorded and C's is discarded, and A's
  `type_alternative` is `Organization` after the commit

### Requirement: Members Not Marked Are Kept Distinct From The Survivor

Committing a merge-group MUST leave every member not marked unchanged, and,
after every step has been written, MUST record a keep-distinct ruling over the
survivor and each unmarked member, and, when two or more members are unmarked,
one ruling over the survivor and all of them. The rulings MUST be written through
the same writer the keep-distinct action uses, and MUST decline any open
identity row over exactly the same members. A ruling that cannot be recorded
MUST NOT undo the merges: it is fail-open, reported through the warning sink, and
the applied result MUST list only the rulings that were recorded. A merge-group
whose every member is marked MUST record no ruling. The preview MUST list the
members that will be kept distinct.

#### Scenario: Two unmarked members are kept distinct from the survivor

- GIVEN a judged SAME group of A, B, C and D
- WHEN a client commits merge-group marking A and B with survivor A
- THEN B is merged into A, C and D are byte-identical to before, and rulings
  are recorded over A and C, over A and D, and over A, C and D

#### Scenario: A failed ruling keeps the merge

- GIVEN a merge-group with one unmarked member and a ruling write that fails
- WHEN the client commits
- THEN the merge is written and committed, the warning sink receives one
  warning, and the applied result lists no ruling

### Requirement: A Cross-Type Group Merges Only Into The Survivor The Selection Names

A merge-group whose marked members declare different OKF types MUST merge into
the survivor the selection names and into no other. The surface MUST NOT offer
merge for such a group and MUST NOT derive a survivor for it. The preview MUST
carry the cross-type concern computed over the marked members, so the warning is
part of what the person consents to, and the survivor MUST record an absorbed
type as its `type_alternative` exactly as `openkos merge --include-cross-type`
does for the same pair. Bulk eligibility is unchanged: identity, merge-group
included, is never accepted in bulk, and neither the accept-recommended answer
nor `--auto-merge` reaches a merge-group.

#### Scenario: The chosen type survives

- GIVEN a judged SAME pair of A (Person) and B (Organization)
- WHEN a client commits merge-group with survivor B
- THEN A is merged into B, B keeps type `Organization` and records
  `type_alternative: Person`

#### Scenario: The concern is in the preview

- GIVEN a judged SAME pair of A (Person) and B (Organization)
- WHEN a client previews merge-group
- THEN the preview carries a cross-type concern naming both types

#### Scenario: Identity stays outside bulk

- GIVEN the bulk-eligibility table and `item_consent`
- WHEN a caller asks about an identity item offering merge-group
- THEN it is never bulk-eligible and its consent is always asked per item

### Requirement: A Group Decision Is Checked Once, Holds The Lock Once And Commits Once

A merge-group MUST be one decision with one token. Its content half MUST cover
the selection, every byte each step's plan rests on as previewed from the
current bundle, and the bytes of every unmarked member; it MUST be compared once,
before the commit section opens. The whole group MUST be committed inside one
commit section: the row half (`row_resolved`, `row_superseded`) is re-read once,
every member, marked or not, is confirmed present (a vanished one is the
returned not-applied result `member_missing`, writing nothing), the first step's
drift, catalog and read-drift checks run (raised as drift, writing nothing), and
`row_inputs_changed` is checked once, all before the first write. Steps after the
first MUST be prepared inside the same section, against the bytes the earlier
steps wrote. A held lock beyond the caller's wait MUST be the existing
workspace-busy error, raised before anything is written. The decision MUST make
one auto-commit holding every path its steps and rulings wrote.

When a step after the first cannot be prepared or written, the surface MUST stop,
MUST keep and auto-commit the steps already written, MUST NOT record any ruling,
and MUST raise a typed write failure for an interrupted group that carries the
completed (survivor, absorbed) pairs, the commit identity, if any, and the
absorbed member whose step failed. A failure of the first step's write MUST be
the ordinary typed write failure.

#### Scenario: A held lock writes nothing

- GIVEN the workspace lock held by another process and a wait of zero
- WHEN a client commits a merge-group
- THEN the workspace-busy error is raised and no step is written

#### Scenario: An edit before the section is stale

- GIVEN a merge-group preview and then an unmarked member edited
- WHEN the client commits
- THEN the typed Stale result `content_changed` is returned and nothing is
  written

#### Scenario: One commit holds the whole group

- GIVEN a judged SAME group of A, B, C and D, marking A, B and C with survivor A
- WHEN the client commits
- THEN exactly one git commit is made, holding both steps' paths and the
  rulings' paths

#### Scenario: A failure at the second step keeps the first

- GIVEN a merge-group of A, B and C into A whose second step's write fails
- WHEN the client commits
- THEN B is merged into A and committed, C is unchanged, no ruling is
  recorded, and the interrupted-group failure is raised naming A and B as
  completed and C as the failed step
