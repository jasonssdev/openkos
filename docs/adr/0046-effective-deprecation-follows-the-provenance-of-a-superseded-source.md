---
type: Decision
title: "ADR-0046: Effective deprecation follows the provenance of a superseded Source; its export stays edge-only"
description: A concept whose entire provenance is a superseded Source is deprecated at read time through the shared effective-status predicate, using forget's own orphan closure; the OKF status export stays a projection of supersedes edges only, and forgetting a superseded Source detaches its generated historical references in the same confirmed forget.
status: Proposed
date: 2026-10-02
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-02T00:00:00Z
sensitivity: public
---

# ADR-0046: Effective deprecation follows the provenance of a superseded Source; its export stays edge-only

- **Status:** Proposed
- **Date:** 2026-10-02
- **Issues:** [#1268](https://github.com/jasonssdev/openkos/issues/1268), [#1259](https://github.com/jasonssdev/openkos/issues/1259), [#1263](https://github.com/jasonssdev/openkos/issues/1263)

## Context

ADR-0041 makes a source edited after import a new version and proposes, never
writes, `relate <new> supersedes <old>`. ADR-0032 makes the OKF
`status: deprecated` an export computed from `supersedes` edges and never read
back. Together they left the lifecycle the engine itself recommends without a
clean exit (#1259, #1263): `supersedes` deprecated only the old Source, so
concepts resting solely on it stayed `stable` and kept competing in
retrieval; the recommended command warned that `supersedes` is "not a seeded
relation type"; and `forget <old> --scope source` refused on the very edge the
engine proposed, with `--force` leaving the new Source holding a dangling
`supersedes` reference. With ADR-0045, a concept that both versions support
also lists the old Source in `provenance` and in its generated `## Related`
bullet, which refuse the forget as well.

## Decision

**One. Deprecation follows provenance, computed at read time.** A concept is
effectively deprecated when its `provenance` is non-empty and every entry is,
directly or through other such concepts, a Source that another concept
supersedes. It is computed with the orphan closure `forget --scope source`
uses (`bundle.provenance.provenance_closure`), rooted only at superseded ids
under `sources/`, and lives in the one shared predicate
(`lifecycle.provenance_orphans`), so retrieval, candidate generation, `answer`
and `list` agree. A concept that also cites a live Source stays live; empty
provenance is never swept. Nothing is written: `unrelate` restores the
concepts at once.

**Two. The export stays edge-only.** `superseded_from_metadata`, `repair` and
the `lint` drift scan are unchanged. Exporting the propagated set would make
`relate` write into concepts it did not name and make every later writer
recompute and withdraw them, going stale whenever an attach adds a live
Source. The engine never reads its own export, so correctness of retrieval
does not depend on it. The cost is recorded, not hidden: an external OKF
reader sees a propagated concept as `stable`.

**Three. The lifecycle relation types draw no advisory.** `relate` no longer
calls `supersedes`, `revises` or `reconciled_with` unseeded. The seeded set
and the suggester's vocabulary are unchanged, so a model still cannot propose
them.

**Four. Retiring a superseded Source detaches its historical references.**
When the forgotten set holds a Source that another Source supersedes, that
Source's `supersedes` edge and a surviving concept's references the engine
generated for the old Source (its `provenance`/`sources` entry and its
`## Related` bullet in the exact generated shape) are removed in the same
confirmed forget, previewed as `~` edits, count-confirmed and drift-guarded.
An edit is planned only if a re-scan then finds no remaining reference from
that file; anything else, including a hand-written link or a differently
worded bullet, still blocks. `purge` does not take this path.

## Consequences

Easier: the whole lifecycle closes without `--force`; a question can no longer
cite a concept whose only Source was replaced; `lint` stays clean. Harder: a
concept the new version simply does not mention disappears from retrieval
until the supersession is removed, which is reversible but silent about
*why*; `list` shows `deprecated`. Effective status is now computed over
provenance as well as edges in two places (`lifecycle` and the one-walk
`list`), kept in step by one shared pure function and a parity test. The OKF
export and the engine's view differ for propagated concepts.

## Alternatives considered

- **Export the propagated set to frontmatter.** Reaches external readers, but
  makes `relate`, `unrelate`, `forget`, `merge` and `repair` each recompute
  and withdraw writes to documents they did not name.
- **Keep the edge as a historical record exempt from `forget` and `lint`.**
  Preserves the record in frontmatter but teaches two verbs a "historical
  edge" exception and leaves a reference to a concept that no longer exists.
  The forget tombstone and the `**Relate**` log entry already record the
  supersession durably.
- **Keep the refusal and document `--force`.** Changes nothing and keeps the
  dead end.
- **Root the closure at every superseded concept.** Would propagate from a
  superseded `Decision` to the Insights citing it, a different rule that no
  report asked for.
