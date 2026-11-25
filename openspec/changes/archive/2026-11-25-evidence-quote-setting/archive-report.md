# Archive Report: evidence-quote-setting

**Archived**: 2026-11-25
**Archive folder**: `openspec/changes/archive/2026-11-25-evidence-quote-setting/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1768.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| ingestion | Updated | 1 ADDED requirement appended: Asking For An Evidence Quote Is Opt-In (4 scenarios). No MODIFIED, REMOVED or RENAMED headings. |

## Requirement-vs-Code Check

Checked against PR #1794 (`evidence_quote` setting, concept prompt wiring and the registered treatment hash). The default-off behavior, boolean-only validation and unchanged re-ask, decision-pass and participant-capture prompts are covered by `tests/unit/extraction/test_evidence_quote_setting.py`.
