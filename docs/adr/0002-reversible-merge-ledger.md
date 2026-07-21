---
type: Decision
title: "ADR-0002: Reversible merge ledger with embedded verbatim snapshots"
description: The on-disk format that makes object merges losslessly reversible.
status: Accepted
date: 2026-07-20
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-20T00:00:00Z
sensitivity: public
---

# ADR-0002: Reversible merge ledger with embedded verbatim snapshots

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

`merge <survivor> <absorbed>` is OpenKOS's first destructive operation. KOM
(`docs/knowledge-object-model.md:317-328`) specs merge as a first-class,
**reversible, no-information-loss** lifecycle step — a higher bar than
`forget`'s "undo is plain git" precedent. Merge mutates the survivor
irreversibly: sensitivity is recomputed by high-water-mark (lossy — the
combined value cannot recover either input), provenance is union-deduped
(loses ordering/multiplicity), the body is appended, and inbound `[...]
(/absorbed.md)` links across the bundle are rewritten. The absorbed file is
deleted. A durable, engine-driven `unmerge` must restore the exact pre-merge
bundle without depending on the user's git discipline or an intact `.git`
(exports may ship without it). At the time of this decision, the graph was
ephemeral (rebuilt per run), so there was no store to repair — the only
durable representation was markdown text. (Update, performance-caching
Slice 5: the graph — like FTS and the vector store — is now ALSO persisted
to `.openkos/graph.db`, written only by `reindex` and rebuilt wholesale on
the next `reindex` run after any bundle change; it remains a rebuildable
DERIVED cache, never the source of truth, so this ADR's core reasoning is
unaffected — merge/unmerge's reversibility contract still rests entirely
on markdown text, never on the graph store.)

## Decision

We embed a `merged_from` **list** in the survivor's OKF frontmatter (an
ordinary unknown key, not a new OKF type — §4.1 tolerance). Each entry carries
the **verbatim pre-merge byte-for-byte snapshots** of the absorbed file
(`absorbed_snapshot`), the survivor itself (`survivor_before`), `index.md`
(`index_before`), and `log.md` (`log_before`), plus the recorded
`link_rewrites` (`{file, old_link, new_link}`) and
`sensitivity_before`/`sensitivity_after` for audit. `survivor_before` is the
survivor's full verbatim bytes immediately prior to THIS merge's write,
explicitly **RETAINING any prior `merged_from` entries** from earlier merges —
it excludes ONLY the new entry being created by this merge (which does not
exist yet at snapshot time); it does NOT strip the whole `merged_from` key.
This is what lets sequential pairwise merges reverse losslessly in LIFO order.
`unmerge <survivor-id> <absorbed-id>` is **two-arg, LIFO-enforced**: it
reverses ONLY the most-recent unreversed entry (the tail), and the supplied
`absorbed-id` MUST equal that tail entry's `absorbed_id`, else the command
refuses with a clean error and no write (reversing a non-tail entry is unsafe
due to nested snapshots / overlapping rewrites). It writes every snapshot back
verbatim (restoring `log.md` from `log_before` and THEN appending one unmerge
audit line), and reverses each recorded link rewrite by exact fence-masked
substitution bounded to the recorded occurrences — failing closed if a target
file has drifted. The
ledger is written and read only through `okf.dump_frontmatter`/
`load_frontmatter` (symmetric YAML), never hand-spliced. Merge writes the
survivor (carrying the snapshot) **before** deleting the absorbed file, so a
crash never destroys the only copy. Round-trip parity (merge→unmerge =
byte-identical pre-merge bundle) is the contract, guarded by a property test.

## Consequences

Easier: full, git-independent, portable undo; the survivor is self-describing;
partial writes stay benign and recoverable. Harder: the survivor's frontmatter
grows by the size of the embedded snapshots (acceptable — data, not a new
type); the ledger format becomes a **durable on-disk contract** that later
changes must migrate; `unmerge` must fail closed on drift rather than
corrupt. Deterministic (no LLM), so fully unit-testable.

## Alternatives considered

- **Git-only + `merged_from` breadcrumb** (explore Option A): lowest effort but
  reversibility depends on user commit discipline and a live `.git`; loses the
  no-info-loss bar KOM sets, and does nothing for dangling links.
- **Redirect-stub tombstone** (Option B): best portability but stubs accumulate
  forever in `index.md`/FTS/graph, in tension with "fewer, richer objects".
- **Hash-referenced snapshot**: loses content the moment history is rewritten,
  gc'd, or exported without `.git`.
- **Deterministic inversion instead of snapshots**: impossible — sensitivity
  high-water-mark and provenance dedup are lossy.
