# Design: Judging As An Application Service

## Technical Approach

Judging gets a narrow synchronous `application/` surface, `application/judging.py`,
built on a **quote → consent → run** protocol (ADR-0059). A caller asks for an
LLM-free `JudgingQuote`; the human consents to it by handing its `QuoteToken` back;
`run_judging` judges at most the quoted work, persists per chunk, reports progress,
stops at chunk boundaries and ends at persisted verdicts and queue rows. It never
prompts, prints, reads a TTY, merges, or calls a write core. It follows ADR-0018 (no
`engine.py`), ADR-0021 (sync core, no preemption), ADR-0036 (lock only for each
chunk's persist), ADR-0037 and ADR-0049 (never auto-merges), and ADR-0053 (the
decision surface lists and applies; this surface judges).

Identity sets the protocol. Its serve, persist and reassemble helpers move out of
`cli/main.py` into `application/identity_adjudication.py` first (S1), behind
delegators that keep every patched name. The service composes the existing leaf
`resolution.adjudication.adjudicate_candidates` unchanged and does the chunking
itself. `relation_type` and `volatility` then adopt the same protocol over the
existing `suggest_relations_service` and `suggest_volatility_service` (S3).

Two defects are fixed in the same change because the feature does not work without
them: the daemon's identity stage rewrites judged rows as unjudged, and an
UNCERTAIN verdict is listed as pending evaluation and so re-offered for paid
judging.

The curate CLI is not rewired onto the service, and its output stays byte-identical.
The desktop app is the intended first client; until it exists, the service's own
tests and one equivalence test against curate's Identity stage stand in.

### Corrections to the proposal, from reading the code

None of these reopens a binding scope decision.

1. **There is no "unattended is enabled" key.** `config.UnattendedConfig`
   (config.py:1105-1123) is always present with defaults (`Config.unattended` uses
   `default_factory`), and the daemon runs whenever someone starts it. The
   `unattended` notice is therefore keyed on observed activity in `.openkos/jobs.db`,
   read-only (Decision 9), not on configuration.
2. **The daemon has no confidential-inclusion bit for identity.** Its identity stage
   makes no model call and builds no client, so it cannot resolve a local exemption
   without constructing one. The daemon fix carries the latest stored verdict that
   passes the rubric, member-set and member-digest checks, whatever bit it was
   judged under (Decision 6). The proposal's "matching confidential inclusion" stays
   true for every path that *judges*: the quote and the run partition with the
   effective bit, as curate does.
3. **`upsert_proposal` alone inflates `pending --stats`.** A changed payload digest
   retires the open row as `stale` and inserts a new one (pending_queue.py:380-386),
   and `kind_stats` counts both (pending_queue.py:762-777). Write-through therefore
   amends the open row in place when its recorded inputs are unchanged (Decision 5).
4. **`suggest_volatility_service` has no `CostQuote`.** Only
   `suggest_relations_service` has one (suggest_relations_service.py:190-197). S3
   reuses `max_calls`, `deferred` and the relations `CostQuote`; volatility's quote
   is built by the judging surface from its own partition.
5. **The S0 RED test cannot merge red.** `main` merges only on green CI, so S0b lands
   it as `pytest.mark.xfail(strict=True)`. S2a removes the marker; a strict xfail
   that starts passing fails CI, so the flip cannot be forgotten.
6. **`_EXPECTED_SERVICE_TASKS` expects one task per file.** The guard asserts
   `tasks == {expected}` (test_chat_timeout_wiring.py:629-640). `judging.py` names
   `adjudication` from S2b and, in S3, the relation and volatility tasks for the
   quote's model disclosure, so the map's value widens to a set. It is widened, not
   bypassed.

## Architecture Decisions

### Decision 1: Two modules, `identity_adjudication.py` and `judging.py`

**Choice**:
- `application/identity_adjudication.py` (S1): `AdjudicationServes`,
  `partition_adjudication_serves`, `servable_verdict` (the one predicate),
  `persist_adjudications` returning a typed `PersistResult`, and
  `reassemble_adjudications`. Moved from `cli/main.py:13249-13591` with unchanged
  semantics. The two `typer.echo` advisories become fields on the result.
- `application/judging.py` (S2b onward): the protocol. Quote, token, binding,
  outcome, ports, observer, `QuoteStale`, and the per-kind quote and run functions.

**Alternatives considered**:
- One module holding both. Rejected: curate and `adjudicate` need the serve and
  persist helpers without the protocol, and the daemon fix needs `servable_verdict`
  without either. Keeping them apart keeps the daemon's import narrow.
- Extending `curate_decisions.py`. Rejected: ADR-0053 Decision 3 keeps that surface
  free of model calls, and its `list` is free; judging is neither.
- `application/adjudication.py`. Rejected: it collides in meaning with
  `resolution/adjudication.py` (the leaf) and `state/adjudications.py` (the store).

