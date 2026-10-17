# Delta for curate-decisions

## MODIFIED Requirements

### Requirement: Each Kind Carries Its Own Action Set

Items of kinds `identity`, `relation_type`, `volatility`, `contradiction` and
`revision` MUST carry actions, and items of kind `watch_refusal` MUST be listed
with an empty action set. A finding's directed actions are named relative to the
item's `targets` order: `supersede` and `revise` make the first target the holder
of the edge, and their `_reversed` forms the second. The actions MUST be:

- identity: merge, keep-distinct, skip; and merge-group in place of merge for a group of more than two members or of members whose OKF types differ (see "Only Judged-SAME Identity Items Are Mergeable");
- relation_type: relate as proposed, relate reversed (only for an asymmetric relation type), skip; a row whose suggested type is `supersedes` carries no action and resolves through `openkos relate`;
- volatility: set the proposed tier, skip;
- contradiction over a typed edge: decline, reconcile, supersede, supersede reversed, skip;
- contradiction found inside a combined page (it carries a merged absorbed id): decline and skip, with reconcile withheld for the reason `merged_content`;
- revision whose dates order the pair: the one directed write the dates and the verdict name (supersede for a reversal, revise for a refinement, held by the later Decision, in whichever form makes it the holder), reconcile, skip;
- revision whose dates do not order the pair: supersede, supersede reversed, revise, revise reversed, reconcile, skip;
- a contradiction or revision row whose payload cannot be read: no action, with reconcile withheld for the reason `no_valid_suggestion`.

Skip MUST write nothing and MUST leave the row `pending`. An item with an
empty action set MUST name where it resolves: a `watch_refusal` through
`openkos ingest`.
(Previously: contradiction and revision items carried no action and named `openkos contradictions --decline`, `openkos reconcile` or `openkos unmerge`, and their producer, as where they resolve.)

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

#### Scenario: A typed-edge contradiction offers the three resolutions and decline

- GIVEN an open contradiction item over a typed edge
- WHEN a client lists it
- THEN its actions are decline, reconcile, supersede, supersede reversed and skip

#### Scenario: A merged-body contradiction offers decline only

- GIVEN an open contradiction item that carries a merged absorbed id
- WHEN a client lists it
- THEN its actions are decline and skip, and reconcile is withheld with the
  reason `merged_content`

#### Scenario: A dated reversal offers the one write its dates name

- GIVEN an open revision item with the verdict reverses, whose second Decision is
  dated later than its first
- WHEN a client lists it
- THEN its actions are supersede reversed, reconcile and skip

#### Scenario: An undated revision offers every direction

- GIVEN an open revision item whose dates do not order the pair
- WHEN a client lists it
- THEN its actions are supersede, supersede reversed, revise, revise reversed,
  reconcile and skip

#### Scenario: A watch_refusal is listed without actions

- GIVEN an open watch_refusal item
- WHEN a client lists it
- THEN its action set is empty and it names `openkos ingest`

#### Scenario: Skip leaves the row pending

- GIVEN an open identity item
- WHEN a client applies skip
- THEN nothing is written and the row is still `pending`

### Requirement: Staleness Is Marked, Never Hidden

The list MUST return open rows whose inputs have changed since they were
computed, marked stale, rather than omitting them. A stale item MUST NOT be
applied silently: an apply against it MUST return the typed Stale result.

A preview MUST agree with the apply about a stale item: previewing any write
action of an open row whose inputs have changed, through the decision preview
or the finding preview, MUST return the typed Stale result `row_inputs_changed`,
the reason the apply returns, and MUST write nothing. An action the item does
not take MUST still be refused as not applied, and skip, which writes nothing,
MUST still return its not-applied result. The list MUST keep reporting the stale
item's actions unchanged.

