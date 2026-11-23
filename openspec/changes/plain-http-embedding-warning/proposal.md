# Proposal: plain-http-embedding-warning

Refs #1776. Follow-up to #1765 (per-endpoint API keys).

## Intent

The "secret over plain HTTP" warning covers only the chat endpoint. Since a key
can also reach the embedding endpoint (`OPENKOS_OPENAI_EMBEDDING_API_KEY` to
any `embedding_base_url`, or the chat key to a same-origin one), a key sent
unencrypted to a non-local `http://` embedding host produced only the ordinary
remote-key notice. This warns for every endpoint that receives a key.

Success: the plain-HTTP warning fires per endpoint that receives a key, names
the endpoint (chat or embedding) and its host, never the key; the chat-only
output is byte-identical; one host gets one line.

## Scope

### In Scope

1. `application/backends.py`: the warning is built from `key_routes()`, so the
   set of endpoints that get a key has one source of truth; the function
   returns every applicable warning.
2. The callers that print it (CLI, MCP startup, judging quote notices).
3. The remote-key notice: an origin already named by a plain-HTTP warning is
   not named again.
4. CHANGELOG.

### Out of Scope

- Refusing to send a key over plain `http://` (warn, never refuse).
- `desktop/`.
- The `ollama` backend (no key exists there).

## Approach

For each route from `key_routes()` that receives a key, the origin is `http://`
and classifies non-local, emit one warning. Routes sharing an origin produce
one warning (the chat route wins). The chat wording is unchanged; the
embedding wording names the embedding endpoint and the variable supplying its
key. Printing stays at most once per process through the existing callers.

## Risks

None to behaviour: advisory only. A caller that assumed a single warning now
receives a tuple.
