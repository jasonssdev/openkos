# Archive Report: query-application-service

**Change**: `query-application-service`
**Issue**: #918 (design: extract synchronous application services from the CLI before MVP 3 adapters) — **stays OPEN**. This change delivered the QUERY bounded-context service only. Ingestion and lifecycle services named in #918 are not implemented and are not addressed by this archive.
**Archived**: 2026-08-31
**Verdict**: PASS — 0 CRITICAL, 0 WARNING, 1 SUGGESTION (see Verification below)

## Final State (authoritative, at close)

This section reflects the state of the change AFTER the intermediate `apply-progress`/`verify-report` snapshots were written, per the Final-State Authority hierarchy. It is the terminal record.

- **PR #934** (slice 1 — read path): squash-merged to `main` as commit `f669b39`.
- **PR #935** (slice 2 — `--save` filing composition): squash-merged to `main` as commit `1352f50`.
- `main` is at `1352f50`; no open PRs against this change; only `main` exists in the repository; working tree was clean before this archive cycle began.
- Both merge commits confirmed on `main` and `origin/main` via `git log`/`gh pr view` at archive time.
- Final gates on the merged tree: **5,871 passed, 1 skipped**; total coverage **96.86%** against a 90% gate; `application/query.py` at 98% branch coverage; `ruff check .` / `ruff format --check .` clean on 282 files; `mypy .` clean on 282 files.
- `src/openkos/cli/main.py`: 18,847 → 18,275 lines (application composition extracted to `src/openkos/application/query.py`).
- Every commit on both PRs used `Refs #918`, never `Closes #918` — confirmed by issue #918 still reporting `state: OPEN` via `gh issue view` at archive time.

## Verification (per `verify-report`, obs #3131, at verification time)

Re-verified after commit `30e411c` (a docs-only correction to `spec.md`, the `query-command` Purpose delta, and ADR-0018's Context — zero code/test files touched, confirmed via `git diff-tree`). Verdict: **PASS — 0 CRITICAL, 0 WARNING, 1 SUGGESTION.**

A prior verification pass had found one CRITICAL: the delta spec's "Non-CLI Callable Answer Composition" requirement claimed the service constructs the LLM/embedder and reports uninitialized-workspace via return contract — neither of which the shipped `run_query` does. The owner ruled the CODE was correct (ADR-0018's Decision deliberately keeps LLM/embedder construction and concrete-backend binding in the adapter, not the service) and the SPEC was wrong. Commit `30e411c` rewrote the spec, the `query-command` Purpose delta, and ADR-0018's Context to match what was actually decided and shipped. Re-verification confirmed the diff touched only those three doc files and that the rewritten text now matches the code exactly (module-level grep for concrete backend names returned zero matches; `llm`/`embedder` are `Protocol`-typed parameters).

**The one SUGGESTION was already addressed before merge**, and is not an open gap at archive time: it noted that neither "No concrete backend is bound inside the service" nor "Shared Write Mechanics Are Called Through, Never Forked" had a dedicated automated regression test (both were verified only by static source inspection during that verification pass). `tests/unit/application/test_layering.py` now carries an automated guard for both, and both guards were mutation-verified before the change closed. This closes the SUGGESTION; nothing remains pending from verification.

## Specs Synced

| Domain | Action | Requirements |
|--------|--------|--------------|
| `query-application-service` | **Created** (new capability, mechanical `cp` copy, zero-diff readback) | 5 added, 5 confirmed present in destination by name-match |
| `query-command` | **Updated** (Purpose paragraph replaced only) | 0 requirement changes — the delta explicitly scopes to Purpose only; all 20 pre-existing requirement headings confirmed unchanged via `git diff` |
| `query-answer` | **Unaffected** — not touched, per the delta's own note |

Requirement name-match, source delta vs. merged destination for the new capability:
- Non-CLI Callable Answer Composition
- Filing Composition Is Independently Callable
- Shared Write Mechanics Are Called Through, Never Forked
- Adapter Owns Interaction, Presentation, And Exit Codes
- The Extraction Preserves Observable CLI Behavior

All 5 present in both source and `openspec/specs/query-application-service/spec.md`; `diff` between source delta and destination is empty (byte-identical).

`query-command`'s merged Purpose paragraph:

> The `openkos query "<question>"` Typer command is the CLI entry point for the MVP-1 query chain: it gates the workspace, reads the configuration and builds the LLM/embedder seams, then delegates to the query application service, which opens the indexes with degrade handling, calls the `retrieval.answer()` library seam, and computes the `--save` filing plan. `query` itself owns only argument parsing, workspace and client setup, interactive confirmation, exit-code mapping, and rendering the answer plus citations as plain text to stdout.