Freshness MUST be computed for the kinds that carry actions. A `contradiction`
row is fresh when every input it recorded that names a concept still hashes as
recorded (an input the engine cannot currently read, such as a merged-body
snapshot label, counts as unchanged, as the contradiction reader decides) and its
pair's concepts exist (only the first for a merged-body row). A `revision` row is
fresh when the input digests recomputed for its pair, the two Decisions and the
Sources they reach, equal the recorded ones exactly, as the revision reader
decides. A `watch_refusal`, which carries no action, MUST report freshness as not
computed (`fresh` is `None`, never `True`).
(Previously: contradiction and revision items also reported freshness as not computed.)

#### Scenario: A row whose input changed is listed as stale

- GIVEN an open identity row and a member concept edited since the row was
  computed
- WHEN a client lists the open decisions
- THEN the row is present and marked stale

#### Scenario: An edited page makes its contradiction stale

- GIVEN an open typed-edge contradiction row and one of its pages edited since
  the row was computed
- WHEN a client lists the open decisions
- THEN the row is present and marked stale, and previewing reconcile returns
  Stale `row_inputs_changed`

#### Scenario: An edited Decision makes its revision stale

- GIVEN an open revision row and one of its Decisions edited since the row was
  computed
- WHEN a client lists the open decisions
- THEN the row is present and marked stale

#### Scenario: A stale item's preview is the apply's Stale

- GIVEN an open judged-same identity row whose member was edited since the row
  was computed
- WHEN a client previews merge or keep-distinct on it
- THEN the preview returns Stale `row_inputs_changed` and nothing is written

#### Scenario: Skip on a stale item is still skip

- GIVEN an open stale row
- WHEN a client previews skip
- THEN the preview returns the not-applied result `skipped`

#### Scenario: A refused file reports freshness as not computed

- GIVEN an open watch_refusal item
- WHEN a client lists the open decisions
- THEN the item's freshness is reported as not computed, not as fresh


## ADDED Requirements

### Requirement: A Finding Is Settled Through The Write Path Its CLI Verb Uses

The finding actions MUST be previewed and committed on the token path, through
the write cores the CLI uses, never a second implementation:

- decline MUST record the pair's contradiction decision as `declined` through
  the writer `contradictions --decline` uses, keyed by the sorted pair and the
  merged absorbed id, commit only the sidecar it wrote, and resolve the row
  `declined`;
- reconcile, supersede, supersede reversed, revise and revise reversed MUST write
  through the transaction `reconcile` uses: a symmetric `reconciled_with`, or one
  `supersedes` or `revises` edge on the holder, with its notes, its log entry and,
  for `supersedes`, the counterpart's deprecated-status export; and commit the
  two pages and `log.md` only.

The preview MUST plan without writing, take no lock and call no model; its
version MUST cover the bytes of every page the action rests on and the planned
edge and holder, so a version issued for one direction never applies as
another. A page the action names that no longer exists makes the row stale, so
the preview MUST return Stale `row_inputs_changed`; an apply that finds it gone
MUST return the not-applied result `member_missing` and write nothing. A pair already reconciled in a different way MUST be
the not-applied result `already_reconciled`, at preview and at apply, and MUST
write nothing. The apply MUST re-plan from current bytes, return Stale
`content_changed` when the version moved, and inside its one commit section
re-check that the row is still the open one and still fresh before writing.

#### Scenario: Supersede deprecates the second page

- GIVEN an open typed-edge contradiction over pages A (first) and B
- WHEN supersede is previewed and applied against that version
- THEN A carries a `supersedes` edge to B, B lists as deprecated, the commit holds
  A, B and `log.md`, and the row is `applied`

#### Scenario: A version for one direction does not apply as the other

- GIVEN the version a supersede preview returned
- WHEN it is applied with supersede reversed
- THEN Stale `content_changed` is returned and nothing is written

#### Scenario: Decline records the decision and closes the row

- GIVEN an open merged-body contradiction
- WHEN decline is previewed and applied
- THEN the decision record is `declined`, the commit holds only its sidecar, and
  the row is `declined`

#### Scenario: A pair reconciled differently is refused at preview

- GIVEN an open contradiction over A and B, and A already superseding B
- WHEN reconcile is previewed
- THEN the not-applied result `already_reconciled` is returned and nothing is
  written
