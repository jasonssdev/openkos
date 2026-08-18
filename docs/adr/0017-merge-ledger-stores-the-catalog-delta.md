---
type: Decision
title: "ADR-0017: The merge ledger stores the catalog delta, not a catalog snapshot"
description: index_before/log_before become index_restores, and unmerge reverses index.md/log.md surgically -- supersedes the catalog-snapshot clause of ADR-0002 and ADR-0013.
status: Accepted
date: 2026-08-17
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-08-17T00:00:00Z
sensitivity: public
---

# ADR-0017: The merge ledger stores the catalog delta, not a catalog snapshot

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

ADR-0013 moved the merge ledger out of the survivor's own frontmatter and
into a sidecar under `bundle/.state/ledger/`. That fixed the document
corruption and the geometric growth of the survivor file, but it did not
change what an entry *stores*. Each entry still carried `index_before` and
`log_before`: complete verbatim copies of `index.md` and `log.md` as they
stood at merge time.

So a sidecar still scaled with the size of the **bundle** rather than with
the size of the **merge**. Measured on a real 33-document workspace after a
single merge, those two fields were **79.6%** of the sidecar's 16598
characters; the two documents the merge actually touched were 11.6%. Held
against a synthetic bundle with the same one merge, the entry cost 1838
characters at 10 documents, 5998 at 50, and 21798 at 200 — linear in the
catalog, and compounding across merges, because each successive merge
photographs a larger catalog than the one before it.

The waste is specific and avoidable. A merge **touches a handful of lines**:
it removes one catalog bullet and prepends one log entry. Storing both files
whole to reverse a three-line change is the defect.

There was a second, unadvertised cost. Because reversal wrote those
snapshots back **wholesale**, any catalog or log work that landed between
the merge and the unmerge was destroyed. `unmerge` warned about it
(`catalog_log_drifted`) and continued; the code documented the limitation in
so many words — *"Drift that arrives a moment earlier is still discarded."*
Proven by execution before this change: merge two concepts, catalogue a
third, unmerge, and the third concept's bullet is gone from `index.md`.

## Decision

Ledger entries are `MERGE_LEDGER_SCHEMA_V5`. `index_before` and `log_before`
are replaced by `index_restores`, and `unmerge` reverses the catalog
**surgically**.

**`log.md` needs no stored field at all.** A merge's only effect on it is one
`**Merge**: Merged [<absorbed>](…) into [<survivor>](…).` bullet, fully
derivable from `absorbed_id`, the survivor id and `merged_at` — a
reconstruction the drift check was already performing. That string now lives
in exactly one function, `bundle.merge.merge_log_entry`, read from three
directions: the merge writes it, the unmerge removes it, the drift check
rebuilds it. Inlined at each site, a reworded merge line would have left the
reversal silently unable to find the bullet it was meant to remove.

**`index.md` needs only the bullets the merge removed**, because a catalog
bullet carries a title and description that no id can regenerate. Each
`CatalogLineRestore` holds the exact line, the line that preceded it, and
that anchor's occurrence index.

The anchor is by **content**, not by character offset — unlike
`LinkRewrite`, whose file is restored wholesale around it, a catalog is
appended to by every ingest, so an offset recorded at merge time is stale
before the reversal runs. The **occurrence index** exists for the same
reason `LinkRewrite.offset` does: a catalog may legitimately hold
byte-identical duplicate bullets, and a content-only anchor cannot tell them
apart.

**Reversal semantics are surgical.** Put back exactly the recorded bullets,
remove exactly this merge's log line, touch nothing else. Fail closed rather
than approximate: an anchor that no longer occurs as often as recorded
refuses with nothing written. Idempotent by **count**, so a run that died
midway is safe to re-run — a presence test would restore the first of a
duplicated pair and silently swallow the second. Where several identical
`**Merge**` bullets coexist the topmost is reversed, which is not a guess:
`log.md` is newest-first by construction and `unmerge` only ever reverses the
LIFO tail, so the two orderings agree.

**Old ledgers keep working, by shape and never by migration.** A v1–v4 entry
carries its snapshots, restores wholesale, and keeps its drift warning. The
reader accepts all five shapes. Nothing rewrites an older entry into the
newer one: an entry already on disk records no delta, so converting it would
mean inventing reversal information nobody stored — and it would turn a read
of the artifact that exists to reverse a merge into a write of it.

## Consequences

**A sidecar now tracks the merge, not the bundle.** Measured: constant at
963 characters across 10-, 50- and 200-document bundles, against 1838 / 5998
/ 21798 before. The ten-year projection that motivated the issue — a ledger
plausibly exceeding the size of the bundle it exists to protect — no longer
applies.

**Interleaved work survives an unmerge.** This is a behavior change and the
reason the discard warning is now silent for v5 entries: there is nothing
left to discard. The literal reading of "byte-for-byte identical to their
pre-merge state" no longer holds for `index.md`/`log.md` when the bundle did
not stand still — which is the honest outcome, since the alternative was
deleting work the operator never asked to lose. Byte-parity on an
otherwise-untouched bundle is unchanged and still tested.

**The privacy sweep gets a smaller surface.** `forget`'s ledger sweep
(ADR-0002's descendant, issues #602/#689) had to scrub an entire catalog and
log out of every surviving entry. A v5 entry holds one bullet and its
neighbour. The sweep must still reach `index_restores`, and drops a restore
**whole** — never blanked — when a purge-set member resolves from either its
`line` or its `preceded_by` anchor, since an anchor cannot be emptied and
still anchor. Privacy over reversibility, as ADR-0002 already ruled: the
cost is that one bullet can no longer be put back.

**A reworded merge log line is now a breaking change** for any ledger
written before the rewording, because the reversal locates the bullet by its
exact text. That is why the string has exactly one definition; changing it
requires the same care as changing an on-disk schema.

## Alternatives considered

**Migrate old sidecars on read.** Rejected: it converts a *read* of the
reversibility record into a *write* of it, and a conversion that loses a byte
makes `unmerge` silently inexact — on the one artifact whose entire purpose
is exactness.

**Break compatibility and store only deltas.** Rejected. It is the cleanest
code and the cheapest change today, when few ledgers exist, but any user with
a merge already recorded would lose the ability to undo it.

**Keep the wholesale restore and shrink storage only.** Rejected. A delta
cannot reproduce the pre-merge file once the bundle has moved on, so
preserving the clobber would have meant writing code whose only purpose was
to reproduce a known data-loss defect.

**A relevance-free generic diff (offsets, unified hunks).** Rejected as
unnecessary: the merge's effect on both files is already deterministic and
was already being reconstructed by the drift check, so the delta is
expressible in terms the ledger's own fields imply.
