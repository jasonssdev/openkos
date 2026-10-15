# Proposal: Judging As An Application Service

MVP 6 stage "Judging as an application service" ([roadmap](../../../docs/roadmap.md), MVP 6;
[ADR-0039](../../../docs/adr/0039-the-stable-python-api-ships-with-a-desktop-app-as-its-first-client.md)).
It is the required follow-up named by the archived `curate-decisions-as-data` change
([ADR-0053](../../../docs/adr/0053-curation-decisions-are-a-two-phase-application-surface.md)).
The full analysis is in `exploration.md` in this folder; this proposal does not
repeat it. Paths are under `src/openkos` unless they start with `tests/`, `docs/` or
`openspec/`.

## Intent

The `curate-decisions` surface lists open decisions and applies one action to one
item, but it deliberately computes no judgment. An identity row the daemon enqueues
carries `adjudication: null`, so it is listed as *pending evaluation* with no merge
action. The only way to judge it today is the interactive `curate` Identity stage
or the `adjudicate` verb, both of which live in `cli/`, print, prompt and read a TTY.
A desktop app therefore cannot make an unjudged identity item decidable.

This change gives judging an `application/` surface built on a **quote → consent →
run** protocol. A caller asks for an LLM-free quote of what judging would cost,
the human consents to that quote, and a run judges at most what was quoted,
persists verdicts incrementally, reports progress and can be cancelled. The run
ends at persisted verdicts and at queue rows that carry them, so the item becomes
decidable through `curate-decisions`.

Two existing defects make the feature unusable unless they are fixed in the same
change, so both are in scope:

- **The daemon undoes the verdict** (exploration finding 3). `_identity_stage`
  (`cli/daemon.py:253-283`) enqueues identity proposals with no `adjudicated`
  argument, so every unattended pass proposes `adjudication: null`. Its digest
  differs from a judged row's, and `upsert_proposal` retires the judged row and
  inserts an unjudged one. Any verdict the app pays for is erased by the next
  daemon pass.
- **UNCERTAIN loops** (finding 4). An UNCERTAIN verdict is persisted, yet
  `_identity_actions` (`application/curate_decisions.py:1070-1103`) still maps it to
  pending evaluation, so a client would offer to judge it again and pay again.

Why now: the app's review screen is the next MVP 6 stage, and it needs judging to
be callable without `openkos.cli`. ADR-0039 asks for the API to be shaped by a real
client; the CLI paths and the daemon are the real clients that set its edge cases.

## Binding scope decisions

These were resolved by the user on 2026-10-14 and recorded in `exploration.md`.
Spec and design must not reopen them.

1. **Kinds.** Identity first, then `relation_type` and `volatility`. Contradictions
   and revisions are deferred: they carry no app actions (ADR-0053 §1) and need the
   judge model swap. The quote carries `kind`, so adding them later is additive.
2. **The quote is a binding ceiling.** A run judges only quoted items that are still
   needed, never more, and reports newly appeared work as `not_in_quote`. A change of
   model, backend locality, rubric or the effective confidential flag returns a typed
   `QuoteStale` and requires new consent. The quote has no TTL; staleness is decided
   by digests.
3. **The app may judge while the daemon runs.** There is no lease lock, which would
   contradict ADR-0036. Safety comes from idempotent persists and digest pins. When
   `unattended` is enabled in config, the quote carries an informational notice that
   the automatic engine is also active. The daemon clobber is fixed regardless.

Carried from the exploration, not blockers:

- An UNCERTAIN verdict is named `judged_uncertain` and is not re-offered for judging
  by default.
- The quote carries no ETA. No per-call figure for the judge model is recorded, so an
  ETA cannot be sourced.

## Scope

### In scope

