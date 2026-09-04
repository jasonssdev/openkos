# Archive Report: ingest-application-service

**Change**: `ingest-application-service`
**Issue**: #918 (design: extract synchronous application services from the CLI before MVP 3 adapters) — **stays OPEN**. This change delivered the INGEST bounded-context service only. #918 also covers the lifecycle service, which carries the deferred headless-consent protocol and remains unimplemented.
**Archived**: 2026-09-04
**Verdict**: PASS — 0 CRITICAL, 0 WARNING, 2 informational SUGGESTIONs (see Verification below)

## Final State (authoritative, at close)

This section reflects the state of the change AFTER the intermediate `apply-progress`/`verify-report` snapshots were written, per the Final-State Authority hierarchy. It is the terminal record; snapshot claims of "pending" or "next step" below it are superseded.

- **PR #940** (Slice 1 — foundation, `DerivedPlan` + collision helpers): squash-merged to `main` as commit `7ec516b`.
- **PR #941** (Slice 2 — de-presentation of `stage_derived_objects`, typed contracts, adapter wiring): squash-merged to `main` as commit `e5a7682`.
- **PR #943** (Slice 3 — plan-composition core, `converged_reingest`/`compose_source_document`/`compose_catalog_update`): squash-merged to `main` as commit `8dcefbe`.
- `main` is at `8dcefbe`; all three commits confirmed as ancestors of `main` via `git merge-base --is-ancestor` at archive time.
- Final gates on the merged tree: **6,020 passed, 1 skipped**; total coverage **96.93%** against a 90% gate; `application/ingest.py` at 98% branch coverage.
- `src/openkos/cli/main.py`: **18,394 → 17,669 lines** (−725) across the three slices, as orchestration composition moved to `src/openkos/application/ingest.py`.
- The characterization goldens grew **6 → 10 scenarios**, all confirmed passing in both the default git-config environment and `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null`.
- Every commit on all three PRs used `Refs #918`, never `Closes #918` — confirmed by issue #918 still reporting `state: OPEN` via `gh issue view` at archive time.
- **Issue #942** (`state: OPEN`, confirmed via `gh issue view` at archive time) was filed during Slice 3 verification for a pre-existing latent bug: `converged_reingest`'s `except ValueError:` cannot catch the malformed frontmatter it documents, because `yaml.YAMLError` is not a `ValueError` subclass (`issubclass(yaml.parser.ParserError, ValueError) is False`, confirmed directly). This bug predates this change — it was present on `main` before this change touched the code and was only made independently visible (and testable) once the branch was moved into its own function during Slice 3. Deliberately out of scope for this change; tracked separately by #942.
- **A regression was found and fixed during Slice-3 verification**, not left open: the frontmatter-refusal error message had begun naming the raw source path instead of the Source document's display path, because two display-path parameters had been collapsed into one during the move. Fixed, and pinned by a new regression test, `test_frontmatter_refusal_names_the_source_document_not_the_raw_source`.

## Verification (per `verify-report`, obs #3142, at verification time)

**PASS** — 7/7 requirements compliant, 9/9 scenarios passing, 0 CRITICAL, 0 WARNING. Build: `ruff check .` / `ruff format --check .` clean (291 files); `mypy .` clean (291 files). Full suite: `uv run pytest --cov=src/openkos -q` → 6020 passed, 1 skipped, 96.93% coverage.

Two informational SUGGESTIONs, both confirmed correct-as-is rather than open gaps:
1. Issue #942 confirmed filed, open, and correctly out of scope (pre-existing latent bug, not introduced by this change).
2. The `ingestion` delta spec's zero-requirement-changes shape is confirmed correct BY DESIGN, not an omission — this is a pure composition refactor and the delta explicitly says so (see Specs Synced below).

