---
type: Reference
title: Architecture Decision Records
description: Index and process for OpenKOS Architecture Decision Records (ADRs).
tags:
  - openkos
  - adr
  - architecture
  - decisions
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-14T23:00:00Z
sensitivity: public
---

# Architecture Decision Records

An **Architecture Decision Record (ADR)** captures a single significant decision: the context that forced it, the decision itself, and its consequences. ADRs are short, immutable-once-accepted, and append-only — we do not rewrite history; when a decision changes, we add a new ADR that supersedes the old one.

> **The ADR log starts with the code.** During the design phase the project's decisions live in the design documents under [`docs/`](../) (vision, philosophy, knowledge object model, roadmap, tech stack). ADRs are meant to record decisions *as they are made during development*, with real implementation context — so the numbered log begins with the first decision taken while building MVP 1, not before. This directory holds the process and template, ready for that first entry.

## Status lifecycle

- **Proposed** — under discussion (usually via a design proposal issue).
- **Accepted** — the decision is in effect.
- **Superseded by ADR-XXXX** — replaced by a later decision; kept for history.
- **Deprecated** — no longer relevant, but retained.

## How to add an ADR

1. Copy [`template.md`](template.md) to `NNNN-short-title.md`, using the next number (the first is `0001`).
2. Fill in context, decision, consequences, and alternatives considered.
3. Open a pull request. Significant decisions should reference a design proposal issue.
4. Once merged as **Accepted**, the ADR is not edited except to change its status.

## Index

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](0001-default-extraction-model.md) | Default extraction model settled by measurement | Accepted | 2026-07-19 |
| [0002](0002-reversible-merge-ledger.md) | Reversible merge ledger with embedded verbatim snapshots | Superseded in part by ADR-0013 and ADR-0017 | 2026-07-20 |
| [0003](0003-sensitivity-high-water-mark.md) | Sensitivity high-water-mark ordering and fail-closed combine | Accepted | 2026-07-20 |
| [0004](0004-typed-relationships-frontmatter.md) | Typed relationships in frontmatter; guard-then-rewire staging | Accepted | 2026-07-20 |
| [0005](0005-merge-edge-rewiring.md) | Merge edge rewiring -- refuse-then-rewire reversal, v2 ledger contract | Accepted | 2026-07-20 |
| [0006](0006-default-embedding-model.md) | Default embedding model -- bge-m3, reliability as the prior filter | Accepted | 2026-07-22 |
| [0007](0007-volatility-taxonomy.md) | Volatility taxonomy and volatility-aware freshness windows | Accepted | 2026-07-22 |
| [0008](0008-human-sensitivity-override.md) | Human sensitivity override, and where lowering needs a flag | Accepted | 2026-07-27 |
| [0009](0009-source-sensitivity-propagation.md) | Source sensitivity propagates to provenance descendants, raise-only | Accepted | 2026-07-28 |
| [0010](0010-reingest-raise-only-sensitivity.md) | Re-ingest resolves sensitivity as a raise-only high-water mark | Accepted | 2026-07-28 |
| [0011](0011-provenance-retarget-on-merge.md) | Third-party provenance retargets on merge; v3 reversibility ledger | Accepted | 2026-07-29 |
| [0012](0012-sensitivity-backfill-per-source-sweep.md) | Sensitivity backfill as an explicit per-Source sweep, not a silent migration | Proposed | 2026-07-29 |
| [0013](0013-relocate-merge-ledger-to-bundle-state.md) | Relocate the merge ledger to `bundle/.state/ledger/` | Superseded in part by ADR-0017 | 2026-08-11 |
| [0014](0014-durable-pending-work-stores.md) | Durable pending-work stores -- findings in `.openkos/`, decisions in `bundle/.state/` | Accepted | 2026-08-12 |
| [0015](0015-per-type-default-sensitivity.md) | Per-type default sensitivity as a floor-relative offset | Amended | 2026-08-14 |
| [0016](0016-maintain-the-cited-high-water-mark.md) | Maintain the cited high-water mark, not only apply it at birth | Accepted | 2026-08-16 |
| [0017](0017-merge-ledger-stores-the-catalog-delta.md) | The merge ledger stores the catalog delta, not a catalog snapshot | Accepted | 2026-08-17 |
| [0018](0018-application-layer-for-bounded-context-services.md) | An application layer for bounded-context services | Accepted | 2026-08-31 |
