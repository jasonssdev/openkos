# Archive Report: revisions-json

**Archived**: 2026-11-08
**Archive folder**: `openspec/changes/archive/2026-11-08-revisions-json/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1739.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| decision-revision-detection | Updated | 1 ADDED requirement appended: `revisions --json` Emits One Machine-Readable Object (3 scenarios). |

After the merge the requirement heading and each of its 3 scenario headings appear exactly once in the living spec. No MODIFIED, REMOVED or RENAMED headings.

## Requirement-vs-Code Check

Checked against `application/revisions_report.py` (`revisions_json_report`, `revisions_json_status`) and `cli/main.py` (`revisions`). Two wording corrections: a backend failure before any verdict does not keep an empty stdout (the verb prints the object with `partial: true` and exits 1), so it was removed from the refusal list and folded into the `partial` field's definition.
