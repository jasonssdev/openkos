# Archive Report: recheck-changed-findings

**Archived**: 2026-10-20
**Archive folder**: `openspec/changes/archive/2026-10-20-recheck-changed-findings/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1523. Implementation merged in #1595.

## Final State At Close

- A changed `contradiction` or `revision` is re-checked for its one pair: `finding_recheck.quote_finding` and `run_finding`, `openkos.api.quote_finding_recheck` (with `FindingKind` and `BatchJudgingKind`), `decision_key` on `POST /v1/judging/quote`, and Check again in To review. Additive within `API_VERSION` 1.
- **ADR-0077**: Accepted, in all three places (frontmatter, the `**Status:**` body line, the `docs/adr/README.md` row).
- **Task 8 (the gate task)** was not ticked during apply; it is ticked at archive on observed evidence: the full `pytest --cov` run on the final commit (14747 passed, 6 skipped, coverage 96.69%), ruff, mypy, the eval self-tests and the desktop tests, and green CI on PR #1595.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| judging-application-service | Updated | 1 ADDED requirement appended: A Changed Finding Is Re-Checked For Its One Pair. |
| stable-python-api | Updated | 1 ADDED requirement appended: A Changed Finding Is Re-Checked Through A Quote By Its Decision Key. |
| local-http-api | Updated | 1 ADDED requirement appended: The Judging Quote Names A Finding's Decision. |
| desktop-review | Updated | 1 MODIFIED requirement replaced in place: A Changed Decision Is Checked Again, Never Decided As It Was. The "can only wait" paragraph and its scenario were replaced; the new paragraph and 2 scenarios were added. |

### Heading Name-Match

The MODIFIED heading was matched as an exact line in the living spec before the edit (one occurrence). After the merge every `### Requirement:` and `#### Scenario:` heading of each delta appears exactly once in its living spec. No REMOVED or RENAMED headings.

## Requirement-vs-Code Check

All four requirements are met by `application/finding_recheck.py`, `api/_judging.py`, `localapi/_models.py` and `_routes.py` (`QuoteRequest` validator, route dispatch), `application/revisions.py` (`judge_revisions` pins inputs; `recorded_inputs_fresh`), `state/pending_queue.retire_open_by_key`, and `desktop/ui/review` (`model.ts`, `wording.ts`). No wording was changed for overclaim.
