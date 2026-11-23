# Proposal: api-key-per-endpoint

Refs #1765. Per-endpoint follow-up to #1130.

## Intent

With `backend: openai-compatible`, `OPENKOS_OPENAI_API_KEY` is sent as
`Authorization: Bearer` to the chat endpoint and also to `embedding_base_url`,
whatever host that names. The practical hosted-chat setup (a chat provider with
no embeddings API plus local embeddings) sends the chat provider's credential
to the embedding host on every request. This sends each key only to the
endpoint it belongs to.

Success: the chat key reaches `base_url` as today; an embedding request carries
it only when it goes to `base_url`'s origin; a different embedding host gets
a key only from a new opt-in variable, otherwise none; `doctor` and the notice
say which host receives which key.

## Scope

### In Scope

1. Embedding-key resolution in `application/backends.py`, shared by
   `embed_client` and `diagnostics_client(purpose="embed")`.
2. `OPENKOS_OPENAI_EMBEDDING_API_KEY`, read only from the environment.
3. The remote-key notice and `doctor`'s `API key destination` line state the
   destination per endpoint, including an embedding endpoint that gets no key.
4. The `openkos.yaml.template` comment, `docs/cli.md`, the CHANGELOG.

### Out of Scope

- The `ollama` backend (no key exists there) and the chat key's behaviour.
- `desktop/`: the shell hands the engine `OPENKOS_OPENAI_API_KEY` only
  (ADR-0070); it does not set the new variable, so a desktop user with a remote
  embedding provider on another host gets no embedding key until a follow-up.
- Origin normalization beyond the existing one (no default-port folding).

## Approach

Origin is `scheme://host[:port]` as `key_destinations` already derives it. The
embedding key is, in order: the embedding variable when set and non-empty;
otherwise the chat key when `embedding_base_url` is unset or shares
`base_url`'s origin; otherwise none. Unset `embedding_base_url` with no new
variable is byte-identical to today.

## Risks

A user whose embedding host genuinely needs the chat key on another origin now
gets a 401 until they set the new variable; `doctor` names the destination so
the cause is visible.
