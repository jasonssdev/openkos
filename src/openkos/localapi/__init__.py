"""The OpenKOS local HTTP API: a loopback adapter over `openkos.api`.

Read these statements before relying on it:

1. Responses are owner-local, as the CLI and `openkos.api` are: the caller is the
   workspace owner, holding a per-launch bearer token on a loopback socket, and
   no sensitivity disclosure gate is applied. The gate is deferred to the first
   endpoint that answers an ask or a query (ADR-0058).
2. Every request passes the Host, Origin and token checks of `_guard` before any
   route is reached.
3. The web framework is confined to this package and ships in the optional `api`
   extra; the core never imports it.
"""
