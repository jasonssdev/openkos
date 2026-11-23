# Archive Report: api-key-per-endpoint

**Archived**: 2026-11-23
**Archive folder**: `openspec/changes/archive/2026-11-23-api-key-per-endpoint/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1765.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| backend-selection | Updated | 1 ADDED requirement appended: The API Key Is Sent Only To The Endpoint It Belongs To (6 scenarios). 1 MODIFIED requirement replaced: A Non-Local Key Destination Is Named Once Per Host (its two unaffected scenarios kept). |
| doctor-command | Updated | 1 MODIFIED requirement replaced: Doctor States Where The API Key Will Be Sent (its unaffected no-key/ollama scenario kept). |

No REMOVED or RENAMED headings.

## Requirement-vs-Code Check

Checked against `application/backends.py` (`_embed_api_key`, `key_routes`, `remote_key_notices`) and `application/doctor.py` (`_api_key_destination_check`). One wording correction: the keyless-embedding-endpoint statement is appended to the chat notice, so it appears only when that chat notice is printed (non-local chat origin, not already covered by the plain-http warning); the requirement now says so.
