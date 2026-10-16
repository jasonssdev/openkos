# Archive Report: unmerge-api

**Archived**: 2026-10-16 (S4, the last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-16-unmerge-api/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1432

## Final State At Close

- **Implementation**: four slices before this one merged to `main`, each one squash-merged PR, stacked to `main` in order. S4 (this archive) closes the issue.

| Slice | PR | Squash commit | Authored lines (additions + deletions; generated snapshots excluded) | CI `test (3.14)` |
|-------|----|---------------|----------------------------------------------------------------------|------------------|
| S1 plan, ADR-0064 Proposed | #1440 | `2fa4d702` | about 600 (docs) | 10m25s |
| S2a history, preview, version | #1441 | `7d6ee738` | about 990 | 11m38s |
| S2b apply, `UnmergeInterrupted` | #1443 | `ad91829d` | about 780 | 11m49s |
| S3 local HTTP API | #1444 | `8fa50102` | about 540 | 11m48s |
| S4 wording, CHANGELOG, spec merge, ADR, archive | this PR | | docs only | |

- **Shipped surface** (all additive, `API_VERSION` unchanged at 1): `merge_history`, `preview_unmerge`, `apply_unmerge`, `MergeRecord`, `UnmergeStep`, `UnmergeVersion`, `UnmergePreview`, `NotUnmergeable`, `UnmergeStale`, `Unmerged`, `UnmergeInterrupted` (a `WriteFailed`; `WriteFailed.operation` gains `"unmerge"`). Over HTTP: `GET /v1/merges`, `POST /v1/unmerge/preview`, `POST /v1/unmerge/apply`, the rows `survivor_missing`, `not_merged`, `survivor_edited` (409), `stale` for `UnmergeStale` (409) and `unmerge_interrupted` (500), and the regenerated OpenAPI snapshot with one `UnmergeVersion` component.
- **ADR-0064**: Accepted, in all three places (frontmatter, the `**Status:**` body line, the `docs/adr/README.md` row).
- **Not changed, on purpose**: `openkos unmerge` and `docs/cli.md`. No keep-distinct ruling is recorded after an API unmerge, and no derived index is refreshed (proposal, out of scope).

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| stable-python-api | Updated | 3 ADDED requirements appended: Merge History Is Readable; Unmerge Is Previewed, Then Applied Against Its Version; Unmerge Extends v1 Additively. 19 requirements now. |
| local-http-api | Updated | 2 MODIFIED replaced in place (The Operations Are Served Under `/v1`, One Per Python API Operation; Outcomes And Errors Map To Status Codes With A Uniform Body), 1 ADDED appended (The Unmerge Version Travels Unchanged From Preview To Apply). 19 requirements now. |

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of both deltas was compared as an exact line against its main spec after the merge: 47 headings, 0 missing. One scenario name of the new stable-python-api requirement repeated an existing one ("The surface diff is additive", already under Group Decisions Extend v1 Additively); it was renamed "The unmerge surface diff is additive" in the delta and the living spec, so every heading is unique. Before the merge each MODIFIED heading matched exactly once and no ADDED heading existed. No REMOVED or RENAMED headings.

## Requirement-vs-Code Check

Each requirement was read against `src/openkos/application/unmerge_service.py`, `write_gate.py`, `src/openkos/api/_unmerge.py`, `_wiring.py`, `src/openkos/localapi/` and the tests, not against the task list.

| Requirement | Result |
|-------------|--------|
| stable-python-api: Merge History Is Readable | Met. `unmerge_service.merge_history` reads the committed sidecar, newest first, title from the recorded snapshot (`None` when unreadable); an invalid id or a concept with no ledger lists nothing. No lock, no client, no write (`test_history_lists_a_groups_merges_newest_first`, byte snapshot). |
| stable-python-api: Unmerge Is Previewed, Then Applied Against Its Version | Met. One step for the newest merge, the whole unwind for a deeper one; `survivor_missing` (with `absorbed_into`), `not_merged` and `survivor_edited` returned; `survivor_edited` decided from `survivor_edits_discarded`, never from error text, for the first step only (design D4). The apply holds one lock, re-plans, returns `UnmergeStale` on a moved digest, runs each step as the CLI's single-step unmerge with its own commit, re-raises a first-step refusal and raises `UnmergeInterrupted` for a later one. A pair splits back to its pre-merge bytes except `log.md`; a group splits in one apply. |
| stable-python-api: Unmerge Extends v1 Additively | Met. The classifier reported every change additive in S2a and S2b; `API_VERSION` is 1. |
| local-http-api: The Operations Are Served Under `/v1` | Met. The three routes are in `EXPECTED_ROUTES` and the published schema; the Concept ID travels in the query string or the body. |
| local-http-api: Outcomes And Errors Map To Status Codes | Met. `_NOT_UNMERGEABLE` holds the three 409 rows, `UnmergeStale` answers `stale`, `UnmergeInterrupted` precedes `WriteFailed`; the spec-table test counts every code exactly once. |
| local-http-api: The Unmerge Version Travels Unchanged From Preview To Apply | Met. `UnmergeApplyRequest` is an `_EchoRequest`; a flipped echoed flag is 409 `stale`; an unknown key is 422; `UnmergeVersion` is one component. |

## Deviations From The Design

1. **The survivor-edit check at preview covers the first step only (amended in S1 before merge).** The survivor a later step sees includes any deprecated-status export folded in by the earlier step, so predicting it from the ledger alone could refuse a chain the run completes. A later step's check runs in its own Phase A at apply and stops the chain as `UnmergeInterrupted`.
2. **Fewer digest lines (S2a).** Per-step lines, per-file lines for later steps, the recreated-path line and document-name lines were written first; mutation checks showed each redundant with the ledger digest and the first step's whole-bundle read dependencies. They were dropped and design D2 rewritten; every kept line (target, discard flag, ledger, survivor, rewrite files, snapshot catalog, read dependencies) has a test that fails without it, and a test pins that a delta entry leaves the catalog out.
3. **A self-merge ledger entry (S2b).** The full-change review against the spec found that a hand-authored entry naming the survivor itself let the planner's `ValueError` escape `preview_unmerge` unwrapped; it is now a `Refused`, with a test.
4. **No `exploration.md`.** The change was planned without a separate exploration artifact.

## Verification Record

- Strict TDD in every code slice: RED observed first (an import error on the new names, or failing tests), then GREEN. Mutations on the exact guarded lines with `__pycache__` purged and the file restored from a byte copy: S2a 25 (4 survived and led to the digest change in Deviation 2; every kept line killed), S2b 15, S3 13, all killed at merge.
- Each slice ran `ruff check`, `ruff format --check`, `mypy .`, the focused suites and `evals/run_self_tests.py` (58 of 58).
- Full `OPENKOS_REQUIRE_API_BASE_REF=1 uv run pytest --cov` on the cumulative content of S2a, S2b and S3 before S2b merged: 13519 passed, 6 skipped, 96.76% coverage. CI ran the full suite on every PR.

## Follow-ups

- #1442: whether and how the app offers to split a page edited since its combine (`discard_survivor_edits`), a product decision for the wording brief.
