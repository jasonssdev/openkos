# Judging Application Service Specification

## Purpose

Judging is the model-backed step that turns an unjudged item into one a human can
decide. This capability is the `application/` surface that lets a caller price
judging, consent to that price, and run it, without importing `openkos.cli`. It
follows a quote, consent, run protocol: a caller asks for an LLM-free quote, a
human consents to that quote, and a run judges at most what was quoted, persists
verdicts incrementally, reports progress and can be cancelled. A run ends at
persisted verdicts and at queue rows that carry them, so the item becomes decidable
through the `curate-decisions` surface.

Known limitation: a judged item becomes listable by `curate-decisions` only once a
producer pass (today the daemon) has enqueued or amended its queue row. Identity
write-through is amend-only, and `relation_type` and `volatility` runs write no
queue row. An app-callable refresh is tracked in issue #1391.

The surface covers three kinds: `identity` first, then `relation_type` and
`volatility`. Contradictions and decision revisions are not covered. The quote
carries its kind, so adding a kind later is additive.

The surface is internal. It is not a published or stable API: the freeze is owned
by a later MVP 6 stage. It returns plain data and never prompts, prints, reads a
TTY or raises `typer.Exit`. The `curate`, `adjudicate`, `suggest-relations` and
`suggest-volatility` commands keep their own inline judging and are not clients of
this surface.

## Requirements

### Requirement: The Surface Is Plain Data With No Interaction

Every quote and run call MUST return plain data. Every outcome MUST be a typed
result, returned or raised as a typed exception, never a `typer.Exit` and never
printed. The surface MUST NOT prompt, print, read a TTY, call `isatty`, write to
stdout or stderr, or raise `typer.Exit`. It MUST NOT import `typer`, `rich`,
`openkos.cli` or `openkos.vcs`. Anything a client needs to render, including
advisories that appear on a stream today when a persist fails, MUST reach the
client as returned data or as observer events, never as direct output.

#### Scenario: A quote produces no output

- GIVEN a workspace with items that could be judged
- WHEN a client requests a quote
- THEN the call returns data and writes nothing to stdout or stderr

#### Scenario: A persist failure is data, not a stream line

- GIVEN a run whose chunk cannot be persisted
- WHEN the run reports it
- THEN the failure reaches the client in the typed outcome or as an observer
  event, and nothing is written to stderr

#### Scenario: The layering guard holds

- GIVEN the module that implements the surface
- WHEN the layering test inspects its imports
- THEN none of `typer`, `rich`, `openkos.cli` or `openkos.vcs` is imported, and a
  dedicated AST assertion confirms the module never calls `isatty`

### Requirement: A Quote Is LLM-Free, Kind-Tagged And Costs Nothing

A quote MUST be computed without calling a model and without spend, so it MUST
succeed when the model backend is unreachable. A quote MUST carry its kind and
MUST report `total` (the items considered), `served` (items already answered by a
persisted, still-servable SAME or DIFFERENT verdict), `uncertain` (items holding a
servable UNCERTAIN verdict, in `to_judge` only when opted in), `fresh` (the rest,
with no servable stored verdict), `to_judge` (items a run would judge) and `calls`
(the model calls a run would make), so that `total == served + uncertain + fresh`. A group or item that needs no model
call, such as an identity group with no readable member, MUST cost no call. A quote
MUST also carry the resolved per-task model, the backend and its locality, the
effective confidential flag, and the judgment rubric digest of the kind where the
kind has one. A quote MUST NOT carry an ETA.

An item MUST count as `served` only when its stored verdict is still servable: it
was computed under the current rubric and the matching confidential inclusion, and
every member's current content digest equals the digest stored with the verdict.
An item whose stored verdict is not servable MUST count in `to_judge`.

#### Scenario: A quote reports served and to-judge

- GIVEN five identity groups, two with a servable persisted verdict
- WHEN a client requests an identity quote
- THEN `total` is 5, `served` is 2, `to_judge` is 3 and `calls` is at most 3

#### Scenario: A quote needs no model

- GIVEN an unreachable model backend
- WHEN a client requests a quote
- THEN the quote is returned in full and no model call is attempted

#### Scenario: An edit makes a served verdict need judging again

- GIVEN a group with a persisted verdict and a member edited since
- WHEN a client requests a quote
- THEN the group counts in `to_judge`, not `served`

#### Scenario: A changed rubric makes every stored verdict need judging again

- GIVEN persisted verdicts computed under a rubric that no longer matches the
  current one
- WHEN a client requests a quote
- THEN none of those groups counts as `served`

