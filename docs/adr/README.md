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

> **The ADR log starts with the code.** During the design phase the project's decisions live in the design documents under [`docs/`](../) (vision, philosophy, knowledge object model, roadmap, tech stack). ADRs are meant to record decisions *as they are made during development*, with real implementation context — so the numbered log begins with the first decision taken while building MVP 1, not before.

## Status lifecycle

- **Proposed** — under discussion (usually via a design proposal issue).
- **Accepted** — the decision is in effect.
- **Amended** — in effect, with part of it changed by a dated amendment section appended to the same ADR.
- **Amended by ADR-XXXX** — in effect, with part of it changed by a later ADR, which names it on an `Amends:` line.
- **Superseded by ADR-XXXX** — replaced by a later decision; kept for history.
- **Superseded in part by ADR-XXXX** — one clause replaced by a later decision; the rest stays in effect.
- **Deprecated** — no longer relevant, but retained.

## How to add an ADR

1. Copy [`template.md`](template.md) to `NNNN-short-title.md`, using the next number.
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
| [0012](0012-sensitivity-backfill-per-source-sweep.md) | Sensitivity backfill as an explicit per-Source sweep, not a silent migration | Amended by ADR-0016 | 2026-07-29 |
| [0013](0013-relocate-merge-ledger-to-bundle-state.md) | Relocate the merge ledger to `bundle/.state/ledger/` | Superseded in part by ADR-0017 | 2026-08-11 |
| [0014](0014-durable-pending-work-stores.md) | Durable pending-work stores -- findings in `.openkos/`, decisions in `bundle/.state/` | Accepted | 2026-08-12 |
| [0015](0015-per-type-default-sensitivity.md) | Per-type default sensitivity as a floor-relative offset | Amended | 2026-08-14 |
| [0016](0016-maintain-the-cited-high-water-mark.md) | Maintain the cited high-water mark, not only apply it at birth | Accepted | 2026-08-16 |
| [0017](0017-merge-ledger-stores-the-catalog-delta.md) | The merge ledger stores the catalog delta, not a catalog snapshot | Accepted | 2026-08-17 |
| [0018](0018-application-layer-for-bounded-context-services.md) | An application layer for bounded-context services | Accepted | 2026-08-31 |
| [0019](0019-dot-directories-are-not-knowledge.md) | Dot-directories are not knowledge | Accepted | 2026-09-14 |
| [0020](0020-concurrency-across-three-writers.md) | Concurrency across three writers -- per-operation locking, detection for the rest | Amended by ADR-0036 | 2026-09-16 |
| [0021](0021-sync-async-boundary.md) | The sync/async boundary -- a worker thread per operation, and no cancellation below it | Accepted | 2026-09-17 |
| [0022](0022-incomplete-read-verbs-report-incompleteness.md) | An incomplete read verb reports incompleteness as data and as exit code 2 | Accepted | 2026-09-21 |
| [0023](0023-source-event-date.md) | A Source records its event date as an optional, never-defaulted `event_date` key | Accepted | 2026-09-25 |
| [0024](0024-revises-relation-and-resolved-pairs.md) | A refinement is a stored `revises` relation, and a resolved pair is never re-judged | Accepted | 2026-09-25 |
| [0025](0025-temporal-direction-never-comes-from-the-model.md) | Temporal direction between two concepts never comes from a model | Accepted | 2026-09-25 |
| [0026](0026-superseded-concepts-re-enter-answers-only-as-labelled-history.md) | A superseded concept re-enters an answer only as labelled, citable history, and only when the workspace opts in | Accepted | 2026-09-26 |
| [0027](0027-hand-rolled-stdio-mcp-server.md) | The MCP adapter is a hand-rolled stdio server for protocol revision 2025-11-25, with no SDK | Accepted | 2026-09-26 |
| [0028](0028-mcp-disclosure-is-its-own-boundary.md) | Disclosure to an MCP client is its own sensitivity boundary -- hidden by default, opened only at launch | Accepted | 2026-09-26 |
| [0029](0029-adopt-okf-v02-frontmatter.md) | Adopt OKF v0.2 frontmatter -- provenance stays the truth, sources is its projection, and repair migrates existing bundles | Amended by ADR-0032 | 2026-09-29 |
| [0030](0030-untrusted-incoming-frontmatter.md) | Incoming frontmatter is untrusted -- preserved whole in one namespace, with a closed per-key lift | Accepted | 2026-09-29 |
| [0031](0031-openai-compatible-backend.md) | A second, OpenAI-compatible local backend beside the Ollama default | Accepted | 2026-09-29 |
| [0032](0032-deprecated-status-is-an-export-of-computed-supersession.md) | Frontmatter `status: deprecated` is a marked export of computed supersession, never read back | Accepted | 2026-09-29 |
| [0033](0033-source-tag-sync-is-union-only.md) | Source tag sync is union-only and never tags below the Source's sensitivity | Accepted | 2026-09-30 |
| [0034](0034-identity-auto-merge-only-for-a-measured-class.md) | Identity merges apply without prior consent only for an opt-in class that passed a pre-registered measurement | Accepted | 2026-09-30 |
| [0035](0035-a-pending-marker-makes-an-interrupted-ingest-completable.md) | A pending marker on the Source makes an interrupted ingest completable | Accepted | 2026-09-30 |
| [0036](0036-lock-a-short-commit-phase-in-a-per-user-state-directory.md) | The workspace lock covers a short commit phase, not the whole verb, and lives in a per-user state directory | Accepted | 2026-09-30 |
| [0037](0037-unattended-work-computes-and-enqueues-only.md) | Unattended work computes and enqueues; only a human-facing path applies a proposal | Accepted | 2026-09-30 |
| [0038](0038-a-watched-folder-is-an-external-inbox.md) | A watched folder is an external inbox; a source edited after import is refused into the queue, never re-imported | Amended by ADR-0041 | 2026-09-30 |
| [0039](0039-the-stable-python-api-ships-with-a-desktop-app-as-its-first-client.md) | The stable Python API ships with a desktop app as its first client, not with interoperability | Accepted | 2026-10-01 |
| [0040](0040-an-optional-native-wake-up-for-the-inbox-watch.md) | The inbox watch may be woken by an optional OS file-notification backend; polling stays the default, the fallback and the safety net | Accepted | 2026-10-01 |
| [0041](0041-a-changed-source-imports-as-a-new-version-and-its-supersession-is-proposed.md) | A source edited after import imports as a new version, and its supersession is proposed, not written | Accepted | 2026-10-01 |
| [0042](0042-human-readable-cli-output-is-a-tty-gated-convention.md) | Human-readable CLI output follows one TTY-gated convention | Accepted | 2026-10-01 |
| [0043](0043-llm-prompts-are-files-versioned-by-content-hash.md) | LLM prompts are files in one folder, versioned by content hash | Proposed | 2026-10-02 |
