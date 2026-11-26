# Archive Report: ingest-token-usage

**Archived**: 2026-11-26
**Archive folder**: `openspec/changes/archive/2026-11-26-ingest-token-usage/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1807.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| ingestion | Updated | 1 ADDED requirement appended: Ingest Reports The Tokens Each Source Consumed (7 scenarios). No MODIFIED, REMOVED or RENAMED headings. |
| llm-client | Updated | 1 ADDED requirement appended: Token Usage Is Reported To An Optional Sink Per Response (3 scenarios). No MODIFIED, REMOVED or RENAMED headings. |
| openai-compatible-client | Updated | 1 ADDED requirement appended: Token Usage Is Reported To An Optional Sink Per Response (6 scenarios). No MODIFIED, REMOVED or RENAMED headings. |
| backend-selection | Updated | 1 ADDED requirement appended: The Chat Resolver Forwards A Usage Sink Only When Given (2 scenarios). No MODIFIED, REMOVED or RENAMED headings. |

## Requirement-vs-Code Check

Checked against PR #1808 (per-source token line on ingest, `ChatUsage` sink on both chat clients, resolver forwarding).
