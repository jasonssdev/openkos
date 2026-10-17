# Archive Report: review-undo

**Archived**: 2026-10-16 (S7, the last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-16-review-undo/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issues**: #1467 (list every combined page) and #1466 (undo keep separate, connections and how-quickly settings), both closed by S6.

## Final State At Close

Six slices before this one merged to `main`, each one squash-merged PR, stacked to `main` in order.

| Slice | PR | Squash commit | Authored lines (additions + deletions; generated `api_surface.json`, `openapi.json`, `schema.d.ts` excluded) | CI `test (3.14)` |
|-------|----|---------------|------------------------------------------------------------------------------------------------------------------|------------------|
| S1 plan, ADR-0067 Proposed | #1472 | `bc1487ab` | about 900 (docs) | green |
| S2 combined pages: `merged_concepts`, `GET /v1/merged` | #1473 | `550451d0` | about 320 | 12m10s |
| S3 reopen: `kept_distinct`, `preview_reopen`, `apply_reopen`, `NotUndoable`, `UndoStale` | #1474 | `e401ae37` | about 1,240, 720 of them tests (`size:exception`) | 11m52s |
| S4 unrelate: `preview_unrelate`, `apply_unrelate` | #1478 | `73e6c02e` | about 890, 513 of them tests (`size:exception`) | 12m32s |
| S5 tier change: `clear_type_tier`, `preview_tier_change`, `apply_tier_change`, `TierPlan.configured_tier` | #1480 | `61cdbed1` | about 1,040, 552 of them tests (`size:exception`) | 10m29s |
| S6 review screen: combined list from the engine, Undo for the three kinds, the brief | #1481 | `98c63640` | about 520, 296 of them tests (`size:exception`) | 9m45s |
| S7 CHANGELOG, spec merge, ADR-0067 Accepted, archive | this PR | | docs only | |

- **Shipped surface** (all additive, `API_VERSION` unchanged at 1): `merged_concepts`, `MergedConcept`, `kept_distinct`, `KeptDistinct`, `preview_reopen`, `apply_reopen`, `ReopenPreview`, `ReopenVersion`, `Reopened`, `preview_unrelate`, `apply_unrelate`, `UnrelatePreview`, `UnrelateVersion`, `Unrelated`, `preview_tier_change`, `apply_tier_change`, `TierChangePreview`, `TierChangeVersion`, `TierChanged`, `VolatilityTier`, `NotUndoable`, `UndoStale`, and the field `TierPlan.configured_tier`. `WriteFailed.operation` gains `"reopen"` and `"unrelate"`. Over HTTP: `GET /v1/merged`, `GET /v1/kept-distinct`, `POST /v1/reopen/{preview,apply}`, `POST /v1/unrelate/{preview,apply}`, `POST /v1/tiers/{preview,apply}`, and the rows `not_kept_distinct`, `not_related`, `tier_unchanged` (409), `unknown_type` (422); `UndoStale` answers `stale`.
- **Engine support**: `application/undo_service.py` (new); `unmerge_service.merged_concepts`; `config.clear_type_tier` and the shared `_editable_type_tiers_header`; `vcs.autocommit.plan_config_edit` and the config-commit port take `tier: str | None`.
- **Screen**: "Pages you combined" lists every combined page from `GET /v1/merged`; keep separate, connect and set-tier confirmations offer Undo; the new words are in `docs/decision-wording.md` (landed with S6).
- **ADR-0067**: Accepted, in all three places (frontmatter, the `**Status:**` body line, the `docs/adr/README.md` row).
- **Not changed, on purpose**: the CLI and `docs/cli.md`; no settings screen or page view (the API operations for "Ask me again", "Remove" and the tier setting ship; their screens do not).

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| stable-python-api | Updated | 5 ADDED requirements appended: Every Combined Page Is Listable; A Kept-Separate Ruling Is Listed And Reopened Against Its Version; A Typed Relation Is Removed Against Its Version; A Type's Tier Is Set Or Restored Against Its Version; Undo And The Combined-Pages Read Extend v1 Additively. The Purpose's v1 operation list names the new reads and undos. |
| local-http-api | Updated | 2 MODIFIED replaced in place (The Operations Are Served Under `/v1`, One Per Python API Operation; Outcomes And Errors Map To Status Codes With A Uniform Body), 1 ADDED appended (The Undo Versions Travel Unchanged From Preview To Apply). |
| desktop-review | Updated | 2 MODIFIED replaced in place (A Choice Is Previewed, Then Applied Against Its Version; A Combine Is Undone With Split Again), 1 ADDED appended (Keep Separate, A Connection And A Setting Are Undone From Their Confirmation). The Purpose names the new undos. |

The deltas' "(Previously: ...)" notes were not carried into the living specs; the outcomes requirement's older "(Previously: ...)" note about the unmerge rows was dropped with its replacement.

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of each delta was compared as an exact line against its living spec after the merge: each appears exactly once. Before the merge each MODIFIED heading matched exactly once and no ADDED heading existed. No REMOVED or RENAMED headings.

## Requirement-vs-Code Check

Each requirement was read against the shipped files, not the task list.

| Requirement | Result |
|-------------|--------|
| Every Combined Page Is Listable | Met. `merged_concepts` walks the ledgers, derives the id from the walked path, keeps existing pages with a non-empty `merge_history`, skips an unreadable ledger, orders by the newest merge then id; no lock (tested with the lock held). |
| A Kept-Separate Ruling Is Listed And Reopened | Met. `kept_distinct` newest first; `preview_reopen` refuses invalid, single and unruled member sets; the digest covers members and the sidecar; `apply_reopen` re-plans under one lock, commits the sidecar only, resolves no row. |
| A Typed Relation Is Removed | Met, with one wording correction at archive: the type is refused only when blank, because `unrelate` accepts any non-blank type (surrounding spaces stripped); the spec now says so. The source frontmatter is re-serialised, which the spec already allowed ("no longer holds the relation"). |
| A Type's Tier Is Set Or Restored | Met. `unknown_type`, `tier_unchanged`, `ValueError` for an unknown tier, `Refused` for an uncommitted same-entry edit, no queue resolution (the write is `fsio.write_atomic`, not `set_volatility_core`). A restore by removal keeps a `type_tiers:` header the decision appended; spec and design say so. |
| Undo And The Combined-Pages Read Extend v1 Additively | Met. The classifier printed only `additive` lines on S2, S3, S4 and S5. |
| Operations Are Served Under `/v1` (modified) | Met. Eight routes in both route tables and the published schema. |
| Outcomes And Errors Map (modified) | Met. `_NOT_UNDOABLE` holds the four rows; `UndoStale` is `stale`; the spec-table test counts every code once; the TS `ERROR_CODES` parity test holds. |
| The Undo Versions Travel Unchanged | Met. The three apply bodies are `_EchoRequest`s; an altered digest is 409 `stale`; an unknown key and a tier outside the three are 422; one component per version. |
| A Choice Is Previewed (modified) | Met. `undoFor` gives Undo for merge, keep_distinct, relate and set_tier plans. |
| A Combine Is Undone With Split Again (modified) | Met. `load()` builds the list from `GET /v1/merged`; the heading is "Pages you combined". |
| Keep Separate, A Connection And A Setting Are Undone | Met, with one sentence added at archive: a setting that is not one of the three tiers offers no Undo, as the code does. Visual rendering not verified. |

## Decisions Taken During Implementation

1. **One digest line dropped (S4).** A "withdraws" line was redundant with the target's bytes, which are present only when the export is withdrawn; a mutation showed it, and a test now pins that a flipped withdrawal moves the digest.
2. **Every refusal reason has its HTTP row from S3**, so no `NotUndoable` can reach the "reason added after this table" 500 while later slices land.
3. **`VolatilityTier` types the tier change; `TierPlan.configured_tier` stays `str | None`**, because `type_tiers` is a lazy passthrough and may hold text that is not a tier. The app offers no Undo in that case.
4. **The brief changed with S6**, beside the code that shows the words, rather than in this archive slice.
5. **No `exploration.md`.** The change was planned without a separate exploration artifact.

## Verification Record

- Strict TDD in every code slice: RED observed first (import errors on the new names, failing tests, 404 routes, `is not a function` in the client); S5's route tests were checked by renaming each route (5 failures each) because they were written with the routes.
- Mutations on the exact guarded lines, each restored from a byte copy (Python with `__pycache__` purged): S2 9, S3 19, S4 16, S5 17, S6 11. Survivors led to new tests or, once, to removing a redundant line; one kept survivor (`len(members) >= 2`, redundant with the ruling lookup) is recorded in `tasks.md`.
- Local full suite `OPENKOS_REQUIRE_API_BASE_REF=1 uv run pytest --cov`: S2 13583 passed, S3 13645 passed, S5 (which contains S4) 13749 passed, each with 6 skipped and 96.78% coverage. `ruff check`, `ruff format --check`, `mypy .` clean on every Python slice; `evals/run_self_tests.py` 58 of 58 on S2 and S5. Node: `typecheck`, vitest (221 at S6), `check:schema`, `build` on every slice that touched the client.
- **Not verified**: visual rendering of the screen (no screenshots or UI automation here), and the screen against a live engine.

## Follow-ups

None filed: no product decision was left open. The settings screen ("Pages you kept separate" with Ask me again, the tier setting) and a page view (connections with Remove) are roadmap screens; the operations they need now exist.
