---
type: Decision
title: Adopt pgvector for vector search
description: Chose pgvector over Qdrant to avoid running a separate service.
tags: [architecture, vector-database]
timestamp: 2026-07-14T16:30:00Z
id: decision/adopt-pgvector
status: active
version: 1
freshness: snapshot
sensitivity: private
provenance:
  - raw/call-with-maria-2026-07-10.txt
  - raw/standup-2026-07-14.txt
---

# Adopt pgvector for vector search

On 2026-07-14 we chose [pgvector](/concepts/pgvector.md) over [Qdrant](/concepts/qdrant.md) for the initial vector search.

**Rationale:** pgvector lives inside our existing Postgres, avoiding a separate service; its ~40ms p95 on 2M vectors (as of 2026-07-14) is acceptable.

**Alternatives:** Qdrant — more tuning control, but a separate service to run.

**Status:** active. Revisit if latency under concurrency becomes a problem.
