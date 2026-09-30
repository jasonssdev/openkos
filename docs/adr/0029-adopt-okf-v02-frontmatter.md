---
type: Decision
title: "ADR-0029: Adopt OKF v0.2 frontmatter -- provenance stays the truth, sources is its projection, and repair migrates existing bundles"
description: OpenKOS writes OKF v0.2 (generated, status stable, sources, okf_version 0.2); provenance remains the internal source of truth and sources is a one-way generated projection; existing bundles migrate only through an explicit repair commit that also migrates the merge ledger; deprecation stays computed from supersedes edges; migrated documents are attributed to openkos/legacy; per-claim footnotes are deferred indefinitely.
status: Amended by ADR-0032
date: 2026-09-29
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-29T00:00:00Z
sensitivity: public
---

# ADR-0029: Adopt OKF v0.2 frontmatter -- provenance stays the truth, sources is its projection, and repair migrates existing bundles

- **Status:** Amended by ADR-0032
- **Date:** 2026-09-29

## Context

OpenKOS adopts the Open Knowledge Format rather than inventing one, and does not re-decide what OKF has decided. OKF v0.2 supersedes v0.1 with two deliberate breaking changes (v0.2 §13.1): `timestamp` is replaced by `generated: { by, at }` (§5.2), and the body `# Citations` list is replaced by the frontmatter `sources` list (§5.1). It also fixes the lifecycle vocabulary as `status: draft | stable | deprecated` (§5.4) and an actor convention for `generated.by` (§7). The engine still writes the v0.1 shapes: `timestamp`, `status: active`, a bare empty `# Citations` heading on every Source, and `okf_version: "0.1"`. Every bundle written in that shape is one more bundle to migrate, and #1062 (preserving incoming frontmatter) targets the v0.2 `sources` shape, so the adoption must come first.

Four forces shape how to adopt it:

- **`provenance` is a trust input.** The sensitivity high-water mark, merge, lint, graph and MCP disclosure all fail closed on `provenance`. OKF's `sources` is a field any external OKF tool may rewrite.
- **Existing bundles contain history.** The merge ledger (ADR-0002, ADR-0013, ADR-0017) stores verbatim pre-merge snapshots of bundle documents, and `unmerge` restores them byte for byte, with drift checks that reconstruct each third-party file's post-merge bytes and link rewrites reversed at recorded file-absolute offsets. A format migration of the live bundle alone would make those snapshots disagree with the files on disk.
- **Human curates, engine maintains.** Rewriting the frontmatter of every document is consequential and must be reviewable.
- **v0.1 recorded no producer.** There is no real actor to put in `generated.by` for content that already exists.

## Decision

1. **We write OKF v0.2.** Engine-built documents carry `generated: { by: openkos/<version>, at: <ISO-8601> }` instead of `timestamp`, `status: stable` instead of `active`, a `sources` list, and no empty `# Citations` heading; a fresh bundle declares `okf_version: "0.2"`. All format knowledge, including the migration, stays in `model/okf.py`.
2. **`provenance` remains the internal source of truth; `sources` is a one-way projection of it.** Each Concept-ID provenance entry projects to `{id: <concept id>, resource: /<concept id>.md}`, in provenance order; `raw/` entries are not projected. The projection is introduced only by the document builders and by `repair`, and maintained only by the merge's provenance retarget when the key is already present. No engine code reads `sources` back as a trust, sensitivity, merge or provenance input; a parity test and an AST guard enforce both directions.
3. **Existing bundles migrate only through an explicit `openkos repair`,** as one idempotent, git-revertible commit that rewrites every concept document, migrates every merge-ledger sidecar with the same pure function (snapshots, embedded pre-relocation ledgers, `index_before` snapshots, and link-rewrite offsets), and flips `okf_version` to `"0.2"` last. Readers accept v0.1, v0.2 and mixed bundles indefinitely: `generated.at` falls back to a legacy `timestamp` only when `generated` is absent, and `active` reads as not deprecated. No other verb migrates a document as a side effect.
4. **Merge reversibility after a migration is defined as commutation.** For a merge recorded after migration, `unmerge` restores byte-identical pre-merge bytes, as before. For a merge recorded before migration and reversed after it, `unmerge` restores the migrated form of the pre-merge bytes -- exactly what `repair` would have written for those documents had the merge never happened. Anything the migration cannot keep coherent fails closed through the existing drift checks.
5. **Deprecation stays computed.** No engine path writes `status: deprecated`; a concept is deprecated when its own frontmatter says so (written by a human) or when it is the target of a non-self `supersedes` edge. The computed status the engine displays uses the v0.2 vocabulary: `stable | deprecated`.
6. **Migrated documents are attributed to `openkos/legacy`,** with `generated.at` equal to the old `timestamp` value unchanged.
7. **Per-claim `[^id]` footnotes are deferred indefinitely.** `## Related` remains the body's attribution surface; `sources[].id` is stable so footnotes can be added later without a new migration.

