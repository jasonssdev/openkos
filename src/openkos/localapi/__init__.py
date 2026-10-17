"""The OpenKOS local HTTP API: a loopback adapter over `openkos.api`.

Read these statements before relying on it:

1. Responses are owner-local, as the CLI and `openkos.api` are: the caller is the
   workspace owner, holding a per-launch bearer token on a loopback socket, and
   the adapter applies no sensitivity disclosure gate of its own. The one
   exception is a question: an ask job's answer is gated by `openkos.api.ask`
   before it reaches the adapter, which adds nothing to it (ADR-0058, ADR-0068).
2. Every request passes the Host, Origin and token checks of `_guard` before any
   route is reached.
3. Adding files reads only what `openkos.api` reads: the adapter passes the
   chosen paths through untouched, and the API checks each one before reading
   it (absolute, a regular file, outside the workspace's own storage).
4. The web framework is confined to this package and ships in the optional `api`
   extra; the core never imports it.
"""