#### Scenario: A group with no readable member costs no call

- GIVEN an identity group none of whose members can be read
- WHEN a client requests a quote
- THEN the group adds nothing to `calls`

### Requirement: A Quote Token Binds The Context Consent Was Given For

A quote MUST return a token that binds its kind, the resolved model, the backend
and its locality, the effective confidential flag, the rubric digest where the
kind has one, whether judged-uncertain items were opted in, and the set of work
items with each item's member content digests. The token MUST be an immutable value
the caller hands back unchanged; its fields are read-only data and a client MUST NOT
construct or alter one. The quote has no time limit: the token MUST NOT expire with elapsed time,
and whether it is still usable MUST be decided only by comparing what it binds
with the current state.

#### Scenario: The token is stable for an unchanged workspace

- GIVEN two quotes requested with no change in between
- WHEN the tokens are compared
- THEN they bind the same context and work set

#### Scenario: An old quote is not stale by age

- GIVEN a quote and no change to the model, locality, rubric, confidential flag
  or work set
- WHEN a run uses the token after any amount of elapsed time
- THEN the run is not refused as stale

### Requirement: Judged-Uncertain Items Are Excluded From The Quote By Default

A stored UNCERTAIN verdict, including one produced by a malformed model reply,
MUST NOT count in `to_judge` and MUST NOT add to `calls` by default, so the same
item is not judged and paid for again. Such an item MUST be reported to the caller
as left out (its count, so a client can offer the choice). A caller MAY explicitly
opt in to include judged-uncertain items; when it does, those items MUST count in
`to_judge` and their cost MUST appear in `calls` in the quote, before any consent.
The opt-in MUST be bound into the token. The opt-in applies to identity only: a
`relation_type` or `volatility` quote MUST ignore it and bind `include_uncertain=false`,
because those kinds have no UNCERTAIN verdict. A content edit to a member changes its
digest, which MUST return the item to pending judging automatically, whether or not
the opt-in is set.

#### Scenario: An uncertain item is not re-priced by default

- GIVEN an identity group with a servable stored UNCERTAIN verdict
- WHEN a client requests a quote with default options
- THEN the group is not in `to_judge`, adds nothing to `calls`, and is counted as
  left out

#### Scenario: Opting in prices the uncertain item before consent

- GIVEN the same group
- WHEN a client requests a quote with the include-uncertain option
- THEN the group is in `to_judge` and its cost is part of `calls` in the quote

#### Scenario: A run honors the opt-in bound in the token

- GIVEN a token from a default quote
- WHEN a run is requested for that token
- THEN judged-uncertain items are not judged

#### Scenario: An edit returns the item to pending judging

- GIVEN a judged-uncertain item and a member edited since
- WHEN a client requests a default quote
- THEN the item counts in `to_judge`

### Requirement: A Quote Carries Its Notices As Data

A quote MUST carry as returned data: a truncation notice when the candidate set was
capped (a count for identity, rendered `text` for `relation_type`), a quarantine
notice for `relation_type` when edges were quarantined (identity has no quarantine), a model notice naming
the model and backend the run would use, and an unattended notice when the
unattended engine has recent job activity recorded in the workspace's job records,
meaning a job in progress or one that started within a recent window (the window is
a design detail). The job records MUST be read without being created, so a workspace
that never ran the engine has no records and gets no notice. The unattended notice
MUST be informational only: it MUST NOT block, change the price or alter the token.
No notice MUST be printed.

#### Scenario: Notices are returned, not printed

- GIVEN a candidate set that was capped
- WHEN a client requests a quote
- THEN the quote carries the truncation notice as data and nothing is written to a
  stream

#### Scenario: The unattended notice appears only when it applies

- GIVEN one workspace whose job records show recent unattended activity and one
  with no job records
- WHEN a client requests a quote for each
- THEN only the first quote carries the unattended notice, both quotes have the
  same price for the same work, and no job record is created for the second

### Requirement: Consent Is A Reference To The Quote Token

A run MUST be requested by presenting a quote token. The surface MUST NOT ask for
consent, prompt or display the quote; consent is the caller presenting the token of
a quote its human accepted. A run request without a token MUST be refused with a
typed refusal result (returned, not raised) naming that no quote token was
presented, and MUST make no model call and write nothing.

#### Scenario: A run without a token is refused

- GIVEN no quote token
- WHEN a client requests a run
- THEN a typed refusal is returned and no model is called

#### Scenario: The surface never asks the human

