---
type: Decision
title: "ADR-0013: Relocate the merge ledger to bundle/.state/ledger/"
description: The merge-ledger sidecar store, its two-phase write, and crash recovery -- supersedes ADR-0002's storage clause only.
status: Superseded in part by ADR-0017
date: 2026-08-11
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-08-11T00:00:00Z
sensitivity: public
---

# ADR-0013: Relocate the merge ledger to `bundle/.state/ledger/`

- **Status:** Superseded in part by ADR-0017 -- the catalog-snapshot clause only
- **Date:** 2026-08-11

## Context

ADR-0002 embeds every `merged_from` entry directly in the survivor's own
OKF frontmatter. That entry carries the FULL pre-merge snapshot set --
`absorbed_snapshot`, `survivor_before`, `index_before`, `log_before`, plus
recorded rewrites -- and `survivor_before` explicitly retains every prior
entry, so a survivor that has absorbed several objects carries the
byte-for-byte history of ALL of them, embedded again inside each new
`survivor_before` snapshot. The frontmatter grows geometrically across
merges: a survivor's file size is proportional to the SQUARE of its merge
count, not linear in it, because each new entry's `survivor_before`
re-embeds every earlier entry's own embedded snapshots. ADR-0005 (typed
relations) and ADR-0011 (provenance retargets) each added a further
whole-file snapshot list per entry, compounding the same growth. A
survivor with a handful of merges becomes a multi-megabyte document that
every tool touching the bundle -- editors, `git diff`, LLM context windows,
the inbound-reference scanners `find_inbound_references` et al. -- now has
to read, diff, or embed in full merely to see the concept's OWN current
content.

Separately, `git add`/`_autocommit`'s scoped staging and the six
`rglob("*.md")` walks (`references.py`, `links.py`, `relations.py`,
`provenance.py`, `okf._iter_docs`, `fts`/`reindex`) all treat the
survivor's frontmatter as ordinary concept content; the ledger riding
inside it is invisible to nothing that reads that file, which is
convenient for `okf.dump_frontmatter`/`load_frontmatter` symmetry but
means the ledger's bulk is indistinguishable from the concept's own
authored text everywhere that content is surfaced.

## Decision

We move every `merged_from` entry OUT of the survivor's frontmatter into a
per-survivor sidecar file, `bundle/.state/ledger/<concept_id>.ledger.okf`,
mirroring the concept's own hierarchical id so the two sit side by side in
a tree view. The container is written and read only through
`okf.dump_frontmatter`/`load_frontmatter` (ADR-0002 invariant 3, preserved
literally -- the sidecar is a frontmatter document with an empty body),
and the `.ledger.okf` suffix is deliberately never `.md`: every existing
inbound-reference/EXCLUDE walk is `sorted(bundle_dir.rglob("*.md"))`, so
the non-`.md` suffix excludes the sidecar from all six of those sites with
ZERO edits there -- a free, structural exclusion rather than a maintained
predicate. A new `bundle.ledger.iter_ledgers` primitive (glob rooted at
`bundle/.state/ledger/`, matching `*.ledger.okf`) is the ONE shared
INCLUDE-walk `forget`/`purge`/the `set-sensitivity` sweep reuse, so the two
opposite walks (EXCLUDE for reference scans, INCLUDE for privacy sweeps)
stay structurally separate rather than sharing one predicate that could
drift.

