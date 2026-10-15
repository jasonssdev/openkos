# Archive Report: judging-application-service

**Archived**: 2026-10-15 (slice S4, PR 9 of 9)
**Archive folder**: `openspec/changes/archive/2026-10-15-judging-application-service/`
**Store mode**: openspec (filesystem merge and move). The launch preflight also names `hybrid`; an Engram copy of this report was saved as well.

## Artifacts Read

- Files read: `proposal.md`, `design.md`, `exploration.md`, `tasks.md` (S4 section), the three delta specs under `specs/`, and `verify-report.md`, which was copied into the change folder from the verify-phase scratchpad.
- `apply-progress.md` was archived unread, as an intermediate snapshot.
- Engram observations read: none. The locators were filesystem paths.

## Final State At Close

- **Implementation**: all nine implementation slices are merged to `main`, S0a through S3b. The planning PR is #1381. No source file changed in S4.
- **Tasks**: `tasks.md` shows 67 checked and 8 unchecked. All 8 unchecked items are S4.1 to S4.8, this archive phase. The archived file is not repaired. The S4 work is recorded here.
- **Verification**: per `verify-report.md`, at verification time on main `4ffbf10f`, the verdict was 0 CRITICAL, 3 WARNING, and a goal-level WARNING (G1). The test run then was `328 passed`. Those WARNINGs were spec overstatements and the G1 queue limitation. They were closed in this archive by amending the specs, not by code changes. The verify report is an intermediate snapshot and is not updated.

## PRs And Commits

| Slice | PR | Commit |
|-------|----|--------|
| Planning | #1381 | `d104de67` |
| S0a characterization goldens | #1382 | `987da94a` |
| S0b daemon clobber xfail and UNCERTAIN pin | #1383 | `8d61562d` |
| S1 identity serve, persist, reassemble | #1385 | `10061ada` |
| S2a amend-only write-through, daemon fix | #1386 | `9ce0e69a` |
| S2b quote, token, notices | #1387 | `4132dd7b` |
| S2c identity run, retry, outcome | #1388 | `3787b763` |
| S3a relation_type quote and run | #1389 | `7d329e47` |
| S3b volatility quote and run | #1390 | `4ffbf10f` |

No commit for #1384 is in the branch history, and it is not listed in the launch scope. This is recorded here, not inferred.

## Amendments Applied Before Merge

Applied to `specs/judging-application-service/spec.md` from the verify report's wording:

| Item | Amendment |
|------|-----------|
| W1 | `served` counts SAME or DIFFERENT verdicts. `uncertain` counts servable UNCERTAIN verdicts. `fresh` is the remainder, with no servable stored verdict. The quote contract is `total == served + uncertain + fresh`. The `fresh` definition is this archive's wording; the verify report did not define it. |
| W2 | The quarantine notice applies to `relation_type` only. Identity has no quarantine notice. Relation notices carry rendered `text`. The identity truncation notice carries a count. |
| W3 | The token is an immutable value the caller hands back unchanged. Clients MUST NOT construct or alter one. |
| Outcome | `not_in_quote` is the work keys, and its length is the count. `dropped` and `observer_failed` are added. `judged == persisted + dropped + unpersisted`. The `observer_failed` description comes from the shipped field, not the verify report. |
| Include-uncertain | The opt-in applies to identity only. `relation_type` and `volatility` bind `include_uncertain=false`. |
| Relation drop | A `relation_type` or `volatility` answer whose sampled concepts or endpoints changed during the call is dropped and reported as `dropped`. |
| J13 | `BackendUnavailable` propagates once completed chunks are persisted. If that chunk's own persist fails, the typed `unpersisted` outcome is returned with `failure` set. |
| G1 | Purpose: a known-limitation paragraph. A judged item is listable by `curate-decisions` only after a producer pass (today the daemon) enqueues or amends its row. Identity write-through is amend-only. `relation_type` and `volatility` write no queue row. Issue #1391 tracks an app-callable refresh. |

Applied to `openspec/specs/curate-decisions/spec.md`, the living Purpose (S4.2): judging is not part of the surface. The `judging-application-service` capability is where a pending-evaluation item becomes decidable.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| judging-application-service | Created | `openspec/specs/judging-application-service/spec.md`, copied mechanically from the amended delta. The delta already carries the Purpose. 19 requirements. |
| curate-decisions | Updated | `gentle-ai sdd-archive-compose` exit 0. 2 MODIFIED replaced in place, 1 ADDED appended. 17 requirements total. Purpose hand-fixed (S4.2). |
| pending-work | Updated | `gentle-ai sdd-archive-compose` exit 0. 2 ADDED appended. 15 requirements total. |

