# Tasks: mvp4-unattended-foundations — the substrate MVP 4's unattended engine runs on

Refs #1137, #1139, #1140, #1141, #1142, #1143, #1168. Design: `design.md`.
Proposal: `proposal.md`. ADRs: 0036, 0037, 0038 (Proposed, already on disk
with this change). Specs: `workspace-lock`, `job-runtime`,
`unattended-budget`, `folder-watch` (new); `pending-work`,
`derived-index-cache`, `reindex-command`, `query-command`,
`next-action-pointer`, `status`, `workspace-autocommit`, `forget-command`,
`privacy-purge`, `mcp`, `curate-command`, `ingestion` (deltas).

Strict TDD is ON, runner `uv run pytest`: observe RED before GREEN. A test
that passes on its first run proves nothing until the line it guards is
mutated and it fails (revert with the inverse edit, never `git checkout
--`, and purge `__pycache__` before trusting a verdict). Every absence
assertion needs a precondition proving the thing existed. Sensitivity and
privacy properties (read-dependency re-validation, sweep erasure, no
document text in logs) each need a sentinel-value, mutation-proven test.

Sizes are the advisory ~400 authored changed lines per work unit
(additions plus deletions, tests included); where a unit's forecast exceeds
it, the reason is stated rather than the unit split artificially. Each unit
closes with its own Conventional Commit. "Depends on" lists hard
dependencies only.

## Phase 0 — #1168 prerequisites (services the runner calls)

