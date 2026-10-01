---
type: Decision
title: "ADR-0038: A watched folder is an external inbox; a source edited after import is refused into the queue, never re-imported"
description: The folder watch reads a user-configured inbox outside raw/ and the bundle, never raw/ itself; it imports a file only after its bytes stay unchanged for a quiet window; it never moves, renames or deletes an inbox file; a file whose bytes change after import produces one pending-work row per source instead of a new import, so raw immutability holds and the human re-imports deliberately; source versioning is deferred.
status: Accepted
date: 2026-09-30
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-30T00:00:00Z
sensitivity: public
---

# ADR-0038: A watched folder is an external inbox; a source edited after import is refused into the queue, never re-imported

- **Status:** Accepted
- **Date:** 2026-09-30

## Context

MVP 4 promises that "a source dropped in is ingested without an
invocation". Ingest already decides what happens to a file it has seen
before: `application/ingest_service.py` matches a source to its `raw/` copy
by `origin_key` (the sha256 of the resolved source path); identical bytes
are a cheap converged skip, and different bytes are refused with
`RawImmutabilityRefused` ("raw sources are immutable. Ingest under a
different name, or inspect the existing copy"), exit 1. That is right for a
deliberate `ingest`. A watch fires on every save, so under a naive watcher
the most common event on a watched folder -- someone saving a note they
are still writing -- is a refusal, repeated on every save, in a log nobody
reads (#1142).

Two questions decide the job substrate's shape and touch a non-negotiable
principle (immutable `raw/`):

- **What is watched.** If the watched folder were `raw/` itself, an in-place
  edit of an imported source would be an edit of an immutable file, and a
  new drop would be indistinguishable from a hand-placed file the engine
  never imported. If it is an inbox that ingest copies from, `raw/` stays
  engine-owned and an edit is simply a changed input.
- **What an edit after import means.** Three behaviours were on the table:
  reject and report, version (import new bytes under a versioned name with
  a Source that `supersedes` the old one), or debounce then one of those.

## Decision

**One. The watched folder is an external inbox that ingest copies from.**
It is configured per workspace (`unattended.inbox` in `openkos.yaml`), is
off when absent, and is refused if it is `raw/`, the bundle, `.openkos/`,
the workspace root, or a path inside any of them. The engine only reads
the inbox: it never moves, renames, deletes or writes an inbox file. Import
goes through the same ingest service a person's `ingest` uses, so every
existing rule -- raw immutability, the collision family, sensitivity, the
pending marker of ADR-0035 -- applies unchanged.

**Two. A file is imported only after it has settled.** A save is debounced
over a quiet window (`unattended.quiet_seconds`): the watcher imports a
file only when its size and modification time have not changed for the
whole window, and it re-checks the bytes it read against that observation
before committing. A file still being written waits for the next poll.

**Three. A source whose bytes changed after import is refused into the
queue, once.** The watcher does not call ingest for it and does not retry
it. It upserts one pending-work row per source (ADR-0037) of kind
`watch_refusal` with reason `source changed after import`, keyed by the
Source concept id and digested over the inbox file's current bytes. The
same bytes produce the same row; a further edit retires it as stale and
opens a new one; restoring the imported bytes, or removing the file from
the inbox, retires it. The human re-imports deliberately -- by renaming the
edited file in the inbox, which the watcher then sees as a new source, or
by ingesting it under a different name by hand -- and the row moves to
`applied` when a raw copy with exactly the refused bytes lands. Raw
immutability is preserved without exception.

**Four. Source versioning is deferred.** Importing changed bytes as a new
version with a `supersedes` link is a coherent later design, but it decides
Source identity, supersession direction (ADR-0025 forbids taking temporal
direction from a model, and a file's mtime is a weak signal), and what
happens to derived objects of the old version. None of that is needed for
the first cut, and the queue row is where a future versioning option would
surface its proposal.

**Five. The watch is a poll, not an OS notification API.** The watcher
polls the inbox's directory listing and file stats on an interval. It adds
no dependency and behaves the same on every platform CI runs; a
notification backend can be added later behind the same "settled file"
contract, which is the roadmap's "watch backends" contribution point.

## Consequences

**Easier.** A user can keep editing notes in the inbox without producing a
stream of errors: the unsettled saves are ignored, the first settled
version is imported, and a later edit becomes one visible, deduplicated
proposal. Nothing about `raw/` or Source identity changes, so every
existing workspace and every existing ingest rule stays valid. The inbox
remains the user's folder; uninstalling or stopping the watcher leaves it
exactly as it was.

**Harder.** The user must understand that an edited note is not picked up
automatically, and must re-import it under a new name; the queue row is the
explanation, and its wording must carry the remedy. Polling costs a
directory scan and a stat per file per interval, and a hash of each settled
candidate; the watcher keeps the last observation per path so an
unchanged file is not rehashed. A very large inbox makes each poll slower;
that is bounded by `max_sources_per_pass` only for imports, not for the
scan.

**Accepted risk.** An inbox on a network or synced folder may report
unstable modification times, which delays settling; the quiet window is the
lever. A file that is replaced by an unrelated file of the same name looks
like an edit and is refused into the queue rather than imported, which is
the conservative outcome.

## Alternatives considered

**Watch `raw/` directly.** Fewer moving parts. Rejected: an in-place edit
would be an edit of an immutable file, a hand-placed file would be
indistinguishable from an imported one, and the engine would need write
access to the folder the user edits in.

**Version on edit.** Keeps every revision. Deferred (Decision Four), not
rejected; it decides more than the first cut needs.

**Move imported files out of the inbox.** Makes "already imported" visible
on disk. Rejected because it writes to a folder the user owns and may sync
elsewhere; the watcher's own record of what it imported answers the same
question without touching the user's files.

**An OS notification library.** Lower latency. Rejected for the first cut
as a new runtime dependency whose platform behaviour CI cannot cover on
every OS; the settled-file contract lets one be added later.
