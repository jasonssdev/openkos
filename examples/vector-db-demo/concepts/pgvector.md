---
type: Concept
title: pgvector
description: PostgreSQL extension for vector similarity search.
resource: https://github.com/pgvector/pgvector
tags: [vector-database, postgres]
timestamp: 2026-07-14T10:12:00Z
id: concept/pgvector
status: active
version: 2
freshness: pointer
sensitivity: private
provenance:
  - raw/call-with-maria-2026-07-10.txt
  - raw/standup-2026-07-14.txt
---

# pgvector

pgvector adds vector similarity search to PostgreSQL using HNSW indexing. It is compared closely with [Qdrant](/concepts/qdrant.md).

Latency p95 over 2M vectors: ~40ms under concurrency (as of 2026-07-14). The earlier ~15ms was a single-thread measurement.

## Related

- [Qdrant](/concepts/qdrant.md) — compared against
- [Adopt pgvector for vector search](/decisions/adopt-pgvector.md) — chosen in