- **S0 — characterization goldens and a RED daemon-clobber test.** Recorded on the
  current tree before anything moves, following `golden_support.Goldens` and the
  pinned-environment pattern. Pins the unpinned spend paths: the cost line with
  served > 0 as a full stream, the model-disclosure lines, declining at the gate with
  real probes, partial-batch failure for Structure and Metadata, and the persist
  fail-open and busy advisories for adjudications and edge suggestions. Adds one
  test that is **RED on the current tree**: a judged-SAME identity queue row survives
  a daemon pass with its verdict. S0 may be two PRs.
- **S1 — move identity serve, persist and reassemble into `application/`.** The
  helpers in `cli/main.py:13276-13591` (`_partition_adjudication_serves`,
  `_persist_adjudications` and the reassembly) move into an application module.
  `cli/main.py` keeps delegators (the #1168 precedent at `main.py:13420-13491`), so
  no patched name moves. The queue-resolution registry key
  `"openkos.cli.main._persist_adjudications"` and the chat-timeout AST task map are
  updated, not worked around.
- **S2a — identity quote, queue write-through, the daemon fix and `judged_uncertain`.**
  - A kind-tagged, LLM-free identity quote: `total`, `served`, `to_judge` and
    `calls`; the resolved per-task model, the backend and its locality; the effective
    confidential bit (`include_confidential` or the local exemption); the rubric
    digest; the work keys with their per-member digests; and the truncation,
    quarantine, model and `unattended` notices. Its token binds all of these.
  - Queue write-through: a persisted verdict upgrades the open identity row through
    `pq.upsert_proposal` (a new `curate_queue.set_identity_verdicts`), so the item
    becomes decidable. DIFFERENT keeps resolving the row as today.
  - **The daemon-clobber fix (required).** The daemon's identity stage carries every
    stored verdict that is still servable (current rubric, current member digests,
    matching confidential inclusion) into `adjudicated`. It makes no model call and
    keeps `uses_model=False`. The S0 RED test turns green here.
  - **`judged_uncertain`.** An additive value of the `curate-decisions` identity
    item's evaluation field. A persisted UNCERTAIN verdict, including a
    malformed-reply degrade, is listed as `judged_uncertain` with keep-distinct and
    skip, no merge action, and is excluded from the quote's `to_judge` by default.
- **S2b — the identity run.**
  - **Binding ceiling.** The run takes the quote token, re-partitions, judges only
    the intersection of the quoted set and the still-needed set, reports
    `not_in_quote`, and returns `QuoteStale` on a model, locality, rubric or
    confidential change.
  - **Incremental per-chunk persistence.** The orchestrator chunks the to-judge list
    and persists each chunk (adjudications store, DIFFERENT resolution, queue
    write-through) before judging the next. No leaf in `resolution/` changes. A
    re-quote after an interruption shows the persisted work as `served`.
  - **Cancellation via `StopToken` at chunk boundaries.** Latency is stated
    honestly: nothing preempts a chat call (ADR-0021), so a stop takes effect after
    the in-flight chunk finishes. That is at least one chat call, and each call can
    take up to `chat_timeout` (600 s by default). Design chooses the chunk size and
    states the resulting worst case.
  - **Progress events** through an observer, carrying the index and `total`. An
    observer failure must not discard work already persisted.
  - **A bounded persist retry on a busy workspace.** Today a busy workspace costs
    the spend (`main.py:13585-13591`). The run retries the commit section a bounded
    number of times, and on exhaustion returns a typed `unpersisted` outcome with
    its count rather than dropping paid work silently.
  - **A typed outcome**: `judged`, `persisted`, `deferred`, `not_in_quote`, `failure`
    and `unpersisted`. `BackendUnavailable` and `BackendModelNotFound` keep their
    propagation contract, with the persisted prefix intact.
- **S3 — `relation_type` and `volatility` on the same protocol**, over the existing
  `suggest_relations_service` and `suggest_volatility_service`. Their `max_calls`,
  `deferred` and `CostQuote` shapes are reused, not replaced.
- **S4 — delta specs, the ADR and archive.** The ADR and the delta specs are drafted
  at design, before S1. Docs change at archive only where the general shape changes.

### Constraints on every slice

- **CLI output stays byte-identical**: stdout, stderr and exit codes for `curate`,
  `adjudicate`, `suggest-relations`, `suggest-volatility` and `daemon`. The S0
  goldens and the existing S0a/S0b goldens run unchanged at the end of each slice.
- **No patched name moves.** The new orchestration sits beside curate. Ports use
  call-time lookup, following the daemon's `lambda *a, **k: …` pattern. Each slice
  mutates one patched line and confirms that a test reddens.
- **curate's judging section is not rewired.** The duplicated sequencing this leaves
  is accepted. One equivalence test runs both paths on the same fixture and compares
  the persisted adjudications store and the resulting queue verdicts.
- **The service never calls auto-merge or any write core** (ADR-0037, ADR-0049). It
  ends at persisted verdicts and queue rows.
- **Model calls stay outside the workspace lock** (ADR-0036). The lock is taken only
  for each chunk's persist.
- The new module is owner-local and is covered by `test_curate_decisions_boundary.py`
  and `test_layering.py` (ADR-0053 §6).

### Out of scope

- **Contradictions and revisions judging.** Deferred by binding decision 1. The
  divergent pricing between `contradictions_service` and curate's probe stays
  unpinned and is flagged only.
- **An ETA in the quote.** No per-call figure for the judge model exists to source it.
- **Rewiring curate's judging onto the service.** Curate keeps its inline Identity,
  Structure and Metadata judging, its probes and `gate()`. Moving them would move the
  code that about 206 monkeypatch sites resolve.
- **Declaring the surface public or stable.** ADR-0039 puts the freeze in MVP 6's API
  stage, so this surface stays internal.
- A lease lock or mutual exclusion between the app and the daemon.
- Counting app spend against the daemon's `max_calls_per_day`. The daemon's budget
  applies to the runner only, and that does not change.
- Merging an unjudged identity item on a human decision alone, and any write action.
- Any `api/` or `mcp/` wiring.
- Any change to the on-disk format, the adjudications schema or the queue schema.

## Capabilities

### New Capabilities

- `judging-application-service`: the application surface for judging. It covers the
  kind-tagged quote and its token; consent as a reference to that token; the run's
  binding ceiling, `not_in_quote` and the typed `QuoteStale`; incremental per-chunk
  persistence; cancellation through `StopToken` at chunk boundaries with its stated
  latency; progress events; the bounded persist retry and the `unpersisted` outcome;
  the typed run outcome; the `unattended` notice; and the guarantees that it never
  prompts, prints, reads a TTY, holds the lock across a model call, or calls
  auto-merge or a write core. The name follows the existing
  `ingest-application-service`, `query-application-service` and
  `lifecycle-application-service` capabilities.

### Modified Capabilities

- `curate-decisions`:
  - MODIFIED "Only Judged-SAME Identity Items Are Mergeable": the identity item's
    evaluation field gains `judged_uncertain` beside pending evaluation. An UNCERTAIN
    item carries keep-distinct and skip, no merge action, and is not pending
    evaluation.
  - MODIFIED "The Surface Computes No Judgment": the surface still computes none.
    Judging lives in `judging-application-service`, and the requirement names it as
    the place a pending-evaluation item becomes decidable.
  - ADDED: a persisted verdict written through to an open identity row makes the
    listed item reflect that verdict (SAME becomes mergeable under the existing
    checks, UNCERTAIN becomes `judged_uncertain`) without a new decision key.
- `pending-work`:
  - MODIFIED "Producers Upsert By Key And Retire On A Changed Digest", or a new
    ADDED requirement if the spec phase judges it clearer: an identity producer MUST
    carry a stored verdict that is still servable into its proposal, so an unattended
    pass never replaces a judged row with an unjudged one. The scenario is the S0 RED
    test.
  - The spec phase states how a verdict write-through is recorded in
    `pending --stats` (see Risks).
- `curate-command`: **expected unchanged.** Output is byte-identical and curate's
  judging is not rewired. The spec phase confirms that no requirement text changes,
  and writes no delta if none does.
- `job-runtime`: **expected unchanged.** "The Maintenance Pass Computes And Enqueues
  Only" still holds: the fix adds no model call and no write to `bundle/`. The spec
  phase confirms this.
- `entity-resolution-adjudication`: **expected unchanged.** "Persisted Verdicts Are
  Served Before Re-Judging" already defines servability, and the daemon fix reuses
  it. The spec phase confirms this.

## ADR

**A new ADR is required, numbered 0059 or later.** ADR-0055 through ADR-0058 are the
desktop ADRs merged to main in #1380. This worktree predates that merge, so design
must take the next free number from `docs/adr/README.md` on `origin/main`. Design
writes it, with status Proposed. The decision it records:

> Judging is an `application/` surface that runs a quote → consent → run protocol.
> The quote is LLM-free and kind-tagged, and its token binds the model, backend
> locality, effective confidential inclusion, rubric and the work set with its
> member digests. A run never exceeds its quote: it judges only quoted items that
> are still needed, and a change of model, locality, rubric or confidential flag
> returns a typed stale result and requires new consent. There is no TTL and no
> lease lock across human time (ADR-0036). Verdicts persist per chunk, a run stops
> only at chunk boundaries (ADR-0021), and the surface ends at persisted verdicts;
> it never merges.

This passes the design gate. It decides an interface and a consent trade-off on
spend, and that interface is part of the stable API ADR-0039 owes, which makes it
hard to reverse. It applies ADR-0018, ADR-0021, ADR-0036, ADR-0037, ADR-0039,
ADR-0047, ADR-0049 and ADR-0053, and supersedes none of them.

## Approach

This follows approach 1 from the exploration: **an identity-first vertical, goldens
first, with the protocol emerging from the hard case.**

1. Goldens and the RED daemon-clobber test come first, because the byte-identical
   risk lives in the spend paths and the clobber has no test today.
2. S1 moves only the identity helpers, keeping delegators, so S2 has an
   application-layer home that curate and the new service share.
3. Identity sets the protocol, because it is the only kind with a "judge an existing
   row" semantic, the queue write-through, the daemon clobber and UNCERTAIN.
4. `relation_type` and `volatility` then adopt the protocol over services that
   already exist. A generic `quote()`/`run()` designed first was rejected by ADR-0053
   as designed in a vacuum.

The service composes existing leaves (`adjudicate_candidates`, `suggest_edge_types`,
`suggest_volatility`) with no leaf change. Chunking, cancellation, progress and
persist retry live in the orchestrator. The CLI adapter is not a client in this
change; the desktop app is the intended first client, and the equivalence test and
the service's own tests stand in until it exists.

**Slice plan** (sizes are the exploration's estimates; about 400 authored lines per
PR is advisory only, and tests and docs are never cut to fit it):

| Slice | Content | Size |
|---|---|---|
| S0 | Characterization goldens for the unpinned spend paths; RED daemon-clobber test | ~400 (JSON excluded) |
| S1 | Identity serve/persist/reassemble into `application/`, delegators, registry key, AST task map | ~250 net |
| S2a | Identity quote; queue write-through; daemon carries servable verdicts; `judged_uncertain` | ~350 |
| S2b | Identity run: observer, `StopToken` via chunking, per-chunk persist, bounded retry, `QuoteStale` | ~400 |
| S3 | `relation_type` and `volatility` on the protocol | ~350 |
| S4 | Delta specs, ADR, archive | ~150 |

The slice plan is kept as the exploration states it. One adjustment is allowed
without reopening the proposal: if S0 exceeds its budget, it splits into S0a
(goldens) and S0b (the RED test), because the RED test must land in a PR that CI
can run red-then-green against S2a.

**Delivery:** auto-chain, stacked-to-main. The user chose both, and authorized
merging each slice after review and green CI. Each slice targets `main` after the
previous one merges. About 1,900 authored changed lines across 6–7 PRs.

**TDD:** strict mode is active, with runner `uv run pytest`. S0's goldens are
characterization and are green by design; its daemon-clobber test is the one
intended RED. Every later slice needs an observed RED before it is implemented.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/application/judging.py` (name settled at design) | New | Quote, token, run, outcome types, observer, ports, `QuoteStale` |
| `src/openkos/application/` (identity serve/persist module) | New | Moved from `cli/main.py:13276-13591` in S1 |
| `src/openkos/cli/main.py` | Modified | Delegators for the moved helpers; no patched name moves |
| `src/openkos/application/curate_queue.py` | Modified | `set_identity_verdicts` via `upsert_proposal` |
| `src/openkos/application/curate_decisions.py` | Modified | `_identity_actions` maps UNCERTAIN to `judged_uncertain` |
| `src/openkos/application/queue_producers.py`, `src/openkos/cli/daemon.py` | Modified | The identity stage carries servable stored verdicts into `adjudicated` |
| `src/openkos/application/suggest_relations_service.py`, `suggest_volatility_service.py` | Modified (S3) | Adopt the quote/run protocol over their existing shapes |
| `src/openkos/application/queue_resolution.py` | Modified | Registry key updated for the moved persist |
| `src/openkos/cli/curate.py` | Unchanged | Judging section not rewired |
| `tests/unit/cli/fixtures/*goldens*.json`, `tests/unit/cli/test_daemon.py`, `tests/unit/cli/test_curate.py` | New / Modified | S0 goldens, RED clobber test, equivalence test |
| `tests/unit/test_chat_timeout_wiring.py`, `tests/unit/application/test_layering.py`, `test_curate_decisions_boundary.py`, the budget AST guards | Modified | Updated for the new module, never weakened |
| `openspec/specs/curate-decisions/spec.md`, `pending-work/spec.md`, new `judging-application-service/spec.md` | Delta / New | At archive |
| `docs/adr/0059-*.md` (or next free), `docs/adr/README.md` | New / Modified | Written at design |
| `docs/architecture.md` | Possibly modified | Only if the general shape changes |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| The daemon erases app-paid verdicts | Certain today | In scope: S0 RED test, fixed in S2a |
| UNCERTAIN items are re-judged and re-paid | Certain today | `judged_uncertain`, excluded from `to_judge` by default |
| A run spends more than the human consented to | Medium | The token binds the work set; the run judges the intersection only; `QuoteStale` on model, locality, rubric or confidential change |
| A kill or a busy workspace loses paid work | High today | Per-chunk persist; bounded retry; typed `unpersisted` outcome |
| Cancel latency surprises the app user | Medium | Stated in the spec: at least one chat call, up to `chat_timeout` (600 s) per in-flight call, times the remainder of the chunk |
| The two identity paths drift | Medium | Shared S1 helpers; one equivalence test. It must compare the adjudications store and queue verdicts on a fixture where curate's missing-only enqueue and the write-through agree; design chooses that fixture |
| Write-through counts as `stale` in `pending --stats` | Medium | Upgrading a row through `upsert_proposal` retires the old row as stale. The spec phase decides whether that is acceptable or needs a distinct resolution; the stats contract is in `pending-work` |
| A queue-row verdict from an old rubric is served | Medium | Queue-row verdicts carry no rubric digest. The daemon fix reads servability from the adjudications store, which checks the rubric; design verifies `curate_queue.identity_verdicts` against this |
| A relocated monkeypatch target goes silently inert | Medium | No patched name moves; one mutation per slice must redden a test |
| CLI output drifts | Medium | S0 goldens plus the existing S0a/S0b goldens run unchanged every slice |
| Model swap thrash (26B and 8B) when the app and daemon alternate | Medium | Accepted; disclosed by the `unattended` notice (ADR-0047 §6) |
| Confidential content crosses a boundary | Low | Owner-local module, covered by the boundary guard; no `api/` or `mcp/` wiring |
| The spec overstates the shipped code | Medium | At verify and archive, check requirement text against shipped behavior and this Intent |
| The ADR number collides with main | Low | Design takes the number from `origin/main`'s `docs/adr/README.md` |

## Rollback Plan

The change adds a surface and fixes the daemon's identity enqueue. It touches no
on-disk format, adjudications schema or queue schema, and every queue row it writes
is derived and rebuildable. Each slice is a self-contained PR that leaves `curate`,
`adjudicate` and `daemon` working end to end, so any prefix of the chain is a valid
stopping point. Reverting a slice's merge commit restores the previous code: S0 is
tests only; S1 reverts to the helpers in `cli/main.py`; S2a reverts the quote, the
write-through, the daemon fix and `judged_uncertain` (the daemon then clobbers again,
which is today's behavior); S2b and S3 revert the run and the two kinds. A queue row
upgraded with a verdict before a revert stays valid data; the next daemon pass after
reverting S2a retires it to unjudged, and the verdict remains in the adjudications
store to be served. The S0 goldens stay after any revert as the guard for a retry.

## Dependencies

- The archived `curate-decisions-as-data` change and ADR-0053: the list, actions and
  evaluation field this change extends.
- PR #1380 on `main` (ADR-0055 to ADR-0058). This worktree should be rebased before
  design writes the ADR.
- Existing services and leaves: `suggest_relations_service`,
  `suggest_volatility_service`, `resolution.adjudication`, `state.pending_queue`,
  `application/runtime.py` (`StopToken`), `lock_wait.locked_commit_section`.

## Non-negotiables preserved

- **Local-first.** The quote is LLM-free and costs nothing. The backend and model are
  the configured ones; confidential content never leaves the device, and the
  confidential bit is bound in the quote.
- `raw/` is untouched, and `bundle/` stays pure OKF. The service writes no bundle
  file.
- **Reconstructible.** The adjudications store and the queue stay derived caches;
  the daemon fix re-derives verdicts from the store.
- **Provenance and freshness.** Verdicts are persisted with their member digests and
  rubric digest by the existing persist path.
- **Human curates, engine maintains.** Spend needs consent to a quote, and the
  service never merges. Merging stays a per-item human action in `curate-decisions`.
- The core stays synchronous, and there is no `engine.py`.

## Success Criteria

- [ ] S0 goldens were recorded before any move and pass unchanged at the end of every
      slice. Stdout, stderr and exit codes are byte-identical.
- [ ] The daemon-clobber test was observed RED on the S0 tree and is GREEN after
      S2a: a judged-SAME row keeps its verdict across a daemon pass, which makes no
      model call.
- [ ] A persisted UNCERTAIN verdict is listed as `judged_uncertain`, carries no merge
      action, and is absent from a default quote's `to_judge`.
- [ ] A run never makes more model calls than its quote's `calls`; new work is
      reported as `not_in_quote`; a model, locality, rubric or confidential change
      returns `QuoteStale` and makes no model call.
- [ ] A run stopped through `StopToken` keeps every completed chunk persisted, and a
      re-quote reports those items as `served`.
- [ ] A busy workspace is retried a bounded number of times; on exhaustion the
      outcome is `unpersisted` with its count.
- [ ] The service imports none of `typer`, `rich`, `openkos.cli` or `openkos.vcs`,
      never prompts or prints, holds no lock across a model call, and never calls
      auto-merge or a write core. Guarded by `test_layering.py` and the boundary test.
- [ ] The equivalence test passes: curate's Identity stage and the service produce
      the same adjudications store and queue verdicts on the shared fixture.
- [ ] Each slice's patched-line mutation reddens a test.
- [ ] Delta specs for `curate-decisions` and `pending-work` and the new
      `judging-application-service` spec are merged at archive with every heading
      name-matched. The ADR is written, numbered after ADR-0058, and indexed.
- [ ] `uv run pytest --cov` passes with branch coverage at or above 90%, along with
      `ruff check .`, `ruff format --check .`, `mypy .` and the eval self-tests.
