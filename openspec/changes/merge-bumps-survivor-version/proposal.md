# Proposal: A Merge Increments The Survivor's Version (#1267)

## Intent

`version` is the Knowledge Object's monotonic revision counter. An ingest
attach increments it, but a `merge` left the survivor at its old value, so a
concept that absorbed two others still read `version: 1`. A merge rewrites
the survivor's content and provenance; it is a revision.

## Scope

### In Scope

- `okf.build_merged_document` sets the merged `version` to the survivor's
  previous value plus one (missing or non-integer counts as 1), through the
  one helper the attach path also uses.
- `unmerge` needs no code: it restores the ledger's verbatim `survivor_before`,
  so the previous value returns byte for byte. A test pins this.
- `docs/knowledge-object-model.md` states the counter's rule.

### Out of Scope

- Bumping `version` on other verbs (`set-sensitivity`, `relate`, ...).
- Migrating existing merged concepts; their `version` is not recomputed.

## Approach

Factor the attach path's `version` read into `okf._next_version` and call it
from both builders. The absorbed side's `version` is never consulted.

## Decision

Owner decision on #1267: merge bumps `version`, unmerge restores it.

## Risks

- A hand-written survivor with no `version` gains `version: 2` on its first
  merge. Accepted: it matches how an attach treats the same concept.
- The merge characterization goldens and the merged-document golden change by
  exactly the `version` line.
