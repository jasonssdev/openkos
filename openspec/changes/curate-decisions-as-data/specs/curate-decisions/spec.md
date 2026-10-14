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
judgment. Judging as an application service is a separate, required follow-up
change that comes after this one and before the desktop review screen, and is
out of scope here.

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

Items of kinds `identity`, `relation_type` and `volatility` MUST carry
actions, and items of kinds `contradiction`, `revision` and `watch_refusal`
MUST be listed with an empty action set. The actions MUST be:

- identity: merge, keep-distinct, skip;
- relation_type: relate as proposed, relate reversed (only for an asymmetric
  relation type), skip;
- volatility: set the proposed tier, skip.

Skip MUST write nothing and MUST leave the row `pending`. An item with an
empty action set MUST name where it resolves: a `contradiction` through
`openkos contradictions --decline`, `openkos reconcile` or `openkos unmerge`;
a `revision` by its producer, which retires it; a `watch_refusal` through
`openkos ingest`.

#### Scenario: An identity item carries merge, keep-distinct and skip

- GIVEN an open identity item judged SAME
- WHEN a client lists it
- THEN its actions are merge, keep-distinct and skip

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

An identity item MUST carry the merge action only when it holds a persisted
SAME judgment. An identity item whose adjudication is null MUST be listed as
pending evaluation and MUST carry no merge action. The surface MUST NOT merge
an unjudged item on a caller's decision alone, and MUST NOT call a model to
judge it.

#### Scenario: A judged SAME item is mergeable

- GIVEN an open identity item with a SAME judgment
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

### Requirement: Staleness Is Marked, Never Hidden

The list MUST return open rows whose inputs have changed since they were
computed, marked stale, rather than omitting them. A stale item MUST NOT be
applied silently: an apply against it MUST return the typed Stale result.

#### Scenario: A row whose input changed is listed as stale

- GIVEN an open identity row and a member concept edited since the row was
  computed
- WHEN a client lists the open decisions
- THEN the row is present and marked stale

### Requirement: Apply Is A Two-Phase Preview And Commit Against A Version Token

Applying a write action MUST be two phases. The preview phase MUST return,
without writing, what the action would do, plus a version token for the item.
The commit phase MUST take the token the caller holds and MUST re-validate it
inside the commit section, where the workspace lock is held, before any write.
The token MUST be derived from the item's payload digest plus its input
digests; for a volatility item, which carries no input digests, it MUST cover
the proposed and the currently effective tier. A caller MUST be able to treat
the token as opaque. No lock MUST be held between the two phases.

If the token no longer matches, the commit MUST return the typed Stale result
and MUST write nothing. A caller that holds no state other than the token and
the `decision_key` MUST be able to complete a commit: every other input the
re-preview needs MUST come from the open row's payload or from a default policy
the caller MAY override, never from state the caller must remember. A token
whose payload digest is absent, because no queue row backs the item, MUST be
rejected as Stale on this path.

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
failed. Every failed check MUST write nothing. A write failure MUST be a raised
typed exception with structured fields. Each typed result MUST carry
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
as the walk's decline helper does today. A keep-distinct on an item whose members no longer exist
MUST NOT record a ruling.

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
these entries: `identity` never; `relation_type` yes, except an asymmetric
relation type, which is never; `volatility` yes; `contradiction`, `revision`
and `watch_refusal` have no actions and so are not eligible. Whether a spend
or a write is consented, given the stage, whether a terminal is present,
`--auto`, the accepted stages and `--auto-merge`, MUST be answered by a pure
function that does not prompt. The outcomes MUST equal those the `curate`
requirements already define; this requirement MUST NOT add, remove or relax a
consent rule. The surface offers no bulk apply for identity merges. The
separate accept-recommended and `--auto-merge` paths keep their own consent
and still write each merge through the identity merge action.

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
the surface. The only model-backed step reachable from an apply is the
injected reconciliation port of an identity merge.

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
MUST pass unchanged after each slice that moves a walk onto it.

#### Scenario: Goldens pass unchanged

- GIVEN characterization goldens recorded on the tree before any move
- WHEN each slice that rewires a walk is complete
- THEN stdout, stderr and exit codes match the goldens byte for byte in every
  recorded scenario