Concept-id-to-sidecar-path mapping reuses `okf.concept_path_for`,
generalized to accept `(root, suffix)` instead of hard-coding `.md` --
the SAME NFC/NFD-tolerant resolver the concept-id-to-`.md`-path direction
already uses (issue #430), rather than a second, independently-maintained
mapping.

`MergeLedgerEntry`'s schema (v1/v2/v3, ADR-0002/ADR-0005/ADR-0011) is
UNCHANGED -- this decision moves where entries live, never what they are.
`bundle.merge.plan_merge`/`plan_unmerge` become pure over caller-supplied
ledger entries (`existing_entries` in, `ledger_entries` out;
`entries` in, `remaining_entries` out) instead of decoding/encoding a
`merged_from` frontmatter key.

### Crash safety across two files

Splitting one atomic frontmatter write into two files (concept + sidecar)
reopens a durability question ADR-0002's single-file write closed by
construction: the two can now disagree mid-crash. We close it with a
two-phase write, a hash-bound intent marker, and a recovery function that
is TOTAL over on-disk state rather than a heuristic:

```
S1  write_atomic(<id>.ledger.okf.pending)   # full new container + expected_survivor_sha256
V   write_atomic(survivor.md)               # unchanged call site
S2  os.replace(pending, <id>.ledger.okf)    # commit
D   remove_file(absorbed.md)
```

| `.pending` | `sha256(survivor on disk)` | Verdict | Repair |
|---|---|---|---|
| absent | -- | consistent | none |
| present | == `expected_survivor_sha256` | V landed, S2 torn | roll forward (promote pending) |
| present | != (or survivor missing) | V never landed | roll back (discard pending) |

`merge`/`unmerge` both refuse (no `--force` override) while a `.pending`
marker exists for the survivor they touch -- it is mechanically exact and
trivially repairable, and forcing past it would commit a known-inconsistent
ledger. `fsio.write_atomic` fsyncs file content but deliberately omits a
parent-directory fsync, so this scheme is correct against a process crash
(the case above) and best-effort against power loss -- an identical,
pre-existing gap to ADR-0002's own single-file write, not a regression.

## Consequences

Easier: a concept's own file stays proportional to its own content
regardless of merge count; tools reading/diffing a survivor no longer pay
for its full merge history; `git diff` on a concept file shows only that
concept's changes. Harder: reading a survivor's merge history now needs a
second file read (`bundle.ledger.read_entries`), and the crash-safety
argument now spans two files instead of one, requiring the two-phase
write/recovery mechanism above rather than relying on a single atomic
rename. `_autocommit`'s scoped `git add -- <paths>` must explicitly stage
the sidecar path or the ledger silently never enters git; `purge`'s `git
filter-repo` path set must explicitly cover `bundle/.state/ledger/**` or a
privacy purge misses historical confidential snapshots now living outside
`bundle/**.md`. A new `lint` rule flags any `.md` file appearing under
`bundle/.state/`, so a future author who reintroduces the old exclusion
predicate discovers it immediately rather than the exclusion silently
regressing.

## Alternatives considered

- **Keep entries in frontmatter, cap history length**: does not solve
  geometric growth (each retained entry still re-embeds everything before
  it); a length cap would silently break `unmerge`'s LIFO reversal for
  merges older than the cap.
- **Derive crash safety from git instead of a two-phase write**: rejected
  -- `_autocommit` is best-effort and non-fatal (skips silently on no
  repo, no git identity, or any `GitError`/`OSError`), so git is a likely
  safety net, never a derivable invariant this design can depend on.
- **Ledger-last write order** (write the sidecar only after the survivor):
  rejected -- a crash between the two leaves the merge silently
  irreversible (survivor merged, no ledger entry), detectable only by
  comparing bundle state against expectations, never by the ledger itself.
- **Hash in the survivor pointing at its sidecar**: rejected -- the
  survivor is user-editable, so the hash goes stale on any legitimate hand
  edit and the detector becomes noise rather than a signal.

## Cross-references

- ADR-0002 (Reversible merge ledger with embedded verbatim snapshots) --
  this ADR supersedes ADR-0002's STORAGE clause only (where entries live);
  the entry schema, LIFO-tail enforcement, and round-trip-parity contract
  are unchanged and remain governed by ADR-0002.
- ADR-0005 (Merge edge rewiring) -- `relation_rewrites` (v2) moves with the
  rest of the entry into the sidecar; its own reversal mechanics are
  unaffected.
- ADR-0011 (Provenance retarget on merge) -- `provenance_rewrites` (v3)
  likewise moves unchanged; the v1/v2/v3 reader tolerance this ADR
  established is preserved verbatim by the sidecar's reader.
- ADR-0008 (Human sensitivity override) -- the per-entry sensitivity gate
  (`sensitivity.merged_content_blocked`) is unaffected: it already takes a
  `MergeLedgerEntry`, not a survivor, so relocating the entry's storage
  changes only where `contradiction.py` reads it from, never the gate
  itself.
