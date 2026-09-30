---
type: Decision
title: "ADR-0035: A pending marker on the Source makes an interrupted ingest completable"
description: Ingest writes the Source first carrying `ingest_pending: true` and rewrites it without the key as the last write, so a kill between the Source and the rest of Phase B can never be read as a converged Source (#1136).
status: Proposed
date: 2026-09-30
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-30T00:00:00Z
sensitivity: public
---

# ADR-0035: A pending marker on the Source makes an interrupted ingest completable

- **Status:** Proposed
- **Date:** 2026-09-30

## Context

Phase B of `ingest` is a sequence of individually atomic writes -- raw copy,
Source, each derived concept, `index.md`, `log.md` -- with no transaction and
no journal. `converged_reingest` decides "already extracted" from the Source
alone, and a healthy Source carries no debt marker, so a process killed after
the Source landed and before the rest did left a Source the next ingest skipped
forever: no derived objects, no catalog entry, no report (#1136). A watcher or
daemon that is stopped and restarted (MVP 4) makes that window routine.

The convergence gate has to be right for a kill at every point, and for every
workspace that already exists, none of whose Sources carries anything new.

## Decision

The Source is written FIRST with the frontmatter key `ingest_pending: true`
and rewritten WITHOUT it as the LAST write of the run. A Source still carrying
the key is the durable trace of an interrupted run: `extraction_retry_due`
treats it as retryable debt, so `converged_reingest` falls through to the full
run and the batch skip predictor does not count it free. Only the literal
`true` counts. The key lives in `model/okf.py` beside the other Source keys
and is an OKF §4.1 extension: ignored by anything that does not know it.

The retry also adopts derived objects the interrupted run wrote but never
catalogued. Staging sees them as same-source collisions and drops them as
no-ops, so without adoption a kill between the derived writes and the
`index.md` write would leave them orphaned for good. Adoption runs only for a
Source that was pending, so an object a user removed from the index of a
healthy Source is never resurrected.

A Source-only rewrite of an already converged Source (a changed
`event_date`, lifted frontmatter) stays one atomic write and never carries the
key: it extracts nothing, so the marker would only force a needless
re-extraction after a crash.

## Consequences

- A kill at any point leaves either nothing to recover, or a pending Source
  that the same command completes. No new verb, no journal file.
- The marker is a PENDING marker, not a completion stamp, because absence must
  mean "complete": every Source written before this key existed lacks it and is
  complete, and a completion stamp would force a full re-extraction of every
  source ever ingested.
- The Source is written twice per full run (the second is small and atomic).
- A retry re-runs extraction. Extraction is non-deterministic, so a retry can
  add objects the interrupted run did not produce; this is the same
  accumulation `--re-extract` already has, bounded by the same-source slug
  no-op.
- A kill between the `index.md` and `log.md` writes leaves the catalog complete
  and the log without the "Extracted" lines for the objects that had already
  landed; the retry writes its own re-ingest entry. The log is an audit trail,
  not state anything derives from.
- A kill after the final Source write and before the commit leaves a complete
  but uncommitted ingest, exactly as before; the embed step is a derived cache
  that `reindex` rebuilds. Neither is affected.
- `lint` does not yet name a pending Source; the retry is triggered by
  ingesting the same file again.

## Alternatives considered

- **Reorder: derived objects, catalog, Source last.** The Source's existence
  would imply the rest. Rejected: a kill after the derived writes leaves derived
  objects citing a Source that does not exist, and the retry (no prior Source)
  re-extracts and cannot tell it already produced them, so nondeterministic
  output accumulates duplicates with no owner to reconcile against. It also
  writes `index.md` entries for a Source that is not on disk yet.
- **A completion stamp required by the gate.** Rejected: every existing Source
  lacks it, so absence would have to mean either "incomplete" (a forced
  re-extraction of every workspace) or "complete" (which is exactly the hole).
- **Verify the index entry and derived objects in `converged_reingest`.**
  Rejected: the Source records nothing about how many derived objects to
  expect, and zero is legitimate (`no-concepts-found`), so a check cannot tell
  a missing object from a correctly absent one. It would also make the pure,
  read-once gate do filesystem scans on every skip.
- **A ledger-style journal file plus a scan verb** (the merge ledger's
  `scan_torn_writes`). Rejected as heavier than needed: the Source itself can
  carry the pending state, and a separate file would be one more thing to keep
  consistent with it.