## Consequences

- An external OKF v0.2 consumer reads OpenKOS provenance from `sources` without any trust path inside the engine changing. The cost is two representations of provenance in every derived document, kept in step by tests rather than by structure.
- An external consumer that reads only frontmatter sees a superseded concept as `stable`; supersession is visible to it only through the `supersedes` relation. In exchange, frontmatter can never disagree with the edges, and `unmerge`/`unreconcile` have no status write to undo.
- `repair` becomes the migration verb for both the ledger relocation and the format. Its cross-survivor pollution gate is scoped to extracting pre-relocation ledgers, so a bundle whose survivor was merged twice can still be migrated.
- Between upgrading the engine and running `repair`, `unmerge` of a pre-upgrade merge keeps working because no non-builder writer introduces `sources`; if a drift check refuses on an unmigrated bundle, the refusal names `repair`.
- The displayed status value changes from `active` to `stable` in `list`, the concept read service and the MCP payload; scripts matching `active` must change.
- `generated.by` embeds the installed version, so every full-stream golden pins it.
- A Source document carries no `sources` entry for its raw original. #1062 decides how a Source records signals about that original.
- A future OKF major version is handled the same way: readers first, builders second, an explicit `repair` migration including the ledger third.

## Alternatives considered

- **Make `sources` canonical and retire `provenance`.** Moves every fail-closed trust input onto a field external tools may rewrite, and rewrites the engine's provenance model to do so.
- **Keep both `provenance` and `sources` independently writable.** Two writable copies of the same fact drift, and nothing could say which one is right.
- **Lazy migration** (on read, or on the next write of each document). Produces long-lived mixed bundles and hides format rewrites inside unrelated commits. Projecting `sources` inside the generic serializer was rejected for the same reason: every metadata-only rewrite would have migrated documents as a side effect and changed the bytes `unmerge`'s drift checks reconstruct.
- **A new `migrate` verb.** `repair` already owns explicit, refusing, single-commit bundle migrations; a second verb would duplicate its guards.
- **Leave the merge ledger in v0.1 shape, or normalize documents on the way back in `unmerge`.** Either loses reversibility for every pre-migration merge that rewrote an inbound link, relation or provenance entry, and the second is lazy migration inside an unrelated verb.
- **Write `status: deprecated` when a concept is superseded.** Lets frontmatter and edges disagree and adds a write that every reversal must undo.
- **Keep `active` as an OpenKOS extension value.** Re-decides a vocabulary OKF v0.2 fixes.
- **`generated.by: openkos/<current version>` with `at` set to the migration time for migrated documents.** Asserts a generation event that never happened; relabelling a field is not a content change.
- **Adopt the rest of v0.2 at once** (`verified`, trust tiers, `stale_after`, usage signals, attested computations). Additive, with no consumer yet.
