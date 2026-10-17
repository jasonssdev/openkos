# Archive Report: undo-findings

**Archived**: 2026-10-17 (with S2 and S3, the code slices, in one PR)
**Archive folder**: `openspec/changes/archive/2026-10-17-undo-findings/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1497 (the engine and API half; the review screen adopts the operations in its own change).

## Final State At Close

| Slice | PR | Squash commit | Authored lines (additions + deletions; generated `api_surface.json`, `openapi.json`, `schema.d.ts` excluded) |
|-------|----|---------------|-------------|
| S1 plan, ADR-0074 Proposed | #1521 | `f909112e` | 638 (docs) |
| S2 reopen a declined contradiction | #1524 | the squash of #1524 | 1,195, 700 of them tests |
| S3 undo a reconcile, with S4 (CHANGELOG, spec merge, ADR-0074 Accepted, archive) | #1524 | the squash of #1524 | about 1,690, 755 of them tests and about 400 docs |

- **Shipped surface** (all additive, `API_VERSION` unchanged at 1): `declined_contradictions`, `DeclinedContradiction`, `preview_reopen_contradiction`, `apply_reopen_contradiction`, `ContradictionReopenPreview`, `ContradictionReopenVersion`, `ContradictionReopened`, `reconciled_pairs`, `ReconciledPair`, `preview_unreconcile`, `apply_unreconcile`, `UnreconcilePreview`, `UnreconcileVersion`, `Unreconciled`, and the reasons `not_declined` and `not_reconciled` on `NotUndoable`. Over HTTP: `GET /v1/declined-contradictions`, `POST /v1/contradictions/reopen/{preview,apply}`, `GET /v1/reconciled`, `POST /v1/unreconcile/{preview,apply}`, and the 409 rows `not_declined` and `not_reconciled`.
- **Engine support**: `undo_service` gains both kinds; `reconcile_service.remove_reconciliation_note` (the inverse beside the note builder) and the public name `existing_reconciliation_state`; `WriteFailed` operation `unreconcile`.
- **Client**: six methods in `desktop/client/index.ts`, two codes in `ERROR_CODES`.
- **Wording**: `docs/decision-wording.md` gives every contradiction and revision confirmation an Undo, and words both refusals.
- **ADR-0074**: Accepted, in all three places (frontmatter, the `**Status:**` body line, the `docs/adr/README.md` row).
- **Not changed, on purpose**: the CLI and `docs/cli.md` (the reconcile undo is the app's; `reconcile`'s refusal text is unchanged), and `desktop/ui/` (rebuilt in parallel).

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| stable-python-api | Updated | 3 ADDED requirements appended: A Declined Contradiction Is Listed And Reopened Against Its Version; A Reconcile Is Listed And Undone Against Its Version; Undoing Findings Extends v1 Additively. The Purpose's v1 operation list names the two new undos. |
| local-http-api | Updated | 1 ADDED requirement appended: Undoing A Decline Or A Reconcile Is Served Under `/v1`, with its own route and error tables, so the shared operations and outcomes tables, which concurrent changes edit, were not modified. |

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of each delta was compared as an exact line against its living spec after the merge: each appears exactly once. Three scenario names first collided with existing ones ("A page edited after the preview is stale", "The surface diff is additive", "An altered version is stale") and were renamed in both the delta and the living spec before the merge was kept. No MODIFIED, REMOVED or RENAMED headings.

## Requirement-vs-Code Check

| Requirement | Result |
|-------------|--------|
| A Declined Contradiction Is Listed And Reopened | Met. `declined_contradictions` walks the sidecars without the findings join (a test asserts no `findings.db` appears); the preview sorts the pair and refuses `not_declined`; the digest covers pair, discriminator and sidecar; the apply re-plans under one lock and commits the sidecar only; `apply_contradiction_decision` with state `open` resolves no queue row. One spec phrase was removed during S2: a pair naming one page twice is not refused, because the engine records a merged-body finding that way. |
| A Reconcile Is Listed And Undone | Met. Mixed pairs and edges to missing pages are not listed; edges are removed through `prepare_unrelate`; the mark is its withdrawal; notes are removed only in reconcile's shape; an emptied `relations` list is dropped on a rewritten page; one log line; one commit of the changed pages and the log; no queue call. The digest is each page's id and bytes plus the withdrawal; the resolution is derived from those bytes. |
| Undoing Findings Extends v1 Additively | Met. The classifier printed only `additive` lines on S2 and S3. |
| Undoing A Decline Or A Reconcile Is Served Under `/v1` | Met. Six routes in the route-set and error-status tables and the published schema; GETs write nothing (snapshot tests); unknown top-level fields are 422; altered digests are 409 `stale`; both rows are in the spec-code table test and the TS `ERROR_CODES` parity test. |

## Decisions Taken During Implementation

1. **No one-page-twice guard on the reopen.** A mutation showed it redundant, and a smoke on a real workspace showed merged-body declines are recorded over `(page, page)`; the guard would have refused them.
2. **Digest lines trimmed.** The resolution and pair lines of the unreconcile digest were redundant with each page's id and bytes (mutations survived); a test with two pages of identical bytes pins the ids.
3. **Byte restoration is for pages in the engine's form.** `reconcile` writes `relations: []` on the page with no edge, and `unrelate`'s withdrawal writes `status: stable`; the undo drops an emptied list and keeps that withdrawal, so engine-written pages come back byte for byte (confirmed on a real workspace for `--winner` and `--revision`), and hand-written pages keep their values, re-serialised.
4. **S2, S3 and the archive ship in one PR (#1524).** S2 was green but fell behind `main` twice while concurrent changes merged; each refresh costs a full CI run, so S3 and the archive joined it as separate commits instead of waiting for a third and fourth run.
5. **No `exploration.md`.** The change was planned without a separate exploration artifact.

## Verification Record

- Strict TDD in both code slices: RED observed first (missing `undo_service` names, facade import errors, 404 routes, vitest failures for the new client methods and the engine-code parity test).
- Mutations on the guarded lines, each restored from a byte copy with `__pycache__` purged: S2 8, S3 21. Survivors led to four new tests and three removals, recorded in `tasks.md`.
- Local full suite on S2, `OPENKOS_REQUIRE_API_BASE_REF=1 uv run pytest --cov`: 14074 passed, 6 skipped, 96.80% coverage; `evals/run_self_tests.py` 58 of 58; `ruff check`, `ruff format --check`, `mypy .` clean; desktop `typecheck`, vitest, `check:schema`, `build` green. On S3 with the archive (before the last rebase): 14146 passed, 6 skipped, 96.79%; evals 58 of 58; the same lint, type and desktop checks clean.
- Smoke on a scratch copy of a real workspace, through `openkos.api`: a merged-body decline reopened (replay answered `not_declined`); a CLI `reconcile --winner` and a `reconcile --revision` each undone, both pages byte-identical to before (`git diff --stat` empty).
- **Not verified**: the screens (none in scope), and a live engine over HTTP outside the test client.

## Follow-ups

None filed: no product decision was left open. The review screen's Undo for these confirmations and a settings list ("Disagreements you left as they are", "Pages you settled") are screen work; the operations they need now exist.