- GIVEN a run request with a valid token
- WHEN the run executes
- THEN no prompt is issued and no TTY is read

### Requirement: A Run Never Exceeds Its Quote

A run MUST re-partition the work at run time and MUST judge only the intersection
of the quoted set and the still-needed set. It MUST NOT make more model calls than
the quote's `calls`. Work that is needed now but was not in the quote MUST NOT be
judged and MUST be reported as `not_in_quote` with its count. A quoted item that is
no longer needed, for example because another surface persisted its verdict
meanwhile, because a member was edited so the item is no longer the quoted one, or
because the group vanished or was ruled kept-distinct, MUST NOT be judged and MUST
be counted as settled since the quote.

#### Scenario: New work is not judged

- GIVEN a quote for three groups and a fourth group that appears before the run
- WHEN the run executes
- THEN only quoted groups are judged and the fourth is reported `not_in_quote`

#### Scenario: A quoted item answered elsewhere is not paid for again

- GIVEN a quote for three groups and a persisted verdict for one of them landing
  before the run
- WHEN the run executes
- THEN that group is not judged and is counted as settled since the quote, and at most two
  model calls are made

#### Scenario: A vanished group is settled, not judged

- GIVEN a quoted group that no longer exists when the run starts
- WHEN the run executes
- THEN it is not judged and is counted as settled since the quote

#### Scenario: The call count never exceeds the quote

- GIVEN any quote and any intervening change that adds work
- WHEN the run executes
- THEN the number of model calls is not greater than the quote's `calls`

### Requirement: A Changed Model, Backend, Locality, Rubric Or Confidential Flag Returns QuoteStale

When a run's token binds a model, backend, backend locality, rubric digest or
effective confidential flag that differs from the current one, the run MUST return the typed
`QuoteStale` result naming which of them changed, MUST make no model call and MUST
write nothing. The caller MUST obtain a new quote and new consent. A change of the
work set alone, including new or removed items, MUST NOT return `QuoteStale`: it
follows the binding-ceiling rule instead.

#### Scenario: A model change is stale

- GIVEN a quote and then a change of the resolved model
- WHEN the run executes
- THEN `QuoteStale` names the model, and no model call is made

#### Scenario: A backend change with the same model and locality is stale

- GIVEN a quote and then a swap to a different backend of equal locality serving
  the same model tag
- WHEN the run executes
- THEN `QuoteStale` names the backend and no model call is made

#### Scenario: A locality change is stale

- GIVEN a quote and then a backend change from local to remote
- WHEN the run executes
- THEN `QuoteStale` names the locality and nothing is written

#### Scenario: A rubric change is stale

- GIVEN a quote and then a change of the judgment rubric
- WHEN the run executes
- THEN `QuoteStale` names the rubric and no model call is made

#### Scenario: A confidential flag change is stale

- GIVEN a quote and then a change of the effective confidential flag
- WHEN the run executes
- THEN `QuoteStale` names the confidential flag and no model call is made

#### Scenario: Changed work alone is not stale

- GIVEN a quote and then only a change to the set of needed items
- WHEN the run executes
- THEN no `QuoteStale` is returned and the run judges the intersection

### Requirement: Verdicts Persist Per Chunk Before The Next Chunk Is Judged

A run MUST judge the to-judge list in chunks and MUST persist each chunk's verdicts
before judging the next. Persisting a chunk MUST record the verdicts in the
adjudications store with their member digests and rubric digest, MUST resolve the
open identity row of a DIFFERENT verdict as `declined` as the shipped DIFFERENT
path does, and MUST write the verdict through to the open row of the item (see
`curate-decisions`). A verdict whose member was edited, raised or forgotten during
the model call MUST be dropped, not stored against content nobody judged. A result
with an unreadable member and so no digest MUST NOT be persisted. After an
interruption, a new quote MUST report every persisted chunk's items as `served`.

#### Scenario: A chunk is persisted before the next is judged

- GIVEN a run over two chunks
- WHEN the first chunk is judged
- THEN its verdicts are in the adjudications store before any call is made for the
  second chunk

#### Scenario: A killed run keeps its completed chunks

- GIVEN a run terminated after its first chunk was persisted
- WHEN a client requests a new quote
- THEN the first chunk's items count as `served`

#### Scenario: A verdict about edited content is dropped

- GIVEN a member edited while its group was being judged
- WHEN the chunk is persisted
- THEN that group's verdict is not stored

#### Scenario: A DIFFERENT verdict closes its row

- GIVEN an open identity row and a DIFFERENT verdict persisted for exactly its
  members