**Rationale**: The names say what each holds. `cli/main.py` keeps
`_partition_adjudication_serves`, `_persist_adjudications`,
`_reassemble_adjudications` and `AdjudicationServes` as delegators and
re-exports (the #1168 precedent at main.py:13420-13491), so no patched name moves.
`_persist_adjudications` keeps rendering the two advisories, byte for byte, from
`PersistResult`.

### Decision 2: The quote token and what `QuoteStale` compares

**Choice**:

```python
JudgingKind = Literal["identity", "relation_type", "volatility"]

@dataclass(frozen=True)
class QuoteBinding:
    """What consent was given to. A change of any field is QuoteStale."""
    kind: JudgingKind
    model: str                     # config.resolve_task_model(cfg, task)
    backend: str                   # cfg.backend
    is_local: bool                 # client.locality.is_local
    effective_confidential: bool   # include_confidential or resolve_local_exemption(client, cfg)
    rubric_digest: str | None      # adjudication.rubric_digest() for identity; None otherwise

@dataclass(frozen=True)
class WorkItem:
    work_key: str                          # identity: adjudications_store.group_key_for(member_ids)
    member_digests: tuple[tuple[str, str], ...]  # sorted (concept id, sha256 of current bytes)

@dataclass(frozen=True)
class QuoteToken:
    binding: QuoteBinding
    include_confidential: bool     # the caller's request bit, re-resolved at run
    include_uncertain: bool        # the opt-in, Decision 4
    work: tuple[WorkItem, ...]     # the ceiling, in quote order
    calls: int                     # upper bound on chat calls; == len(work) for identity

    def digest(self) -> str: ...   # opaque sha256 hex for transport; parsing is the API stage's
```

- **`QuoteStale`** is returned, not raised, from `run_judging`, before any client
  call:

  ```python
  @dataclass(frozen=True)
  class QuoteStale:
      kind: JudgingKind
      changed: tuple[Literal["model", "backend", "locality", "confidential", "rubric"], ...]
  ```

  The run rebuilds a `QuoteBinding` from the current config, a freshly built
  client's `locality` (construction only, no call) and the current rubric, and
  compares it field by field with `token.binding`. Every changed field is named.
  The work set is **not** part of this comparison: a changed work set is the binding
  ceiling's job (Decision 3), never a reason to refuse the whole run.
- **No TTL.** Nothing in the token is a time. A token is stale only when a binding
  field changed, decided by digests and resolved values.
- **The token carries the request.** `include_confidential` and `include_uncertain`
  are part of what was consented to, so the caller cannot run a token under
  different flags. The run re-resolves the local exemption: a backend moved off this
  machine flips `effective_confidential` and `is_local`, and both are named.
- `digest()` covers every field, sorted and NUL-separated like `rubric_digest`.
  Nothing parses it back in this change. The in-process caller passes the dataclass,
  as with ADR-0053's `DecisionVersion`.

**Alternatives considered**:
- An opaque string only. Rejected for ADR-0053's reason: the caller and the tests
  must know *which* binding moved.
- A TTL. Rejected by binding decision 2: a day-old quote over unchanged digests is
  still exact, and a fresh one over a changed model is not.
- Including the work-set digest in `QuoteStale`. Rejected: it would refuse every run
  the daemon or an ingest touched between quote and consent, which is the common
  case at human timescales. The ceiling already makes a moved work set safe.

### Decision 3: The binding ceiling

**Choice**: after the binding check, `run_judging` re-partitions, LLM-free, and
computes three sets by `(work_key, member_digests)` equality:

| Set | Definition | What the run does |
|---|---|---|
| `judge` | still needed now **and** in `token.work` | judged, in quote order |
| `not_in_quote` | still needed now, **not** in `token.work` | reported by work key, never judged |
| `settled_since_quote` | in `token.work`, no longer needed (served by another surface or the daemon, member edited, group gone, kept distinct) | reported as a count, never judged |

A member edited after the quote changes the item's digests, so the item is no longer
the one consented to: it is reported in `not_in_quote` (its new digests) and counted
in `settled_since_quote` (its old ones). `len(judge) <= len(token.work)` holds by
construction, and a counting wrapper on the client asserts at the end that calls
made never exceeded `token.calls`; an excess raises an internal `AssertionError`
(a defect, not an outcome). A zero-call short-circuit (an all-unreadable or
all-confidential group, adjudication.py:575-581) counts as judged and costs nothing.

**Alternatives considered**:
- Judging the current needed set up to `calls` items. Rejected: it spends the
  consented count on work the human never saw.
- Refusing the run when any quoted item moved. Rejected for the reason in
  Decision 2.

### Decision 4: `judged_uncertain`, the opt-in, and re-entry to pending

**Choice**:
- **Listing** (S2a). `curate_decisions._identity_actions` maps a row verdict of
  `uncertain` to evaluation `judged_uncertain`, with actions keep-distinct and skip
  and the merge withheld for reason `judged_uncertain`. Both literals are additive
  (`DecisionItem.evaluation`, `WithheldAction.reason`). A malformed-reply degrade is
  an UNCERTAIN verdict (adjudication.py:479-481) and is listed the same way. A row
  with an unknown verdict string, or none, stays `pending_evaluation`.
- **Quoting** (S2b). The identity partition splits served verdicts into decided
  (SAME, DIFFERENT) and uncertain. The quote reports
  `uncertain` (the count, and their work items) separately from `served`. By default
  they are in neither `to_judge` nor `calls`. `quote_judging(..., include_uncertain=True)`
  moves them into `to_judge` and `calls`, so their cost is in the quote **before**
  consent. The flag is recorded in the token.
- **Running** (S2c). A quoted uncertain item is "still needed" only while its latest
  servable verdict is still UNCERTAIN. A re-judge persists a new row; the store
  serves the latest row (`latest[...] = row`, main.py:13360-13362), so the new verdict
  replaces it in every reader.
- **Re-entry.** A content edit changes a member's digest, so the stored verdict stops
  being servable (`servable_verdict`'s digest check). The quote then counts that group
  in `to_judge` immediately, with no producer pass needed. The listed item is marked
  `fresh=False` by the existing `row_is_fresh` until the next producer pass (daemon or
  curate) re-proposes it with `adjudication: null`, at which point it is listed
  `pending_evaluation`. That replacement is a genuine input change, so it retires the
  old row as `stale`, which is correct.

**Alternatives considered**:
- Hiding UNCERTAIN items from the list. Rejected: ADR-0053 lists, never hides.
- Re-offering UNCERTAIN by default. Rejected by the user's decision: it pays again
  for an answer the judge already declined to give.
- A TTL on UNCERTAIN. Rejected: no TTL anywhere; staleness is digests.

### Decision 5: Write-through only amends an open row in place (risk b)

**Choice**: the state layer gains one in-place amendment, used two ways.

- **The amendment.** Inside one `_transaction`, an open row (`pending` or `claimed`)
  whose `payload_digest` differs from the proposal's **and** whose recorded input
  digests equal the proposal's (same refs, same digests, same order) is updated in
  place: `payload`, `payload_digest` and `last_seen_at` change; `id`, `status`,
  `claimed_by`, `created_at`, targets and input digests do not. It lives in one private
  helper, `_amend_open_in_transaction`, after the same decline check
  `upsert_proposal` makes.
- **Write-through is amend-only**: `pq.amend_open_proposal(conn, proposal, *,
  commit_section, bundle_dir, clock) -> AmendOutcome` with
  `AmendOutcome = Literal["amended", "unchanged", "no_open_row", "inputs_changed",
  "suppressed"]`. It never inserts and never touches a resolved row:
  - no open row for the key (absent, or only `applied`/`declined`/`stale` rows):
    `no_open_row`, nothing written. A resolved row is never reopened;
  - an open row whose inputs differ: `inputs_changed`, nothing written. Replacing it is
    a producer's job, not the judge's;
  - an absent queue: nothing written, and the queue is never created.
- `curate_queue.set_identity_verdicts(layout, results, *, pinned, commit_section)`
  builds each SAME or UNCERTAIN result's proposal with the existing
  `queue_producers.identity_group_proposal` and calls `amend_open_proposal`. It skips a
  result whose members moved since `pinned`, and never touches a DIFFERENT result,
  which `resolve_judged_different` already resolves. A judged group with no open row
  gets its row from the next producer pass, which now carries the verdict
  (Decision 6).
- **Producers keep inserting.** `pq.upsert_proposal` gains `amend_same_inputs: bool =
  False` and `UpsertOutcome` gains `AMENDED`; with the flag, the "open row, different
  digest, same inputs" case calls the same helper instead of retiring and inserting.
  Every other case behaves exactly as today (suppressed, inserted, unchanged, replaced
  as `stale`). `queue_producers.enqueue_identity` passes the flag, so the daemon
  upgrading a row curate left unjudged (finding 2) is an amendment, not a retirement.
  `ProducerResult` gains an additive `amended: int = 0`; it has no reader outside
  `queue_producers.py`.
- `kind_stats` is unchanged. An amendment adds no row, so `enqueued` and `stale` do
  not move. `stale` keeps one meaning: the inputs changed or the proposal vanished.

**Alternatives considered**:
- Plain `upsert_proposal` for write-through. Rejected twice over: it retires the open
  row as `stale` and inserts (pending_queue.py:380-386), inflating `pending --stats`,
  and it inserts a new `pending` row whenever no *open* row exists
  (pending_queue.py:371-373), which reopens a key a human already resolved.
- A new resolution value such as `superseded`. Rejected: the queue schema's CHECK
  constraints (pending_queue.py:86-88) would change, which the proposal excludes.
- Making write-through a flag on `upsert_proposal` too. Rejected: an insert-capable
  function with a "do not insert" flag is the shape in which the reopen bug comes
  back. Two public entry points share one private helper and one decline check.

### Decision 6: The daemon serves from the adjudications store (risk d)

**Choice**: `_identity_stage` passes
`adjudicated=identity_adjudication.servable_stored_verdicts(ctx.layout, report.groups)`
to `producers.enqueue_identity`. That function reads the adjudications store
read-only and, for each group, applies the same `servable_verdict` predicate
`partition_adjudication_serves` uses, with the confidential check disabled:

1. the group's **latest** row exists;
2. `rubric_digest` equals this build's (`adjudication.rubric_digest()`);
3. the stored ref set equals the group's member set;
4. every stored digest equals the member's current content hash;
5. the verdict is in the enum, and `withdraw_self_refuting_same` is applied, as on
   every serve.

It never builds a client, never calls a model, and the stage keeps `uses_model=False`.
An absent or unreadable store carries nothing, which is today's behavior. The rows it
produces are byte-identical to the ones write-through produced from the same verdict:
both build the payload with `identity_group_proposal` from the same
`(verdict, confidence, rationale)`, so the daemon's upsert is `UNCHANGED`.

- **Why the store, not the queue row.** Queue-row verdicts carry no rubric digest, and
  `curate_queue.identity_verdicts` (curate_queue.py:152-179) checks only freshness. A
  daemon that preserved whatever verdict the open row held would keep a pre-rubric
  SAME mergeable across a judgment fix (#838). Reading the store makes the rubric
  check the daemon's too: after a rubric change, the next pass amends the row to
  `adjudication: null`, which is correct.
- **Why no confidential check.** The bit exists so a *judging* run serves only a
  verdict its own judge could have produced. The daemon is not judging; it preserves
  the latest owner-local judgment, and the queue row is already bit-agnostic (curate
  serves queue rows without the bit). Requiring a match would need a client in a
  model-free stage, and a mismatch would re-clobber exactly the verdict the app paid
  for under `include_confidential`. The judging paths keep the bit check.
- **Not a change to `curate`.** curate's `_serve_identity_rows` still serves fresh
  queue rows without a rubric check. That is pre-existing, and Decision 6 narrows it
  by keeping the queue in step with the store after each daemon pass. It is recorded
  as a follow-up, not changed here.

**Alternatives considered**:
- Preserving the open row's verdict while its inputs are fresh. Rejected for the
  rubric reason above.
- Matching the bit by building a locality-only client in the stage. Rejected: it adds
  client construction to the one model-free stage, and the result is wrong for
  app-paid verdicts, as above.

### Decision 7: Chunk size and cancel latency (risk e)

**Choice**: identity judges **one group per chunk** (`IDENTITY_CHUNK_SIZE: Final = 1`;
the run accepts a `chunk_size` override, validated `>= 1`, for tests). Each chunk is:

```
check stop ─▶ pin member digests ─▶ adjudicate_candidates([group]) ─▶ persist chunk
  (stop.is_set() ⇒ deferred)         (no lock, one chat call)          (one commit section)
```

- **Worst-case cancel latency** is one in-flight chat call plus one persist: at most
  `cfg.chat_timeout` (600 s by default) plus the persist's bounded wait. Nothing
  preempts a call (ADR-0021).
- **Cost.** Each chunk is one `adjudicate_candidates` call, which computes
  `sensitivity.sensitive_concept_ids(bundle_dir)` once per call
  (adjudication.py:557-561), so a run pays one LLM-free sensitivity walk per group
  instead of one per batch. Against a judge call measured in tens of seconds, that is
  accepted, and it is slightly more correct: a sensitivity raise between chunks is
  honored by the next chunk.
- **Progress.** The leaf's `on_progress(index, total, result)` is wrapped: the index
  is rebased to the run (`offset + index`) and `total` is `len(judge)`. The
  observer receives `progress(JudgingProgress(index, total, work_key, verdict))`
  after each group, and `chunk_persisted(persisted_so_far)` after each persist.
- **S3** chunks by re-invoking each existing service with `max_calls=chunk_size` and a
  candidate port filtered to the remaining quoted keys; the service's own persist runs
  per invocation and its store serves the persisted prefix on the next one. Each
  invocation repeats an LLM-free candidate walk (a graph build for relations), so S3
  starts from `RELATION_CHUNK_SIZE = 8` and `VOLATILITY_CHUNK_SIZE = 4`, UNMEASURED
  placeholders. S3's first task measures the per-invocation overhead and fixes both
  before the run is built; the worst-case latency (chunk size times `chat_timeout`) is
  stated in the spec from the measured values.

**Alternatives considered**:
- A batch of N per chunk for identity. Rejected: it multiplies worst-case latency by N
  to save sensitivity walks that cost less than one call.
- A stop hook inside the leaf. Rejected: the proposal keeps every leaf unchanged, and
  a raising `on_progress` discards the leaf's whole batch (adjudication.py:543-545).

### Decision 8: Persist, retry and the typed outcome

**Choice**:
- `identity_adjudication.persist_adjudications(layout, results, *, include_confidential,
  judged_digests, commit_section, write_through)` returns
  `PersistResult(persisted: int, dropped: int, store_error: str | None, busy: str | None)`.
  `write_through=False` is the CLI path (curate keeps its missing-only enqueue);
  `True` calls `set_identity_verdicts` inside the same section, after the store write
  and the DIFFERENT resolution.
- **Bounded retry.** The run enters `ports.commit_section()` up to
  `PERSIST_ATTEMPTS: Final = 3` times on `WorkspaceBusyError`, sleeping
  `ports.sleep(1.0)` then `ports.sleep(2.0)` between attempts (UNMEASURED
  placeholders). The section itself may wait too (the app adapter passes
  `lock_wait.locked_commit_section(root, wait_seconds=...)`); the attempts bound the
  service, the section's wait bounds each attempt.
- **On exhaustion or a store error** the chunk's results are counted as `unpersisted`,
  the run judges nothing further (the remaining items become `deferred`), and the
  outcome says why. Paid work at risk is therefore at most one chunk.
- **Outcome**:

  ```python
  @dataclass(frozen=True)
  class JudgingOutcome:
      kind: JudgingKind
      status: Literal["completed", "stopped", "failed", "unpersisted"]
      judged: int                         # results produced, zero-call degrades included
      calls: int                          # chat calls made; never > token.calls
      persisted: int
      unpersisted: int
      unpersisted_cause: Literal["workspace_busy", "store_error"] | None
      deferred: int                       # in `judge`, not reached
      not_in_quote: tuple[str, ...]       # work keys
      settled_since_quote: int
      failure: BackendError | None        # a non-propagating backend error
      failed_at: int | None               # 1-based run index of the failing item
      observer_failed: bool

  @dataclass(frozen=True)
  class RunRefused:
      """No model call was made and nothing was written."""
      reason: Literal["no_quote_token"]

  RunResult = JudgingOutcome | QuoteStale | RunRefused
  ```

- **Consent is required.** `run_judging` accepts `token: QuoteToken | None` so a
  caller that has no quote gets a typed answer rather than a type error at a
  transport boundary. `None` returns `RunRefused("no_quote_token")` before the config
  is read or a client is built.
- **Backend errors.** The leaf returns a `BackendError` in the batch with its
  1-based `failed_index` within the chunk (adjudication.py:603-608). The run rebases
  it like progress (`offset + failed_index`) into `failed_at`, and persists the
  chunk's prefix first. Then
  `BackendUnavailable` and `BackendModelNotFound` are re-raised unchanged (their
  propagation contract); every other `BackendError` ends the run with
  `status="failed"` and `failure` set. The observer has already received
  `chunk_persisted` for the prefix, so a caller that catches the raise still knows
  what was kept.
- **Observer failures.** Every observer call goes through one guard that catches
  `Exception` (`# noqa: BLE001 -- an observer is the client's callback; its failure
  must not cost paid work`), sets `observer_failed`, and stops calling the observer.
  The leaf never sees an observer exception, so its batch is never discarded.

**Alternatives considered**:
- Carrying an unpersisted chunk forward and retrying it with the next one. Rejected:
  the workspace is busy for a reason, and continuing to spend while persistence fails
  risks more paid work, not less.
- Fail-open with an advisory, as the CLI does. Rejected for the app: a dropped persist
  is double spend, and it must be a typed result.

### Decision 9: The unattended notice

**Choice**: the quote carries `QuoteNotice(code="unattended_active", ...)` when
`.openkos/jobs.db` exists and its newest job (`jobs.last_job`) either has no
`ended_at` or started within `cfg.unattended.maintenance_interval_seconds` of now. It
is read through `jobs.open_jobs_read_only`, which never creates the file. An
unreadable record emits the notice with `state="unknown"`, because an informational
notice should err toward disclosure. The notice changes nothing about the quote or
the run: no lease, no lock (ADR-0036), and app spend is not counted against
`max_calls_per_day`.

**Alternatives considered**:
- A configuration key. None exists (correction 1), and inventing one would be a
  config surface the proposal did not ask for.
- Always emitting the notice. Rejected: it would say nothing.

### Decision 10: Ports, observer and the boundary

**Choice**:

```python
@dataclass(frozen=True)
class JudgingPorts:
    chat_client: Callable[[config.Config, str], LLMBackend]      # (cfg, task)
    resolve_local_exemption: Callable[[HasLocality, config.Config], bool]
    commit_section: CommitSection
    find_candidates_report: Callable[..., CandidatesReport]
    adjudicate_candidates: Callable[..., AdjudicationBatch]
    sleep: Callable[[float], None] = time.sleep

class JudgingObserver:                       # every method a no-op
    def progress(self, event: JudgingProgress) -> None: ...
    def chunk_persisted(self, persisted: int) -> None: ...
```

- Every port that reaches a monkeypatched name is a call-time lambda in the adapter
  (the daemon's `lambda *a, **k: …` pattern), so no patch goes inert.
- The quote builds the client only to read `locality` and the model tag; it issues no
  chat. The run builds it once.
- `judging.py` is owner-local: its quote and outcome carry work keys, and its verdict
  progress events carry rationale-free verdicts only. The boundary test's `_TARGET`
  becomes a tuple, `("curate_decisions", "judging", "identity_adjudication")`, and
  every spelling check runs for each.

## Data Flow

### Identity quote → consent → run (S2b–S2c)

```
client                   judging.py                                    stores
------                   ----------                                    ------
quote_judging(root,
  kind="identity",
  include_confidential,
  include_uncertain)
   ├─ cfg, client = ports.chat_client(cfg, "adjudication")   (no call)
   ├─ binding = model, backend, is_local, effective bit, rubric
   ├─ find_candidates_report → drop kept-distinct ─────────────────▶ bundle (read)
   ├─ partition_adjudication_serves(effective bit) ────────────────▶ adjudications (read)
   ├─ split served → decided | uncertain
   ├─ notices: model, truncation, rubric_rejudge, unattended_active ▶ jobs.db (read-only)
   ◀── JudgingQuote(total, served, uncertain, to_judge, calls, binding, notices, token)

      ... human time, no lock, no TTL ...

run_judging(root, token, stop, ports, observer)
   ├─ token is None? ───────────────────────── return RunRefused("no_quote_token")
   ├─ rebuild binding; differs? ─────────────── return QuoteStale(changed)
   ├─ re-partition; judge = needed ∩ token.work; not_in_quote; settled_since_quote
   └─ for each chunk (one group):
        stop.is_set()? ─────────── deferred += rest; status="stopped"; break
        pin digests
        adjudicate_candidates([group], llm=counted) ─────────▶ one chat call (no lock)
        with ports.commit_section():  (≤ PERSIST_ATTEMPTS) ──▶ lock
             record_adjudications ───────────────────────────▶ adjudications
             resolve_judged_different (DIFFERENT) ───────────▶ queue: resolved declined
             set_identity_verdicts (SAME, UNCERTAIN) ────────▶ queue: open row amended in place;
                                                                no open row: nothing written
        observer.chunk_persisted(n)
   ◀── JudgingOutcome(...)
```

### Daemon identity stage after S2a

```
report_duplicates ─▶ drop judged-different ─▶ servable_stored_verdicts(groups) ─▶ enqueue_identity(adjudicated=…)
                                               (store, rubric, members, digests)    upsert(amend_same_inputs=True)
                                                                                     judged row: UNCHANGED
```

## File Changes

| File | Action | Slice | Description |
|---|---|---|---|
| `tests/unit/cli/test_judging_characterization.py`, `tests/unit/cli/fixtures/judging_characterization_goldens.json` | Create | S0a | Full-stream goldens for the unpinned spend paths (see Testing) |
| `tests/unit/cli/test_daemon.py` | Modify | S0b, S2a | The daemon-clobber test, `xfail(strict=True)` in S0b; marker removed in S2a |
| `tests/unit/application/test_curate_decisions.py` | Modify | S0b, S2a | Pins today's UNCERTAIN → `pending_evaluation` (S0b); flipped to `judged_uncertain` (S2a) |
| `src/openkos/application/identity_adjudication.py` | Create | S1 | Serve, persist and reassemble, moved; `servable_verdict`; `PersistResult`; `servable_stored_verdicts` (S2a) |
| `src/openkos/cli/main.py` | Modify | S1 | Delegators and re-exports; the persist delegator renders both advisories from `PersistResult` |
| `src/openkos/application/queue_resolution.py` | Modify | S1 | Registry key `openkos.cli.main._persist_adjudications` → `openkos.application.identity_adjudication.persist_adjudications` |
| `src/openkos/state/pending_queue.py` | Modify | S2a | `_amend_open_in_transaction`; amend-only `amend_open_proposal`; `UpsertOutcome.AMENDED`; `upsert_proposal(amend_same_inputs=...)` |
| `src/openkos/application/queue_producers.py` | Modify | S2a | `ProducerResult.amended`; `enqueue_identity` amends |
| `src/openkos/application/curate_queue.py` | Modify | S2a | `set_identity_verdicts` |
| `src/openkos/cli/daemon.py` | Modify | S2a | `_identity_stage` passes `adjudicated=servable_stored_verdicts(...)` |
| `src/openkos/application/curate_decisions.py` | Modify | S2a | `judged_uncertain` evaluation and withheld reason |
| `src/openkos/application/judging.py` | Create | S2b, S2c, S3 | Quote, token, binding, `QuoteStale` (S2b); run, outcome, observer, retry (S2c); relation and volatility (S3) |
| `tests/unit/application/test_identity_adjudication.py`, `test_judging.py`, `tests/unit/state/test_pending_queue.py` | Create / Modify | S1–S3 | Unit tests, RED first |
| `tests/unit/application/test_judging_equivalence.py` | Create | S2c | curate's Identity stage and the service on one fixture (Testing) |
| `tests/unit/cli/test_chat_timeout_wiring.py` | Modify | S2b, S3 | `_EXPECTED_SERVICE_TASKS` value becomes a set; `judging.py` added |
| `tests/unit/application/test_curate_decisions_boundary.py` | Modify | S2b | `_TARGET` becomes a tuple covering the new modules |
| `tests/unit/application/test_layering.py` | Unchanged | — | Scans new modules automatically |
| `src/openkos/cli/curate.py` | Unchanged | — | Judging section not rewired; calls the same `cli_main._*` names |
| `openspec/specs/...`, `docs/adr/0059-...`, `docs/adr/README.md` | Merge / Create | design, S4 | ADR written at design; specs merged at archive |

## Interfaces / Contracts

```python
# src/openkos/application/judging.py (public names; signatures abridged)

@dataclass(frozen=True)
class QuoteNotice:
    code: Literal["model", "truncated", "quarantined", "rubric_rejudge", "unattended_active"]
    count: int | None = None        # truncated: shown of produced; rubric_rejudge: groups
    state: Literal["active", "unknown"] | None = None   # unattended_active only
    model: str | None = None        # model only: the resolved task model
    backend: str | None = None      # model only: cfg.backend
    # Every quote carries exactly one `model` notice, the data behind the line the CLI
    # discloses before spending (curate.py:504). It repeats binding.model and
    # binding.backend so a client renders notices from one list.

@dataclass(frozen=True)
class JudgingQuote:
    kind: JudgingKind
    total: int
    served: int                     # decided verdicts served, no call
    uncertain: int                  # judged_uncertain; in to_judge only when opted in
    to_judge: int
    calls: int                      # upper bound; the run never exceeds it
    binding: QuoteBinding
    notices: tuple[QuoteNotice, ...]
    token: QuoteToken

@dataclass(frozen=True)
class JudgingProgress:
    index: int                      # 1-based across the run
    total: int                      # len(judge)
    work_key: str
    verdict: str                    # "same" | "different" | "uncertain"; never the rationale

def quote_judging(root: Path, kind: JudgingKind, *, include_confidential: bool = False,
                  include_uncertain: bool = False, ports: JudgingPorts) -> JudgingQuote: ...

def run_judging(root: Path, token: QuoteToken | None, *, stop: StopToken, ports: JudgingPorts,
                observer: JudgingObserver = JudgingObserver(),
                chunk_size: int | None = None) -> JudgingOutcome | QuoteStale | RunRefused: ...
```

```python
# src/openkos/application/identity_adjudication.py

class AdjudicationServes(NamedTuple): ...       # moved unchanged
class PersistResult(NamedTuple):
    persisted: int
    dropped: int                    # members unreadable or moved since judged_digests
    store_error: str | None
    busy: str | None

def servable_verdict(stored: Adjudication, group: CandidateGroup, *,
                     current_digest: CurrentDigest, rubric: str,
                     include_confidential: bool | None) -> AdjudicatedCandidate | None: ...
    # include_confidential=None: the bit is not compared (the daemon only)
def partition_adjudication_serves(layout, candidates, *, include_confidential,
                                  on_warning: Callable[[str], None] | None, surface: str) -> AdjudicationServes: ...
def persist_adjudications(layout, results, *, include_confidential, judged_digests,
                          commit_section: CommitSection, write_through: bool = False) -> PersistResult: ...
def reassemble_adjudications(candidates, served, fresh) -> list[AdjudicatedCandidate]: ...
def servable_stored_verdicts(layout, groups) -> tuple[AdjudicatedCandidate, ...]: ...
```

```python
# src/openkos/state/pending_queue.py
class UpsertOutcome(StrEnum):
    INSERTED = "inserted"; UNCHANGED = "unchanged"; REPLACED = "replaced"
    SUPPRESSED = "suppressed"; AMENDED = "amended"

def upsert_proposal(conn, proposal, *, commit_section, bundle_dir, clock=_now,
                    amend_same_inputs: bool = False) -> UpsertOutcome: ...   # producers

AmendOutcome = Literal["amended", "unchanged", "no_open_row", "inputs_changed", "suppressed"]
def amend_open_proposal(conn, proposal, *, commit_section, bundle_dir,
                        clock=_now) -> AmendOutcome: ...   # write-through; never inserts

# src/openkos/application/curate_queue.py
def set_identity_verdicts(layout, results, *, pinned, commit_section) -> None: ...
```

Contract notes:
- `persist_adjudications` takes a `commit_section` port. The CLI delegator passes
  `_commit_section_for(layout.root)` (defined at main.py:441, entered by the persist
  at main.py:13526), so the patch sites of
  `_commit_section_for` keep intercepting. The service enters its own port around the
  call, so the persist runs inside exactly one section per chunk.
- The CLI `_persist_adjudications` delegator prints the same two lines with the same
  `surface` (main.py:13581-13591), from `store_error` and `busy`.
- The service never imports `typer`, `rich`, `openkos.cli` or `openkos.vcs`, never
  calls `isatty`, and writes nothing under `bundle/`.

## Testing Strategy

Strict TDD from S1 with `uv run pytest`. S0a is characterization and green by design;
each golden is proven able to fail by mutating the line it pins, then reverting
byte-exactly and purging `__pycache__`. S0b's daemon test is the one intended RED,
landed as a strict xfail.

| Layer | What to test | Approach |
|---|---|---|
| Characterization (S0a) | curate Identity cost line with served > 0, as a full stream; the model-disclosure lines in a stream; declining at the gate with real probes; partial-batch failure for Structure and Metadata; persist fail-open and busy advisories for adjudications and edge suggestions, for both `curate` and `adjudicate` | `golden_support.Goldens`, `CliRunner`, full stdout/stderr/exit code; environment pinned (`GIT_CONFIG_COUNT`, `isatty` patched, no ambient `OLLAMA_HOST`); judge stubbed through the existing monkeypatch targets |
| RED (S0b) | A judged-SAME identity row with a servable store row keeps its verdict across `_identity_stage`, which makes no chat call; today's UNCERTAIN → `pending_evaluation` pinned | `xfail(strict=True, raises=AssertionError)` on the clobber test. Setup errors must not be absorbed by the xfail, so the test first asserts its own preconditions outside the xfailed part (store row servable, open row carries `verdict: same`, chat stub never called), then its one xfailed assertion compares the open row's `payload["adjudication"]` after the pass with the seeded `{"verdict": "same", ...}`. Today that value is `None`, which is the clobber and the only failure the marker accepts. Seeded `findings.db` via `pq.upsert_proposal` and `record_adjudications` |
| Unit (S1) | Moved helpers: identical partition, rubric_stale, store_read, persist drops, DIFFERENT resolution; `PersistResult` fields; delegator lines byte-identical | Existing `test_adjudicate.py` and curate tests run unchanged; one mutation of a patched line reddens a test |
| Unit (S2a) | `amend_open_proposal`: no open row creates nothing; a resolved (`applied`, `declined`) or `stale` row keeps its status and no new row appears; an absent queue is not created; `inputs_changed` writes nothing; `AMENDED` only when inputs equal; claimed row keeps its claim; decline still suppresses; `kind_stats` unchanged after an amendment; `servable_stored_verdicts` refuses each failed check; daemon pass yields `UNCHANGED`; rubric change amends to null; `judged_uncertain` listing | `tests/unit/state/test_pending_queue.py`, `test_identity_adjudication.py`, `test_curate_decisions.py`, `test_daemon.py` |
| Unit (S2b) | Quote is LLM-free (client with a raising `chat`); exactly one `model` notice naming the model and backend; uncertain excluded by default and included with its cost on opt-in; every `QuoteStale` field; token digest stability; unattended notice from a seeded `jobs.db`, absent, open job, unreadable | `test_judging.py` with recording fakes |
| Unit (S2c) | `run_judging(token=None)` returns `RunRefused("no_quote_token")` with no client built and no write; a failing item sets `failed_at` to its run index, not its chunk index; ceiling: never more calls than `token.calls`; `not_in_quote` and `settled_since_quote`; stop between chunks persists every completed chunk and a re-quote shows them served; busy retried `PERSIST_ATTEMPTS` times then `unpersisted`; store error; `BackendUnavailable` re-raised after the prefix persists; other backend errors returned; observer exception does not discard work; no lock held across a chat call (section fake records entry and exit around the fake chat) | `test_judging.py`; a `StopToken` set from inside the fake chat call |
| Equivalence (S2c) | curate Identity vs `run_judging` on one fixture | See below |
| Guards | Layering; boundary tuple; chat-timeout task map; registry key; budget AST guards; no `isatty` in the new modules | Existing tests, widened not weakened; each new guard RED-first with a temporary offending line |
| Monkeypatch survival | No patched name moves | Per slice, mutate one patched line and confirm a test reddens |

**The equivalence fixture (risk c).** Four two-member groups. The queue is seeded with
an open `pending_evaluation` row (`adjudication: null`, current input digests) for
each of `g1`, `g2` and `g3`, as a daemon pass would leave them:
- `g1` stubbed SAME, `g2` stubbed DIFFERENT, `g3` stubbed UNCERTAIN;
- `g4` with a servable stored verdict under the current rubric and no queue row, so
  both paths serve it and neither judges it.

No queue row carries a verdict beforehand (curate serves queue rows; the service does
not), `--auto-merge` is off (the service never auto-merges), and the walk answers `s`
to every prompt so curate writes nothing to `bundle/`. The judge stub is keyed by
member ids, so both runs see identical replies. The test runs curate in one copy of
the fixture and the service in another and compares, ignoring row ids and timestamps:

1. **The adjudications rows** (members, verdict, confidence, rationale, bit, input
   digests, rubric). They must be equal: both paths persist through
   `persist_adjudications`.
2. **The DIFFERENT resolution.** `g2`'s open row is resolved `declined` in both: both
   paths call `resolve_judged_different` from the same persist.
3. **The queue verdicts on `g1`, `g3` and `g4`, after one daemon pass on each side.**
   Straight after the run they differ by design: curate leaves `g1` and `g3` at
   `adjudication: null` and inserts a row carrying `g4`'s verdict; the service amends
   `g1` and `g3` and writes no `g4` row (amend-only, Decision 5). The test asserts that
   difference first, then runs one daemon identity pass on each copy and asserts the
   queue verdicts are equal: with Decision 6 the daemon amends curate's null rows from
   the store and inserts the service's missing `g4` row with its verdict. The daemon is
   the producer of record for queue rows, so convergence through it is the property
   that matters, not equality at the instant the run returns.

Byte identity of curate's output is the goldens' job, not this test's.

Gates per slice, run directly and never piped: `uv run pytest --cov` (branch coverage
at least 90%), `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`,
`uv run python evals/run_self_tests.py`.

## Slices

About 400 authored changed lines per PR is an advisory heuristic only; goldens JSON is
excluded, and tests and docs are never cut to fit. Delivery is auto-chain,
stacked-to-main; each slice targets `main` after the previous one merges and leaves
`curate`, `adjudicate` and `daemon` working end to end.

| Slice | Content | Forecast (authored) |
|---|---|---|
| S0a | Spend-path goldens (characterization) | ~380 |
| S0b | Daemon-clobber test as strict xfail; UNCERTAIN listing pinned | ~140 |
| S1 | `identity_adjudication.py` (moved), `PersistResult`, delegators, registry key | ~320 |
| S2a | Amend-only `amend_open_proposal`, `AMENDED` upsert, `set_identity_verdicts`, `servable_stored_verdicts` and the daemon fix (xfail removed), `judged_uncertain` | ~360 |
| S2b | `judging.py`: binding, token, `QuoteStale`, identity quote, uncertain opt-in, notices; guard updates | ~380 |
| S2c | Identity run: ceiling, chunks, stop, progress, persist retry, outcome; equivalence test | ~420 |
| S3a | `relation_type` on the protocol (overhead measured first) | ~300 |
| S3b | `volatility` on the protocol | ~250 |
| S4 | Delta specs merged and name-matched; ADR-0059 Accepted; docs only where the shape changes | ~150 |

About 2,700 authored lines across nine PRs. The proposal's S2a is split into S2a
(queue side) and S2b (the quote), and its S2b becomes S2c, because the queue changes
and the quote are independently mergeable and together forecast well over the
heuristic. S3 is split per kind for the same reason. S2c may exceed the heuristic: the
run, its retry and its equivalence test are one coherent behavior.

## Threat Matrix

N/A. No routing, shell command, subprocess, VCS or PR automation, executable-file
classification or process-integration boundary is added. The service writes only
`.openkos/findings.db` through existing state functions, makes no auto-commit, and
`openkos.vcs` stays out of `application/` (layering guard). The confidential
boundary is handled by the owner-local boundary test (Decision 10).

## Migration / Rollout

No migration. No on-disk format, adjudications schema or queue schema changes:
`AMENDED` is an `UPDATE` of existing columns, and the queue's CHECK constraints are
untouched. Every queue row the change writes is derived and rebuildable. Each slice
reverts on its own; reverting S2a restores the clobber (today's behavior), and the
verdicts stay in the adjudications store to be served.

## Open Questions

- [x] **The equivalence conflict (resolved).** curate's Identity path inserts identity
  rows where none is open and never upgrades an open row (`curate_queue.py:319-328`,
  `pending_queue.py:371-373`); the amend-only write-through does the reverse, so their
  queues differ at the instant a run returns. Resolved as options (i) plus (ii): the
  store and the DIFFERENT resolution are compared directly, the immediate queue
  difference is asserted as intended, and the queue verdicts are compared after one
  daemon identity pass on each side, where they converge. curate's enqueue and the
  amend-only rule are unchanged. The `Existing Commands Keep Their Output` requirement
  is about curate's own output and schemas, which this leaves untouched.
- [ ] S3 chunk sizes are UNMEASURED placeholders; S3a's first task measures the
  per-invocation overhead and fixes them (Decision 7).
- [ ] `PERSIST_ATTEMPTS` and its backoff are UNMEASURED placeholders.
- [ ] Follow-up, not blocking: curate's `_serve_identity_rows` serves queue-row
  verdicts without a rubric check (curate_queue.py:152-179); Decision 6 narrows the
  window, it does not close it.
- [ ] Follow-up, not blocking: token serialization and parsing belong to the MVP 6 API
  stage (ADR-0039).