`git diff --stat` on `openspec/specs/query-command/spec.md` confirms exactly 8 insertions / 4 deletions, entirely within the Purpose paragraph; the Requirements section is byte-for-byte unchanged.

## ADR-0018

`docs/adr/0018-application-layer-for-bounded-context-services.md` flipped from `Proposed` to `Accepted` in both the YAML frontmatter (`status:`) and the body `- **Status:**` line. `docs/adr/README.md`'s index row updated to match. The document was re-read before flipping and confirmed to describe what actually shipped: `src/openkos/application/query.py` exists, exports `QueryOutcome`/`run_query`/`FiledAnswerPlan`/`stage_filed_answer` and related pure predicates, imports nothing from `openkos.cli`, and binds no concrete backend (`llm`/`embedder` arrive as `Protocol`-typed parameters, matching the Decision section's stated invariant).

**One known minor discrepancy, left unedited (ADRs are immutable after acceptance):** the ADR's Accepted Risk table states the `answer` patch target has "123 ... one production call site." Implementation found a 124th site in the attribute-object form (`monkeypatch.setattr(main_mod, "answer", spy)` in `test_confidential_local_exemption.py:228`), invisible to the string-literal grep the ADR's inventory was built from. This does not contradict the ADR's Decision or invariants — it is a completeness gap in one count within the Consequences narrative, not in the ruling. Recorded here rather than silently edited into the ADR.

## Implementation Deviations (both sound, both already reviewed)

1. **`_slugify` promoted to `bundle/source_titles.py`.** `_stage_filed_answer` called `_slugify`, a CLI-shared utility used at 8 other `main.py` call sites (ingest, tarball extraction, LLM-extracted staging). Importing it from `application` would have violated the layering invariant. Promoted `slugify`/`_is_slug_char`/`_SLUG_COLLAPSE_RE` to `bundle/source_titles.py`, mirroring that file's own existing precedent (`titleize` was promoted there from `cli/main.py`'s `_titleize` for the identical reason). `main.py`'s `_slugify` now delegates; all 126 `test_slugify.py` approval tests pass unchanged. This dependency was not named in `design.md`'s D3 "moves" list.
2. **A 124th `answer` patch site**, found in the attribute-object form (see ADR note above), migrated alongside the other 123 to `openkos.application.query.answer`.

## Task Completion Gate

`tasks.md` (obs #3128) was read in full before this archive proceeded: all implementation tasks across both slices (Phases 0–9) are checked `- [x]`. No unchecked task exists. No stale-checkbox reconciliation was needed.

## Archive Mechanics

- `openspec/specs/query-application-service/spec.md`: created via mechanical `cp` into a temp file within the target directory, `diff -r` against the source delta returned empty, then `mv` into place. Readback `diff` (source vs. final destination) is empty.
- `openspec/specs/query-command/spec.md`: edited in place (Purpose-paragraph replacement per the delta's own instruction); `git diff` confirms the edit is scoped to exactly the Purpose paragraph.
- `docs/adr/0018-application-layer-for-bounded-context-services.md` and `docs/adr/README.md`: edited in place (status flip only).
- `openspec/changes/query-application-service/apply-progress.md`: materialized from Engram observation #3129 (the only Slice/ADR-relevant fact updated in the materialized copy is a short "Note on final delivery" pointer to this report — the observation's own body is otherwise reproduced verbatim) so the archived folder is self-contained on disk.
- The entire `openspec/changes/query-application-service/` folder was moved (not copied) to `openspec/changes/archive/2026-08-31-query-application-service/` via `git mv`, verified by `diff -r` against a pre-move recursive snapshot (archive-report.md excluded, since it is additive and did not exist in the snapshot) and by confirming the original path no longer exists on disk.

## Observation IDs Read (traceability)

| Artifact | Observation ID |
|----------|-----------------|
| `sdd/query-application-service/proposal` | #3124 |
| `sdd/query-application-service/spec` | #3125 |
| `sdd/query-application-service/design` | #3126 |
| `sdd/query-application-service/tasks` | #3128 |
| `sdd/query-application-service/apply-progress` | #3129 |
| `sdd/query-application-service/verify-report` | #3131 |

## Scope Boundary — What Remains Open on #918

#918 asks for three application services: **query**, **ingestion**, and **lifecycle**. This change delivered **query only**. Ingestion and lifecycle composition are explicitly out of scope for this change (per `proposal.md`, obs #3124: "scope = QUERY context only (no ingest/lifecycle stubs)") and remain unimplemented. #918 is left open to track that remaining work; no artifact in this change states or implies #918 is complete.

## SDD Cycle Complete

The `query-application-service` change has been fully planned, implemented, verified, and archived. Ready for a future change to pick up the ingestion and lifecycle application-service work #918 still tracks.
