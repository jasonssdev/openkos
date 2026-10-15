# Design: A Stable Python API For The Review Queue

## Technical Approach

`openkos.api` is a thin, synchronous, in-process facade over the existing
`application/` services (ADR-0018, ADR-0021). It owns five things and nothing
else: the `Workspace` handle, the public error root, the default composition
(chat client, commit section, auto-commit), the API-owned result types that stop
an internal type from leaking, and the compatibility machinery (`API_VERSION`,
`__all__`, the surface snapshot and its guards). No `application/` module moves
and none is declared public in place. The decision is recorded as ADR-0060
(content below, transcribed by a task).

Issue #1391 lands first and independently of the facade. The daemon's identity
stage is split into an LLM-free compute half and a commit half under
`application/identity_stage.py`; the daemon calls both. Store-only builders turn
persisted edge suggestions and cached volatility answers into the same
proposals the producers publish, and `application/queue_refresh.py` runs the
three model-free stages through `queue_producers.publish`, so the three queue
properties stay applied in one place. Only identity can prove a complete pass
model-free; relations and volatility insert and amend only (Decision 6).

Paths are under `src/openkos/` unless they start with `tests/`, `docs/` or
`openspec/`.

### Corrections to the proposal, from reading the code

None of these reopens a binding scope decision (v1 is the review queue).

