# Archive Report: preserve-source-frontmatter

**Date archived:** 2026-09-29
**Issue:** #1062
**ADR:** ADR-0030, "Incoming frontmatter is untrusted", Accepted at archive
**Tasks:** all checked. The five that were closed at archive (5.11–5.14 and 6.2) carry their evidence inline.

## PRs (stacked to main)

| Slice | PR | Scope |
|---|---|---|
| 1 | #1088 | Planning and ADR-0030 (Proposed); fail-closed `parse_incoming_frontmatter`; shared `frontmatter_block_end` |
| 2 | #1089 | `source_frontmatter` stored verbatim at both build sites; `_SPECIAL_KEYS`; decision D Source-only rewrite |
| 3 | #1090 | `tags` lift (unioned) and `sensitivity` lift (raise only); the gate follows the raise; `set-sensitivity` advisory |
| 4 | #1091 | `date:` event-date tier (flag > stored > `date:` > file name) |
| 5 | #1092 | Source tags given to derived concepts extracted in the same run |
| 6 | #1094 | Docs (knowledge-object-model, cli.md event-date precedence) |

## Delta specs merged into `openspec/specs/`

Each merge was composed with `gentle-ai sdd-archive-compose` (exit 0). Every ADDED, MODIFIED and RENAMED requirement heading appears exactly once in its living spec.

| Domain | Requirements before → after |
|---|---|
| ingestion | 53 → 57 (4 added; "Converged Re-Ingest Date-Only Rewrite" RENAMED to "… Source-Only Rewrite") |
| ingest-application-service | 9 → 10 |
| entity-resolution-merge | 17 → 17 (`source_frontmatter` joins the per-file keys a merge never fills) |

## Decisions

- The incoming mapping is stored verbatim under `source_frontmatter`. Only `tags`, `sensitivity` (raise only) and `date:` are lifted.
- `status`, `type`, `provenance`, `version`, `timestamp`, `generated`, `verified`, `sources`, `author`, `updated` and `created` are never lifted.
- `author` and `updated` are not lifted because ADR-0029 left no `sources[]` on a Source to put them in. This revises a row of the issue's original proposal.
- The parser is fail-closed:
  - YAML only;
  - at most 64 KiB and a nesting depth of at most 32;
  - **any** anchor or alias is rejected;
  - only plain data that survives a round trip is accepted.

  At review, the rule was corrected from "alias references only" to "any anchor", to match design.md and ADR-0030.
- Tag propagation happens at creation time only. Re-syncing existing derived concepts is follow-up #1093.

## Related

- #1086 (PR #1087): the re-extract send gate now reads the Source's own resolved sensitivity. Slice 3's raise-only lift composes with it.
- #1076: Source `provenance: [raw/…]` compared with the example. Orthogonal to this change.
