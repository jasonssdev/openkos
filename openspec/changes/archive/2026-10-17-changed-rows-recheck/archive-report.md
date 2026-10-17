# Archive Report: changed-rows-recheck

**Archived**: 2026-10-17 (the last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-17-changed-rows-recheck/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1475, items 1 and 2. Item 3 (the question order for pages of
different kinds) is not part of this change and stays with #1477; #1475 stays
open for it.

## Final State At Close

| Slice | PR | Squash commit | Authored lines (generated `api_surface.json`, `openapi.json`, `schema.d.ts` excluded) | CI `test (3.14)` |
|-------|----|---------------|---------------------------------------------------------------------------------------|------------------|
| S1 + S2 engine and API, review screen, plan, brief | #1491 | `2c85736b` | about 615 code and tests (574 added, 41 removed), plus the plan and the brief | green |
| S3 spec merge and archive | this PR | | docs only | |

S1 and S2 shipped as one PR: `main` moved four times while S1 waited on CI,
each time needing a rebase and a fresh run, so the screen slice was folded in
(recorded in `tasks.md`).

- **Shipped surface** (additive, `API_VERSION` unchanged at 1): the field
  `KindRefresh.retired_missing`, and `preview_decision` answering
  `Stale("row_inputs_changed")` for a decision whose inputs changed (a reason
  that already existed and already maps to 409 `stale`).
- **Engine**: `pending_queue.retire_open_naming_absent` (new),
  `queue_refresh.refresh_decision_queue` calls it before the producers with an
  absence predicate over the bundle; `ProducerResult.retired_missing`;
  `curate_decisions.preview_decision` refuses a stale row after the action
  checks.
- **Screen**: `ReviewModel.visit()`, `recheck()`, `startCheck(text, kind)`,
  `offersCheckAll`; `wording.ts` `CHANGED_CHECK`, the Check again option and
  the consent heading by kind; `render.ts` reads `offersCheckAll` (one
  condition and one line of copy); `main.ts` calls `visit()`.
- **Not changed, on purpose**: the CLI; the judging run's amend-only
  write-through (the refresh is the publisher, design D2); no one-item quote
  (design D4); no styling.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| curate-decisions | Updated | 1 MODIFIED replaced in place: Staleness Is Marked, Never Hidden. |
| pending-work | Updated | 1 MODIFIED replaced in place (Refresh Retires A Row Only After A Complete Pass For Its Kind), 1 ADDED appended (Refresh Retires Every Row Naming A Concept That No Longer Exists). |
| stable-python-api | Updated | 1 MODIFIED replaced in place: Refreshing The Decision Queue Is Exposed As A Model-Free Operation. |
| desktop-review | Updated | 2 MODIFIED replaced in place (A Stale, Busy Or Refused Choice Is Never Applied Blindly; The Check Is Agreed To Once Per Batch And Can Be Stopped), 1 ADDED appended (A Changed Decision Is Checked Again, Never Decided As It Was). The Purpose names the check again. |

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of each delta was compared
as an exact line against its living spec after the merge: each appears exactly
once. Before the merge each MODIFIED heading matched exactly once and no ADDED
heading existed. No REMOVED or RENAMED headings.

## Requirement-vs-Code Check

Each requirement was read against the shipped files, not the task list.

| Requirement | Result |
|-------------|--------|
| Staleness Is Marked, Never Hidden (modified) | Met. The check runs after `_refusal` and skip, so an action not offered is still `NotApplied` and skip is `skipped`; every write action of a stale row, merge-group included, is `Stale("row_inputs_changed")`. `list_decisions` is untouched. |
| Refresh Retires Every Row Naming A Concept That No Longer Exists | Met. Targets only, `identity` and `relation_type` only, absence asked inside the transaction, `FileNotFoundError` alone counts as missing, resolved rows untouched, the function never creates the queue (the refresh's own `_ensure_queue` may, as before, when it has something to publish). Not exercised by a test: a target whose `stat` fails with another `OSError` (not taken for missing). |
| Refresh Retires A Row Only After A Complete Pass For Its Kind (modified) | Met. `retired` keeps counting absence from a complete pass only; the new count is reported apart. |
| Refreshing The Decision Queue Is Exposed (modified) | Met. `KindRefresh.retired_missing`, default 0; the volatility entry never sets it. The classifier printed `additive`. |
| A Changed Decision Is Checked Again | Met. Exact strings match `wording.ts`; gone, current and still-changed outcomes are tested; `visit()` refreshes before listing. Drawing in a real window not verified. |
| A Stale, Busy Or Refused Choice Is Never Applied Blindly (modified) | Met. `#reloadAndPreview` shows a now-changed decision as changed and sends no second preview. |
| The Check Is Agreed To Once Per Batch (modified) | Met. `startCheck` refreshes and lists before quoting; `agreeCheck` refreshes before relisting; headings by kind; `offersCheckAll` covers unjudged or changed identity decisions. |

## Known Limitation

A changed identity group that the candidate search no longer retains (past its
cap; 50 of 59 groups on the reporting copy) is not re-judged by any check, so it
stays listed as changed with Check again (whose consent then says there is
nothing to check) and Decide later. Design D4 records why no one-item quote was
built.

## Evidence

On a copy of the reporting workspace: before, `identity:39fec53a…` previewed a
plan and applied as `Stale` every time. After: the preview is `Stale`; one
refresh retires 8 identity and 18 relation rows naming absorbed pages and keeps
the 2 contradictions; a run with a scripted judge followed by a refresh makes
the pair fresh and judged same, and the merge applies.