- [ ] 0.1 Extract `reindex` into `application/reindex_service.py`: explicit
  root, typed outcome, typed refusals carrying the exact message text,
  effects through ports, no prompt, no `typer.Exit`. Characterization
  goldens recorded on the old code first.
  Tests: `tests/unit/application/test_reindex_service.py` (outcome shapes,
  each refusal's text byte-equal to the golden); existing
  `tests/unit/cli/test_reindex*.py` stay green unchanged. ~400.
- [ ] 0.2 Extract the findings computations — `contradictions`,
  `duplicates` (candidate groups), `suggest-relations` (edge typing
  compute, no gate), `suggest-volatility` — into application services that
  return verdicts and never write the bundle; each accepts a `max_calls`
  bound (used by Phase 5). Goldens first. May land as two units
  (contradictions+duplicates, relations+volatility). Depends on: none.
  Tests: `tests/unit/application/test_findings_services.py`; absence guards
  that the CLI bodies no longer hold the moved logic. ~2 × 400.

## Phase 1 — Lock: relocation, contention, commit phase

- [ ] 1.1 `userstate.py` leaf module (state, locks and log directories per
  OS; POSIX home from the account database) and lock relocation with the
  transitional legacy lock. Update the leaf-module sentence in
  `openspec/config.yaml` context and `docs/architecture.md`.
  Tests: `tests/unit/test_userstate.py` (per-OS paths via injected
  platform; environment ignored for the lock dir; `XDG_STATE_HOME` honoured
  for logs); `tests/unit/test_lock.py` (new location, owner-only checks
  reused, symlinked-root spelling shares one lock, legacy lock contention
  reported as busy); `tests/unit/cli/conftest.py` snapshot suite stays
  green. ~350.
- [ ] 1.2 Derived-store contention exits 3 with the neutral message
  (`_LOCK_CONTENTION_TEMPLATE`, `_guard_workspace_lock`, `reindex` ladder).
  Tests: update `tests/unit/cli/test_reindex*` lock-contention cases to 3;
  a findings-writer contention case under a non-reindex verb. ~150.
- [ ] 1.3 Commit-phase mechanism: `application/lock_wait.py`
  (`acquire_with_backoff`), the `commit_section` port type, three-class
  command classification (read-only / locked / self-locking) with
  `test_every_command_is_classified` extended, and `--wait <seconds>` added
  by the guard to every locked verb (exit 2 on a bad value; one waiting
  line). Depends on: 1.1.
  Tests: `tests/unit/application/test_lock_wait.py` (injected clock and
  sleep; bound respected; jitter within range);
  `tests/unit/cli/test_workspace_lock_wiring.py` (`--wait 0` equals
  default; `--wait 10` succeeds after release; expired wait exits 3). ~400.
- [ ] 1.4 Plain `query` lock-free; `query --save` commit phase with its
  read dependencies (cited concepts' sensitivity). Depends on: 1.3.
  Tests: `tests/unit/cli/test_query_lock.py` (answers while lock held;
  sentinel: cited concept raised to confidential between phases → exit 3,
  nothing filed; mutation: drop the dependency → test fails). ~300.
- [ ] 1.5 Ingest commit phase: `IngestPorts.commit_section`, read
  dependencies (sensitivity/provenance inputs), catalog re-composition from
  a staged catalog delta, per-file commit in a batch, vector upsert
  re-taking the lock with a content-hash re-check. Depends on: 1.3.
  Forecast ~500: the catalog-delta representation and its tests are one
  behaviour and splitting them would leave a half-migrated `_Prepared`.
  Tests: `tests/unit/application/test_ingest_service_commit_phase.py`
  (lock not held during a blocking fake `chat`; concurrent `index.md`
  append re-composed, both entries present; changed concept target → exit
  3; sentinel sensitivity race; kill-point tests from ADR-0035 still
  green).
- [ ] 1.6 Lifecycle verbs' commit phase (merge, unmerge, forget, relate,
  set-sensitivity, set-volatility, sync-tags, normalize-names, repair,
  adjudicate `--apply`, reconcile, suggest-relations `--apply`): prompts
  outside the lock, read-dependency sets declared per verb. Two or three
  units grouped by shared core. Depends on: 1.3.
  Tests: per verb, one "prompt does not hold the lock" test and one
  read-dependency sentinel where the verb computes sensitivity or
  provenance. ~3 × 400.
- [ ] 1.7 `curate`: stage compute lock-free; each item's write and each
  findings persist in its own commit phase with input re-validation (drop
  rows whose inputs vanished). Depends on: 1.6.
  Tests: `tests/unit/cli/test_curate_lock.py` (persist after a concurrent
  `forget` of an input writes no row naming it). ~350.
- [ ] 1.8 `purge` stays whole-verb; assert it. Derived refresh placement:
  FTS/graph inside the commit phase, embedding outside, vector upsert under
  a brief lock. Depends on: 1.5.
  Tests: `tests/unit/cli/test_purge_lock.py` (a commit phase cannot
  interleave); a purge-during-compute test for a findings writer. ~250.

## Phase 2 — Per-document derived refresh (#1143)

- [ ] 2.1 `doc_manifest` + `schema_version` in `fts.db`; per-document FTS
  update with the whole-rebuild fallback. Depends on: none (lands before
  1.5 is merged if possible, since it shortens the commit phase).
  Tests: `tests/unit/state/test_fts_incremental.py` (one edit rewrites one
  doc's rows; pre-change store rebuilds once then goes incremental;
  manifest mismatch → rebuild). ~350.
- [ ] 2.2 Graph: `doc_outlinks`, per-document node/edge maintenance,
  inbound-link recovery for new documents, global candidate recompute,
  fallback. Depends on: 2.1. Forecast ~450: the outlink table and edge
  re-materialisation are one invariant.
  Tests: `tests/unit/graph/test_graph_incremental.py` (link to a
  not-yet-existing target recovered; removed target drops edges; candidate
  ceiling unchanged).
- [ ] 2.3 Equivalence property test over random edit sequences for both
  stores; the staleness probe stays hash-only.
  Tests: `tests/unit/state/test_incremental_equivalence.py`. ~250.

## Phase 3 — Runtime primitives (#1139)

- [ ] 3.1 `logsetup.py`: one setup for CLI (stderr, WARNING, output
  byte-identical) and daemon (rotating file per workspace digest); fold
  `mcp/server.py`'s setup in. Depends on: 1.1.
  Tests: `tests/unit/test_logsetup.py` (paths per OS; no file under
  `bundle/`; CLI goldens unchanged; sentinel body text never in a log). ~300.
- [ ] 3.2 `jobs.db` store (`state/jobs.py`), disposable: jobs, uncommitted
  paths, watch observations; read-only opener for `status`/`next`/`pending`;
  absent → recreated with no history and no error; present but unreadable →
  typed error naming deletion as the remedy.
  Tests: `tests/unit/state/test_jobs.py` (deleted record → fresh counters,
  no history, no error, no file under `bundle/` or `raw/` touched). ~300.
- [ ] 3.3 Stop flag and deadline primitives (`application/runtime.py`:
  `StopToken`, `Deadline`, `UnattendedPolicy`). The CLI's cost gates are
  unchanged.
  Tests: `tests/unit/application/test_runtime.py`. ~300.

## Phase 4 — Pending-work queue (#1141)

- [ ] 4.1 Queue store (`state/pending_queue.py`): schema, kind-scoped keys,
  upsert algorithm, one-open-row index, claims by live pid, retire-unseen on
  a complete run. Depends on: 1.3 (writes inside a commit phase).
  Tests: `tests/unit/state/test_pending_queue.py` (every upsert branch; a
  typed-edge and merged-body contradiction stay distinct; decline in force
  suppresses insert; dead claimant reads as pending). ~400.
- [ ] 4.2 Producers: contradictions, adjudication/duplicate groups, edge
  suggestions, volatility, revisions enqueue through 4.1. Depends on: 0.2,
  4.1. Tests: `tests/unit/application/test_queue_producers.py` (re-run does
  not duplicate; changed input retires). ~400.
- [ ] 4.3 Resolution in the shared write cores (merge, relate,
  set-volatility, reconcile, decline/keep-distinct writers, ingest for
  `watch_refusal`) with `as_proposed`/`modified`. Depends on: 4.1, 1.6.
  Tests: one per core; an AST/registry test that enumerates the cores so a
  new one cannot forget to resolve. ~400.
- [ ] 4.4 `forget` sweep over `pending_items` / targets / digests with
  erasure; `purge` names `jobs.db` and deletes the daemon logs. Depends on:
  4.1, 3.1, 3.2. Tests: `tests/unit/cli/test_forget_queue_sweep.py`,
  `tests/unit/cli/test_purge_unattended.py` (bytes not recoverable;
  undeletable log reported). ~350.
- [ ] 4.5 `openkos pending` (read-only; `--all`, `--stats` with the lifetime
  caveat; absent queue is not an empty queue). Depends on: 4.1, 3.2.
  Tests: `tests/unit/cli/test_pending.py`. ~350.
- [ ] 4.6 Readers: `next` tiers 9/11 queue-backed and tier 12; `status`
  queue counts and last outcome; MCP `pending` lists rows through the
  disclosure gate; `curate` reads and resolves rows. Depends on: 4.3, 4.5.
  Two units (next+status, MCP+curate).
  Tests: `tests/unit/application/test_next_action.py` additions,
  `tests/unit/cli/test_status.py` additions,
  `tests/unit/mcp/test_pending_queue.py` (withheld row, no count),
  `tests/unit/cli/test_curate_queue.py`. ~2 × 400.

## Phase 5 — Budget (#1140)

- [ ] 5.1 `unattended:` config keys in `config.read_config` with
  validation, template comments, `docs` mention.
  Tests: `tests/unit/test_config_unattended.py` (absent → defaults; unknown
  key refused; bool rejected; ranges); `tests/unit/test_config_keys_specified.py`
  must see `unattended` in a living spec after archive. ~300.
- [ ] 5.2 `CountingBackend`, admission by estimate, daily sum from
  `jobs.db` (absent → zero spent; unreadable → no model call). Runner-only:
  the counting wrapper is installed only by the runner. Depends on: 3.2, 5.1.
  Tests: `tests/unit/application/test_budget.py` (retries counted; embeds
  not counted; over-estimate source never started; a truncated
  contradictions stage resumes with zero repeated calls). ~350.
- [ ] 5.3 Pin that CLI runs are never budget-limited: batch `ingest --auto`,
  `curate --auto`, `suggest-relations --auto`, `revisions --auto` with a
  restrictive `unattended:` section behave exactly as without it.
  Depends on: 5.2.
  Tests: `tests/unit/cli/test_cli_unbudgeted.py` (`ingest notes/ --auto`
  over five files with `max_sources_per_pass: 2` ingests all five, reports
  no deferral, same exit code; no `jobs.db` row added). ~150.

## Phase 6 — Runner and daemon

- [ ] 6.1 Runner core (`application/runner.py`): maintenance job
  (incremental refresh → lint counts → advisors compute-and-enqueue),
  outcomes, backoff on contention within deadline, commit-retry job.
  Depends on: 0.1, 0.2, 2.x, 3.x, 4.2, 5.2.
  Tests: `tests/unit/application/test_runner.py` (no file under `bundle/`
  changes in a maintenance pass; runner never passes a consent flag; stop
  flag before burst writes nothing; deadline defers; `commit_failed`
  retried first); AST guard: runner never imports `openkos.cli`, `typer`,
  `rich`. ~400.
- [ ] 6.2 `openkos daemon [--once]` verb (self-locking class; signal
  handlers; daemon logging; exit 0 on stop). Depends on: 6.1, 3.1.
  Tests: `tests/unit/cli/test_daemon.py` (`--once` runs due jobs in order
  and exits; SIGTERM during a fake burst completes it; idle daemon does not
  hold the lock). ~350.

## Phase 7 — Folder watch (#1142)

- [ ] 7.1 `unattended.inbox` validation (not raw/, bundle/, .openkos/, the
  root, or inside them; a directory). Depends on: 5.1.
  Tests: `tests/unit/test_config_unattended.py` additions. ~150.
- [ ] 7.2 Watch job: poll, settle with injected clock/stat, observations,
  admit/defer, pre-commit re-hash, import through `ingest_source`.
  Depends on: 6.1, 7.1, 1.5.
  Tests: `tests/unit/application/test_watch.py` (unsettled file skipped;
  steady inbox makes no model call; inbox names/bytes/mtimes unchanged;
  deferred files picked up next job). ~400.
- [ ] 7.3 `watch_refusal` rows: refusal on `RawImmutabilityRefused`,
  over-budget source rows, stale on removal/restoration, `applied` when a
  raw copy with the refused bytes lands. Depends on: 7.2, 4.3.
  Tests: `tests/unit/application/test_watch_refusal.py` (repeated saves →
  one row; further edit → stale + new; rename in inbox → import + applied).
  ~350.

## Phase 8 — Docs (shape only, when the code lands)

- [ ] 8.1 `docs/architecture.md`: state taxonomy (`jobs.db` as the first
  operational store; the queue tenant; lock and logs outside the
  workspace); leaf-module rule; runner in the application layer.
  `docs/cli.md`: `daemon`, `pending`, `--wait`, exit 3 for every
  contention, runner-only budgeting, the `unattended:` keys (pointing at the
  config as the authority for defaults). `docs/roadmap.md`: MVP 4 status.
  No counts that rot. ~250.

## Phase 9 — Archive

- [ ] 9.1 Merge every delta into `openspec/specs/`; name-match every
  heading. Rewrite `derived-index-cache`'s Purpose and Non-Goals (they
  still describe whole rebuilds) and `pending-work`'s Purpose ("covers the
  Contradictions advisor only"). Correct the stale "written ONLY by
  `reindex`" wording flagged in design Code reality check 6 if not already
  fixed.
- [ ] 9.2 Flip ADR-0036, ADR-0037, ADR-0038 to Accepted in frontmatter,
  body, and index.
- [ ] 9.3 In ADR-0036, rename `- **Amends (on acceptance):**` to
  `- **Amends:**`; set ADR-0020's status to `Amended by ADR-0036` in its
  frontmatter, body and index row. Run
  `uv run pytest tests/unit/test_adr_index.py`.
- [ ] 9.4 Open follow-up issues: remove the legacy temp-dir lock after one
  release; source versioning for the watcher; an OS notification watch
  backend; the mechanical-fraction measurement run once the queue has
  data; a per-machine budget if a multi-workspace daemon appears.

## Review Workload Forecast

| Field | Value |
| --- | --- |
| Work units | ~32 (Phases 0–8), most near or under 400 authored lines |
| Units over budget, by design | 1.5 (~500), 2.2 (~450), each explained above |
| Critical path | 0.2 → 1.3 → 1.5/1.6 → 4.1–4.3 → 5.2 → 6.1 → 7.2 → 7.3 |
| Parallelizable | Phase 2 alongside Phase 1; 3.1–3.3 alongside 1.4–1.8 |
