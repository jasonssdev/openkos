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
| [0002](0002-reversible-merge-ledger.md) | Reversible merge ledger with embedded verbatim snapshots | Proposed | 2026-07-20 |
| [0003](0003-sensitivity-high-water-mark.md) | Sensitivity high-water-mark ordering and fail-closed combine | Proposed | 2026-07-20 |
