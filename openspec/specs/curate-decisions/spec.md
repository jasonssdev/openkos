# Curate Decisions Specification

## Purpose

The pending-work queue holds every open curation decision as durable data. This
capability is the `application/` surface that lets a caller list those open
decisions and apply one chosen action to one item, without importing
`openkos.cli`. It returns plain data and never prompts, prints, reads a TTY or
raises `typer.Exit`. The `curate` command is its first client; a desktop
review screen is the next.

The surface is internal. It is not a published or stable API: the freeze is
owned by a later MVP 6 stage. It lists and applies; it never computes a
judgment. Judging is not part of this surface: the `judging-application-service`
capability is where a pending-evaluation item becomes decidable through this
surface, and this surface lists and applies the result.

## Requirements

### Requirement: The Surface Is Plain Data With No Interaction

The surface MUST return plain data for every list and apply call. Every
outcome of an apply MUST be a typed result, returned or raised as a typed
exception, never a `typer.Exit` and never printed. It MUST NOT
prompt, print, read a TTY, call `isatty`, write to stdout or stderr, or raise
`typer.Exit`. It MUST NOT import `typer`, `rich`, `openkos.cli` or
`openkos.vcs`. Anything a client needs to render, including notices that today
appear on a stream during a write, MUST reach the client as returned data or
as observer events, never as direct output.

#### Scenario: A list call produces no output

- GIVEN a workspace with open decisions
- WHEN a client lists the open decisions
- THEN the call returns data and writes nothing to stdout or stderr

#### Scenario: Refusals are typed, not exited

- GIVEN an apply that cannot proceed
- WHEN the client calls it
- THEN a typed result is returned, or a typed exception is raised, and no
  `typer.Exit` is raised

#### Scenario: The layering guard holds

- GIVEN the module that implements the surface
- WHEN the layering test inspects its imports
- THEN none of `typer`, `rich`, `openkos.cli` or `openkos.vcs` is imported, and
  a dedicated AST assertion in the layering test confirms the module never
  calls `isatty`

### Requirement: The List Returns All Six Queue Kinds

Listing MUST read the pending queue without calling a model and without cost.
It MUST return open items of all six kinds: `identity`, `relation_type`,
`volatility`, `contradiction`, `revision` and `watch_refusal`. The list order MUST be
deterministic: the `pq.KINDS` order, then the queue's own order within a kind.
Each item MUST carry its stable `decision_key`, its kind, its payload, its targets and its
action set. The `decision_key` MUST be the handle; the queue store's
autoincrement id MUST NOT be used as one, because that store is a derived
cache.

#### Scenario: Every kind is listed

- GIVEN open rows of all six kinds
- WHEN a client lists the open decisions
- THEN each kind is represented, each item identified by its `decision_key`

#### Scenario: Listing needs no model

- GIVEN an unreachable model backend
- WHEN a client lists the open decisions
- THEN the list is returned in full

#### Scenario: An empty queue lists nothing

- GIVEN a workspace with no open rows
- WHEN a client lists the open decisions
- THEN an empty list is returned and no error is raised

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

### Requirement: Identity Items Are Mergeable Unless They Await A Judgment Or Break A Guard

The judge is advisory and the person is the authority. An identity item MUST carry the merge action when it holds a persisted judgment (SAME, DIFFERENT or UNCERTAIN) over exactly two members that pass the cross-type check. A judged item with more than two members and a cross-type pair carry no merge action and name the reason; every identity item keeps keep-distinct and skip. An identity item whose adjudication is null MUST be listed as
pending evaluation and MUST carry no merge action. An identity item whose persisted judgment is UNCERTAIN, including one produced by a malformed model reply, MUST be listed as `judged_uncertain`, MUST carry the merge action over a passing pair, and MUST NOT be listed as pending evaluation; an item judged DIFFERENT MUST likewise be listed as `judged_different` and carry the merge action over a passing pair. A merge against a DIFFERENT or UNCERTAIN verdict is applied and undone exactly as one against SAME. The surface MUST NOT merge
an unjudged item on a caller's decision alone, and MUST NOT call a model to
judge it.

The merge-group action MUST be carried exactly by a judged-SAME identity item whose merge is withheld for `more_than_two_members` or `cross_type`; merge stays withheld there, with the same reason. An identity item whose group has more than two members, or whose members' OKF types differ, and whose judgment is not SAME MUST list merge-group as withheld with the same reason as merge (`pending_evaluation`, `judged_different` or `judged_uncertain`). An identity item of two members of one OKF type MUST NOT carry or withhold merge-group. A merge-group request against an item that does not carry it MUST be the typed not-applied result `not_mergeable` when the item withholds it and `not_offered` otherwise, and MUST write nothing.
(Previously: a judged-SAME group of more than two members and a cross-type pair carried no merging action at all, and resolved only through the CLI.)
(Previously: only a judged-SAME pair carried the merge action; a pair judged DIFFERENT or UNCERTAIN withheld it, leaving the person no way to combine two pages they knew were the same.)

