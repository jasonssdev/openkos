---
type: Decision
title: "ADR-0032: Frontmatter `status: deprecated` is a marked export of computed supersession, never read back"
description: When a concept is superseded, the engine writes status deprecated plus a status_derived_from marker so OKF v0.2 consumers see it; supersedes edges stay the only authority, the engine ignores marked values, a human-authored status other than stable is never overwritten, every relation-changing verb applies one projection, and repair fixes drift from any state. Amends ADR-0029 Decision 5.
status: Proposed
date: 2026-09-29
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-29T00:00:00Z
sensitivity: public
---

# ADR-0032: Frontmatter `status: deprecated` is a marked export of computed supersession, never read back

- **Status:** Proposed
- **Date:** 2026-09-29
- **Amends:** [ADR-0029](0029-adopt-okf-v02-frontmatter.md) (Decision 5 only)

## Context

OpenKOS decides that a concept is deprecated from `supersedes` edges
(`lifecycle.deprecated_concept_ids`): a concept is hidden when its own
`status` is `deprecated` or when another concept supersedes it. ADR-0029
Decision 5 kept that computation and chose that no engine path writes
`status: deprecated`, accepting that "an external consumer that reads only
frontmatter sees a superseded concept as `stable`".

Since #1064 every new concept carries `status: stable`, and OKF v0.2 §5.4
makes `status` the lifecycle field. A tool other than OpenKOS reading the
bundle therefore sees a superseded concept as current (issue #1075). That
is a portability defect in a project whose promise is that the bundle is
readable without OpenKOS.

The forces:

- **Edges are what everything is specified on.** Retrieval, contradiction
  candidates, adjudication, `list`, and the MCP payload all read the one
  predicate. Moving authority to frontmatter would make every reversal path
  (`unmerge`, `forget`, `purge`, a hand-deleted edge) a second writer that
  must be right, or the engine silently hides or resurrects concepts.
- **The predicate already reads `status: deprecated`.** A naive export would
  feed back: once written, deleting the edge would no longer un-hide the
  concept, and a partial write would change retrieval.
- **Humans also write `status`.** A `draft`, or a deliberate
  `deprecated`, is a person's statement; "human curates, engine maintains"
  forbids the engine overwriting it.
- **Multi-file writes are not transactional.** `forget`, `unmerge`, and
  `purge` write files one at a time; any design has to make a partial write
  harmless.
- **Reconstructible.** Anything derived must be recomputable from canonical
  files.

## Decision

We write the computed supersession into frontmatter as an EXPORT, and we
make the engine unable to read it back.

1. **A marked export.** When a concept is superseded, the engine writes
   `status: deprecated` together with the extension key
   `status_derived_from: supersedes` (OKF v0.2 §4.1). The marker says the
   value was derived by OpenKOS, not declared by a person.
2. **Never read back.** `okf.declares_deprecated` counts a
   `status: deprecated` only when it carries no valid marker. Effective
   deprecation is therefore exactly "human-declared, or superseded by an
   edge", as before; the export cannot change what OpenKOS retrieves,
   lists, or judges, so a stale, missing, or half-written export is a
   portability defect at worst, never a correctness one.
3. **One projection.** A single pure function in `model/okf.py` maps a
   concept's frontmatter and whether it is superseded to one outcome:
   export, withdraw (back to `status: stable`), drop an invalid marker,
   blocked, or unchanged. Every writer, the `lint` drift scan, and the
   `repair` fixer call it, so they cannot disagree.
4. **Human values win.** The engine only toggles between absent/`stable`
   (or legacy `active`) and an exported `deprecated`. A human-authored
   `draft` or any other value is preserved and reported as blocked; a
   human-authored `deprecated` (no marker) is never withdrawn.
5. **Every relation-changing verb applies it in the same write.**
   `reconcile --winner` and `relate … supersedes …` export; `forget` and
   `purge` withdraw for resurrected targets; `merge` and `unmerge` project
   the documents they rewrite. Each includes the extra document in its own
   preview, drift guard, and commit.
6. **Repair closes every gap.** `lint` reports drift and names
   `openkos repair`; `repair` rewrites every document to its projection.
   Paths with no reversal verb (a hand-deleted edge, a future
   `unreconcile`) are recovered this way, so no state is unrecoverable.
7. **Incomplete walks never withdraw.** If any document cannot be read,
   the edge set may be missing the one edge that justifies an export, so
   withdrawal is skipped and reported.

This amends ADR-0029 Decision 5 ("No engine path writes `status:
deprecated`"). The rest of ADR-0029 stands, including that deprecation is
computed from edges.

## Consequences

- An OKF v0.2 consumer sees `status: deprecated` on a superseded concept,
  and `stable` again once nothing supersedes it.
- Writes grow: a supersede now rewrites the loser, and `forget`/`purge`
  rewrite resurrected targets. Each verb's preview names these.
- A person reading frontmatter sees a new extension key. Deleting it is how
  a person claims an exported `deprecated` as their own.
- `unmerge` round-trip parity keeps holding on an export-consistent bundle;
  it may differ only in `status`/`status_derived_from` when the pre-merge
  state already had drift or a later write changed the relevant edges.
- A withdrawn export restores `status: stable`, not the exact prior bytes:
  absent and `stable` mean the same under OKF v0.2 §5.4.
- A superseded `draft` stays `draft` on disk while OpenKOS hides it; `lint`
  says so, and only a person can change it.
- New invariant to keep: no code outside `model/okf.py` may read or write
  `status_derived_from`, and no consumer may treat a marked `deprecated` as
  a declaration.

## Alternatives considered

- **Do nothing and document that on-disk `status` is not authoritative**
  (ADR-0029 Decision 5). Leaves every non-OpenKOS reader misinformed about
  exactly the concepts a human took the trouble to supersede.
- **Make frontmatter `status` the source of truth** and stop computing
  deprecation from edges. Every reversal path becomes a writer that must be
  right, and a partial write changes retrieval; the retrieval gate, the
  candidate filters, and ADR-0026's history channel are specified on edges.
- **Write an unmarked `status: deprecated`.** The predicate would read it
  back: removing the edge would no longer un-hide the concept, a partial
  `forget` would leave a concept hidden that should have resurrected, and
  the engine could no longer tell its own value from a person's.
- **Mark with the superseding ids (`deprecated_by: [...]`).** More useful
  to a reader, but it goes stale whenever a superseder is merged or
  renamed, which would make `merge` rewrite third-party documents it never
  touches today.
- **Record the prior status to restore it byte-exactly.** Adds a second
  value to keep consistent for a distinction (absent vs `stable`) OKF
  defines as meaningless.
- **Fix drift in `lint --fix`.** `lint` is read-only by specification, and
  `repair` already rewrites derived frontmatter (`sources` from
  `provenance`, `active` to `stable`).