Two documented, non-blocking design deviations were independently re-confirmed correct at verification time (both disclosed in `apply-progress`, obs #3141):
- `SourceDocumentPlan` carries two fields (`raw_content`, `origin_key`) beyond the design's literal signature list — necessary for `compose_catalog_update`'s conditional Source re-render, confirmed by reading the shipped implementation.
- The malformed-prior-frontmatter refusal message now names `source_display_path` instead of holding a `Path` — a consequence of the D2 constraint (the service never holds a filesystem path); unpinned by any pre-existing test, so nothing broke, and it is now covered by a dedicated application-layer unit test.

## Specs Synced

| Domain | Action | Requirements |
|--------|--------|--------------|
| `ingest-application-service` | **Created** (new capability, mechanical `cp` copy, zero-diff readback) | 7 added, 7 confirmed present in destination by name-match |
| `ingestion` | **Updated** (Purpose paragraph replaced only) | 0 requirement changes — the delta explicitly scopes to Purpose only; all 43 pre-existing requirement headings in `openspec/specs/ingestion/spec.md` confirmed unchanged (count identical before and after the edit: 43 → 43) |

Requirement name-match, source delta vs. merged destination for the new capability (`diff` between the two headings lists is empty):
- Non-CLI Callable Ingest Composition
- Extraction Disclosure Data Is Returned, Not Rendered
- Progress Reporting Is Injected, Never Owned
- Decoded Text Arrives As A Parameter
- The #773 Convergence Short-Circuit Is A Typed Outcome
- Shared Write Mechanics And Client Construction Stay Adapter-Side
- The Extraction Preserves Observable CLI Behavior

All 7 present in both the source delta and `openspec/specs/ingest-application-service/spec.md`; `diff -r` between the source delta file and the final destination file is empty (byte-identical).

`ingestion`'s merged Purpose paragraph:

> `openkos ingest <path>` is the CLI entry point for ingesting a raw source: it gates the workspace, reads the configuration, builds the LLM client, and performs every snapshot read, then delegates to the ingest application service, which stages a bounded list of derived objects — zero up to a post-judge backstop cap of 12, each classified across the 9-type derived-object vocabulary (`Concept`, `Entity`, `Place`, `Event`, `Procedure`, `Decision`, `Project`, `Person`, `Organization`) — alongside the generated Source concept. `ingest` itself owns argument parsing, workspace and client setup, the confirmation gate, rendering the extraction notices and derived-object preview, catalog (`index.md`) and log (`log.md`) writes via the shared write helpers, and degrading to Source-only behavior with zero crashes on any LLM failure.

The inserted paragraph was diffed line-by-line against the source delta's own "Purpose Update" text before the merge was accepted; the two are byte-identical. `grep -c '^### Requirement'` on `openspec/specs/ingestion/spec.md` confirms exactly 43 requirements both before and after the edit — the Requirements section is byte-for-byte unchanged; only the Purpose paragraph (lines 5–12 of the pre-edit file) was replaced.

## ADR-0018 — untouched, confirmed correct

`docs/adr/0018-application-layer-for-bounded-context-services.md` was **not edited by this archive**. It already reads `status: Accepted` in the YAML frontmatter and `- **Status:** Accepted` in the body, flipped by the prior, unrelated `docs(cli): archive the query application service change (#936)` commit — ADR-0018 is shared across both the query and ingest bounded-context slices, and that earlier archive already performed the flip. Confirmed via direct `grep` at archive time; both lines read `Accepted`. No ADR was created for this change: it applies ADR-0018's existing decisions (the layer, granularity, the import invariant, "services never render", "services stage, adapters write") to a second bounded context rather than establishing anything new. `docs/adr/README.md`'s index row was already correct from the prior archive and required no change. `tests/unit/test_adr_index.py`, which pins ADR frontmatter, body `**Status:**`, and the README index row in agreement, is unaffected because no ADR file was touched.

## Task Completion Gate

`tasks.md` (obs #3140, plus the fuller Slice 2/3 detail carried in `apply-progress.md`, obs #3141) was read in full before this archive proceeded: all 44 assigned implementation tasks across all three slices (Phase 0 through Phase 14) are checked `- [x]`. `grep -c '^\- \[ \]'` against the archived `tasks.md` returns 0 unchecked implementation tasks. No stale-checkbox reconciliation was needed.

## Archive Mechanics

- `openspec/specs/ingest-application-service/spec.md`: created via mechanical `cp` into a temp file within the target directory, `diff -r` against the source delta returned empty (exit 0), then `mv` into place. Readback `diff -r` (source vs. final destination) is empty.
- `openspec/specs/ingestion/spec.md`: edited in place via a targeted Python script that replaced only the paragraph's exact line range (5–12), never a Read→Write reproduction of the whole file; the inserted text was independently diffed against the source delta's paragraph and confirmed byte-identical; `grep -c '^### Requirement'` confirms 43 → 43, unchanged.
- `docs/adr/0018-application-layer-for-bounded-context-services.md` and `docs/adr/README.md`: **not touched** (already correct from a prior archive; see ADR section above).
- `openspec/changes/ingest-application-service/apply-progress.md`: materialized from Engram observation #3141 (the full body reproduced verbatim, with one appended "Final-state note" clarifying that the snapshot's own "next step" language is superseded by this report) so the archived folder is self-contained on disk. This file did not exist on disk before this archive phase — Engram was its only prior location.
- The entire `openspec/changes/ingest-application-service/` folder was moved (not copied) to `openspec/changes/archive/2026-09-04-ingest-application-service/` via `mv` (git rename detection applies at commit time; `git mv` itself failed only because of a stderr-redirect path error in the archive shell script, not a git failure — the fallback plain `mv` executed the identical filesystem operation). Verified by `diff -r` against a pre-move recursive snapshot taken via `cp -R` before the move (archive-report.md excluded, since it is additive and did not exist in the snapshot) — the readback diff was empty (exit 0) — and by confirming the original path no longer exists on disk (`[ -e ... ]` check failed as required).
- Archived folder contents, confirmed via `find` at archive time: `apply-progress.md`, `design.md`, `exploration.md`, `proposal.md`, `specs/ingest-application-service/spec.md`, `specs/ingestion/spec.md`, `tasks.md`, `verify-report.md` — 8 files, matching the sibling `2026-08-31-query-application-service` archive's shape exactly (the 9th file, this report, is added after this list is generated).

## Observation IDs Read (traceability)

| Artifact | Observation ID |
|----------|-----------------|
| `sdd/ingest-application-service/proposal` | #3137 |
| `sdd/ingest-application-service/spec` | #3138 |
| `sdd/ingest-application-service/design` | #3139 |
| `sdd/ingest-application-service/tasks` | #3140 |
| `sdd/ingest-application-service/apply-progress` | #3141 |
| `sdd/ingest-application-service/verify-report` | #3142 |

## Scope Boundary — What Remains Open on #918

#918 asks for three application services: **query**, **ingestion**, and **lifecycle**. The query slice was delivered and archived on 2026-08-31 (`2026-08-31-query-application-service`); this change delivers **ingestion**. **Lifecycle composition remains unimplemented** and, per the launch context for this archive, carries the deferred headless-consent protocol — a materially larger scope than a mechanical composition extraction. #918 is left open to track that remaining work; no artifact in this change states or implies #918 is complete.

## SDD Cycle Complete

The `ingest-application-service` change has been fully planned, implemented, verified, and archived. Ready for a future change to pick up the lifecycle application-service work (with its deferred headless-consent protocol) that #918 still tracks.
