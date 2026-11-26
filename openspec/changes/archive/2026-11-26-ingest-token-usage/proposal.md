# Proposal: ingest-token-usage

Refs #1807.

## Intent

With an opt-in hosted backend, the only way to know what an `ingest` cost is a
provider's billing dashboard, which is coarse (per day, per project) and cannot
be attributed to one source or one experiment arm. OpenKOS reads a response's
token counters only on the length-capped path, and only to word the error.

Success: `openkos ingest` prints, for each ingested source, one `tokens:` line
with the chat calls made and the summed prompt, completion and total tokens the
backend reported, so a cost can be computed exactly downstream.

## Scope

### In Scope

1. `llm/base.py`: a neutral `ChatUsage` value, a `UsageSink` callable, and a
   thread-safe `UsageTally` with a `UsageSummary` snapshot.
2. `llm/openai_compatible.py` and `llm/ollama.py`: an optional `usage_sink`
   constructor argument handed one `ChatUsage` per parsed chat response,
   including a length-capped one, before it raises.
3. `application/backends.py` forwards a `usage_sink` to the built client, only
   when given; `cli/main.py` wires one tally per ingested source and prints the
   line, single and batch, on every way out of the source (written, source-only,
   declined, refused).
4. `docs/cli.md` (ingest section and the hosted-providers guide) and the
   CHANGELOG.

### Out of Scope

- Any file, `.openkos/` ledger, `bundle/log.md` entry or other persistence: the
  record is not reconstructible and the owner chose the output line.
- Money. Prices change and differ per provider; the line carries tokens.
- `curate`, `query`, the daemon, MCP, the API, embeddings usage, the desktop
  app.

## Approach

Follow `OllamaClient`'s `diagnostics_sink`: an injected callable, never
instance state, so `chat` stays safe under the concurrent extraction fan-out.
The total is the backend's own `usage.total_tokens` when present, because a
reasoning model's hidden tokens are billed and counted there but not in
`completion_tokens` (a Gemini smoke run reported completion 26, total 331);
otherwise prompt plus completion. For Ollama the total is `prompt_eval_count +
eval_count`. A response with no usable counters still counts as a call, and the
line says so instead of printing zeros.

## Risks

Low. The request body is byte-identical with and without a sink, which tests
pin. A retried attempt that receives a response is counted once; a transient
failure carries no completion, so nothing is lost or doubled. `calls` counts
responses received, so a request that failed before any reply is not a call.

Rollback: remove the `usage_sink` plumbing and the printed line; nothing is
persisted, so there is no data to migrate.