- WHEN the chunk is persisted
- THEN the row is `declined` and no decision sidecar is written

### Requirement: Cancellation Takes Effect At Chunk Boundaries

A run MUST accept a stop signal and MUST observe it at each chunk boundary, so a
stop takes effect no later than the end of the chunk that is in flight. Nothing
preempts a model call: the in-flight chunk MUST finish and be persisted, and no
further chunk MAY start. A stop signalled before the first chunk MUST result in no
model call. A stopped run MUST report its remaining quoted items as `deferred`, and
the work already persisted MUST remain. The worst-case latency of a stop MUST be
stated by the surface's documentation as the in-flight chunk's calls times the
configured per-call chat timeout; it is at least one model call.

#### Scenario: A stop ends the run after the in-flight chunk

- GIVEN a run in its second chunk of three
- WHEN a stop is signalled
- THEN the second chunk finishes and is persisted, the third does not start, and
  the third chunk's items are reported `deferred`

#### Scenario: A stop before the first chunk makes no call

- GIVEN a stop already signalled
- WHEN a run is requested
- THEN no model call is made and every quoted item is reported `deferred`

#### Scenario: Persisted work survives a stop

- GIVEN a stopped run
- WHEN a client requests a new quote
- THEN the persisted chunks count as `served`

### Requirement: Progress Is Reported Through An Observer

A run MUST report progress to a caller-supplied observer as events carrying the
1-based index of the completed item and the `total`. The surface MUST NOT render
progress itself. An exception raised by the observer MUST NOT discard work already
persisted and MUST NOT lose the verdicts of the chunk in flight.

#### Scenario: Events carry index and total

- GIVEN a run judging three items
- WHEN the run executes with an observer
- THEN the observer receives events whose indices run 1 to 3 with `total` of 3

#### Scenario: A failing observer loses nothing

- GIVEN an observer that raises on the second event
- WHEN the run executes
- THEN every chunk persisted before the failure remains stored and the chunk in
  flight is still persisted

### Requirement: A Busy Workspace Is Retried, Then Reported As Unpersisted

When a chunk's persist finds the workspace busy, the run MUST retry the persist a
bounded number of times. When the bound is exhausted, the run MUST stop judging
further chunks, MUST NOT drop the paid-for verdicts silently, and MUST return the
typed `unpersisted` outcome with the count of verdicts not persisted. The retry
MUST NOT hold the workspace lock between attempts.

#### Scenario: A transient busy workspace is absorbed

- GIVEN a persist that finds the workspace busy once and then free
- WHEN the run executes
- THEN the chunk is persisted and the run continues

#### Scenario: A persistently busy workspace is reported

- GIVEN a workspace that stays busy past the retry bound
- WHEN the run executes
- THEN the outcome is `unpersisted` with the count of verdicts not persisted, and
  no further chunk is judged

### Requirement: The Run Outcome Is Typed

A run MUST return a typed outcome with the counts `judged`, `persisted`, `dropped`,
`deferred`, `unpersisted` and `settled_since_quote` (the count of quoted items settled
since the quote), and `not_in_quote` (the work keys needed now that the quote did not
cover; its length is the count), with `judged == persisted + dropped + unpersisted`.
`dropped` counts verdicts withheld on purpose (member edited, raised, forgotten,
unreadable, or a degraded reply). The outcome also carries a typed `failure` when a
backend error ended the run, and `observer_failed`, set when the progress observer raised. A backend error that is not an
availability error MUST return the `failure` outcome with the 1-based run position it failed at
and the persisted prefix intact. `BackendUnavailable` MUST propagate to the caller as raised once the completed chunks
are persisted; if that chunk's own persist fails, the typed `unpersisted` outcome is
returned with `failure` set instead. `BackendModelNotFound` MUST propagate to the caller
as raised, with the verdicts of every completed chunk already persisted. When either
is raised, the counts of the completed chunks reach the caller through the progress
observer's per-chunk events, not through a returned outcome.

#### Scenario: A failed item ends the run with the prefix kept

- GIVEN a run whose third item fails with a backend error
- WHEN the run ends
- THEN the outcome is `failure` with that index, the first two items' verdicts are
  persisted, and the remaining quoted items are reported `deferred`

#### Scenario: An unavailable backend propagates

- GIVEN a backend that becomes unavailable mid-run
- WHEN the run ends
- THEN `BackendUnavailable` is raised and every completed chunk is persisted

#### Scenario: A missing model propagates

- GIVEN a configured model the backend does not have
- WHEN the run executes
- THEN `BackendModelNotFound` is raised

