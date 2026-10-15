# Proposal: A Stable Python API For The Review Queue

MVP 6 stage "A stable Python API" ([roadmap](../../../docs/roadmap.md), MVP 6;
[ADR-0039](../../../docs/adr/0039-the-stable-python-api-ships-with-a-desktop-app-as-its-first-client.md)),
with issue #1391 folded in. The full analysis is in `exploration.md` in this folder;
this proposal does not repeat it. Paths are under `src/openkos` unless they start
with `tests/`, `docs/` or `openspec/`.

## Intent

ADR-0039 owes a Python API over the application services that is versioned, declared
public, and shaped by the desktop app as its first client. Today nothing is public:
the package has no `__all__`, `application/__init__.py` exports nothing, and the only
composition root (backend clients, local exemption, autocommit) lives in the 16k-line
`cli/main.py`, which `application/` may not import. A desktop sidecar that wants to
show and work the review queue must either import unstable internals or reimplement
the CLI's wiring.

The review queue also has a gap of its own (#1391). Judging persists verdicts, but a
judged item only appears in the queue after a producer pass inserts its row, and the
only producer pass is the daemon's. The relations and volatility producers take a
model run's outcome, not stored state, so there is no model-free way to rebuild the
queue from what is already persisted. An app that judges while the daemon is off
shows nothing for the work the user paid for.

Why now: the judging application service (ADR-0059) has landed, so every operation
the review screen needs exists as an `application/` service. What is missing is a
declared, guarded public edge over them and a model-free queue refresh.

## Binding scope decisions

Settled by the user. Spec and design must not reopen them.

1. **v1 is the review queue only**: workspace status, the pending report, listing
   open decisions, previewing and applying a decision, judging quote and run, and
   `refresh_decision_queue` (#1391). Ingest, query, doctor and guided setup are a
   later API slice.

Adopted recommendations, stated as the approach below: an `openkos.api` facade; a
separate integer `API_VERSION`; snapshot and import-guard enforcement; a new ADR;
option B for #1391 with the identity stage lifted out of the daemon; and disclosure
left to the local API (ADR-0058).

## Scope

### In scope

- **`openkos.api` facade package** with an explicit `__all__`. It re-exports or wraps
  the v1 operations. No `application/` module moves. API-owned types are introduced
  only where an internal type would leak (for example a `state.*` row or an
  unversioned stored-JSON mapping in a signature).
- **`API_VERSION`**, an integer starting at `1`, independent of the package version.
  Changes within a major are additive only.
- **Enforcement tests**: a golden snapshot of public signatures, public dataclass
  fields and the exception hierarchy, so any diff forces a deliberate bump; an
  `__all__` completeness test; an import guard that `openkos.api` imports only
  `application/` and leaf types, never `openkos.cli`, `openkos.mcp.gate` or `typer`;
  and a check that no public signature mentions `sqlite3` or `state.*` types.
- **Default backend wiring inside the API.** Judging run needs a chat client without
  importing `openkos.cli`. The API builds its default through the existing
  resolvers in `application/backends.py`, and the caller may override it through the
  existing ports.
- **#1391, option B**:
  - Lift the daemon's identity stage (`cli/daemon.py:254-290`) into `application/`;
    the daemon calls the shared stage. Behavior is unchanged.
  - A store-only, model-free builder for relations and volatility that produces the
    producer outcome from persisted verdicts (the edge-suggestions store and the
    volatility cached answers), so the existing `enqueue_relations` and
    `enqueue_volatility` publish it.
  - `refresh_decision_queue`: runs the three model-free stages, makes no model call,
    resolves no backend, holds the workspace lock only inside each commit section,
    amends rows whose inputs are unchanged (no `pending --stats` inflation), and
    retires a row only when its stage's pass is `complete`, under the same contract
    as the producers.
- **A new ADR, ADR-0060** (the next free number after ADR-0059), status Proposed.
- **Docs** where the general shape changes: `docs/architecture.md`,
  `docs/roadmap.md` (MVP 6 stage), and the `application/__init__.py` docstring.

### Out of scope

- **Ingest, query, doctor and guided setup** in the public API. They need their own
  port wiring and error mapping and arrive in a later API slice.
- **Sensitivity disclosure in the Python API.** The Python API is an owner-local,
  in-process surface: its caller is the owner, as with the CLI. The disclosure gate
  stays the local API's job (ADR-0058) and is not re-implemented here. The API's
  documentation says so explicitly.
- The local HTTP API itself, the desktop shell, packaging and model setup.
- Third-party extension points (`Producer`/`Consumer`); they stay in the Horizon.
- Moving `application/` modules into a public package, or declaring them public in
  place.
- Rewiring the CLI onto `openkos.api`. CLI output stays byte-identical.
- Async variants. The core stays synchronous (ADR-0021).
- Any change to the on-disk format or the queue, adjudications or edge-suggestions
  schemas.

## Capabilities

### New Capabilities

- `stable-python-api`: the public package and its promises. It covers the v1
  operation set; `API_VERSION` and the additive-within-a-major policy; the snapshot,
  `__all__` and import-guard enforcement; the workspace handle; the public error
  root; default backend wiring with port override; cancellation and progress as the
  underlying services provide them; and the statement that the surface is
  owner-local and applies no disclosure gate.

### Modified Capabilities

- `pending-work`: ADDED requirements for the model-free refresh: it makes no model
  call, publishes relations and volatility from persisted verdicts, holds the lock
  only in commit sections, amends unchanged inputs without counting them as new or
  stale, and retires only after a complete pass. The existing requirements "An
  Identity Producer Carries A Servable Stored Verdict" and "Writing A Verdict Through
  To An Open Row Does Not Count As A New Or Stale Proposal" are expected to hold
  unchanged; the spec phase confirms.
- `job-runtime`: **expected unchanged.** The daemon calls the lifted identity stage
  with identical behavior. The spec phase confirms and writes no delta if none is
  needed.
- `judging-application-service`, `curate-decisions`, `status`: **expected
  unchanged.** The API wraps them; it does not change their requirements. The spec
  phase confirms.

## Approach

1. **Facade, not move.** `openkos.api` is a thin package over `application/`. Most
   operations are re-exported or wrapped in a few lines; the facade owns only the
   workspace handle, the error root, the default wiring and the API-owned types
   that stop a leak.
2. **Recommended resolutions for the open technical points** (design details them):
   - **Workspace handle: an object.** A frozen `Workspace` opened from a path resolves
     the layout and config once. Services today split between `root: Path` and
     `WorkspaceLayout`; the handle hides that split and gives later slices a stable
     place to hang ingest and query.
   - **Errors: one public root, not wrappers.** A leaf `OpenKosError` base that the
     existing refusal families (`WriteRefused`, `JudgingRefused`, `QuoteStale`,
     `ListUsageError`, `ConceptNotFound` and the others in scope) also inherit.
     Adding a base is catch-compatible, so no CLI handler changes. The neutral
     `Backend*` errors from `llm/base.py` are re-exported as part of the public
     hierarchy. Wrapping was rejected because it loses exception identity and
     duplicates every type.
   - **Default chat client: built in the API from `application/backends.py`.** The
     API resolves the configured backend through `BackendFactories`, with no CLI
     import. The CLI keeps its own patch-friendly wiring in v1; the two compositions
     coexist, and design decides whether the CLI later delegates.
3. **#1391 lands first, then the facade wraps it.** Lifting the identity stage
   follows the judging change's precedent (behavior-preserving, daemon delegates).
   The store-only builders reuse `partition_edge_suggestion_serves` and the volatility
   cached answers, and feed the existing `enqueue_*` functions so the three queue
   properties stay applied in one place (`queue_producers.publish`).
4. **Versioning.** The package is 0.x, so its semver promises nothing about the API.
   `API_VERSION` is the promise. A snapshot diff that removes or changes a public
   name, signature, field or exception base fails until the snapshot is regenerated
   and `API_VERSION` bumped in the same change.

**Slice forecast** (auto-chain; about 400 authored lines per PR is advisory, and
tests and docs are never cut to fit it):

| Slice | Content | Size |
|---|---|---|
| S1 | Lift the identity stage into `application/`; daemon delegates; behavior-preserving tests | ~250 |
| S2 | Store-only relations and volatility builders; `refresh_decision_queue` service; no-model, lock, amend and retire tests | ~400 |
| S3 | ADR-0060; `openkos.api` skeleton: `API_VERSION`, `Workspace`, error root, read side (status, pending report, open decisions); snapshot, `__all__` and import-guard tests | ~400 |
| S4 | Decision preview and apply; judging quote and run with default wiring; refresh exported; snapshot updated | ~350 |
| S5 | Docs, delta specs, archive | ~150 |

About 1,550 authored changed lines across five PRs. S1 and S2 are independent of
the facade and can merge first.

**TDD:** strict mode is active, runner `uv run pytest`. Each slice needs an observed
RED before implementation; S1's behavior-preservation tests are characterization and
green by design.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/api/` | New | Facade, `__all__`, `API_VERSION`, `Workspace`, error root, default wiring, API-owned types |
| `src/openkos/application/` (identity stage module, refresh service) | New | Lifted identity stage; store-only builders; `refresh_decision_queue` |
| `src/openkos/cli/daemon.py` | Modified | Identity stage delegates to `application/` |
| `src/openkos/application/queue_producers.py` | Possibly modified | Entry points for store-built outcomes, if `enqueue_*` cannot take them as is |
| `src/openkos/application/` refusal families, `llm/base.py` | Modified | Gain the leaf error root as an extra base |
| `src/openkos/application/__init__.py` | Modified | Docstring points to `openkos.api` as the public edge |
| `tests/unit/api/` | New | Snapshot golden, `__all__`, import guard, signature-type check |
| `tests/unit/application/test_layering.py`, `test_curate_decisions_boundary.py` | Modified | Allow `openkos.api` to import owner-local services; keep `mcp/` barred; never weakened |
| `openspec/specs/pending-work/spec.md`, new `stable-python-api/spec.md` | Delta / New | At archive |
| `docs/adr/0060-*.md`, `docs/adr/README.md` | New / Modified | Written at design |
| `docs/architecture.md`, `docs/roadmap.md` | Modified | Package tree, public edge, MVP 6 stage |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Re-exported internal dataclasses (`DecisionItem`, judging outcomes) freeze their field sets | High | The snapshot makes every change deliberate; design decides per type whether to re-export or wrap |
| `DecisionItem.payload` is an unversioned stored-JSON mapping | Medium | Design either wraps it in an API-owned type or documents it as opaque and outside the compatibility promise |
| A store-only builder cannot know the full relations candidate set without walking the graph and proximity store, so it cannot prove a pass `complete` | Medium | If completeness cannot be shown model-free, the builder reports `complete=False` and refresh inserts and amends only, retiring nothing. Design states which kinds can retire |
| Refresh inflates `pending --stats` | Medium | Amend over unchanged inputs (`amend_same_inputs=True` already on identity); a test pins it for all three kinds |
| The owner-local services become importable by a disclosing adapter through `openkos.api` | Medium | The import guard bars `mcp/` from `openkos.api`; the API docs state it is owner-local; the local API applies the gate (ADR-0058) |
| Two default compositions (CLI and API) drift | Medium | Both resolve through `application/backends.py`; a test checks the API default selects the same backend and model as `openkos.yaml` |
| Adding a base class to refusal families changes CLI behavior | Low | Catch-compatible by construction; CLI goldens run unchanged |
| The desktop app does not yet exist, so v1 is shaped without its real calls (ADR-0039's condition) | Medium | v1 is limited to the review screen's known operations; additive growth is allowed within the major |
| Cancellation is cooperative only; a judging stop waits for the in-flight chunk | Known | Stated in the API docs, as in ADR-0059 |
| The spec overstates the shipped code | Medium | At verify and archive, check requirements against shipped behavior and this Intent |

## Rollback Plan

The change adds a package and a service and lifts one daemon stage. It touches no
on-disk format or store schema, and every queue row refresh writes is derived and
rebuildable. Each slice is a self-contained PR. Reverting S1 restores the inline
daemon stage; reverting S2 removes refresh, and the queue again fills only on daemon
passes (today's behavior); reverting S3 or S4 removes the facade, which nothing in
the engine depends on. Rows inserted by a refresh before a revert stay valid and are
amended or retired by the next daemon pass. `API_VERSION` 1 is not considered
published until S4 merges, so reverting earlier slices breaks no external caller.

## Dependencies

- ADR-0059 and the judging application service (quote, run, `QuoteStale`).
- ADR-0053 and `curate_decisions` (list, preview, apply).
- ADR-0058 (the local API owns disclosure) and ADR-0021 (synchronous core).
- `application/backends.py` (`BackendFactories`), `lock_wait.locked_commit_section`,
  `runtime.StopToken`, `queue_producers`.

## Non-negotiables preserved

- **Local-first.** Refresh makes no model call and resolves no backend; the API's
  default wiring uses the configured local backend.
- `raw/` is untouched and `bundle/` stays pure OKF; refresh writes only the derived
  queue.
- **Reconstructible.** The queue stays a derived cache rebuilt from persisted
  verdicts.
- **Sensitivity.** The Python API is owner-local; confidential content does not leave
  the device through it, and the disclosure boundary stays at the local API.
- **Human curates, engine maintains.** Applying a decision stays an explicit call per
  item; judging still needs consent to a quote.
- The core stays synchronous, and there is no `engine.py`.

## Success Criteria

- [ ] `import openkos.api` exposes exactly the names in `__all__`, `API_VERSION == 1`,
      and the snapshot golden covers every public signature, dataclass field and
      exception base.
- [ ] Removing or changing a public name, field or signature fails the snapshot test
      until the snapshot and `API_VERSION` change together (shown by a mutation).
- [ ] The import guard fails if `openkos.api` imports `openkos.cli`,
      `openkos.mcp.gate` or `typer`, or a public signature mentions `sqlite3` or a
      `state.*` type.
- [ ] A caller can open a workspace, read status and the pending report, list open
      decisions, preview and apply one, and quote and run judging, without importing
      `openkos.cli`.
- [ ] #1391: after judging persists relation, volatility or identity verdicts with no
      daemon pass, `refresh_decision_queue` makes the judged items appear in the open
      decisions, with no model call and no backend resolved.
- [ ] #1391: a second refresh over unchanged inputs leaves `pending --stats`
      unchanged; refresh holds the lock only inside commit sections; it retires a
      row only after a complete pass.
- [ ] The daemon produces the same queue as before S1 on the existing daemon fixtures.
- [ ] CLI stdout, stderr and exit codes are byte-identical; existing goldens pass
      unchanged.
- [ ] ADR-0060 is written with status Proposed and indexed; the `stable-python-api`
      spec and the `pending-work` delta are merged at archive with every heading
      name-matched.
- [ ] `uv run pytest --cov` passes at or above 90% branch coverage, along with
      `ruff check .`, `ruff format --check .`, `mypy .` and the eval self-tests.
