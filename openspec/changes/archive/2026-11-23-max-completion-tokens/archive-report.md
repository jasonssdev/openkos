# Archive Report: max-completion-tokens

**Archived**: 2026-11-23
**Archive folder**: `openspec/changes/archive/2026-11-23-max-completion-tokens/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1766.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| openai-compatible-client | Updated | 1 ADDED requirement appended: A Rejected Sampling Setting Names The Config Key (2 scenarios). 1 MODIFIED requirement replaced: Generation Length Cap Is Detected From `finish_reason` (scenario renamed to max_completion_tokens is forwarded per request). No REMOVED or RENAMED headings. |

## Requirement-vs-Code Check

Checked against `llm/openai_compatible.py` (`_chat_once`, `_map_http_error`, `_SAMPLING_CONFIG_KEYS`). One wording correction: the code matches `temperature` or `seed` anywhere in a 400 body (case-insensitive) rather than only when the body calls it unsupported, deprecated or not allowed, so the requirement reads "mentions". The "mapped exactly as before" clause is stated as "mapped like any other failure".
