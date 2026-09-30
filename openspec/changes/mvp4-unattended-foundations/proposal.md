# Proposal: mvp4-unattended-foundations — the substrate MVP 4's unattended engine runs on

## Intent

Refs #1137, #1139, #1140, #1141, #1142, #1143, #1168 (and #1128's deferred
fourth item, now tracked in #1139).

MVP 4 promises that a user can "leave OpenKOS running, drop sources into a
folder, and find the base current — with a short queue holding only the
decisions that were genuinely theirs". The 2026-09-30 pre-MVP-4 audit found
that nothing in the engine is yet built to run without a person at a
terminal, and filed six design issues. Each names a gap that would turn the
first daemon into either a starvation source, an unbounded spend loop, or a
process that applies consequential changes nobody reviewed:

- the workspace lock is held for a whole verb, model waits and prompts
  included, so a background ingest would make every locked verb a person
  types — including `query` — exit 3 (#1137);
- there is no durable log, no clean stop, no per-job deadline, and prompts
  and commit failures assume a human is watching (#1139, #1128);
- the only spend control is an interactive "Proceed?" gate (#1140);
- `findings.db` is an append-only recompute cache and `decisions/` holds
  verdicts, so "enqueue the consequential" has no home (#1141);
- a folder watch would turn every in-progress save into a refusal (#1142);
- any bundle change rebuilds the whole FTS and graph stores (#1143).

The maintainer adopted each issue's recommendation. This change turns them
into one reviewed contract before any code is written, so the daemon is
built on decided ground rather than discovering these in its own log.

Success: `openkos daemon` can run beside a person using the CLI without
either refusing the other for more than a commit phase; it imports settled
inbox files, runs a maintenance pass, never applies a consequential change,
spends within a per-workspace budget, records every outcome, stops cleanly
on SIGTERM, and leaves a queue that `next`, `status`, `pending` and MCP read
without recomputing.

## Scope

### In Scope

1. **Lock scope and location** (#1137). Compute lock-free; lock only a
   commit phase that re-validates write targets *and* sensitivity/provenance
   read dependencies, writes, commits and refreshes FTS/graph. `purge` keeps
   a whole-verb lock; plain `query` takes none. CLI fail-fast by default
   plus `--wait <seconds>`; runner bounded backoff; derived-store contention
   becomes exit 3; the lock file moves to a per-user state directory,
   keeping its realpath-keyed name and owner-only checks, with a
   transitional double acquisition of the legacy lock. ADR-0036 (amends
   ADR-0020).
2. **Runtime prerequisites** (#1139, #1128 item 4). One logging setup
   (stderr under the CLI, a rotating per-user file under the daemon, never
   inside `bundle/`); a cooperative stop flag set by SIGTERM/SIGINT, checked
   between units and before — never inside — a write burst; a per-job
   wall-clock deadline; every prompt on an unattended path as a caller
   policy; auto-commit failure as a recorded, retryable job outcome.
3. **Spend budget** (#1140). An `unattended:` section in `openkos.yaml`
   (`max_calls_per_pass`, `max_calls_per_day`, `max_sources_per_pass`, plus
   the runtime's deadline, maintenance interval, inbox and quiet window);
   per workspace; counted in chat calls; `--auto` skips the question but
   never the budget; exhaustion recorded and surfaced. Provisional defaults
   derived from measurements in the repo (design Decision 3).
4. **Pending-work queue** (#1141). A derived queue table in
   `.openkos/findings.db` with a stable, kind-scoped decision key, payload
   digest, status `pending|claimed|applied|declined|stale`, producer and
   timestamps; producers upsert and retire on a changed digest; the runner
   computes and enqueues only; only human-facing write paths resolve rows;
   declines stay in `bundle/.state/decisions/`; merge candidates from
   `duplicates` included; `next` reads the queue first; a read-only
   `openkos pending` verb with `--stats`, the instrument for the roadmap's
   "how much of the queue is mechanical" measurement. ADR-0037.
5. **Folder watch** (#1142). An external inbox, read-only to the engine;
   debounced over a quiet window; a source edited after import becomes one
   `watch_refusal` queue row, never a re-import. ADR-0038.
6. **Per-document derived refresh** (#1143, second half). FTS and graph
   update per document from a per-document manifest diff; whole rebuild as
   the fallback.
7. **The job runner and `openkos daemon [--once]`** as requirements: one
   workspace, one job at a time, watch and maintenance jobs, recorded
   outcomes in `.openkos/jobs.db`.
8. **#1168 prerequisites.** Extract `reindex` and the findings computations
   (`duplicates`, `contradictions`, `suggest-relations`,
   `suggest-volatility`) into application services, because the runner must
   not import the CLI.
9. **Governance.** ADR-0036, ADR-0037, ADR-0038 (Proposed, indexed).
   Shape-level doc updates when the code lands: the state taxonomy
   (`jobs.db`, lock location), the leaf-module rule, `docs/cli.md` for
   `daemon`, `pending`, `--wait` and exit 3.

### Out of Scope

- **Source versioning** — importing changed inbox bytes as a new version
  with a `supersedes` link (deferred by ADR-0038).
- **Per-machine or cross-workspace budgets**, token or currency budgets,
  and counting embedding calls against the budget.
- **Auto-applying any consequential work**, including an ADR-0034 class;
  the queue measures, it does not act.
- Installing or supervising the daemon as an OS service; watching several
  workspaces from one process; parallel jobs.
- An OS file-notification backend (the watch polls).
- A stat-based shortcut for the manifest hash walk.
- Moving the merge/reconcile/unmerge cores and splitting `test_ingest.py`
  (#1168's remaining slices are not prerequisites of the daemon).
- Holder identity in the lock file, or waiting by default when the daemon
  holds the lock.

## Capabilities

### New Capabilities

- `workspace-lock`: which commands lock, for how long, the commit-phase
  rules, `--wait`, exit 3 for all contention, the lock's location and
  owner-only checks, the transitional legacy lock.
- `job-runtime`: `openkos daemon`, job kinds, recorded outcomes, logging,
  cooperative stop, deadline, policy-answered prompts, commit-failure retry.
- `unattended-budget`: the `unattended:` keys, their validation and
  defaults, counting and admission, `--auto` semantics, exhaustion.
- `folder-watch`: inbox configuration, settling, refusal into the queue.

### Modified Capabilities

- `pending-work`: the queue, keys, upsert, resolution, `openkos pending`,
  measurement counters.
- `derived-index-cache`: whole rebuild replaced by per-document update with
  fallback; per-document manifest.
- `reindex-command`: lock contention exits 3 with a neutral message.
- `query-command`: plain `query` takes no lock; `--save` locks its filing.
- `next-action-pointer`: tier 12 and queue-backed tiers.
- `status`: queue counts and last unattended outcome.
- `workspace-autocommit`: commit failure as a runner outcome.
- `forget-command`, `privacy-purge`: sweeps cover the queue, `jobs.db`, and
  daemon logs.
- `mcp`: `pending` lists open rows through the disclosure gate.
- `curate-command`: reads and resolves queue rows; `--auto` budgeted.
- `ingestion`: extraction lock-free; batch `--auto` budgeted.

## Approach

Build order follows dependency, not issue number: the #1168 extractions
the runner needs; the lock split (with per-document refresh, which keeps
commit phases short); the runtime primitives; the queue; the budget; the
runner and daemon; the watcher. Each work unit is a behavior with its
tests, committed on its own; `tasks.md` slices them near the advisory
~400-line mark with dependencies.

## Principles Impact

- **Local-first & private:** no new dependency, no network surface. The
  daemon log and lock live outside the workspace and outside `bundle/`;
  `purge` deletes the workspace's logs and `jobs.db`.
- **Immutable `raw/`:** strengthened — the watcher never touches `raw/` or
  the inbox, and a changed source is refused, not re-imported.
- **Reconstructible:** the queue is derived and rebuildable; `jobs.db` is
  the first operational (non-derived) store under `.openkos/`, holds no
  knowledge, and fails closed when unreadable (flagged for review).
- **Sensitivity:** the commit phase re-validates sensitivity inputs so a
  concurrent raise is never lost; queue rows pass the MCP disclosure gate;
  the `forget` sweep covers every field of a row that names a concept.
- **Human curates, engine maintains:** now a data structure (the queue)
  and a definition (unattended = runner-initiated), not a flag.
- **Adopt OKF:** no format change; no new frontmatter.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| A split verb under-declares a read dependency and loses a concurrent sensitivity raise | Med, high impact | per-verb sentinel tests, mutation-proven; ADR-0036 names it mandatory |
| `--auto` scripts that batch-ingest many files now stop at `max_sources_per_pass` (behaviour change for existing users) | High for those users | stderr deferral line, exit 0, documented; defaults provisional; open decision O1 |
| Compute spent then refused at commit under contention | Med | catalog re-composition removes the common case; runner records and retries |
| Incremental graph diverges from a whole rebuild | Med | property-test equivalence oracle; any failure falls back to rebuild |
| Two processes resolve different lock directories | Low | account-database home on POSIX; environment ignored for the lock |
| Mixed old/new versions during upgrade | Med | transitional double lock |
| A new `.openkos/` tenant missed by a privacy sweep | Med | `pending_item_targets` column swept by membership; erasure tests |
| Daemon log names concepts outside the workspace | Low | ids/paths only, never text; `purge` deletes the logs |
| `jobs.db` loss resets the daily budget | Low | loss is a user action; unreadable fails closed |

## Rollback Plan

Every slice is behaviour-scoped and revertible in reverse order. Reverting
the daemon and watcher slices removes the new verbs; nothing else depends on
them. Reverting the queue slices leaves an unused table in `findings.db`
(harmless; `purge` deletes it). Reverting the lock split restores
whole-verb locking; the new lock location can stay (it is a pure
relocation, and the transitional double lock keeps old and new builds
mutually exclusive in either direction). Reverting per-document refresh
leaves `doc_manifest` tables that the old whole-rebuild path ignores.
Bundles, `raw/` and `bundle/.state/` are never migrated by this change, so
no rollback touches knowledge.

## Open decisions for the maintainer

- **O1. Exit code and default for a budget-truncated `--auto` run.** The
  design exits 0 with a stderr deferral line, which keeps existing scripts
  green but lets a script miss the deferral; exit 2 (ADR-0022's
  "incomplete" code) would make it visible at the cost of breaking scripts
  that treat non-zero as failure. The design chooses 0.
- **O2. `jobs.db` under `.openkos/`.** Chosen for purge coverage and
  portability with the workspace; it is not reconstructible, which bends
  AGENTS.md's "every SQLite store under `.openkos/` is a derived cache".
  The alternative is the per-user state directory, outside `purge`'s reach.