The merge-group action MUST also be carried by an identity item of the LOW tier with more than two members (a near-match cluster) whose persisted judgment is DIFFERENT or UNCERTAIN, with merge withheld for `more_than_two_members`; a cluster not yet judged MUST withhold merge-group as `pending_evaluation`. An identity item of the HIGH or ACRONYM tier with more than two members MUST keep merge-group withheld against a DIFFERENT or UNCERTAIN verdict.
(Previously: merge-group was carried only by a judged-SAME item. A near-match cluster replaces the pairs that carried merge on any verdict, so withholding it would have taken that ability away.)

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

- GIVEN an open identity item whose persisted judgment is UNCERTAIN over two
  members of one OKF type
- WHEN a client lists it
- THEN its evaluation is `judged_uncertain`, its actions are merge, keep-distinct
  and skip, nothing is withheld, and it is not marked pending evaluation

#### Scenario: A different item still offers the merge

- GIVEN an open identity item whose persisted judgment is DIFFERENT over two
  members of one OKF type
- WHEN a client lists it
- THEN its evaluation is `judged_different` and its actions are merge,
  keep-distinct and skip

#### Scenario: A merge against an uncertain or different item is applied

- GIVEN an open identity item judged UNCERTAIN or DIFFERENT over two members of
  one OKF type
- WHEN a client previews and applies merge with the version it was issued
- THEN the merge is applied as it is for a SAME item, and can be undone

#### Scenario: An uncertain group still withholds the merge

- GIVEN an open identity item judged UNCERTAIN over three members
- WHEN a client lists it
- THEN its actions are keep-distinct and skip, and merge and merge-group are
  withheld with the reason `judged_uncertain`

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

#### Scenario: A near-match cluster judged uncertain offers the group walk

- GIVEN an open LOW-tier identity item over three members judged UNCERTAIN or DIFFERENT
- WHEN a client lists it
- THEN its actions are merge-group, keep-distinct and skip, and merge is withheld as `more_than_two_members`

#### Scenario: An unjudged near-match cluster withholds the group walk

- GIVEN an open LOW-tier identity item over three members with no judgment
- WHEN a client lists it
- THEN merge and merge-group are withheld as `pending_evaluation`

#### Scenario: An exact-title group judged uncertain still withholds the group walk

- GIVEN an open HIGH-tier identity item over three members judged UNCERTAIN
- WHEN a client lists it
- THEN merge and merge-group are withheld as `judged_uncertain`

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

### Requirement: The Identity Merge Preview Discloses A Planned Reconciliation

The identity merge preview MUST include whether a reconciliation call is
planned. When one is planned, it MUST run only after the caller has chosen to
commit, before the drift re-check, through an injected reconciliation port. The
surface MUST NOT construct a model client itself. When none is planned, the
commit MUST NOT call the port.

#### Scenario: The preview reports a planned reconciliation

- GIVEN an identity pair whose merge would reconcile
- WHEN the client previews the merge
- THEN the preview reports that a reconciliation is planned, and no model is
  called

#### Scenario: No reconciliation, no port call

- GIVEN an identity pair whose preview reports none planned
- WHEN the client commits
- THEN the reconciliation port is not called

### Requirement: Commit Re-Checks Members, Drift And The Catalog Before Writing

An identity merge commit MUST, inside the commit section and in this order,
confirm both members still exist, re-check the target bytes against the bytes
the preview read, recompose the catalog, and re-check for read drift, before
calling the merge write. A vanished member MUST be a returned not-applied
result. A drifted target, a catalog recompose failure and read drift MUST each
be raised as the typed drift exception, whose message states which check
failed. Every failed check MUST write nothing. A write failure MUST be a raised typed exception with structured fields. A tier commit that the adapter refuses to make on its own (uncommitted edits to `openkos.yaml`) is raised as the typed refusal carrying the adapter's text. Each typed result MUST carry
structured fields; it MUST NOT embed `openkos <verb>:`
text meant for a terminal.

#### Scenario: A vanished member is refused

- GIVEN a preview and then one member concept deleted
- WHEN the client commits
- THEN a typed not-applied result with reason `member_missing`, naming the
  member, is returned and nothing is written

#### Scenario: Drifted bytes are detected

- GIVEN a preview and then a member concept edited after the token check
  would pass
- WHEN the client commits
- THEN the typed drift exception naming the drifted targets is raised and
  nothing is written

#### Scenario: A write failure is typed

- GIVEN a commit whose merge write fails
- WHEN the client commits
- THEN the typed write-failure exception is raised and no exception about
  terminal exit is raised

