# Archive Report: chat-transient-retry

**Archived**: 2026-11-23
**Archive folder**: `openspec/changes/archive/2026-11-23-chat-transient-retry/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1764.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| openai-compatible-client | Updated | 2 ADDED requirements appended: Transient Chat Failures Are Retried Before Propagating (7 scenarios); Retry-After Is Honoured And Capped (3 scenarios). 2 MODIFIED requirements replaced: Server Unavailable Raises A Typed Error; Other Failures Raise A Generic Typed Error. No REMOVED or RENAMED headings. |

## Requirement-vs-Code Check

Checked against `llm/openai_compatible.py` (`chat`, `_chat_once`, `_parse_retry_after`). One wording correction: the code retries any non-timeout `URLError` raised while connecting, not only a refused or reset connection, so the transient definition reads "other than a timeout (for example refused or reset)".