### Requirement: Model Calls Hold No Workspace Lock

A run MUST NOT hold the workspace lock across a model call. The lock MUST be taken
only for a chunk's persist and MUST be released before the next model call.

#### Scenario: The lock is free during judging

- GIVEN a run in the middle of a model call
- WHEN another process requests the workspace lock
- THEN it is not blocked by the run

### Requirement: The App May Judge While The Daemon Runs

The surface MUST NOT take a lease lock or any mutual exclusion against the
unattended engine, and a run MUST NOT refuse because the engine is active. Safety
MUST come from idempotent persists and the digest pins of the token: two judgments
of the same item MAY both be paid for, but MUST NOT corrupt the adjudications
store or the queue, and the latest persisted verdict MUST be the one served. Spend
of a run MUST NOT count against the unattended engine's call budget.

#### Scenario: Concurrent judging wastes spend but keeps the store sound

- GIVEN a run and an unattended pass that both judge the same group
- WHEN both persist
- THEN the store holds a servable verdict for the group, one open queue row exists
  for it, and nothing is corrupted

#### Scenario: An active engine does not block a run

- GIVEN a workspace whose job records show an unattended job in progress
- WHEN a run is requested with a valid token
- THEN the run executes

### Requirement: The Surface Ends At Persisted Verdicts

The surface MUST NOT merge, relate, set a tier, call an auto-merge pass or call any
write core. It MUST NOT write `raw/` or any file under `bundle/`. Its writes MUST be
limited to the derived stores under `.openkos/`. A verdict MUST become actionable
only through the `curate-decisions` surface and a human decision.

#### Scenario: A SAME verdict is never merged by a run

- GIVEN a run that judges a group SAME
- WHEN the run ends
- THEN both concepts are unchanged, the verdict is persisted, and the item is
  listed as mergeable for a human to decide

### Requirement: Relation Type And Volatility Use The Same Protocol

The `relation_type` and `volatility` kinds MUST be quotable and runnable through
the same quote, consent, run protocol. Their quotes MUST be kind-tagged and MUST
bind the resolved model, the backend locality and the effective confidential flag.
Their runs MUST honor the binding ceiling, `not_in_quote`, `QuoteStale`,
cancellation at chunk boundaries, progress, per-chunk persistence and the typed
outcome. For these kinds the unit judged is the unit the shipped suggestion
services use, and the shipped `max_calls` and `deferred` semantics MUST be kept:
the surface exposes a per-chunk size and the quote's ceiling rather than a
`max_calls` parameter, and passes the chunk size as each chunk's `max_calls`.
Items already answered by a servable persisted answer MUST count as `served`. A
`relation_type` or `volatility` answer whose sampled concepts or endpoints changed
during the call MUST be dropped, not stored (reported as `dropped`).

#### Scenario: A relation-type quote is kind-tagged

- GIVEN candidate edges, some with a servable persisted suggestion
- WHEN a client requests a relation-type quote
- THEN the quote carries kind `relation_type`, the served edges count as `served`
  and the rest as `to_judge`

#### Scenario: A volatility run stops at a chunk boundary

- GIVEN a volatility run over several chunks
- WHEN a stop is signalled during the first chunk
- THEN the first chunk is persisted and the remaining items are `deferred`

### Requirement: Existing Commands Keep Their Output

Adding this surface MUST NOT change any stdout, stderr, exit code, stream ordering,
on-disk format, adjudications schema or queue schema of `curate`, `adjudicate`,
`suggest-relations`, `suggest-volatility` or `daemon`. Characterization goldens
recorded before any move MUST pass unchanged after each slice.

#### Scenario: Goldens pass unchanged

- GIVEN goldens recorded on the tree before any move
- WHEN each slice is complete
- THEN stdout, stderr and exit codes match byte for byte

### Requirement: Payloads Are Owner-Local

Quotes, verdicts and rationales derive from concept text and may contain
confidential content. The surface MUST be usable only by the owner's local process.
The MCP package MUST NOT import it, and the surface MUST NOT weaken sensitivity
handling in any persist. The confidential flag MUST be bound in the token so a
change in what content may be sent to the model requires new consent.

#### Scenario: The MCP package does not import the surface

- GIVEN the `mcp/` package
- WHEN the layering test inspects its imports
- THEN it does not import the judging module

#### Scenario: A confidential flag is part of consent

- GIVEN a quote taken with confidential content excluded
- WHEN the effective flag changes to include it before the run
- THEN the run returns `QuoteStale` and sends nothing to the model