### Requirement: The Adapter Owns Rendering, Exit Codes And Commits

The surface MUST take the commit section, the workspace commit and the
reconciliation as injected ports. The surface MUST NOT choose exit codes. The
`curate` adapter MUST map a drift result to exit 3 ending the run, and a write
failure to exit 1, and MUST keep the terminal commit helpers
(`_autocommit`, `_reject_drifted_targets`, `_echo_commit_disclosure`) as one
definition each on the adapter side. Notices that appear on a stream today MUST
be produced by the adapter at the same points in the sequence: the merge-skip
line, `committed as <sha>` after the lock is released, the reconciliation
notices, and the section break, so that the command's output stays
byte-identical.

#### Scenario: Drift keeps exit 3

- GIVEN a drift exception raised from a commit during `curate`
- WHEN the adapter handles it
- THEN `curate` exits 3 and no later item or stage runs

#### Scenario: A commit sha is printed after the lock exits

- GIVEN a successful identity merge during `curate`
- WHEN the adapter renders it
- THEN `committed as <sha>` appears after the commit section has been released

### Requirement: Keep-Distinct Records A Durable Ruling

The keep-distinct action on an identity item MUST record the permanent
keep-distinct ruling and MUST resolve the row `declined`. The surface
MUST write the ruling sidecar and commit through the injected autocommit port,
as the walk's decline helper does today. A keep-distinct on an item whose members no longer exist MUST NOT record a ruling. A failure to record the ruling is fail-open: it is returned as `NotApplied` with reason `record_failed` and notified to the observer, not raised.

#### Scenario: Keep-distinct records the ruling and declines the row

- GIVEN an open identity item
- WHEN a client applies keep-distinct
- THEN the ruling sidecar is written and the row is `declined`

#### Scenario: A vanished member records nothing

- GIVEN an open identity item and one member concept deleted
- WHEN a client applies keep-distinct
- THEN no ruling is written and the typed not-applied result with reason
  `member_missing` is returned

### Requirement: Structure And Metadata Actions Resolve Their Rows

Relate as proposed MUST write the relation through the `relate` core and MUST
resolve the row `applied` as proposed. Relate reversed MUST write the opposite
direction and MUST resolve the row `applied` as modified. Setting a tier MUST
write through the `set-volatility` core into `openkos.yaml` and MUST resolve
the row applied. Neither kind has a decline action in this surface: a relation
or tier that the operator rejects is not recorded and may be offered again on a
later run. This is an existing limitation and MUST NOT be hidden by the
surface.

#### Scenario: Relate as proposed resolves as proposed

- GIVEN an open relation_type item
- WHEN a client previews and commits relate as proposed
- THEN the relation is written and the row is `applied` as proposed

#### Scenario: Relate reversed resolves as modified

- GIVEN an open relation_type item with an asymmetric type
- WHEN a client previews and commits relate reversed
- THEN the edge is written in the opposite direction and the row is `applied`
  as modified

#### Scenario: Setting a tier writes the configuration

- GIVEN an open volatility item
- WHEN a client previews and commits the proposed tier
- THEN the tier is written to `openkos.yaml` through the `set-volatility` core
  and the row is `applied`

### Requirement: Consent And Bulk Eligibility Are Data

The surface MUST expose consent as data. Whether a stage can be accepted in
bulk MUST be a table keyed by kind, not logic spread across callers, with
these entries: `identity` never; `relation_type` yes, except an asymmetric relation type, which is never; `volatility` yes (the table decides which kinds may be accepted in bulk; a per-item exemption flag, `asymmetric` in `item_consent`, decides which items of an accepted kind still ask, and the shipped command sets it only for asymmetric relations); `contradiction`, `revision`
and `watch_refusal` have no actions and so are not eligible. Whether a spend
or a write is consented, given the stage, whether a terminal is present, `--auto`, the accepted stages and whether the unattended identity pass applies (which the adapter derives from `--auto-merge`), MUST be answered by a pure
function that does not prompt. The outcomes MUST equal those the `curate`
requirements already define; this requirement MUST NOT add, remove or relax a
consent rule. The surface offers no bulk apply for identity merges. The separate accept-recommended and `--auto-merge` paths keep their own consent and stay outside the table: accept-recommended writes each merge through the identity merge action, while `--auto-merge` keeps the automatic pass (`apply_auto_merges`), which does not use that action and commits once per run.

#### Scenario: Identity is never bulk-eligible

- GIVEN the bulk-eligibility table
- WHEN a caller asks whether `identity` can be accepted in bulk
- THEN the answer is no, whatever the other flags

#### Scenario: An asymmetric relation is never bulk-eligible

- GIVEN a relation_type item with an asymmetric type
- WHEN a caller asks whether it can be accepted in bulk
- THEN the answer is no

#### Scenario: Consent outcomes match the existing matrix