1. **Applying a decision needs a git auto-commit the API cannot import.**
   `curate_decisions.DecisionPorts.autocommit` is required
   (curate_decisions.py:169), and the only implementation is
   `cli/main._autocommit` (main.py:1458), which prints with `typer.echo`. The
   tier decision also needs `_decide_config_commit` (main.py:1532) and
   `_autocommit_config_edit` (main.py:1600) so a dirty `openkos.yaml` is not
   swept into the commit (#1330). `test_shared_write_helpers_are_never_forked`
   (test_layering.py:163) forbids a second `_autocommit`, and `application/`
   may not import `openkos.vcs` (test_layering.py:96). The print-free bodies
   therefore move to a new `vcs/autocommit.py` that both adapters call; the CLI
   keeps every name as a delegator that prints the same bytes (Decision 9). This
   adds one slice.
2. **`openkos.api` collides with a placeholder in two places.**
   `test_curate_decisions_boundary.py:29` bars a package named `api` from the
   owner-local surfaces, and `docs/architecture.md:229` describes `api/` as the
   future async local HTTP adapter. The binding name `openkos.api` is kept for
   the Python API; the boundary guard is generalized so that every package other
   than `application`, `cli` and `api` is barred, which is stronger than today
   (Decision 10); the HTTP adapter gets its own name in its own change.
3. **The API needs a third `BackendFactories` binding module.**
   `tests/unit/conftest.py:507` patches the concrete client classes only in
   `openkos.cli.main` and `openkos.mcp.server`. A module that binds them
   without joining `_BACKEND_BINDING_MODULES` would let a test reach the
   network. `openkos.api._wiring` joins that tuple in the slice that creates it.
4. **Previews and the pending/status reads leak internal types.**
   `MergeDecisionPreview.prepared` is a `lifecycle.PreparedMerge`
   (curate_decisions.py:371), `PendingReport.items` are `pq.PendingItem` and
   `attention` are `jobs.JobRecord` (pending_queue_report.py:199-204), and
   `StatusReport` carries `lint.LintFinding`, `okf.BundleSurvey` and a
   `QueueSnapshot` of `pq.PendingItem` (status.py:105-210). Each gets an
   API-owned result type (Decision 3).
5. **v1 apply runs no body reconciliation and no derived-store refresh.** The
   merge reconciliation port is the one place apply may reach a model
   (curate_decisions.py:187-192), and the CLI refreshes the derived stores once
   at the end of `curate` through `_refresh_derived_after_write`
   (main.py:15896), an adapter-side helper with one definition. v1 passes
   `DecisionPolicy(no_reconcile=True)` to both preview and apply (so they agree)
   and refreshes nothing; `status` reports the stale indexes, and the daemon's
   maintenance pass refreshes them. Both are additive later.

### Spec reconciliation with the shipped services (resolved)

The spec phase ran concurrently and first contradicted these shipped contracts.
The spec revision aligned every row below with the design; the table is kept
as the record of why each contract reads as it does.

| Spec statement | Shipped contract | Design |
|---|---|---|
| "`QuoteStale` is raised" (scenario "A stale quote is refused"); `QuoteStale` listed as an `OpenKosError` | `QuoteStale` is a frozen dataclass "Returned, never raised" (judging.py:166-172, ADR-0059 Decision 2) | `run_judging` returns `JudgingOutcome \| QuoteStale \| RunRefused`; nothing is raised for staleness |
| "A stale decision ... a typed `OpenKosError` is raised" | `apply_decision` returns `Stale` (curate_decisions.py:113-123, ADR-0053) | `apply_decision` returns `Applied \| NotApplied \| Stale` |
| `Workspace.open` from a nested directory "with the same discovery rule the CLI uses" | The CLI has no upward discovery: every verb uses `Path.cwd()` and `config.require_workspace(root)` (main.py:1694 and siblings; config.py:893) | `Workspace.open(path)` requires the root itself, the CLI's rule. A nested path is `WorkspaceNotFound` |
| "The quote ... MUST NOT resolve a backend" | The quote constructs (never calls) a client to read `locality` and the task model (judging.py:457-490, ADR-0059 Decision 10) | The quote builds a client and makes no call; `refresh` and the reads build none |
| "no vendor-specific exception type escapes" | `run_judging` re-raises `BackendUnavailable` and `BackendModelNotFound` unchanged; the instance is a vendor leaf such as `OllamaUnavailable` | The neutral bases are exported and catchable; the vendor leaf class is not exported. Wrapping was rejected by the proposal |

## Architecture Decisions

### Decision 1: Package layout and the public names

**Choice**: one package with private modules, so the only import path is
`openkos.api`:

```
src/openkos/api/
  __init__.py     docstring (owner-local, synchronous, no disclosure gate), API_VERSION, __all__, re-exports
  _workspace.py   Workspace, WorkspaceNotFound, WorkspaceUnreadable
  _reads.py       status, pending_report and their result types
  _decisions.py   list_decisions, preview_decision, apply_decision and their result types
  _judging.py     quote_judging, run_judging, ChatClient, ChatClientFactory
  _refresh.py     refresh_decision_queue, QueueRefresh, KindRefresh
  _wiring.py      _backend_factories, default chat client, commit section, auto-commit port
src/openkos/errors.py   OpenKosError (leaf: imports nothing from openkos)
```

`API_VERSION: Final = 1`. `__all__` (sorted in the file; grouped here):

| Group | Names | Origin |
|---|---|---|
| Version | `API_VERSION` | API |
| Handle | `Workspace` | API |
| Errors | `OpenKosError` | `openkos.errors` |
| | `WorkspaceNotFound`, `WorkspaceUnreadable` | API |
| | `WriteRefused`, `Refused`, `WriteFailed`, `DriftDetected` | re-export, `application.write_gate` |
| | `JudgingRefused` | re-export, `application.judging` |
| | `QueueUnavailableError` | re-export, `application.pending_queue_report` |
| | `QueueRefreshRefused` | re-export, `application.queue_refresh` |
| | `WorkspaceBusyError`, `WorkspaceLockUnavailableError` | re-export, `lock` |
| | `BackendError`, `BackendUnavailable`, `BackendModelNotFound`, `BackendGenerationCapped` | re-export, `llm.base` |
| Reads | `status`, `WorkspaceStatus`, `HealthCounts`, `ActivityEntry`, `JobSummary` | API |
| | `pending_report`, `PendingReport`, `PendingRow`, `KindCounts` | API |
| Decisions | `list_decisions`, `Decision`, `DecisionKind`, `Evaluation`, `IdentityDetail`, `RelationDetail`, `TierDetail` | API |
| | `preview_decision`, `DecisionPreview`, `MergePlan`, `RelatePlan`, `TierPlan`, `KeepDistinctPlan` | API |
| | `apply_decision`, `ApplyOutcome` | API (function, alias) |
| | `Action`, `WithheldAction`, `DecisionVersion`, `Applied`, `NotApplied`, `Stale` | re-export, `application.curate_decisions` |
| Judging | `quote_judging`, `run_judging`, `ChatClient`, `ChatClientFactory`, `RunResult` | API |
| | `JudgingKind`, `JudgingQuote`, `QuoteToken`, `QuoteBinding`, `QuoteNotice`, `WorkItem`, `JudgingOutcome`, `QuoteStale`, `RunRefused`, `JudgingProgress`, `JudgingObserver` | re-export, `application.judging` |
| | `StopToken` | re-export, `application.runtime` |
| | `LLMBackend`, `Message`, `BackendHostLocality` | re-export, `llm.base` |
| Refresh | `refresh_decision_queue`, `QueueRefresh`, `KindRefresh` | API |

**Re-export rule**: a type is re-exported when it is already a plain, frozen,
leak-free data contract whose every annotation resolves to another exported
name (the judging protocol types, `DecisionVersion`, `Action`, the apply
outcomes, `StopToken`). It is wrapped when any field or parameter would expose an
unexported `openkos` type, a `state.*` type, `sqlite3`, or an unversioned stored
mapping. `JudgingPorts` and `DecisionPorts` are **not** exported: they carry
graph, resolution and lifecycle types (judging.py:302-325). The caller overrides
the chat client through one API parameter that the API maps onto the existing
`JudgingPorts.chat_client` port.

**Alternatives considered**:
- Public submodules (`openkos.api.decisions`). Rejected: two import paths per
  name double the surface the snapshot must pin, and v1 is small.
- Re-exporting `DecisionItem`, the preview bodies and `PendingReport`. Rejected:
  each leaks internal types (correction 4).

**Rationale**: one import path, one `__all__`, one snapshot. Private module
names keep the completeness test honest: the package binds no public name it
does not mean to export.

### Decision 2: `DecisionItem.payload` is wrapped, never exposed

**Choice**: `list_decisions` returns API-owned `Decision` values whose kind
details are typed. The payload mapping, its digest and the adjudicator's
confidence are not exposed. Payload key knowledge stays in its two existing
homes: `queue_producers` writes it, `curate_decisions` reads it. The API never
indexes a payload by key: `curate_decisions` gains public readers
(`relation_fields`, promoted from `_relation_fields` at curate_decisions.py:1037,
plus `identity_detail_fields` and `tier_detail_fields`), and the API builds its
detail types from their tuples. A payload those readers cannot parse yields
`detail=None`; the item is still listed (ADR-0053: list, never hide).

**Alternatives considered**:
- Document `payload` as opaque and outside the promise. Rejected: the first
  client must render the rationale and the proposed type; telling it to parse an
  unversioned mapping makes the promise meaningless for the one thing the review
  screen shows.
- Exposing confidence. Rejected: `DecisionItem`'s contract is that nothing ranks
  by or presents the adjudicator's confidence (curate_decisions.py:978-980).

**Rationale**: the stored JSON can change in a producer without an API bump,
because only the reader functions change.

### Decision 3: API-owned result types for reads, previews and refresh

**Choice**: see Interfaces. `WorkspaceStatus` maps `StatusOverview` and
`StatusReport` to counts and strings (lint findings become counts in v1; the
findings themselves are additive later). `PendingReport` keeps the "not
computed" state distinct (`queue_computed: bool`) and lists open rows with the
resolving command `pending` prints (`pending_queue_report.row_subject`,
`row_resolving_command`). Previews become per-action plans holding only ids,
types and flags. `QueueRefresh` maps each `ProducerResult` to a `KindRefresh`.

**Alternatives considered**: re-exporting `ProducerResult`. Rejected: it is the
daemon's internal tally; freezing it would make an internal producer change an
API bump.

**Rationale**: every public annotation resolves to an exported name, which the
leak guard checks mechanically (Decision 11).

### Decision 4: The `Workspace` handle

**Choice**:

```python
@dataclass(frozen=True)
class Workspace:
    root: Path
    _layout: config.WorkspaceLayout = field(init=False, repr=False, compare=False)

    @classmethod
    def open(cls, path: str | os.PathLike[str]) -> Workspace: ...
```

- `open` takes `Path(path).absolute()` (never `resolve()`: the #926 symlink
  refusal in `require_workspace` must see the path as given), then:
  `config.require_workspace(root)` is `None` → continue; otherwise
  `config.workspace_absent(root)` (config.py:885) → `WorkspaceNotFound(path,
  reason)`, else `WorkspaceUnreadable(path, reason)` (permission denied, symlinked
  segment). `config.read_config(root)` raising `OSError`/`ValueError` →
  `WorkspaceUnreadable`. Nothing is created or written.
- `__post_init__` performs the same checks, so `Workspace(root=p)` and
  `Workspace.open(p)` are equivalent; `_layout` is set there with
  `object.__setattr__`. The snapshot records only non-underscore fields.
- The configuration is read once to validate and **is not cached for
  services**. Every operation passes `ws.root` or `ws._layout` to services that
  re-read `openkos.yaml` themselves. A desktop app holds a handle for hours;
  judging's `QuoteStale` exists precisely because the model, backend or
  locality can change between quote and run (judging.py:1146-1155), and a cached
  config would hide that.
- The `root: Path` versus `WorkspaceLayout` split in the services is hidden: an
  API function passes `ws.root` to `run_judging`, `quote_judging`,
  `preview_decision`, `apply_decision` and `refresh_decision_queue`, and
  `ws._layout` to `list_open_decisions`, `read_report`, `read_status_overview`,
  `build_status_report` and `preview_decision`/`apply_decision` (which take both).

**Alternatives considered**: functions taking a `Path`. Rejected by the
proposal; it also pushes the split onto every caller. A cached `Config` on the
handle. Rejected for the staleness reason above.

### Decision 5: The identity stage is split into compute and commit halves (#1391)

**Choice**: `application/identity_stage.py`:

```python
def identity_report(
    root: Path,
    layout: config.WorkspaceLayout,
    *,
    find_candidates_report: Callable[..., CandidateGroupReport],
) -> duplicates_service.DuplicatesReport:
    """LLM-free: report_duplicates, then drop judged-different groups (#1226)."""

def enqueue_identity_report(
    conn: sqlite3.Connection,
    layout: config.WorkspaceLayout,
    report: duplicates_service.DuplicatesReport,
    *,
    commit_section: CommitSection,
    clock: Callable[[], datetime] | None = None,
) -> queue_producers.ProducerResult:
    """servable_stored_verdicts, then queue_producers.enqueue_identity."""
```

The daemon keeps `_identity_stage(ctx)` (13 test references in
`tests/unit/cli/test_daemon.py`) as:

```
report = identity_stage.identity_report(ctx.root, ctx.layout,
    find_candidates_report=lambda *a, **k: resolution.find_candidates_report(*a, **k))
_disclose(ctx.notify, "identity", report.truncation_notice)
identity_stage.enqueue_identity_report(ctx.queue(), ctx.layout, report,
    commit_section=ctx.commit_section)
return StageResult()
```

- Order is preserved exactly: the truncation notice is disclosed before the
  enqueue, so a busy commit still has the notice announced (today's behavior,
  daemon.py:274-289).
- Patch survival: tests patch `openkos.resolution.find_candidates_report`
  (test_daemon.py:414 and siblings), reached through the daemon's call-time
  lambda, and `duplicates_service.report_duplicates` (test_daemon.py:1171),
  reached because `identity_stage` calls it through the module attribute.
- `uses_model=False` stays on the stage registration (daemon.py:443). No client
  is built in either half.

**Alternatives considered**: one function that returns the notice in its
result. Rejected: the notice would be lost when the enqueue raises
`WorkspaceBusyError`, a behavior change. Keeping it in `cli/daemon.py`.
Rejected: `application/` cannot import the CLI, so refresh could not share it.

### Decision 6: Store-only builders, and which kinds can retire

**Choice**:

| Kind | Builder | Serving rule | `complete` | Retires |
|---|---|---|---|---|
| identity | `identity_stage.identity_report` + `servable_stored_verdicts` | rubric, member set and member digests (ADR-0059 Decision 6) | `report.truncation_notice is None` (queue_producers.py:338), the daemon's contract | yes, under `publish` |
| relation_type | `suggest_relations_service.stored_edge_suggestions(layout)` | the latest row per directed pair; digest rows for exactly both endpoints, each equal to the current content hash; type validates | always `False` | no |
| volatility | `suggest_volatility_service.stored_volatility_answers(layout, cfg)` | `cached_answers` under the task model, keyed by the exact prompt digest (suggest_volatility_service.py:100-135) | always `False` | no |

- **Relations.** The per-row check of `partition_edge_suggestion_serves`
  (suggest_relations_service.py:442-468) moves into one predicate,
  `servable_edge_suggestion(stored, edge, current_digest, *,
  include_confidential: bool | None) -> EdgeSuggestion | None`, which the
  partition calls unchanged and the builder calls with `include_confidential=None`
  (bit not compared), mirroring `servable_verdict`'s precedent. The builder
  iterates the latest stored row per pair (`latest[key] = row`, the partition's
  own rule at :435-437) with `Edge(source_id, target_id)` (untyped,
  graph/base.py:29) and reconstructs `corrected_edge` from the stored rationale,
  so its payloads are byte-identical to the daemon's for the same row and the
  daemon's upsert is `UNCHANGED`. An edge that was typed, unlinked or forgotten
  since changed an endpoint's bytes, so its row is not served.
  **Why relations cannot be complete**: retiring needs "every candidate edge was
  answered", and the candidate set comes from the graph walk filtered by the
  local exemption, which needs a client's `locality` (suggest_relations_service.py:595-628).
  A store-only pass also cannot see a daemon row whose suggestion was published
  but whose fail-open persist failed (:544-556); retiring it would discard paid
  work. So refresh inserts and amends, and the daemon keeps retirement.
- **Volatility.** The prompt a cached answer is keyed on depends on which
  concepts were sampled, which depends on the effective confidential bit
  (`include_confidential or local_exemption`), which needs a client to resolve.
  Refresh resolves no client, so it plans both regimes with the existing LLM-free
  `volatility_typing.plan_types` (volatility_typing.py:407): excluded
  `(include_confidential=False, local_exemption=False)` and included
  `(include_confidential=True, local_exemption=False)`. A type answered in one
  regime is published from that answer; a type answered in both with identical
  `(suggested_tier, rationale)` is published once; a type answered in both with
  different answers is **skipped** (never guessed) and counted in
  `KindRefresh.ambiguous`. A type equal to its current effective tier yields no
  proposal (the existing `volatility_suggestion_proposal` rule). `complete` is
  false for the same two reasons as relations: the true regime is unknown, and a
  fail-open `record_answers` (:173-174) can leave a published row with no cached
  answer.
- **Publishing.** `queue_producers` gains
  `enqueue_stored_relations(conn, suggestions, *, current_digest, bundle_dir,
  commit_section, clock=None)` and `enqueue_stored_volatility(conn, answers, *,
  bundle_dir, commit_section, clock=None)`. Each builds proposals with the
  existing per-item functions (`relation_suggestion_proposal`,
  `volatility_suggestion_proposal`) and calls `publish(..., complete=False,
  amend_same_inputs=True)`. Building a fake `SuggestRelationsOutcome` to feed
  `enqueue_relations` was rejected: `status="completed"` with no truncation would
  make `relations_run_complete` true, which is exactly the dishonest completeness
  this decision avoids. The daemon's `enqueue_relations` and `enqueue_volatility`
  are unchanged, so `job-runtime` behavior is unchanged.
- **Amend, not replace.** Both new entry points pass `amend_same_inputs=True`
  (queue_producers.py:113), so a verdict carried over unchanged inputs amends the
  open row in place and `pending --stats` does not move (ADR-0059 Decision 5).
  Volatility proposals have no input digests, so for them "same inputs" is the
  empty set and any payload change amends; this is the same treatment a
  write-through gets.

**Alternatives considered**:
- Option A from the exploration (the existing services with `max_calls=0` and a
  no-model client). Rejected: it builds a client, resolves the backend and walks
  the graph, and `deferred_by_bound > 0` makes every pass incomplete anyway.
- Building a locality-only client for volatility and relations. Rejected: the
  proposal binds refresh to resolving no backend, and ADR-0059 Decision 6
  rejected the same move for the identity stage.
- Serving only the excluded regime for volatility. Rejected: with the default
  local Ollama the exemption is on, so the judged type would never appear,
  failing #1391's success criterion.

**Assumption to pin**: `sensitivity` treats the two bits as a disjunction, so
`(True, False)` and `(False, True)` plan identically. A test pins it before the
builder relies on it.

### Decision 7: `refresh_decision_queue` service

**Choice**: `application/queue_refresh.py`:

```python
class QueueRefreshRefused(OpenKosError):
    """`root` is not a readable workspace. Nothing was written."""

@dataclass(frozen=True)
class RefreshOutcome:
    identity: queue_producers.ProducerResult
    relation_type: queue_producers.ProducerResult
    volatility: queue_producers.ProducerResult
    volatility_ambiguous: int
    identity_truncation_notice: str | None

def refresh_decision_queue(
    root: Path,
    *,
    commit_section: CommitSection,
    clock: Callable[[], datetime] | None = None,
    find_candidates_report: Callable[..., CandidateGroupReport] = <call-time lambda>,
) -> RefreshOutcome: ...
```

- Validates the workspace and reads the config (needed for the volatility task
  model, `type_tiers` and `rationale_language`); failures raise
  `QueueRefreshRefused`.
- Computes all three outcomes first, with no lock and no model, then opens the
  queue. **An absent queue with nothing to publish is not created**
  (`curate_queue.queue_present` false and every proposal list empty): all-zero
  results, nothing written. Otherwise the queue is opened with
  `derived.open_derived_connection`, and when `pq.queue_exists` is false the
  schema is created inside one `commit_section()` (stricter than the runner's
  unlocked `ensure_schema`, runner.py:377-382, because the spec confines writes
  to commit sections).
- Publishes identity, then relation_type, then volatility. `publish` enters the
  commit section per upsert and for `retire_unseen` (queue_producers.py:120-139);
  no lock is held across the compute phase. A `WorkspaceBusyError` from a section
  propagates unchanged (now an `OpenKosError`); rows already upserted stay, which
  is safe because each is a complete, derived row and the next pass converges.
- Never constructs a client, never imports `application/backends.py` client
  builders, never writes under `bundle/` or to the verdict stores.

**Alternatives considered**: returning a partial result on busy instead of
raising. Rejected by the pending-work delta ("surface the existing busy failure
rather than a silent partial success").

### Decision 8: Error taxonomy

**Choice**: `src/openkos/errors.py`, a leaf that imports nothing from `openkos`:

```python
class OpenKosError(Exception):
    """Root of every typed refusal the public API documents. Catch-compatible:
    each family keeps every base it had."""
```

Families that gain it, keeping existing bases first:

| Class | Before | After |
|---|---|---|
| `llm.base.BackendError` | `Exception` | `OpenKosError` (all `Backend*` and vendor leaves follow) |
| `write_gate.WriteRefused` | `Exception` | `OpenKosError` |
| `judging.JudgingRefused` | `Exception` | `OpenKosError` |
| `suggest_relations_service.SuggestionRefused` | `Exception` | `OpenKosError` (reachable through the judging run; not exported) |
| `pending_queue_report.QueueUnavailableError` | `Exception` | `OpenKosError` |
| `lock.WorkspaceBusyError` | `RuntimeError` | `(RuntimeError, OpenKosError)` |
| `lock.WorkspaceLockUnavailableError` | `RuntimeError` | `(RuntimeError, OpenKosError)` |
| `list_service.ListUsageError`, `concept_read.ConceptNotFound` | `Exception` | `OpenKosError` (named by the spec; not reachable in v1, not exported) |
| `queue_refresh.QueueRefreshRefused` | new | `OpenKosError` |
| `api.WorkspaceNotFound`, `api.WorkspaceUnreadable` | new | `OpenKosError`, carrying `path: Path` and `reason: str` |

- **Layering.** `errors.py` is a leaf, so `lock` (a leaf, AGENTS.md), `llm/base.py`
  (imports nothing from `openkos`, llm/base.py:33-37) and every `application/`
  module may import it without any canonical-to-derived edge, and the
  application layer never imports the CLI. The llm guard bans only `config`
  imports (test_ollama.py:1974-1991).
- **Catch-compatibility** holds by construction: every class keeps every
  previous base, in the same relative order, so every existing `except` clause
  and `isinstance` keeps matching and the CLI's exit codes and messages do not
  change. A test asserts, for each class, that the old MRO is a subsequence of the
  new one.
- **Not raised**: `Stale`, `QuoteStale` and `RunRefused` stay returned values
  (see the reconciliation table). Programming errors (`ValueError` for an
  unknown kind or a bad `wait_seconds`) are not forced under the root.
- **Not in v1**: `IngestRefused`, `BudgetRefused` and the other families of
  operations outside v1 gain the root in the slice that exposes them (additive).

**Alternatives considered**: wrapping internal exceptions in API exceptions.
Rejected by the proposal: it loses identity and duplicates every type. Putting
the root in `application/`. Rejected: `lock` and `llm/base.py` must not import
`application/`.

### Decision 9: The API's composition root (`api/_wiring.py`)

**Choice**:

- **Chat client.** `_backend_factories()` returns
  `application_backends.BackendFactories(ollama=OllamaClient,
  openai_compatible=OpenAICompatibleClient)` from `_wiring`'s own globals at call
  time, the CLI and MCP pattern (main.py:194-206, server.py:216-226). The default
  `JudgingPorts.chat_client` is
  `lambda cfg, task: application_backends.chat_client(cfg, factories=_backend_factories(), task=task)`;
  it receives the service's freshly read `cfg`, never a cached one. An override
  `chat_client: ChatClientFactory` (task name to client) replaces it as
  `lambda cfg, task: override(task)`, so no default client is built.
  `openkos.api._wiring` joins `_BACKEND_BINDING_MODULES` (conftest.py:507) in the
  same slice. `BackendFactories(...)` holds class references, not calls, so
  `test_backend_construction_guard.py` stays unconditional.
- **Commit section.** `lock_wait.locked_commit_section(ws.root,
  wait_seconds=wait_seconds)`; `wait_seconds` defaults to `0` (the CLI's `--wait`
  default, main.py:412-417) and is validated against
  `lock_wait.MAX_WAIT_SECONDS`.
- **Auto-commit.** `vcs/autocommit.py` holds the print-free bodies, lifted from
  the CLI:

  ```python
  @dataclass(frozen=True)
  class CommitOutcome:
      sha: str | None
      skipped: Literal["not_a_repository", "identity_unset", "failed", "nothing_to_commit"] | None
      detail: str | None
      includes_confidential: bool

  def paths_include_confidential(root: Path, paths: Sequence[str]) -> bool: ...   # body of _commit_has_confidential
  def commit_paths(root, paths, message, *, has_confidential=paths_include_confidential) -> CommitOutcome: ...   # body of _autocommit
  @dataclass(frozen=True)
  class ConfigCommitPlan: apply: Callable[[bytes], bytes]                      # was cli.main._ConfigCommitPlan
  @dataclass(frozen=True)
  class ConfigEditConflict: concept_type: str                                # neutral; the adapter words the refusal
  def plan_config_edit(root, concept_type, tier, baseline) -> ConfigCommitPlan | ConfigEditConflict | None: ...   # body of _decide_config_commit
  def commit_config_edit(root, message, plan: ConfigCommitPlan | None) -> CommitOutcome: ...   # body of _autocommit_config_edit
  ```

  The module calls git through `vcs.git` module attributes, so existing patches
  of `openkos.cli.main.vcs_git.<name>` (the same module object) keep
  intercepting. `cli/main.py` keeps `_autocommit`, `_commit_has_confidential`,
  `_decide_config_commit`, `_autocommit_config_edit` and `_ConfigCommitPlan`
  (alias) as delegators that print the exact lines they print today from
  `CommitOutcome`; `_autocommit` passes
  `has_confidential=lambda r, p: _commit_has_confidential(r, p)` so a patch of
  that name keeps working. The API's `DecisionPorts` use
  `autocommit=lambda root, paths, message: _commit(root, paths, message, on_warning)`,
  `plan_config_commit` mapping `ConfigEditConflict` to
  `ConfigCommitRefusal(message)` with API wording, and `autocommit_config_edit`
  over `commit_config_edit`. A skipped commit is reported through the caller's
  optional `on_warning` with API wording and leaves `Applied.commit_sha=None`
  (the documented degradation, curate_decisions.py:93-94).
- **Reconciliation.** Not wired; `DecisionPolicy(no_reconcile=True)` on preview
  and apply (correction 5).

**Alternatives considered**:
- The CLI delegating to the API's wiring now. Rejected: rewiring the CLI is out
  of scope, and the CLI's factories must stay patch-friendly at
  `openkos.cli.main.*` (about 144 patches, main.py:202-203). Two compositions
  coexist; both go through `application/backends.py`, and a parity test pins
  that they select the same backend and model. The CLI may later delegate.
- A no-commit default for the API. Rejected: a desktop user would lose
  `git revert` as the undo for every decision, against "history via git".
- An API-local copy of `_autocommit`. Rejected: a fork of a shared write helper,
  the hazard `test_shared_write_helpers_are_never_forked` exists for.

### Decision 10: Import and boundary guards

**Choice**:

- **API import allowlist** (`tests/unit/api/test_api_guards.py`): every module
  under `api/` may import only the standard library, `openkos.api.*`,
  `openkos.errors`, `openkos.config`, `openkos.lock`, `openkos.application.*` and
  `openkos.llm.base`; additionally `_wiring.py` may import `openkos.llm.ollama`,
  `openkos.llm.openai_compatible` and `openkos.vcs.autocommit`. Everything else
  fails, and the failure names the module and the import. `openkos.cli`,
  `openkos.mcp` (including `mcp.gate`), `typer`, `rich`, `asyncio`,
  `openkos.state` and `sqlite3` are asserted by name as well, so the spec's
  scenarios each have a direct check. An allowlist is stronger than the spec's
  denylist.
- **Nothing imports the API**: no module under `src/openkos/` outside `api/`
  imports `openkos.api` in v1. This bars `mcp/` from reaching owner-local
  services through the facade, and keeps the CLI off the API until a change
  decides otherwise. The future local HTTP adapter will have to add itself to
  this guard deliberately, next to its disclosure gate.
- **Owner-local boundary, generalized**
  (`test_curate_decisions_boundary.py`): `_ADAPTERS = ("mcp", "api")` becomes a
  scan of every top-level package under `src/openkos/` except `application`,
  `cli` and `api`. `mcp` stays barred, and every future package (including the
  HTTP adapter) is barred by construction. The spelling matrix and the
  tolerated-missing-package tests are kept, retargeted at a temporary package
  named `localapi`.
- **Fresh interpreter**: `subprocess.run([sys.executable, "-I", "-c", ...])`
  imports `openkos.api` and asserts `openkos.cli` and `typer` are absent from
  `sys.modules`.
- **`test_layering.py`** is not weakened: its scans already cover new
  `application/` modules (`identity_stage.py`, `queue_refresh.py`) by glob. Two
  additions: `test_application_directory_is_scanned_completely` asserts the two
  new modules are scanned, and `test_shared_write_helpers_are_never_forked` adds
  `commit_paths`, `plan_config_edit` and `commit_config_edit` to its set (one
  definition each, never under `application/`).
  `test_application_private_names_are_never_consumed_across_the_boundary`
  already scans `api/` (it walks all of `src/` outside `application/`), which is
  why Decision 2 promotes `_relation_fields` instead of reaching it.

### Decision 11: The surface snapshot

**Choice**:

- **Location and format**: `tests/unit/api/api_surface.json`, generated by
  `tests/unit/api/surface.py`, compared by `tests/unit/api/test_api_surface.py`.
  JSON with sorted keys and two-space indent, so a review diff reads line by
  line:

  ```json
  {
    "api_version": 1,
    "names": {
      "Applied": {"kind": "dataclass", "frozen": true, "fields": [
        {"name": "decision_key", "annotation": "str | None", "default": false}, ...]},
      "apply_decision": {"kind": "function", "parameters": [
        {"name": "workspace", "kind": "POSITIONAL_OR_KEYWORD", "annotation": "Workspace", "default": null}, ...],
        "returns": "Applied | NotApplied | Stale"},
      "OpenKosError": {"kind": "exception", "bases": ["Exception"]},
      "JudgingKind": {"kind": "alias", "value": "Literal['identity', 'relation_type', 'volatility']"},
      "API_VERSION": {"kind": "constant", "type": "int"}
    }
  }
  ```

  Classes also record public methods and properties (signatures), so
  `StopToken`, `JudgingObserver`, `ChatClient` and `LLMBackend` are pinned.
- **Rendering**: annotations are evaluated (`annotationlib.get_annotations` with
  `Format.VALUE`, Python 3.14) and rendered canonically: a class exported in
  `__all__` renders as its exported name; any other class renders as
  `module.qualname`. Source spelling therefore never moves the snapshot, and an
  internal type shows up with its module path.
- **Leak check (same walk)**: any rendered name that starts with `openkos.` (an
  unexported engine type, which covers every `openkos.state.*` type) or with
  `sqlite3.` fails, naming the public symbol and the annotation.
- **Classifier**: the test diffs the committed snapshot against the live
  surface and classifies each change:

  | Change | Class |
  |---|---|
  | name added; optional parameter (with default) added; dataclass field with a default added; exception base added; `Literal` widened in a returned or field annotation | additive |
  | name removed or renamed; parameter removed, renamed, reordered, re-kinded, re-annotated or made required; default removed; required parameter added; field removed or re-annotated; field without default added; exception base removed; return annotation changed (other than `Literal` widening) | breaking |

  The test fails on any difference from the committed file. After
  regeneration it passes only if every breaking change is matched by
  `API_VERSION` greater than the version recorded in the **previous** committed
  snapshot. The previous snapshot is read from `git show HEAD:<path>` when the
  working tree differs from `HEAD`, and from the file otherwise; the test skips
  that half only when git is unavailable, and says so.
- **Regeneration**: `uv run python -m tests.unit.api.surface --write`, in the
  same commit as the surface change, and with the `API_VERSION` bump when the
  classifier reports a breaking change. The module prints the classified diff
  before writing.
- **Completeness**: `set(__all__) == {n for n in vars(openkos.api) if not
  n.startswith("_")}` minus nothing; every `__all__` entry resolves; `__all__`
  has no duplicates. Re-exported internal modules are never bound (no
  `from openkos.application import judging` at package level).

**Alternatives considered**: a text snapshot. Rejected: the classifier needs
structure. A snapshot keyed on source text (`Format.STRING`). Rejected: renaming
an import alias would break it without any API change. Semver on the package.
Rejected: 0.x promises nothing (proposal).

### Decision 12: Concurrency, cancellation and progress

**Choice**:

- The API holds no process state. A `Workspace` is an immutable value; any
  number of threads may call the API on it. Workspace mutual exclusion is the
  workspace lock, taken only inside commit sections (ADR-0036); a decision
  changed between preview and apply is caught by its `DecisionVersion`, and a
  judging binding by `QuoteStale`, exactly as between two CLI processes. The
  local HTTP adapter will call these synchronous functions on one worker thread
  per operation (ADR-0021).
- Reads (`status`, `pending_report`, `list_decisions`, `preview_decision`) take
  no lock and build no client. `quote_judging` builds a client and calls
  nothing.
- **Cancellation**: only `run_judging` takes `stop: StopToken | None`
  (`None` = a fresh, never-set token). It is cooperative: a stop is observed
  between chunks, and the documented worst case is one chunk's calls times
  `chat_timeout` (2,400 s for relation type at the default, judging.py:32-44).
  `refresh_decision_queue` and `apply_decision` take no stop token in v1: refresh
  is model-free and bounded, and apply is one item. Adding one later is additive.
- **Progress**: `run_judging(observer=JudgingObserver)`; the service's guard
  already isolates a failing observer (ADR-0059 Decision 8). No other v1
  operation reports progress.
- **Busy**: a commit section that cannot take the lock within `wait_seconds`
  raises `WorkspaceBusyError`, except inside `run_judging`, which retries
  `PERSIST_ATTEMPTS` times and returns `status="unpersisted"`.

## Data Flow

### #1391: refresh over persisted verdicts

```
api.refresh_decision_queue(ws, wait_seconds)
  └─ queue_refresh.refresh_decision_queue(root, commit_section=locked(root, wait))
       ├─ require_workspace, read_config ──────────────── refuse: QueueRefreshRefused
       │   (compute phase: no lock, no client, no model)
       ├─ identity_stage.identity_report ─────────────────▶ bundle (read), decisions/ (read)
       ├─ stored_edge_suggestions(layout) ────────────────▶ findings.db edge_suggestions (read)
       ├─ stored_volatility_answers(layout, cfg)
       │     plan_types ×2 regimes + cached_answers ──────▶ bundle (read), volatility store (read)
       ├─ nothing to publish and no queue? ── return zeros (nothing created)
       │   (commit phase: lock only inside each section)
       ├─ open queue; schema missing? ── with section(): ensure_schema
       ├─ enqueue_identity_report ── publish(complete=report not truncated, amend=True)
       ├─ enqueue_stored_relations ── publish(complete=False, amend=True)
       └─ enqueue_stored_volatility ── publish(complete=False, amend=True)
                         each upsert / retire_unseen: with section(): ... ──▶ findings.db queue
  ◀── QueueRefresh(identity, relation_type, volatility: KindRefresh)
```

### Daemon identity stage after S1

```
_identity_stage(ctx)
  report = identity_stage.identity_report(root, layout, find_candidates_report=<late lambda>)
  _disclose(notify, "identity", report.truncation_notice)          # same order as today
  identity_stage.enqueue_identity_report(ctx.queue(), layout, report, commit_section)
```

### Apply one decision through the API

```
client                 api._decisions                         curate_decisions / vcs
------                 --------------                         ----------------------
preview_decision(ws, key, action)
   └─ preview_decision(root, layout, key, action, policy=no_reconcile) ─▶ reads only
   ◀── DecisionPreview(key, action, plan, version)

apply_decision(ws, version, action, wait_seconds, on_warning)
   └─ apply_decision(root, layout, version, action, policy=no_reconcile,
                     ports=DecisionPorts(autocommit=vcs.autocommit.commit_paths→sha,
                                         commit_section=locked(root, wait),
                                         plan_config_commit, autocommit_config_edit,
                                         on_warning), observer=DecisionObserver())
        ├─ re-preview from current bytes; version differs? ─── return Stale(...)
        ├─ with commit_section(): write core, queue row → applied
        └─ autocommit(root, paths, msg) ─▶ git add/commit -- <paths>  (skipped → on_warning)
   ◀── Applied(key, action, commit_sha) | NotApplied | Stale
```

### Judging through the API

```
quote_judging(ws, kind, include_confidential, include_uncertain, chat_client=None)
   ports = JudgingPorts(chat_client=default|override, commit_section=locked(root, 0))
   └─ judging.quote_judging(root, kind, ..., ports) ── client built, never called
   ◀── JudgingQuote(..., token)

run_judging(ws, token, stop, observer, chat_client=None, wait_seconds=0)
   └─ judging.run_judging(root, token, stop=stop, ports=..., observer=observer)
        token None → RunRefused; binding moved → QuoteStale (both returned)
        per chunk: model call (no lock) → with section(): persist → observer.chunk_persisted
   ◀── JudgingOutcome | QuoteStale | RunRefused
```

## File Changes

| File | Action | Slice | Description |
|---|---|---|---|
| `application/identity_stage.py` | Create | S1 | `identity_report`, `enqueue_identity_report` (Decision 5) |
| `cli/daemon.py` | Modify | S1 | `_identity_stage` calls the two halves; registration unchanged |
| `tests/unit/application/test_identity_stage.py` | Create | S1 | Unit tests (RED first) |
| `application/suggest_relations_service.py` | Modify | S2 | `servable_edge_suggestion` extracted; partition delegates; `stored_edge_suggestions` |
| `application/suggest_volatility_service.py` | Modify | S2 | `stored_volatility_answers` (two regimes, ambiguity skip) |
| `application/queue_producers.py` | Modify | S2 | `enqueue_stored_relations`, `enqueue_stored_volatility` |
| `tests/unit/application/test_stored_builders.py`, `test_queue_producers.py` | Create / Modify | S2 | Builder and publish tests |
| `errors.py` | Create | S3 | `OpenKosError` leaf |
| `application/queue_refresh.py` | Create | S3 | `refresh_decision_queue`, `RefreshOutcome`, `QueueRefreshRefused` |
| `tests/unit/application/test_queue_refresh.py`, `tests/unit/test_errors.py` | Create | S3 | No-model, lock, amend, retire, absent-queue tests; leaf-import test |
| `tests/unit/application/test_layering.py` | Modify | S1, S3, S4 | Scanned-module assertion widened; shared-helper set widened |
| `vcs/autocommit.py` | Create | S4 | Print-free commit core (Decision 9) |
| `cli/main.py` | Modify | S4 | `_autocommit`, `_commit_has_confidential`, `_decide_config_commit`, `_autocommit_config_edit`, `_ConfigCommitPlan` become delegators; output byte-identical |
| `tests/unit/vcs/test_autocommit.py` | Create | S4 | Core tests incl. threat-matrix rows |
| `llm/base.py`, `lock.py`, `application/write_gate.py`, `judging.py`, `suggest_relations_service.py`, `pending_queue_report.py`, `list_service.py`, `concept_read.py` | Modify | S5 | Gain `OpenKosError` (Decision 8) |
| `application/curate_decisions.py` | Modify | S5 | Public `relation_fields` (promoted), `identity_detail_fields`, `tier_detail_fields` |
| `api/__init__.py`, `api/_workspace.py`, `api/_reads.py`, `api/_decisions.py` (list only) | Create | S5 | Skeleton, handle, errors, reads, list |
| `tests/unit/api/__init__.py`, `surface.py`, `api_surface.json`, `test_api_surface.py`, `test_api_guards.py`, `test_workspace.py`, `test_reads.py`, `test_list_decisions.py`, `tests/unit/test_error_root.py` | Create | S5 | Snapshot, guards, behavior |
| `tests/unit/application/test_curate_decisions_boundary.py` | Modify | S5 | Generalized scan (Decision 10) |
| `docs/adr/0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md`, `docs/adr/README.md` | Create / Modify | S5 | ADR-0060, status Proposed, indexed |
| `api/_decisions.py` (preview, apply), `api/_wiring.py` (commit section, auto-commit) | Create / Modify | S6a | Preview plans, apply with commit wiring |
| `api/_judging.py`, `api/_refresh.py`, `api/_wiring.py` (chat client) | Create / Modify | S6b | Quote, run, default wiring, refresh |
| `tests/unit/conftest.py` | Modify | S6b | `"openkos.api._wiring"` joins `_BACKEND_BINDING_MODULES` |
| `tests/unit/api/test_decisions_apply.py`, `test_judging_api.py`, `test_wiring_parity.py`, `test_refresh_api.py` | Create | S6a, S6b | Behavior and parity tests |
| `docs/architecture.md`, `docs/roadmap.md`, `application/__init__.py` | Modify | S7 | Shape only (see Docs) |
| `openspec/specs/pending-work/spec.md`, `openspec/specs/stable-python-api/spec.md` | Merge / Create | S7 | At archive, every heading name-matched |

## Interfaces / Contracts

```python
# src/openkos/api/_workspace.py
class WorkspaceNotFound(OpenKosError):
    path: Path; reason: str
class WorkspaceUnreadable(OpenKosError):
    path: Path; reason: str

# src/openkos/api/_reads.py
@dataclass(frozen=True)
class ActivityEntry: date: str; text: str

@dataclass(frozen=True)
class HealthCounts:
    dangling: int; unextracted: int; unjudged: int; unevidenced: int
    staging_dropped: int; below_source_sensitivity: int
    dangling_provenance: int; unbacked_provenance: int
    exact_title_groups: int; open_contradictions: int; stale_contradictions: int

@dataclass(frozen=True)
class JobSummary:
    kind: str; started_at: str; ended_at: str | None; outcome: str | None
    detail_code: str | None; units_deferred: int; needs_attention: bool

@dataclass(frozen=True)
class WorkspaceStatus:
    root: Path
    sources: int
    concepts: int
    concepts_by_type: Mapping[str, int]          # read-only mapping
    conformance_findings: tuple[str, ...]
    recent_activity: tuple[ActivityEntry, ...] | None   # None: log unreadable; (): empty
    health: HealthCounts
    vectors_missing: bool
    stale_indexes: tuple[str, ...]
    edges: tuple[int, int] | None                # (total, typed); None when not computed
    queue: Literal["present", "absent", "unreadable"]
    open_decisions_by_kind: Mapping[str, int]
    last_job: JobSummary | None
    jobs_unreadable: bool
    warnings: tuple[str, ...]

def status(workspace: Workspace) -> WorkspaceStatus: ...

@dataclass(frozen=True)
class KindCounts:
    kind: str; enqueued: int; applied_as_proposed: int; applied_modified: int
    declined: int; stale: int; open: int

@dataclass(frozen=True)
class PendingRow:
    decision_key: str; kind: str; targets: tuple[str, ...]; subject: str; resolving_command: str

@dataclass(frozen=True)
class PendingReport:
    queue_computed: bool             # False: never computed, distinct from zero rows
    open_rows: tuple[PendingRow, ...]
    stats: tuple[KindCounts, ...]
    attention: tuple[JobSummary, ...]
    jobs_unreadable: bool

def pending_report(workspace: Workspace) -> PendingReport: ...   # raises QueueUnavailableError

# src/openkos/api/_decisions.py
DecisionKind = curate_decisions.DecisionKind      # Literal alias, pinned by the snapshot
Evaluation = Literal["judged_same", "judged_different", "judged_uncertain",
                     "pending_evaluation", "not_applicable"]

@dataclass(frozen=True)
class IdentityDetail:
    member_ids: tuple[str, ...]; okf_type: str; tier: str
    verdict: Literal["same", "different", "uncertain"] | None; rationale: str | None
@dataclass(frozen=True)
class RelationDetail:
    source_id: str; target_id: str; effective_source_id: str; effective_target_id: str
    suggested_type: str; rationale: str
@dataclass(frozen=True)
class TierDetail:
    type_name: str; current_default: str; suggested_tier: str; rationale: str

@dataclass(frozen=True)
class Decision:
    decision_key: str
    kind: DecisionKind
    targets: tuple[str, ...]
    fresh: bool | None
    evaluation: Evaluation
    actions: tuple[Action, ...]
    withheld: tuple[WithheldAction, ...]
    resolves_via: str
    detail: IdentityDetail | RelationDetail | TierDetail | None

@dataclass(frozen=True)
class MergePlan:
    survivor_id: str; absorbed_id: str; survivor_criterion: str
    files_rewritten: int; cross_source_same_pair: bool; cross_type_concern: str | None
@dataclass(frozen=True)
class RelatePlan: source_id: str; target_id: str; relation_type: str
@dataclass(frozen=True)
class TierPlan: type_name: str; tier: str
@dataclass(frozen=True)
class KeepDistinctPlan: member_ids: tuple[str, ...]

@dataclass(frozen=True)
class DecisionPreview:
    decision_key: str; action: Action
    plan: MergePlan | RelatePlan | TierPlan | KeepDistinctPlan
    version: DecisionVersion

ApplyOutcome = Applied | NotApplied | Stale

def list_decisions(workspace: Workspace) -> tuple[Decision, ...]: ...
def preview_decision(workspace: Workspace, decision_key: str, action: Action
                     ) -> DecisionPreview | NotApplied | Stale: ...
def apply_decision(workspace: Workspace, version: DecisionVersion, action: Action, *,
                   wait_seconds: float = 0.0,
                   on_warning: Callable[[str], None] | None = None) -> ApplyOutcome: ...
    # raises WriteRefused family, WorkspaceBusyError

# src/openkos/api/_judging.py
class ChatClient(LLMBackend, Protocol):
    @property
    def locality(self) -> BackendHostLocality: ...
ChatClientFactory = Callable[[str], ChatClient]     # task name -> client
RunResult = JudgingOutcome | QuoteStale | RunRefused

def quote_judging(workspace: Workspace, kind: JudgingKind, *,
                  include_confidential: bool = False, include_uncertain: bool = False,
                  chat_client: ChatClientFactory | None = None) -> JudgingQuote: ...
def run_judging(workspace: Workspace, token: QuoteToken | None, *,
                stop: StopToken | None = None, observer: JudgingObserver | None = None,
                chat_client: ChatClientFactory | None = None,
                wait_seconds: float = 0.0) -> RunResult: ...
    # raises JudgingRefused, BackendUnavailable, BackendModelNotFound

# src/openkos/api/_refresh.py
@dataclass(frozen=True)
class KindRefresh:
    kind: Literal["identity", "relation_type", "volatility"]
    inserted: int; amended: int; unchanged: int; replaced: int
    suppressed: int; retired: int; complete: bool
    ambiguous: int = 0                 # volatility only: types skipped, two regimes disagree
@dataclass(frozen=True)
class QueueRefresh:
    identity: KindRefresh; relation_type: KindRefresh; volatility: KindRefresh
    identity_truncation_notice: str | None

def refresh_decision_queue(workspace: Workspace, *, wait_seconds: float = 0.0) -> QueueRefresh: ...
    # raises QueueRefreshRefused, WorkspaceBusyError
```

The package docstring states, in this order: the surface is in-process and
synchronous; it is owner-local, as the CLI is, and applies no sensitivity
disclosure gate, so a disclosing adapter must apply the local API's gate itself
(ADR-0058); judging is the only operation that sends content anywhere, and only
to the configured backend; cancellation is cooperative and a stop waits for the
chunk in flight; v1 apply does not reconcile merged bodies and does not refresh
the derived stores; `API_VERSION` is the compatibility promise, not the package
version.

## Testing Strategy

Strict TDD, `uv run pytest`. Each slice writes its RED tests first and observes
them fail for the intended reason (an `ImportError` counts only for a test of a
module that does not exist yet; any other test must fail on an assertion).
Gates per slice, run directly and never piped: `uv run pytest --cov` (branch
coverage at least 90%), `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy .`, `uv run python evals/run_self_tests.py`.

| Slice | RED tests first | Then green and guard checks |
|---|---|---|
| S1 | `test_identity_stage.py`: `identity_report` drops a judged-different group and keeps the truncation notice; it never builds a client (`backends.chat_client` patched to fail); `enqueue_identity_report` carries a servable stored verdict and amends (`ProducerResult.amended == 1`, `inserted == 0`) over an open unjudged row; a second call is `unchanged` and `kind_stats` is identical | Existing `tests/unit/cli/test_daemon.py` identity tests unchanged (characterization, green by design); one new daemon test that the notice is announced when the enqueue raises `WorkspaceBusyError`; mutate the disclose/enqueue order and see that test redden |
| S2 | `servable_edge_suggestion` refuses each failed check (missing endpoint digest, extra digest, changed digest, invalid type) and with `include_confidential=None` ignores the bit; `stored_edge_suggestions` payloads equal the daemon's `relation_proposals` for the same stored row (byte equality of `pq.Proposal.payload`); `stored_volatility_answers` serves a type answered in one regime, serves once when both agree, skips and counts a disagreement, ignores another model's rows; the disjunction pin `(True, False)` vs `(False, True)` plans identically; `enqueue_stored_*` never retire (`complete=False`) and amend on a changed payload over equal inputs | Existing partition and suggest-relations characterization tests unchanged |
| S3 | `refresh_decision_queue`: with a chat factory and `backends.chat_client` patched to fail, judged identity, relation and volatility verdicts become open rows; a second refresh leaves `kind_stats` identical for all three kinds; a changed member retires the old row as `stale` and inserts one; identity retires an absent open row on a complete pass; relation and volatility rows the pass did not see stay open; a `source-supersession/1` row is never retired; an absent queue with nothing to publish is not created; every file under `bundle/` and the verdict stores are byte-identical; a recording section proves every write happened inside a section and the compute phase held none (lock taken by another process during compute does not block it, using `lock.workspace_lock` in the test); busy surfaces `WorkspaceBusyError`; `errors.py` imports nothing from `openkos` (AST) | `test_layering.py` scanned-module assertion includes `identity_stage.py` and `queue_refresh.py` |
| S4 | `test_autocommit.py`: each threat-matrix row below; `commit_paths` returns `skipped="not_a_repository"`, `"identity_unset"`, `"failed"` with `sha=None`; `includes_confidential` only for an explicit `confidential`; `plan_config_edit` returns `None` on a clean file, a plan on unrelated dirty edits, `ConfigEditConflict` on an edit to the same tier entry; `test_shared_write_helpers_are_never_forked` widened with the three core names | Existing `test_main_autocommit.py`, `test_config_commit_decision.py`, `test_curate_decision_ports.py` and every CLI golden pass unchanged (byte identity); mutate one echoed line in the delegator and see a test redden |
| S5 | Error root: for each class in the Decision 8 table, the previous MRO is a subsequence of the new one and `issubclass(cls, OpenKosError)`; `Workspace.open` on a root, a non-workspace (`WorkspaceNotFound` naming the path, nothing created), a nested directory (`WorkspaceNotFound`), an unreadable `openkos.yaml` (`WorkspaceUnreadable`); assigning an attribute raises; `status` and `pending_report` under a held lock return, build no client, write nothing; an absent queue reports `queue_computed=False`; `list_decisions` returns typed details and `detail=None` for a malformed payload; guards: allowlist, nothing-imports-api, generalized boundary, fresh interpreter; snapshot: completeness, leak check, classifier unit tests on synthetic before/after dicts for every row of its table, and the committed golden matches | Mutation proofs: add a public name without `__all__` (completeness reddens); remove a dataclass field and regenerate without a bump (classifier reddens); annotate a public return with a `state.*` type (leak check reddens); add `import typer` to an API module (allowlist reddens). Revert byte-exactly and purge `__pycache__` after each |
| S6a | `preview_decision` writes nothing (bundle and queue byte-identical) and returns API plans; `apply_decision` applies one of two open decisions and the other stays open; the applied row is `applied`; a member edited after preview returns `Stale`; a tier decision with unrelated dirty `openkos.yaml` edits commits only the tier change; a non-repository workspace returns `Applied(commit_sha=None)` and calls `on_warning` once; the commit section is the locked one (busy raises `WorkspaceBusyError` with `wait_seconds=0`) | Snapshot regenerated; additive only |
| S6b | Quote with a chat client whose `chat` fails the test returns a token; the default chat client for each `(backend, task)` has the same constructor kwargs as the CLI's `_chat_client` (parity, recording factories patched into both `openkos.cli.main` and `openkos.api._wiring`); an override is used and no default client is built (factories patched to fail); a moved model returns `QuoteStale` with no chat call; a stop set inside the first chunk's chat ends the run `stopped` before the second chunk; an unreachable backend raises an instance of `BackendUnavailable` and `OpenKosError`; `refresh_decision_queue` through the API resolves no client and returns three `KindRefresh` entries; the conftest binding test asserts `openkos.api._wiring` is patched | Snapshot regenerated; `API_VERSION == 1` |
| S7 | Docs and archive; no code | Delta specs name-matched at archive |

## Threat Matrix

Applicable only for the commit wiring: S4 moves the git auto-commit into
`vcs/autocommit.py`, and S6a adds a second caller (the API). No routing, shell
string composition, push or PR automation is added. The rows carry into
`tasks.md` unchanged.

| Boundary | Applicability | Design response | Planned RED tests (S4 unless noted) |
|---|---|---|---|
| Documentation-like paths | N/A: nothing classifies or executes files | — | — |
| Git repository selection | Applicable: the API commits for `ws.root`, which may be a subdirectory of another repository or not a repository | Repository authority is `vcs.git.repo_root(root)` exactly as today; pathspecs are workspace-relative and taken literally; no `-C` override and no cwd dependence (the API never reads `Path.cwd()`) | (1) a workspace nested in an outer repository commits only the listed paths; (2) a non-repository returns `skipped="not_a_repository"` and writes no `.git`; (3) S6a: the API apply from a process whose cwd is another repository commits in the workspace's repository, never the cwd's |
| Commit state | Applicable: a user's staged unrelated file, an empty commit, a dirty `openkos.yaml` | `git add -- <paths>` then `git commit -- <paths>`, never `-a`/`-A`; a config edit commits only the verb's change over `HEAD`'s file; nothing-to-commit is `skipped="nothing_to_commit"`, not a failure | (1) a pre-staged unrelated file is neither committed nor unstaged; (2) re-applying the current tier yields `nothing_to_commit` and no commit; (3) user edits to another `type_tiers` entry stay uncommitted in the working tree; (4) an edit to the same entry is `ConfigEditConflict` and the API returns the refusal with nothing written |
| Push state | N/A: OpenKOS never pushes (the confidential notice states it) | — | — |
| PR commands | N/A: no PR automation | — | — |

## Concurrency, Cancellation And Lock (summary)

See Decision 12. Lock only in commit sections (`lock_wait.locked_commit_section`);
compute phases hold nothing; refresh, apply and the judging persist each enter
their own sections; `wait_seconds` is the only lock policy the caller sets.
Cancellation is cooperative and exists only on `run_judging`.

## ADR-0060 (content to transcribe)

File: `docs/adr/0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md`,
from `docs/adr/template.md`, status **Proposed**, dated when written, with a row
in `docs/adr/README.md`. Title: *ADR-0060: The stable Python API is a versioned
facade package with one error root*.

**Context.** ADR-0039 owes a Python API over the application services,
versioned and declared public, shaped by the desktop app as its first client.
Nothing is public today: the package has no `__all__`, `application/__init__.py`
exports nothing, and the composition root (backend clients, auto-commit, commit
sections) lives in `cli/main.py`, which `application/` may not import. The
services already have a consumer-friendly shape (frozen dataclasses, ports with
defaults, observers, `StopToken`), but they disagree on the workspace argument
(`root: Path` or `WorkspaceLayout`), their refusals share no root, and several
results carry internal types (`state` rows, lifecycle plans, an unversioned
stored payload). The package is 0.x, so its version promises nothing. The
desktop app does not exist yet, so the first shape is chosen without its real
calls; the judging service (ADR-0059) completed the operations the review
screen needs. Separately, judging persists verdicts that only a daemon pass made
visible in the queue (#1391).

**Decision.**
- We add `openkos.api`, a synchronous, in-process facade over `application/`.
  No `application/` module moves or is declared public in place. v1 covers the
  review queue: workspace status, the pending report, listing, previewing and
  applying a decision, judging quote and run, and a model-free queue refresh.
- We version the surface with an integer `API_VERSION`, independent of the
  package version, starting at 1. Within a value, changes are additive; a
  breaking change raises it in the same change. A committed snapshot of every
  public signature, dataclass field and exception base, with a classifier,
  enforces this in CI.
- We re-export application types that are already plain, leak-free contracts and
  wrap the rest in API-owned types; no public annotation may name an unexported
  engine type, a `state` type or `sqlite3`. The stored decision payload is never
  exposed; typed details are built from reader functions that live beside the
  writer.
- We add one error root, `OpenKosError`, in a leaf module. Existing refusal
  families and the neutral `Backend*` errors gain it as an extra base and keep
  every base they had. Outcomes the services return as values (`Stale`,
  `QuoteStale`, `RunRefused`) stay values.
- A `Workspace` handle, opened from the workspace root by the CLI's rule,
  replaces the `Path`/layout split. It validates the configuration once and
  caches nothing for the services.
- The API owns its composition: a third `BackendFactories` binding module for
  the chat client, the bounded-wait commit section, and the git auto-commit
  through a print-free core in `vcs/autocommit.py` that the CLI also calls. The
  CLI is not rewired onto the API.
- The surface is owner-local and applies no disclosure gate; a disclosing adapter
  must apply ADR-0058's gate itself. In v1 no module outside `openkos.api`
  imports it.
- `openkos.api` names the Python API. The local HTTP API of MVP 6 is a separate
  adapter built on it, named in its own change.

**Consequences.**
- Easier: a client works the review queue without importing the CLI or choosing
  between a path and a layout; one `except OpenKosError` catches every documented
  refusal; an accidental breaking change fails CI; later slices (ingest, query,
  doctor, setup) attach to the same handle, root and snapshot.
- Harder: every re-exported dataclass freezes its fields, so an internal change
  to one now needs a deliberate snapshot regeneration and possibly a bump; two
  compositions (CLI and API) coexist and are held together by a parity test, not
  by sharing code; the stored-payload readers must keep their typed results
  stable.
- v1 apply does not reconcile merged bodies and does not refresh the derived
  stores, so the index is reported stale until the next maintenance pass or
  reindex.
- Refresh retires only identity rows: relation-type and volatility completeness
  cannot be proven from persisted state, so their retirement stays with the
  daemon.

**Alternatives considered.**
- Declaring selected `application/` modules public in place: almost no code,
  but public and owner-local helpers stay mixed and nothing enforces which is
  which.
- Moving modules into a public package: a clean boundary at the cost of heavy
  churn across `cli/main.py`, the daemon and `mcp/`, against "start lean".
- Package semver as the promise: 0.x promises nothing, and the package version
  moves for reasons unrelated to the API.
- Wrapping internal exceptions in API exceptions: loses identity and duplicates
  every type.
- Raising for stale decisions and quotes: contradicts ADR-0053 and ADR-0059,
  which return them as data so a caller cannot forget to handle them.
- Functions taking a `Path` instead of a handle: pushes the `Path`/layout split
  onto every caller and gives later slices nowhere to attach.
- Rewiring the CLI onto the API now: a large, byte-identity-sensitive change
  with no client benefit in v1.

## Docs (shape only)

- `docs/architecture.md`: add `api/` (the declared, versioned, in-process Python
  surface), `errors.py` (leaf) and `vcs/autocommit.py` to the package tree; add
  `api` to the "each package is a piece of the architecture" sentence and
  `errors` to the leaf-module list; in "Target architecture" and the adapters
  paragraph (lines 219, 229-231), the planned async adapter is "the local HTTP
  API", built on `openkos.api`; add `tests/unit/api/test_api_guards.py` to the
  list of AST guards. No counts, no "since #NNN".
- `docs/roadmap.md`: the MVP 6 "stable Python API" bullet states that it exists
  for the review queue and that ingest, query, doctor and setup are later slices.
- `application/__init__.py` docstring: the package stays internal; the public
  edge is `openkos.api`.

## Slices

Delivery is auto-chain, stacked-to-main; about 400 authored changed lines per PR
is advisory only (the snapshot JSON is generated and excluded); tests and docs
are never cut to fit. Each slice leaves the CLI, the daemon and the test suite
green, and each reverts on its own.

| Slice | Content | Depends on | Forecast (authored) |
|---|---|---|---|
| S1 | Identity stage lift (Decision 5) | — | ~220 |
| S2 | Serving predicate, store-only builders, `enqueue_stored_*` (Decision 6) | — | ~350 |
| S3 | `errors.py`; `queue_refresh.py` (Decision 7) | S1, S2 | ~330 |
| S4 | `vcs/autocommit.py`; CLI delegators byte-identical (Decision 9) | — | ~320 |
| S5 | ADR-0060; error root on families; `openkos.api` skeleton (`API_VERSION`, `Workspace`, errors, `status`, `pending_report`, `list_decisions`); detail readers; snapshot tooling, golden and guards; boundary generalization | S3 | ~480 |
| S6a | `preview_decision`, `apply_decision`, commit wiring | S4, S5 | ~300 |
| S6b | `quote_judging`, `run_judging`, chat wiring and parity, `refresh_decision_queue` export, conftest binding | S5 | ~320 |
| S7 | Docs; delta specs merged and name-matched at archive; ADR-0060 Accepted | S6b | ~150 |

About 2,470 authored lines across eight PRs, against the proposal's 1,550: the
proposal's S2 splits into S2 (builders) and S3 (service) because the builders
are independently mergeable; S4 is new (correction 1); the proposal's S4 splits
into S6a and S6b. S5 may exceed the heuristic: the first public name must land
with the snapshot, the classifier and the guards that make it a promise, and
splitting them would merge a public package with no enforcement. S1, S2 and S4
are independent and can merge in any order. `API_VERSION` 1 is not published
until S6b merges, so reverting S5 to S6a breaks no external caller.

## Migration / Rollout

No migration. No on-disk format, store schema or queue CHECK constraint changes.
Refresh writes only derived queue rows, which the next daemon pass converges.
Reverting S1 restores the inline daemon stage; reverting S3 removes refresh and
the queue again fills only on daemon passes; reverting S4 restores the CLI-local
commit helpers; reverting S5 to S6b removes the facade, which nothing in the
engine imports.

## Open Questions

- [x] The five spec statements in "Spec reconciliation" were revised in the
  spec before tasks.
- [ ] Not blocking: the CLI prints `insecure_key_warning` and remote-key notices
  before building a client (main.py:280); the API builds clients silently. A
  quote notice carrying the same advisory would be additive; v1 does not add it.
- [ ] Not blocking: whether the CLI should later delegate its composition to
  `api/_wiring.py`, collapsing the two compositions into one.
- [ ] Not blocking: the volatility two-regime plan doubles an LLM-free walk
  measured at 3 ms to 109 ms per invocation (judging.py:41-42); refresh pays it
  once per call. Not measured on large bundles.