### Heading Match Table (MODIFIED and ADDED, name-matched)

| Delta | Op | Delta heading | Living heading matched | Living line |
|-------|----|---------------|------------------------|-------------|
| curate-decisions | MODIFIED | Only Judged-SAME Identity Items Are Mergeable | `### Requirement: Only Judged-SAME Identity Items Are Mergeable` (exact) | 130 |
| curate-decisions | MODIFIED | The Surface Computes No Judgment | `### Requirement: The Surface Computes No Judgment` (exact) | 401 |
| curate-decisions | ADDED | A Persisted Verdict Written Through To An Open Row Is Reflected In The List | none before merge; appended | 472 |
| pending-work | ADDED | An Identity Producer Carries A Servable Stored Verdict | none before merge; appended | 427 |
| pending-work | ADDED | Writing A Verdict Through To An Open Row Does Not Count As A New Or Stale Proposal | none before merge; appended | 485 |

Mutation check (S4.1 independent mutation): a copy of the curate-decisions delta whose MODIFIED heading was renamed to `The Surface Computes No Judgment RENAMED` made `sdd-archive-compose` exit 1 with `no canonical requirement named`. The name-match gate is therefore live.

## ADR

- ADR-0059 is Accepted in three places: frontmatter `status`, the `**Status:**` line, and the `docs/adr/README.md` row. The date is unchanged at 2026-10-15. This follows the ADR-0053 accept commit `9d13a2ee`.
- The ADR status consistency tests pass.

## Architecture Docs (S4.4)

`docs/architecture.md` is unchanged. Its `application/` entry is a generic listing that names no services. The general shape did not change.

## Deviations From Design And Spec

1. Shipped chunk sizes are `IDENTITY_CHUNK_SIZE` 1, `RELATION_CHUNK_SIZE` 4 and `VOLATILITY_CHUNK_SIZE` 1. The verify report records the relation and volatility values as differing from the design's 8 and 4. The spec states only the latency formula, which still holds, so no spec change was needed.
2. `include_uncertain` is ignored for relation_type and volatility. The spec is amended (above).
3. `JudgingOutcome` gained `dropped` and `observer_failed`. The spec is amended (above).
4. Volatility answers about concepts edited or forgotten mid-call are dropped. The spec is amended (above).
5. `PERSIST_ATTEMPTS` and its backoff are UNMEASURED placeholders. The spec states no number, so none is overstated.
6. Zero-call groups are counted through a recording stand-in. The spec states the behavior, not the mechanism, so no change was needed.
7. The J13 nuance is amended (above).

## Open Findings And Follow-ups

1. Issue #1391: an app-callable refresh, so a judged item becomes listable without a daemon pass (G1).
2. Measure `PERSIST_ATTEMPTS` and its backoff against the daemon commit section. Both are UNMEASURED.
3. No test that app spend is excluded from the daemon budget. The property holds by construction (no shared counter), but it is untested.
4. The pre-existing `suggest-volatility` CLI path stores answers without a digest guard, so a concept forgotten mid-call can leave its text in the volatility store. Reported in the launch scope. Not re-verified in this phase.
5. curate's `_serve_identity_rows` serves queue-row verdicts without a rubric check.
6. Token serialization belongs to the MVP 6 API stage (ADR-0039).
7. Judging of contradictions and decision revisions is not covered by this surface.
8. Verify F4 is resolved at archive. The surface exposes `chunk_size` and the token ceiling rather than a `max_calls` parameter; the relation_type/volatility requirement now states this, and that the chunk size is passed as each chunk's `max_calls`.

## Checks Run In This Phase

- `uv run pytest tests/unit -q -k "adr or openspec or spec"`: `275 passed, 12222 deselected in 10.15s`, exit 0.
- `uv run ruff check .`: `All checks passed!`, exit 0.
- `uv run ruff format --check .`: `602 files already formatted`, exit 0.

## Mechanical Readback

- The snapshot readback at move time compared the pre-move copy of the change folder with the archive folder. `diff -r` output was empty, exit 0.
- The original folder `openspec/changes/judging-application-service/` does not exist.
- The verify report was copied into the change folder and compared with `diff -r`. Output was empty, exit 0.
- Spec and ADR edits were applied by targeted `sed` or `Edit` replacements, not by copying. Main-spec merges used `gentle-ai sdd-archive-compose`.
