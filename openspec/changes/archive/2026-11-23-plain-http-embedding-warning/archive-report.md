# Archive Report: plain-http-embedding-warning

**Archived**: 2026-11-23
**Archive folder**: `openspec/changes/archive/2026-11-23-plain-http-embedding-warning/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1776.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| backend-selection | Updated | 1 MODIFIED requirement replaced in full: A Non-Loopback Key Send Over Plain HTTP Is Warned (now covers the chat and the embedding endpoint, one line per origin). Three scenarios added within it: a plain-http embedding host warns; a same-origin embedding endpoint is warned about once; an endpoint that receives no key prints no such warning. The three existing scenarios are kept. No ADDED, REMOVED or RENAMED requirement headings. |

## Requirement-vs-Code Check

Checked against `application/backends.py` (`insecure_key_warnings`, `_plain_http_key_routes`, `key_routes`, `remote_key_notices`) and the callers `cli/main.py` (`_maybe_warn_insecure_key`) and `mcp/server.py`. Two wording corrections: the delta says the warning states the credential is sent "unencrypted" and names "the endpoint (chat or embedding)"; the code says the key is sent to a non-local server "over plain HTTP", names the supplying variable and the host, and labels only the embedding endpoint as such. "One line per host" is stated as one line per origin, which is what the code dedupes on.