- GIVEN every combination of stage, terminal present, `--auto`, `--accept`
  and `--auto-merge`
- WHEN the pure consent function is evaluated
- THEN each outcome equals the outcome `curate` produced for that combination
  before the surface existed

### Requirement: The Surface Computes No Judgment

The surface MUST NOT call a model to decide whether, or how, an item should be
resolved. Judging, the cost gate and the candidate probes MUST remain outside
the surface; judging is provided by the `judging-application-service` capability,
through which an item pending evaluation becomes decidable. The only model-backed
step reachable from an apply is the injected reconciliation port of an identity
merge.
(Previously: judging was named only as out of scope and a separate follow-up, with no capability named as the place a pending-evaluation item becomes decidable.)

#### Scenario: List and skip need no model

- GIVEN no model backend configured
- WHEN a client lists the open decisions and applies skip
- THEN both succeed

#### Scenario: No judge is started by the surface

- GIVEN an identity item pending evaluation
- WHEN a client lists it
- THEN it stays pending evaluation and no judging runs
### Requirement: Payloads Are Owner-Local

Item payloads, such as rationales and conflicting claims, derive from concept
text and may contain confidential content. The surface MUST be usable only by
the owner's local process. The MCP server MUST keep its reduced `pending`
projection (kind, targets and resolving command, withholding rows with a
non-disclosable target) and MUST NOT import or call this surface unless it
adds its own disclosure gate. The surface MUST NOT weaken sensitivity
handling in any write it performs.

#### Scenario: MCP does not expose payloads

- GIVEN an open row whose payload derives from a confidential concept
- WHEN the MCP `pending` tool lists the queue
- THEN no payload text is returned

#### Scenario: The MCP package does not import the surface

- GIVEN the `mcp/` package
- WHEN the layering test inspects its imports
- THEN it does not import the curate-decisions module, and the guard passes
  when `src/openkos/api/` does not yet exist

### Requirement: Confidence Is Not Presented As A Trust Signal

The surface MUST NOT present an adjudicator's confidence value as evidence
that a judgment is correct. If an item carries a confidence, it MUST be
returned as the stored value, unlabeled as a measure of trust, and the
surface MUST NOT sort, rank, filter or recommend by it.

#### Scenario: Items are not ranked by confidence

- GIVEN open identity items with different stored confidences
- WHEN a client lists them
- THEN the list order does not depend on confidence and no item is labeled
  trusted or recommended by it

### Requirement: Behavior Of The Command Is Unchanged

Routing the `curate` walks through the surface MUST NOT change any stdout,
stderr, exit code, stream ordering, on-disk format, queue schema or consent
rule of `curate`. Characterization goldens recorded before the surface exists
MUST pass unchanged after each slice that moves a walk onto it. A behavior change
specified for the command itself is not a routing move: the Identity group walk
(`curate-command`: "Identity Walks A Group Through The Group Decision") replaces
the skip lines for groups whose row offers merge-group.
(Previously: no exception was stated, because no walk had gained behavior.)

#### Scenario: Goldens pass unchanged

- GIVEN characterization goldens recorded on the tree before any move
- WHEN each slice that rewires a walk is complete
- THEN stdout, stderr and exit codes match the goldens byte for byte in every
  recorded scenario

### Requirement: A Persisted Verdict Written Through To An Open Row Is Reflected In The List

When a verdict is persisted for the member set of an open identity row, the row
MUST be updated in place so the listed item reflects that verdict, without a new
`decision_key`. A SAME verdict MUST make the item mergeable under the existing
checks; a DIFFERENT verdict keeps resolving the row as `declined` as it does today;
an UNCERTAIN verdict MUST list the item as `judged_uncertain`. The write-through
MUST update only an open row: it MUST NOT create a row where none is open, MUST NOT
reopen a resolved row and MUST NOT resolve a row. A verdict whose members no longer
hash to what was judged MUST NOT be written through. The effect on the queue's
lifetime counters is defined by `pending-work`.

#### Scenario: A SAME verdict makes the item mergeable

- GIVEN an open identity item pending evaluation over two members that pass the
  cross-type check
- WHEN a SAME verdict is persisted for exactly those members
- THEN a later list shows the same `decision_key` with the merge action present

#### Scenario: An UNCERTAIN verdict lists as judged uncertain

- GIVEN an open identity item pending evaluation
- WHEN an UNCERTAIN verdict is persisted for exactly its members
- THEN a later list shows the item as `judged_uncertain`

#### Scenario: Nothing is created for an item with no open row

- GIVEN a persisted verdict for a member set that holds no open row
- WHEN the write-through runs
- THEN no row is created

#### Scenario: A resolved row is not reopened

- GIVEN an identity row already `applied` or `declined`
- WHEN a verdict is persisted for its members
- THEN the row keeps its status

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
